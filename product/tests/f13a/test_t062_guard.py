"""T062 acceptance: the completion guard proves report claims against real rows.

A sealed report is only a claim. These tests upload crafted reports through the
real job-scoped artifact path and submit them as a claimed success; the guard is
the only thing that can accept them, and it must refuse every report whose
binding fields disagree with the evaluation, and every passed case that is not
backed by a *finished* run of this evaluation.

The runs here are real: they are created through the server-side evaluation-run
bridge (never SQL), one is left incomplete on purpose and one is really
cancelled through the owner's cancel endpoint. Nothing mocks the guard.
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


def _submit_cases(stack, owner, cases, **kwargs) -> str:
    return t062.submit_evaluation_for(stack, owner, cases, **kwargs)


def test_a_forged_passed_report_over_unfinished_or_cancelled_runs_is_refused(
    t052_stack,
) -> None:
    """A passed case must be backed by a finished run; one cancelled, one queued.

    Both case runs really exist for this evaluation, and the crafted document
    names their real run ids. A guard that only checked the document's internal
    consistency would accept the claimed ``passed``; the platform facts (queued /
    cancelled) must make it refuse instead.
    """
    owner = t062.minimal_owner(t052_stack, login="t062-forged-run-owner")
    evaluation_id = _submit_cases(
        t052_stack,
        owner,
        [
            t062.evaluation_case(
                case_id="still-running",
                assertions=[t062.assertion_run_state("succeeded")],
            ),
            t062.evaluation_case(
                case_id="cancelled-out",
                assertions=[t062.assertion_run_state("succeeded")],
            ),
        ],
    )
    evaluation = t062.evaluation_row(t052_stack.dbname, evaluation_id)
    client, lease = t062.claim_evaluation(t052_stack)

    unfinished = t062.create_case_run(
        client, lease, case_id="still-running", input_value={"text": "never executed"}
    )
    cancelled = t062.create_case_run(
        client, lease, case_id="cancelled-out", input_value={"text": "cancelled"}
    )
    cancelled_response = t062.cancel_run(owner.creator, cancelled["run_id"])
    assert cancelled_response.status == 202, cancelled_response.text

    unfinished_run = t062.run_row(t052_stack.dbname, unfinished["run_id"])
    cancelled_run = t062.run_row(t052_stack.dbname, cancelled["run_id"])
    assert unfinished_run["state"] == "queued", unfinished_run
    assert cancelled_run["state"] == "cancelled", cancelled_run

    document = t062.crafted_document(
        t052_stack,
        evaluation,
        status="passed",
        cases=[
            t062.passed_case(
                "still-running", run_id=unfinished["run_id"], run_state="queued", expected="queued"
            ),
            t062.passed_case(
                "cancelled-out",
                run_id=cancelled["run_id"],
                run_state="cancelled",
                expected="cancelled",
            ),
        ],
    )
    artifact_id = t062.upload_ready_report(
        t052_stack, client, lease, t062.report_bytes(document)
    )
    before = t062.report_count(t052_stack.dbname)
    response = t062.submit_result(client, lease, result_artifact_id=artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "failed", response.json
    assert response.json["error_code"] == t062.REPORT_BINDING_MISMATCH, response.json

    failed = t062.evaluation_row(t052_stack.dbname, evaluation_id)
    assert failed["state"] == "failed", failed
    assert failed["error_code"] == t062.REPORT_BINDING_MISMATCH, failed
    assert failed["report_id"] is None, failed
    assert t062.report_count(t052_stack.dbname) == before

    # The real runs are untouched: the refusal never rewrote platform truth.
    assert t062.run_row(t052_stack.dbname, unfinished["run_id"])["state"] == "queued"
    assert t062.run_row(t052_stack.dbname, cancelled["run_id"])["state"] == "cancelled"


def test_a_report_with_a_wrong_manifest_dataset_or_evaluation_binding_is_refused(
    t052_stack,
) -> None:
    """Each binding field is load-bearing: break one and the guard refuses.

    The crafted document is otherwise exactly what the runner would write (right
    status, right provenance, a passing assertion outcome), so the refusal can
    only come from the binding the test broke.
    """
    owner = t062.minimal_owner(t052_stack, login="t062-binding-owner")
    other_owner = t062.minimal_owner(t052_stack, login="t062-binding-other")
    # A second, real evaluation supplies a genuine but foreign dataset artifact id
    # and a foreign evaluation id for the two id variants.
    foreign_evaluation_id = _submit_cases(
        t052_stack,
        other_owner,
        [
            t062.evaluation_case(
                case_id="foreign-case",
                assertions=[t062.assertion_run_state("succeeded")],
            )
        ],
    )
    foreign = t062.evaluation_row(t052_stack.dbname, foreign_evaluation_id)
    # Claim the foreign evaluation's job so it is not a ready job the loop's
    # ``claim`` could pick instead of the evaluation under test. Its lease is
    # simply left to expire with the test.
    t062.claim_evaluation(t052_stack)

    variants = [
        ("manifest_sha256", {"manifest_sha256": "1" * 64}, t062.REPORT_BINDING_MISMATCH),
        ("dataset_sha256", {"dataset_sha256": "2" * 64}, t062.REPORT_BINDING_MISMATCH),
        (
            "dataset_artifact_id",
            {"dataset_artifact_id": foreign["dataset_artifact_id"]},
            t062.REPORT_BINDING_MISMATCH,
        ),
        ("evaluation_id", {"evaluation_id": foreign_evaluation_id}, t062.REPORT_BINDING_MISMATCH),
    ]

    for label, overrides, expected_code in variants:
        evaluation_id = _submit_cases(
            t052_stack,
            owner,
            [
                t062.evaluation_case(
                    case_id="binding-case",
                    assertions=[t062.assertion_run_state("succeeded")],
                )
            ],
        )
        evaluation = t062.evaluation_row(t052_stack.dbname, evaluation_id)
        client, lease = t062.claim_evaluation(t052_stack)
        document = t062.crafted_document(
            t052_stack,
            evaluation,
            status="passed",
            cases=[
                t062.passed_case(
                    "binding-case", run_id=t062.new_uuid(), run_state="succeeded"
                )
            ],
            **overrides,
        )
        artifact_id = t062.upload_ready_report(
            t052_stack, client, lease, t062.report_bytes(document)
        )
        before = t062.report_count(t052_stack.dbname)
        response = t062.submit_result(client, lease, result_artifact_id=artifact_id)
        assert response.status == 200, (label, response.text)
        assert response.json["state"] == "failed", (label, response.json)
        assert response.json["error_code"] == expected_code, (label, response.json)

        failed = t062.evaluation_row(t052_stack.dbname, evaluation_id)
        assert failed["state"] == "failed", (label, failed)
        assert failed["error_code"] == expected_code, (label, failed)
        assert failed["report_id"] is None, (label, failed)
        assert t062.report_count(t052_stack.dbname) == before, label


def test_a_borrowed_report_artifact_or_document_cannot_become_another_result(
    t052_stack, tmp_path: Path
) -> None:
    """Borrowing A's sealed report cannot make B's evaluation pass.

    Two independent owners, two real evaluations. B's job first tries to name A's
    ready report artifact directly (the job-scoped link refuses it), then uploads
    A's report *bytes* under B's own job (the document's evaluation id is A's, so
    the completion guard refuses it).
    """
    owner_a = t062.minimal_owner(t052_stack, login="t062-borrow-a")
    owner_b = t062.minimal_owner(t052_stack, login="t062-borrow-b")

    # A's evaluation completes through the real pools. Its only case is cancelled
    # (a real terminal state), so the sealed report is `failed`, not `passed`;
    # what matters here is that the file is a genuine report for A.
    case = t062.evaluation_case(
        case_id="a-case", assertions=[t062.assertion_run_state("cancelled")]
    )
    case["input"] = {"interactions": [{"kind": "cancel"}]}
    evaluation_a = _submit_cases(t052_stack, owner_a, [case])
    log = tmp_path / "pools.log"
    process, handle = t062.start_pools(t052_stack, log_path=log)
    try:
        completed_a = t062.wait_for_evaluation(
            t052_stack.dbname, evaluation_a, {"succeeded", "failed"}, timeout=120
        )
    finally:
        t062.stop_process(process, handle, log)
    assert completed_a["report_id"] is not None, completed_a
    report_a = t062.report_row(t052_stack.dbname, completed_a["report_id"])
    a_bytes = t062.report_raw_bytes(t052_stack, report_a)

    evaluation_b = _submit_cases(
        t052_stack,
        owner_b,
        [
            t062.evaluation_case(
                case_id="b-case", assertions=[t062.assertion_run_state("succeeded")]
            )
        ],
    )
    client, lease = t062.claim_evaluation(t052_stack)
    evaluation_b_row = t062.evaluation_row(t052_stack.dbname, evaluation_b)

    # 1. Naming A's artifact directly: the artifact is not linked to B's job.
    direct = t062.submit_result(
        client, lease, result_artifact_id=report_a["result_artifact_id"]
    )
    assert direct.status == 422, direct.text
    assert direct.json["error"]["code"] == "validation_failed", direct.text
    assert "not linked to this job" in direct.text, direct.text

    # 2. Uploading A's exact report bytes under B's job: the document names A.
    artifact_id = t062.upload_ready_report(t052_stack, client, lease, a_bytes)
    forged = t062.submit_result(client, lease, result_artifact_id=artifact_id)
    assert forged.status == 200, forged.text
    assert forged.json["state"] == "failed", forged.json
    assert forged.json["error_code"] == t062.REPORT_BINDING_MISMATCH, forged.json

    failed = t062.evaluation_row(t052_stack.dbname, evaluation_b)
    assert failed["state"] == "failed", failed
    assert failed["report_id"] is None, failed
    assert t062.report_row(t052_stack.dbname, report_a["id"])["status"] != "passed"
    # B never gained a report of its own.
    assert t062.report_count(t052_stack.dbname) == 1
    # A's report artifact still belongs to A, not B.
    assert t062.artifact_row(t052_stack.dbname, artifact_id)["owner_id"] == (
        evaluation_b_row["owner_id"]
    )
