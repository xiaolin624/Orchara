"""F09/T063 real-stack helpers: confirmed-memory recall in the agent context.

The stack is T054/T058's (real PostgreSQL migrated through 0036, the compiled
``cmd/api`` and ``cmd/supervisor`` binaries, the real internal mTLS listener, the
real filesystem object store and the real WSL2/runsc sandbox). A recall proof is
one real debug run: the run is submitted through ``debugDraft``, claimed by the
real ``cmd/supervisor exec --once``, the agent loop builds the context from the
real PostgreSQL memory rows, and the persisted trace plus the sealed model
request are read back through the ordinary owner-only routes.

The model provider is the documented deterministic dev stub; the tool execution,
the memory store, the object bytes and the context trace are real.
"""

from __future__ import annotations

import json

from support import stack as st
from support import t027, t029, t033, t041, t043, t054

# The production recall bounds mirrored from internal/memory/recall.go. The trace
# assertions name these numbers instead of hard-coding them twice.
RECALL_ROW_LIMIT = 200      # memory.RecallRowLimit
RECALL_SCAN_BATCH = 200     # memory.RecallScanBatch
RECALL_SCAN_LIMIT = 2000    # memory.RecallScanRowLimit


# ---------------------------------------------------------------------------
# setup: real owners, applications, memories
# ---------------------------------------------------------------------------
def owner(stack):
    """A real creator with the real golden draft (the T029/T033 owner)."""
    return t033.golden_owner(stack)


def application_for(owner_, *, name: str = "t063 agent") -> str:
    """A real application whose draft is the four-node agent chain."""
    return t043.application_for(owner_, t043.agent_definition(name=name), name=name)


def other_application(session, *, name: str = "t063 second application") -> str:
    """A second real application owned by the same creator."""
    return t027.application_with_definition(session, t027.chain_definition([]), name=name)


def create_memory(session, application_id: str, text: str) -> dict:
    response = t054.create_memory(session, application_id=application_id, text=text)
    assert response.status == 201, response.text
    return response.json


def seed_memories(
    session, application_id: str, *, prefix: str, count: int, start: int = 0
) -> list[str]:
    """Create ``count`` ordinary confirmed memories through the real API.

    Bulk seeding exists so a test can put a protected row beyond the recency cap
    (``RECALL_ROW_LIMIT``) or beyond the hard scan bound (``RECALL_SCAN_LIMIT``)
    without hiding that setup in SQL. Every row is a real ``POST /memories`` with
    its own sealed body.
    """
    ids: list[str] = []
    for index in range(start, start + count):
        memory = create_memory(
            session, application_id, f"{prefix}-{index}: value {index}"
        )
        ids.append(memory["id"])
    return ids


def submit_run(stack, owner_, application_id: str, *, input_value: dict | None = None) -> dict:
    """One queued debug run against the agent application (real submission)."""
    csv_id = t043.ready_csv(stack, owner_)
    return t043.submit_agent_run(
        owner_,
        application_id,
        artifact_id=csv_id,
        input_value=input_value if input_value is not None else {"source": "t063"},
    )


def claim_run(
    stack,
    run: dict,
    *,
    role: str = "supervisor",
    worker_id: str = "t063-reader",
    execution_unit_id: str = "t063-reader",
) -> dict:
    """Claim one specific queued run; fail loudly if a different job came back."""
    response = t029.execution_client(stack, role).claim_job(
        worker_id=worker_id, execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


def propose_candidate(stack, lease: dict, *, text: str, logical_key: str) -> dict:
    response = t054.propose(stack, lease, text=text, logical_key=logical_key)
    assert response.status == 201, response.text
    return response.json


def confirm_candidate(session, candidate_id: str) -> str:
    """Confirm one candidate through the real decision entry; return the memory id."""
    response = t054.decide(session, candidate_id, decision="confirm")
    assert response.status == 200, response.text
    body = response.json
    assert body["candidate_id"] == candidate_id, body
    assert body["state"] == "confirmed", body
    assert isinstance(body["memory_id"], str), body
    return body["memory_id"]


def run_agent_once(stack) -> tuple[object, dict]:
    """The real supervisor claim/run/result/exit with the real object store wired.

    ``ORCHARA_OBJECT_DIR`` is the same directory the API seals memory bodies in;
    without it the supervisor can read memory rows but not their bytes, and the
    trace correctly reports ``unavailable``. The recall proof must see the real
    bytes, so the deployment identity is passed exactly as an operator would.
    """
    return t043.run_agent_once(
        stack, env_extra={"ORCHARA_OBJECT_DIR": str(stack.object_dir)}
    )


# ---------------------------------------------------------------------------
# the durable trace and the sealed model request
# ---------------------------------------------------------------------------
def trace_document(session, stack, run_id: str) -> dict:
    """Read the context trace through the documented owner-only routes.

    ``GET /api/v1/runs/{run_id}/node-results`` locates the persisted
    ``context-trace`` file row; the ordinary artifact content route serves its
    bytes. No test-only endpoint is used.
    """
    response = session.client.get(f"/api/v1/runs/{run_id}/node-results")
    assert response.status == 200, response.text
    items = response.json["items"]
    trace_rows = [
        item for item in items
        if item["kind"] == "file" and item["label"] == "context-trace"
    ]
    assert len(trace_rows) == 1, items
    content = t033.artifact_content(session, trace_rows[0]["artifact_id"])
    assert content.status == 200, content.text
    document = json.loads(content.raw)
    assert document["version"] == 1, document
    return document


def last_turn(trace: dict) -> dict:
    turns = trace["turns"]
    assert len(turns) >= 1, trace
    return turns[-1]


def memory_sources(turn: dict) -> list[dict]:
    return [
        source for source in turn.get("sources", [])
        if source.get("kind") == "memory"
    ]


def optional_memory_truncations(turn: dict) -> list[dict]:
    return [
        truncation for truncation in turn.get("truncations", [])
        if truncation.get("reason") == "optional_memory"
    ]


def memory_scan(turn: dict) -> dict:
    """The recall source's bounded-scan report of one trace turn ({} if absent)."""
    return turn.get("memory_scan") or {}


def scan_truncations(turn: dict) -> list[dict]:
    """Trace entries that admit the recall scan stopped at its hard bound."""
    return [
        truncation for truncation in turn.get("truncations", [])
        if truncation.get("reason") == "memory_scan_truncated"
    ]


def newest_memory_ids(
    dbname: str, owner_id: str, application_id: str, limit: int
) -> list[str]:
    """The exact recency page ``RecallSource`` reads, ordered like its SQL.

    This is the independent setup proof that a seeded row is outside the cap: the
    recall source itself is never asked to describe what it cannot see.
    """
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id FROM platform.memories "
        f"WHERE owner_id = {st.quote_literal(owner_id)}::uuid "
        f"AND application_id = {st.quote_literal(application_id)}::uuid "
        "AND deleted_at IS NULL "
        "ORDER BY confirmed_at DESC, id DESC "
        f"LIMIT {int(limit)}",
    )
    return [row["id"] for row in rows]


def model_messages(stack, run_id: str) -> list[dict]:
    """The exact first model request the agent loop sent (sealed ledger artifact)."""
    operations = [
        row for row in t041.operation_rows(stack.dbname, run_id)
        if row["capability_kind"] == "model"
    ]
    assert operations, "the agent run made no model call"
    params = json.loads(
        t041.artifact_bytes(
            stack, t041.artifact_row(stack.dbname, operations[0]["params_artifact_id"])
        )
    )
    assert params["task"] == "agent", params
    return params["messages"]


def messages_text(messages: list[dict]) -> str:
    """Readable model input: JSON message bodies are decoded once, so a marker
    assertion observes the text the model would receive, not its JSON escaping."""
    parts = []
    for message in messages:
        content = str(message.get("content", ""))
        try:
            parts.append(json.dumps(json.loads(content), ensure_ascii=False))
        except (TypeError, ValueError):
            parts.append(content)
    return "\n".join(parts)


def memory_message(messages: list[dict]) -> dict | None:
    """The parsed confirmed-memory message of one model request, if present."""
    for message in messages:
        content = message.get("content")
        if not isinstance(content, str):
            continue
        try:
            parsed = json.loads(content)
        except ValueError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("confirmed_memory"), list):
            return parsed
    return None


def trace_text(trace: dict) -> str:
    return json.dumps(trace, ensure_ascii=False)


def memory_row(dbname: str, memory_id: str) -> dict:
    row = t054.memory_row(dbname, memory_id)
    assert row is not None, memory_id
    return row


def assert_no_content_leak(trace: dict, texts: list[str]) -> None:
    """The trace carries identifiers/reasons/counts only, never memory text."""
    encoded = trace_text(trace)
    for text in texts:
        assert text not in encoded, (text, encoded[:400])
