"""T052 acceptance: the real runner decides on collected truth, end to end.

Each test submits through the public API, starts the real two-pool maintenance
process, and (where a run must actually execute) drives the real
``cmd/supervisor exec --once`` executor until the case's platform run reaches a
terminal state. The report the completion guard records is then read back from
its sealed artifact and from PostgreSQL.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from support import f00b, t052
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submit_cases(stack, owner, cases, *, case_timeout: str = "30s", key: str | None = None):
    dataset = t052.dataset_document(cases)
    artifact_id = t052.upload_dataset(stack, owner.creator, dataset)
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
        idempotency_key=key,
        extra_headers={},
    )
    assert response.status == 202, response.text
    return response.json["id"]


def _drive_supervisor_until(stack, evaluation_id: str, *, deadline_s: float = 120.0) -> list[dict]:
    """Run the real executor until the evaluation is terminal."""
    env = t052.supervisor_exec_env(stack)
    reports: list[dict] = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        row = t052.evaluation_row(stack.dbname, evaluation_id)
        if row["state"] in {"succeeded", "failed", "cancelled"}:
            return reports
        completed = t052.run_supervisor_exec(stack, env, timeout=180)
        report = t052.exec_report(completed)
        reports.append(report)
        if report.get("status") == "idle":
            time.sleep(0.05)
    raise AssertionError(
        f"the evaluation never became terminal: {t052.evaluation_row(stack.dbname, evaluation_id)}"
    )


def test_a_real_run_is_decided_by_collected_truth(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit_cases(
            owner,
        [
            t052.evaluation_case(
                case_id="minimal-succeeds",
                assertions=[
                    t052.assertion_run_state("succeeded"),
                    # expected=0 against the platform's real (empty) ledger: a
                    # real assertion, not "no checker".
                    t052.assertion_effect_count("test.send", 0),
                    t052.assertion_json("", {}),
                    t052.assertion_citations(["doc-a"], require_one=False),
                ],
            )
        ],
    )

    log = tmp_path / "pools.log"
    process, handle = t052.start_pools(t052_stack, log_path=log)
    try:
        _drive_supervisor_until(t052_stack, evaluation_id)
    finally:
        t052.stop_process(process, handle, log)

    evaluation = t052.wait_for_evaluation(t052_stack.dbname, evaluation_id, {"succeeded"})
    assert evaluation["report_id"] is not None, evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "passed", report

    document = t052.report_document(t052_stack, report["result_artifact_id"])
    assert document["status"] == "passed", document
    assert document["evaluator"] == "orchara-platform-runner/1", document
    assert document["synthetic"] is False, document
    assert document["case_count"] == 1, document
    case = document["cases"][0]
    assert case["id"] == "minimal-succeeds", case
    assert case["status"] == "passed" and case["verdict"] == "passed", case
    kinds = {outcome["kind"] for outcome in case["assertions"]}
    assert kinds == {
        "run_state", "effect_count", "json_equals", "citation_sources",
    }, case["assertions"]
    assert all(outcome["passed"] for outcome in case["assertions"]), case["assertions"]
    zero = [item for item in case["assertions"] if item["kind"] == "effect_count"][0]
    assert zero["expected"] == "0" and zero["actual"] == "0", zero

    facts = case["facts"]
    assert facts["run_state"] == "succeeded", facts
    assert facts["output_artifact_id"], facts
    assert facts["effect_counts"] == {}, facts  # collected, and genuinely empty
    assert facts["citation_sources"] == [], facts
    assert case["timings"]["run_duration_ms"] >= 0, case["timings"]

    # The report is bound to the evaluation's own frozen identity.
    provenance = document["provenance"]
    assert provenance["snapshot_id"] == evaluation["snapshot_id"], provenance
    assert provenance["dataset_artifact_id"] == evaluation["dataset_artifact_id"], provenance
    assert provenance["compiler_version"], provenance
    assert provenance["worker_image_digest"], provenance

    # The case's run is a real platform run, owned by the evaluation owner, of
    # the evaluation's own snapshot, with the case input linked as an artifact.
    case_runs = t052.case_run_rows(t052_stack.dbname, evaluation_id)
    assert len(case_runs) == 1, case_runs
    run = t052.run_row(t052_stack.dbname, case_runs[0]["run_id"])
    assert run["owner_id"] == owner.creator.principal_id, run
    assert run["state"] == "succeeded", run
    assert run["kind"] == "debug", run
    assert run["execution_snapshot_id"] == evaluation["snapshot_id"], run
    assert run["manifest_sha256"] == document["manifest_sha256"], run
    assert run["output_artifact_id"] == facts["output_artifact_id"], run
    linked = t052.run_artifact_ids(t052_stack.dbname, run["id"])
    assert run["input_artifact_id"] in linked, (run, linked)
    input_row = t052.artifact_row(t052_stack.dbname, run["input_artifact_id"])
    assert input_row["purpose"] == "run_input", input_row
    assert input_row["owner_id"] == owner.creator.principal_id, input_row
    # The real side-effect ledger is empty, which is exactly what expected=0 said.
    assert t052.operation_count(t052_stack.dbname, run["id"]) == 0

    # The contract reads project exactly the contract fields.
    evaluation_view = t052.get_evaluation(owner.creator, evaluation_id)
    assert evaluation_view.status == 200, evaluation_view.text
    assert set(evaluation_view.json) == t052.EVALUATION_FIELDS, evaluation_view.json
    assert evaluation_view.json["state"] == "succeeded", evaluation_view.json
    assert evaluation_view.json["report_id"] == evaluation["report_id"], evaluation_view.json
    report_view = t052.get_report(owner.creator, evaluation["report_id"])
    assert report_view.status == 200, report_view.text
    assert set(report_view.json) == t052.REPORT_FIELDS, report_view.json
    assert report_view.json["status"] == "passed", report_view.json


def test_a_timeout_is_not_run_and_never_passed(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit_cases(
            owner,
        [
            t052.evaluation_case(
                case_id="never-executed",
                assertions=[t052.assertion_run_state("succeeded")],
            )
        ],
        case_timeout="2s",
    )
    log = tmp_path / "pools.log"
    process, handle = t052.start_pools(t052_stack, log_path=log, case_timeout="2s")
    try:
        t052.wait_for_evaluation(t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=90)
    finally:
        t052.stop_process(process, handle, log)

    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    # The job succeeded (the report was generated); the report is not_run.
    assert evaluation["state"] == "succeeded", evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    case = document["cases"][0]
    assert case["status"] == "not_run" and case["verdict"] == "not_run", case
    assert case["reason_code"] == "run_timeout", case
    assert "never passed" in case["detail"], case
    # The assertion was evaluated against uncollected truth and did not pass.
    assert case["assertions"][0]["passed"] is False, case["assertions"]
    assert case["assertions"][0]["actual"] in {"queued", "running"}, case["assertions"]
    # A run really exists and is still queued: nothing fake was reported.
    case_runs = t052.case_run_rows(t052_stack.dbname, evaluation_id)
    assert t052.run_row(t052_stack.dbname, case_runs[0]["run_id"])["state"] == "queued"


def test_a_predefined_cancel_is_a_real_terminal_run(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    cancelled = t052.evaluation_case(
        case_id="cancel-before-execution",
        assertions=[
            t052.assertion_run_state("cancelled"),
            t052.assertion_effect_count("test.send", 0),
        ],
    )
    # The reserved collector member: a predefined interaction that really exists.
    cancelled["input"] = {"interactions": [{"kind": "cancel"}]}
    evaluation_id = _submit_cases(t052_stack, owner, [cancelled], case_timeout="30s")

    log = tmp_path / "pools.log"
    process, handle = t052.start_pools(t052_stack, log_path=log)
    try:
        t052.wait_for_evaluation(t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=90)
    finally:
        t052.stop_process(process, handle, log)

    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "passed", report
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    case = document["cases"][0]
    assert case["status"] == "passed", case
    assert case["facts"]["run_state"] == "cancelled", case["facts"]
    run_id = t052.case_run_rows(t052_stack.dbname, evaluation_id)[0]["run_id"]
    assert t052.run_row(t052_stack.dbname, run_id)["state"] == "cancelled"
    assert t052.operation_count(t052_stack.dbname, run_id) == 0
