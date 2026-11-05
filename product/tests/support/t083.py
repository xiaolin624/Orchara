"""F04B/T083 real-stack helpers: structured condition/join graphs.

The definitions here are the T083 acceptance shapes. They are published through
the real draft API, executed by the compiled ``cmd/supervisor exec --once`` in
WSL2/runsc, and read back through the owner's real HTTP surface.

Only the fixed preset python tool is used (the one this build can execute) so the
branching proof is a real execution, not a model stub: both branches bind the same
run task_input artifact and each selected branch really runs the tool.
"""

from __future__ import annotations

import json

from support import stack as st
from support import t027, t032, t033, t042

PRESET_TOOL_ID = t042.PRESET_TOOL_ID

FLAG_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"requires_approval": {"type": "boolean"}},
    "required": ["requires_approval"],
    "additionalProperties": False,
}

MODE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string"}},
    "required": ["mode"],
    "additionalProperties": False,
}

ANY_INPUT_SCHEMA = {"type": "object"}

RESULTS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "object"}},
    "required": ["results"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# definition builders
# ---------------------------------------------------------------------------
def python_node(node_id: str) -> dict:
    return {
        "id": node_id,
        "type": "python",
        "inputs": {"file": {"ref": "node:start#/artifact"}},
        "output_schema": dict(t042.PYTHON_OUTPUT_SCHEMA),
        "config": {"tool_version": PRESET_TOOL_ID},
    }


def condition_node(
    node_id: str,
    *,
    join_id: str,
    input_name: str,
    cases: list[dict],
    default_port: str,
) -> dict:
    return {
        "id": node_id,
        "type": "condition",
        "inputs": {input_name: {"ref": f"input#/{input_name}"}},
        "config": {"join_id": join_id, "default_port": default_port, "cases": cases},
        "output_schema": {"type": "object"},
    }


def join_node(
    node_id: str, *, fork_id: str, required: tuple[str, ...], optional: tuple[str, ...] = ()
) -> dict:
    return {
        "id": node_id,
        "type": "join",
        "inputs": {},
        "config": {
            "fork_id": fork_id,
            "mode": "all_active",
            "required_sources": list(required),
            "optional_sources": list(optional),
        },
        "output_schema": {"type": "object"},
    }


def graph_end() -> dict:
    node = t027.end_node()
    node["inputs"] = {"results": {"ref": "node:join#/results"}}
    return node


def _definition(nodes: list[dict], edges: list[dict], input_schema: dict) -> dict:
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema,
        "output_schema": dict(RESULTS_OUTPUT_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "policy": dict(t027.BASE_POLICY),
    }


def branch_definition(
    *, required: tuple[str, ...] = ("left", "right"), optional: tuple[str, ...] = ()
) -> dict:
    """start -> choose --yes--> left / --no--> right -> join -> end."""
    choose_cases = [{"input": "requires_approval", "op": "eq", "value": True, "port": "yes"}]
    nodes = [
        t027.start_node(),
        condition_node(
            "choose",
            join_id="join",
            input_name="requires_approval",
            cases=choose_cases,
            default_port="no",
        ),
        python_node("left"),
        python_node("right"),
        join_node("join", fork_id="choose", required=required, optional=optional),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "left", "port": "yes"},
        {"source": "choose", "target": "right", "port": "no"},
        {"source": "left", "target": "join"},
        {"source": "right", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, FLAG_INPUT_SCHEMA)


def parallel_definition() -> dict:
    """start forks onto two python branches that converge at one join."""
    nodes = [
        t027.start_node(),
        python_node("left"),
        python_node("right"),
        join_node("join", fork_id="start", required=("left", "right")),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "right"},
        {"source": "left", "target": "join"},
        {"source": "right", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, ANY_INPUT_SCHEMA)


def nested_definition() -> dict:
    """A condition region nested inside another condition region's "yes" branch."""
    nodes = [
        t027.start_node(),
        condition_node(
            "choose",
            join_id="join",
            input_name="mode",
            cases=[{"input": "mode", "op": "in", "value": ["nested", "deep"], "port": "yes"}],
            default_port="no",
        ),
        condition_node(
            "choose2",
            join_id="join2",
            input_name="mode",
            cases=[{"input": "mode", "op": "eq", "value": "deep", "port": "yes"}],
            default_port="no",
        ),
        python_node("deep"),
        python_node("shallow"),
        join_node("join2", fork_id="choose2", required=("deep", "shallow")),
        python_node("direct"),
        join_node("join", fork_id="choose", required=("join2", "direct")),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "choose2", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "choose2", "target": "deep", "port": "yes"},
        {"source": "choose2", "target": "shallow", "port": "no"},
        {"source": "deep", "target": "join2"},
        {"source": "shallow", "target": "join2"},
        {"source": "join2", "target": "join"},
        {"source": "direct", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, MODE_INPUT_SCHEMA)


def escaping_definition() -> dict:
    """A third branch of the inner parallel fork escapes the outer join.

    Publish must locate this as cross_region_edge at /edges/10.
    """
    definition = nested_definition()
    definition["nodes"].append(
        {
            "id": "escapee",
            "type": "python",
            "inputs": {},
            "output_schema": {"type": "object"},
            "config": {"tool_version": PRESET_TOOL_ID},
        }
    )
    definition["edges"].append({"source": "choose2", "target": "escapee", "port": "no"})
    definition["edges"].append({"source": "escapee", "target": "join"})
    return definition


# ---------------------------------------------------------------------------
# submission
# ---------------------------------------------------------------------------
def application_for(stack, definition: dict, *, name: str = "t083 graph"):
    owner = t033.golden_owner(stack)
    application_id = t027.application_with_definition(owner.creator, definition, name=name)
    return owner, application_id


def queue_graph_run(
    stack,
    definition: dict,
    *,
    input_value: dict,
    csv: bool = True,
    name: str = "t083 graph",
):
    owner, application_id = application_for(stack, definition, name=name)
    artifact_id = t042.ready_csv(stack, owner) if csv else None
    run = t042.submit_linear_run(
        owner, application_id, artifact_id=artifact_id, input_value=input_value
    )
    return owner, application_id, run


def run_once(stack, *, env_extra: dict[str, str] | None = None):
    return t042.run_linear_once(stack, env_extra=env_extra)


# ---------------------------------------------------------------------------
# owner reads and database assertions
# ---------------------------------------------------------------------------
def node_results(session, run_id: str, *, expected: int) -> list[dict]:
    body = t042.wait_node_results(session, run_id, expected=expected)
    return body["items"]


def node_data_rows(items: list[dict], node_id: str) -> list[dict]:
    return [item for item in items if item["node_id"] == node_id and item["kind"] == "data"]


def node_data_document(session, items: list[dict], node_id: str) -> dict:
    rows = node_data_rows(items, node_id)
    assert len(rows) == 1, (node_id, rows)
    content = t033.artifact_content(session, rows[0]["artifact_id"])
    assert content.status == 200, content.text
    return json.loads(content.raw)


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    """The durable node.status sequence, from the run_events projection."""
    states: dict[str, list[str]] = {}
    for event in t042.node_events(dbname, run_id):
        payload = event["payload"]
        states.setdefault(payload["node_id"], []).append(payload["state"])
    return states


def sse_node_states(stack, session, run_id: str) -> dict[str, list[str]]:
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


def final_output_document(session, dbname: str, run_id: str) -> dict:
    artifact_id = t033.output_artifact_id(dbname, run_id)
    assert artifact_id, "the run sealed no final output"
    content = t033.artifact_content(session, artifact_id)
    assert content.status == 200, content.text
    return json.loads(content.raw)


def count_jobs_for_application(dbname: str, application_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.jobs AS j "
            "JOIN platform.runs AS r ON r.id = j.run_id "
            "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
            f"WHERE s.application_id = {st.quote_literal(application_id)}::uuid",
        )
    )


def count_runs_for_application(dbname: str, application_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.runs AS r "
            "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
            f"WHERE s.application_id = {st.quote_literal(application_id)}::uuid",
        )
    )
