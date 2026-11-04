"""T062 acceptance: the real runner never passes a cancelled or unfinished case.

T052's positive path deliberately drove a predefined ``cancel`` interaction and
accepted a ``passed`` report because every assertion evaluated true against the
cancelled run. T062 makes that verdict illegal: a run that never finished is not
a pass, no matter what the checker can prove about the state it stopped in. The
case verdict must be a refusal and the report status must not be ``passed``.

Everything here is the real path: public submission, real background job, real
two-pool maintenance process, real cancel transition, real sealed report and the
real completion guard.
"""

from __future__ import annotations

from pathlib import Path

from support import t062
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _run_pools_until_terminal(stack, evaluation_id: str, tmp_path: Path, *, case_timeout: str):
    log = tmp_path / "pools.log"
    process, handle = t062.start_pools(stack, log_path=log, case_timeout=case_timeout)
    try:
        return t062.wait_for_evaluation(
            stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=120
        )
    finally:
        t062.stop_process(process, handle, log)


def test_a_cancelled_case_never_passes(t052_stack, tmp_path: Path) -> None:
    """A run that really reaches ``cancelled`` cannot be the basis of a pass."""
    owner = t062.minimal_owner(t052_stack, login="t062-cancelled-owner")
    case = t062.evaluation_case(
        case_id="cancel-before-execution",
        assertions=[
            # Both assertions are *true* of the cancelled run: the run state is
            # cancelled and the real effect ledger is empty. A checker-only
            # verdict would therefore be "passed" — which is exactly the verdict
            # this ticket forbids.
            t062.assertion_run_state("cancelled"),
            t062.assertion_effect_count("test.send", 0),
        ],
    )
    case["input"] = {"interactions": [{"kind": "cancel"}]}
    evaluation_id = t062.submit_evaluation_for(t052_stack, owner, [case])

    evaluation = _run_pools_until_terminal(
        t052_stack, evaluation_id, tmp_path, case_timeout="30s"
    )
    # The evaluation itself is a successful job with a business verdict report.
    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["report_id"] is not None, evaluation

    report = t062.report_row(t052_stack.dbname, evaluation["report_id"])
    document = t062.report_document(t052_stack, report["result_artifact_id"])
    case_result = document["cases"][0]

    assert case_result["status"] == "failed", case_result
    assert case_result["verdict"] == "failed", case_result
    assert case_result["reason_code"] == "run_cancelled", case_result
    assert "cancel" in case_result["detail"].lower(), case_result
    # The assertion outcomes are still recorded as evidence, never as a verdict.
    assert [outcome["passed"] for outcome in case_result["assertions"]] == [True, True], (
        case_result["assertions"]
    )
    assert case_result["facts"]["run_state"] == "cancelled", case_result["facts"]
    assert document["status"] == "failed", document
    assert report["status"] == "failed", report
    assert all(item["status"] != "passed" for item in document["cases"]), document["cases"]

    # The case's truth is a real cancelled platform run owned by the owner.
    case_runs = t062.case_run_rows(t052_stack.dbname, evaluation_id)
    assert len(case_runs) == 1, case_runs
    run = t062.run_row(t052_stack.dbname, case_runs[0]["run_id"])
    assert run["state"] == "cancelled", run
    assert run["owner_id"] == owner.creator.principal_id, run


def test_an_incomplete_case_is_not_run_and_never_passed(t052_stack, tmp_path: Path) -> None:
    """A case whose run never reaches a terminal state cannot count as passed.

    The maintenance process is real but no supervisor executes the case's run, so
    the bounded case wait expires. The window is 5s (BRIEF §6.1), exercised
    against a genuinely queued run row rather than a clock race.
    """
    owner = t062.minimal_owner(t052_stack, login="t062-incomplete-owner")
    evaluation_id = t062.submit_evaluation_for(
        t052_stack,
        owner,
        [
            t062.evaluation_case(
                case_id="never-executed",
                assertions=[t062.assertion_run_state("succeeded")],
            )
        ],
        case_timeout="5s",
    )
    evaluation = _run_pools_until_terminal(
        t052_stack, evaluation_id, tmp_path, case_timeout="5s"
    )
    assert evaluation["state"] == "succeeded", evaluation
    report = t062.report_row(t052_stack.dbname, evaluation["report_id"])
    document = t062.report_document(t052_stack, report["result_artifact_id"])
    case_result = document["cases"][0]

    assert case_result["status"] == "not_run", case_result
    assert case_result["verdict"] == "not_run", case_result
    assert case_result["reason_code"] == "run_timeout", case_result
    assert report["status"] == "not_run", report
    assert report["status"] != "passed", report
    # The assertion is evaluated against uncollected truth and cannot pass.
    assert case_result["assertions"][0]["passed"] is False, case_result["assertions"]
    # The run really exists and really never executed.
    case_runs = t062.case_run_rows(t052_stack.dbname, evaluation_id)
    assert len(case_runs) == 1, case_runs
    run = t062.run_row(t052_stack.dbname, case_runs[0]["run_id"])
    assert run["state"] == "queued", run


def test_a_passed_report_is_accepted_over_a_real_completed_run(t052_stack, tmp_path: Path) -> None:
    """The guard is not refuse-all: a report backed by a finished run is accepted.

    The minimal application's run genuinely executes and genuinely fails with
    ``tool_unsupported_graph`` (the documented F13A boundary). A case that asserts
    that expected failure is a legitimate pass: the run finished, so the case
    verdict is the checker's verdict and the immutable report is recorded.
    """
    owner = t062.minimal_owner(t052_stack, login="t062-passed-owner")
    evaluation_id = t062.submit_evaluation_for(
        t052_stack,
        owner,
        [
            t062.evaluation_case(
                case_id="expected-failure",
                assertions=[
                    t062.assertion_run_state("failed"),
                    t062.assertion_effect_count("test.send", 0),
                ],
            )
        ],
    )
    log = tmp_path / "pools.log"
    process, handle = t062.start_pools(t052_stack, log_path=log)
    try:
        reports = t062.drive_supervisor_until(t052_stack, evaluation_id)
    finally:
        t062.stop_process(process, handle, log)
    assert reports, "the executor never claimed the case's run"

    evaluation = t062.wait_for_evaluation(
        t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=120
    )
    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["report_id"] is not None, evaluation
    report = t062.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "passed", report
    document = t062.report_document(t052_stack, report["result_artifact_id"])
    case_result = document["cases"][0]
    assert case_result["status"] == "passed", case_result
    assert case_result["facts"]["run_state"] == "failed", case_result["facts"]

    case_runs = t062.case_run_rows(t052_stack.dbname, evaluation_id)
    run = t062.run_row(t052_stack.dbname, case_runs[0]["run_id"])
    assert run["state"] == "failed", run
    assert run["error_code"] == "tool_unsupported_graph", run
    # A passed report is still a real contract report for its owner.
    assert t062.get_report(owner.creator, report["id"]).json["status"] == "passed"
