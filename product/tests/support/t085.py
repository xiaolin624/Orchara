"""F04B/T085 real-stack helpers: parallel regions, optional/required branches.

The definitions here are the T085 acceptance shapes. They are published through
the real draft API (so the Go workflow validator proves the regions), executed by
the compiled ``cmd/supervisor exec --once`` in WSL2/runsc, and read back through
the owner's real HTTP surface.

Only the fixed preset python tool is used for the data branches so the parallel
proof is a real execution (two runsc units at the same time), not a model stub.
The agent branch of ``parallel_agent_definition`` uses the seeded golden model
profile; its failure case revokes that profile after the run is queued, which is a
capability/authorization failure the optional branch must never absorb.
"""

from __future__ import annotations

from support import stack as st
from support import t025, t027, t042, t083

PRESET_TOOL_ID = t042.PRESET_TOOL_ID
GOLDEN_MODEL_ID = t025.GOLDEN_MODEL_ID

AGENT_TEXT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

# A well-formed node output schema the fixed preset tool's data can never satisfy:
# the required key is not produced. The node fails with node_output_invalid after
# its sandbox ran, which is an explicit pure-compute error.
FAILING_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": ["never_produced"],
    "additionalProperties": True,
}

MODE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string"}},
    "required": ["mode"],
    "additionalProperties": False,
}


def _python(node_id: str, output_schema: dict | None = None) -> dict:
    node = t083.python_node(node_id)
    if output_schema is not None:
        node["output_schema"] = dict(output_schema)
    return node


def _definition(nodes: list[dict], edges: list[dict], input_schema: dict) -> dict:
    return {
        "dsl_version": "1.0",
        "input_schema": input_schema,
        "output_schema": dict(t083.RESULTS_OUTPUT_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "policy": dict(t027.BASE_POLICY),
    }


def parallel_definition(*, right_schema: dict | None = None) -> dict:
    """start forks onto two python branches that converge at one join."""
    nodes = [
        t027.start_node(),
        _python("left"),
        _python("right", right_schema),
        t083.join_node("join", fork_id="start", required=("left", "right")),
        t083.graph_end(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "right"},
        {"source": "left", "target": "join"},
        {"source": "right", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, t083.ANY_INPUT_SCHEMA)


def optional_failure_definition() -> dict:
    """left required, right optional, and right's output schema rejects the data."""
    nodes = [
        t027.start_node(),
        _python("left"),
        _python("right", FAILING_OUTPUT_SCHEMA),
        t083.join_node("join", fork_id="start", required=("left",), optional=("right",)),
        t083.graph_end(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "right"},
        {"source": "left", "target": "join"},
        {"source": "right", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, t083.ANY_INPUT_SCHEMA)


def required_failure_definition() -> dict:
    """Both branches are required and right's node fails after its sandbox."""
    return parallel_definition(right_schema=FAILING_OUTPUT_SCHEMA)


def parallel_agent_definition() -> dict:
    """start forks onto a python branch and an optional guarded agent branch.

    The optional join source is ``analysis_out`` (a pure python node) because
    publish validation rejects an optional source that names an agent directly.
    A failure inside the branch still belongs to that source, so an agent
    capability failure remains non-convergent (see the runtime allowlist).
    """
    agent = {
        "id": "analysis",
        "type": "agent",
        "inputs": {"artifact": {"ref": "node:start#/artifact"}},
        "output_schema": dict(AGENT_TEXT_SCHEMA),
        "config": {
            "model_profile_version": GOLDEN_MODEL_ID,
            "instructions": "Summarise the validated sales statistics.",
            "max_turns": 2,
            "tool_versions": [PRESET_TOOL_ID],
            "subapp_versions": [],
        },
    }
    nodes = [
        t027.start_node(),
        _python("left"),
        agent,
        _python("analysis_out"),
        t083.join_node(
            "join", fork_id="start", required=("left",), optional=("analysis_out",)
        ),
        t083.graph_end(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "analysis"},
        {"source": "analysis", "target": "analysis_out"},
        {"source": "left", "target": "join"},
        {"source": "analysis_out", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, t083.ANY_INPUT_SCHEMA)


def direct_optional_agent_definition() -> dict:
    """An agent branch declared optional directly: now refused at publish time."""
    agent = {
        "id": "analysis",
        "type": "agent",
        "inputs": {"artifact": {"ref": "node:start#/artifact"}},
        "output_schema": dict(AGENT_TEXT_SCHEMA),
        "config": {
            "model_profile_version": GOLDEN_MODEL_ID,
            "instructions": "Summarise the validated sales statistics.",
            "max_turns": 2,
            "tool_versions": [PRESET_TOOL_ID],
            "subapp_versions": [],
        },
    }
    nodes = [
        t027.start_node(),
        _python("left"),
        agent,
        t083.join_node(
            "join", fork_id="start", required=("left",), optional=("analysis",)
        ),
        t083.graph_end(),
    ]
    edges = [
        {"source": "start", "target": "left"},
        {"source": "start", "target": "analysis"},
        {"source": "left", "target": "join"},
        {"source": "analysis", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, t083.ANY_INPUT_SCHEMA)


def parallel_in_condition_definition() -> dict:
    """A parallel fork nested inside a condition's "yes" branch.

    start -> choose --yes--> fork -> (deepA|deepB) -> join2 -> join -> end
                 --no-----> direct ---------------------> join
    """
    nodes = [
        t027.start_node(),
        t083.condition_node(
            "choose",
            join_id="join",
            input_name="mode",
            cases=[{"input": "mode", "op": "eq", "value": "deep", "port": "yes"}],
            default_port="no",
        ),
        _python("fork"),
        _python("deepA"),
        _python("deepB"),
        t083.join_node("join2", fork_id="fork", required=("deepA", "deepB")),
        _python("direct"),
        t083.join_node("join", fork_id="choose", required=("join2", "direct")),
        t083.graph_end(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "fork", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "fork", "target": "deepA"},
        {"source": "fork", "target": "deepB"},
        {"source": "deepA", "target": "join2"},
        {"source": "deepB", "target": "join2"},
        {"source": "join2", "target": "join"},
        {"source": "direct", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, MODE_INPUT_SCHEMA)


# ---------------------------------------------------------------------------
# submission and reads (reuse the T083 real-stack path)
# ---------------------------------------------------------------------------
queue_graph_run = t083.queue_graph_run


def revoke_golden_model(dbname: str) -> None:
    """Revoke the seeded model profile between manifest compile and execution."""
    st.sql(
        dbname,
        "UPDATE platform.model_profile_versions SET revoked_at = now() "
        f"WHERE id = {st.quote_literal(GOLDEN_MODEL_ID)}::uuid",
    )
