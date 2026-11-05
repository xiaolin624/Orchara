"""T090 (#91, F14) routing baseline: record per-case outcomes and classify errors.

The baseline replays T084's frozen routing corpus through the real REST routing
control plane, reusing T084's evaluator primitives
(``tests/f08/routing/runner.py``: catalog publication, ``run_case``, ``aggregate``)
and adding the pieces T090 needs:

* per-app catalog facts from the authoritative ``platform.application_versions``
  rows — ``manifest_sha256`` and the platform's own card hash, checked against the
  frozen card definitions;
* a per-case error taxonomy that separates ``retrieval`` / ``card`` / ``policy``
  (the layers this host can actually measure) from ``model_semantic`` (never
  measured here: no model exists);
* a provisional stop/go recommendation with the blocked strong-prompt arm stated
  as blocked, never as a result.

The control model is the dev stub. The server — not the stub — owns candidate
authorization, the required-input check and the decision policy, so the measured
accuracy is a property of ACL + card text + tokenizer + policy, not model quality.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from f08.routing import corpus, runner
from f14 import routing_freeze as freeze
from support import f00b, f08, t077, t079
from support import stack as st

REPORT_SCHEMA = "orchara.t090-routing-baseline/1"

# What a training-value judgement would need but this host cannot provide. Kept
# identical to the freeze manifest list so the two documents cannot diverge.
STRONG_PROMPT_MISSING = freeze.STRONG_PROMPT_MISSING


# ---------------------------------------------------------------------------
# catalog: real compiled versions with the frozen card definitions
# ---------------------------------------------------------------------------
def publish_sales_card(stack, publisher_session, *, card: dict,
                       name: str) -> t077.CatalogVersion:
    """Publish one sales card through the documented F08/T077 path.

    T084 published the same arm-A card through the real T068 submit/review/approve
    path. T090 isolates the routing baseline and therefore does not re-run a
    sandbox evaluation; the card content is frozen and verified against its source
    by the freeze test. The baseline report states this publication distinction
    instead of claiming a second T068 run.
    """
    return t077.publish_version(
        stack,
        publisher_session,
        name=name,
        purpose=card["purpose"],
        includes=list(card["includes"]),
        excludes=list(card["excludes"]),
        required_inputs=list(card["required_inputs"]),
        expected_outputs=list(card["expected_outputs"]),
        examples=list(card["examples"]),
    )


def sales_app(version: t077.CatalogVersion, *, publication: str) -> runner.PublishedApp:
    return runner.PublishedApp(
        key="sales",
        application_id=version.application_id,
        version_id=version.version_id,
        version_no=version.version_no,
        card=dict(version.card),
        publication=publication,
    )


def catalog_instances(stack, catalog: dict[str, runner.PublishedApp],
                      frozen_cards: dict[str, dict]) -> dict[str, dict]:
    """Read the published version rows the router will actually see.

    The candidate card hash written by the router is ``jcs.SHA256Hex(card)``; the
    same canonicalisation is recomputed here and must match the frozen definition.
    """
    version_ids = [app.version_id for app in catalog.values()]
    rows = st.sql_rows(
        stack.dbname,
        "SELECT av.application_id::text AS application_id, av.id::text AS version_id, "
        "av.version_no, av.manifest_sha256, av.capability_card::text AS capability_card, "
        "av.state_schema_version "
        "FROM platform.application_versions AS av "
        "WHERE av.id IN ("
        + ", ".join(f"{st.quote_literal(version_id)}::uuid" for version_id in version_ids)
        + ")",
    )
    by_version = {row["version_id"]: row for row in rows}
    assert set(by_version) == set(version_ids), (sorted(by_version), sorted(version_ids))
    facts: dict[str, dict] = {}
    for key, app in sorted(catalog.items()):
        row = by_version[app.version_id]
        card = json.loads(row["capability_card"])
        assert card == frozen_cards[key], f"{key}: the published card is not the frozen definition"
        facts[key] = {
            "key": key,
            "application_id": row["application_id"],
            "version_id": row["version_id"],
            "version_no": row["version_no"],
            "manifest_sha256": row["manifest_sha256"],
            "card_sha256": freeze.card_sha256(card),
            "purpose": card.get("purpose", ""),
            "required_inputs": list(card.get("required_inputs", [])),
            "publication": app.publication,
            "state_schema_version": row["state_schema_version"],
        }
    return facts


def control_model_facts(stack, route_model: dict) -> dict:
    rows = st.sql_rows(
        stack.dbname,
        "SELECT id::text AS id, name, provider, model_id, validation_state, "
        "config::text AS config, revoked_at::text AS revoked_at, data_destination "
        "FROM platform.model_profile_versions "
        f"WHERE id = {st.quote_literal(route_model['id'])}::uuid",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    config = json.loads(row["config"])
    return {
        "id": row["id"],
        "name": row["name"],
        "provider": row["provider"],
        "stub": True,
        "model_id": row["model_id"],
        "validation_state": row["validation_state"],
        "revoked_at": row["revoked_at"],
        "route_control": bool(config.get("route_control")),
        "config": config,
        "data_destination": row["data_destination"],
        "semantic_baseline_allowed": False,
        "note": (
            "the dev-stub route-control model is a real registered profile, not a "
            "vendor model; it cannot produce a strong-prompt arm or a model-quality "
            "signal"
        ),
    }


def postgres_facts(stack) -> str:
    rows = st.sql_rows(stack.dbname, "SELECT version() AS version")
    return rows[0]["version"]


# ---------------------------------------------------------------------------
# error taxonomy
# ---------------------------------------------------------------------------
def classify_case(record: dict) -> dict:
    """Attribute one mismatch to the measurable layer that explains it.

    ``model_semantic`` is never assigned: no semantic model ran, so a mismatch
    cannot honestly be attributed to model semantics (and a match cannot honestly
    be attributed to model quality either).
    """
    expected = record["expected"]
    actual = record["actual"]
    expected_candidates = set(expected["candidates"])
    actual_candidates = set(actual["candidates"])
    entry = {
        "case_id": record["case_id"],
        "split": record["split"],
        "cell": record["cell"],
        "family_id": record["family_id"],
        "expected_decision": expected["decision"],
        "actual_decision": actual["decision"],
        "error_code": actual["error_code"],
        "candidates": actual["candidates"],
        "clarification_required": record["clarification_required"],
        "clarification_completed": record["clarification_completed"],
        "run_id": actual["run_id"],
        "decision_matches": record["decision_matches"],
        "candidate_set_matches": record["candidate_set_matches"],
        "false_auto_execution": record["false_auto_execution"],
        "error_class": None,
        "detail": "the expected decision and candidate set were observed",
        "model_semantic": "not_measured",
    }
    if record["decision_matches"] and record["candidate_set_matches"]:
        return entry

    if not expected_candidates <= actual_candidates:
        entry["error_class"] = "retrieval"
        entry["detail"] = "expected candidate(s) were not recalled by the authorized search"
    elif actual_candidates - expected_candidates:
        entry["error_class"] = "retrieval"
        entry["detail"] = "the authorized search recalled candidate(s) the corpus does not expect"
    elif expected["decision"] == "failed" and actual["decision"] != "failed":
        entry["error_class"] = "retrieval"
        entry["detail"] = "a false candidate turned a no-match/refusal case into a match"
    elif expected["decision"] == "awaiting_input" and actual["decision"] == "auto_dispatch":
        entry["error_class"] = "card"
        entry["detail"] = "the card's required_inputs were not enforced before dispatch"
    elif expected["decision"] == "auto_dispatch" and actual["decision"] == "awaiting_input":
        entry["error_class"] = "card"
        entry["detail"] = "the card declares required inputs the corpus considers complete"
    elif (
        expected["decision"] == "failed"
        and actual["decision"] == "failed"
        and actual["error_code"] != expected["no_run_reason"]
    ):
        entry["error_class"] = "policy"
        entry["detail"] = "the server refused with a different error code than the corpus expects"
    elif expected["decision"] == "awaiting_selection" and actual["decision"] == "auto_dispatch":
        entry["error_class"] = "policy"
        entry["detail"] = "the server auto-dispatched although the candidate snapshot is ambiguous"
    else:
        entry["error_class"] = "policy"
        entry["detail"] = "the decision contradicts the observed candidate/input state"
    return entry


def error_taxonomy(records: list[dict]) -> dict:
    cases = [classify_case(record) for record in records]
    classes = ("retrieval", "card", "policy")
    counts = {name: 0 for name in classes}
    matches = 0
    for case in cases:
        if case["error_class"] is None:
            matches += 1
        else:
            counts[case["error_class"]] += 1
    by_split = {}
    for split in corpus.SPLITS:
        split_cases = [case for case in cases if case["split"] == split]
        by_split[split] = {
            "cases": len(split_cases),
            "matches": sum(1 for case in split_cases if case["error_class"] is None),
            "errors": {
                name: sum(1 for case in split_cases if case["error_class"] == name)
                for name in classes
            },
            "model_semantic": "not_measured",
        }
    families: dict[str, dict] = {}
    for case in cases:
        entry = families.setdefault(
            case["family_id"],
            {"split": case["split"], "cell": case["cell"], "cases": 0, "matches": 0},
        )
        entry["cases"] += 1
        entry["matches"] += 1 if case["error_class"] is None else 0
    return {
        "cases": cases,
        "counts": {
            "total": len(cases),
            "matches": matches,
            "retrieval": counts["retrieval"],
            "card": counts["card"],
            "policy": counts["policy"],
            "model_semantic": "not_measured",
        },
        "by_split": by_split,
        "by_family": dict(sorted(families.items())),
        "measured_layers": {
            "retrieval": "measured (authorized SQL search + literal card tokens)",
            "card": "measured (capability card content / required_inputs)",
            "policy": "measured (server decision policy)",
            "model_semantic": (
                "not_measured: no model credential, endpoint or weights on this host; "
                "the dev-stub is not a semantic router"
            ),
        },
        "mistake_free_corpus_note": (
            "all corpus messages were written by T084 to share literal tokens with the "
            "target card; a clean taxonomy therefore says the measured pipeline has no "
            "error on this corpus, not that a model would not help on real paraphrases"
        ),
    }


# ---------------------------------------------------------------------------
# stop / go
# ---------------------------------------------------------------------------
def stop_go(summary: dict, taxonomy: dict) -> dict:
    fixable = taxonomy["counts"]["retrieval"] + taxonomy["counts"]["card"] + taxonomy["counts"]["policy"]
    total = taxonomy["counts"]["total"]
    mismatches = total - taxonomy["counts"]["matches"]
    if mismatches == 0:
        decision = "stop_training_on_this_frozen_corpus"
        headline = (
            "no measured decision error; there is no error for a training objective to "
            "explain on this frozen corpus"
        )
    elif fixable == mismatches:
        decision = "stop_training_fix_the_measured_layers_first"
        headline = (
            f"all {mismatches} observed mismatch(es) are attributable to retrieval/card/"
            "policy, which are not fixed by fine-tuning"
        )
    else:
        decision = "inconclusive_keep_blocked"
        headline = "a mismatch is not attributable to the measurable layers, so keep the arm blocked"
    return {
        "decision": decision,
        "provisional": True,
        "headline": headline,
        "basis": {
            "cases": total,
            "mismatches": mismatches,
            "retrieval_errors": taxonomy["counts"]["retrieval"],
            "card_errors": taxonomy["counts"]["card"],
            "policy_errors": taxonomy["counts"]["policy"],
            "model_semantic_errors": "not_measured",
            "false_auto_executions": summary["false_auto_executions_total"],
            "clarification_completed": summary["clarification_completion_total"],
        },
        "rationale": [
            "TRAINING-EXPERIMENT.md:40 — generate training data only if the baseline "
            "errors are explainable by the training objective. The measured pipeline "
            "either has no error on this corpus or its errors belong to "
            "retrieval/card/policy, which fine-tuning does not replace.",
            "ROUTING-MEMORY-KNOWLEDGE.md:49 — when capability cards cannot separate real "
            "needs, change the card and the clarification interaction first; do not use "
            "fine-tuning to hide an unclear product definition.",
            "TRAINING-EXPERIMENT.md:48 — do not claim an improvement when the gain could "
            "be catalog memory or a prompt change; without the same-base-model "
            "strong-prompt arm there is no adjusted-prompt comparison, so the arm stays "
            "blocked and this stop stays provisional.",
        ],
        "not_complete_because": list(STRONG_PROMPT_MISSING),
        "what_would_change_it": [
            "an approved same-base-model endpoint + budget, so the strong-prompt arm can "
            "run on the same inputs, candidate order and post-processing",
            "a genuinely paraphrased held-out set that does not share card tokens "
            "(family-isolated, provenance recorded), to measure semantic recall",
            "a training-capable GPU and a base model with a pinned revision, tokenizer "
            "and legal adapter distribution before any run is claimed",
        ],
        "spec_anchors": [
            "TRAINING-EXPERIMENT.md:40",
            "TRAINING-EXPERIMENT.md:46",
            "TRAINING-EXPERIMENT.md:48",
            "ROUTING-MEMORY-KNOWLEDGE.md:49",
            "EVALUATION.md:35",
            "TESTING-AND-DATASETS.md:53",
        ],
    }


# ---------------------------------------------------------------------------
# one arm = one catalog x the same 34 frozen cases
# ---------------------------------------------------------------------------
def run_arm(stack, second, catalog: dict[str, runner.PublishedApp],
            cases: list[corpus.Case], attachment_id: str) -> dict:
    results: dict[str, list[dict]] = {split: [] for split in corpus.SPLITS}
    for split in corpus.SPLITS:
        for case in cases:
            if case.split != split:
                continue
            results[split].append(runner.run_case(stack, second, catalog, case, attachment_id))
    records = [record for split in corpus.SPLITS for record in results[split]]
    summary = runner.aggregate(records)
    taxonomy = error_taxonomy(records)
    return {
        "results": results,
        "records": records,
        "summary": summary,
        "errors": taxonomy,
        "stop_go": stop_go(summary, taxonomy),
    }


def combined_stop_go(arms: dict[str, dict]) -> dict:
    """The provisional stop/go over every measured arm."""
    totals = {"cases": 0, "mismatches": 0, "retrieval": 0, "card": 0, "policy": 0}
    per_arm: dict[str, dict] = {}
    evidence: list[dict] = []
    for name, arm in arms.items():
        counts = arm["errors"]["counts"]
        mismatches = counts["total"] - counts["matches"]
        totals["cases"] += counts["total"]
        totals["mismatches"] += mismatches
        for error_class in ("retrieval", "card", "policy"):
            totals[error_class] += counts[error_class]
        per_arm[name] = {
            "cases": counts["total"],
            "matches": counts["matches"],
            "mismatches": mismatches,
            "decision_accuracy": round(counts["matches"] / counts["total"], 6),
            "retrieval": counts["retrieval"],
            "card": counts["card"],
            "policy": counts["policy"],
            "model_semantic": "not_measured",
        }
        for case in arm["errors"]["cases"]:
            if case["error_class"]:
                evidence.append({
                    "arm": name,
                    "case_id": case["case_id"],
                    "cell": case["cell"],
                    "expected_decision": case["expected_decision"],
                    "actual_decision": case["actual_decision"],
                    "candidates": case["candidates"],
                    "error_class": case["error_class"],
                    "detail": case["detail"],
                })
    fixable = totals["retrieval"] + totals["card"] + totals["policy"]
    if totals["mismatches"] == 0:
        decision = "stop_training_on_this_frozen_corpus"
        headline = (
            "no measured decision error in any arm; there is no error for a training "
            "objective to explain on this frozen corpus"
        )
    elif fixable == totals["mismatches"]:
        decision = "stop_training_fix_the_measured_layers_first"
        headline = (
            f"{totals['mismatches']} measured mismatch(es) across {len(arms)} arm(s), all "
            "attributable to retrieval/card/policy; fine-tuning does not replace them"
        )
    else:
        decision = "inconclusive_keep_blocked"
        headline = "a mismatch is not attributable to the measurable layers, so keep the arm blocked"
    return {
        "decision": decision,
        "provisional": True,
        "headline": headline,
        "arms": per_arm,
        "basis": {
            "cases": totals["cases"],
            "mismatches": totals["mismatches"],
            "retrieval_errors": totals["retrieval"],
            "card_errors": totals["card"],
            "policy_errors": totals["policy"],
            "model_semantic_errors": "not_measured",
        },
        "observed_error_evidence": evidence,
        "rationale": [
            "TRAINING-EXPERIMENT.md:40 — generate training data only if the baseline "
            "errors are explainable by the training objective. The measured errors (when "
            "present) are candidate recall/over-recall or card-content effects, which "
            "fine-tuning does not replace.",
            "ROUTING-MEMORY-KNOWLEDGE.md:49 — when capability cards cannot separate real "
            "needs, change the card and the clarification interaction first; do not use "
            "fine-tuning to hide an unclear product definition.",
            "TRAINING-EXPERIMENT.md:48 — do not claim an improvement when the gain could "
            "be catalog memory or a prompt change; without the same-base-model "
            "strong-prompt arm there is no adjusted-prompt comparison, so the arm stays "
            "blocked and this stop stays provisional.",
            "The two arms differ only in the sales capability card; the same 34 frozen "
            "cases were replayed in both, so the delta is a card-content effect, not a "
            "corpus or evaluator change.",
        ],
        "not_complete_because": list(STRONG_PROMPT_MISSING),
        "what_would_change_it": [
            "an approved same-base-model endpoint + budget, so the strong-prompt arm can "
            "run on the same inputs, candidate order and post-processing",
            "a genuinely paraphrased held-out set that does not share card tokens "
            "(family-isolated, provenance recorded), to measure semantic recall",
            "a training-capable GPU and a base model with a pinned revision, tokenizer "
            "and legal adapter distribution before any run is claimed",
        ],
        "spec_anchors": [
            "TRAINING-EXPERIMENT.md:40",
            "TRAINING-EXPERIMENT.md:46",
            "TRAINING-EXPERIMENT.md:48",
            "ROUTING-MEMORY-KNOWLEDGE.md:49",
            "EVALUATION.md:35",
            "TESTING-AND-DATASETS.md:53",
        ],
    }


def t084_comparison(primary: dict) -> dict:
    """Compare arm A with the committed T084 evidence run (same corpus, same cards).

    T084's catalog is the arm-A catalog (generic sales card), so a difference in
    these numbers is a real finding about reproducibility, not a measurement to
    hide behind an assertion.
    """
    reference = json.loads(freeze.T084_REPORT.read_text(encoding="utf-8"))["summary"]
    comparison: dict = {"reference": "reports/f04b/T084-routing-eval.json", "splits": {}}
    same = True
    for split in corpus.SPLITS:
        ours = primary["summary"]["splits"][split]
        theirs = reference["splits"][split]
        fields = ("cases", "decision_accuracy", "false_auto_executions",
                  "clarification_required", "clarification_completed",
                  "candidate_recall")
        split_same = all(ours[field] == theirs[field] for field in fields)
        same = same and split_same
        comparison["splits"][split] = {
            "t090": {field: ours[field] for field in fields},
            "t084": {field: theirs[field] for field in fields},
            "identical": split_same,
        }
    comparison["identical"] = same
    return comparison


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _repo_relative(path: Path) -> str:
    return path.resolve().relative_to(freeze.REPO_ROOT.resolve()).as_posix()


def environment_facts(stack) -> dict:
    environment = freeze.environment_facts()
    environment.update(
        {
            "profile": "dev-stub",
            "stack": (
                "real PostgreSQL 16 cluster (tests/support/stack.py) + compiled cmd/api "
                "and cmd/maintenance; no sandbox execution in this baseline"
            ),
            "database": "throwaway PostgreSQL 16 cluster on 127.0.0.1:55432",
            "postgres": postgres_facts(stack),
            "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "sandbox_execution": "not_used (routing decisions only; T084 owns the real execution evidence)",
            "real_model": "not_run (no vendor model credentials; dev-stub only)",
            "browser_dom": "not_used (no browser in this ticket)",
        }
    )
    return environment


def report_document(*, primary: dict, primary_catalog: dict[str, dict], probe: dict,
                    probe_catalog: dict[str, dict], control_model: dict,
                    environment: dict) -> dict:
    freeze_document = freeze.load_manifest()
    rows = freeze.corpus_facts()
    stop_go_document = combined_stop_go({
        "frozen_t084_catalog": primary,
        "product_template_card_probe": probe,
    })
    return {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "status": "blocked",
        "ticket": "T090",
        "github_issue": 91,
        "feature": "F14",
        "status_reason": (
            "blocked on the same-base-model strong-prompt arm and on any training: this "
            "host has no model credential/endpoint/weights and no training-capable GPU. "
            "All model-independent parts were executed and are recorded here."
        ),
        "primary_arm": "frozen_t084_catalog",
        "corpus": {
            "corpus_version": rows["corpus_version"],
            "frozen_at": rows["frozen_at"],
            "registry_path": rows["registry_path"],
            "registry_sha256": rows["registry_sha256"],
            "dataset_digest": rows["dataset_digest"],
            "case_count": rows["case_count"],
            "splits": rows["splits"],
            "cells": rows["cells"],
            "split_counts": rows["split_counts"],
            "held_out": rows["held_out"],
            "dev": rows["dev"],
            "freeze_manifest": _repo_relative(freeze.MANIFEST_PATH),
        },
        "freeze": {
            "manifest": _repo_relative(freeze.MANIFEST_PATH),
            "catalog_definitions_sha256": freeze_document["catalog"][
                "frozen_t084_catalog"]["definitions_sha256"],
            "probe_definitions_sha256": freeze_document["catalog"][
                "product_template_card_probe"]["definitions_sha256"],
            "retrieval": freeze_document["retrieval"],
            "leakage": freeze_document["leakage"],
        },
        "catalog": primary_catalog,
        "publication": {
            "mode": "t077_fixture_all",
            "arms": {
                "frozen_t084_catalog": (
                    "sales card = the generic card T084's T068-published app carried "
                    "(tests/support/f02a.full_card, purpose 'summarise sales'), published "
                    "here through the F08/T077 fixture path"
                ),
                "product_template_card_probe": (
                    "sales card = internal/templates/templates.go::CapabilityCard, the "
                    "product's fixed sales template card, published the same way"
                ),
            },
            "t068_real_publication": (
                "not re-run in T090; T084's reports/f04b/T084-routing-eval.json is the "
                "evidence for the real T068 submit/review/approve publication. Both T090 "
                "arms only isolate routing, they do not re-claim T068."
            ),
            "why_this_is_enough_for_routing": (
                "routing decisions depend on the immutable version's authorized card "
                "content, not on which approved path wrote the version row; the freeze "
                "test pins both card sets and this report records the compiler manifest "
                "sha256 per application"
            ),
        },
        "control_model": control_model,
        "retrieval": freeze_document["retrieval"],
        "strong_prompt_arm": freeze_document["strong_prompt_arm"],
        "training": freeze_document["training"],
        "results": primary["results"],
        "summary": primary["summary"],
        "errors": primary["errors"],
        "t084_comparison": t084_comparison(primary),
        "card_variant_probe": {
            "arm": "product_template_card_probe",
            "catalog": probe_catalog,
            "note": (
                "supplementary measured arm: the same 34 frozen cases against the same "
                "six fixture cards, with only the sales card replaced by the product "
                "template card; it is not the T084 frozen catalog"
            ),
            "results": probe["results"],
            "summary": probe["summary"],
            "errors": probe["errors"],
        },
        "stop_go": stop_go_document,
        "coverage": {
            "dev_baseline": "executed",
            "frozen_baseline": "executed",
            "card_variant_probe": "executed",
            "leakage_checks": "executed (tests/f14/test_t090_freeze.py)",
            "retrieval_freeze": "not_applicable (retrieval.not_used=true)",
            "strong_prompt_baseline": "not_run",
            "rule_vs_strong_prompt_comparison": "not_run",
            "training": "not_run",
            "model_semantic_errors": "not_measured",
            "browser_dom": "not_used",
            "sandbox_execution": "not_used",
        },
        "environment": environment,
        "replay": {
            "commands": [
                "cd E:\\Orchara-wt\\t090\\product",
                "python -m pytest tests/f14/test_t090_freeze.py -q",
                "$env:T090_ROUTING_REPORT='reports/f14/T090-routing-baseline.json'",
                "python -m pytest tests/f14/test_t090_baseline.py -q -s",
                "python tools/sync_spec.py --check",
            ],
            "report_paths": [
                _repo_relative(freeze.T090_REPORT),
                "reports/f14/T090-routing-baseline.md",
                _repo_relative(freeze.MANIFEST_PATH),
                "reports/f04b/T084-routing-eval.json (reused corpus/evaluator evidence)",
            ],
        },
    }


def write_report(document: dict, *, default_path: Path) -> Path:
    """Write to ``T090_ROUTING_REPORT`` for the evidence run, tmp for pytest."""
    path = Path(os.environ.get("T090_ROUTING_REPORT", str(default_path)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


__all__ = [
    "REPORT_SCHEMA",
    "publish_sales_card",
    "sales_app",
    "catalog_instances",
    "control_model_facts",
    "classify_case",
    "error_taxonomy",
    "stop_go",
    "environment_facts",
    "report_document",
    "write_report",
]
