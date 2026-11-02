"""T029 acceptance: heartbeat renews only a live attempt and reports real facts.

Every heartbeat is a real mTLS request. Lease expiry is judged by the database
clock: the tests move a lease into the past with SQL instead of sleeping, and the
server must then answer 409 without touching the row.
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

CHECKPOINT_CONNECTIONS = [
    {
        "pid": 4242,
        "backend_start": "2026-11-03T00:00:00Z",
        "application_name": "orchara-checkpoint",
    },
    {
        "pid": 4243,
        "backend_start": "2026-11-03T00:00:01.250Z",
        "application_name": "orchara-saver",
    },
]


def epoch(value: str) -> float:
    return t029.lease_deadline_epoch(value)


def setup_claimed(t029_stack):
    owner = t029.golden_owner(t029_stack)
    lease = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-heartbeat")
    return owner, lease


def test_heartbeat_extends_the_lease_and_reports_control_facts(t029_stack) -> None:
    owner, lease = setup_claimed(t029_stack)
    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])

    # Make the extension unambiguous: the worker is 5s from expiry, not 30s.
    st.sql(
        t029_stack.dbname,
        "UPDATE platform.run_attempts SET lease_deadline = now() + interval '5 seconds' "
        f"WHERE token = {st.quote_literal(lease['attempt_token'])}::uuid",
    )
    before = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])

    response = t029.execution_client(t029_stack, "worker").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert response.status == 200, response.text
    reply = response.json
    t029.assert_heartbeat_reply(reply)

    after = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert epoch(after["lease_deadline"]) > epoch(before["lease_deadline"]) + 20, (
        before["lease_deadline"],
        after["lease_deadline"],
    )
    assert epoch(after["heartbeat_at"]) >= epoch(attempt["heartbeat_at"]), after
    assert abs(epoch(reply["lease_deadline"]) - epoch(after["lease_deadline"])) < 0.001, (
        reply["lease_deadline"],
        after["lease_deadline"],
    )

    # Control facts come from the database, not from defaults.
    assert reply["cancel_requested"] is False, reply
    assert reply["policy_revision"] == t029.policy_revision(t029_stack.dbname), reply
    stored_run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert reply["budget_remaining"] == stored_run["budget_limit"], (
        reply["budget_remaining"],
        stored_run["budget_limit"],
    )
    assert reply["budget_remaining"] == "1.00000000", reply

    owner_view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert owner_view.status == 200
    assert abs(
        epoch(owner_view.header("X-Run-Lease-Deadline")) - epoch(after["lease_deadline"])
    ) < 0.001


def test_heartbeat_reports_a_real_cancel_request(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    stored = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert stored["cancel_requested_at"] is None

    t029.set_cancel_requested(t029_stack.dbname, lease["run_id"])
    reply = t029.execution_client(t029_stack, "worker").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert reply.status == 200, reply.text
    assert reply.json["cancel_requested"] is True, reply.json
    # Cancel is intent: the run is still running until the cancel/finalizer ticket.
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "running"


def test_an_expired_lease_is_a_409_and_changes_nothing(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    t029.force_lease_expiry(t029_stack.dbname, lease["attempt_token"])
    expired = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])

    response = t029.execution_client(t029_stack, "worker").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text

    after = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert after["lease_deadline"] == expired["lease_deadline"], (expired, after)
    assert after["heartbeat_at"] == expired["heartbeat_at"], (expired, after)


def test_a_revoked_attempt_is_a_409(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    t029.revoke_attempt(t029_stack.dbname, lease["attempt_token"])

    response = t029.execution_client(t029_stack).heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text


def test_a_token_for_another_run_is_a_409(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    first = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-1")
    second_run = t029.queued_run(t029_stack, owner)

    response = t029.execution_client(t029_stack).heartbeat_run(
        second_run["id"],
        attempt_token=first["attempt_token"],
        execution_unit_id="unit-1",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text

    unknown = t029.execution_client(t029_stack).heartbeat_run(
        first["run_id"],
        attempt_token=t029.new_uuid(),
        execution_unit_id="unit-1",
    )
    assert unknown.status == 409, unknown.text
    assert unknown.json["error"]["code"] == "stale_attempt", unknown.text


def test_a_different_execution_unit_is_a_409(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    response = t029.execution_client(t029_stack).heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="some-other-unit",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text


def test_a_maintenance_certificate_cannot_heartbeat(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    response = t029.execution_client(t029_stack, "maintenance").heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden", response.text


def test_checkpoint_connections_are_persisted_and_validated(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack, "worker")

    stored = client.heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
        checkpoint_connections=CHECKPOINT_CONNECTIONS,
    )
    assert stored.status == 200, stored.text
    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert json.loads(attempt["checkpoint_connections"]) == CHECKPOINT_CONNECTIONS, attempt

    invalid = [
        [{"pid": 4}],
        [{"pid": 4, "backend_start": "not-a-time", "application_name": "x"}],
        [{"pid": 0, "backend_start": "2026-11-03T00:00:00Z", "application_name": "x"}],
        [{"pid": 4, "backend_start": "2026-11-03T00:00:00Z", "application_name": "x", "extra": 1}],
        "not-an-array",
    ]
    for connection in invalid:
        response = client.heartbeat_run(
            lease["run_id"],
            attempt_token=lease["attempt_token"],
            execution_unit_id="unit-heartbeat",
            body={
                "attempt_token": lease["attempt_token"],
                "execution_unit_id": "unit-heartbeat",
                "checkpoint_connections": connection,
            },
        )
        assert response.status == 422, (connection, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (connection, error)
        assert error.get("details", {}).get("field") == "checkpoint_connections", (connection, error)

    # A heartbeat without the field leaves the recorded connections untouched.
    plain = client.heartbeat_run(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        execution_unit_id="unit-heartbeat",
    )
    assert plain.status == 200, plain.text
    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert json.loads(attempt["checkpoint_connections"]) == CHECKPOINT_CONNECTIONS


def test_the_heartbeat_body_requires_valid_uuids_and_unit(t029_stack) -> None:
    _owner, lease = setup_claimed(t029_stack)
    client = t029.execution_client(t029_stack)

    for body, field in (
        ({"attempt_token": "not-a-uuid", "execution_unit_id": "unit-heartbeat"}, "attempt_token"),
        ({"attempt_token": lease["attempt_token"], "execution_unit_id": " "}, "execution_unit_id"),
        ({"attempt_token": lease["attempt_token"]}, "execution_unit_id"),
    ):
        response = client.heartbeat_run(lease["run_id"], body=body)
        assert response.status == 422, (field, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (field, error)
        assert error.get("details", {}).get("field") == field, (field, error)
