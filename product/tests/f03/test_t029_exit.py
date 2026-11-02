"""T029 acceptance: supervisor exit evidence, slot release and the started=false path.

The ticket's core failure case is "startup failed and can be proven never to have
started": the attempt is exited and revoked, the slot is released exactly once,
the job returns to ready and the run returns to queued. Evidence that says
otherwise stops at `held`: the F03 finalizer (T031) owns the next transition.
"""

from __future__ import annotations

import json

from support import stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

STARTUP_FAILED = {
    "started": False,
    "reason": "spawn_failed",
    "exit_code": 127,
    "detail": "worker process exited before loading the graph",
}


def setup_claimed(t029_stack):
    owner = t029.golden_owner(t029_stack)
    lease = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-exit")
    return owner, lease


def test_startup_failure_evidence_returns_work_to_the_queue(t029_stack) -> None:
    owner, lease = setup_claimed(t029_stack)
    job_id = lease["job_id"]
    before = t029.run_state_row(t029_stack.dbname, lease["run_id"])

    response = t029.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-exit",
        evidence=STARTUP_FAILED,
    )
    assert response.status == 200, response.text
    assert response.json == {"id": lease["attempt_token"], "state": "exited"}, response.json

    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None, attempt
    assert attempt["revoked_at"] is not None, attempt
    assert json.loads(attempt["exit_evidence"]) == STARTUP_FAILED, attempt

    job = t029.job_row(t029_stack.dbname, job_id)
    assert job["state"] == "ready", job
    assert job["current_attempt_token"] is None, job

    after = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert after["state"] == "queued", after
    assert after["phase"] == "idle", after
    # Claim (1->2) and the requeue (2->3) are two aggregate transitions.
    assert after["revision"] == before["revision"] + 1, (before, after)
    # The brief's "started_at unchanged": the first-claim timestamp is left as it is.
    assert after["started_at"] == before["started_at"], (before, after)
    assert after["finished_at"] is None, "a startup failure is not a terminal run"

    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    allocations = t029.quota_allocations(t029_stack.dbname)
    assert len(allocations) == 1, allocations
    assert allocations[0]["holder_key"] == "job:" + job_id
    assert allocations[0]["released_at"] is not None, allocations

    # The owner sees the returned run, not a fake start.
    view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert view.status == 200
    assert view.json["state"] == "queued" and view.json["phase"] == "idle", view.json
    assert view.header("X-Run-Lease-Deadline") is None


def test_repeating_identical_evidence_is_idempotent(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)

    first = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    )
    assert first.status == 200, first.text
    after_first = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    attempt_first = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    allocation_first = t029.allocation_for_job(t029_stack.dbname, lease["job_id"])

    # Different key order is still the same evidence: JSONB equality decides.
    reordered = {key: STARTUP_FAILED[key] for key in reversed(list(STARTUP_FAILED))}
    second = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=reordered
    )
    assert second.status == 200, second.text
    assert second.json == first.json, second.json

    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == after_first
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"]) == attempt_first
    assert t029.allocation_for_job(t029_stack.dbname, lease["job_id"]) == allocation_first
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    assert len(t029.quota_allocations(t029_stack.dbname)) == 1


def test_conflicting_evidence_is_a_409_and_changes_nothing(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)
    assert client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    ).status == 200

    before = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    allocation = t029.allocation_for_job(t029_stack.dbname, lease["job_id"])
    conflicting = {"started": False, "reason": "different_reason"}
    response = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=conflicting
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "attempt_evidence_conflict", response.text

    assert json.loads(
        t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["exit_evidence"]
    ) == STARTUP_FAILED
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == before
    assert t029.allocation_for_job(t029_stack.dbname, lease["job_id"]) == allocation
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0


def test_started_evidence_holds_the_job_and_leaves_the_run_non_terminal(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    evidence = {"started": True, "reason": "process_crashed", "exit_code": 1}

    response = t029.execution_client(t029_stack).confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=evidence
    )
    assert response.status == 200, response.text

    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None, attempt
    assert json.loads(attempt["exit_evidence"]) == evidence

    job = t029.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "held", job
    assert job["current_attempt_token"] is None, job

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "running", "T029 must not invent a terminal state"
    assert run["phase"] == "executing", run
    assert run["finished_at"] is None, run
    assert run["revision"] == 2, "the run aggregate does not move on a hold"

    # The physical execution unit is gone, so its slot is free. T031's finalizer
    # releases the allocation again idempotently (released_at is already set).
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    assert t029.allocation_for_job(t029_stack.dbname, lease["job_id"])["released_at"] is not None

    # The evidence is durable and a repeat is idempotent.
    repeated = t029.execution_client(t029_stack).confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=evidence
    )
    assert repeated.status == 200, repeated.text
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "held"


def test_absent_started_evidence_holds_the_job(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    response = t029.execution_client(t029_stack).confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-exit",
        evidence={"reason": "drained", "note": "no started key"},
    )
    assert response.status == 200, response.text
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "held"
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "running"


def test_a_second_claim_after_a_start_failure_succeeds(t029_stack) -> None:
    owner, first = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)
    assert client.confirm_exit(
        first["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    ).status == 200
    allocation_before = t029.allocation_for_job(t029_stack.dbname, first["job_id"])

    second = client.claim_job(execution_unit_id="unit-retry")
    assert second.status == 200, second.text
    lease = second.json
    t029.assert_lease(lease)
    assert lease["run_id"] == first["run_id"], lease
    assert lease["job_id"] == first["job_id"], lease
    assert lease["attempt_token"] != first["attempt_token"]

    attempts = t029.attempts_for_run(t029_stack.dbname, first["run_id"])
    assert len(attempts) == 2, attempts
    stored_run = t029.run_state_row(t029_stack.dbname, first["run_id"])
    assert stored_run["state"] == "running" and stored_run["phase"] == "executing", stored_run
    assert stored_run["revision"] == 4, stored_run
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1

    allocation_after = t029.allocation_for_job(t029_stack.dbname, first["job_id"])
    assert allocation_after["id"] == allocation_before["id"], (
        "the contract's unique holder key means the allocation is re-armed, not duplicated"
    )
    assert allocation_after["released_at"] is None, allocation_after
    assert len(t029.quota_allocations(t029_stack.dbname)) == 1

    # The retried lease is a real start lease again.
    assert t029.job_row(t029_stack.dbname, first["job_id"])["state"] == "leased"


def test_a_worker_certificate_cannot_confirm_exit(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    response = t029.execution_client(t029_stack, "worker").confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden", response.text
    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None, attempt
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"


def test_exit_requires_the_live_attempt_and_its_execution_unit(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)

    wrong_unit = client.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="some-other-unit",
        evidence=STARTUP_FAILED,
    )
    assert wrong_unit.status == 409, wrong_unit.text
    assert wrong_unit.json["error"]["code"] == "stale_attempt", wrong_unit.text
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["exited_at"] is None

    unknown = client.confirm_exit(t029.new_uuid(), execution_unit_id="unit-exit")
    assert unknown.status == 404, unknown.text
    assert unknown.json["error"]["code"] == "attempt_not_found", unknown.text

    malformed = client.confirm_exit("not-a-uuid", execution_unit_id="unit-exit")
    assert malformed.status == 404, malformed.text

    # An expired lease cannot be certified as an exit by this ticket: recovery
    # (and the reason it is not a death certificate) belongs to T031.
    t029.force_lease_expiry(t029_stack.dbname, lease["attempt_token"])
    expired = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    )
    assert expired.status == 409, expired.text
    assert expired.json["error"]["code"] == "stale_attempt", expired.text
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["exited_at"] is None


def test_exit_evidence_shape_is_validated_before_any_change(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)

    for evidence in ([], "not-an-object", None, 7):
        response = client.confirm_exit(
            lease["attempt_token"], execution_unit_id="unit-exit", body={
                "execution_unit_id": "unit-exit",
                "evidence": evidence,
            }
        )
        assert response.status == 422, (evidence, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (evidence, error)
        assert error.get("details", {}).get("field") == "evidence", (evidence, error)

    not_bool = client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", body={
            "execution_unit_id": "unit-exit",
            "evidence": {"started": "false"},
        }
    )
    assert not_bool.status == 422, not_bool.text
    assert not_bool.json["error"]["details"]["field"] == "evidence.started", not_bool.text

    missing_unit = client.confirm_exit(
        lease["attempt_token"], body={"evidence": {"started": False}}
    )
    assert missing_unit.status == 422, missing_unit.text
    assert missing_unit.json["error"]["details"]["field"] == "execution_unit_id", missing_unit.text

    unknown_key = client.confirm_exit(
        lease["attempt_token"],
        body={"execution_unit_id": "unit-exit", "evidence": {"started": False}, "extra": True},
    )
    assert unknown_key.status == 400, unknown_key.text
    assert unknown_key.json["error"]["code"] == "invalid_json", unknown_key.text

    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None, attempt
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"


def test_a_heartbeat_after_exit_is_a_409(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)
    assert client.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit", evidence=STARTUP_FAILED
    ).status == 200

    response = client.heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-exit",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text
