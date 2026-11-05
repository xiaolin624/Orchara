"""F11/T076 (#77): recovery refuses a Run whose input was deleted -- every input.

The F03/T038 recovery gate already refused a deleted ``runs.input_artifact_id``
(T069's ``input_deleted``). This suite proves the same predicate for *every* input
the Run owns, because the supervisor discovers inputs through
``platform.run_artifacts`` (the request path links the sealed message *and* each
attachment there), and it replays each refusal twice: the second attempt must
refuse with the same evidence and write nothing.

The last test proves the execution side of the same boundary: a queued Run whose
input was deleted is cancelled by the delete and can never be leased.
"""

from __future__ import annotations

from support import t028, t029, t035, t038, t069, t071, t076
from support import stack as st


def _assert_still_parked(stack, run_id: str, *, job_id: str, token: str) -> None:
    row = t069.run_rows(stack.dbname, run_id)
    assert row["state"] == "recovery_pending", row
    assert t038.recovery_evidence_rows(stack.dbname, run_id) == []
    job = t038.job_row(stack.dbname, job_id)
    assert job["state"] == "held", job
    assert t038.attempt_is_live(stack.dbname, token)


def _failed_check_names(refusal: dict, name: str) -> None:
    failed = [check for check in refusal.get("checks", []) if check["status"] == "failed"]
    assert failed, refusal
    assert failed[-1]["name"] == name, refusal


def test_a_deleted_primary_input_is_refused_twice_with_named_evidence(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner, lease, _park = t038.parked_run(stack, execution_unit_id="unit-t076-primary")
    run_id = lease["run_id"]
    input_artifact = t069.run_rows(stack.dbname, run_id)["input_artifact_id"]
    assert input_artifact, t069.run_rows(stack.dbname, run_id)

    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, input_artifact)
    )
    deleted = t069.artifact_row(stack.dbname, input_artifact)
    assert deleted["deleted_at"] is not None and deleted["state"] == "deleted", deleted
    assert t069.run_rows(stack.dbname, run_id)["state"] == "recovery_pending"

    for attempt in (1, 2):
        completed, refusal = t038.recover(
            stack,
            run_id,
            evidence=t038.recovery_evidence("unit-t076-primary"),
            directory=tmp_path,
        )
        assert completed.returncode == 1, (
            f"attempt {attempt} must be refused:\n{completed.stdout}{completed.stderr}"
        )
        t038.assert_refused(refusal, t038.CODE_INPUT_DELETED)
        _failed_check_names(refusal, "input_artifact")
        assert input_artifact in refusal["message"], refusal
        _assert_still_parked(stack, run_id, job_id=lease["job_id"], token=lease["attempt_token"])
        # The refusal wrote nothing: no evidence row, no new attempt, no job change.
        assert t038.recovery_evidence_rows(stack.dbname, run_id) == []
        assert len(t038.attempts_for_run(stack.dbname, run_id)) == 1
        st.assert_error(
            t028.get_artifact(owner.creator, input_artifact),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )


def test_recovery_rechecks_every_linked_input_artifact_not_only_the_primary(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner, lease, _park = t038.parked_run(stack, execution_unit_id="unit-t076-linked")
    run_id = lease["run_id"]
    primary = t069.run_rows(stack.dbname, run_id)["input_artifact_id"]
    attachment = t071.ready_artifact(
        stack, owner.creator, b"t076 linked attachment: deleted after the claim\n"
    )
    # The row the real request path writes for a second input (submit.go links the
    # sealed message and every attachment with relation='input'). Seeding it keeps
    # this test about the recovery predicate, not about reproducing T074's publisher.
    t076.link_extra_input(stack.dbname, run_id, attachment)
    assert t076.linked_input_artifacts(stack.dbname, run_id) == sorted([primary, attachment])

    # Negative control: with every input alive the same evidence passes the input
    # check. --check-only proves the predicate without consuming the parked run.
    report = t038.recover_ok(
        stack,
        run_id,
        evidence=t038.recovery_evidence("unit-t076-linked"),
        directory=tmp_path,
        extra=["--check-only"],
    )
    assert report["status"] == "check_only", report
    assert any(
        check["name"] == "input_artifact" and check["status"] == "passed"
        for check in report["checks"]
    ), report

    # Delete the *attachment* through the real endpoint: the primary input is
    # still alive, so a primary-only check would let the recovery through.
    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, attachment)
    )
    assert t069.artifact_row(stack.dbname, primary)["deleted_at"] is None
    assert t069.artifact_row(stack.dbname, attachment)["deleted_at"] is not None
    assert t069.run_rows(stack.dbname, run_id)["state"] == "recovery_pending"
    assert t069.audit_events(
        stack.dbname, "deletion.run_cancel", resource_id=run_id
    ) == []

    for attempt in (1, 2):
        completed, refusal = t038.recover(
            stack,
            run_id,
            evidence=t038.recovery_evidence("unit-t076-linked"),
            directory=tmp_path,
        )
        assert completed.returncode == 1, (
            f"attempt {attempt} must be refused:\n{completed.stdout}{completed.stderr}"
        )
        t038.assert_refused(refusal, t038.CODE_INPUT_DELETED)
        _failed_check_names(refusal, "input_artifact")
        assert attachment in refusal["message"], refusal
        _assert_still_parked(stack, run_id, job_id=lease["job_id"], token=lease["attempt_token"])


def test_a_waiting_run_whose_input_was_deleted_never_resumes(t069_stack) -> None:
    stack = t069_stack
    owner, lease, _response = t035.waiting_run_with_lease(
        stack, execution_unit_id="unit-t076-waiting"
    )
    run_id = lease["run_id"]
    input_artifact = t069.run_rows(stack.dbname, run_id)["input_artifact_id"]
    assert t069.run_rows(stack.dbname, run_id)["state"] == "waiting_approval"

    # The delete's cancel fan-out covers a waiting run (it could still continue).
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, input_artifact)
    )
    after_delete = t069.run_rows(stack.dbname, run_id)
    assert after_delete["cancel_requested_at"] is not None or after_delete["state"] == "cancelled", (
        after_delete
    )

    # The supervisor's exit fact arrives after the delete: the resume transaction
    # must never create a resume job for a run whose input is gone.
    exited = t035.confirm_waiting_exit(stack, lease, execution_unit_id="unit-t076-waiting")
    assert exited.status == 200, exited.text
    supervisor = t035.execution_client(stack, "supervisor")
    for attempt in (1, 2):
        decided = supervisor.resolve_waiting(
            run_id,
            kind="approval",
            outcome="approved",
            reference=f"t076-waiting-replay-{attempt}",
        )
        assert decided.status in (200, 409), decided.text
        t035.assert_no_resume(stack.dbname, run_id)
    row = t069.run_rows(stack.dbname, run_id)
    assert row["state"] in {"cancelled", "cancelling"}, row
    st.assert_error(
        t028.get_artifact(owner.creator, input_artifact), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] in {"pending", "failed", "completed"}, stored


def test_a_queued_run_whose_input_was_deleted_is_cancelled_and_never_leased(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    run = t029.queued_run(stack, owner)
    input_artifact = t069.run_rows(stack.dbname, run["id"])["input_artifact_id"]

    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, input_artifact)
    )
    row = t069.run_rows(stack.dbname, run["id"])
    assert row["state"] == "cancelled", row

    # The execution path cannot lease it and cannot start an attempt: no input, no
    # execution. Two consecutive claims stay 204.
    for _attempt in (1, 2):
        t038.claim_none(stack, execution_unit_id="unit-t076-claim")
        assert t038.attempts_for_run(stack.dbname, run["id"]) == []
    assert t069.run_rows(stack.dbname, run["id"])["state"] == "cancelled"
