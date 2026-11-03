"""T036 acceptance: unknown external writes park reconciliation, never a fake end.

The internal bridge records one safe unknown-operation code against the Run's live
attempt (worker or supervisor), resolves it once, and the one finalizer reads the
unresolved rows. A cancelled Run with an unknown operation commits
``needs_reconciliation`` (not cancelled, not revoked); resolving the last unknown
re-invokes the finalizer so the Run can become cancelled exactly once. A terminal
Run is never revived by a late record or resolution.
"""

from __future__ import annotations

from support import t035, t036
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def test_recorded_unknown_operation_is_idempotent_and_resolvable(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-unknown-basic")
    worker = t036.unknown_client(t029_stack, "worker")
    operation = t036.new_operation_id()

    recorded = worker.record_unknown(
        lease["run_id"], attempt_token=lease["attempt_token"], operation_id=operation
    )
    assert recorded.status == 200, recorded.text
    body = recorded.json
    assert body["run_id"] == lease["run_id"] and body["operation_id"] == operation, body
    assert body["reason"] == t036.UNKNOWN_REASON, body
    assert body["resolved"] is False and body["resolved_at"] is None, body
    assert body["run_state"] == "running", body
    assert t036.unresolved_unknown_count(t029_stack.dbname, lease["run_id"]) == 1
    assert t036.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "running"

    replay = worker.record_unknown(
        lease["run_id"], attempt_token=lease["attempt_token"], operation_id=operation
    )
    assert replay.status == 200 and replay.json == body, replay.text
    assert len(t036.unknown_operations(t029_stack.dbname, lease["run_id"])) == 1

    conflict = worker.record_unknown(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        operation_id=operation,
        reason="worker_crash",
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == t036.EVENT_CONFLICT, conflict.text
    row = t036.unknown_operation_row(t029_stack.dbname, lease["run_id"], operation)
    assert row["reason"] == t036.UNKNOWN_REASON, row

    resolved = worker.resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert resolved.status == 200, resolved.text
    assert resolved.json["resolved"] is True and resolved.json["resolved_at"] is not None
    assert resolved.json["run_state"] == "running", "resolving alone does not move the Run"
    assert t036.unresolved_unknown_count(t029_stack.dbname, lease["run_id"]) == 0
    first_resolved_at = resolved.json["resolved_at"]

    repeat = worker.resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert repeat.status == 200, repeat.text
    assert repeat.json["resolved_at"] == first_resolved_at, repeat.text

    # A resolved operation no longer blocks the guard: the later cancel finalizes
    # cancelled normally once the exit arrives.
    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-unknown-basic",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == "cancelled", row
    assert row["error_code"] is None, row


def test_unknown_bridge_rejects_bad_role_reason_token_and_foreign_run(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    first = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-unknown-a")
    second = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-unknown-b")
    operation = t036.new_operation_id()

    maintenance = t036.unknown_client(t029_stack, "maintenance").record_unknown(
        first["run_id"], attempt_token=first["attempt_token"], operation_id=operation
    )
    assert maintenance.status == 403, maintenance.text

    bad_reason = t036.unknown_client(t029_stack, "worker").record_unknown(
        first["run_id"],
        attempt_token=first["attempt_token"],
        operation_id=operation,
        reason="provider body: secret=leak",
    )
    assert bad_reason.status == 422, bad_reason.text
    assert bad_reason.json["error"]["code"] == "validation_failed", bad_reason.text
    assert bad_reason.json["error"]["details"]["field"] == "reason", bad_reason.text

    foreign = t036.unknown_client(t029_stack, "worker").record_unknown(
        first["run_id"], attempt_token=second["attempt_token"], operation_id=operation
    )
    assert foreign.status == 409, foreign.text
    assert foreign.json["error"]["code"] == t036.STALE_ATTEMPT, foreign.text

    missing = t036.unknown_client(t029_stack, "supervisor").resolve_unknown(
        first["run_id"], operation, attempt_token=first["attempt_token"]
    )
    assert missing.status == 404, missing.text
    assert missing.json["error"]["code"] == "resource_not_found", missing.text
    assert t036.unknown_operations(t029_stack.dbname, first["run_id"]) == []


def test_cancel_with_unknown_parks_reconciliation_then_resolving_last_cancels(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-unknown-waiting", checkpoint_id="ckpt-unknown"
    )
    operation = t036.new_operation_id()
    recorded = t036.unknown_client(t029_stack, "worker").record_unknown(
        lease["run_id"], attempt_token=lease["attempt_token"], operation_id=operation
    )
    assert recorded.status == 200, recorded.text
    # The supervisor's exit fact exists, but the waiting decision never does.
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-unknown-waiting"
    ).status == 200
    assert t036.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "waiting_approval"

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    assert response.json["state"] == t036.STATE_NEEDS_RECONCILIATION, response.text
    parked = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert parked["error_code"] == t036.UNKNOWN_CODE, parked
    assert parked["finished_at"] is None and parked["phase"] == "idle", parked
    assert t036.unresolved_unknown_count(t029_stack.dbname, lease["run_id"]) == 1
    assert t036.resume_jobs(t029_stack.dbname, lease["run_id"]) == []
    assert t036.waiting_cancel_marker(t029_stack.dbname, lease["run_id"]) is not None
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "needs_reconciliation",
        "error_code": t036.UNKNOWN_CODE,
    }
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", "nothing new may be dispatched while unknown"

    # A worker token is stale after the exit; the supervisor may resolve it.
    stale = t036.unknown_client(t029_stack, "worker").resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == t036.STALE_ATTEMPT, stale.text

    resolved = t036.unknown_client(t029_stack, "supervisor").resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert resolved.status == 200, resolved.text
    assert resolved.json["resolved"] is True, resolved.text
    assert resolved.json["run_state"] == "cancelled", resolved.text
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == "cancelled", row
    assert row["finished_at"] is not None and row["phase"] == "idle", row
    assert row["error_code"] is None, "the resolved unknown reason is cleared by the cancel"
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0
    waiting = t036.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting is not None and waiting["resume_job_id"] is None, "evidence stays"
    assert waiting["cancel_requested_at"] is not None, waiting

    late = t036.unknown_client(t029_stack, "supervisor").resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == t036.RUN_TERMINAL, late.text
    assert t036.run_cancel_row(t029_stack.dbname, lease["run_id"]) == row


def test_running_cancel_with_unknown_parks_then_supervisor_resolution_cancels(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-unknown-cancel")
    operation = t036.new_operation_id()
    worker = t036.unknown_client(t029_stack, "worker")
    assert worker.record_unknown(
        lease["run_id"], attempt_token=lease["attempt_token"], operation_id=operation
    ).status == 200

    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-unknown-cancel",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    parked = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert parked["state"] == t036.STATE_NEEDS_RECONCILIATION, parked
    assert parked["error_code"] == t036.UNKNOWN_CODE, parked
    assert parked["finished_at"] is None, parked
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "needs_reconciliation",
        "error_code": t036.UNKNOWN_CODE,
    }
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    attempt = t036.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["revoked_at"] is not None, attempt
    allocation = t036.allocation_for_job(t029_stack.dbname, lease["job_id"])
    assert allocation["released_at"] is not None, allocation

    resolved = t036.unknown_client(t029_stack, "supervisor").resolve_unknown(
        lease["run_id"], operation, attempt_token=lease["attempt_token"]
    )
    assert resolved.status == 200, resolved.text
    assert resolved.json["run_state"] == "cancelled", resolved.text
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == "cancelled" and row["finished_at"] is not None, row
    assert row["error_code"] is None, row
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0


def test_unknown_after_terminal_never_revives_the_run(t029_stack) -> None:
    owner, lease, artifact = t035.claimed_with_output(
        t029_stack, execution_unit_id="unit-unknown-terminal"
    )
    worker = t036.execution_client(t029_stack, "worker")
    assert worker.submit_result(
        lease["run_id"],
        body=t036.result_body(lease, suggested_state="succeeded", output_artifact_id=artifact),
    ).status == 200
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-unknown-terminal",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    terminal = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert terminal["state"] == "succeeded", terminal
    events_before = t036.run_event_count(t029_stack.dbname, lease["run_id"])
    operation = t036.new_operation_id()

    for role in ("supervisor", "worker"):
        recorded = t036.unknown_client(t029_stack, role).record_unknown(
            lease["run_id"], attempt_token=lease["attempt_token"], operation_id=operation
        )
        assert recorded.status == 409, recorded.text
        assert recorded.json["error"]["code"] == t036.RUN_TERMINAL, recorded.text
        resolved = t036.unknown_client(t029_stack, role).resolve_unknown(
            lease["run_id"], operation, attempt_token=lease["attempt_token"]
        )
        assert resolved.status == 409, resolved.text
        assert resolved.json["error"]["code"] == t036.RUN_TERMINAL, resolved.text

    assert t036.run_cancel_row(t029_stack.dbname, lease["run_id"]) == terminal
    assert t036.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before
    assert t036.unknown_operations(t029_stack.dbname, lease["run_id"]) == []


def test_cancel_with_a_live_child_run_stays_cancelling(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-child-cancel")
    child = t036.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="running",
    )
    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-child-cancel",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == t036.STATE_CANCELLING, "a live controlled unit blocks cancelled"
    # The phase is kept as-is (the contract enum has no cancelling member) and the
    # Run is not terminal.
    assert row["finished_at"] is None and row["phase"] == "executing", row
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", "no new attempt is dispatched"
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "cancelling"
    }
    assert t036.run_state_row(t029_stack.dbname, child)["state"] == "running"
