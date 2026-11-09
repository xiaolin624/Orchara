"""F03/T032 real-stack helpers: durable run events and SSE replay.

T032 makes the committed Run timeline durable in ``platform.run_events`` and
exposes it through two contract operations:

* ``POST /internal/v1/runs/{run_id}/events`` (``submitWorkerEvents``) on the real
  mTLS listener, authenticated by a live attempt token bound to the run;
* ``GET /api/v1/runs/{run_id}/events`` (``runEvents``) as an owner-only SSE
  stream with ``Last-Event-ID`` replay and a retention window.

Everything here is the T031 stack: real PostgreSQL + compiled API + real mTLS +
real object bytes. The SSE client below is a real ``http.client`` connection
that reads frames incrementally and can disconnect mid-stream; no mock server and
no synthetic event rows are involved in the event endpoint tests.
"""

from __future__ import annotations

import http.client
import json
import urllib.parse
import uuid

from support.runtime_stack import Stack
from support import stack as st
from support import t029, t031

# Re-exports so a T032 test imports one module.
T032Stack = Stack
Session = t029.Session
admin_session = t029.admin_session
session_for = t029.session_for
invite_and_activate = t029.invite_and_activate
golden_owner = t029.golden_owner
minimal_owner = t029.minimal_owner
queued_run = t029.queued_run
claim_lease = t029.claim_lease
execution_client = t029.execution_client
exit_evidence = t029.exit_evidence
attempt_row = t029.attempt_row
quota_counter = t029.quota_counter
run_state_row = t029.run_state_row
job_row = t029.job_row
new_uuid = t029.new_uuid
wait_until = t029.wait_until
run_path = t029.run_path
seed_output_artifact = t031.seed_output_artifact
link_output = t031.link_output
result_body = t031.result_body


# ---------------------------------------------------------------------------
# real worker events (contract WorkerEvent, node.status only)
# ---------------------------------------------------------------------------
def node_event(
    *,
    event_id: str | None = None,
    local_sequence: int = 1,
    node_id: str = "node-1",
    state: str = "running",
    attempt_no: int = 1,
    payload: dict | None = None,
) -> dict:
    if payload is None:
        payload = {"node_id": node_id, "state": state, "attempt_no": attempt_no}
    return {
        "event_id": event_id if event_id is not None else new_uuid(),
        "local_sequence": local_sequence,
        "type": "node.status",
        "payload": payload,
    }


def submit_events(
    client,
    run_id: str,
    attempt_token: str | None,
    events: list[dict],
    *,
    body: dict | None = None,
):
    """One real mTLS POST to the contract's submitWorkerEvents endpoint."""
    if body is None:
        body = {"attempt_token": attempt_token, "events": events}
    return client.post(f"/internal/v1/runs/{run_id}/events", body)


# ---------------------------------------------------------------------------
# durable event read model (database facts)
# ---------------------------------------------------------------------------
def run_events(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT event_id::text AS event_id, run_id::text AS run_id, seq, revision, type, "
        "payload::text AS payload, created_at::text AS created_at "
        "FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY seq",
    )


def run_event_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.run_events WHERE "
            f"run_id = {st.quote_literal(run_id)}::uuid",
        )
    )


def next_event_seq(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT next_event_seq FROM platform.runs WHERE "
            f"id = {st.quote_literal(run_id)}::uuid",
        )
    )


def payload_of(row: dict) -> dict:
    return json.loads(row["payload"])


def status_events(dbname: str, run_id: str) -> list[tuple[int, int, dict]]:
    """(seq, revision, run.status payload) for every run.status event."""
    return [
        (row["seq"], row["revision"], payload_of(row))
        for row in run_events(dbname, run_id)
        if row["type"] == "run.status"
    ]


def events_path(run_id: str) -> str:
    return f"/api/v1/runs/{run_id}/events"


# ---------------------------------------------------------------------------
# real SSE client
# ---------------------------------------------------------------------------
class SSEStream:
    """One real HTTP/1.1 SSE connection, read frame by frame.

    ``http.client.HTTPResponse.readline`` decodes the chunked transfer encoding
    the Go server uses when it flushes frames, so a frame is one blank-line
    terminated block on the wire. ``close`` disconnects mid-stream, which is how
    the reconnect test exercises ``Last-Event-ID``.
    """

    def __init__(
        self,
        server,
        *,
        cookie: str | None,
        run_id: str,
        last_event_id: str | int | None = None,
        timeout: float = 15.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(server.base_url)
        self.connection = http.client.HTTPConnection(
            parsed.hostname, parsed.port, timeout=timeout
        )
        headers = {"Accept": "text/event-stream"}
        if cookie:
            headers["Cookie"] = f"{st.SESSION_COOKIE}={cookie}"
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        self.connection.request("GET", events_path(run_id), headers=headers)
        self.response = self.connection.getresponse()
        self.status = self.response.status
        self.headers = self.response.headers

    def read_frame(self) -> dict | None:
        """Read one SSE block, or None at EOF."""
        lines: list[str] = []
        while True:
            raw = self.response.readline()
            if not raw:
                return None
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if line == "":
                if not lines:
                    continue
                return parse_frame(lines)
            lines.append(line)

    def error_body(self) -> dict:
        return json.loads(self.response.read().decode("utf-8"))

    def close(self) -> None:
        try:
            self.response.close()
        finally:
            self.connection.close()


def open_sse(
    stack,
    session: Session,
    run_id: str,
    *,
    last_event_id: str | int | None = None,
    timeout: float = 15.0,
) -> SSEStream:
    return SSEStream(
        stack.server,
        cookie=session.cookie,
        run_id=run_id,
        last_event_id=last_event_id,
        timeout=timeout,
    )


def parse_frame(lines: list[str]) -> dict:
    frame = {"id": "", "event": "", "data": "", "comment": ""}
    data: list[str] = []
    for line in lines:
        if line.startswith(":"):
            frame["comment"] = line[1:].strip()
            continue
        if ":" in line:
            field, value = line.split(":", 1)
            value = value[1:] if value.startswith(" ") else value
        else:
            field, value = line, ""
        if field == "id":
            frame["id"] = value
        elif field == "event":
            frame["event"] = value
        elif field == "data":
            data.append(value)
    frame["data"] = "\n".join(data)
    return frame


def frame_envelope(frame: dict) -> dict:
    assert frame["data"], frame
    return json.loads(frame["data"])


def read_frames(
    stream: SSEStream, *, max_frames: int = 50, until=None
) -> list[dict]:
    """Read frames until EOF/predicate/max; never spins past max."""
    frames: list[dict] = []
    for _ in range(max_frames):
        frame = stream.read_frame()
        if frame is None:
            break
        frames.append(frame)
        if until is not None and until(frame):
            break
    return frames


def read_status_frames(stream: SSEStream, *, max_frames: int = 50) -> list[dict]:
    """Read only data frames (comments/heartbeats are skipped)."""
    frames: list[dict] = []
    for _ in range(max_frames):
        frame = stream.read_frame()
        if frame is None:
            break
        if frame["data"]:
            frames.append(frame)
        if is_terminal_frame(frame):
            break
    return frames


def read_until_heartbeat(stream: SSEStream, *, max_frames: int = 20) -> dict | None:
    for _ in range(max_frames):
        frame = stream.read_frame()
        if frame is None:
            return None
        if frame["comment"]:
            return frame
    return None


def is_terminal_frame(frame: dict) -> bool:
    if not frame.get("data"):
        return False
    try:
        envelope = json.loads(frame["data"])
    except json.JSONDecodeError:
        return False
    return envelope.get("type") == "run.status" and envelope.get("payload", {}).get(
        "state"
    ) in t031.terminal_states()


def wait_for_events(dbname: str, run_id: str, count: int, *, timeout: float = 5.0) -> None:
    wait_until(
        lambda: run_event_count(dbname, run_id) >= count,
        timeout=timeout,
        what=f"run {run_id} to have {count} events",
    )


def assert_envelope(envelope: dict, *, run_id: str, seq: int, event_type: str) -> None:
    assert set(envelope) == {
        "schema_version",
        "event_id",
        "run_id",
        "seq",
        "revision",
        "type",
        "created_at",
        "payload",
    }, envelope
    assert envelope["schema_version"] == "1.0", envelope
    assert uuid.UUID(envelope["event_id"]), envelope
    assert envelope["run_id"] == run_id, envelope
    assert envelope["seq"] == seq, envelope
    assert isinstance(envelope["revision"], int) and envelope["revision"] >= 1, envelope
    assert envelope["type"] == event_type, envelope
    assert isinstance(envelope["payload"], dict), envelope
    assert isinstance(envelope["created_at"], str) and envelope["created_at"], envelope


# ---------------------------------------------------------------------------
# forced-failure trigger helpers (event rows must roll back with the business tx)
# ---------------------------------------------------------------------------
def fail_inserts_on(dbname: str, table: str, *, trigger: str) -> None:
    function = f"platform.{trigger}_fn"
    st.sql(
        dbname,
        f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger LANGUAGE plpgsql AS $$ "
        f"BEGIN RAISE EXCEPTION '{trigger}: forced failure'; END $$",
    )
    st.sql(
        dbname,
        f"DROP TRIGGER IF EXISTS {trigger} ON platform.{table}",
    )
    st.sql(
        dbname,
        f"CREATE TRIGGER {trigger} BEFORE INSERT ON platform.{table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
    )


def clear_insert_failure(dbname: str, table: str, *, trigger: str) -> None:
    st.sql(dbname, f"DROP TRIGGER IF EXISTS {trigger} ON platform.{table}")
    st.sql(dbname, f"DROP FUNCTION IF EXISTS platform.{trigger}_fn()")
