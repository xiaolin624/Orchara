"""T034 acceptance: the frozen active-time deadline stops work and fails honestly.

The deadline is checked in claim, heartbeat and result submission. At/over the
limit the platform records the intent once (``cancel_requested_at`` +
``error_code='deadline_exceeded'``), heartbeat answers ``cancel_requested=true``,
new results are refused, and once the execution unit exits the same T031 finalizer
commits ``failed(deadline_exceeded)`` with no output artifact and releases the slot
exactly once. A Run with a NULL limit has no deadline (unknown is not zero).
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


def short_deadline(t029_stack, run_id: str, *, milliseconds: int = 1000) -> None:
    t034.set_run_max_active_ms(t029_stack.dbname, run_id, milliseconds)


def heartbeat(t029_stack, lease: dict, unit: str = "unit-deadline"):
    return t034.execution_client(t029_stack, "supervisor").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id=unit,
    )


def test_heartbeat_at_the_deadline_records_the_intent_once_and_refuses_results(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-deadline")
    short_deadline(t029_stack, lease["run_id"], milliseconds=1000)
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)

    first = heartbeat(t029_stack, lease)
    assert first.status == 200, first.text
    t029.assert_heartbeat_reply(first.json)
    assert first.json["cancel_requested"] is True, first.json

    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "running", "the deadline is intent, not a terminal guess"
    assert run["error_code"] == t034.DEADLINE_CODE, run
    assert run["cancel_requested_at"] is not None, run
    first_cancel_at = run["cancel_requested_at"]
    # The lease is still renewed so the supervisor can stop the unit and certify
    # the exit that lets the finalizer commit.
    attempt = t034.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None, attempt

    second = heartbeat(t029_stack, lease)
    assert second.status == 200, second.text
    assert second.json["cancel_requested"] is True, second.json
    again = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert again["cancel_requested_at"] == first_cancel_at, (
        "cancel_requested_at must be recorded once",
        run,
        again,
    )

    # No new result work: the proposal is refused before it is stored. The refusal
    # still settled this attempt's interval; the run is not terminal.
    worker = t034.execution_client(t029_stack, "worker")
    refused = worker.submit_result(
        lease["run_id"],
        body=t034.result_body(lease, suggested_state="failed", error_code="tool_timeout"),
    )
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == t034.DEADLINE_CODE, refused.text
    after_refusal = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert after_refusal["state"] == "running" and after_refusal["finished_at"] is None, after_refusal
    assert after_refusal["error_code"] == t034.DEADLINE_CODE, after_refusal
    assert t034.attempt_active_row(
        t029_stack.dbname, lease["attempt_token"]
    )["reported_result"] is None
    t034.assert_active_ms(
        after_refusal["active_elapsed_ms"], at_least=2000, below=4000, label="settled at refusal"
    )
    # The pure deadline rule still holds after settlement (no live part is added).
    retry = worker.submit_result(
        lease["run_id"],
        body=t034.result_body(lease, suggested_state="failed", error_code="tool_timeout"),
    )
    assert retry.status == 409 and retry.json["error"]["code"] == t034.DEADLINE_CODE, retry.text

    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    headers = t034.active_headers(detail)
    assert headers["max_active_ms"] == 1000, headers
    assert headers["deadline_exceeded"] is True, headers
    assert headers["active_elapsed_ms"] >= 2000, headers
    assert detail.json["error_code"] == t034.DEADLINE_CODE, detail.json

    # The supervisor's exit then drives the one finalizer.
    exited = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-deadline",
        evidence=t029.exit_evidence(lease, started=True, exit_code=137),
    )
    assert exited.status == 200, exited.text

    terminal = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert terminal["state"] == "failed", terminal
    assert terminal["error_code"] == t034.DEADLINE_CODE, terminal
    assert terminal["output_artifact_id"] is None, "no output artifact is published"
    assert terminal["finished_at"] is not None and terminal["phase"] == "idle", terminal
    assert terminal["revision"] == 3, "claim 1->2 and the deadline terminal 2->3"
    job = t034.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    assert t034.quota_counter(t029_stack.dbname)["used"] == 0
    allocations = t034.allocations_for_holder(t029_stack.dbname, "job:" + lease["job_id"])
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations
    assert t034.latest_run_status(t029_stack.dbname, lease["run_id"]) == {
        "state": "failed",
        "error_code": t034.DEADLINE_CODE,
    }
    settled_attempt = t034.attempt_active_row(t029_stack.dbname, lease["attempt_token"])
    assert settled_attempt["active_settled_at"] is not None, settled_attempt
    assert settled_attempt["active_ms"] == terminal["active_elapsed_ms"], (
        "the attempt's settled interval is the run total",
        settled_attempt,
        terminal,
    )

    # A repeat exit is a no-op: the deadline never commits twice.
    repeated = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-deadline",
        evidence=t029.exit_evidence(lease, started=True, exit_code=137),
    )
    assert repeated.status == 200, repeated.text
    assert t034.run_active_row(t029_stack.dbname, lease["run_id"]) == terminal


def test_exit_without_a_prior_heartbeat_still_resolves_the_deadline(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-exit-only")
    short_deadline(t029_stack, lease["run_id"], milliseconds=1000)
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)

    exited = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-exit-only",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert exited.status == 200, exited.text

    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "failed" and run["error_code"] == t034.DEADLINE_CODE, run
    assert run["cancel_requested_at"] is not None, "the exit path records the intent too"
    assert run["output_artifact_id"] is None, run
    assert t034.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"


def test_claim_refuses_a_queued_run_that_is_already_over(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    queued = t034.queued_run(t029_stack, owner)
    # A queued Run with accumulated active time already at/over its frozen limit:
    # it must never be leased (a second attempt would only burn more budget).
    t034.set_run_active_elapsed_ms(t029_stack.dbname, queued["id"], 5000)
    t034.set_run_max_active_ms(t029_stack.dbname, queued["id"], 4000)

    claim = t034.execution_client(t029_stack, "supervisor").claim_job(
        execution_unit_id="unit-over"
    )
    assert claim.status == 204, claim.text
    assert claim.raw == b"", claim.raw

    run = t034.run_active_row(t029_stack.dbname, queued["id"])
    assert run["state"] == "failed", run
    assert run["error_code"] == t034.DEADLINE_CODE, run
    assert run["output_artifact_id"] is None and run["finished_at"] is not None, run
    assert run["revision"] == 2, "queued 1 -> failed 2, with no attempt"
    assert run["cancel_requested_at"] is not None, run
    assert t034.attempts_for_run(t029_stack.dbname, queued["id"]) == []
    job = t034.job_row(t029_stack.dbname, t034.jobs_for_run(t029_stack.dbname, queued["id"])[0]["id"])
    assert job["state"] == "void", "the job never ran"
    assert t034.quota_allocations(t029_stack.dbname) == []
    assert t034.quota_counter(t029_stack.dbname)["used"] == 0
    assert t034.latest_run_status(t029_stack.dbname, queued["id"]) == {
        "state": "failed",
        "error_code": t034.DEADLINE_CODE,
    }

    detail = owner.creator.client.get(t029.run_path(queued["id"]))
    headers = t034.active_headers(detail)
    assert headers["deadline_exceeded"] is True, headers
    assert headers["active_elapsed_ms"] == 5000, headers

    # The refusal is not a stuck claim loop: the next real job is lease-able.
    second = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-after-over")
    assert second["run_id"] != queued["id"]


def test_a_live_child_blocks_the_deadline_terminal_and_parks_the_run(t029_stack) -> None:
    """The deadline still goes through the T031 exit guard, never around it."""
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-child-deadline")
    child = t034.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="running",
    )
    short_deadline(t029_stack, lease["run_id"], milliseconds=1000)
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)

    beat = heartbeat(t029_stack, lease, unit="unit-child-deadline")
    assert beat.status == 200 and beat.json["cancel_requested"] is True, beat.text
    exited = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-child-deadline",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert exited.status == 200, exited.text

    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "recovery_pending", run
    assert run["phase"] == "idle" and run["finished_at"] is None, run
    assert run["error_code"] == t034.DEADLINE_CODE, "the intent survives the blocked guard"
    assert run["revision"] == 3, "claim 1->2 and the park 2->3"
    job = t034.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", job
    assert t034.quota_counter(t029_stack.dbname)["used"] == 0
    child_row = t034.run_active_row(t029_stack.dbname, child)
    assert child_row["state"] == "running", "the guard is the live child, not a timeout guess"


def test_a_run_without_a_max_has_no_deadline(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-null-max")
    # NULL is "unknown / no deadline", never zero.
    t034.set_run_max_active_ms(t029_stack.dbname, lease["run_id"], None)
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 20)

    beat = heartbeat(t029_stack, lease, unit="unit-null-max")
    assert beat.status == 200, beat.text
    assert beat.json["cancel_requested"] is False, beat.json
    run = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert run["error_code"] is None and run["cancel_requested_at"] is None, run

    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    headers = t034.active_headers(detail)
    assert headers["active_elapsed_ms"] >= 20_000, headers
    assert headers["max_active_ms"] is None, "a NULL limit omits the header"
    assert headers["deadline_exceeded"] is False, headers

    # The normal proposal path is untouched: a real failure code is the outcome.
    worker = t034.execution_client(t029_stack, "worker")
    accepted = worker.submit_result(
        lease["run_id"],
        body=t034.result_body(lease, suggested_state="failed", error_code="tool_output_invalid"),
    )
    assert accepted.status == 200, accepted.text
    exited = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-null-max",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert exited.status == 200, exited.text
    terminal = t034.run_active_row(t029_stack.dbname, lease["run_id"])
    assert terminal["state"] == "failed", terminal
    assert terminal["error_code"] == "tool_output_invalid", terminal
    assert terminal["active_elapsed_ms"] >= 20_000, terminal
