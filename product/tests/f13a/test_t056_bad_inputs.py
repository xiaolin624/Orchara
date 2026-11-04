"""T056 acceptance: bad inputs are located, expected and never silently passed.

Every case here runs the real preset sales tool in the real sandbox. The case's
verdict is ``passed`` exactly when the run really failed with the declared
business error code (``missing_column``, ``duplicate_order_id``,
``negative_amount``, ``formula_not_supported``, ``row_limit_exceeded``), so the
report shows the located failure reason instead of a vague failure.

The timeout case is a real 20 MiB input that the sandbox kills at the minimum
(1 s) tool timeout: the stdlib CSV parse of ~20 million blank lines measures
~2.2 s in the sandbox distro (more under load), so the case exceeds the timeout
by a real margin instead of racing it. The missing-fixture and malformed-binding
cases exercise the failure boundary: neither can ever produce a passed case.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import t056
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _expected_codes(document: dict) -> dict[str, str]:
    return {
        case["case_id"]: case["input"]["expected_failure"]["error_code"]
        for case in document["cases"]
    }


def test_bad_input_cases_pass_only_for_their_expected_located_failure(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    document = t056.load_dataset("bad_inputs.json")
    expected_codes = _expected_codes(document)
    assert len(expected_codes) == 5

    evaluation, report, sealed = t056.run_evaluation(
        t056_stack, owner, document, log_path=Path(t056_stack.object_dir).parent / "pools.log"
    )
    assert evaluation["state"] == "succeeded", evaluation
    assert report["status"] == "passed", (
        report["status"],
        [
            (
                case["id"],
                case["status"],
                case.get("reason_code"),
                (case.get("facts") or {}).get("run_error_code"),
                case.get("detail"),
                [
                    (outcome["kind"], outcome["expected"], outcome["actual"], outcome["passed"])
                    for outcome in case.get("assertions") or []
                ],
            )
            for case in sealed["cases"]
        ],
    )
    assert sealed["case_count"] == len(expected_codes)

    case_runs = t056.t052.case_run_rows(t056_stack.dbname, evaluation["id"])
    assert len(case_runs) == len(expected_codes), case_runs

    for case in sealed["cases"]:
        code = expected_codes[case["id"]]
        assert case["status"] == "passed", (code, case)
        assert case["verdict"] == "passed", case
        facts = case["facts"]
        assert facts["run_state"] == "failed", (code, facts)
        assert facts["run_error_code"] == code, (code, facts)
        assert facts["task_input_artifact_id"], facts
        outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
        # The run failure is the expected business failure, not an infrastructure
        # error and not a generic "failed".
        assert outcomes["failure_reason"]["expected"] == code, outcomes["failure_reason"]
        assert outcomes["failure_reason"]["actual"] == code, outcomes["failure_reason"]
        assert outcomes["failure_reason"]["passed"] is True, outcomes["failure_reason"]
        assert outcomes["run_state"]["actual"] == "failed", outcomes["run_state"]
        # A rejected input writes no controlled side effect.
        assert outcomes["effect_count"]["passed"] is True, outcomes["effect_count"]
        assert outcomes["effect_count"]["actual"] == "0", outcomes["effect_count"]

    # The per-case read shows the actual diagnostics attachment for each failure,
    # and the document's own located reason is the same code the case expected.
    response = t056.get_cases(owner.creator, report["id"])
    assert response.status == 200, response.text
    for case in response.json["cases"]:
        code = expected_codes[case["case_id"]]
        assert case["verdict"] == "passed", case
        labels = t056.attachment_labels(case)
        assert "diagnostics" in labels, (code, labels)
        diagnostics = next(
            attachment
            for attachment in case["attachments"]
            if attachment["label"] == "diagnostics"
        )
        document_bytes = t056.t052.report_document(t056_stack, diagnostics["artifact_id"])
        assert document_bytes["error_code"] == code, (code, document_bytes)
        assert document_bytes["status"] == "failed", document_bytes
        # The per-row rejections locate the offending row/column; the row-limit
        # rejection is a file-level rule and has no single row to point at.
        if code != "row_limit_exceeded":
            assert document_bytes.get("location"), (code, document_bytes)
        assert diagnostics["media_type"] == "application/json", diagnostics


def test_a_tool_timeout_is_a_passed_case_with_the_expected_failure(t056_stack) -> None:
    """The real 20 MiB blank-line input exceeds the minimum (1 s) tool timeout.

    The sandbox's csv parse of the 20 MiB fixture measures ~2.2 s in the sandbox
    distro (more under load), and the deployment timeout rounds to whole seconds,
    so the minimum 1 s is the only bound with a real margin. If a very fast host
    completes the run anyway, the case observes ``empty_input`` (the tool finished
    instead of being killed): that is an environment timing miss, not a product
    verdict, so the evaluation is retried. A case that failed for any other reason
    is asserted, never retried into a pass.
    """
    owner = t056.sales_owner(t056_stack)
    document = t056.load_dataset("timeout.json")
    log_path = Path(t056_stack.object_dir).parent / "pools.log"
    evaluation: dict = {}
    report: dict = {}
    sealed: dict = {}
    for attempt in range(1, 4):
        evaluation, report, sealed = t056.run_evaluation(
            t056_stack,
            owner,
            document,
            sandbox_timeout="1s",
            case_timeout="60s",
            log_path=log_path,
        )
        if report["status"] == "passed":
            break
        case = sealed["cases"][0]
        observed = (case.get("facts") or {}).get("run_error_code")
        print(f"T056: timeout case observed {observed!r} on attempt {attempt}")
        if observed != "empty_input":
            break
    assert evaluation["state"] == "succeeded", evaluation
    assert report["status"] == "passed", (report, sealed)
    case = sealed["cases"][0]
    assert case["status"] == "passed", case
    assert case["facts"]["run_state"] == "failed", case["facts"]
    assert case["facts"]["run_error_code"] == "tool_timeout", case["facts"]
    outcomes = {outcome["kind"]: outcome for outcome in case["assertions"]}
    assert outcomes["failure_reason"]["actual"] == "tool_timeout", outcomes["failure_reason"]
    assert outcomes["failure_reason"]["passed"] is True
    # The run is the platform's own terminal failure, not a collector timeout.
    run = t056.t052.run_row(
        t056_stack.dbname,
        t056.t052.case_run_rows(t056_stack.dbname, evaluation["id"])[0]["run_id"],
    )
    assert run["state"] == "failed", run
    assert run["error_code"] == "tool_timeout", run
    assert run["output_artifact_id"] is None, run


def test_a_missing_fixture_blocks_the_case_and_never_passes(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    document = t056.load_dataset("sales_happy_csv.json")
    # Deliberately do NOT upload the fixture the case names.
    submitted = t056.submit_dataset(t056_stack, owner, document)
    evaluation_id = submitted["id"]
    log_path = Path(t056_stack.object_dir).parent / "pools.log"
    process, handle = t056.t052.start_pools(t056_stack, log_path=log_path)
    try:
        t056.t052.wait_for_evaluation(t056_stack.dbname, evaluation_id, {"succeeded", "failed"})
    finally:
        t056.t052.stop_process(process, handle, log_path)

    evaluation = t056.t052.evaluation_row(t056_stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    report = t056.t052.report_row(t056_stack.dbname, evaluation["report_id"])
    # Nothing was collected: one blocked case is not a failed *check*, it is a
    # not_run report (the T052 status rule), and it can never become passed.
    assert report["status"] == "not_run", report
    sealed = t056.t052.report_document(t056_stack, report["result_artifact_id"])
    case = sealed["cases"][0]
    assert case["status"] == "blocked", case
    assert case["verdict"] == "blocked", case
    assert case["reason_code"] == "task_input_unavailable", case
    # A blocked case never produced a run at all.
    assert t056.t052.case_run_rows(t056_stack.dbname, evaluation_id) == []


def test_a_malformed_reserved_binding_is_refused_at_submit_time(t056_stack) -> None:
    owner = t056.sales_owner(t056_stack)
    document = t056.load_dataset("sales_happy_csv.json")
    broken = json.loads(json.dumps(document))
    broken["cases"][0]["input"]["task_input"]["sha256"] = "not-a-sha"
    artifact_id = t056.upload_dataset(
        t056_stack, owner.creator, t056.dataset_bytes(broken)
    )
    response = t056.t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t056.t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    assert response.status == 422, response.text
    body = response.json
    assert body["error"]["code"] == "validation_failed", body
    located = body["error"]["details"]["errors"]
    assert located and located[0]["pointer"] == "/cases/0/input/task_input/sha256", located

    bad_expectation = json.loads(json.dumps(document))
    bad_expectation["cases"][0]["input"]["expected_failure"] = {"error_code": "Not A Code"}
    artifact_id = t056.upload_dataset(
        t056_stack, owner.creator, t056.dataset_bytes(bad_expectation)
    )
    response = t056.t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t056.t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    assert response.status == 422, response.text
    located = response.json["error"]["details"]["errors"]
    assert located[0]["pointer"] == "/cases/0/input/expected_failure/error_code", located
