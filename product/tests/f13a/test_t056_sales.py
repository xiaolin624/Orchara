"""T056 acceptance: a real sales case succeeds through the official API.

The ticket's load-bearing claim is that the *succeeded* sales/tool case the T052
report recorded as unreachable is now reachable: a committed dataset case binds a
caller-uploaded CSV/XLSX fixture (by content) as the run's ``task_input``, the
real supervisor executes the fixed preset tool in the sandbox, and the sealed
report's case verdict is ``passed`` with Decimal evidence. Nothing is faked: the
dataset is a real sealed artifact, the run is created by the server as the
evaluation owner, the bytes move over signed links, and the report is the
completion guard's own product.

The frozen boundary set is executed in the same way; its regions/dates/amount
precisions were committed before this implementation was exercised and are not
tuned afterwards.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from support import t056
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _run_passed_case(stack, owner, dataset_name: str, *, expected_case_id: str):
    """Run one committed happy dataset and return (evaluation, report, case)."""
    document = t056.load_dataset(dataset_name)
    evaluation, report, sealed = t056.run_evaluation(
        stack, owner, document, log_path=Path(stack.object_dir).parent / "pools.log"
    )
    assert evaluation["state"] == "succeeded", evaluation
    assert report["status"] == "passed", (report, sealed)
    case = t056.case_by_id(sealed, expected_case_id)
    assert case["status"] == "passed" and case["verdict"] == "passed", case
    assert case["assertions"], "a passed case must carry assertion evidence"
    assert all(outcome["passed"] for outcome in case["assertions"]), case["assertions"]
    assert case["facts"]["run_state"] == "succeeded", case["facts"]
    assert case["facts"].get("run_error_code") in (None, ""), case["facts"]
    # The fixture really was bound as the run's task_input artifact, by content.
    assert case["facts"]["task_input_artifact_id"], case["facts"]
    assert case["facts"]["task_input_sha256"], case["facts"]
    # The real run is owned by the evaluation owner and executed the frozen snapshot.
    case_runs = t056.t052.case_run_rows(stack.dbname, evaluation["id"])
    assert len(case_runs) == 1, case_runs
    run = t056.t052.run_row(stack.dbname, case_runs[0]["run_id"])
    assert run["owner_id"] == owner.creator.principal_id, run
    assert run["state"] == "succeeded", run
    assert run["output_artifact_id"], run
    assert run["execution_snapshot_id"] == evaluation["snapshot_id"], run
    assert t056.t052.operation_count(stack.dbname, run["id"]) == 0
    return evaluation, report, case


def test_a_succeeded_csv_sales_case_yields_a_passed_report(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    evaluation, report, case = _run_passed_case(
        t056_stack, owner, "sales_happy_csv.json", expected_case_id="sales-happy-csv"
    )

    outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
    decimals = {
        outcome["expected"]: outcome
        for outcome in case["assertions"]
        if outcome["kind"] == "decimal_equals"
    }
    assert outcomes["run_state"]["actual"] == "succeeded", outcomes["run_state"]
    assert "250.00" in decimals, decimals
    assert decimals["250.00"]["actual"] == "250.00", decimals["250.00"]
    assert decimals["200.00"]["actual"] == "200.00", decimals["200.00"]
    assert decimals["50.00"]["actual"] == "50.00", decimals["50.00"]
    assert outcomes["json_equals"]["actual"] == "3", outcomes["json_equals"]
    # expected=0 against the platform's real (empty) effect ledger is asserted,
    # not assumed.
    assert outcomes["effect_count"]["passed"] is True, outcomes["effect_count"]
    assert outcomes["effect_count"]["actual"] == "0", outcomes["effect_count"]

    # The run's final output document is the real end-node mapping, and the
    # summary really is the tool's verified Decimal summary.
    run = t056.t052.run_row(
        t056_stack.dbname, t056.t052.case_run_rows(t056_stack.dbname, evaluation["id"])[0]["run_id"]
    )
    output = t056.t052.artifact_row(t056_stack.dbname, run["output_artifact_id"])
    assert output["purpose"] == "final_output", output
    content = t056.t052.report_document(t056_stack, run["output_artifact_id"])
    assert content == {"report": "sales summary", "summary": {
        "total": "250.00", "regions": {"东区": "200.00", "西区": "50.00"}, "order_count": 3,
    }}, content

    # --- the per-case results read (non-contract, ADR 0013) ------------------
    first = t056.get_cases(owner.creator, report["id"])
    assert first.status == 200, first.text
    body = first.json
    assert set(body) == {"report_id", "status", "cases"}, body
    assert body["report_id"] == report["id"] and body["status"] == "passed", body
    case_view = t056.case_by_id(body, "sales-happy-csv")
    assert case_view["verdict"] == "passed", case_view
    assert case_view["facts"]["run_id"] == run["id"], case_view["facts"]
    assert case_view["facts"]["task_input_sha256"] == case["facts"]["task_input_sha256"]
    labels = t056.attachment_labels(case_view)
    assert {"summary.json", "summary.csv", "output"} <= set(labels), labels
    assert len(case_view["assertions"]) == len(case["assertions"]), case_view
    for outcome in case_view["assertions"]:
        assert set(outcome) >= {"kind", "expected", "actual", "passed"}, outcome

    # No sealed object key (or staging path) is ever part of the body.
    serialized = first.text
    assert "object_key" not in serialized and "staging" not in serialized
    for attachment in case_view["attachments"]:
        row = t056.t052.artifact_row(t056_stack.dbname, attachment["artifact_id"])
        assert row["state"] == "ready", row
        assert row["object_key"] not in serialized
        assert attachment["sha256"] == row["sha256"], attachment

    # Immutability: repeated reads are byte-identical.
    second = t056.get_cases(owner.creator, report["id"])
    assert second.status == 200 and second.text == first.text

    # Authorization: non-owner 404 (no id enumeration), admin 200.
    other = t056.t052.other_creator(t056_stack, login="t056-other-" + uuid.uuid4().hex[:8])
    assert t056.get_cases(other.creator, report["id"]).status == 404
    admin_view = t056.get_cases(owner.admin, report["id"])
    assert admin_view.status == 200 and admin_view.text == first.text

    # A scoped key needs application:read; without it the read is refused
    # before any ACL decision. With it, a non-owner still learns nothing.
    read_key = t056.scoped_api_key(
        t056_stack, owner.admin, owner.creator.principal_id, ["application:read"]
    )
    assert read_key.get(t056.cases_path(report["id"])).status == 404
    write_key = t056.scoped_api_key(
        t056_stack, owner.admin, owner.creator.principal_id, ["application:write"]
    )
    refused = write_key.get(t056.cases_path(report["id"]))
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "insufficient_scope", refused.text

    # The actual summary.csv attachment downloads through the contract route.
    csv_attachment = next(
        attachment for attachment in case_view["attachments"]
        if attachment["label"] == "summary.csv"
    )
    link = t056.t033.artifact_download(owner.creator, csv_attachment["artifact_id"])
    assert link.status == 200, link.text
    downloaded = t056.t024.get_link(link.json["url"])
    assert downloaded.status == 200 and downloaded.raw == t056.t033.EXPECTED_SUMMARY_CSV
    # …and the summary.json attachment is the tool's canonical Decimal summary.
    summary_attachment = next(
        attachment for attachment in case_view["attachments"]
        if attachment["label"] == "summary.json"
    )
    summary = t056.t052.report_document(t056_stack, summary_attachment["artifact_id"])
    assert summary == {
        "total": "250.00",
        "regions": {"东区": "200.00", "西区": "50.00"},
        "order_count": 3,
    }, summary


def test_a_succeeded_xlsx_sales_case_yields_the_same_decimal_totals(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    _, _, case = _run_passed_case(
        t056_stack, owner, "sales_happy_xlsx.json", expected_case_id="sales-happy-xlsx"
    )
    outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
    decimals = {
        outcome["expected"]: outcome
        for outcome in case["assertions"]
        if outcome["kind"] == "decimal_equals"
    }
    assert decimals["250.00"]["actual"] == "250.00", decimals["250.00"]
    assert outcomes["json_equals"]["actual"] == "3", outcomes["json_equals"]
    # CSV and XLSX are byte-different fixtures that must agree on the Decimal truth.
    expected = t056.expected_values()
    assert expected["sales_happy_csv"]["total"] == expected["sales_happy_xlsx"]["total"]
    assert expected["sales_happy_csv"]["regions"] == expected["sales_happy_xlsx"]["regions"]


def test_a_frozen_boundary_case_matches_the_reference_truth(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    _, _, case = _run_passed_case(
        t056_stack, owner, "frozen_boundary.json", expected_case_id="sales-frozen-boundary"
    )
    expected = t056.expected_values()["frozen_boundary"]
    outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
    decimals = {
        outcome["expected"]: outcome
        for outcome in case["assertions"]
        if outcome["kind"] == "decimal_equals"
    }
    assert decimals[expected["total"]]["actual"] == expected["total"]
    assert outcomes["json_equals"]["actual"] == str(expected["order_count"])
    assert set(decimals) == {expected["total"], *expected["regions"].values()}
    assert {outcome["actual"] for outcome in decimals.values()} == set(decimals)
