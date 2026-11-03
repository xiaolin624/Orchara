"""T035 acceptance: a waiting proposal stops the Run without ending it.

The real path under test: a supervisor claims the debug Run, the worker submits the
contract ``waiting_approval`` proposal at a checkpoint, the Run moves to
``waiting_approval`` / phase ``waiting`` (settled active time, job held), and the
supervisor's exit evidence releases the execution slot while the Run stays
non-terminal. Only a stored decision *and* the exit fact create the one resume.

Everything is real HTTP/mTLS + PostgreSQL + object bytes; the approval decision
comes through the documented synthetic bridge because F06 owns the real one.
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


def test_waiting_proposal_stops_the_run_and_holds_the_job(t029_stack) -> None:
    owner, lease, response = t035.waiting_run_with_lease(
        t029_stack,
        execution_unit_id="unit-wait-proposal",
        checkpoint_id="ckpt-stop-1",
        shift_seconds=2,
    )
    assert response.status == 200, response.text
    body = response.json
    t031.assert_run_shape(body)
    assert body["state"] == "waiting_approval", body
    assert body["phase"] == "waiting", body

    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "waiting_approval", run
    assert run["phase"] == "waiting", run
    assert run["finished_at"] is None, run
    # claim queued->running (2) and the waiting transition (3): exactly one bump.
    assert run["revision"] == 3, run

    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting is not None, waiting
    assert waiting["kind"] == "approval", waiting
    assert waiting["checkpoint_id"] == "ckpt-stop-1", waiting
    assert waiting["decision"] is None and waiting["decision_at"] is None, waiting
    assert waiting["exit_confirmed_at"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting

    job = t035.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", job
    # The attempt may still be live until the supervisor confirms the exit, so the
    # held job keeps naming it (only the exit drops the token).
    assert job["current_attempt_token"] == lease["attempt_token"], job

    attempt = t035.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None, attempt
    assert json.loads(attempt["reported_result"])["suggested_state"] == "waiting_approval"
    assert attempt["checkpoint_id"] == "ckpt-stop-1", attempt
    # T034: the transition out of executing settles the active interval once.
    settled = t035.attempt_active_row(t029_stack.dbname, lease["attempt_token"])
    assert settled["active_settled_at"] is not None, settled
    assert settled["active_ms"] >= 1000, settled

    # No resume exists while the supervisor has not confirmed the exit.
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])

    # The committed waiting transition is one run.status event with the new state.
    statuses = [event for event in t032.status_events(t029_stack.dbname, lease["run_id"])]
    assert [payload["state"] for _, _, payload in statuses] == [
        "queued",
        "running",
        "waiting_approval",
    ], statuses
    assert statuses[-1][2]["state"] == "waiting_approval", statuses

    # The owner sees the same committed Run through the owner-only read.
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    assert detail.json["state"] == "waiting_approval", detail.text
    assert detail.json["phase"] == "waiting", detail.text


def test_exit_releases_the_execution_slot_but_keeps_the_run_non_terminal(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-wait-exit", checkpoint_id="ckpt-stop-2"
    )
    before = t035.quota_counter(t029_stack.dbname)
    assert before["used"] == 1, before

    exited = t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-wait-exit"
    )
    assert exited.status == 200, exited.text
    assert exited.json == {"id": lease["attempt_token"], "state": "exited"}, exited.text

    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    # The exit alone is not a decision: the Run stays waiting, non-terminal.
    assert run["state"] == "waiting_approval", run
    assert run["phase"] == "waiting", run
    assert run["finished_at"] is None, run
    assert run["revision"] == 3, run

    after = t035.quota_counter(t029_stack.dbname)
    assert after["used"] == 0, after
    allocation = t035.allocation_for_job(t029_stack.dbname, lease["job_id"])
    assert allocation["released_at"] is not None, allocation

    job = t035.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", job
    assert job["current_attempt_token"] is None, job

    attempt = t035.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None, attempt

    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["exit_confirmed_at"] is not None, waiting
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])

    # The repeated exit with identical evidence is idempotent: no second release,
    # no second event, no state change.
    events_before = t035.run_event_count(t029_stack.dbname, lease["run_id"])
    repeated = t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-wait-exit"
    )
    assert repeated.status == 200, repeated.text
    assert t035.run_state_row(t029_stack.dbname, lease["run_id"]) == run
    assert t035.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before
    assert t035.quota_counter(t029_stack.dbname)["used"] == 0
    assert len(t035.quota_allocations(t029_stack.dbname)) == 1


def test_resolution_while_the_attempt_is_live_stores_only_the_decision(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-wait-resolve", checkpoint_id="ckpt-stop-3"
    )
    supervisor = t035.execution_client(t029_stack, "supervisor")
    response = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="approval-42"
    )
    assert response.status == 200, response.text
    body = response.json
    assert body["run_id"] == lease["run_id"], body
    assert body["kind"] == "approval" and body["outcome"] == "approved", body
    assert body["resume_job_id"] is None, body

    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["decision"] is not None and waiting["decision_at"] is not None, waiting
    assert waiting["exit_confirmed_at"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting
    decision = t035.waiting_decision(t029_stack.dbname, lease["run_id"])
    assert decision == {
        "kind": "approval",
        "outcome": "approved",
        "reference": "approval-42",
    }, decision

    # Decision first does not resume before the exit fact exists.
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])
    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "waiting_approval", run

    # The identical replay is idempotent (no new row, no new event).
    events_before = t035.run_event_count(t029_stack.dbname, lease["run_id"])
    replay = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="approval-42"
    )
    assert replay.status == 200, replay.text
    assert replay.json["resume_job_id"] is None, replay.text
    assert t035.waiting_count(t029_stack.dbname) == 1
    assert t035.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before

    # A different decision for the same wait is a conflict; the first is authority.
    conflict = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="rejected", reference="approval-43"
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == t035.DECISION_CONFLICT, conflict.text
    assert t035.waiting_decision(t029_stack.dbname, lease["run_id"]) == decision
    assert t035.waiting_row(t029_stack.dbname, lease["run_id"])["decision_at"] == waiting[
        "decision_at"
    ]


def test_resolution_requires_a_waiting_run_of_the_same_kind(t029_stack) -> None:
    owner = t035.golden_owner(t029_stack)
    lease = t035.claim_lease(t029_stack, owner, execution_unit_id="unit-wait-mismatch")
    supervisor = t035.execution_client(t029_stack, "supervisor")

    # A running run has no waiting condition: the bridge refuses without writing.
    running = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="too-early"
    )
    assert running.status == 409, running.text
    assert running.json["error"]["code"] == t035.RUN_STATE_CONFLICT, running.text
    assert t035.waiting_row(t029_stack.dbname, lease["run_id"]) is None

    # Waiting for children refuses an approval decision (and vice versa).
    assert t035.submit_waiting(
        t035.execution_client(t029_stack, "worker"), lease, kind="children",
        checkpoint_id="ckpt-children-mismatch",
    ).status == 200
    mismatch = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="wrong-kind"
    )
    assert mismatch.status == 409, mismatch.text
    assert mismatch.json["error"]["code"] == t035.RUN_STATE_CONFLICT, mismatch.text
    assert t035.waiting_decision(t029_stack.dbname, lease["run_id"]) is None

    # An unknown run is a 404; a malformed body is a located 422.
    missing = supervisor.resolve_waiting(
        t035.new_uuid(), kind="approval", outcome="approved", reference="nobody"
    )
    assert missing.status == 404, missing.text
    assert missing.json["error"]["code"] == "run_not_found", missing.text

    invalid = supervisor.resolve_waiting(
        lease["run_id"], body={"kind": "approval", "outcome": "maybe", "reference": "x"}
    )
    assert invalid.status == 422, invalid.text
    assert invalid.json["error"]["code"] == "validation_failed", invalid.text
    assert invalid.json["error"]["details"]["field"] == "outcome", invalid.text

    # Arbitrary worker payloads (a Command, a thread id, a user id) are refused by
    # the closed request shape instead of being stored.
    odd = supervisor.resolve_waiting(
        lease["run_id"],
        body={
            "kind": "children",
            "outcome": "succeeded",
            "child_run_id": t035.new_uuid(),
            "command": {"resume": "do anything"},
            "thread_id": "user-controlled",
        },
    )
    assert odd.status == 400, odd.text
    assert t035.waiting_decision(t029_stack.dbname, lease["run_id"]) is None

    # The worker certificate cannot resolve (supervisor-only capacity).
    worker = t035.execution_client(t029_stack, "worker")
    forbidden = worker.resolve_waiting(
        lease["run_id"], kind="children", outcome="succeeded", child_run_id=t035.new_uuid()
    )
    assert forbidden.status == 403, forbidden.text
