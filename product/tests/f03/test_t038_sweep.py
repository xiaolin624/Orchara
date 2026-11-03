"""T038 acceptance: an expired lease is parked, never taken over.

The real sweep command runs against a real PostgreSQL with a real claimed attempt
whose lease expired beyond the grace window. It must park (`recovery_pending`,
`recovery_reason=lease_expired`, `recovery_since` set, same-transaction
`run.status` event and audit row), keep the job `held`, keep the allocation and
keep the old attempt token untouched. Claim then returns nothing: a parked Run
cannot get a second executor, and a live old worker's fresh heartbeat makes the
recover command refuse `old_execution_still_live`.
"""

from __future__ import annotations

import json

from support import t038
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def test_expired_lease_is_parked_without_requeue_or_token_swap(t029_stack, tmp_path) -> None:
    owner, lease, report = t038.parked_run(
        t029_stack, execution_unit_id="unit-sweep-park"
    )
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    job_id = lease["job_id"]
    token = lease["attempt_token"]

    assert report["status"] == "ok", report
    assert report["dry_run"] is False, report
    assert report["parked"] == 1, report
    assert report["runs"][0]["action"] == "parked", report

    run = t038.run_state_row(dbname, run_id)
    assert run["state"] == "recovery_pending", run
    assert run["phase"] == "idle", run
    columns = t038.recovery_columns(dbname, run_id)
    assert columns["recovery_reason"] == t038.REASON_LEASE_EXPIRED, columns
    assert columns["recovery_since"] is not None, columns

    job = t038.job_row(dbname, job_id)
    assert job["state"] == "held", job
    assert job["current_attempt_token"] is None, job
    # The old attempt is deliberately NOT finalized: the host may still be alive.
    assert t038.attempt_is_live(dbname, token), t038.attempt_row(dbname, token)
    # The slot stays allocated until the old unit is proven gone.
    allocation = t038.allocation_for_job(dbname, job_id)
    assert allocation is not None and allocation["released_at"] is None, allocation
    assert t038.quota_counter(dbname)["used"] == 1, t038.quota_counter(dbname)
    assert len(t038.attempts_for_run(dbname, run_id)) == 1

    # Same-transaction projection + audit.
    assert t038.run_status_states(dbname, run_id)[-1] == "recovery_pending"
    parked_audit = t038.audit_events(dbname, t038.AUDIT_PARKED, run_id)
    assert len(parked_audit) == 1, parked_audit
    assert parked_audit[0]["actor_id"] is None, parked_audit
    metadata = json.loads(parked_audit[0]["metadata"])
    assert metadata["reason"] == t038.REASON_LEASE_EXPIRED, metadata
    assert metadata["execution_unit_id"] == "unit-sweep-park", metadata
    assert "attempt_token" not in metadata, metadata

    # The owner-facing detail exposes the reason/since and no evidence yet.
    detail = owner.creator.client.get(t038.run_path(run_id))
    assert detail.status == 200, detail.text
    assert detail.json["state"] == "recovery_pending", detail.text
    headers = t038.recovery_headers(detail)
    assert headers["reason"] == t038.REASON_LEASE_EXPIRED, headers
    assert headers["since"] is not None, headers
    assert headers["evidence_at"] is None, headers
    assert token not in detail.text

    # No second executor is possible: claim only selects queued Runs.
    t038.claim_none(t029_stack)

    # A second sweep is idempotent (a parked Run is no longer scanned).
    _completed, again = t038.sweep(t029_stack, "--grace", "1s")
    assert again["parked"] == 0 and again["scanned"] == 0, again
    assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"
    assert len(t038.attempts_for_run(dbname, run_id)) == 1


def test_live_old_worker_is_not_replaced_and_recover_refuses(t029_stack, tmp_path) -> None:
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-still-live")
    run_id = lease["run_id"]
    token = lease["attempt_token"]
    dbname = t029_stack.dbname

    # Only the lease deadline has passed; the worker's heartbeat is fresh, so the
    # old execution may still be running. The sweep still parks (conservative),
    # but nothing may replace the unit.
    t038.set_lease_expired_seconds_ago(dbname, token, 120)
    _completed, report = t038.sweep(t029_stack, "--grace", "1s")
    assert report["parked"] == 1, report
    assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"
    t038.claim_none(t029_stack)

    completed, refusal = t038.recover(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-still-live"),
        directory=tmp_path,
        extra=["--grace", "60s"],
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    t038.assert_refused(refusal, t038.CODE_OLD_EXECUTION_STILL_LIVE)
    assert "never be replaced" in refusal["message"], refusal

    # Nothing was written and the live unit was not replaced.
    assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"
    assert t038.attempt_is_live(dbname, token)
    assert len(t038.attempts_for_run(dbname, run_id)) == 1
    assert t038.recovery_evidence_rows(dbname, run_id) == []
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] == "held" and job["current_attempt_token"] is None, job
    assert t038.allocation_for_job(dbname, lease["job_id"])["released_at"] is None
    assert t038.quota_counter(dbname)["used"] == 1


def test_dry_run_reports_the_park_and_writes_nothing(t029_stack) -> None:
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-dry-run")
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    t038.expire_lease(dbname, lease["attempt_token"])
    before_events = t038.run_event_count(dbname, run_id)
    before_audit = len(t038.audit_events(dbname, t038.AUDIT_PARKED, run_id))

    _completed, report = t038.sweep(t029_stack, "--grace", "1s", "--dry-run")
    assert report["dry_run"] is True, report
    assert report["would_park"] == 1, report
    assert report["parked"] == 0, report
    assert report["runs"][0]["action"] == "would_park", report

    run = t038.run_state_row(dbname, run_id)
    assert run["state"] == "running", run
    assert t038.recovery_columns(dbname, run_id)["recovery_reason"] is None
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] == "leased", job
    assert job["current_attempt_token"] == lease["attempt_token"], job
    assert t038.attempt_is_live(dbname, lease["attempt_token"])
    assert t038.run_event_count(dbname, run_id) == before_events
    assert len(t038.audit_events(dbname, t038.AUDIT_PARKED, run_id)) == before_audit
    assert t038.quota_counter(dbname)["used"] == 1


def test_sweep_skips_a_cancelling_run(t029_stack) -> None:
    """Documented boundary: recovery never resurrects a cancel intent.

    A Run the owner cancelled is `cancelling`; parking it would create a state
    recovery must not re-queue. The sweep therefore leaves it to the cancel/exit
    path, and the already-parked Run is skipped on the next pass.
    """
    owner = t038.golden_owner(t029_stack)
    cancelled = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-cancel-skip")
    parked = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-park-skip")
    dbname = t029_stack.dbname

    accepted = t038.cancel_run(owner.creator, cancelled["run_id"])
    assert accepted.status == 202, accepted.text
    assert accepted.json["state"] == "cancelling", accepted.text
    t038.expire_lease(dbname, cancelled["attempt_token"])
    t038.expire_lease(dbname, parked["attempt_token"])

    _completed, report = t038.sweep(t029_stack, "--grace", "1s")
    assert report["parked"] == 1, report
    assert report["runs"][0]["run_id"] == parked["run_id"], report
    assert t038.run_state_row(dbname, cancelled["run_id"])["state"] == "cancelling"
    assert t038.run_state_row(dbname, parked["run_id"])["state"] == "recovery_pending"

    # The parked Run is not re-parked, and the cancelling Run stays untouched.
    _completed, again = t038.sweep(t029_stack, "--grace", "1s")
    assert again["parked"] == 0 and again["scanned"] == 0, again


def test_sweep_usage_and_unknown_flags_exit_two(t029_stack) -> None:
    completed = t038.run_runs_command(t029_stack, ["sweep-leases", "--grace", "nonsense"])
    assert completed.returncode == 2, completed.stdout + completed.stderr
    completed = t038.run_runs_command(t029_stack, ["sweep-leases", "--nope"])
    assert completed.returncode == 2, completed.stdout + completed.stderr
    completed = t038.run_runs_command(t029_stack, ["sweep-leases", "--grace", "-1s"])
    assert completed.returncode == 2, completed.stdout + completed.stderr
