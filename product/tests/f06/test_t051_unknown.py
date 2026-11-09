"""F06/T051 acceptance: unknown external writes and evidence reconciliation.

Every test drives the real stack: PostgreSQL migrated through 0032, the compiled
API, real mTLS capability calls, real object bytes, the real T050 approval flow
and a real loopback connector endpoint. The lost-receipt server really receives
the write and really closes the connection with no response; the platform's real
egress guard sees a connection loss.

The assertions are on durable facts: ``platform.operations`` state and
``reconciliation_*`` columns, ``platform.run_unknown_operations``,
``platform.operation_attempts`` (the physical write count), the Run state and the
endpoint's own request log.

``not_run``: a real vendor connector (loopback stands in for the
deployment-defined endpoint), a real browser DOM (the JS helpers are executed
under Node in ``test_t051_pages.py``), and a provider that supports idempotency
keys or query-by-receipt (no such connector shape exists in F06/T048, so the
disclosure path is the only real one here).
"""

from __future__ import annotations

import json
import uuid

import pytest

from support.runtime_stack import Stack
from support import f00b, t024, t029, t035, t041, t048, t050, t051, t028
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

T051_SECRET_VALUE = "t051-lost-receipt-secret-3d9b1f"


@pytest.fixture
def t051_stack(tmp_path, fresh_database, go_binaries, api_server) -> Stack:
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env[t048.TEST_SECRET_ENV] = T051_SECRET_VALUE
    server = api_server(dsn, extra_env=env)

    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


@pytest.fixture
def write_server() -> t051.LostReceiptServer:
    server = t051.LostReceiptServer()
    try:
        yield server
    finally:
        server.close()


def lose_the_receipt(stack, server, owner) -> dict:
    """Drive a real write dispatch whose response is lost; return the facts."""
    prepared = t051.prepare_approved_write(stack, server, owner)
    assert server.records == [], "the approval path must never send"
    server.respond_drop()
    response = t051.invoke_capability(stack, prepared["lease"], prepared)
    assert response.status == 200, response.text
    assert response.json["state"] == t051.STATE_UNKNOWN, response.json
    prepared["response"] = response.json
    return prepared


def park_needs_reconciliation(stack, lease, owner_id: str, *, success_output: bool = False) -> None:
    """Report the worker result and exit; the unknown guard parks the Run."""
    if success_output:
        artifact = t051.seed_output_artifact(stack, owner_id)
        t051.link_output(stack, lease["run_id"], artifact, lease["attempt_token"])
        t051.submit_succeeded_result(stack, lease, artifact)
    else:
        t051.submit_failed_result(stack, lease)
    t051.confirm_exit(stack, lease)


# ---------------------------------------------------------------------------
# milestone 1: unknown, never failed/succeeded; duplicate never resends
# ---------------------------------------------------------------------------
def test_lost_receipt_is_unknown_and_duplicate_invoke_never_resends(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]

    # The real endpoint observed exactly one write.
    assert t051.connector_request_count(write_server) == 1, write_server.records
    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation is not None
    assert operation["state"] == t051.STATE_UNKNOWN, operation
    assert operation["receipt_artifact_id"] is None, operation
    assert operation["last_error_code"] == "connector_transport_error", operation
    assert operation["reconciliation_limitation"] == t051.LIMITATION_NO_IDEMPOTENCY, operation
    attempts = t051.attempt_rows(t051_stack.dbname, operation_id)
    assert len(attempts) == 1 and attempts[0]["state"] == "dispatching", attempts

    unknown = t051.unknown_row(t051_stack.dbname, run_id, operation_id)
    assert unknown is not None and unknown["resolved_at"] is None, unknown
    assert unknown["reason"] == "transport_error", unknown

    # Duplicate invoke of the same operation/logical key: the recorded unknown
    # state is returned and no second physical request happens.
    duplicate = t051.invoke_capability(t051_stack, prepared["lease"], prepared)
    assert duplicate.status == 200, duplicate.text
    assert duplicate.json["state"] == t051.STATE_UNKNOWN, duplicate.json
    assert t051.connector_request_count(write_server) == 1, write_server.records

    # A same-key/different-params call is the documented 409 conflict.
    changed = dict(prepared["params"])
    changed["body"] = {"amount": 9999, "currency": "CNY"}
    conflict = t048.invoke(
        t051_stack,
        prepared["lease"],
        policy_id=prepared["policy"]["id"],
        params=changed,
        logical_key=prepared["logical_key"],
        operation_id=operation_id,
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "operation_conflict", conflict.text
    assert t051.connector_request_count(write_server) == 1, write_server.records

    # The attempt exits: the Run parks in needs_reconciliation, never failed.
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)
    run = t051.run_state(t051_stack.dbname, run_id)
    assert run["state"] == t051.STATE_NEEDS_RECONCILIATION, run
    assert run["error_code"] == "unknown_operations", run
    assert run["finished_at"] is None, run


def test_a_five_hundred_after_send_is_unknown_not_failed(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = t051.prepare_approved_write(t051_stack, write_server, owner)
    write_server.respond_status(503, {"ok": False})
    response = t051.invoke_capability(t051_stack, prepared["lease"], prepared)
    assert response.status == 200, response.text
    assert response.json["state"] == t051.STATE_UNKNOWN, response.json
    operation = t051.operation_row(
        t051_stack.dbname, prepared["run"]["id"], prepared["operation_id"]
    )
    assert operation["state"] == t051.STATE_UNKNOWN, operation
    assert operation["receipt_artifact_id"] is None, operation
    assert t051.connector_request_count(write_server) == 1, write_server.records


def test_a_receipt_that_cannot_be_sealed_is_unknown_not_failed(t051_stack, write_server) -> None:
    """P1-3 regression: a real write whose receipt cannot be persisted is unknown.

    The endpoint really receives and applies the write, then answers 2xx; the API
    process can no longer seal the receipt object. Before the fix this path ran
    ``failAttemptAndOperation``, so the operation became ``failed`` and no
    ``run_unknown_operations`` row existed: the Run could be failed even though
    the remote side had applied one write. The only honest state is ``unknown``
    with one unresolved ledger row, and owner evidence must settle it without a
    second write.
    """
    owner = t041.owner(t051_stack)
    prepared = t051.prepare_approved_write(t051_stack, write_server, owner)
    assert write_server.records == [], "the approval path must never send"

    fault = t051.ReceiptSealFault(t051_stack)
    write_server.responder = fault.responder
    try:
        response = t051.invoke_capability(t051_stack, prepared["lease"], prepared)
    finally:
        fault.restore()
    assert response.status == 200, response.text
    assert response.json["state"] == t051.STATE_UNKNOWN, response.json

    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    assert t051.connector_request_count(write_server) == 1, write_server.records
    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation["state"] == t051.STATE_UNKNOWN, operation
    assert operation["last_error_code"] == "connector_receipt_unavailable", operation
    assert operation["receipt_artifact_id"] is None, operation
    assert operation["reconciliation_limitation"] == t051.LIMITATION_NO_IDEMPOTENCY, operation
    attempts = t051.attempt_rows(t051_stack.dbname, operation_id)
    assert len(attempts) == 1 and attempts[0]["state"] == "dispatching", attempts
    unknown = t051.unknown_row(t051_stack.dbname, run_id, operation_id)
    assert unknown is not None and unknown["resolved_at"] is None, unknown
    assert unknown["reason"] == "receipt_seal_failed", unknown

    # The Run parks in needs_reconciliation; the owner's evidence settles the one
    # real write without a resend.
    park_needs_reconciliation(
        t051_stack, prepared["lease"], owner.creator.principal_id, success_output=True
    )
    assert t051.run_state(t051_stack.dbname, run_id)["state"] == t051.STATE_NEEDS_RECONCILIATION
    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    settled = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=evidence,
    )
    assert settled.status == 200, settled.text
    assert settled.json["state"] == t051.STATE_SUCCEEDED, settled.json
    assert t051.physical_write_count(t051_stack.dbname, operation_id) == 1
    assert t051.connector_request_count(write_server) == 1, write_server.records


# ---------------------------------------------------------------------------
# milestone 2: reconciliation
# ---------------------------------------------------------------------------
def test_confirmed_success_uses_evidence_and_never_resends(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    prepared["lease"]["owner_id"] = owner.creator.principal_id
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id, success_output=True)
    assert t051.run_state(t051_stack.dbname, run_id)["state"] == t051.STATE_NEEDS_RECONCILIATION

    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    key = "t051-confirmed-success-1"
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=evidence,
        idempotency_key=key,
    )
    assert response.status == 200, response.text
    assert response.json == {"id": operation_id, "state": t051.STATE_SUCCEEDED}, response.json

    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation["state"] == t051.STATE_SUCCEEDED, operation
    assert operation["receipt_artifact_id"] == evidence, operation
    assert operation["reconciled_by"] == owner.creator.principal_id, operation
    assert operation["reconciliation_evidence_id"] == evidence, operation
    assert operation["reconciliation_conclusion"] == t051.CONCLUSION_SUCCESS, operation
    assert operation["reconciliation_key"] == key, operation
    assert operation["last_error_code"] is None, operation

    unknown = t051.unknown_row(t051_stack.dbname, run_id, operation_id)
    assert unknown is not None and unknown["resolved_at"] is not None, unknown

    # The Run leaves needs_reconciliation through the finalizer: the stored
    # succeeded proposal is now authoritative.
    run = t051.run_state(t051_stack.dbname, run_id)
    assert run["state"] == t051.STATE_SUCCEEDED, run
    assert run["finished_at"] is not None, run

    # No resend: one physical attempt, one observed request.
    assert t051.physical_write_count(t051_stack.dbname, operation_id) == 1
    assert t051.connector_request_count(write_server) == 1, write_server.records

    # The same tuple replays the recorded answer idempotently.
    replay = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=evidence,
        idempotency_key=key,
    )
    assert replay.status == 200, replay.text
    assert replay.json["state"] == t051.STATE_SUCCEEDED, replay.json
    assert t051.connector_request_count(write_server) == 1, write_server.records

    # A different conclusion after the terminal write is a conflict.
    conflict = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_FAILURE, evidence_artifact_id=evidence,
        idempotency_key="t051-conflicting-conclusion",
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "reconciliation_conflict", conflict.text

    item = t051.ledger_operation(owner.creator, run_id, operation_id)
    assert item["state"] == t051.STATE_SUCCEEDED, item
    assert not item.get("requires_reconciliation", False), item
    assert item["reconciliation_conclusion"] == t051.CONCLUSION_SUCCESS, item
    assert item["attempts"] == 1, item


def test_confirmed_not_sent_fails_without_a_second_operation_or_write(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)

    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_NOT_SENT, evidence_artifact_id=evidence,
    )
    assert response.status == 200, response.text
    assert response.json["state"] == t051.STATE_FAILED, response.json

    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation["state"] == t051.STATE_FAILED, operation
    assert operation["last_error_code"] == "operation_not_sent", operation
    assert operation["reconciliation_conclusion"] == t051.CONCLUSION_NOT_SENT, operation
    # No safe same-operation retry exists without explicit policy support.
    assert operation["receipt_artifact_id"] is None, operation

    # A new operation id was never created and no second request happened.
    rows = t051.operation_rows(t051_stack.dbname, run_id)
    assert len(rows) == 1 and rows[0]["id"] == operation_id, rows
    assert t051.physical_write_count(t051_stack.dbname, operation_id) == 1
    assert t051.connector_request_count(write_server) == 1, write_server.records

    unknown = t051.unknown_row(t051_stack.dbname, run_id, operation_id)
    assert unknown["resolved_at"] is not None, unknown
    run = t051.run_state(t051_stack.dbname, run_id)
    assert run["state"] == t051.STATE_FAILED, run
    assert run["error_code"] == "operation_not_sent", run

    # The terminal operation cannot be re-settled with a fresh key.
    again = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=evidence,
        idempotency_key="t051-second-settlement",
    )
    assert again.status == 409, again.text
    assert again.json["error"]["code"] == "reconciliation_conflict", again.text


def test_confirmed_failure_marks_the_run_failed(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)
    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_FAILURE, evidence_artifact_id=evidence,
    )
    assert response.status == 200, response.text
    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation["state"] == t051.STATE_FAILED, operation
    assert operation["reconciliation_conclusion"] == t051.CONCLUSION_FAILURE, operation
    assert operation["last_error_code"] == "connector_transport_error", operation
    run = t051.run_state(t051_stack.dbname, run_id)
    assert run["state"] == t051.STATE_FAILED, run
    assert run["finished_at"] is not None, run
    assert t051.connector_request_count(write_server) == 1, write_server.records


def test_only_the_owner_or_approver_may_reconcile(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)
    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    intruder = t041.secondary_owner(t051_stack)
    response = t051.reconcile(
        intruder.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=evidence,
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden", response.text
    assert t051.operation_row(t051_stack.dbname, run_id, operation_id)["state"] == t051.STATE_UNKNOWN
    assert t051.run_state(t051_stack.dbname, run_id)["state"] == t051.STATE_NEEDS_RECONCILIATION


# ---------------------------------------------------------------------------
# evidence refusals
# ---------------------------------------------------------------------------
def test_evidence_must_exist_be_ready_and_bind_to_the_operation(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)

    missing = str(uuid.uuid4())
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=missing,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evidence_artifact_invalid", response.text

    wrong = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id,
        operation_id=str(uuid.uuid4()), run_id=run_id,
    )
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=wrong,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evidence_artifact_invalid", response.text

    tombstoned = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    t051.tombstone_evidence(t051_stack, tombstoned)
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=tombstoned,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evidence_artifact_invalid", response.text

    # Another owner's artifact, even when it exists and is ready, is refused.
    other = t041.secondary_owner(t051_stack)
    foreign = t051.seed_evidence_artifact(
        t051_stack, other.creator.principal_id, operation_id=operation_id, run_id=run_id
    )
    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence_artifact_id=foreign,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evidence_artifact_invalid", response.text

    # Nothing was settled by any refused attempt.
    assert t051.operation_row(t051_stack.dbname, run_id, operation_id)["state"] == t051.STATE_UNKNOWN
    assert t051.run_state(t051_stack.dbname, run_id)["state"] == t051.STATE_NEEDS_RECONCILIATION


def test_missing_idempotency_key_and_bad_conclusion_are_refused(t051_stack, write_server) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    operation_id = prepared["operation_id"]
    park_needs_reconciliation(t051_stack, prepared["lease"], owner.creator.principal_id)
    evidence = t051.seed_evidence_artifact(
        t051_stack, owner.creator.principal_id,
        operation_id=operation_id, run_id=prepared["run"]["id"],
    )

    response = t051.reconcile_without_key(
        owner.creator, operation_id,
        conclusion=t051.CONCLUSION_SUCCESS, evidence=evidence,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "validation_failed", response.text
    assert response.json["error"]["details"]["field"] == "Idempotency-Key", response.text

    response = t051.reconcile(
        owner.creator, operation_id,
        conclusion="confirmed_maybe", evidence_artifact_id=evidence,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "validation_failed", response.text
    assert t051.operation_row(
        t051_stack.dbname, prepared["run"]["id"], operation_id
    )["state"] == t051.STATE_UNKNOWN


# ---------------------------------------------------------------------------
# disclosure
# ---------------------------------------------------------------------------
def test_no_idempotency_query_support_is_disclosed_and_never_auto_retried(
    t051_stack, write_server
) -> None:
    owner = t041.owner(t051_stack)
    prepared = lose_the_receipt(t051_stack, write_server, owner)
    run_id = prepared["run"]["id"]
    operation_id = prepared["operation_id"]

    operation = t051.operation_row(t051_stack.dbname, run_id, operation_id)
    assert operation["reconciliation_limitation"] == t051.LIMITATION_NO_IDEMPOTENCY, operation
    assert operation["provider_idempotency_key"] is None, operation
    response_data = prepared["response"].get("data") or {}
    assert response_data.get("limitation") == t051.LIMITATION_NO_IDEMPOTENCY, response_data
    assert response_data.get("requires_reconciliation") is True, response_data

    item = t051.ledger_operation(owner.creator, run_id, operation_id)
    assert item["requires_reconciliation"] is True, item
    assert item["reconciliation_limitation"] == t051.LIMITATION_NO_IDEMPOTENCY, item
    assert item.get("provider_idempotency_key") is None, item
    assert item["attempts"] == 1, item

    # No automatic resend exists: a duplicate invoke is a recorded-state replay.
    duplicate = t051.invoke_capability(t051_stack, prepared["lease"], prepared)
    assert duplicate.status == 200 and duplicate.json["state"] == t051.STATE_UNKNOWN
    # The duplicate's replay data carries the same limitation disclosure as the
    # first dispatch and the owner ledger; an unknown write never pretends it
    # could be queried or safely retried.
    replay_data = duplicate.json.get("data") or {}
    assert replay_data.get("limitation") == t051.LIMITATION_NO_IDEMPOTENCY, replay_data
    assert replay_data.get("requires_reconciliation") is True, replay_data
    assert replay_data.get("dispatch_state") == "unknown", replay_data
    assert t051.connector_request_count(write_server) == 1, write_server.records
