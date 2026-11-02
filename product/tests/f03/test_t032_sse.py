"""T032: the owner-only SSE replay of the durable run timeline.

The SSE client is a real HTTP connection reading frames as the Go server flushes
them. The tests cover Last-Event-ID replay, terminal close, the 15s heartbeat
comment (shortened by the deployment knob), the retention-window 410 with a
snapshot URL, and a genuine mid-run disconnect + reconnect.
"""

from __future__ import annotations

import time

from support import stack as st
from support import t029, t032
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t032_stack,
)


def claimed_run(stack, owner, *, execution_unit_id: str = "unit-sse") -> dict:
    run = t032.queued_run(stack, owner)
    response = t032.execution_client(stack).claim_job(
        worker_id="t032-supervisor", execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


def fail_run(stack, lease: dict, *, execution_unit_id: str = "unit-sse") -> None:
    response = t032.execution_client(stack, "worker").submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="failed",
        error_code="tool_crashed",
    )
    assert response.status == 200, response.text
    response = t032.execution_client(stack).confirm_exit(
        lease["attempt_token"],
        execution_unit_id=execution_unit_id,
        evidence=t032.exit_evidence(lease),
    )
    assert response.status == 200, response.text


def test_sse_replays_envelopes_and_last_event_id_returns_only_newer_frames(
    t032_stack,
) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    run_id = lease["run_id"]

    stream = t032.open_sse(t032_stack, owner.creator, run_id)
    try:
        assert stream.status == 200, stream.status
        assert "text/event-stream" in stream.headers.get("Content-Type"), stream.headers
        assert stream.headers.get("Cache-Control") == "no-store", stream.headers
        frames = t032.read_frames(stream, max_frames=2)
        assert [frame["id"] for frame in frames] == ["1", "2"], frames
        assert [frame["event"] for frame in frames] == ["run.status", "run.status"], frames
        first = t032.frame_envelope(frames[0])
        second = t032.frame_envelope(frames[1])
        t032.assert_envelope(first, run_id=run_id, seq=1, event_type="run.status")
        t032.assert_envelope(second, run_id=run_id, seq=2, event_type="run.status")
        assert first["payload"] == {"state": "queued"}, first
        assert second["payload"] == {"state": "running"}, second
        assert first["revision"] == 1 and second["revision"] == 2, (first, second)
    finally:
        stream.close()

    # Reconnect from seq 1: only seq 2 is replayed, never seq 1 again.
    stream = t032.open_sse(t032_stack, owner.creator, run_id, last_event_id=1)
    try:
        frames = t032.read_frames(stream, max_frames=1)
        assert [frame["id"] for frame in frames] == ["2"], frames
        envelope = t032.frame_envelope(frames[0])
        assert envelope["seq"] == 2 and envelope["payload"] == {"state": "running"}, envelope
    finally:
        stream.close()


def test_sse_closes_after_the_terminal_event_and_ids_are_monotonic(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    fail_run(t032_stack, lease)

    stream = t032.open_sse(t032_stack, owner.creator, lease["run_id"])
    try:
        frames = t032.read_frames(stream, max_frames=10)
        envelope_frames = [frame for frame in frames if frame["data"]]
        assert len(envelope_frames) == 4, frames
        assert [frame["id"] for frame in envelope_frames] == ["1", "2", "3", "4"]
        ids = [t032.frame_envelope(frame)["seq"] for frame in envelope_frames]
        assert ids == sorted(ids) and len(set(ids)) == len(ids), ids
        assert [
            t032.frame_envelope(frame)["payload"]["state"] for frame in envelope_frames
        ] == ["queued", "running", "running", "failed"]
        assert t032.frame_envelope(envelope_frames[-1])["payload"] == {
            "state": "failed",
            "error_code": "tool_crashed",
        }
        # The server returns after replaying the terminal state.
        assert stream.read_frame() is None
    finally:
        stream.close()


def test_heartbeat_is_a_comment_and_does_not_consume_seq(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)
    before = t032.next_event_seq(t032_stack.dbname, run["id"])
    assert before == 2

    stream = t032.open_sse(t032_stack, owner.creator, run["id"], last_event_id=1)
    try:
        comment = t032.read_until_heartbeat(stream)
        assert comment is not None, "no heartbeat comment arrived"
        assert comment["comment"] == "heartbeat", comment
        assert comment["id"] == "" and comment["event"] == "" and comment["data"] == "", comment
    finally:
        stream.close()
    assert t032.next_event_seq(t032_stack.dbname, run["id"]) == before
    assert t032.run_event_count(t032_stack.dbname, run["id"]) == 1


def test_cursor_at_the_latest_event_streams_without_a_retention_error(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    assert t032.next_event_seq(t032_stack.dbname, lease["run_id"]) == 3

    stream = t032.open_sse(
        t032_stack, owner.creator, lease["run_id"], last_event_id=2
    )
    try:
        assert stream.status == 200
        comment = t032.read_until_heartbeat(stream)
        assert comment is not None
        # No event frame before the heartbeat: the cursor was already current.
        assert comment["data"] == ""
    finally:
        stream.close()


def test_retention_window_returns_410_with_the_run_snapshot_url(
    t032_stack, api_server
) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)
    before = t032.next_event_seq(t032_stack.dbname, run["id"])

    # A second API process over the same database with a deliberately tiny
    # retention window (the knob is deployment configuration, never per request).
    expiring = api_server(
        t032_stack.dsn,
        extra_env={"ORCHARA_RUN_EVENT_RETENTION": "50ms"},
    )
    client = st.HttpClient(expiring.base_url)
    cookie = {"Cookie": f"{st.SESSION_COOKIE}={owner.creator.cookie}"}
    time.sleep(0.2)
    response = client.get(t032.events_path(run["id"]), headers=cookie)
    assert response.status == 410, response.text
    assert response.json["error"]["code"] == "event_cursor_expired", response.text
    assert response.json["error"]["details"] == {
        "snapshot_url": t029.run_path(run["id"])
    }, response.text

    # The same client with Last-Event-ID well outside the window is refused too.
    response = client.get(
        t032.events_path(run["id"]), headers={**cookie, "Last-Event-ID": "0"}
    )
    assert response.status == 410, response.text
    assert t032.next_event_seq(t032_stack.dbname, run["id"]) == before
    assert t032.run_event_count(t032_stack.dbname, run["id"]) == 1


def test_the_stream_is_owner_only_and_last_event_id_is_validated(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)
    other = t032.invite_and_activate(
        t032_stack, owner.admin, "t032-other-" + t032.new_uuid()[:8], roles=("user",)
    )

    response = other.client.get(t032.events_path(run["id"]))
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found", response.text

    admin = t032.admin_session(t032_stack)
    response = admin.client.get(t032.events_path(run["id"]))
    assert response.status == 404, response.text

    response = owner.creator.client.get(
        t032.events_path(run["id"]), headers={"Last-Event-ID": "not-digits"}
    )
    assert response.status == 400, response.text
    assert response.json["error"]["code"] == "validation_failed", response.text
    assert response.json["error"]["details"]["field"] == "Last-Event-ID", response.text

    response = owner.creator.client.get(t032.events_path(t032.new_uuid()))
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found", response.text


def test_real_streaming_disconnect_reconnect_and_terminal_finish(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)
    run_id = run["id"]

    # Open the stream while the run is queued, read the committed queued fact,
    # then disconnect mid-run.
    stream = t032.open_sse(t032_stack, owner.creator, run_id)
    try:
        frames = t032.read_frames(stream, max_frames=1)
        assert [frame["id"] for frame in frames] == ["1"], frames
        assert t032.frame_envelope(frames[0])["payload"] == {"state": "queued"}
    finally:
        stream.close()

    response = t032.execution_client(t032_stack).claim_job(
        worker_id="t032-supervisor", execution_unit_id="unit-stream"
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run_id

    # Reconnect with Last-Event-ID: only the newer running event is replayed.
    stream = t032.open_sse(t032_stack, owner.creator, run_id, last_event_id=1)
    try:
        frames = t032.read_frames(stream, max_frames=1)
        assert [frame["id"] for frame in frames] == ["2"], frames
        assert t032.frame_envelope(frames[0])["payload"] == {"state": "running"}

        # Finish the run while the reconnected stream is open.
        response = t032.execution_client(t032_stack, "worker").submit_result(
            run_id,
            attempt_token=lease["attempt_token"],
            suggested_state="failed",
            error_code="tool_crashed",
        )
        assert response.status == 200, response.text
        response = t032.execution_client(t032_stack).confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-stream",
            evidence=t032.exit_evidence(lease),
        )
        assert response.status == 200, response.text

        rest = t032.read_frames(stream, max_frames=10)
        rest_frames = [frame for frame in rest if frame["data"]]
        assert [frame["id"] for frame in rest_frames] == ["3", "4"], rest
        assert [
            t032.frame_envelope(frame)["payload"]["state"] for frame in rest_frames
        ] == ["running", "failed"]
        assert stream.read_frame() is None
    finally:
        stream.close()
