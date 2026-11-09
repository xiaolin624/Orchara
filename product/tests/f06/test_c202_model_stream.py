"""C2.02 (#147): one model-attempt lifecycle for the streaming primary and fallback.

Every test is the real stack: a throwaway PostgreSQL database migrated through the
real ``cmd/admin migrate`` chain, the compiled API behind real HTTP, the real
worker mTLS listener, the real sealed execution manifest of a debug run and the
real filesystem object store. The providers are the documented dev stub and the
T122 loopback fake HTTP provider; the real vendor remains ``not_run``.

The ticket moves the streaming main call and its compatible fallback onto the
private model-attempt session/driver that C2.01 introduced for the synchronous
path. What is pinned here is the observable consequence, not the internal type:

* one dense physical-attempt sequence (``1..n``) with one independent generation
  (``platform.operation_attempts.id``) per attempt, and the same retry/fallback
  classification as the synchronous path;
* failed drafts never mix into the answer; only a complete streamed tool call can
  reach the final projection;
* a retry/fallback notice whose delivery fails stops further dispatch, and
  consumer stop / caller cancellation never retries or falls back;
* a final frame that cannot be delivered after the receipt was sealed does not
  revoke the committed success, and a complete answer that fails platform
  validation still settles its real usage;
* replay delivers the sealed final only, without a new provider request.
"""

from __future__ import annotations

import hashlib
import json
import socket
import time

import pytest

from support.runtime_stack import Stack
from support import f00b, t024, t025, t041, t044, t045, t046, t122
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr


def configure_primary(
    stack,
    owner,
    *,
    model_id: str = "stub-model",
    fallback_ids: list[str] | None = None,
    **overrides,
) -> str:
    """Point the golden primary at a model id and a compatible fallback list."""
    t045.set_profile(
        stack.dbname,
        t041.GOLDEN_MODEL_ID,
        model_id=model_id,
        config=t045.compatible_config(fallback_ids, **overrides),
    )
    return t041.GOLDEN_MODEL_ID


def test_an_adapter_class_stream_fault_moves_to_the_fallback_without_a_hole(f06_stack) -> None:
    """The streaming path and the synchronous path spend the same attempts.

    Before C2.02 the streaming loop always numbered its fallback attempt
    ``maxPhysicalAttempts + 1``, so an adapter-class primary fault (one physical
    attempt, no retries) left the dense attempt sequence with a hole: ``[1, 4]``.
    C2.01 fixed the synchronous path to ``[1, 2]``; the migrated streaming path
    must produce the same dense sequence, one generation per attempt.
    """
    owner = t041.owner(f06_stack)
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id, model_id="stub-model"
    )
    configure_primary(
        f06_stack, owner, model_id="stub-adapter-down-primary", fallback_ids=[fallback_id]
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)

    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=operation_id,
        params=t041.model_params(stub_stream_chunks=3),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "error", "delta", "delta", "delta", "final",
    ], frames
    notice = t044.errors(frames)[0]
    assert notice["error"]["code"] == "adapter_unavailable", notice
    assert notice["retryable"] is True, notice
    generations = t044.generation_ids(frames)
    assert len(generations) == 2, generations
    final = t044.finals(frames)[0]
    assert final["generation_id"] == generations[1]
    assert final["data"]["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_id,
        "reason": "adapter_unavailable",
    }, final["data"]

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2], attempts
    assert [row["state"] for row in attempts] == ["failed", "succeeded"], attempts
    assert [row["id"] for row in attempts] == generations, attempts
    assert attempts[0]["error_code"] == "adapter_unavailable", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert attempts[0]["charged_amount"] is None, attempts[0]
    assert attempts[1]["result_artifact_id"] == final["receipt_artifact_id"]

    operation = t045.operation_fallback(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "succeeded", operation
    assert operation["fallback_profile_version_id"] == fallback_id, operation
    assert operation["fallback_reason"] == "adapter_unavailable", operation
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 1
    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["attempts"] == 2, item
    assert item["fallback_reason"] == "adapter_unavailable", item


# ---------------------------------------------------------------------------
# the T122 loopback provider stack, local to this ticket
# ---------------------------------------------------------------------------
@pytest.fixture
def c202_real_stack(tmp_path, fresh_database, go_binaries, api_server):
    """The T041 stack pointed at a real loopback chat-completions provider.

    It mirrors the T122 fixture so this ticket can drive attempt timing (stalled
    streams, half streams, a consumer that stops) through the real adapter wire
    protocol without depending on another test module's fixture.
    """
    provider = t122.FakeProvider()
    secret = t122.new_secret()
    deploy_secret = t122.new_secret()
    try:
        dsn, dbname = fresh_database
        st.migrate_or_fail(go_binaries["admin"], dsn)
        st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
        object_dir = tmp_path / "objects"
        object_dir.mkdir()
        internal_addr, _port = unique_internal_addr()
        certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
        signing_key = t024.new_signing_key()
        env = t024.api_extra_env(
            object_dir,
            certificates,
            signing_key=signing_key,
            extra=t025.api_extra_env(None),
        )
        env.update(t122.catalog_env())
        env["ORCHARA_MODEL_ENDPOINT"] = provider.url + "/v1"
        env[t122.SECRET_ENV] = secret
        env["ORCHARA_MODEL_SECRET_REF"] = "env:" + t122.DEPLOY_SECRET_ENV
        env[t122.DEPLOY_SECRET_ENV] = deploy_secret
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
        yield t122.T122Stack(
            stack=stack, provider=provider, secret=secret, deploy_secret=deploy_secret,
        )
    finally:
        provider.close()


def real_run(harness: t122.T122Stack, *, run_owner=None, fallback_ids: list[str] | None = None):
    """Seed the owner, register the fake real provider and claim a run."""
    if run_owner is None:
        run_owner = t041.owner(harness.stack)
    config = t122.model_config()
    if fallback_ids:
        config["fallback_profile_version_ids"] = list(fallback_ids)
    t122.register_model(harness.dbname, config_value=config)
    _, run, lease = t041.claimed_run(harness.stack, run_owner=run_owner)
    return run_owner, run, lease


def seed_real_fallback(harness: t122.T122Stack, owner, *, model_id: str = t122.MODEL_ID) -> str:
    fallback_id = t045.seed_profile(
        harness.dbname, owner.creator.principal_id, model_id=model_id
    )
    t122.register_model(harness.dbname, model_id=model_id, model_profile_id=fallback_id)
    return fallback_id


def raw_stream(harness: t122.T122Stack, lease: dict, *, operation_id: str, params: dict | None = None):
    body = {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": "stream-1",
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": params if params is not None else t041.model_params(),
        "capability_kind": "model",
    }
    return t044.open_raw_stream(t041.worker_client(harness.stack), lease["run_id"], body)


def read_until(connection, received: bytes, marker: bytes, *, timeout: float = 10.0) -> bytes:
    """Read more mTLS bytes until marker appears (or the stream ends/timeouts)."""
    deadline = time.time() + timeout
    connection.settimeout(max(0.1, timeout))
    while marker not in received and time.time() < deadline:
        try:
            chunk = connection.recv(65536)
        except (socket.timeout, OSError):
            break
        if not chunk:
            break
        received += chunk
    return received


def read_delta_events(connection, received: bytes, count: int, *, timeout: float = 30.0) -> bytes:
    """Read until count complete delta events have arrived, then stop reading."""
    deadline = time.time() + timeout
    connection.settimeout(max(0.1, timeout))
    while received.count(b"\n\n") < count and time.time() < deadline:
        try:
            chunk = connection.recv(1 << 20)
        except (socket.timeout, OSError):
            break
        if not chunk:
            break
        received += chunk
    return received


def wait_operation_stopped(dbname: str, run_id: str, operation_id: str, *, timeout: float = 15.0):
    operation = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        operation = t041.operation_row(dbname, run_id, operation_id)
        if operation and operation["state"] in {"failed", "succeeded"}:
            return operation
        time.sleep(0.05)
    return operation


def half_stream(frames: list[dict], *, stall: float = 0.8):
    """A real SSE responder: write frames, wait, then close without [DONE]."""

    def responder(_record, handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        for frame in frames:
            try:
                handler.wfile.write(f"data: {json.dumps(frame)}\n\n".encode())
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return None
        time.sleep(stall)
        handler.close_connection = True
        try:
            handler.connection.close()
        except OSError:
            pass
        return None

    return responder


def stub_usage(params: dict) -> tuple[int, int]:
    """The dev stub's documented usage derivation, computed independently."""
    canonical = json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    tokens_in = (len(canonical) + 3) // 4
    text = "stub-agent:" + hashlib.sha256(canonical).hexdigest()[:16]
    tokens_out = (len(text) + 3) // 4
    return tokens_in, tokens_out


# ---------------------------------------------------------------------------
# AC2: independent generations; a failed draft never contributes content
# ---------------------------------------------------------------------------
def test_retry_keeps_only_the_successful_generations_tool_calls(c202_real_stack) -> None:
    """A tool call buffered by the failed draft never reaches final validation."""
    owner, run, lease = real_run(c202_real_stack)
    t041.set_tool_schema(c202_real_stack.dbname, t041.CITY_SCHEMA)
    provider = c202_real_stack.provider
    stale = t122.stream_frame(tool_calls=[{
        "index": 0, "id": "call-old", "type": "function",
        "function": {"name": "lookup", "arguments": '{"city":"Rome"}'},
    }])
    fresh = t122.stream_frame(tool_calls=[{
        "index": 0, "id": "call-new", "type": "function",
        "function": {"name": "lookup", "arguments": '{"city":"Paris"}'},
    }])
    provider.respond_sequence([
        provider.stream_responder([stale], abort_after=1),
        provider.stream_responder([
            fresh,
            t122.stream_frame(finish_reason="stop"),
            t122.stream_frame(usage=(9, 4)),
        ]),
    ])
    tools = [{"name": "lookup", "capability_version": t041.GOLDEN_TOOL_ID}]
    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(c202_real_stack.stack), lease, operation_id=operation_id,
        params=t041.model_params(tools=tools),
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "tool_delta", "error", "tool_delta", "final",
    ], frames
    generations = t044.generation_ids(frames)
    assert len(generations) == 2, generations
    assert frames[1]["error"]["code"] == "provider_transient", frames[1]
    assert frames[1]["retryable"] is True, frames[1]
    final = t044.finals(frames)[0]
    assert final["generation_id"] == generations[1]
    assert final["data"]["tool_calls"] == [
        {"id": "call-new", "name": "lookup", "arguments": {"city": "Paris"}},
    ], final["data"]
    document = t041.receipt_document(c202_real_stack.stack, final["receipt_artifact_id"])
    assert document["tool_calls"] == final["data"]["tool_calls"]

    attempts = t041.attempt_rows(c202_real_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2], attempts
    assert [row["state"] for row in attempts] == ["failed", "succeeded"], attempts
    assert [row["id"] for row in attempts] == generations, attempts
    assert attempts[0]["error_code"] == "provider_transient", attempts[0]
    assert attempts[0]["result_artifact_id"] is None, attempts[0]
    assert attempts[1]["result_artifact_id"] == final["receipt_artifact_id"]
    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["tool_call_summary"] == [{"id": "call-new", "name": "lookup"}], item
    assert len(provider.requests()) == 2, provider.requests()


# ---------------------------------------------------------------------------
# AC3: a consumer stop is a cancellation - no retry, no fallback
# ---------------------------------------------------------------------------
def test_consumer_stop_during_the_stream_never_retries(c202_real_stack) -> None:
    _, run, lease = real_run(c202_real_stack)
    provider = c202_real_stack.provider
    provider.respond_sequence([
        half_stream([t122.stream_frame(content="partial")], stall=2.0),
    ])
    operation_id = t041.new_uuid()
    connection, received = raw_stream(c202_real_stack, lease, operation_id=operation_id)
    try:
        assert b"event: delta" in received, received
    finally:
        t044.abort_stream(connection)

    attempts = t044.wait_attempt_closed(c202_real_stack.dbname, operation_id)
    operation = wait_operation_stopped(c202_real_stack.dbname, run["id"], operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts[0]
    assert attempts[0]["error_code"] == "stream_cancelled", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert operation["receipt_artifact_id"] is None, operation
    assert t044.receipt_artifact_count(c202_real_stack.dbname, run["id"]) == 0
    assert len(provider.requests()) == 1, provider.requests()


def test_consumer_stop_during_backoff_does_not_start_the_next_attempt(c202_real_stack) -> None:
    """The retry notice was delivered; the caller vanished during the pause."""
    _, run, lease = real_run(c202_real_stack)
    provider = c202_real_stack.provider
    provider.respond_sequence([t122.status_responder(500)])
    operation_id = t041.new_uuid()
    connection, received = raw_stream(c202_real_stack, lease, operation_id=operation_id)
    try:
        received = read_until(connection, received, b"event: error")
        assert b"event: error" in received, received
    finally:
        t044.abort_stream(connection)

    attempts = t044.wait_attempt_closed(c202_real_stack.dbname, operation_id)
    operation = wait_operation_stopped(c202_real_stack.dbname, run["id"], operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts[0]
    assert attempts[0]["error_code"] == "provider_transient", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert t044.receipt_artifact_count(c202_real_stack.dbname, run["id"]) == 0
    assert len(provider.requests()) == 1, provider.requests()


# ---------------------------------------------------------------------------
# AC4: sealed success survives an undeliverable final frame
# ---------------------------------------------------------------------------
def test_final_frame_failure_after_the_receipt_is_sealed_keeps_the_success(c202_real_stack) -> None:
    """The consumer never reads the final; the sealed success must stand.

    The answer is streamed as many sub-1 MiB deltas, then the consumer stops
    reading before the final frame (which repeats the full multi-megabyte text
    and therefore cannot fit the socket buffers). The receipt is already
    committed when the final delivery fails; the success must not be revoked.
    """
    owner, run, lease = real_run(c202_real_stack)
    provider = c202_real_stack.provider
    chunk = "y" * 700_000
    delta_count = 8
    frames = [t122.stream_frame(content=chunk) for _ in range(delta_count)]
    frames.append(t122.stream_frame(finish_reason="stop"))
    frames.append(t122.stream_frame(usage=(9, 4)))
    provider.respond_stream(frames)
    operation_id = t041.new_uuid()
    connection, received = raw_stream(c202_real_stack, lease, operation_id=operation_id)
    try:
        received = read_delta_events(connection, received, delta_count)
        assert received.count(b"event: delta") == delta_count, received[-2000:]
        assert b"event: final" not in received, received[-2000:]
        # Stop reading: the final frame cannot be delivered to this consumer.
        operation = wait_operation_stopped(
            c202_real_stack.dbname, run["id"], operation_id, timeout=20.0
        )
        assert operation is not None, "the streamed operation never settled"
        assert operation["state"] == "succeeded", operation
    finally:
        t044.abort_stream(connection)
        time.sleep(0.2)

    attempts = t041.attempt_rows(c202_real_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "succeeded", attempts[0]
    assert attempts[0]["result_artifact_id"] == operation["receipt_artifact_id"]
    assert operation["last_error_code"] is None, operation
    document = t041.receipt_document(c202_real_stack.stack, operation["receipt_artifact_id"])
    assert document["text"] == chunk * delta_count
    assert document["usage"] == {"input_tokens": 9, "output_tokens": 4}
    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["state"] == "succeeded", item


# ---------------------------------------------------------------------------
# AC2/AC4: complete-response consumption follows the actual candidate
# ---------------------------------------------------------------------------
def test_a_streamed_fallback_answer_that_fails_validation_settles_its_usage(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    fallback_config = t045.compatible_config()
    fallback_config["price"] = {
        "currency": "USD",
        "input_per_million": "10",
        "output_per_million": "20",
    }
    fallback_id = t045.seed_profile(
        f06_stack.dbname, owner.creator.principal_id,
        model_id="stub-model", config=fallback_config,
    )
    configure_primary(
        f06_stack, owner, model_id="stub-unavailable-primary", fallback_ids=[fallback_id]
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params(
        response_schema={
            "type": "object",
            "required": ["count"],
            "properties": {"count": {"type": "banana"}},
        }
    )
    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=operation_id, params=params,
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == [
        "error", "error", "error", "delta", "delta", "delta", "error",
    ], frames
    for notice in t044.errors(frames)[:3]:
        assert notice["retryable"] is True, notice
        assert notice["error"]["code"] == "provider_transient", notice
    terminal = frames[-1]
    assert terminal["error"]["code"] == "provider_response_invalid", terminal
    assert terminal.get("retryable") is not True, terminal

    attempts = t041.attempt_rows(f06_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2, 3, 4], attempts
    assert [row["state"] for row in attempts] == ["failed"] * 4, attempts
    for failed in attempts[:3]:
        assert failed["settlement_state"] == "released", failed
        assert failed["charged_amount"] is None, failed
    refused = attempts[3]
    assert refused["settlement_state"] == "settled", refused
    tokens_in, tokens_out = stub_usage(params)
    charge = t046.expected_charge(
        tokens_in, tokens_out, input_per_million="10", output_per_million="20"
    )
    assert int(refused["tokens_in"]) == tokens_in, refused
    assert int(refused["tokens_out"]) == tokens_out, refused
    assert str(refused["charged_amount"]) == str(charge), refused
    assert refused["price_version"] == f"price-v1:{fallback_id}:in=10:out=20:USD", refused

    operation = t041.operation_row(f06_stack.dbname, run["id"], operation_id)
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "provider_response_invalid", operation
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 0
    charged = [row for row in t046.ledger_rows(f06_stack.dbname, run["id"]) if row["kind"] == "charge"]
    assert len(charged) == 1, charged
    assert str(charged[0]["amount"]) == str(charge), charged


def test_a_streamed_answer_without_usage_keeps_the_reservation(f06_stack) -> None:
    owner = t041.owner(f06_stack)
    t046.configure_golden(
        f06_stack.dbname, model_id="stub-usage-missing", config=t045.compatible_config(
            price={"currency": "USD", "input_per_million": "1", "output_per_million": "2"}
        ),
    )
    owner, run, lease = t041.claimed_run(f06_stack, run_owner=owner)
    params = t041.model_params()
    operation_id = t041.new_uuid()
    response = t044.stream(
        t041.worker_client(f06_stack), lease, operation_id=operation_id, params=params,
    )
    assert response.status == 200, response.text
    frames = t044.parse_sse(response.text)
    assert [frame["type"] for frame in frames] == ["delta", "delta", "delta", "final"], frames
    final = t044.finals(frames)[0]
    assert "usage" not in final["data"], final["data"]
    assert final["data"]["text"].startswith("stub-agent:"), final["data"]

    attempt = t046.attempt_row(f06_stack.dbname, operation_id, 1)
    assert attempt["state"] == "succeeded", attempt
    assert attempt["settlement_state"] == "usage_missing", attempt
    assert attempt["charged_amount"] is None, attempt
    assert int(attempt["tokens_in"]) == 0 and int(attempt["tokens_out"]) == 0, attempt
    rows = t046.ledger_rows(f06_stack.dbname, run["id"])
    assert [row["kind"] for row in rows] == ["reserve"], rows
    facts = t046.budget_facts(owner, run["id"])
    assert int(facts["charged_amount"]) == 0 or float(facts["charged_amount"]) == 0.0, facts
    assert float(facts["reserved_amount"]) > 0, facts


# ---------------------------------------------------------------------------
# AC5: replay is the sealed final, never another provider request
# ---------------------------------------------------------------------------
def test_replaying_a_succeeded_stream_delivers_only_the_sealed_final(f06_stack) -> None:
    owner, run, lease = t041.claimed_run(f06_stack)
    worker = t041.worker_client(f06_stack)
    operation_id = t041.new_uuid()
    params = t041.model_params(stub_stream_chunks=3)
    first = t044.stream(worker, lease, operation_id=operation_id, params=params)
    assert first.status == 200, first.text
    first_frames = t044.parse_sse(first.text)
    final = t044.finals(first_frames)[0]
    assert [frame["type"] for frame in first_frames] == ["delta", "delta", "delta", "final"]

    replay = t044.stream(worker, lease, operation_id=operation_id, params=params)
    assert replay.status == 200, replay.text
    replay_frames = t044.parse_sse(replay.text)
    assert [frame["type"] for frame in replay_frames] == ["final"], replay_frames
    replayed = replay_frames[0]
    assert replayed["generation_id"] == final["generation_id"], replay_frames
    assert replayed["receipt_artifact_id"] == final["receipt_artifact_id"], replay_frames
    assert replayed["data"] == final["data"], replay_frames
    assert t041.attempt_count(f06_stack.dbname, operation_id) == 1
    assert t044.receipt_artifact_count(f06_stack.dbname, run["id"]) == 1
    item = t041.ledger_items(owner.creator, run["id"])[0]
    assert item["attempts"] == 1, item

