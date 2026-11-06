"""T090 (#91, F14): one bounded real-stack routing baseline run, recorded per case.

Everything asserted here is read from the run itself:

1. the frozen routing corpus is replayed by two principals through the real
   ``POST /api/v1/requests`` + ``cmd/maintenance --kinds route_request`` control
   plane, against real published catalogs whose version rows carry
   ``manifest_sha256`` and the platform's own capability-card hash;
2. arm A replays T084's catalog (the generic sales card T084 actually published);
   arm B replays the same 34 cases with only the product's sales-template card
   swapped in — a card-content probe, not a corpus change;
3. the report is computed from the per-case records (``runner.aggregate`` plus the
   T090 error taxonomy), and the strong-prompt arm and training stay
   ``blocked``/``not_run``;
4. the report is written to ``reports/f14/T090-routing-baseline.json`` only for the
   evidence run (``T090_ROUTING_REPORT``); a normal pytest run writes its own tmp
   file and leaves the repository alone.

The control model is the dev stub (`stub: true`). It is not a model; no
model-quality claim is made or implied.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from f08.routing import corpus, runner
from f14 import routing_freeze as freeze
from f14 import t090_baseline as baseline
from support import f00b, f02a, f08


def test_the_frozen_routing_baseline_is_recorded_through_the_real_control_plane(
    t090_stack, tmp_path: Path
) -> None:
    stack = t090_stack
    started_at = time.time()
    admin = f00b.admin_session(stack)
    publisher = f08.account(stack, login="t090-publisher")
    second_a = f08.account(stack, login="t090-second-a")
    second_b = f08.account(stack, login="t090-second-b")

    manifest = freeze.load_manifest()
    fixture_cards = freeze.fixture_card_definitions()
    cards_a = {**fixture_cards, "sales": freeze.T084_SALES_CARD}
    cards_b = {**fixture_cards, "sales": freeze.SALES_CARD}

    # Arm A: T084's catalog — the generic card T084's T068-published sales app
    # actually carried. The six fixture apps are published once, by runner.build_catalog.
    sales_a_version = baseline.publish_sales_card(
        stack, publisher.session, card=freeze.T084_SALES_CARD,
        name="T090 sales A (T084 generic card, fixture publication)",
    )
    sales_a = baseline.sales_app(
        sales_a_version, publication="t077_fixture_t084_generic_card")
    catalog_a = runner.build_catalog(
        stack, admin, second_a.principal_id, publisher.session, sales_a)

    # Arm B: the same fixture cards, sales replaced by the product's fixed template
    # card. The second principal only ever sees arm B's sales version.
    sales_b_version = baseline.publish_sales_card(
        stack, publisher.session, card=freeze.SALES_CARD,
        name="T090 sales B (product template card, fixture publication)",
    )
    sales_b = baseline.sales_app(
        sales_b_version, publication="t077_fixture_product_template_card")
    catalog_b = dict(catalog_a)
    catalog_b["sales"] = sales_b
    for key, app in catalog_b.items():
        if key == runner.HIDDEN_KEY:
            # payroll stays published-but-unreadable for both principals; granting
            # it here would silently destroy the corpus's ACL negative cases.
            continue
        f02a.grant_read(admin, second_b.principal_id, app.application_id)

    route_model = runner.seed_route_control(stack, publisher.principal_id)
    assert route_model["provider"] == "stub", route_model
    assert route_model["validation_state"] == "passed", route_model
    control_model = baseline.control_model_facts(stack, route_model)
    assert control_model["route_control"] is True, control_model
    assert control_model["revoked_at"] is None, control_model

    catalog_facts_a = baseline.catalog_instances(stack, catalog_a, cards_a)
    catalog_facts_b = baseline.catalog_instances(stack, catalog_b, cards_b)
    for key in catalog_a:
        assert catalog_facts_a[key]["card_sha256"] == manifest["catalog"][
            "frozen_t084_catalog"]["cards"][key]["card_sha256"], key
        assert catalog_facts_b[key]["card_sha256"] == manifest["catalog"][
            "product_template_card_probe"]["cards"][key]["card_sha256"], key
        for facts in (catalog_facts_a, catalog_facts_b):
            assert len(facts[key]["manifest_sha256"]) == 64, facts[key]
            assert len(facts[key]["card_sha256"]) == 64, facts[key]

    registry = corpus.verify_registry()
    cases = corpus.load_cases()
    corpus.validate_corpus(cases, registry)
    assert manifest["corpus"]["registry_sha256"] == freeze.sha256_file(corpus.REGISTRY)
    assert manifest["corpus"]["dataset_digest"] == corpus.dataset_digest(cases)

    attachment_a, _ = runner.upload_sales_csv(stack, second_a.session)
    attachment_b, _ = runner.upload_sales_csv(stack, second_b.session)
    primary = baseline.run_arm(stack, second_a, catalog_a, cases, attachment_a)
    probe = baseline.run_arm(stack, second_b, catalog_b, cases, attachment_b)

    for arm_name, arm in (("frozen_t084_catalog", primary),
                          ("product_template_card_probe", probe)):
        assert len(arm["records"]) == 34, (arm_name, len(arm["records"]))
        assert arm["summary"]["total_cases"] == 34
        counts = arm["errors"]["counts"]
        assert counts["total"] == 34
        assert counts["matches"] + counts["retrieval"] + counts["card"] + counts["policy"] == 34
        assert counts["model_semantic"] == "not_measured"
        # Safety invariant this corpus was written to catch: a hidden/unreadable
        # application version must never appear in any candidate snapshot.
        payroll_version = catalog_a[runner.HIDDEN_KEY].version_id
        for record in arm["records"]:
            assert payroll_version not in record["actual"]["candidate_version_ids"], record
            if record["expected"]["decision"] != "auto_dispatch":
                assert record["actual"]["run_id"] is None, record
            else:
                assert record["actual"]["run_owner_matches_second_user"] is True, record
                assert record["actual"]["run_version_matches_expected"] is True, record
        assert arm["summary"]["false_auto_executions_total"] == 0

    # The metrics in the report are the ones computed from the records.
    for arm in (primary, probe):
        for split in corpus.SPLITS:
            stats = arm["summary"]["splits"][split]
            split_records = arm["results"][split]
            assert stats["cases"] == len(split_records), stats
            assert stats["decision_accuracy"] == round(
                sum(1 for record in split_records if record["decision_matches"])
                / len(split_records), 6
            )
            assert stats["false_auto_executions"] == sum(
                1 for record in split_records if record["false_auto_execution"])
            assert stats["clarification_required"] == sum(
                1 for record in split_records if record["clarification_required"])
            assert stats["clarification_completed"] == sum(
                1 for record in split_records if record["clarification_completed"])

    for arm_name, arm in (("FROZEN_T084", primary), ("CARD_PROBE", probe)):
        for split in corpus.SPLITS:
            for record in arm["results"][split]:
                print(
                    "T090CASE "
                    + json.dumps(
                        {
                            "arm": arm_name,
                            "case_id": record["case_id"],
                            "split": record["split"],
                            "cell": record["cell"],
                            "family_id": record["family_id"],
                            "message": record["message"],
                            "expected": record["expected"]["decision"],
                            "actual": record["actual"]["decision"],
                            "error_code": record["actual"]["error_code"],
                            "candidates": record["actual"]["candidates"],
                            "decision_matches": record["decision_matches"],
                            "candidate_set_matches": record["candidate_set_matches"],
                            "false_auto_execution": record["false_auto_execution"],
                            "clarification_required": record["clarification_required"],
                            "clarification_completed": record["clarification_completed"],
                            "run_id": record["actual"]["run_id"],
                        },
                        ensure_ascii=False,
                    )
                )

    environment = baseline.environment_facts(stack)
    assert "PostgreSQL" in environment["postgres"], environment["postgres"]
    environment["run_started_at"] = time.strftime(
        "%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)
    )
    document = baseline.report_document(
        primary=primary,
        primary_catalog=catalog_facts_a,
        probe=probe,
        probe_catalog=catalog_facts_b,
        control_model=control_model,
        environment=environment,
    )
    assert document["summary"] == primary["summary"], "the report summary must be computed"
    assert document["errors"] == primary["errors"]
    assert document["corpus"]["registry_sha256"] == freeze.sha256_file(corpus.REGISTRY)
    assert document["corpus"]["dataset_digest"] == corpus.dataset_digest(cases)
    assert document["status"] == "blocked"
    assert document["strong_prompt_arm"]["status"] == "blocked"
    assert document["strong_prompt_arm"]["executed"] is False
    assert document["training"]["status"] == "not_run"
    assert document["training"]["executed"] is False
    assert document["control_model"]["stub"] is True
    assert document["coverage"]["strong_prompt_baseline"] == "not_run"
    assert document["coverage"]["training"] == "not_run"
    assert document["stop_go"]["provisional"] is True
    assert document["stop_go"]["not_complete_because"] == freeze.STRONG_PROMPT_MISSING
    assert document["freeze"]["catalog_definitions_sha256"] == manifest["catalog"][
        "frozen_t084_catalog"]["definitions_sha256"]
    assert document["freeze"]["probe_definitions_sha256"] == manifest["catalog"][
        "product_template_card_probe"]["definitions_sha256"]
    # Arm A must reproduce the committed T084 evidence run field for field: a
    # legitimate routing/catalog change has to update the frozen reference and this
    # assertion instead of silently drifting away from T084.
    assert document["t084_comparison"]["identical"] is True, document["t084_comparison"]
    # The combined stop/go must account for every measured mismatch, and every
    # measured error must be one of the three attributable layers.
    counts = document["stop_go"]["basis"]
    assert counts["mismatches"] == (
        primary["errors"]["counts"]["total"] - primary["errors"]["counts"]["matches"]
        + probe["errors"]["counts"]["total"] - probe["errors"]["counts"]["matches"]
    )
    assert counts["retrieval_errors"] + counts["card_errors"] + counts["policy_errors"] == (
        counts["mismatches"]
    )

    written = baseline.write_report(
        document, default_path=tmp_path / "T090-routing-baseline.json")
    stored = json.loads(written.read_text(encoding="utf-8"))
    assert stored["summary"] == primary["summary"]
    assert stored["errors"]["counts"]["total"] == 34
    assert stored["card_variant_probe"]["summary"]["total_cases"] == 34
    assert len(stored["results"]["frozen_test"]) == 25
    assert len(stored["results"]["dev"]) == 9
    text = json.dumps(stored, ensure_ascii=False).lower()
    for forbidden in ("training ran", '"training_ran": true',
                      '"strong_prompt_arm": {"status": "passed'):
        assert forbidden not in text

    print(f"T090: routing baseline report written to {written}")
    print("T090: primary_summary=" + json.dumps(
        {split: primary["summary"]["splits"][split] for split in corpus.SPLITS},
        ensure_ascii=False)[:1600])
    print("T090: primary_taxonomy=" + json.dumps(
        primary["errors"]["counts"], ensure_ascii=False))
    print("T090: probe_taxonomy=" + json.dumps(
        probe["errors"]["counts"], ensure_ascii=False))
    print("T090: t084_comparison_identical=" + json.dumps(
        document["t084_comparison"]["identical"]))
    print("T090: stop_go=" + json.dumps(document["stop_go"]["decision"], ensure_ascii=False))
