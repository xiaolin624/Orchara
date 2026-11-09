"""F04A/T043 real-stack helpers: the four-node start -> python -> agent -> end run.

Everything goes through the product's real path: a real application draft, the
real debug submission (sealed run input + linked task_input artifact), the
compiled ``cmd/supervisor exec --once`` claiming the attempt, the real T041
capability proxy (worker mTLS + the run's live attempt token), the real
WSL2/runsc sandbox for both the python node and the agent's preset tool call, the
non-contract node-results report, and the owner-only reads.

The model provider is the documented deterministic dev stub (``provider=stub``,
labelled stub in the ledger); the tool execution is real.
"""

from __future__ import annotations

import json

from support import execution_support as execution
from support import stack as st
from support import t025, t027, t033

PRESET_TOOL_ID = t025.GOLDEN_TOOL_ID
GOLDEN_MODEL_ID = t025.GOLDEN_MODEL_ID

AGENT_TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

INPUT_SCHEMA = {
    "type": "object",
    "properties": {"source": {"type": "string"}},
    "required": ["source"],
    "additionalProperties": False,
}


def agent_definition(
    *,
    instructions: str = "Summarise the validated sales statistics.",
    max_turns: int = 3,
    tool_versions: list[str] | None = None,
    model_profile_version: str = GOLDEN_MODEL_ID,
    agent_output_schema: dict | None = None,
    python_output_schema: dict | None = None,
    output_schema: dict | None = None,
    end_inputs: dict | None = None,
    name: str = "t043 agent",
) -> dict:
    schema = agent_output_schema or AGENT_TEXT_SCHEMA
    python_node = {
        "id": "stats",
        "type": "python",
        "inputs": {"file": {"ref": "node:start#/artifact"}},
        "output_schema": python_output_schema or t042_output_schema(),
        "config": {"tool_version": PRESET_TOOL_ID},
    }
    agent_node = {
        "id": "analysis",
        "type": "agent",
        "inputs": {"stats": {"ref": "node:stats#"}},
        "output_schema": schema,
        "config": {
            "model_profile_version": model_profile_version,
            "instructions": instructions,
            "max_turns": max_turns,
            "tool_versions": list(tool_versions if tool_versions is not None else [PRESET_TOOL_ID]),
            "subapp_versions": [],
        },
    }
    end_node = {
        "id": "end",
        "type": "end",
        "inputs": end_inputs or {"text": {"ref": "node:analysis#/text"}},
        "output_schema": {"type": "object"},
        "config": {},
    }
    return {
        "dsl_version": "1.0",
        "input_schema": INPUT_SCHEMA,
        "output_schema": output_schema or AGENT_TEXT_SCHEMA,
        "nodes": [t027.start_node(), python_node, agent_node, end_node],
        "edges": [
            {"source": "start", "target": "stats"},
            {"source": "stats", "target": "analysis"},
            {"source": "analysis", "target": "end"},
        ],
        "policy": dict(t027.BASE_POLICY),
    }


def t042_output_schema() -> dict:
    """The preset sales_stats output shape (T042's node schema)."""
    return {
        "type": "object",
        "properties": {
            "total": {"type": "string"},
            "regions": {"type": "object", "additionalProperties": {"type": "string"}},
            "order_count": {"type": "integer"},
        },
        "required": ["total", "regions", "order_count"],
        "additionalProperties": False,
    }


def application_for(owner, definition: dict, *, name: str = "t043 agent") -> str:
    return t027.application_with_definition(owner.creator, definition, name=name)


def submit_agent_run(
    owner,
    application_id: str,
    *,
    artifact_id: str | None = None,
    input_value: dict | None = None,
):
    revision = t027.draft_revision(owner.creator, application_id)
    response = t027.submit_debug(
        owner.creator,
        application_id,
        draft_revision=revision,
        input_value=input_value if input_value is not None else {"source": "t043"},
        artifact_ids=[artifact_id] if artifact_id else [],
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run


def ready_csv(stack, owner, *, content: bytes = t033.SALES_CSV) -> str:
    return t033.upload_artifact(stack, owner.creator, content)


def run_agent_once(stack, *, env_extra: dict[str, str] | None = None):
    env = execution.supervisor_exec_env(stack)
    if env_extra:
        env.update(env_extra)
    with execution.FinalizeWorker(stack):
        completed = execution.run_supervisor_exec(stack, env)
    return completed, execution.exec_report(completed)


def queue_agent_run(stack, *, definition: dict | None = None, input_value: dict | None = None):
    owner = t033.golden_owner(stack)
    application_id = application_for(owner, definition or agent_definition())
    csv_id = ready_csv(stack, owner)
    run = submit_agent_run(owner, application_id, artifact_id=csv_id, input_value=input_value)
    return owner, run, csv_id


# ---------------------------------------------------------------------------
# capability / node reads
# ---------------------------------------------------------------------------
def set_tool_state(dbname: str, state: str, *, tool_id: str = PRESET_TOOL_ID) -> None:
    """Change the tool row state between manifest compile and execution.

    ``state`` must be one of the migration's closed vocabulary
    (submitted/building/ready/failed/revoked).
    """
    st.sql(
        dbname,
        f"UPDATE platform.tool_versions SET state = {st.quote_literal(state)} "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )


def request_cancel_on_first_operation(dbname: str) -> None:
    """Cancel the run on its first capability operation row (mid agent loop).

    A test-owned trigger; the product has no such hook. It fires only on
    platform.operations inserts, so the python node completes and the cancel is
    observed between the agent's first model turn and its tool grant.
    """
    st.sql(
        dbname,
        "CREATE OR REPLACE FUNCTION t043_cancel_on_operation() RETURNS trigger AS $$ "
        "BEGIN "
        "  UPDATE platform.runs SET cancel_requested_at = now() "
        "   WHERE id = NEW.run_id AND cancel_requested_at IS NULL; "
        "  RETURN NEW; "
        "END $$ LANGUAGE plpgsql",
    )
    st.sql(
        dbname,
        "CREATE TRIGGER t043_cancel_on_operation AFTER INSERT ON platform.operations "
        "FOR EACH ROW EXECUTE FUNCTION t043_cancel_on_operation()",
    )


def drop_cancel_trigger(dbname: str) -> None:
    st.sql(dbname, "DROP TRIGGER IF EXISTS t043_cancel_on_operation ON platform.operations")
    st.sql(dbname, "DROP FUNCTION IF EXISTS t043_cancel_on_operation()")


def node_events(dbname: str, run_id: str) -> list[dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT seq, type, payload::text AS payload FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid AND type = 'node.status' "
        "ORDER BY seq",
    )
    for row in rows:
        row["payload"] = json.loads(row["payload"])
    return rows


def node_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT o.node_id, o.kind, o.artifact_id::text AS artifact_id, o.label, "
        "a.purpose, a.state, a.media_type "
        "FROM platform.run_node_outputs AS o "
        "JOIN platform.artifacts AS a ON a.id = o.artifact_id "
        f"WHERE o.run_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY o.node_id, o.kind, o.label",
    )
