"""F06/T044 acceptance: streaming generations, interrupted streams and incomplete
tool-call refusal, on the real stack.

Every assertion here is observed through a real surface: the contract
``streamModelCall`` SSE over a real worker mTLS certificate, real
PostgreSQL operations/attempts/receipts, real sealed object bytes, and the real
owner preview over public HTTP. The provider is the documented dev stub, labelled
stub everywhere; no vendor is contacted (no vendor adapter exists in this build).
"""

from __future__ import annotations

import threading
import time

from support import t029, t041, t044
from support import stack as st


def test_stream_frames_are_generation_scoped_and_the_receipt_is_sealed_once(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    events_before = t044.run_events_count(f06_stack.dbname, run["id"])

    response = t044.stream(
        worker, lease, operation_id=operation_id,
        params=t041.model_params(stub_stream_chunks=3),
    )
    assert response.status == 200, response.text
    assert "text/event-stream" in (response.header("Content-Type") or ""), response.header("Content-Type")
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == ["delta", "delta", "delta", "final"], frames
    assert t044.operation_ids(frames) == {operation_id}
    generations = t044.generation_ids(frames)
    assert len(generations) == 1, generations
    generation = generations[0]

    final = t044.finals(frames)[0]
    assert final["generation_id"] == generation
    assert final["data"]["stub"] is True
    assert final["data"]["text"].startswith("stub-agent:")
    assert final["data"]["tool_calls"] == []
    assert final["data"]["model_profile_version_id"] == t041.GOLDEN_MODEL_ID
    assert final["data"]["usage"]["input_tokens"] > 0
    assert final["usage"] == final["data"]["usage"]
    assert t044.delta_text(frames, generation) == final["data"]["text"]

    operations = t041.operation_rows(f06_stack.dbname, run["id"])
    assert len(operations) == 1, operations
    operation = operations[0]
    assert operation["id"] == operation_id and operation["state"] == "succeeded"
    assert operation["receipt_artifact_id"] == final["receipt_artifact_id"]
    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["id"] == generation
    assert attempts[0]["state"] == "succeeded"
    assert attempts[0]["result_artifact_id"] == final["receipt_artifact_id"]
    assert attempts[0]["attempt_token"] == lease["attempt_token"]

    receipt = t041.artifact_row(f06_stack.dbname, final["receipt_artifact_id"])
    assert receipt["purpose"] == "receipt" and receipt["state"] == "ready"
    document = t041.receipt_document(f06_stack, final["receipt_artifact_id"])
    assert document["stub"] is True and document["text"] == final["data"]["text"]

    # Streaming is temporary: it must never be written as a durable run event.
    assert t044.run_events_count(f06_stack.dbname, run["id"]) == events_before
    ledger = t041.ledger_items(owner.creator, run["id"])
    assert len(ledger) == 1 and ledger[0]["attempts"] == 1 and ledger[0]["stub"] is True


def test_mid_stream_failure_starts_a_new_generation_and_never_concatenates(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)

    response = t044.stream(
        worker, lease, operation_id=operation_id,
        params=t041.model_params(stub_stream_chunks=3, stub_stream_fail_after=1),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "delta", "error", "delta", "delta", "delta", "final",
    ], frames
    assert t044.operation_ids(frames) == {operation_id}
    generations = t044.generation_ids(frames)
    assert len(generations) == 2, generations
    first, second = generations
    assert frames[1]["generation_id"] == first
    assert frames[1]["error"]["code"] == "provider_transient"
    assert frames[1]["retryable"] is True
    final = t044.finals(frames)[0]
    assert final["generation_id"] == second

    second_text = t044.delta_text(frames, second)
    first_text = t044.delta_text(frames, first)
    assert first_text == second_text[: len(first_text)], (first_text, second_text)
    assert final["data"]["text"] == second_text
    assert final["data"]["text"] != first_text + second_text
    assert final["data"]["tool_calls"] == []

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert len(attempts) == 2, attempts
    assert [row["state"] for row in attempts] == ["failed", "succeeded"], attempts
    assert attempts[0]["error_code"] == "provider_transient", attempts[0]
    assert attempts[0]["id"] == first and attempts[1]["id"] == second
    assert attempts[0]["result_artifact_id"] is None
    assert attempts[1]["result_artifact_id"] == final["receipt_artifact_id"]
    assert attempts[0]["finished_at"] and attempts[1]["finished_at"]

    operations = t041.operation_rows(f06_stack.dbname, run["id"])
    assert len(operations) == 1 and operations[0]["state"] == "succeeded"
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 1
    document = t041.receipt_document(f06_stack, final["receipt_artifact_id"])
    assert document["text"] == second_text


def test_incomplete_tool_call_is_dropped_and_never_executed(f06_stack) -> None:
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    tools = [{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}]

    response = t044.stream(
        worker, lease, operation_id=operation_id,
        params=t041.model_params(
            tools=tools,
            stub_stream_chunks=2,
            stub_stream_incomplete_tool_call=True,
        ),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    tool_frames = [frame for frame in frames if frame["type"] == "tool_delta"]
    assert len(tool_frames) == 1, frames
    assert tool_frames[0]["tool_call"]["name"] == "lookup"
    assert tool_frames[0]["tool_call"]["id"] == "stub-call-1"
    # The argument fragment is not a complete JSON object: pending in the UI,
    # never a validated call in the final projection.
    assert tool_frames[0]["tool_call"]["arguments_fragment"] != "{}"
    final = t044.finals(frames)[0]
    assert final["data"]["tool_calls"] == [], final["data"]
    document = t041.receipt_document(f06_stack, final["receipt_artifact_id"])
    assert document["tool_calls"] == []
    ledger = t041.ledger_items(owner.creator, run["id"])
    assert ledger[0]["tool_call_summary"] == [], ledger
    # No tool capability operation was executed or even granted.
    assert len(t041.operation_rows(f06_stack.dbname, run["id"])) == 1


def test_complete_streamed_tool_call_is_validated_before_the_final(f06_stack) -> None:
    t041.set_tool_schema(f06_stack.dbname, t041.CITY_SCHEMA)
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    tools = [{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}]

    response = t044.stream(
        worker, lease, operation_id=operation_id,
        params=t041.model_params(
            tools=tools,
            stub_tool_calls=[{"id": "call-1", "name": "lookup", "arguments": {"city": "Paris"}}],
        ),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert len([frame for frame in frames if frame["type"] == "tool_delta"]) == 1, frames
    final = t044.finals(frames)[0]
    assert final["data"]["tool_calls"] == [
        {"id": "call-1", "name": "lookup", "arguments": {"city": "Paris"}},
    ], final["data"]
    document = t041.receipt_document(f06_stack, final["receipt_artifact_id"])
    assert document["tool_calls"] == final["data"]["tool_calls"]
    ledger = t041.ledger_items(owner.creator, run["id"])
    assert ledger[0]["tool_call_summary"] == [{"id": "call-1", "name": "lookup"}], ledger


def test_stale_token_non_worker_and_tool_kind_are_refused_without_a_stream(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)
    operation_id = t041.new_uuid()

    stale = t044.stream(
        worker, lease, operation_id=operation_id, attempt_token=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert stale.status == 403, stale.text
    assert stale.json["error"]["code"] == "stale_attempt", stale.text
    assert "text/event-stream" not in (stale.header("Content-Type") or "")

    supervisor = t029.execution_client(f06_stack, "supervisor")
    forbidden = t044.stream(
        supervisor, lease, operation_id=operation_id, params=t041.model_params(),
    )
    assert forbidden.status == 403, forbidden.text
    assert forbidden.json["error"]["code"] == "forbidden", forbidden.text

    tool_kind = t044.stream(
        worker, lease, operation_id=t041.new_uuid(), capability_kind="tool",
        capability_version=t041.GOLDEN_TOOL_ID,
        params={"city": "Paris"},
    )
    assert tool_kind.status == 422, tool_kind.text
    assert tool_kind.json["error"]["code"] == "capability_kind_unsupported", tool_kind.text
    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []


def test_cancelled_run_cannot_stream_a_model(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    t041.cancel_run(f06_stack.dbname, run["id"])
    response = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "run_cancelled", response.text
    assert t041.operation_rows(f06_stack.dbname, run["id"]) == []


def test_client_disconnect_stops_the_stream_without_success(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    body = {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": "disconnect-1",
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": t041.model_params(stub_stream_chunks=12, stub_stream_delay_ms=200),
        "capability_kind": "model",
    }
    connection, received = t044.open_raw_stream(worker, run["id"], body)
    try:
        assert b"event: delta" in received, received
    finally:
        t044.abort_stream(connection)

    attempts = t044.wait_attempt_closed(f06_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "stream_cancelled", attempts
    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert operation["receipt_artifact_id"] is None
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 0


def test_owner_preview_replays_only_the_current_generation_then_closes(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    streamed = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=operation_id,
        params=t041.model_params(stub_stream_chunks=3, stub_stream_fail_after=1),
    )
    assert streamed.status == 200, streamed.text
    model_frames = t044.parse_sse(streamed.text)
    second = t044.generation_ids(model_frames)[1]

    response = t044.preview(owner.creator, run["id"])
    assert response.status == 200, response.text
    assert "text/event-stream" in (response.header("Content-Type") or "")
    preview_frames = t044.parse_sse(response.text)
    assert preview_frames, "preview returned no frames"
    assert t044.generation_ids(preview_frames) == [second], preview_frames
    assert [frame["type"] for frame in preview_frames] == ["delta", "delta", "delta", "final"]
    assert t044.finals(preview_frames)[0]["data"] == t044.finals(model_frames)[0]["data"]
    assert t044.finals(preview_frames)[0]["receipt_artifact_id"] == t044.finals(model_frames)[0]["receipt_artifact_id"]

    other = t041.secondary_owner(f06_stack)
    hidden = t044.preview(other.creator, run["id"])
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found", hidden.text


def test_owner_preview_streams_live_frames_and_closes_on_final(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    watcher = t044.PreviewThread(owner.creator, run["id"])
    watcher.start()
    time.sleep(0.3)
    try:
        streamed = t044.stream(
            t041.worker_client(f06_stack), lease, operation_id=operation_id,
            params=t041.model_params(stub_stream_chunks=3, stub_stream_delay_ms=150),
        )
    finally:
        response = watcher.result(timeout=30)
    assert streamed.status == 200, streamed.text
    assert response.status == 200, response.text
    preview_frames = t044.parse_sse(response.text)
    model_frames = t044.parse_sse(streamed.text)
    assert [frame["type"] for frame in preview_frames] == [
        "delta", "delta", "delta", "final",
    ], preview_frames
    assert [frame["generation_id"] for frame in preview_frames] == [
        frame["generation_id"] for frame in model_frames
    ]
    assert t044.finals(preview_frames)[0]["data"] == t044.finals(model_frames)[0]["data"]


def test_replaying_a_failed_stream_is_an_error_frame_not_a_fake_final(f06_stack) -> None:
    _, run, lease = t041.claimed_run(f06_stack)
    operation_id = t041.new_uuid()
    worker = t041.worker_client(f06_stack)
    body = {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": "exhausted-1",
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": t041.model_params(
            stub_stream_chunks=2,
            stub_stream_fail_after=0,
            stub_stream_fail_attempts=3,
        ),
        "capability_kind": "model",
    }
    first = t044.stream(worker, lease, operation_id=operation_id, body=body)
    assert first.status == 200, first.text
    first_frames = t044.parse_sse(first.text)
    assert first_frames[-1]["type"] == "error", first_frames
    assert first_frames[-1].get("retryable") is not True
    assert t041.operation_row(f06_stack.dbname, run["id"], operation_id)["state"] == "failed"

    # Same operation/logical key/params: the idempotent replay must keep failing
    # honestly instead of inventing a final or a second physical attempt.
    replay = t044.stream(worker, lease, operation_id=operation_id, body=body)
    assert replay.status == 200, replay.text
    replay_frames = t044.parse_sse(replay.text)
    assert [frame["type"] for frame in replay_frames] == ["error"], replay_frames
    assert replay_frames[0]["error"]["code"] == "provider_transient", replay_frames
    assert t044.finals(replay_frames) == []
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 3


def test_preview_for_a_terminal_run_without_a_stream_is_not_retained(f06_stack) -> None:
    owner, run, _lease = t041.claimed_run(f06_stack)
    st.sql(
        f06_stack.dbname,
        "UPDATE platform.runs SET state = 'succeeded' WHERE "
        f"id = {st.quote_literal(run['id'])}::uuid",
    )
    response = t044.preview(owner.creator, run["id"])
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found", response.text
