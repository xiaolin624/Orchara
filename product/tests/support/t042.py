"""F04A/T042 real-stack helpers: the linear start -> python -> end debug run.

Everything here goes through the product's real HTTP/mTLS/database/sandbox path:

* a real application draft whose definition is exactly the linear chain (saved
  through the public draft API),
* the real debug submission (sealed run input + linked task_input artifact),
* the compiled ``cmd/supervisor exec --once`` claiming and running the fixed
  preset tool in WSL2/runsc,
* the real run-scoped mTLS artifact routes and the non-contract node-results
  report, then the owner-only read.

Nothing is mocked. The only fixtures are the documented public sales CSV and the
expected summary from WORKFLOW-SPEC §6.
"""

from __future__ import annotations

import json
import time

from support import execution_support as execution
from support import stack as st
from support import t027, t033

# The fixed preset tool id (migrations seed it with the golden owner).
PRESET_TOOL_ID = "11111111-1111-4111-8111-111111111111"

PYTHON_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "total": {"type": "string"},
        "regions": {"type": "object", "additionalProperties": {"type": "string"}},
        "order_count": {"type": "integer"},
    },
    "required": ["total", "regions", "order_count"],
    "additionalProperties": False,
}

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"source": {"type": "string"}},
    "required": ["source"],
    "additionalProperties": False,
}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"report": {"type": "string"}, "summary": {"type": "object"}},
    "required": ["report", "summary"],
    "additionalProperties": False,
}


def linear_definition(
    *,
    input_schema: dict | None = None,
    python_output_schema: dict | None = None,
    python_inputs: dict | None = None,
    output_schema: dict | None = None,
) -> dict:
    """The T042 definition: start -> stats(python) -> end with explicit bindings."""
    python_node = {
        "id": "stats",
        "type": "python",
        "inputs": python_inputs or {"file": {"ref": "node:start#/artifact"}},
        "output_schema": python_output_schema or PYTHON_OUTPUT_SCHEMA,
        "config": {"tool_version": PRESET_TOOL_ID},
    }
    end_node = {
        "id": "end",
        "type": "end",
        "inputs": {
            "summary": {"ref": "node:stats#"},
            "report": {"literal": "sales summary"},
        },
        "output_schema": {"type": "object"},
        "config": {},
    }
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema or INPUT_SCHEMA,
        "output_schema": output_schema or OUTPUT_SCHEMA,
        "nodes": [t027.start_node(), python_node, end_node],
        "edges": [
            {"source": "start", "target": "stats"},
            {"source": "stats", "target": "end"},
        ],
        "policy": dict(t027.BASE_POLICY),
    }


def application_for(owner, *, definition: dict | None = None, name: str = "t042 linear") -> str:
    """Create one owner application whose draft is the given linear definition."""
    return t027.application_with_definition(
        owner.creator, definition or linear_definition(), name=name
    )


def submit_linear_run(
    owner,
    application_id: str,
    *,
    artifact_id: str | None = None,
    input_value: dict | None = None,
):
    """Submit one debug run of the linear draft and return the contract Run."""
    revision = t027.draft_revision(owner.creator, application_id)
    response = t027.submit_debug(
        owner.creator,
        application_id,
        draft_revision=revision,
        input_value=input_value if input_value is not None else {"source": "t042"},
        artifact_ids=[artifact_id] if artifact_id else [],
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run


def ready_csv(stack, owner, *, content: bytes = t033.SALES_CSV) -> str:
    """Upload the sales CSV through the real T028 path and finalize it."""
    return t033.upload_artifact(stack, owner.creator, content)


run_linear_once = execution.execute_once


# ---------------------------------------------------------------------------
# owner reads and database assertions
# ---------------------------------------------------------------------------
def node_results(session, run_id: str):
    return session.client.get(f"/api/v1/runs/{run_id}/node-results")


def node_result_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT o.node_id, o.kind, o.artifact_id::text AS artifact_id, o.label, "
        "a.purpose, a.state, a.media_type, a.sha256 "
        "FROM platform.run_node_outputs AS o "
        "JOIN platform.artifacts AS a ON a.id = o.artifact_id "
        f"WHERE o.run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY o.node_id, o.kind, o.label",
    )


def node_events(dbname: str, run_id: str) -> list[dict]:
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


def wait_node_results(session, run_id: str, *, expected: int, timeout: float = 30.0) -> dict:
    """Poll the owner read until the node result rows are visible (or timeout)."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = node_results(session, run_id)
        if response.status == 200:
            last = response.json
            if len(last.get("items", [])) >= expected:
                return last
        time.sleep(0.05)
    raise AssertionError(f"node results never reached {expected}: {last}")


def artifacts_of_kind(items: list[dict], kind: str) -> list[dict]:
    return [item for item in items if item["kind"] == kind]


def content_of(session, artifact_id: str):
    return t033.artifact_content(session, artifact_id)


def request_cancel_after_first_heartbeat(dbname: str) -> None:
    """Simulate T036's cancel arriving while the linear run's sandbox boots.

    Same deterministic trigger the T033 cancel test uses: the write happens on the
    attempt's first heartbeat, so the supervisor's next heartbeat (50ms later)
    observes cancel_requested and kills the unit. Test-owned SQL; no product
    trigger exists.
    """
    st.sql(
        dbname,
        "CREATE OR REPLACE FUNCTION t042_request_cancel() RETURNS trigger AS $$ "
        "BEGIN "
        "  IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at THEN "
        "    UPDATE platform.runs SET cancel_requested_at = now() "
        "     WHERE id = NEW.run_id AND cancel_requested_at IS NULL; "
        "  END IF; "
        "  RETURN NEW; "
        "END $$ LANGUAGE plpgsql",
    )
    st.sql(
        dbname,
        "CREATE TRIGGER t042_cancel_after_heartbeat AFTER UPDATE ON platform.run_attempts "
        "FOR EACH ROW EXECUTE FUNCTION t042_request_cancel()",
    )
