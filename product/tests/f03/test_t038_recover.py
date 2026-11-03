"""T038 acceptance: one audited evidence row re-queues exactly one job/attempt.

The real ``orchara-admin runs recover`` command runs against a real parked Run and
a real object store: it re-verifies the frozen manifest with the same
``compiler.VerifyManifestBytes`` the worker uses, hashes the operator document,
and one transaction records the evidence, finalizes the old attempt, revokes its
token, re-arms exactly one job and returns the Run to queued. The old token is
stale, a repeated command never creates a second attempt, and a T031
exit-without-result park is recoverable through the same checks.
"""

from __future__ import annotations

import json
import uuid

from support import f00b, t034, t035, t038
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _admin_id(dbname: str) -> str:
    return f00b.principal_id_for_login(dbname)


def test_full_evidence_records_one_requeue_and_a_new_attempt(t029_stack, tmp_path) -> None:
    owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-recover-one"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    old_token = lease["attempt_token"]
    job_id = lease["job_id"]
    evidence = t038.recovery_evidence("unit-recover-one")
    expected_sha = t038.expected_evidence_sha256(evidence)

    report = t038.recover_ok(
        t029_stack, run_id, evidence=evidence, directory=tmp_path
    )
    assert report["status"] == "recovered", report
    assert report["run_id"] == run_id, report
    assert report["state"] == "queued", report
    assert report["recovery_reason"] == t038.REASON_LEASE_EXPIRED, report
    assert report["execution_unit_id"] == "unit-recover-one", report
    assert report["evidence_sha256"] == expected_sha, report
    assert report["resumed_job_id"] == job_id, report
    assert report["recorded_by"] == _admin_id(dbname), report
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["old_execution"] == "passed", report
    assert statuses["database_drained"] == "passed", report
    assert statuses["manifest_artifact"] == "passed", report

    # The Run left recovery_pending exactly one revision later, columns cleared.
    run = t038.run_state_row(dbname, run_id)
    assert run["state"] == "queued", run
    assert run["phase"] == "idle", run
    columns = t038.recovery_columns(dbname, run_id)
    assert columns["recovery_reason"] is None, columns
    assert columns["recovery_since"] is None, columns

    # Old attempt finalized with the operator proof; its token is revoked.
    attempt = t038.attempt_row(dbname, old_token)
    assert attempt["exited_at"] is not None, attempt
    assert attempt["revoked_at"] is not None, attempt
    stored_exit = json.loads(attempt["exit_evidence"])
    assert stored_exit["evidence_kind"] == "operator_recovery", stored_exit
    assert stored_exit["execution_unit_id"] == "unit-recover-one", stored_exit

    # Exactly one evidence row, naming the same attempt and the re-armed job.
    rows = t038.recovery_evidence_rows(dbname, run_id)
    assert len(rows) == 1, rows
    assert rows[0]["attempt_token"] == old_token, rows
    assert rows[0]["resumed_job_id"] == job_id, rows
    assert rows[0]["evidence_sha256"] == expected_sha, rows
    assert rows[0]["recorded_by"] == _admin_id(dbname), rows
    stored = json.loads(rows[0]["evidence"])
    assert stored["process"]["pid"] == 4123, stored
    assert stored["sandbox"]["result"] == "absent", stored

    # The one job is ready again with its identity preserved; the slot is free.
    job = t038.job_row(dbname, job_id)
    assert job["state"] == "ready", job
    assert job["current_attempt_token"] is None, job
    allocation = t038.allocation_for_job(dbname, job_id)
    assert allocation is not None and allocation["released_at"] is not None, allocation
    assert t038.quota_counter(dbname)["used"] == 0, t038.quota_counter(dbname)

    # Same-transaction projection and audit, with a real principal.
    assert t038.run_status_states(dbname, run_id)[-1] == "queued"
    recovered_audit = t038.audit_events(dbname, t038.AUDIT_RECOVERED, run_id)
    assert len(recovered_audit) == 1, recovered_audit
    assert recovered_audit[0]["actor_id"] == _admin_id(dbname), recovered_audit
    metadata = json.loads(recovered_audit[0]["metadata"])
    assert metadata["evidence_sha256"] == expected_sha, metadata
    assert metadata["resumed_job_id"] == job_id, metadata
    assert "attempt_token" not in metadata, metadata

    # A repeated command is a refusal: no second evidence row, no second attempt.
    completed, refusal = t038.recover(
        t029_stack, run_id, evidence=evidence, directory=tmp_path
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_ALREADY_RECOVERED)
    assert len(t038.recovery_evidence_rows(dbname, run_id)) == 1
    assert len(t038.attempts_for_run(dbname, run_id)) == 1
    assert t038.run_state_row(dbname, run_id)["state"] == "queued"

    # Claim issues a fresh token for exactly one new attempt.
    new_lease = t038.claim_next(t029_stack, execution_unit_id="unit-recover-two")
    assert new_lease["run_id"] == run_id, new_lease
    assert new_lease["job_id"] == job_id, new_lease
    assert new_lease["attempt_token"] != old_token, new_lease
    attempts = t038.attempts_for_run(dbname, run_id)
    assert len(attempts) == 2, attempts
    assert attempts[0]["token"] == old_token and attempts[1]["token"] == new_lease["attempt_token"]
    assert t038.attempt_is_live(dbname, new_lease["attempt_token"])
    assert t038.run_state_row(dbname, run_id)["state"] == "running"

    # A late write from the old token is rejected: it can never be the winner.
    late_result = t038.execution_client(t029_stack, "worker").submit_result(
        run_id,
        attempt_token=old_token,
        suggested_state="failed",
        error_code="tool_crash",
    )
    assert late_result.status == 409, late_result.text
    assert late_result.json["error"]["code"] == "stale_attempt", late_result.text
    late_exit = t038.execution_client(t029_stack, "supervisor").confirm_exit(
        old_token,
        execution_unit_id="unit-recover-one",
        evidence=t038.exit_evidence(new_lease, started=True),
    )
    assert late_exit.status == 409, late_exit.text
    assert late_exit.json["error"]["code"] == "attempt_evidence_conflict", late_exit.text
    assert t038.run_state_row(dbname, run_id)["state"] == "running"
    assert t038.attempt_row(dbname, new_lease["attempt_token"])["exited_at"] is None


def test_park_settles_active_time_at_the_lease_deadline(t029_stack, tmp_path) -> None:
    """T034 active time must not include the parked (not executing) window.

    The sweep parks at the last provable activity end (``lease_deadline``) and
    settles the attempt in the same transaction. If recover settled at park time
    instead, the whole parked window would be added to the Run total, the next
    claim could fail immediately with ``deadline_exceeded`` and the UI clock
    would jump.
    """
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-active-park")
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    token = lease["attempt_token"]

    # Deterministic timeline: the attempt started 10s ago, but the lease (the
    # last provable activity) ended 5s ago. The limit (7s) is above the provable
    # 5s and below the old, corrupted 10s settlement.
    t034.shift_attempt_started_at_seconds(dbname, token, 10)
    t034.set_run_max_active_ms(dbname, run_id, 7_000)
    t038.set_lease_expired_seconds_ago(dbname, token, 5)
    t038.age_attempt_heartbeat(dbname, token, 120)

    _completed, sweep = t038.sweep(t029_stack, "--grace", "1s")
    assert sweep["parked"] == 1, sweep

    settled = t034.attempt_active_row(dbname, token)
    assert settled["active_settled_at"] is not None, settled
    assert 4_000 <= settled["active_ms"] < 7_000, settled
    parked_run = t034.run_active_row(dbname, run_id)
    assert parked_run["state"] == t038.STATE_RECOVERY, parked_run
    assert 4_000 <= parked_run["active_elapsed_ms"] < 7_000, parked_run

    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-active-park"),
        directory=tmp_path,
        extra=["--grace", "60s"],
    )
    assert report["status"] == "recovered", report

    # Recover is a no-op on an already-settled attempt: the parked window is not
    # added a second time.
    recovered = t034.run_active_row(dbname, run_id)
    assert recovered["state"] == "queued", recovered
    assert 4_000 <= recovered["active_elapsed_ms"] < 7_000, recovered
    assert t034.attempt_active_row(dbname, token)["active_ms"] < 7_000

    # The next claim must not be failed by the deadline: the recovered total is
    # under the frozen limit.
    new_lease = t038.claim_next(t029_stack, execution_unit_id="unit-active-park-2")
    assert new_lease["run_id"] == run_id, new_lease
    assert new_lease["attempt_token"] != token, new_lease
    running = t034.run_active_row(dbname, run_id)
    assert running["state"] == "running", running
    assert running["error_code"] is None, running
    assert running["active_elapsed_ms"] < 7_000, running


def test_a_second_recovery_cycle_names_the_same_requeued_job(t029_stack, tmp_path) -> None:
    """Every cycle records the job it requeued, including a later cycle.

    Requeue reuses the same job id, so the evidence column must not be unique;
    the once-per-attempt rule is ``UNIQUE(run_id, attempt_token)``. The old
    writer stored NULL for later cycles, which made the audit link unreadable.
    """
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-cycle-one"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    job_id = lease["job_id"]

    first = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-cycle-one"),
        directory=tmp_path,
    )
    assert first["resumed_job_id"] == job_id, first

    second_lease = t038.claim_next(t029_stack, execution_unit_id="unit-cycle-two")
    assert second_lease["job_id"] == job_id, second_lease
    t038.expire_lease(dbname, second_lease["attempt_token"])
    _completed, sweep = t038.sweep(t029_stack, "--grace", "1s")
    assert sweep["parked"] == 1, sweep
    assert t038.run_state_row(dbname, run_id)["state"] == t038.STATE_RECOVERY

    second = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-cycle-two"),
        directory=tmp_path,
        extra=["--grace", "60s"],
    )
    assert second["status"] == "recovered", second
    assert second["resumed_job_id"] == job_id, second

    rows = t038.recovery_evidence_rows(dbname, run_id)
    assert len(rows) == 2, rows
    assert [row["resumed_job_id"] for row in rows] == [job_id, job_id], rows
    assert rows[0]["attempt_token"] == lease["attempt_token"], rows
    assert rows[1]["attempt_token"] == second_lease["attempt_token"], rows


def test_check_only_writes_nothing(t029_stack, tmp_path) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-check-only"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    before_run = t038.run_state_row(dbname, run_id)
    before_events = t038.run_event_count(dbname, run_id)

    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-check-only"),
        directory=tmp_path,
        extra=["--check-only"],
    )
    assert report["status"] == "check_only", report
    assert report["check_only"] is True, report
    assert report["run_id"] == run_id, report
    assert {check["name"] for check in report["checks"]} >= {
        "run_state",
        "attempt",
        "old_execution",
        "manifest_artifact",
        "input_artifact",
        "database_drained",
        "actor",
    }, report

    assert t038.run_state_row(dbname, run_id) == before_run
    assert t038.recovery_columns(dbname, run_id)["recovery_reason"] == t038.REASON_LEASE_EXPIRED
    assert t038.recovery_evidence_rows(dbname, run_id) == []
    assert t038.run_event_count(dbname, run_id) == before_events
    assert t038.run_status_states(dbname, run_id)[-1] == "recovery_pending"
    assert t038.audit_events(dbname, t038.AUDIT_RECOVERED, run_id) == []
    assert t038.attempt_is_live(dbname, lease["attempt_token"])
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] == "held" and job["current_attempt_token"] is None, job
    assert t038.allocation_for_job(dbname, lease["job_id"])["released_at"] is None


def test_exit_without_result_park_is_recoverable_with_the_same_checks(
    t029_stack, tmp_path
) -> None:
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-exit-park")
    dbname = t029_stack.dbname
    run_id = lease["run_id"]

    exited = t038.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-exit-park",
        evidence=t038.exit_evidence(lease, started=True, exit_code=0),
    )
    assert exited.status == 200, exited.text
    run = t038.run_state_row(dbname, run_id)
    assert run["state"] == "recovery_pending", run
    assert t038.recovery_columns(dbname, run_id)["recovery_reason"] == (
        t038.REASON_EXIT_WITHOUT_RESULT
    ), t038.recovery_columns(dbname, run_id)
    # T031 already finalized the attempt and released the slot; nothing requeued.
    assert t038.attempt_is_finalized(dbname, lease["attempt_token"])
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] == "held" and job["current_attempt_token"] is None, job
    assert t038.quota_counter(dbname)["used"] == 0
    t038.claim_none(t029_stack)

    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-exit-park"),
        directory=tmp_path,
        extra=["--grace", "60s"],
    )
    assert report["status"] == "recovered", report
    assert report["recovery_reason"] == t038.REASON_EXIT_WITHOUT_RESULT, report
    statuses = {check["name"]: check["status"] for check in report["checks"]}
    assert statuses["old_execution"] == "skipped", report
    assert t038.run_state_row(dbname, run_id)["state"] == "queued"
    assert t038.run_status_states(dbname, run_id)[-1] == "queued"

    new_lease = t038.claim_next(t029_stack, execution_unit_id="unit-exit-park-2")
    assert new_lease["run_id"] == run_id, new_lease
    assert new_lease["attempt_token"] != lease["attempt_token"], new_lease
    assert len(t038.attempts_for_run(dbname, run_id)) == 2


def test_resume_job_is_requeued_with_its_resume_artifact(t029_stack, tmp_path) -> None:
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-resume-park")
    dbname = t029_stack.dbname
    run_id = lease["run_id"]

    waiting = t035.submit_waiting(
        t038.execution_client(t029_stack, "worker"),
        lease,
        kind="approval",
        checkpoint_id="ckpt-t038-recovery",
    )
    assert waiting.status == 200, waiting.text
    resolved = t038.execution_client(t029_stack, "supervisor").resolve_waiting(
        run_id, kind="approval", outcome="approved", reference="t038-recovery-ref"
    )
    assert resolved.status == 200, resolved.text
    exited = t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-resume-park"
    )
    assert exited.status == 200, exited.text
    resume = t035.resume_job(dbname, run_id)
    assert resume["state"] == "ready" and resume["resume_artifact_id"], resume

    first = t038.claim_next(t029_stack, execution_unit_id="unit-resume-second")
    assert first["job_id"] == resume["id"], first
    assert first["job_kind"] == "resume", first
    assert first["resume_artifact_id"] == resume["resume_artifact_id"], first
    assert first["resume_url"], first

    t038.expire_lease(dbname, first["attempt_token"])
    _completed, sweep = t038.sweep(t029_stack, "--grace", "1s")
    assert sweep["parked"] == 1, sweep
    assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"

    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-resume-second"),
        directory=tmp_path,
    )
    assert report["status"] == "recovered", report
    requeued = t038.job_row(dbname, resume["id"])
    assert requeued["state"] == "ready", requeued
    assert requeued["resume_artifact_id"] == resume["resume_artifact_id"], requeued
    jobs = t038.jobs_for_run(dbname, run_id)
    assert [(entry["kind"], entry["state"]) for entry in jobs][-1] == ("resume", "ready"), jobs

    again = t038.claim_next(t029_stack, execution_unit_id="unit-resume-third")
    assert again["job_id"] == resume["id"], again
    assert again["job_kind"] == "resume", again
    assert again["resume_artifact_id"] == resume["resume_artifact_id"], again
    assert again["resume_url"], again


def test_actor_login_records_that_administrator(t029_stack, tmp_path) -> None:
    admin = t038.admin_session(t029_stack)
    login = "t038-ops-" + uuid.uuid4().hex[:8]
    second = t038.invite_and_activate(t029_stack, admin, login, roles=("admin",))
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-actor"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]

    # An unknown login is a refusal, never a silent "bootstrap admin".
    completed, refusal = t038.recover(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-actor"),
        directory=tmp_path,
        extra=["--actor-login", "no-such-operator"],
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_ACTOR_NOT_FOUND)
    assert t038.recovery_evidence_rows(dbname, run_id) == []

    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-actor"),
        directory=tmp_path,
        extra=["--actor-login", login],
    )
    assert report["recorded_by"] == second.principal_id, report
    rows = t038.recovery_evidence_rows(dbname, run_id)
    assert rows[0]["recorded_by"] == second.principal_id, rows
    audit = t038.audit_events(dbname, t038.AUDIT_RECOVERED, run_id)
    assert audit[0]["actor_id"] == second.principal_id, audit


def test_queued_run_is_not_recovery_pending(t029_stack, tmp_path) -> None:
    owner = t038.golden_owner(t029_stack)
    run = t038.queued_run(t029_stack, owner)
    completed, refusal = t038.recover(
        t029_stack,
        run["id"],
        evidence=t038.recovery_evidence("unit-none"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_NOT_RECOVERY_PENDING)
    assert t038.recovery_evidence_rows(t029_stack.dbname, run["id"]) == []
    assert t038.run_state_row(t029_stack.dbname, run["id"])["state"] == "queued"


def test_no_attempt_names_the_documented_needs_reconciliation_path(
    t029_stack, tmp_path
) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-no-attempt"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    # Remove the attempt: the Run is still recovery_pending but there is nothing
    # to finalize. The command must not invent a requeue for it.
    st.sql(
        dbname,
        "DELETE FROM platform.run_attempts WHERE run_id = "
        f"{st.quote_literal(run_id)}::uuid",
    )
    completed, refusal = t038.recover(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-no-attempt"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_NEEDS_RECONCILIATION)
    assert "needs_reconciliation" in refusal["message"], refusal
    assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"
    assert t038.recovery_evidence_rows(dbname, run_id) == []
    assert t038.job_row(dbname, lease["job_id"])["state"] == "held"
    assert t038.allocation_for_job(dbname, lease["job_id"])["released_at"] is None
