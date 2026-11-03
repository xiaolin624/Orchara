"""T034 acceptance: active time accumulates across attempts and never resets.

Every interval is produced by moving a real attempt's ``started_at`` into the past
(PostgreSQL computes ``now() - started_at`` at settlement), so the tests are
deterministic without sleeping. Settlement is observed in the real database and
through the non-contract active-time headers of the owner-only Run read.
"""

from __future__ import annotations

from support import t029, t034
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def startup_failed(lease: dict) -> dict:
    """Evidence that the unit never started; the attempt still held the run."""
    return t029.exit_evidence(lease, started=False, reason="spawn_failed", exit_code=127)


def test_two_attempts_accumulate_and_the_second_claim_does_not_reset(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    first = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-acc-1")

    # A new debug run froze the golden policy's 900s limit (900000 ms) at creation.
    queued = t034.run_active_row(t029_stack.dbname, first["run_id"])
    assert queued["max_active_ms"] == t034.GOLDEN_MAX_ACTIVE_MS, queued

    # Attempt 1 spends ~5s and is requeued (started=false keeps T029's meaning).
    t034.shift_attempt_started_at_seconds(
        t029_stack.dbname, first["attempt_token"], 5
    )
    released = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        first["attempt_token"],
        execution_unit_id="unit-acc-1",
        evidence=startup_failed(first),
    )
    assert released.status == 200, released.text

    attempt1 = t034.attempt_active_row(t029_stack.dbname, first["attempt_token"])
    t034.assert_active_ms(attempt1["active_ms"], at_least=5000, below=6000, label="attempt 1")
    assert attempt1["active_settled_at"] is not None, attempt1
    run_after_first = t034.run_active_row(t029_stack.dbname, first["run_id"])
    assert run_after_first["state"] == "queued", run_after_first
    t034.assert_active_ms(
        run_after_first["active_elapsed_ms"], at_least=5000, below=6000, label="run after attempt 1"
    )
    detail = owner.creator.client.get(t029.run_path(first["run_id"]))
    assert detail.status == 200, detail.text
    headers = t034.active_headers(detail)
    assert headers["max_active_ms"] == t034.GOLDEN_MAX_ACTIVE_MS, headers
    t034.assert_active_ms(
        headers["active_elapsed_ms"], at_least=5000, below=6000, label="detail header"
    )

    # Attempt 2 continues from the accumulated total: the claim does not reset it,
    # and the fresh attempt starts unsettled at zero.
    second_response = t034.execution_client(t029_stack, "supervisor").claim_job(
        execution_unit_id="unit-acc-2"
    )
    assert second_response.status == 200, second_response.text
    second = second_response.json
    assert second["run_id"] == first["run_id"], second
    assert second["job_id"] == first["job_id"], second
    assert second["attempt_token"] != first["attempt_token"], second

    run_after_second_claim = t034.run_active_row(t029_stack.dbname, first["run_id"])
    assert run_after_second_claim["active_elapsed_ms"] == run_after_first["active_elapsed_ms"], (
        run_after_first,
        run_after_second_claim,
    )
    fresh = t034.attempt_active_row(t029_stack.dbname, second["attempt_token"])
    assert fresh["active_ms"] == 0 and fresh["active_settled_at"] is None, fresh

    # Attempt 2 adds ~2s: two attempts are displayed as one 7s clock.
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, second["attempt_token"], 2)
    released2 = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        second["attempt_token"],
        execution_unit_id="unit-acc-2",
        evidence=startup_failed(second),
    )
    assert released2.status == 200, released2.text

    attempt2 = t034.attempt_active_row(t029_stack.dbname, second["attempt_token"])
    t034.assert_active_ms(attempt2["active_ms"], at_least=2000, below=3000, label="attempt 2")
    total = t034.run_active_row(t029_stack.dbname, first["run_id"])
    t034.assert_active_ms(
        total["active_elapsed_ms"], at_least=7000, below=9000, label="run total"
    )
    attempts = t034.attempts_for_run(t029_stack.dbname, first["run_id"])
    assert len(attempts) == 2, attempts

    # One accumulated clock: the owner read exposes the sum, never a per-attempt
    # reset or a second clock.
    detail = owner.creator.client.get(t029.run_path(first["run_id"]))
    assert detail.status == 200, detail.text
    headers = t034.active_headers(detail)
    assert headers["active_elapsed_ms"] == total["active_elapsed_ms"], (headers, total)
    assert headers["deadline_exceeded"] is False, headers


def test_settling_twice_is_idempotent(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-idem")
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 3)
    evidence = t029.exit_evidence(lease, started=True, reason="process_crashed", exit_code=1)
    client = t034.execution_client(t029_stack, "supervisor")

    first = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-idem", evidence=evidence
    )
    assert first.status == 200, first.text
    run_once = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    attempt_once = t034.attempt_active_row(t029_stack.dbname, lease["attempt_token"])
    t034.assert_active_ms(
        run_once["active_elapsed_ms"], at_least=3000, below=4000, label="settled total"
    )
    assert run_once["state"] == "recovery_pending", run_once

    # A repeated exit (same JSONB evidence) is a no-op: no second settlement, no
    # second terminal/park, no second allocation release.
    repeated = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-idem", evidence=evidence
    )
    assert repeated.status == 200, repeated.text
    assert t034.run_active_row(t029_stack.dbname, lease["run_id"]) == run_once
    assert t034.attempt_active_row(t029_stack.dbname, lease["attempt_token"]) == attempt_once
    assert t034.quota_counter(t029_stack.dbname)["used"] == 0
    allocations = t034.allocations_for_holder(
        t029_stack.dbname, "job:" + lease["job_id"]
    )
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations


def test_finalizing_does_not_accrue(t029_stack) -> None:
    owner, lease, artifact = t034.claimed_with_output(
        t029_stack, execution_unit_id="unit-finalizing"
    )
    worker = t034.execution_client(t029_stack, "worker")
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)

    proposal = t034.result_body(
        lease, suggested_state="succeeded", output_artifact_id=artifact
    )
    accepted = worker.submit_result(lease["run_id"], body=proposal)
    assert accepted.status == 200, accepted.text
    assert accepted.json["phase"] == "finalizing", accepted.json

    settled = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    t034.assert_active_ms(
        settled["active_elapsed_ms"], at_least=2000, below=3000, label="settled at proposal"
    )
    attempt = t034.attempt_active_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["active_settled_at"] is not None, attempt

    # Simulate another 20s of wall time: once phase=finalizing the live part is not
    # added any more (the proposal already settled the interval).
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 20)
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    headers = t034.active_headers(detail)
    assert headers["active_elapsed_ms"] == settled["active_elapsed_ms"], (headers, settled)
    assert headers["deadline_exceeded"] is False, headers

    heartbeat = t034.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-finalizing",
    )
    assert heartbeat.status == 200, heartbeat.text
    assert heartbeat.json["cancel_requested"] is False, heartbeat.json
    assert (
        t034.run_active_row(t029_stack.dbname, lease["run_id"])["active_elapsed_ms"]
        == settled["active_elapsed_ms"]
    )


def test_waiting_does_not_accrue_and_cannot_trigger_the_deadline(t029_stack) -> None:
    """The exact phase T035 will write for waiting; T034 only observes it."""
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-waiting")

    # A limit that the live attempt would blow through if waiting accrued.
    t034.set_run_max_active_ms(t029_stack.dbname, lease["run_id"], 1000)
    t034.set_run_phase(t029_stack.dbname, lease["run_id"], "waiting")
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 20)

    heartbeat = t034.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-waiting",
    )
    assert heartbeat.status == 200, heartbeat.text
    assert heartbeat.json["cancel_requested"] is False, heartbeat.json
    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["active_elapsed_ms"] == 0 and run["error_code"] is None, run
    assert run["cancel_requested_at"] is None, run
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    headers = t034.active_headers(detail)
    assert headers["active_elapsed_ms"] == 0, headers

    # The same attempt/limit does trigger once the run is actually executing: the
    # exclusion is the phase rule, not a broken deadline.
    t034.set_run_phase(t029_stack.dbname, lease["run_id"], "executing")
    over = t034.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-waiting",
    )
    assert over.status == 200, over.text
    assert over.json["cancel_requested"] is True, over.json
    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["error_code"] == t034.DEADLINE_CODE, run
