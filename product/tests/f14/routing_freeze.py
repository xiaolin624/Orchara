"""F14/T090 (#91) freeze manifest and leakage checker for the routing corpus.

T090 must freeze the *held-out selection* and the *evaluator inputs* before any
baseline analysis. The corpus itself is T084's frozen routing corpus
(``tests/f08/routing/datasets``); this module reads it through the T084 loader,
recomputes every hash and derives the facts recorded in
``experiments/routing_sft/freeze/T090-freeze.json``.

Two kinds of checks live here:

* **freeze** — every corpus byte, the catalog card definitions and the
  T084 evidence anchor are reduced to sha256 values. ``T090-freeze.json`` stores
  those values; :func:`freeze_manifest` regenerates them, so a test that compares
  the two fails the moment any input drifts.
* **leakage** — families must not cross the dev/frozen split, cross-split
  messages must not repeat, and cross-split near-duplicates are measured with a
  documented similarity. The similarity tokenizer is deliberately *not* the
  router's matcher (which would make the check circular); it is a plain
  word/CJK-2-gram tokenizer stated in the manifest.

Nothing here starts a stack, a model or a database: it is a static checker over
files that are already in the repository.
"""

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
REPO_ROOT = PRODUCT_ROOT.parent

if str(TESTS_ROOT) not in sys.path:  # direct `python -m tests.f14.routing_freeze`
    sys.path.insert(0, str(TESTS_ROOT))

from f08.routing import corpus  # noqa: E402

FREEZE_DIR = PRODUCT_ROOT / "experiments" / "routing_sft" / "freeze"
MANIFEST_PATH = FREEZE_DIR / "T090-freeze.json"
T084_REPORT = PRODUCT_ROOT / "reports" / "f04b" / "T084-routing-eval.json"
T090_REPORT = PRODUCT_ROOT / "reports" / "f14" / "T090-routing-baseline.json"

SCHEMA = "orchara.t090-routing-freeze/1"
NEAR_DUPLICATE_THRESHOLD = 0.75
LEGAL_LICENSE_REVIEWS = ("not_applicable", "cleared", "reviewed")
LEGAL_ORIGINS = ("self_created", "public_repository_sample", "imported")

CARD_FIELDS = (
    "purpose",
    "includes",
    "excludes",
    "required_inputs",
    "expected_outputs",
    "examples",
)

# The capability card of the fixed sales template. The bytes are copied from
# `internal/templates/templates.go::CapabilityCard` at the T084 merge; the
# baseline run records the platform's own `card_sha256` for this card from
# `platform.request_routing_candidates`, so drift is caught at execution time as
# well. `tests/f14/test_t090_freeze.py` additionally greps the Go source for every
# string, so a rename/removal in the product fails the freeze check statically.
SALES_CARD = {
    "purpose": "把 CSV/XLSX 销售明细汇总为已验证统计，并生成只引用这些数字的报告正文。",
    "includes": ["CSV/XLSX 销售汇总", "summary.json 与 summary.csv 附件", "独立报告正文"],
    "excludes": ["外部写入", "未经验证的金额", "发布为共享版本"],
    "required_inputs": ["order_id,date,region,amount 四列的 CSV 或 XLSX"],
    "expected_outputs": ["summary", "report", "totals"],
    "examples": [
        "东区 120.50 与 79.50、西区 50.00 → 总额 250.00，3 单",
        "上传等价 XLSX → 同样的 250.00 / 东区 200.00 / 西区 50.00 / 3 单",
    ],
}

# The card T084's catalog actually carried for the sales application. T084 created
# the app through `tests/support/t068.sales_application`, which saves
# `tests/support/f02a.full_card()`; the T084 report records that catalog entry's
# purpose as "summarise sales". It is frozen here because the baseline must replay
# the same catalog T084 froze, not a different (template) card.
T084_SALES_CARD = {
    "purpose": "summarise sales",
    "includes": ["aggregation"],
    "excludes": ["external writes"],
    "required_inputs": ["sales csv"],
    "expected_outputs": ["report"],
    "examples": ["monthly report", "weekly digest"],
}

STRONG_PROMPT_MISSING = [
    "model provider credential (the workspace `.env.local` holds only ORCHARA_PG_PASSWORD)",
    "approved model endpoint/profile of the base model (no ORCHARA_MODEL_ENDPOINT or secret ref is configured)",
    "base-model weights, tokenizer and inference revision (no local weights/Ollama/HF cache on this host)",
    "training-capable GPU (RTX 3050 Laptop, 4 GiB, does not satisfy a training run)",
    "budget/price configuration and context/token-limit validation for a strong-prompt arm",
]


# ---------------------------------------------------------------------------
# canonical hashing
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(value) -> str:
    """The canonicalisation the platform uses for capability cards (RFC 8785 shape).

    Cards are JSON with string/array values only, so sorted keys, compact
    separators and UTF-8 encoding reproduce the platform's ``jcs.SHA256Hex``.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def card_sha256(card: dict) -> str:
    return sha256_bytes(canonical_json(card).encode("utf-8"))


# ---------------------------------------------------------------------------
# leakage measurement
# ---------------------------------------------------------------------------
def normalize_message(text: str) -> str:
    """Case-fold, strip punctuation and collapse whitespace."""
    folded = unicodedata.normalize("NFKC", text).casefold()
    kept = [char if char.isalnum() else " " for char in folded]
    return re.sub(r"\s+", " ", "".join(kept)).strip()


_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_RUN = re.compile(r"[^\W_]+", re.UNICODE)


def similarity_tokens(text: str) -> set[str]:
    """Documented, model-independent tokens for the leakage measure.

    Latin/digit runs are whole tokens. A run containing Han characters also
    contributes its two-character grams. This intentionally differs from the
    router's matching tokens: the leakage check must not reuse the matcher it is
    auditing.
    """
    tokens: set[str] = set()
    for run in _RUN.findall(unicodedata.normalize("NFKC", text).casefold()):
        if _CJK.search(run):
            tokens.add(run)
            chars = list(run)
            for start in range(len(chars) - 1):
                tokens.add(chars[start] + chars[start + 1])
        else:
            tokens.add(run)
    return tokens


def jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def similarity_report(cases: list[corpus.Case], *,
                      threshold: float = NEAR_DUPLICATE_THRESHOLD) -> dict:
    """Cross-split duplicates and near-duplicates, measured pair by pair."""
    by_split = {split: [case for case in cases if case.split == split]
                for split in corpus.SPLITS}
    exact: list[dict] = []
    normalized: list[dict] = []
    near: list[dict] = []
    top: list[dict] = []
    pairs = 0
    for left in by_split[corpus.SPLITS[0]]:
        for right in by_split[corpus.SPLITS[1]]:
            pairs += 1
            if left.message == right.message:
                exact.append({"dev": left.case_id, "frozen_test": right.case_id})
            if normalize_message(left.message) == normalize_message(right.message):
                normalized.append({"dev": left.case_id, "frozen_test": right.case_id})
            score = round(jaccard(similarity_tokens(left.message),
                                  similarity_tokens(right.message)), 6)
            entry = {
                "dev": left.case_id,
                "frozen_test": right.case_id,
                "jaccard": score,
            }
            top.append(entry)
            if score >= threshold:
                near.append(entry)
    top.sort(key=lambda item: (-item["jaccard"], item["dev"], item["frozen_test"]))
    max_score = top[0]["jaccard"] if top else None
    return {
        "checked": True,
        "threshold": threshold,
        "threshold_note": (
            "pre-registered: a cross-split pair with token Jaccard >= threshold is a "
            "near duplicate and must be reviewed and listed in the report"
        ),
        "tokenizer": (
            "normalize_message (NFKC + casefold + alnum/space + whitespace collapse); "
            "tokens = latin/digit runs and, for Han runs, the run plus its 2-grams; "
            "intentionally not the router's messageTokens"
        ),
        "exact_duplicate_pairs": exact,
        "normalized_duplicate_pairs": normalized,
        "near_duplicate_pairs": near,
        "max_cross_split_similarity": max_score,
        "pairs_compared": pairs,
        "top_pairs": top[:5],
    }


def family_isolation(cases: list[corpus.Case]) -> dict:
    """Independent re-assertion that one family lives in exactly one split/cell."""
    families: dict[str, dict] = {}
    violations: list[str] = []
    for case in cases:
        entry = families.setdefault(
            case.family_id,
            {"split": case.split, "cell": case.category, "cases": []},
        )
        if entry["split"] != case.split:
            violations.append(
                f"family {case.family_id} crosses splits: {entry['split']} vs {case.split}"
            )
        if entry["cell"] != case.category:
            violations.append(
                f"family {case.family_id} crosses cells: {entry['cell']} vs {case.category}"
            )
        entry["cases"].append(case.case_id)
    return {
        "families": {
            family_id: {
                "split": entry["split"],
                "cell": entry["cell"],
                "cases": sorted(entry["cases"]),
            }
            for family_id, entry in sorted(families.items())
        },
        "family_count": len(families),
        "violations": sorted(set(violations)),
    }


def provenance_report(cases: list[corpus.Case]) -> dict:
    origins: dict[str, int] = {}
    licences: dict[str, int] = {}
    violations: list[str] = []
    for case in cases:
        origin = case.provenance.get("origin")
        licence = case.provenance.get("license_review")
        origins[origin] = origins.get(origin, 0) + 1
        licences[licence] = licences.get(licence, 0) + 1
        if origin not in LEGAL_ORIGINS:
            violations.append(f"{case.case_id}: illegal origin {origin!r}")
        if licence not in LEGAL_LICENSE_REVIEWS:
            violations.append(f"{case.case_id}: illegal license_review {licence!r}")
        if not case.provenance.get("reference"):
            violations.append(f"{case.case_id}: provenance.reference is empty")
        if licence == "not_applicable" and origin != "self_created":
            violations.append(
                f"{case.case_id}: not_applicable licence on a non-self_created origin"
            )
    return {
        "cases": len(cases),
        "origins": dict(sorted(origins.items())),
        "license_reviews": dict(sorted(licences.items())),
        "violations": sorted(set(violations)),
        "note": (
            "the corpus is self-created diagnostic data: the only attachment is the "
            "public repository sample docs/spec/examples/sales/input.csv copied "
            "byte for byte; no user data and no third-party corpus"
        ),
    }


# ---------------------------------------------------------------------------
# frozen corpus facts
# ---------------------------------------------------------------------------
def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def split_counts(cases: list[corpus.Case]) -> dict:
    counts: dict[str, dict] = {}
    for split in corpus.SPLITS:
        families: dict[str, set[str]] = {}
        cells: dict[str, dict] = {}
        for case in (case for case in cases if case.split == split):
            families.setdefault(case.category, set()).add(case.family_id)
            cell = cells.setdefault(case.category, {"cases": 0, "_families": set()})
            cell["cases"] += 1
            cell["_families"].add(case.family_id)
        counts[split] = {
            "cases": sum(cell["cases"] for cell in cells.values()),
            "families": sum(len(item) for item in families.values()),
            "cells": {
                cell: {
                    "cases": entry["cases"],
                    "families": len(entry["_families"]),
                }
                for cell, entry in sorted(cells.items())
            },
        }
    return counts


def corpus_facts(registry_path: Path = corpus.REGISTRY) -> dict:
    """Every frozen corpus fact, recomputed from bytes on disk."""
    registry = corpus.load_registry(registry_path)
    # These assertions are the loader's own helpers; the manifest values below are
    # recomputed independently so a caller can compare manifest vs measurement.
    corpus.verify_registry(registry_path)
    cases = corpus.load_cases()
    datasets = registry_path.parent
    counts = split_counts(cases)
    return {
        "source_dir": _relative(datasets),
        "registry_path": _relative(registry_path),
        "corpus_version": registry["corpus_version"],
        "frozen_at": registry["frozen_at"],
        "registry_sha256": sha256_file(registry_path),
        "dataset_digest": corpus.dataset_digest(cases),
        "case_count": len(cases),
        "splits": list(corpus.SPLITS),
        "cells": list(corpus.CELLS),
        "files": [
            {
                "path": entry["path"],
                "sha256": sha256_file(datasets / entry["path"]),
                "size": (datasets / entry["path"]).stat().st_size,
            }
            for entry in sorted(registry["files"], key=lambda item: item["path"])
        ],
        "split_counts": counts,
        "families": sorted(
            (
                {"family_id": entry["family_id"], "cell": entry["cell"], "split": entry["split"]}
                for entry in registry["families"]
            ),
            key=lambda item: item["family_id"],
        ),
        "held_out": {
            "split": "frozen_test",
            "cases": counts["frozen_test"]["cases"],
            "families": counts["frozen_test"]["families"],
            "tuning_allowed": False,
            "policy": (
                "T084 frozen_test is the held-out set; it must not be used for any "
                "tuning, prompt iteration or training-data selection. dev is the only "
                "observation split."
            ),
            "scale_note": (
                "25 held-out cases are the delivered starting scale; EVALUATION.md:35 "
                "suggests 100 and states that the numbers are a starting scale, not "
                "statistical sufficiency. T090 did not inflate the corpus."
            ),
        },
        "dev": {
            "split": "dev",
            "cases": counts["dev"]["cases"],
            "families": counts["dev"]["families"],
            "role": "observation only, never reported as the held-out result",
        },
    }


def t084_anchor() -> dict:
    """The T084 evidence run that froze this corpus (sha256 anchor, not re-run)."""
    document = json.loads(T084_REPORT.read_text(encoding="utf-8"))
    return {
        "report": _relative(T084_REPORT),
        "schema": document["schema"],
        "generated_at": document["generated_at"],
        "git_head": document["environment"]["git_head"],
        "corpus_version": document["corpus"]["corpus_version"],
        "registry_sha256": document["corpus"]["registry_sha256"],
        "dataset_digest": document["corpus"]["dataset_digest"],
        "case_count": document["corpus"]["case_count"],
        "note": (
            "T084's real-stack evaluator run is the evidence that this corpus was "
            "replayed through the routing control plane; T090 re-validates its hashes "
            "and does not modify the 25 frozen held-out cases."
        ),
    }


# ---------------------------------------------------------------------------
# frozen catalog card definitions
# ---------------------------------------------------------------------------
def fixture_card_definitions() -> dict[str, dict]:
    """The card content the T084 runner publishes for the fixture apps."""
    from f08.routing import runner  # local import: only the freeze test needs it

    return {
        key: {field: spec[field] for field in CARD_FIELDS}
        for key, spec in sorted(runner.FIXTURE_APPS.items())
    }


def catalog_facts() -> dict:
    """Both frozen catalog card sets the baseline measures.

    ``frozen_t084_catalog`` is the catalog T084's evidence run actually used; the
    ``product_template_card_probe`` swaps only the sales card for the product's
    fixed sales-template card. The corpus cases are identical in both arms.
    """
    fixtures = fixture_card_definitions()

    def _entry(cards: dict[str, dict]) -> dict:
        return {
            "definitions_sha256": sha256_bytes(canonical_json(cards).encode("utf-8")),
            "cards": {
                key: {
                    "card_sha256": card_sha256(card),
                    "purpose": card["purpose"],
                    "required_inputs": list(card["required_inputs"]),
                }
                for key, card in sorted(cards.items())
            },
        }

    frozen_cards = {**fixtures, "sales": dict(T084_SALES_CARD)}
    probe_cards = {**fixtures, "sales": dict(SALES_CARD)}
    return {
        "source": {
            "fixture_cards": "tests/f08/routing/runner.py:FIXTURE_APPS (T084 frozen catalog)",
            "t084_sales_card": (
                "tests/support/f02a.py:full_card() — the generic card T084's "
                "T068-published sales application actually carried (the T084 report "
                "records its catalog purpose as 'summarise sales')"
            ),
            "product_sales_card": "internal/templates/templates.go::CapabilityCard",
            "publication_note": (
                "T084 published its sales app through the real T068 path; both T090 "
                "arms re-publish the same card content through the documented F08/T077 "
                "fixture path so the routing baseline needs no sandbox execution. The "
                "baseline report records this distinction instead of claiming a "
                "second T068 run."
            ),
        },
        "frozen_t084_catalog": _entry(frozen_cards),
        "product_template_card_probe": {
            **_entry(probe_cards),
            "status": "measured supplementary probe",
            "why": (
                "the frozen corpus was written against the generic T084 card; the "
                "product's real sales template card shares the generic token '生成' with "
                "the payroll messages, so this arm measures a card-content effect that "
                "the T084 catalog cannot show. It does not change any corpus case."
            ),
        },
        "runtime_instances": {
            "recorded_in": _relative(T090_REPORT),
            "note": (
                "application/version UUIDs and manifest sha256 are created by the real "
                "compiler inside a throwaway PostgreSQL cluster, so they are recorded "
                "per run in the baseline report rather than frozen here; the card "
                "content above is the frozen definition and each arm's report "
                "card_sha256 must equal these values"
            ),
        },
    }


# ---------------------------------------------------------------------------
# environment
# ---------------------------------------------------------------------------
def _run(argv: list[str], *, cwd: Path | None = None) -> str | None:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _go_version() -> str:
    candidates = [shutil.which("go"), r"E:\orchara-toolchains\go\bin\go.exe"]
    for candidate in candidates:
        if not candidate:
            continue
        version = _run([candidate, "version"])
        if version:
            return version
    return "unavailable"


def environment_facts() -> dict:
    return {
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "git_head": _run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT) or "unavailable",
        "git_branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO_ROOT)
        or "unavailable",
        "python": sys.version.split()[0],
        "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "go": _go_version(),
        "postgres": (
            "recorded at run time in the baseline report environment (real isolated "
            "PostgreSQL 16 cluster on 127.0.0.1:55432 created by tests/support/stack.py)"
        ),
        "profile": "dev-stub (the only model path on this host; no vendor credential)",
    }


# ---------------------------------------------------------------------------
# the manifest
# ---------------------------------------------------------------------------
def control_model_facts() -> dict:
    return {
        "provider": "stub",
        "stub": True,
        "model_id": "stub-route-control",
        "validation_state": "passed",
        "profile": "dev-stub",
        "registration": (
            "tests/support/t079.py:seed_route_control_model registers a real "
            "model_profile_versions row (provider=stub, validation_state=passed, "
            "config.route_control=true); the baseline report records the actual row id"
        ),
        "semantic_baseline_allowed": False,
        "note": (
            "not a vendor model and not a semantic router: it cannot produce a "
            "strong-prompt arm, a paraphrase-sensitive answer or any model-quality "
            "signal; every routing assertion is owned by server policy and SQL rows"
        ),
    }


def leakage_facts(cases: list[corpus.Case] | None = None) -> dict:
    cases = cases if cases is not None else corpus.load_cases()
    families = family_isolation(cases)
    similarity = similarity_report(cases)
    return {
        "family_isolation": families,
        "similarity": similarity,
        "provenance": provenance_report(cases),
        "manual_review": {
            "required": bool(similarity["near_duplicate_pairs"]),
            "reviewed": [],
            "note": (
                "every cross-split pair at or above the threshold must be reviewed "
                "and justified in reports/f14/T090-routing-baseline.md §leakage"
            ),
        },
    }


def build_manifest(*, generated_at: str | None = None) -> dict:
    cases = corpus.load_cases()
    return {
        "schema": SCHEMA,
        "ticket": "T090",
        "github_issue": 91,
        "feature": "F14",
        "status": "blocked",
        "generated_at": generated_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scope": {
            "target": (
                "dynamic, authorization-scoped capability-card routing decisions "
                "(select / clarify / no_match), not fixed application-id classification"
            ),
            "delivered_without_a_model": [
                "frozen corpus + held-out isolation + freeze manifest",
                "paraphrase/duplicate leakage checks",
                "per-case dev/frozen baseline through the real REST routing control plane",
                "error taxonomy and a provisional stop/go recommendation",
            ],
            "blocked_without_a_model": [
                "same-base-model strong-prompt arm",
                "rule/retrieval-prompt vs strong-prompt comparison",
                "any training run (no GPU, no base-model weights)",
            ],
        },
        "corpus": corpus_facts(),
        "evidence_anchor": t084_anchor(),
        "leakage": leakage_facts(cases),
        "catalog": catalog_facts(),
        "retrieval": {
            "not_used": True,
            "reason": (
                "the frozen routing corpus exercises capability-card retrieval only: "
                "authorized SQL candidate selection plus literal card/token matching. "
                "No knowledge-base document, embedding model, chunker, parser or vector "
                "index participates, so there is no retrieval revision to freeze."
            ),
            "embedding_model_revision": None,
            "chunker_version": None,
            "kb_document_revision": None,
            "index_revision": None,
            "acl_epoch": None,
            "what_is_frozen_instead": (
                "the authorized catalog card definitions (catalog.definitions_sha256) "
                "and the candidate snapshot persisted by the router per case "
                "(platform.request_routing_candidates.card_sha256)"
            ),
        },
        "control_model": control_model_facts(),
        "environment": environment_facts(),
        "strong_prompt_arm": {
            "status": "blocked",
            "executed": False,
            "not_run": STRONG_PROMPT_MISSING,
            "cannot_substitute": (
                "the dev-stub is not a model and must not be reported as a strong-prompt "
                "baseline; no rule-vs-strong-prompt comparison is claimed"
            ),
        },
        "training": {
            "status": "not_run",
            "executed": False,
            "reason": (
                "no training-capable GPU and no base-model weights/config on this host; "
                "TRAINING-EXPERIMENT.md:42's model gate and :44's command preconditions "
                "are unmet. No training data was generated and no adapter exists."
            ),
            "artifacts": ["experiments/routing_sft/requirements-train.in/.lock.txt (dependency resolution only)"],
        },
        "replay": {
            "freeze_check": "python -m pytest tests/f14/test_t090_freeze.py -q",
            "baseline": "python -m pytest tests/f14/test_t090_baseline.py -q -s",
            "regenerate_manifest": "python -m tests.f14.routing_freeze",
            "report_paths": [
                _relative(MANIFEST_PATH),
                _relative(T090_REPORT),
                "reports/f14/T090-routing-baseline.md",
                "reports/f04b/T084-routing-eval.json (reused evidence anchor; not modified)",
            ],
        },
    }


def write_manifest(path: Path = MANIFEST_PATH, *, generated_at: str | None = None) -> Path:
    document = build_manifest(generated_at=generated_at)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_manifest(path: Path = MANIFEST_PATH) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema"] == SCHEMA, document["schema"]
    return document


if __name__ == "__main__":
    written = write_manifest()
    print(f"T090: freeze manifest written to {written}")
