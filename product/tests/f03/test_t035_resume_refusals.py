"""T035 acceptance: a resume is refused honestly when it must not happen.

Before a resumed attempt is dispatched the platform re-checks the Run owner's
permission, the budget and the frozen active-time deadline. A failure after the
wait is over commits ``failed(permission_revoked|budget_exhausted|
deadline_exceeded)`` with no output artifact and no resume; a recorded cancel
intent simply leaves the Run waiting (T036 owns the confirmed cancel transition).
There is never a fake resume job or sealed payload.
"""

from __future__ import annotations

import json

from support import t025, t029, t031, t032, t035
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _waiting_with_decision(stack, *, execution_unit_id: str, shift_seconds: int | None = None):
    owner, lease, _ = t035.waiting_run_with_lease(
        stack,
        execution_unit_id=execution_unit_id,
        checkpoint_id="ckpt-refusal",
        shift_seconds=shift_seconds,
    )
    supervisor = t035.execution_client(stack, "supervisor")
    response = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="refusal-ref"
    )
    assert response.status == 200, response.text
    return owner, lease, supervisor


def _assert_terminal_failure(stack, lease, code: str) -> None:
    run = t035.run_state_row(stack.dbname, lease["run_id"])
    assert run["state"] == "failed", run
    assert run["phase"] == "idle", run
    assert run["finished_at"] is not None, run
    assert run["error_code"] == code, run
    assert run["output_artifact_id"] is None, run
    jobs = t035.jobs_for_run(stack.dbname, lease["run_id"])
    assert [(job["kind"], job["state"]) for job in jobs] == [("start", "completed")], jobs
    assert t035.resume_jobs(stack.dbname, lease["run_id"]) == []
    assert t035.resume_artifact_count(stack.dbname) == 0
    assert t035.job_row(stack.dbname, lease["job_id"])["state"] == "completed"
    attempt = t035.attempt_row(stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None
    assert t035.quota_counter(stack.dbname)["used"] == 0
    assert t035.allocation_for_job(stack.dbname, lease["job_id"])["released_at"] is not None
    statuses = t032.status_events(stack.dbname, lease["run_id"])
    assert statuses[-1][2] == {"state": "failed", "error_code": code}, statuses


def test_disabled_owner_fails_the_run_with_permission_revoked(t029_stack) -> None:
    owner, lease, supervisor = _waiting_with_decision(
        t029_stack, execution_unit_id="unit-permission"
    )
    # The permission re-check happens immediately before the resume; disabling the
    # owner here makes the stored earlier authorization insufficient.
    t035.disable_owner(owner)
    exit_response = t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-permission"
    )
    assert exit_response.status == 200, exit_response.text
    _assert_terminal_failure(t029_stack, lease, "permission_revoked")


def test_zero_budget_fails_the_run_instead_of_resuming(t029_stack) -> None:
    owner, lease, supervisor = _waiting_with_decision(
        t029_stack, execution_unit_id="unit-budget"
    )
    t035.set_run_budget(t029_stack.dbname, lease["run_id"], "0")
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-budget"
    ).status == 200
    _assert_terminal_failure(t029_stack, lease, "budget_exhausted")


def test_exceeded_deadline_fails_the_run_instead_of_resuming(t029_stack) -> None:
    owner, lease, supervisor = _waiting_with_decision(
        t029_stack, execution_unit_id="unit-deadline", shift_seconds=2
    )
    # Waiting itself accrues nothing, so the deterministic fixture shrinks the
    # frozen limit below the already-settled interval (T034's own test shape).
    t035.set_run_max_active_ms(t029_stack.dbname, lease["run_id"], 500)
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-deadline"
    ).status == 200
    _assert_terminal_failure(t029_stack, lease, t035.DEADLINE_CODE)
    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["cancel_requested_at"] is not None, run


def test_cancel_intent_blocks_the_resume_and_leaves_the_run_waiting(t029_stack) -> None:
    owner, lease, supervisor = _waiting_with_decision(
        t029_stack, execution_unit_id="unit-cancel"
    )
    t035.set_cancel_requested(t029_stack.dbname, lease["run_id"])
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-cancel"
    ).status == 200

    # Cancel intent wins over a resume, but T036 owns the confirmed cancelled
    # transition: T035 must not fake it.
    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "waiting_approval", run
    assert run["finished_at"] is None, run
    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["exit_confirmed_at"] is not None, waiting
    assert waiting["resume_job_id"] is None, waiting
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])
    assert t035.resume_artifact_count(t029_stack.dbname) == 0

    # A repeated exit after the refused resume changes nothing.
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-cancel"
    ).status == 200
    assert t035.run_state_row(t029_stack.dbname, lease["run_id"]) == run
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])
