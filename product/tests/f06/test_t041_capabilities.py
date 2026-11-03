"""F06/T041 acceptance: the run-scoped controlled capability proxy.

Every test here is the real stack: PostgreSQL migrated through migration 0025, the
compiled API, the real mTLS listener with a real worker certificate, the real
sealed execution manifest of a debug run, and the real filesystem object store.
The model provider is the documented dev stub (``provider="stub"``); there is no
vendor adapter in this build, so the real-provider path is asserted as the honest
``provider_not_configured`` infrastructure failure, never as a fake success.

Real sandboxed python/agent-node execution is T042/T043. The worker side of the
tool tests executes a real local function (`celsius_to_fahrenheit`) after the
server grants the call, and reports the result through the non-contract
capability-results endpoint - the same protocol T043 will use.
"""

from __future__ import annotations

import json

import pytest

from support import f00b, t041


def celsius_to_fahrenheit(city: str, celsius: float) -> dict:
    """A real local tool function, not a network mock."""
    return {"city": city, "fahrenheit": round(celsius * 9 / 5 + 32, 2)}


def test_model_call_is_stub_labeled_and_ledgered(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)

    response = t041.invoke(
        worker,
        lease,
        operation_id=operation_id,
        params=t041.model_params(
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            }
        ),
    )
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"operation_id", "state", "receipt_artifact_id", "approval_id", "data"}, body
    assert body["operation_id"] == operation_id
    assert body["state"] == "succeeded", body
    assert body["approval_id"] is None
    assert body["data"]["stub"] is True, body["data"]
    assert body["data"]["text"].startswith("stub-agent:")
    assert set(body["data"]["structured"]) == {"answer"}
    assert body["data"]["tool_calls"] == []
    assert body["data"]["usage"]["input_tokens"] > 0
    assert body["data"]["model_profile_version_id"] == t041.GOLDEN_MODEL_ID

    receipt = t041.artifact_row(f06_stack.dbname, body["receipt_artifact_id"])
    assert receipt["state"] == "ready" and receipt["purpose"] == "receipt", receipt
    document = t041.receipt_document(f06_stack, body["receipt_artifact_id"])
    assert document["stub"] is True and document["text"] == body["data"]["text"]

    operations = t041.operation_rows(f06_stack.dbname, run["id"])
    assert len(operations) == 1, operations
    operation = operations[0]
    assert operation["id"] == operation_id
    assert operation["capability_kind"] == "model"
    assert operation["capability_version"] == t041.GOLDEN_MODEL_ID
    assert operation["risk_class"] == "pure"
    assert operation["state"] == "succeeded"
    assert operation["receipt_artifact_id"] == body["receipt_artifact_id"]
    assert len(operation["params_sha256"]) == 64
    params_artifact = t041.artifact_row(f06_stack.dbname, operation["params_artifact_id"])
    assert params_artifact["state"] == "ready", params_artifact

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["attempt_no"] == 1
    assert attempts[0]["state"] == "succeeded"
    assert attempts[0]["attempt_token"] == lease["attempt_token"]
    assert attempts[0]["tokens_in"] > 0 and attempts[0]["tokens_out"] > 0
    assert attempts[0]["charged_amount"] is not None
    assert attempts[0]["result_artifact_id"] == body["receipt_artifact_id"]

    items = t041.ledger_items(owner.creator, run["id"])
    assert len(items) == 1, items
    entry = items[0]
    assert set(entry) == {
        "id",
        "capability_kind",
        "capability_version",
        "state",
        "attempts",
        "error_code",
        "receipt_artifact_id",
        "tool_call_summary",
        "structured_output_keys",
        "stub",
    }, entry
    assert entry["attempts"] == 1
    assert entry["stub"] is True
    assert entry["structured_output_keys"] == ["answer"]
    assert entry["receipt_artifact_id"] == body["receipt_artifact_id"]
    # The ledger read never echoes params or the receipt body.
    assert "params" not in entry
    assert "Summarize the sales file" not in json.dumps(entry)


def test_duplicate_operation_replays_without_a_second_physical_call(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    body = {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": "same-call",
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": t041.model_params(),
        "capability_kind": "model",
    }

    first = t041.invoke(worker, lease, operation_id=operation_id, body=body)
    assert first.status == 200, first.text
    second = t041.invoke(worker, lease, operation_id=operation_id, body=body)
    assert second.status == 200, second.text
    assert second.json["receipt_artifact_id"] == first.json["receipt_artifact_id"]
    assert second.json["data"] == first.json["data"]

    assert t041.attempt_count(f06_stack.dbname, operation_id) == 1
    assert len(t041.operation_rows(f06_stack.dbname, run["id"])) == 1


def test_logical_key_conflict_is_a_409_and_never_a_second_call(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)
    first_id = t041.new_uuid()
    first = t041.invoke(worker, lease, operation_id=first_id, logical_key="shared",
                        params=t041.model_params())
    assert first.status == 200, first.text

    different_id = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), logical_key="shared",
        params=t041.model_params(messages=[{"role": "user", "content": "different"}]),
    )
    assert different_id.status == 409, different_id.text
    assert different_id.json["error"]["code"] == "operation_conflict", different_id.text

    same_id = t041.invoke(
        worker, lease, operation_id=first_id, logical_key="shared",
        params=t041.model_params(messages=[{"role": "user", "content": "changed"}]),
    )
    assert same_id.status == 409, same_id.text
    assert same_id.json["error"]["code"] == "operation_conflict"

    assert len(t041.operation_rows(f06_stack.dbname, run["id"])) == 1
    assert t041.attempt_count(f06_stack.dbname, first_id) == 1


def test_tool_call_ids_and_arguments_are_validated_before_sealing(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)
    worker = t041.worker_client(f06_stack)
    tools = [{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}]

    cases = [
        (
            "unknown tool",
            [{"id": "call-1", "name": "delete", "arguments": {"city": "Berlin"}}],
        ),
        (
            "duplicate ids",
            [
                {"id": "same", "name": "lookup", "arguments": {"city": "Berlin"}},
                {"id": "same", "name": "lookup", "arguments": {"city": "Paris"}},
            ],
        ),
        (
            "bad arguments",
            [{"id": "call-1", "name": "lookup", "arguments": {"city": 7}}],
        ),
        (
            "wrong argument set",
            [{"id": "call-1", "name": "lookup", "arguments": {}}],
        ),
    ]
    failed_ids = []
    for name, hint in cases:
        operation_id = t041.new_uuid()
        response = t041.invoke(
            worker, lease, operation_id=operation_id, logical_key=name,
            params=t041.model_params(tools=tools, stub_tool_calls=hint),
        )
        assert response.status == 422, (name, response.text)
        assert response.json["error"]["code"] == "provider_response_invalid", (name, response.text)
        failed_ids.append(operation_id)
        operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
        assert operation["state"] == "failed", (name, operation)
        assert operation["receipt_artifact_id"] is None, (name, operation)
        attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
        assert len(attempts) == 1, (name, attempts)
        assert attempts[0]["state"] == "failed"
        assert attempts[0]["error_code"] == "provider_response_invalid"
        assert attempts[0]["tokens_in"] is None, (name, attempts[0])

    assert len(t041.operation_rows(f06_stack.dbname, run["id"])) == len(cases)
    for operation_id in failed_ids:
        assert t041.attempt_count(f06_stack.dbname, operation_id) == 1


def test_transient_provider_error_retries_once_as_a_second_attempt(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    response = t041.invoke(
        t041.worker_client(f06_stack), lease, operation_id=operation_id,
        params=t041.model_params(stub_transient_failures=1),
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded"
    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 2, attempts
    assert attempts[0]["attempt_no"] == 1
    assert attempts[0]["state"] == "failed"
    assert attempts[0]["error_code"] == "provider_transient"
    assert attempts[1]["attempt_no"] == 2
    assert attempts[1]["state"] == "succeeded"
    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "succeeded"
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 2


def test_invalid_params_manifest_allowlist_and_revocation_leave_no_ledger(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)

    too_many = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(),
        params=t041.model_params(max_output_tokens=999999),
    )
    assert too_many.status == 422, too_many.text
    assert too_many.json["error"]["code"] == "validation_failed"
    assert too_many.json["error"]["details"]["field"] == "params/max_output_tokens"

    unknown_key = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(),
        params=t041.model_params(temperature=0.2),
    )
    assert unknown_key.status == 422, unknown_key.text

    unknown_model = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), capability_version=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert unknown_model.status == 403, unknown_model.text
    assert unknown_model.json["error"]["code"] == "capability_not_allowed"

    wrong_kind = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), capability_version=t041.GOLDEN_TOOL_ID,
        capability_kind="model", params=t041.model_params(),
    )
    assert wrong_kind.status == 403, wrong_kind.text
    assert wrong_kind.json["error"]["code"] == "capability_not_allowed"

    t041.revoke_model(f06_stack.dbname)
    revoked = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), params=t041.model_params(),
    )
    assert revoked.status == 422, revoked.text
    assert revoked.json["error"]["code"] == "model_profile_unavailable"

    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []


def test_tool_grant_then_real_local_result_is_reported_and_ledgered(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)

    grant_response = t041.invoke(
        worker, lease, operation_id=operation_id, capability_kind="tool",
        capability_version=t041.GOLDEN_TOOL_ID, params={"city": "Berlin"},
    )
    assert grant_response.status == 200, grant_response.text
    granted = grant_response.json
    assert granted["state"] == "dispatching", granted
    assert granted["receipt_artifact_id"] is None
    grant = granted["data"]["grant"]
    assert grant["operation_id"] == operation_id
    assert grant["capability_kind"] == "tool"
    assert grant["risk_class"] == "pure"
    assert len(grant["params_sha256"]) == 64

    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "dispatching"
    assert operation["params_sha256"] == grant["params_sha256"]
    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 1 and attempts[0]["state"] == "dispatching", attempts

    # The worker executes the granted call for real, then reports the result.
    result = celsius_to_fahrenheit(**{"city": "Berlin", "celsius": 21.0})
    reported = t041.report_result(worker, lease, operation_id=operation_id, data=result)
    assert reported.status == 200, reported.text
    assert reported.json["state"] == "succeeded"
    receipt_id = reported.json["receipt_artifact_id"]
    assert receipt_id
    receipt = t041.receipt_document(f06_stack, receipt_id)
    assert receipt == result, receipt

    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "succeeded" and operation["receipt_artifact_id"] == receipt_id
    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 1
    assert attempts[0]["state"] == "succeeded"
    assert attempts[0]["result_artifact_id"] == receipt_id

    # Reporting the same execution again is an idempotent replay: no second row.
    replay = t041.report_result(worker, lease, operation_id=operation_id, data=result)
    assert replay.status == 200, replay.text
    assert replay.json["receipt_artifact_id"] == receipt_id
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 1

    items = t041.ledger_items(owner.creator, run["id"])
    assert len(items) == 1 and items[0]["attempts"] == 1, items


def test_tool_argument_schema_refusal_leaves_no_operation(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)
    response = t041.invoke(
        t041.worker_client(f06_stack), lease, operation_id=t041.new_uuid(),
        capability_kind="tool", capability_version=t041.GOLDEN_TOOL_ID,
        params={"city": 7},
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "validation_failed"
    assert response.json["error"]["details"]["field"].startswith("params/")
    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []


def test_stale_attempt_and_cancelled_run_are_refused(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)

    stale = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), attempt_token=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert stale.status == 403, stale.text
    assert stale.json["error"]["code"] == "stale_attempt"

    t041.cancel_run(f06_stack.dbname, run["id"])
    cancelled = t041.invoke(
        worker, lease, operation_id=t041.new_uuid(), params=t041.model_params(),
    )
    assert cancelled.status == 409, cancelled.text
    assert cancelled.json["error"]["code"] == "run_cancelled"
    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []


def test_supervisor_certificate_cannot_invoke_or_report(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    supervisor = t041.t029.execution_client(f06_stack, "supervisor")
    response = t041.invoke(
        supervisor, lease, operation_id=t041.new_uuid(), params=t041.model_params(),
    )
    assert response.status == 403, response.text
    assert response.json["error"]["code"] == "forbidden"

    reported = t041.report_result(
        supervisor, lease, operation_id=t041.new_uuid(), data={"ok": True},
    )
    assert reported.status == 403, reported.text


def test_tool_result_requires_a_dispatching_operation_of_this_run(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)

    missing = t041.report_result(
        worker, lease, operation_id=t041.new_uuid(), data={"ok": True},
    )
    assert missing.status == 404, missing.text
    assert missing.json["error"]["code"] == "operation_not_found"

    # A succeeded model operation never accepts a tool result report.
    model_operation = t041.new_uuid()
    model = t041.invoke(
        worker, lease, operation_id=model_operation, params=t041.model_params(),
    )
    assert model.status == 200, model.text
    wrong_kind = t041.report_result(
        worker, lease, operation_id=model_operation, data={"ok": True},
    )
    assert wrong_kind.status == 409, wrong_kind.text
    assert wrong_kind.json["error"]["code"] == "operation_not_dispatching"


def test_owner_only_ledger_read(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    response = t041.invoke(
        t041.worker_client(f06_stack), lease, operation_id=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert response.status == 200, response.text

    theirs = t041.ledger_items(owner.creator, run["id"])
    assert len(theirs) == 1

    other = t041.secondary_owner(f06_stack)
    denied = t041.ledger(other.creator, run["id"])
    assert denied.status == 404, denied.text
    assert denied.json["error"]["code"] == "run_not_found"
