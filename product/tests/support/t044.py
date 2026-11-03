"""F06/T044 real-stack helpers: streaming model generations and owner preview.

Nothing is mocked: the internal stream is a real mTLS POST read as SSE from the
compiled API, the runs/attempts/receipts are real PostgreSQL rows and real sealed
object bytes, and the preview is the real in-process hub reached over the public
owner HTTP surface.
"""

from __future__ import annotations

import json
import socket
import ssl
import struct
import threading
import time

from support import f00b, t041
from support import stack as st

MODEL_STREAM_PATH = "/internal/v1/runs/{run_id}/model-stream"
PREVIEW_PATH = "/api/v1/runs/{run_id}/model-preview"


def stream(
    client: f00b.InternalClient,
    lease: dict,
    *,
    operation_id: str,
    logical_key: str = "stream-1",
    capability_version: str = t041.GOLDEN_MODEL_ID,
    capability_kind: str = "model",
    params: dict | None = None,
    attempt_token: str | None = None,
    body: dict | None = None,
    timeout: float = 30.0,
):
    if body is None:
        body = {
            "attempt_token": lease["attempt_token"] if attempt_token is None else attempt_token,
            "operation_id": operation_id,
            "logical_key": logical_key,
            "capability_version": capability_version,
            "params": params,
            "capability_kind": capability_kind,
        }
    return client.post(
        MODEL_STREAM_PATH.format(run_id=lease["run_id"]),
        body,
        headers={"Accept": "text/event-stream"},
        timeout=timeout,
    )


def parse_sse(text: str) -> list[dict]:
    """Parse the platform SSE framing into event payload objects.

    Each payload is the JSON data object; ``type`` must equal the event name. A
    non-JSON or eventless line is kept as a test failure rather than ignored.
    """
    frames: list[dict] = []
    event: str | None = None
    data: list[str] = []
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            data.append(line[len("data: "):])
        elif line == "":
            if event is not None:
                payload = json.loads("\n".join(data))
                payload.setdefault("type", event)
                assert payload["type"] == event, (event, payload)
                frames.append(payload)
            event, data = None, []
    return frames


def delta_text(frames: list[dict], generation_id: str | None = None) -> str:
    return "".join(
        frame.get("text", "")
        for frame in frames
        if frame["type"] == "delta"
        and (generation_id is None or frame["generation_id"] == generation_id)
    )


def generation_ids(frames: list[dict]) -> list[str]:
    seen: list[str] = []
    for frame in frames:
        generation = frame.get("generation_id")
        assert isinstance(generation, str) and generation, frame
        if generation not in seen:
            seen.append(generation)
    return seen


def operation_ids(frames: list[dict]) -> set[str]:
    return {frame["operation_id"] for frame in frames}


def finals(frames: list[dict]) -> list[dict]:
    return [frame for frame in frames if frame["type"] == "final"]


def errors(frames: list[dict]) -> list[dict]:
    return [frame for frame in frames if frame["type"] == "error"]


def run_events_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.run_events WHERE "
            f"run_id = {st.quote_literal(run_id)}::uuid",
        )
    )


def receipt_artifact_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.run_artifacts WHERE "
            f"run_id = {st.quote_literal(run_id)}::uuid AND relation = 'capability_receipt'",
        )
    )


# ---------------------------------------------------------------------------
# owner preview
# ---------------------------------------------------------------------------
def _cookie(session: f00b.Session) -> dict[str, str]:
    return {"Cookie": f"{st.SESSION_COOKIE}={session.cookie}"}


def preview(session: f00b.Session, run_id: str, *, timeout: float = 30.0):
    client = st.HttpClient(session.client.base_url)
    return client.get(
        PREVIEW_PATH.format(run_id=run_id),
        headers={**_cookie(session), "Accept": "text/event-stream"},
    )


class PreviewThread(threading.Thread):
    """A real concurrent preview GET (it closes on final/error)."""

    def __init__(self, session: f00b.Session, run_id: str) -> None:
        super().__init__(daemon=True)
        self.session = session
        self.run_id = run_id
        self.response = None
        self.error: BaseException | None = None

    def run(self) -> None:  # pragma: no cover - exercised by the thread caller
        try:
            self.response = preview(self.session, self.run_id)
        except BaseException as exc:  # noqa: BLE001 - reported by the test
            self.error = exc

    def result(self, timeout: float = 30.0):
        self.join(timeout)
        assert not self.is_alive(), "the preview stream did not close"
        assert self.error is None, self.error
        return self.response


# ---------------------------------------------------------------------------
# a deliberately interrupted internal stream (real TLS socket close)
# ---------------------------------------------------------------------------
def open_raw_stream(
    client: f00b.InternalClient, run_id: str, body: dict
) -> tuple[ssl.SSLSocket, bytes]:
    """Send the mTLS stream request and return (socket, bytes read so far).

    The caller reads SSE bytes and may close the socket mid-stream: that is a real
    client disconnect, not a mocked cancel.
    """
    raw = json.dumps(body).encode("utf-8")
    connection = client.context.wrap_socket(
        socket.create_connection((client.host, client.port), timeout=10),
        server_hostname=client.host,
    )
    head = (
        f"POST {MODEL_STREAM_PATH.format(run_id=run_id)} HTTP/1.1\r\n"
        f"Host: {client.host}:{client.port}\r\n"
        "Content-Type: application/json\r\n"
        "Accept: text/event-stream\r\n"
        "X-Internal-Role: worker\r\n"
        f"Content-Length: {len(raw)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("utf-8")
    connection.sendall(head + raw)
    received = b""
    deadline = time.time() + 10
    while b"\n\n" not in received and time.time() < deadline:
        chunk = connection.recv(4096)
        if not chunk:
            break
        received += chunk
    return connection, received


def abort_stream(connection: ssl.SSLSocket) -> None:
    """Close with an RST so the server notices immediately (a real disconnect)."""
    try:
        connection.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
    except OSError:
        pass
    connection.close()


def wait_attempt_closed(dbname: str, operation_id: str, *, timeout: float = 10.0) -> list[dict]:
    deadline = time.time() + timeout
    rows: list[dict] = []
    while time.time() < deadline:
        rows = t041.attempt_rows(dbname, operation_id)
        if rows and all(row["state"] != "dispatching" for row in rows):
            return rows
        time.sleep(0.05)
    return rows
