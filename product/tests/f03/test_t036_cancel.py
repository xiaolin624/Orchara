"""T036 acceptance: cancel stops new work and confirms stopped exactly once.

The real owner calls ``POST /api/v1/runs/{id}/cancel``. A queued Run (or a waiting
Run whose unit already exited) commits cancelled immediately with its job voided;
a Run with a live attempt records ``cancel_requested_at`` and becomes cancelling,
heartbeat reports the intent, result/registration/new-upload calls are refused
while the receipt/query calls stay available, and only the supervisor's exit lets
the one finalizer commit cancelled. Terminal Runs never change again.

Every assertion reads PostgreSQL through the real API/mTLS path; no mock and no
sleep-based race.
"""

from __future__ import annotations

import hashlib
import threading

from support import f00b, f02a, t025, t027, t029, t035, t036
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _events(dbname: str, run_id: str) -> list[dict]:
    return t036.run_events(dbname, run_id)


def test_cancel_queued_run_is_cancelled_immediately_and_idempotently(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    run = t036.queued_run(t029_stack, owner)
    job = t036.jobs_for_run(t029_stack.dbname, run["id"])[0]
    assert job["state"] == "ready", job
    before_events = t036.run_event_count(t029_stack.dbname, run["id"])

    response = t036.cancel_run(owner.creator, run["id"])
    assert response.status == 202, response.text
    t036.assert_run_shape(response.json)
    body = response.json
    assert body["state"] == t036.STATE_CANCELLED and body["phase"] == "idle", body
    assert body["revision"] == 2, body
    assert body["error_code"] is None, body
    headers = t036.cancel_headers(response)
    assert headers["cancel_requested_at"] is not None, response.text

    row = t036.run_cancel_row(t029_stack.dbname, run["id"])
    t036.assert_cancelled(row)
    assert row["revision"] == 2, row
    assert row["error_code"] is None, row
    assert row["output_artifact_id"] is None, row
    job = t036.job_row(t029_stack.dbname, job["id"])
    assert job["state"] == "void" and job["current_attempt_token"] is None, job
    assert t036.attempts_for_run(t029_stack.dbname, run["id"]) == []
    assert t036.quota_allocations(t029_stack.dbname) == []
    events = _events(t029_stack.dbname, run["id"])
    assert len(events) == before_events + 1, events
    assert t036.latest_run_status(t029_stack.dbname, run["id"]) == {"state": "cancelled"}

    # Repeated cancel: same terminal Run, no second revision/event.
    repeated = t036.cancel_run(owner.creator, run["id"])
    assert repeated.status == 202, repeated.text
    assert repeated.json == body, (repeated.json, body)
    assert t036.run_cancel_row(t029_stack.dbname, run["id"]) == row
    assert t036.run_event_count(t029_stack.dbname, run["id"]) == len(events)


def test_cancel_running_run_records_intent_refuses_work_and_cancels_after_exit(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-cancel-run")
    artifact_client = t036.run_artifact_client(t029_stack)
    # An upload already in flight before the cancel: completing it + querying the
    # receipt stay allowed while cancelling.
    upload = t036.create_run_upload(artifact_client, lease)
    content = b'{"ok":true}'
    declared_sha = hashlib.sha256(content).hexdigest()

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    t036.assert_run_shape(response.json)
    assert response.json["state"] == t036.STATE_CANCELLING, response.text
    # The phase enum has no cancelling member: the phase is kept.
    assert response.json["phase"] == "executing", response.json
    assert response.json["revision"] == 3, response.json
    first_cancel_at = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])[
        "cancel_requested_at"
    ]
    assert first_cancel_at is not None, response.text
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "cancelling"
    }
    events_after_cancel = t036.run_event_count(t029_stack.dbname, lease["run_id"])

    # Heartbeat still reports the intent and renews the lease.
    beat = t036.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-cancel-run",
    )
    assert beat.status == 200, beat.text
    t029.assert_heartbeat_reply(beat.json)
    assert beat.json["cancel_requested"] is True, beat.json

    # New execution-bearing calls are refused while cancelling.
    worker = t036.execution_client(t029_stack, "worker")
    refused_result = worker.submit_result(
        lease["run_id"],
        body=t036.result_body(lease, suggested_state="failed", error_code="tool_timeout"),
    )
    assert refused_result.status == 409, refused_result.text
    assert refused_result.json["error"]["code"] == t036.RUN_STATE_CONFLICT, refused_result.text
    refused_upload = artifact_client.create_upload(
        lease["run_id"],
        {
            "attempt_token": lease["attempt_token"],
            "media_type": "application/json",
            "size_bytes": 4,
            "purpose": "node_output",
        },
    )
    assert refused_upload.status == 409, refused_upload.text
    assert refused_upload.json["error"]["code"] == t036.RUN_STATE_CONFLICT, refused_upload.text
    refused_register = artifact_client.register(
        lease["run_id"],
        t036.registration_body(lease, upload["artifact_id"]),
    )
    assert refused_register.status == 409, refused_register.text
    assert refused_register.json["error"]["code"] == t036.RUN_STATE_CONFLICT, refused_register.text
    # The receipt/query calls were already in flight and remain available.
    completed = artifact_client.complete(
        lease["run_id"],
        upload["artifact_id"],
        {"attempt_token": lease["attempt_token"], "sha256": declared_sha},
    )
    assert completed.status == 202, completed.text
    status = artifact_client.status(
        lease["run_id"],
        {"attempt_token": lease["attempt_token"], "artifact_id": upload["artifact_id"]},
    )
    assert status.status == 200, status.text

    # Repeated cancel while cancelling: no new event and the first timestamp wins.
    repeated = t036.cancel_run(owner.creator, lease["run_id"])
    assert repeated.status == 202, repeated.text
    assert repeated.json["state"] == t036.STATE_CANCELLING, repeated.text
    assert t036.run_event_count(t029_stack.dbname, lease["run_id"]) == events_after_cancel
    assert (
        t036.run_cancel_row(t029_stack.dbname, lease["run_id"])["cancel_requested_at"]
        == first_cancel_at
    )

    exited = t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-cancel-run",
        evidence=t036.exit_evidence(lease, started=True, exit_code=137),
    )
    assert exited.status == 200, exited.text

    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    t036.assert_cancelled(row)
    # claim 1->2, cancel 2->3, exit finalizer 3->4: exactly one bump per transition.
    assert row["revision"] == 4, row
    assert row["cancel_requested_at"] == first_cancel_at, row
    assert row["error_code"] is None, row
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    attempt = t036.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None, attempt
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0
    allocation = t036.allocation_for_job(t029_stack.dbname, lease["job_id"])
    assert allocation is not None and allocation["released_at"] is not None, allocation
    events = _events(t029_stack.dbname, lease["run_id"])
    assert len(events) == events_after_cancel + 1, events
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}

    # A late result after the terminal cancel never revives the Run.
    late = worker.submit_result(
        lease["run_id"],
        body=t036.result_body(lease, suggested_state="failed", error_code="tool_timeout"),
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == t036.RUN_TERMINAL, late.text
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200 and detail.json["state"] == "cancelled", detail.text
    assert t036.run_cancel_row(t029_stack.dbname, lease["run_id"]) == row


def test_cancel_waiting_run_with_a_live_attempt_cancels_after_exit_without_a_resume(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-cancel-wait", checkpoint_id="ckpt-cancel"
    )
    before = t036.run_state_row(t029_stack.dbname, lease["run_id"])
    assert before["state"] == t035.STATE_APPROVAL, before

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    assert response.json["state"] == t036.STATE_CANCELLING, response.text
    assert response.json["phase"] == "waiting", ("the phase is kept as-is", response.text)
    assert t036.waiting_cancel_marker(t029_stack.dbname, lease["run_id"]) is not None
    assert t036.waiting_row(t029_stack.dbname, lease["run_id"])["resume_job_id"] is None

    # Resolution is refused while cancelling: only heartbeat/exit/receipt remain.
    refused = t035.execution_client(t029_stack, "supervisor").resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="late-decision"
    )
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == t036.RUN_STATE_CONFLICT, refused.text

    exited = t035.confirm_waiting_exit(t029_stack, lease, execution_unit_id="unit-cancel-wait")
    assert exited.status == 200, exited.text
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    t036.assert_cancelled(row)
    assert row["revision"] == 5, row  # claim 2, waiting 3, cancelling 4, cancelled 5
    assert t036.resume_jobs(t029_stack.dbname, lease["run_id"]) == []
    waiting = t036.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting is not None and waiting["decision"] is None, "waiting evidence stays"
    assert waiting["cancel_requested_at"] is not None, waiting
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}


def test_cancel_after_a_stored_decision_still_never_resumes(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-cancel-decided", checkpoint_id="ckpt-decided"
    )
    supervisor = t035.execution_client(t029_stack, "supervisor")
    decided = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="decided-before-cancel"
    )
    assert decided.status == 200 and decided.json["resume_job_id"] is None, decided.text

    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-cancel-decided"
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    t036.assert_cancelled(row)
    assert t036.resume_jobs(t029_stack.dbname, lease["run_id"]) == []
    waiting = t036.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["resume_job_id"] is None and waiting["decision"] is not None, waiting
    assert waiting["cancel_requested_at"] is not None, waiting


def test_cancel_after_a_resume_voids_the_resume_job_and_cancels(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-cancel-resumed", checkpoint_id="ckpt-resumed"
    )
    supervisor = t035.execution_client(t029_stack, "supervisor")
    assert supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="resume-then-cancel"
    ).status == 200
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-cancel-resumed"
    ).status == 200
    resume = t035.resume_job(t029_stack.dbname, lease["run_id"])
    assert resume["state"] == "ready", resume
    assert t036.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "queued"

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    assert response.json["state"] == t036.STATE_CANCELLED, response.text
    assert t036.job_row(t029_stack.dbname, resume["id"])["state"] == "void"
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}
    # The waiting evidence keeps the historical resume id; the cancelled Run can
    # never be claimed, so the voided job is inert.
    waiting = t036.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["resume_job_id"] == resume["id"], waiting
    assert waiting["cancel_requested_at"] is not None, waiting
    claim = supervisor.claim_job(execution_unit_id="unit-after-cancel")
    assert claim.status == 204, claim.text


def test_cancel_never_changes_a_terminal_run_and_an_earlier_commit_wins(t029_stack) -> None:
    owner, lease, artifact = t035.claimed_with_output(
        t029_stack, execution_unit_id="unit-early-success"
    )
    worker = t036.execution_client(t029_stack, "worker")
    assert worker.submit_result(
        lease["run_id"],
        body=t036.result_body(lease, suggested_state="succeeded", output_artifact_id=artifact),
    ).status == 200
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-early-success",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    terminal = t036.run_state_row(t029_stack.dbname, lease["run_id"])
    assert terminal["state"] == "succeeded", terminal
    events_before = t036.run_event_count(t029_stack.dbname, lease["run_id"])

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    assert response.json["state"] == "succeeded", response.text
    assert response.json["output_artifact_id"] == artifact, response.text
    assert t036.run_cancel_row(t029_stack.dbname, lease["run_id"])["state"] == "succeeded"
    assert t036.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "succeeded"}


def test_cancel_wins_over_an_uncommitted_successful_result(t029_stack) -> None:
    owner, lease, artifact = t035.claimed_with_output(
        t029_stack, execution_unit_id="unit-cancel-priority"
    )
    worker = t036.execution_client(t029_stack, "worker")
    proposal = worker.submit_result(
        lease["run_id"],
        body=t036.result_body(lease, suggested_state="succeeded", output_artifact_id=artifact),
    )
    assert proposal.status == 200, proposal.text
    assert proposal.json["state"] == "running", "a proposal is not a completion"
    assert proposal.json["phase"] == "finalizing", proposal.text

    cancelled = t036.cancel_run(owner.creator, lease["run_id"])
    assert cancelled.status == 202, cancelled.text
    assert cancelled.json["state"] == t036.STATE_CANCELLING, cancelled.text
    assert cancelled.json["phase"] == "finalizing", "the phase is kept as-is"

    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-cancel-priority",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    t036.assert_cancelled(row)
    assert row["output_artifact_id"] is None, "cancel wins over the uncommitted result"
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0
    allocation = t036.allocation_for_job(t029_stack.dbname, lease["job_id"])
    assert allocation["released_at"] is not None, allocation


def test_recorded_deadline_keeps_its_failed_outcome_over_a_later_cancel(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-cancel-deadline")
    t036.set_run_max_active_ms(t029_stack.dbname, lease["run_id"], 1000)
    t036.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)
    beat = t036.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-cancel-deadline",
    )
    assert beat.status == 200 and beat.json["cancel_requested"] is True, beat.text
    recorded = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert recorded["error_code"] == t036.DEADLINE_CODE, recorded
    events_before = t036.run_event_count(t029_stack.dbname, lease["run_id"])

    response = t036.cancel_run(owner.creator, lease["run_id"])
    assert response.status == 202, response.text
    # The intent already exists (recorded by the deadline): no second event/state.
    assert response.json["state"] == "running", response.text
    assert t036.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before

    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-cancel-deadline",
        evidence=t036.exit_evidence(lease, started=True, exit_code=137),
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == "failed", "the deadline keeps its outcome"
    assert row["error_code"] == t036.DEADLINE_CODE, row
    assert row["finished_at"] is not None and row["phase"] == "idle", row
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "failed",
        "error_code": t036.DEADLINE_CODE,
    }


def _seed_queued_run_for(dbname: str, owner_id: str) -> str:
    """One real queued debug-shaped Run + ready job owned by a service principal.

    The public run producer for a service account is F05/F02B; this test only needs
    the owner to be a service account so the API-key cancel path (not the session
    path) is the credential under test. Every row is a real platform row.
    """
    application_id = t025.seed_application(dbname, owner_id, name="t036 service run")
    snapshot_id = t027.seed_execution_snapshot(dbname, owner_id, application_id=application_id)
    input_artifact = t025.insert_artifact(dbname, owner_id, purpose="run_input", sha256="b" * 64)
    run_id = t036.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency, phase) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(owner_id)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot_id)}::uuid, {st.quote_literal('c' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t036.new_uuid())}::uuid, "
        "'queued', 1.00, 'USD', 'idle')",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
        f"{st.quote_literal(t036.new_uuid())}::uuid, "
        f"{st.quote_literal(run_id)}::uuid, 'start', 'ready')",
    )
    return run_id


def test_cancel_is_owner_only_and_needs_the_write_capability(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    run = t036.queued_run(t029_stack, owner)

    admin = t036.admin_session(t029_stack)
    foreign = admin.client.post(t036.cancel_path(run["id"]), csrf=admin.csrf)
    assert foreign.status == 404, foreign.text
    assert foreign.json["error"]["code"] == "resource_not_found", foreign.text
    other = t036.minimal_owner(t029_stack)
    other_attempt = other.creator.client.post(t036.cancel_path(run["id"]), csrf=other.creator.csrf)
    assert other_attempt.status == 404, other_attempt.text
    unknown = owner.creator.client.post(t036.cancel_path(t036.new_uuid()), csrf=owner.creator.csrf)
    assert unknown.status == 404, unknown.text

    no_csrf = owner.creator.client.post(t036.cancel_path(run["id"]))
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid", no_csrf.text

    service_id = f02a.create_service_account(
        admin, "t036 cancel service", owner.creator.principal_id
    )
    _key_id, read_secret = f02a.create_api_key(admin, service_id, ["application:read"])
    read_key = f02a.bearer_client(t029_stack, read_secret)
    refused = read_key.post(t036.cancel_path(run["id"]))
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "insufficient_scope", refused.text

    # Positive API-key path: the service account owns the Run and has write scope.
    service_run = _seed_queued_run_for(t029_stack.dbname, service_id)
    _write_id, write_secret = f02a.create_api_key(
        admin, service_id, ["application:write"]
    )
    write_key = f02a.bearer_client(t029_stack, write_secret)
    accepted = write_key.post(t036.cancel_path(service_run))
    assert accepted.status == 202, accepted.text
    assert accepted.json["state"] == t036.STATE_CANCELLED, accepted.text
    assert t036.run_cancel_row(t029_stack.dbname, service_run)["state"] == "cancelled"
    # The write key still cannot cancel another principal's Run.
    cross = write_key.post(t036.cancel_path(run["id"]))
    assert cross.status == 404, cross.text


def test_late_exit_after_a_terminal_cancel_never_changes_the_run(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-late-exit")
    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    supervisor = t036.execution_client(t029_stack, "supervisor")
    evidence = t036.exit_evidence(lease, started=True)
    assert supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-late-exit", evidence=evidence
    ).status == 200
    terminal = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert terminal["state"] == "cancelled", terminal
    events_before = t036.run_event_count(t029_stack.dbname, lease["run_id"])

    # Identical evidence is the idempotent no-op; different evidence is a conflict.
    replay = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-late-exit", evidence=evidence
    )
    assert replay.status == 200, replay.text
    conflict = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-late-exit",
        evidence=t036.exit_evidence(lease, started=True, exit_code=9),
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "attempt_evidence_conflict", conflict.text
    assert t036.run_cancel_row(t029_stack.dbname, lease["run_id"]) == terminal
    assert t036.run_event_count(t029_stack.dbname, lease["run_id"]) == events_before


def test_cancel_with_a_terminal_child_run_is_not_blocked(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-terminal-child")
    t036.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="succeeded",
    )
    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202
    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-terminal-child",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    assert row["state"] == "cancelled", "only a live child blocks the terminal cancel"
    assert row["finished_at"] is not None, row


def test_cancel_with_a_live_child_run_stays_cancelling(t029_stack) -> None:
    """The immediate-cancel path must honour the finalizer's live-child guard.

    A queued/waiting Run has no live attempt, but a live child Run is still a
    controlled execution unit. Cancelling it immediately would claim "stopped"
    while that unit runs, so the Run stays cancelling with the intent recorded
    and its job held; only the finalizer may commit cancelled once the child is
    terminal.
    """
    owner = t036.golden_owner(t029_stack)
    run = t036.queued_run(t029_stack, owner)
    job = t036.jobs_for_run(t029_stack.dbname, run["id"])[0]
    assert job["state"] == "ready", job
    child_id = t036.seed_child_run(
        t029_stack,
        parent_run_id=run["id"],
        owner_id=owner.creator.principal_id,
        state="running",
    )

    response = t036.cancel_run(owner.creator, run["id"])
    assert response.status == 202, response.text
    t036.assert_run_shape(response.json)
    assert response.json["state"] == t036.STATE_CANCELLING, response.text
    assert response.json["revision"] == 2, response.json
    headers = t036.cancel_headers(response)
    assert headers["cancel_requested_at"] is not None, response.text

    row = t036.run_cancel_row(t029_stack.dbname, run["id"])
    assert row["state"] == t036.STATE_CANCELLING, row
    assert row["finished_at"] is None, row
    assert row["cancel_requested_at"] is not None, row
    assert t036.job_row(t029_stack.dbname, job["id"])["state"] == "held", job
    child = t036.run_cancel_row(t029_stack.dbname, child_id)
    assert child["state"] == "running", child
    assert t036.latest_run_status(t029_stack.dbname, run["id"]) == {
        "state": t036.STATE_CANCELLING
    }


def test_cancelling_a_never_started_unit_cancels_and_voids_the_job(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-never-started")
    assert t036.cancel_run(owner.creator, lease["run_id"]).status == 202

    exited = t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-never-started",
        evidence=t036.exit_evidence(lease, started=False, reason="spawn_failed"),
    )
    assert exited.status == 200, exited.text
    row = t036.run_cancel_row(t029_stack.dbname, lease["run_id"])
    t036.assert_cancelled(row)
    assert row["revision"] == 4, row
    job = t036.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "void", "a unit that provably never started is not completed"
    assert job["current_attempt_token"] is None, job
    assert t036.quota_counter(t029_stack.dbname)["used"] == 0
    assert t036.latest_run_status(t029_stack.dbname, lease["run_id"]) == {"state": "cancelled"}
    # The cancel intent is not silently turned into a requeue: no claimable job.
    assert t036.execution_client(t029_stack, "supervisor").claim_job(
        execution_unit_id="unit-after-never-started"
    ).status == 204


def test_a_queued_cancel_racing_ahead_of_the_deadline_commits_cancelled(t029_stack) -> None:
    """The deadline wins only once it has been observed; a queue cancel stops work.

    T034 observes the limit in claim/heartbeat/result/exit. A queued Run that the
    user cancels before any claim is a real stop with no execution; no worker slot,
    attempt or deadline terminal is fabricated. The claim path would have committed
    failed(deadline_exceeded) had it run first — the Go transaction that arrives
    first wins, which is the documented arbitration order.
    """
    owner = t036.golden_owner(t029_stack)
    queued = t036.queued_run(t029_stack, owner)
    t036.set_run_active_elapsed_ms(t029_stack.dbname, queued["id"], 5000)
    t036.set_run_max_active_ms(t029_stack.dbname, queued["id"], 4000)

    response = t036.cancel_run(owner.creator, queued["id"])
    assert response.status == 202, response.text
    assert response.json["state"] == t036.STATE_CANCELLED, response.text
    row = t036.run_cancel_row(t029_stack.dbname, queued["id"])
    t036.assert_cancelled(row)
    assert row["error_code"] is None, row
    assert t036.attempts_for_run(t029_stack.dbname, queued["id"]) == []
    assert t036.execution_client(t029_stack, "supervisor").claim_job(
        execution_unit_id="unit-after-queue-cancel"
    ).status == 204


def test_two_concurrent_cancel_requests_commit_one_transition(t029_stack) -> None:
    admin = t036.admin_session(t029_stack)
    login = "t036-race-" + t036.new_uuid()[:8]
    creator = t035.invite_and_activate(t029_stack, admin, login, roles=("creator",))
    application_id = t035.seed_golden_draft(t029_stack.dbname, creator.principal_id)
    owner = t029.Owner(admin=admin, creator=creator, application_id=application_id)
    run = t035.queued_run(t029_stack, owner)
    second = t035.session_for(t029_stack, login, f00b.USER_PASSWORD)
    sessions = [creator, second]
    barrier = threading.Barrier(2)
    responses: list[object] = [None, None]

    def attempt(index: int) -> None:
        barrier.wait()
        responses[index] = t036.cancel_run(sessions[index], run["id"])

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    for response in responses:
        assert response is not None and response.status == 202, (
            None if response is None else response.text
        )
    row = t036.run_cancel_row(t029_stack.dbname, run["id"])
    assert row["state"] == "cancelled" and row["revision"] == 2, row
    assert t036.run_event_count(t029_stack.dbname, run["id"]) == 2
    assert t036.latest_run_status(t029_stack.dbname, run["id"]) == {"state": "cancelled"}
