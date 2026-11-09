"""Shared observations of the facts a real Run leaves behind.

The four capabilities here are the ones acceptance tests need after an execution:
the owner-only node results, the sealed content bytes, the durable run/node status
facts (events and the owner-only SSE replay) and the final output. They only read
what the product committed or exposes; this module never prepares data, starts an
execution, invents expected values or turns a supervisor process report into a Run
terminal state. Callers keep their own workflow fixtures, SQL and assertions.

A "proposed result" is the worker's report, parsed by ``execution_support``; a
"supervisor exit report" describes the process. The committed Run terminal state
is what :func:`wait_run_state` and :func:`output_artifact_id` read from
PostgreSQL, and the sealed bytes are read through the owner routes or the real
object store here.
"""

from __future__ import annotations

import json
import time

from support.runtime_stack import Stack
from support import stack as st
from support import t031, t032

NODE_RESULT_POLL_SECONDS = 0.05
NODE_RESULT_TIMEOUT_SECONDS = 30.0
RUN_STATE_TIMEOUT_SECONDS = 60.0


# ---------------------------------------------------------------------------
# owner node results
# ---------------------------------------------------------------------------
def node_results_response(session, run_id: str):
    """GET the owner-only node-results report (the response, so 404s stay visible)."""
    return session.client.get(f"/api/v1/runs/{run_id}/node-results")


def wait_node_results(
    session,
    run_id: str,
    *,
    expected: int,
    timeout: float = NODE_RESULT_TIMEOUT_SECONDS,
) -> dict:
    """Poll the owner read until at least ``expected`` rows are visible."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = node_results_response(session, run_id)
        if response.status == 200:
            last = response.json
            if len(last.get("items", [])) >= expected:
                return last
        time.sleep(NODE_RESULT_POLL_SECONDS)
    raise AssertionError(f"node results never reached {expected}: {last}")


def node_result_items(
    session,
    run_id: str,
    *,
    expected: int,
    timeout: float = NODE_RESULT_TIMEOUT_SECONDS,
) -> list[dict]:
    """The owner-read result items once ``expected`` of them are visible."""
    return wait_node_results(session, run_id, expected=expected, timeout=timeout)["items"]


def node_data_rows(items: list[dict], node_id: str) -> list[dict]:
    """The owner-read ``data`` rows of one node (skips its file/log rows)."""
    return [item for item in items if item["node_id"] == node_id and item["kind"] == "data"]


def node_result_rows(dbname: str, run_id: str) -> list[dict]:
    """Every durable node-result row with its artifact provenance (database fact)."""
    return st.sql_rows(
        dbname,
        "SELECT o.node_id, o.kind, o.artifact_id::text AS artifact_id, o.label, "
        "a.purpose, a.state, a.media_type, a.sha256 "
        "FROM platform.run_node_outputs AS o "
        "JOIN platform.artifacts AS a ON a.id = o.artifact_id "
        f"WHERE o.run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY o.node_id, o.kind, o.label",
    )


# ---------------------------------------------------------------------------
# sealed content bytes
# ---------------------------------------------------------------------------
def artifact_content(session, artifact_id: str):
    """GET the owner-only artifact content route (the response, so 403/404 stay visible)."""
    return session.client.get(f"/api/v1/artifacts/{artifact_id}/content")


def artifact_document(session, artifact_id: str) -> dict:
    """The sealed JSON document one artifact serves through the owner route."""
    content = artifact_content(session, artifact_id)
    assert content.status == 200, content.text
    return json.loads(content.raw)


def sealed_object_bytes(stack: Stack, artifact: dict) -> bytes:
    """Read one artifact's bytes directly from the real object store."""
    return (stack.object_dir / artifact["object_key"]).read_bytes()


def sealed_object_text(stack: Stack, artifact: dict) -> str:
    """Read one sealed object's bytes as UTF-8 text."""
    return sealed_object_bytes(stack, artifact).decode("utf-8")


def sealed_object_document(stack: Stack, artifact: dict) -> dict:
    """Read one sealed object's bytes as a JSON document."""
    return json.loads(sealed_object_bytes(stack, artifact))


def node_data_document(session, items: list[dict], node_id: str) -> dict:
    """The one sealed data document a node published through its owner read."""
    rows = node_data_rows(items, node_id)
    assert len(rows) == 1, (node_id, rows)
    return artifact_document(session, rows[0]["artifact_id"])


# ---------------------------------------------------------------------------
# durable node.status events and the owner-only SSE replay
# ---------------------------------------------------------------------------
def node_events(dbname: str, run_id: str) -> list[dict]:
    """The durable ``node.status`` events, ordered, with payloads decoded."""
    rows = st.sql_rows(
        dbname,
        "SELECT seq, type, payload::text AS payload "
        "FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid AND type = 'node.status' "
        "ORDER BY seq",
    )
    for row in rows:
        row["payload"] = json.loads(row["payload"])
    return rows


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    """The durable node.status sequence per node, from the run_events projection."""
    states: dict[str, list[str]] = {}
    for event in node_events(dbname, run_id):
        payload = event["payload"]
        states.setdefault(payload["node_id"], []).append(payload["state"])
    return states


def sse_node_states(stack: Stack, session, run_id: str) -> dict[str, list[str]]:
    """The same sequence as the owner-only SSE replay sees it."""
    stream = t032.open_sse(stack, session, run_id)
    try:
        frames = t032.read_status_frames(stream, max_frames=200)
    finally:
        stream.close()
    assert frames, "the SSE replay carried no frames"
    states: dict[str, list[str]] = {}
    for frame in frames:
        envelope = t032.frame_envelope(frame)
        if envelope["type"] != "node.status":
            continue
        payload = envelope["payload"]
        assert set(payload) == {"node_id", "state", "attempt_no"}, payload
        states.setdefault(payload["node_id"], []).append(payload["state"])
    return states


# ---------------------------------------------------------------------------
# committed Run status and final output
# ---------------------------------------------------------------------------
def wait_run_state(
    dbname: str,
    run_id: str,
    states: set[str],
    *,
    timeout: float = RUN_STATE_TIMEOUT_SECONDS,
) -> dict:
    """Poll the committed Run row until its state is one of ``states``."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = t031.run_state_row(dbname, run_id)
        if last["state"] in states:
            return last
        time.sleep(NODE_RESULT_POLL_SECONDS)
    raise AssertionError(f"run {run_id} never reached {states}: {last}")


def output_artifact_id(dbname: str, run_id: str) -> str | None:
    """The committed Run's sealed final-output pointer (None until it publishes)."""
    value = st.sql(
        dbname,
        "SELECT COALESCE(output_artifact_id::text, '') FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    return value or None


def final_output_document(session, dbname: str, run_id: str) -> dict:
    """The sealed final output of the committed Run (never the supervisor report)."""
    artifact_id = output_artifact_id(dbname, run_id)
    assert artifact_id, "the run sealed no final output"
    return artifact_document(session, artifact_id)
