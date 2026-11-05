"""T052 acceptance: the real runner decides on collected truth, end to end.

Each test submits through the public API, starts the real two-pool maintenance
process, and (where a run must actually execute) drives the real
``cmd/supervisor exec --once`` executor until the case's platform run reaches a
terminal state. The report the completion guard records is then read back from
its sealed artifact and from PostgreSQL.

The positive path here is a run that really reaches a terminal state and really
has no side effects: a predefined ``cancel`` interaction. **T062 superseded the
original expectation that this is a pass**: a cancelled run never finished, so
the case must be a refusal (``failed``/``run_cancelled``) and the report must not
be ``passed``. T062's suite proves the guard still accepts a legitimate pass over
a *completed* run. A *succeeded* run of a tool-bearing application needs a caller
CSV bound as a ``task_input`` artifact, which this slice cannot express (a case
input cannot name artifacts); that is recorded as the #57 boundary in the report,
not faked here.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from support import f00b, t052
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submit_cases(stack, owner, cases, *, case_timeout: str = "30s"):
    dataset = t052.dataset_document(cases)
    artifact_id = t052.upload_dataset(stack, owner.creator, dataset)
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
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


def test_a_real_run_is_traced_and_never_claimed_successful(t052_stack, tmp_path: Path) -> None:
    """A genuine executor run decides the case; the handler invents nothing.

    The preset linear executor refuses a tool-less start->end chain
    (`tool_unsupported_graph`), so this real run terminates failed with no output
    artifact. The checker must therefore fail every assertion that needs success
    or an output, and the report must be `failed` — HTTP 202, the executor's
    "executed" report and the sealed dataset are all not proof of success.
    """
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit_cases(
        t052_stack,
        owner,
        [
            t052.evaluation_case(
                case_id="minimal-does-not-run",
                assertions=[
                    t052.assertion_run_state("succeeded"),
                    # expected=0 against the real ledger: a real assertion, and
                    # the only one that can pass for a run that failed early.
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
        reports = _drive_supervisor_until(t052_stack, evaluation_id)
    finally:
        t052.stop_process(process, handle, log)
    assert reports, "the executor never claimed the case's run"

    evaluation = t052.wait_for_evaluation(t052_stack.dbname, evaluation_id, {"succeeded"})
    assert evaluation["report_id"] is not None, evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    assert report["status"] == "failed", (report, document)

    case = document["cases"][0]
    assert case["status"] == "failed", case
    outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
    # The real run state is the failure the executor recorded, never a claim.
    assert outcomes["run_state"]["actual"] == "failed", outcomes["run_state"]
    assert outcomes["run_state"]["expected"] == "succeeded", outcomes["run_state"]
    assert case["facts"]["run_state"] == "failed"
    assert case["facts"]["run_error_code"] == "tool_unsupported_graph", case["facts"]
    # No output artifact exists, so nothing may be invented for the assertions
    # that need one.
    assert case["facts"].get("output_artifact_id") is None, case["facts"]
    for kind in ("json_equals", "citation_sources"):
        assert outcomes[kind]["passed"] is False, outcomes[kind]
        assert outcomes[kind]["actual"] == "not_collected", outcomes[kind]
    # expected=0 is a real, satisfied assertion against the real empty ledger.
    assert outcomes["effect_count"]["passed"] is True, outcomes["effect_count"]
    assert outcomes["effect_count"]["actual"] == "0", outcomes["effect_count"]
    assert case["facts"]["effect_counts"] == {}, case["facts"]

    # The result is traceable to the evaluation's frozen identity and to a real
    # platform run owned by the evaluation owner.
    provenance = document["provenance"]
    assert provenance["snapshot_id"] == evaluation["snapshot_id"], provenance
    assert document["manifest_sha256"], document
    case_runs = t052.case_run_rows(t052_stack.dbname, evaluation_id)
    assert len(case_runs) == 1, case_runs
    run = t052.run_row(t052_stack.dbname, case_runs[0]["run_id"])
    assert run["owner_id"] == owner.creator.principal_id, run
    assert run["state"] == "failed", run
    assert run["execution_snapshot_id"] == evaluation["snapshot_id"], run
    assert run["manifest_sha256"] == document["manifest_sha256"], run
    input_row = t052.artifact_row(t052_stack.dbname, run["input_artifact_id"])
    assert input_row["purpose"] == "run_input", input_row
    assert input_row["owner_id"] == owner.creator.principal_id, input_row
    assert run["input_artifact_id"] in t052.run_artifact_ids(t052_stack.dbname, run["id"])
    assert t052.operation_count(t052_stack.dbname, run["id"]) == 0

    # The contract reads project exactly the contract fields.
    evaluation_view = t052.get_evaluation(owner.creator, evaluation_id)
    assert evaluation_view.status == 200, evaluation_view.text
    assert set(evaluation_view.json) == t052.EVALUATION_FIELDS, evaluation_view.json
    report_view = t052.get_report(owner.creator, evaluation["report_id"])
    assert report_view.status == 200, report_view.text
    assert set(report_view.json) == t052.REPORT_FIELDS, report_view.json
    assert report_view.json["status"] == "failed", report_view.json


def test_a_timeout_is_not_run_and_never_passed(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit_cases(
        t052_stack,
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
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    # T062: a cancelled run never finished. The assertion outcomes are recorded
    # as evidence, but the case verdict is a refusal and the report cannot pass.
    assert report["status"] == "failed", (report, document)
    case = document["cases"][0]
    assert case["status"] == "failed", case
    assert case["verdict"] == "failed", case
    assert case["reason_code"] == "run_cancelled", case
    assert case["facts"]["run_state"] == "cancelled", case["facts"]
    assert all(outcome["passed"] for outcome in case["assertions"]), case["assertions"]
    run_id = t052.case_run_rows(t052_stack.dbname, evaluation_id)[0]["run_id"]
    run = t052.run_row(t052_stack.dbname, run_id)
    assert run["state"] == "cancelled", run
    assert run["owner_id"] == owner.creator.principal_id, run
    # The cancelled run published no result and performed no real side effect.
    assert run["output_artifact_id"] is None, run
    assert t052.operation_count(t052_stack.dbname, run_id) == 0
    # The quality slot is present, explicitly informational and unscored.
    assert document["quality"]["informational"] is True, document["quality"]
    assert document["quality"]["score"] is None, document["quality"]
    assert document["synthetic"] is False, document
    assert json.dumps(document["limits"]).find("published_version") >= 0, document["limits"]
