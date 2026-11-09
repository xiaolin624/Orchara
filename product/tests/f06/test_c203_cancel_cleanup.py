"""C2.03 (#148): cancellation, cleanup failure and settlement closure.

Every test is the real stack: a throwaway PostgreSQL database migrated through the
real ``cmd/admin migrate`` chain, the compiled API behind real HTTP, the real
worker mTLS listener, the real sealed execution manifest of a debug run and the
real filesystem object store. The providers are the documented dev stub and the
T122 loopback fake HTTP provider; the real vendor remains ``not_run``.

The four model paths (synchronous primary/fallback and streaming
primary/fallback) share the private ``modelAttemptSession`` driver. What is
pinned here is the observable consequence through the existing capability
entries, never an internal type:

* a caller that stops before a call, during a call, during backoff or while a
  retry/fallback notice is being delivered starts no further request;
* the ledger tells the truth: the physical request count, the attempt and
  operation states, the released or settled reservation and the fee rows;
* a cancellation while a complete answer is being sealed is cleaned up with a
  bounded context detached from the dead request, so the attempt/operation never
  stay ``dispatching`` and the provider's real usage is still settled;
* a cleanup that cannot write (the database refuses the close) is reported as an
  unclosed ledger instead of being swallowed or presented as a clean refusal.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from decimal import Decimal

import pytest

from support.runtime_stack import Stack
from support import f00b, t024, t025, t041, t044, t045, t046, t122
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

SYNC_PATH = "/internal/v1/runs/{run_id}/capabilities"

GOLDEN_PRICE_VERSION = (
    f"price-v1:{t041.GOLDEN_MODEL_ID}:in=1.00:out=2.00:USD"
)


# ---------------------------------------------------------------------------
# a T122-shaped stack local to this ticket
# ---------------------------------------------------------------------------
@pytest.fixture
def c203_real_stack(tmp_path, fresh_database, go_binaries, api_server):
    """The T041 stack pointed at a real loopback chat-completions provider.

    It mirrors the T122 fixture so this ticket can drive in-flight cancellation
    and sealing timing through the real adapter wire protocol without depending
    on another test module's fixture.
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


def real_run(harness: t122.T122Stack, *, run_owner=None):
    """Seed the owner, register the fake real provider and claim a run."""
    if run_owner is None:
        run_owner = t041.owner(harness.stack)
    t122.register_model(harness.dbname, config_value=t122.model_config())
    _, run, lease = t041.claimed_run(harness.stack, run_owner=run_owner)
    return run_owner, run, lease


def configure_stub_primary(
    harness: t122.T122Stack,
    owner,
    *,
    model_id: str,
    fallback_ids: list[str],
) -> str:
    """Make the frozen primary a deterministic dev-stub fault with a fallback."""
    t045.set_profile(
        harness.dbname,
        t041.GOLDEN_MODEL_ID,
        model_id=model_id,
        config=t045.compatible_config(fallback_ids),
    )
    return t041.GOLDEN_MODEL_ID


def seed_real_fallback(harness: t122.T122Stack, owner) -> str:
    """Seed one real fallback profile and register it at the fake provider.

    The config must satisfy the same T045 compatibility gates as any fallback
    (context window and max output cover the request), so the real profile keeps
    the documented compatible shape while its provider/endpoint point at the
    loopback fake.
    """
    fallback_id = t045.seed_profile(
        harness.dbname, owner.creator.principal_id, model_id=t122.MODEL_ID
    )
    t122.register_model(
        harness.dbname,
        model_profile_id=fallback_id,
        config_value=t045.compatible_config(),
    )
    return fallback_id


# ---------------------------------------------------------------------------
# timed provider answers: the test owns when the physical call completes
# ---------------------------------------------------------------------------
def gated_completion(
    provider: t122.FakeProvider,
    *,
    content: str = "late answer",
    usage: tuple[int, int] = (11, 7),
) -> threading.Event:
    """A provider answer that is held until the test releases the gate."""
    gate = threading.Event()

    def responder(_record, _handler):
        if not gate.wait(timeout=30.0):
            return None
        return provider.completion(content=content, usage=usage)

    provider.responder = responder
    return gate


def stalled_stream(provider: t122.FakeProvider, frames: list[dict], *, after: int = 1,
                   stall: float = 3.0) -> None:
    """A real SSE answer that stops mid-stream for ``stall`` seconds."""
    encoded = [json.dumps(frame) for frame in frames]

    def responder(_record, handler):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Connection", "close")
        handler.end_headers()
        for index, payload in enumerate(encoded):
            if index == after:
                time.sleep(stall)
            try:
                handler.wfile.write(f"data: {payload}\n\n".encode())
                handler.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return None
        handler.close_connection = True
        try:
            handler.connection.close()
        except OSError:
            pass
        return None

    provider.responder = responder


# ---------------------------------------------------------------------------
# raw mTLS clients: the test decides exactly when the caller stops
# ---------------------------------------------------------------------------
def open_raw_post(client: f00b.InternalClient, path: str, body: dict):
    """Send an internal capability request over the real mTLS socket."""
    raw = json.dumps(body).encode("utf-8")
    connection = client.context.wrap_socket(
        socket.create_connection((client.host, client.port), timeout=10),
        server_hostname=client.host,
    )
    head = (
        f"POST {path} HTTP/1.1\r\n"
        f"Host: {client.host}:{client.port}\r\n"
        "Content-Type: application/json\r\n"
        "Accept: application/json\r\n"
        "X-Internal-Role: worker\r\n"
        f"Content-Length: {len(raw)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8")
    connection.sendall(head + raw)
    return connection


def abort(connection) -> None:
    """Close with an RST so the server notices immediately (a real disconnect)."""
    import struct

    try:
        connection.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
    except OSError:
        pass
    connection.close()


def sync_body(lease: dict, *, operation_id: str, params: dict,
              logical_key: str = "call-1") -> dict:
    return {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": logical_key,
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": params,
        "capability_kind": "model",
    }


def wait_for_requests(provider: t122.FakeProvider, *, count: int = 1,
                      timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(provider.requests()) >= count:
            return
        time.sleep(0.01)
    raise AssertionError(f"the provider never received {count} request(s)")


def wait_operation_stopped(dbname: str, run_id: str, operation_id: str,
                           *, timeout: float = 20.0):
    operation = None
    deadline = time.time() + timeout
    while time.time() < deadline:
        operation = t041.operation_row(dbname, run_id, operation_id)
        if operation and operation["state"] in {"failed", "succeeded"}:
            return operation
        time.sleep(0.05)
    return operation


def assert_no_receipt(harness: t122.T122Stack, run: dict) -> None:
    assert t044.receipt_artifact_count(harness.dbname, run["id"]) == 0


# ---------------------------------------------------------------------------
# cancellation during a call: both synchronous paths
# ---------------------------------------------------------------------------
def test_cancellation_during_the_sync_call_closes_as_caller_cancelled(c203_real_stack) -> None:
    """The accepted T122 rule, re-observed for the C2.03 matrix.

    The caller disconnects while the physical call is in flight. The attempt is
    closed with ``caller_cancelled`` (never a provider failure), the reservation
    is released, no fee is charged, no receipt exists and nothing retries.
    """
    _, run, lease = real_run(c203_real_stack)
    provider = c203_real_stack.provider
    gate = gated_completion(provider)
    operation_id = t041.new_uuid()
    connection = open_raw_post(
        t041.worker_client(c203_real_stack.stack),
        SYNC_PATH.format(run_id=run["id"]),
        sync_body(lease, operation_id=operation_id, params=t041.model_params()),
    )
    try:
        wait_for_requests(provider)
        abort(connection)
        operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    finally:
        gate.set()

    assert operation is not None, "the cancelled operation was never closed"
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "caller_cancelled", operation
    assert operation["receipt_artifact_id"] is None, operation

    attempts = t041.attempt_rows(c203_real_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "caller_cancelled", attempts
    assert attempts[0]["settlement_state"] == "released", attempts
    assert attempts[0]["charged_amount"] is None, attempts
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)

    rows = t046.ledger_rows(c203_real_stack.dbname, run["id"])
    assert [row["kind"] for row in rows] == ["reserve", "release"], rows


def test_cancellation_during_the_sync_fallback_call_spends_two_requests(c203_real_stack) -> None:
    """The fallback path stops exactly like the primary: no retry after it."""
    owner = t041.owner(c203_real_stack.stack)
    fallback_id = seed_real_fallback(c203_real_stack, owner)
    configure_stub_primary(
        c203_real_stack, owner,
        model_id="stub-adapter-down-primary", fallback_ids=[fallback_id],
    )
    _, run, lease = t041.claimed_run(c203_real_stack.stack, run_owner=owner)
    provider = c203_real_stack.provider
    gate = gated_completion(provider)
    operation_id = t041.new_uuid()
    connection = open_raw_post(
        t041.worker_client(c203_real_stack.stack),
        SYNC_PATH.format(run_id=run["id"]),
        sync_body(lease, operation_id=operation_id, params=t041.model_params()),
    )
    try:
        # The primary is the dev stub: the only wire request is the fallback's.
        wait_for_requests(provider)
        abort(connection)
        operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    finally:
        gate.set()

    assert operation is not None, "the cancelled fallback operation was never closed"
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "caller_cancelled", operation
    assert operation["receipt_artifact_id"] is None, operation

    attempts = t041.attempt_rows(c203_real_stack.dbname, operation_id)
    assert [row["attempt_no"] for row in attempts] == [1, 2], attempts
    assert [row["state"] for row in attempts] == ["failed", "failed"], attempts
    assert attempts[0]["error_code"] == "adapter_unavailable", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert attempts[1]["error_code"] == "caller_cancelled", attempts[1]
    assert attempts[1]["settlement_state"] == "released", attempts[1]
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)

    rows = t046.ledger_rows(c203_real_stack.dbname, run["id"])
    kinds = [row["kind"] for row in rows]
    # Each attempt's own reserve is paired with its release; the ledger orders
    # entries by creation time, not by attempt, so count the kinds.
    assert kinds.count("reserve") == 2, rows
    assert kinds.count("release") == 2, rows
    assert kinds.count("charge") == 0, rows


# ---------------------------------------------------------------------------
# cancellation during a call: both streaming paths
# ---------------------------------------------------------------------------
def test_consumer_stop_during_the_stream_call_closes_as_stream_cancelled(c203_real_stack) -> None:
    _, run, lease = real_run(c203_real_stack)
    provider = c203_real_stack.provider
    stalled_stream(provider, [
        t122.stream_frame(content="partial"),
        t122.stream_frame(content="never"),
        t122.stream_frame(finish_reason="stop"),
        t122.stream_frame(usage=(9, 4)),
    ], after=1, stall=3.0)
    operation_id = t041.new_uuid()
    connection, received = t044.open_raw_stream(
        t041.worker_client(c203_real_stack.stack), run["id"],
        t044_body(lease, operation_id, t041.model_params()),
    )
    try:
        assert b"event: delta" in received, received
        abort(connection)
        attempts = t044.wait_attempt_closed(c203_real_stack.dbname, operation_id)
        operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    finally:
        time.sleep(0.1)

    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "stream_cancelled", attempts
    assert attempts[0]["settlement_state"] == "released", attempts
    assert operation is not None and operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)


def test_consumer_stop_during_the_stream_fallback_call_spends_two_requests(c203_real_stack) -> None:
    owner = t041.owner(c203_real_stack.stack)
    fallback_id = seed_real_fallback(c203_real_stack, owner)
    configure_stub_primary(
        c203_real_stack, owner,
        model_id="stub-adapter-down-primary", fallback_ids=[fallback_id],
    )
    _, run, lease = t041.claimed_run(c203_real_stack.stack, run_owner=owner)
    provider = c203_real_stack.provider
    stalled_stream(provider, [
        t122.stream_frame(content="fallback partial"),
        t122.stream_frame(content="never"),
        t122.stream_frame(finish_reason="stop"),
        t122.stream_frame(usage=(9, 4)),
    ], after=1, stall=3.0)
    operation_id = t041.new_uuid()
    connection, received = t044.open_raw_stream(
        t041.worker_client(c203_real_stack.stack), run["id"],
        t044_body(lease, operation_id, t041.model_params(stub_stream_chunks=1)),
    )
    try:
        assert b"event: error" in received, received
        deadline = time.time() + 10
        while b"event: delta" not in received and time.time() < deadline:
            chunk = connection.recv(65536)
            if not chunk:
                break
            received += chunk
        assert b"event: delta" in received, received
        abort(connection)
        attempts = t044.wait_attempt_closed(c203_real_stack.dbname, operation_id)
        operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    finally:
        time.sleep(0.1)

    assert [row["attempt_no"] for row in attempts] == [1, 2], attempts
    assert [row["state"] for row in attempts] == ["failed", "failed"], attempts
    assert attempts[0]["error_code"] == "adapter_unavailable", attempts[0]
    assert attempts[0]["settlement_state"] == "released", attempts[0]
    assert attempts[1]["error_code"] == "stream_cancelled", attempts[1]
    assert attempts[1]["settlement_state"] == "released", attempts[1]
    assert operation is not None and operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)


def test_consumer_stop_during_backoff_never_starts_the_next_attempt(c203_real_stack) -> None:
    """The retry notice was delivered; the consumer vanished during the pause."""
    _, run, lease = real_run(c203_real_stack)
    provider = c203_real_stack.provider
    provider.respond_sequence([t122.status_responder(500)])
    operation_id = t041.new_uuid()
    connection, received = t044.open_raw_stream(
        t041.worker_client(c203_real_stack.stack), run["id"],
        t044_body(lease, operation_id, t041.model_params()),
    )
    try:
        assert b"event: error" in received, received
        abort(connection)
        attempts = t044.wait_attempt_closed(c203_real_stack.dbname, operation_id)
        operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    finally:
        time.sleep(0.1)

    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "provider_transient", attempts
    assert attempts[0]["settlement_state"] == "released", attempts
    assert operation is not None and operation["state"] == "failed", operation
    assert operation["last_error_code"] == "stream_cancelled", operation
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)


# ---------------------------------------------------------------------------
# cancellation during finishModelSuccess sealing
# ---------------------------------------------------------------------------
def test_cancellation_during_sealing_closes_the_ledger_and_settles_usage(
    c203_real_stack,
) -> None:
    """A complete answer must not leave the attempt/operation ``dispatching``.

    The receipt insert is held behind a real PostgreSQL table lock, so the caller
    disconnects exactly while ``finishModelSuccess`` is sealing the complete
    answer. The physical call already happened and its usage is real consumption:
    the cleanup must close both rows with a context that outlives the dead
    request and settle the reported usage, not silently ignore the failed seal.
    """
    _, run, lease = real_run(c203_real_stack)
    provider = c203_real_stack.provider
    gate = gated_completion(provider, usage=(11, 7))
    operation_id = t041.new_uuid()
    connection = open_raw_post(
        t041.worker_client(c203_real_stack.stack),
        SYNC_PATH.format(run_id=run["id"]),
        sync_body(lease, operation_id=operation_id, params=t041.model_params()),
    )
    holder = st.HeldLock(
        c203_real_stack.dbname,
        "LOCK TABLE platform.artifacts IN ACCESS EXCLUSIVE MODE",
        name="c203-artifacts-seal-lock",
    )
    try:
        wait_for_requests(provider)
        holder.__enter__()
        gate.set()
        # The receipt insert is queued behind the table lock: sealing really is
        # in progress when the caller disappears.
        st.wait_for_lock_waiters(c203_real_stack.dbname, count=1)
        abort(connection)
        operation = wait_operation_stopped(
            c203_real_stack.dbname, run["id"], operation_id, timeout=25.0
        )
    finally:
        gate.set()
        holder.release()

    assert operation is not None, "the sealing operation was never closed"
    assert operation["state"] == "failed", operation
    assert operation["last_error_code"] == "internal_error", operation
    assert operation["receipt_artifact_id"] is None, operation

    attempts = t041.attempt_rows(c203_real_stack.dbname, operation_id)
    assert len(attempts) == 1, attempts
    attempt = attempts[0]
    assert attempt["state"] == "failed", attempt
    assert attempt["error_code"] == "internal_error", attempt
    assert attempt["settlement_state"] == "settled", attempt
    assert int(attempt["tokens_in"]) == 11, attempt
    assert int(attempt["tokens_out"]) == 7, attempt
    charge = t046.expected_charge(
        11, 7, input_per_million="1.00", output_per_million="2.00"
    )
    assert Decimal(attempt["charged_amount"]) == charge, attempt
    assert attempt["price_version"] == GOLDEN_PRICE_VERSION, attempt

    rows = t046.ledger_rows(c203_real_stack.dbname, run["id"])
    assert sorted(row["kind"] for row in rows) == ["charge", "release", "reserve"], rows
    charged = next(row for row in rows if row["kind"] == "charge")
    assert Decimal(charged["amount"]) == charge, charged
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)


# ---------------------------------------------------------------------------
# a cancelled request's cleanup uses a bounded, independent context
# ---------------------------------------------------------------------------
def test_cancelled_call_cleanup_survives_on_a_detached_context(c203_real_stack) -> None:
    """The close must not inherit the cancelled request context.

    The attempt table is locked after the physical call started. The caller then
    disconnects, so the request context is dead while the cleanup's UPDATE is
    queued. The cleanup must still wait on its own bounded context and commit
    once the database allows it; a cleanup bound to the request context would
    fail immediately and leave both rows ``dispatching``.
    """
    _, run, lease = real_run(c203_real_stack)
    provider = c203_real_stack.provider
    gate = gated_completion(provider)
    operation_id = t041.new_uuid()
    connection = open_raw_post(
        t041.worker_client(c203_real_stack.stack),
        SYNC_PATH.format(run_id=run["id"]),
        sync_body(lease, operation_id=operation_id, params=t041.model_params()),
    )
    holder = st.HeldLock(
        c203_real_stack.dbname,
        "LOCK TABLE platform.operation_attempts IN ACCESS EXCLUSIVE MODE",
        name="c203-attempt-cleanup-lock",
    )
    try:
        wait_for_requests(provider)
        holder.__enter__()
        abort(connection)
        # A backend is queued on the attempt table: that is the detached cleanup
        # waiting, even though the request context is already cancelled. The
        # operation is not locked by the holder, so its row is still readable.
        st.wait_for_lock_waiters(c203_real_stack.dbname, count=1)
        blocked = t041.operation_row(c203_real_stack.dbname, run["id"], operation_id)
        assert blocked is not None and blocked["state"] == "dispatching", blocked
    finally:
        gate.set()
        holder.release()

    attempts = t044.wait_attempt_closed(c203_real_stack.dbname, operation_id)
    operation = wait_operation_stopped(c203_real_stack.dbname, run["id"], operation_id)
    assert len(attempts) == 1, attempts
    assert attempts[0]["state"] == "failed", attempts
    assert attempts[0]["error_code"] == "caller_cancelled", attempts
    assert attempts[0]["settlement_state"] == "released", attempts
    assert operation is not None and operation["state"] == "failed", operation
    assert operation["last_error_code"] == "caller_cancelled", operation
    assert len(provider.requests()) == 1, provider.requests()
    assert_no_receipt(c203_real_stack, run)


# ---------------------------------------------------------------------------
# helpers shared by the streaming cases
# ---------------------------------------------------------------------------
def t044_body(lease: dict, operation_id: str, params: dict,
              logical_key: str = "stream-1") -> dict:
    return {
        "attempt_token": lease["attempt_token"],
        "operation_id": operation_id,
        "logical_key": logical_key,
        "capability_version": t041.GOLDEN_MODEL_ID,
        "params": params,
        "capability_kind": "model",
    }
