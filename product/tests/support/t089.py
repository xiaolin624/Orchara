"""F04B/T089 real-stack helpers: call_app nodes inside a proven graph region.

A call_app node is only executable through the F04B graph scheduler when the
sealed definition also carries a structured region (a condition or a join); a
plain start -> call_app -> end definition keeps the linear path. Every fixture
here therefore wraps the delegation in a real region:

* ``single_child_definition``   condition yes -> child, no -> skipped direct
* ``parallel_definition``       parallel fork -> a / b -> join (two children)
* ``same_child_twice_definition`` condition yes -> first -> second -> join

Each child is a real published child application version (the T075 fixture:
real ``POST /validate`` -> sealed canonical manifest -> version row) and every
child Run is created by the contract ``spawnChild`` endpoint from the compiled
``cmd/supervisor exec`` binary, never by test SQL.

The input mapping is always explicit and minimal: a ``literal`` or an
``input#/...`` reference; nothing from the parent input or history is added by
the platform.
"""

from __future__ import annotations

import json

from support import t027, t029, t033, t075, t078
from support import stack as st

CHILD_VERSION_ECHO = {
    "type": "object",
    "properties": {"echo": {"type": "string"}},
    "required": ["echo"],
    "additionalProperties": False,
}

RESULTS_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "object"}},
    "required": ["results"],
    "additionalProperties": False,
}

FLAG_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"requires_approval": {"type": "boolean"}},
    "required": ["requires_approval"],
    "additionalProperties": False,
}

SOURCE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"source": {"type": "string"}},
    "required": ["source"],
    "additionalProperties": True,
}

ANY_INPUT_SCHEMA = {"type": "object", "additionalProperties": True}

POLICY = dict(t075.POLICY)


# ---------------------------------------------------------------------------
# node builders
# ---------------------------------------------------------------------------
def call_app_node(
    node_id: str,
    version_id: str,
    *,
    inputs: dict | None = None,
    output_schema: dict | None = None,
) -> dict:
    return {
        "id": node_id,
        "type": "call_app",
        "inputs": inputs if inputs is not None else {"source": {"literal": node_id}},
        "output_schema": json.loads(json.dumps(output_schema or CHILD_VERSION_ECHO)),
        "config": {"application_version": version_id},
    }


def condition_node(
    node_id: str,
    *,
    join_id: str,
    cases: list[dict],
    default_port: str = "no",
    input_name: str = "requires_approval",
) -> dict:
    return {
        "id": node_id,
        "type": "condition",
        "inputs": {input_name: {"ref": f"input#/{input_name}"}},
        "config": {"join_id": join_id, "default_port": default_port, "cases": cases},
        "output_schema": {"type": "object"},
    }


def join_node(
    node_id: str,
    *,
    fork_id: str,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
) -> dict:
    return {
        "id": node_id,
        "type": "join",
        "inputs": {},
        "config": {
            "mode": "all_active",
            "fork_id": fork_id,
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
        "input_schema": json.loads(json.dumps(input_schema)),
        "output_schema": json.loads(json.dumps(RESULTS_OUTPUT_SCHEMA)),
        "nodes": nodes,
        "edges": edges,
        "policy": dict(POLICY),
    }


# ---------------------------------------------------------------------------
# acceptance fixtures
# ---------------------------------------------------------------------------
def single_child_definition(child_version_id: str) -> dict:
    """start -> choose --yes--> child / --no--> direct -> join -> end.

    ``direct`` is a second call_app declaration on the unselected port: the
    scheduler marks it skipped, so exactly one child Run is created and the join
    envelope records the other branch as skipped.
    """
    nodes = [
        t027.start_node(),
        condition_node(
            "choose",
            join_id="join",
            cases=[{"input": "requires_approval", "op": "eq", "value": True, "port": "yes"}],
        ),
        call_app_node("child", child_version_id, inputs={"source": {"literal": "alpha"}}),
        call_app_node("direct", child_version_id, inputs={"source": {"literal": "unused"}}),
        join_node("join", fork_id="choose", required=("child", "direct")),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "child", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "child", "target": "join"},
        {"source": "direct", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, FLAG_INPUT_SCHEMA)


def parallel_definition(child_version_id: str) -> dict:
    """start forks onto call_app a and call_app b, both converging at one join.

    Both nodes pin the same fixed child version but carry different invocation
    keys (derived from the node id) and different literal inputs, so the platform
    must create two independent child Runs and never dedupe by application id.
    """
    nodes = [
        t027.start_node(),
        call_app_node("a", child_version_id, inputs={"source": {"literal": "alpha"}}),
        call_app_node("b", child_version_id, inputs={"source": {"literal": "beta"}}),
        join_node("join", fork_id="start", required=("a", "b")),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "a"},
        {"source": "start", "target": "b"},
        {"source": "a", "target": "join"},
        {"source": "b", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, ANY_INPUT_SCHEMA)


def same_child_twice_definition(child_version_id: str) -> dict:
    """Two sequential call_app nodes pinning the same child version.

    Both nodes live on the selected condition port: the first child completes,
    the parent waits again for the second one. Two child Runs with two distinct
    invocation keys must exist; an application-id dedupe would collapse them.
    """
    nodes = [
        t027.start_node(),
        condition_node(
            "choose",
            join_id="join",
            cases=[{"input": "requires_approval", "op": "eq", "value": True, "port": "yes"}],
        ),
        call_app_node("first", child_version_id, inputs={"source": {"literal": "first"}}),
        call_app_node("second", child_version_id, inputs={"source": {"literal": "second"}}),
        call_app_node("direct", child_version_id, inputs={"source": {"literal": "unused"}}),
        join_node("join", fork_id="choose", required=("second", "direct")),
        graph_end(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "first", "port": "yes"},
        {"source": "first", "target": "second"},
        {"source": "second", "target": "join"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "direct", "target": "join"},
        {"source": "join", "target": "end"},
    ]
    return _definition(nodes, edges, FLAG_INPUT_SCHEMA)


def defective_binding_definition(child_version_id: str) -> dict:
    """A passing publication whose binding cannot resolve at run time.

    ``input#/source`` is a documented pointer in the parent's input_schema, but
    the field is optional, so a run that omits it makes the executor's own
    binding resolution fail (the publish gate cannot see values).
    """
    definition = single_child_definition(child_version_id)
    definition["input_schema"] = {
        "type": "object",
        "properties": {"requires_approval": {"type": "boolean"}, "source": {"type": "string"}},
        "required": ["requires_approval"],
        "additionalProperties": False,
    }
    for node in definition["nodes"]:
        if node["id"] == "child":
            node["inputs"] = {"source": {"ref": "input#/source"}}
    return definition


def mapping_type_definition(child_version_id: str) -> dict:
    """A definition whose call_app mapping is refused by the static type gate.

    The child's frozen ``source`` input is a string; the node binds it to the
    parent's integer ``count`` input. Publication must locate
    ``io_mapping_type_mismatch`` at ``/nodes/2/inputs/source``.
    """
    definition = single_child_definition(child_version_id)
    definition["input_schema"] = {
        "type": "object",
        "properties": {"requires_approval": {"type": "boolean"}, "count": {"type": "integer"}},
        "required": ["requires_approval"],
        "additionalProperties": False,
    }
    definition["nodes"][2] = call_app_node(
        "child", child_version_id, inputs={"source": {"ref": "input#/count"}}
    )
    return definition


def mapping_missing_definition(child_version_id: str) -> dict:
    """A definition that does not map a required child input.

    Use after creating a stricter child version (``strict_child_definition``):
    publication must locate ``io_mapping_missing_field`` at
    ``/nodes/2/inputs/region`` (and the same error for the skipped ``direct``
    declaration, which the gate still checks).
    """
    definition = single_child_definition(child_version_id)
    definition["nodes"][2] = call_app_node(
        "child", child_version_id, inputs={"source": {"literal": "alpha"}}
    )
    definition["nodes"][3] = call_app_node(
        "direct", child_version_id, inputs={"source": {"literal": "unused"}}
    )
    return definition


def strict_child_definition() -> dict:
    """The echo child with a stricter frozen input schema (requires ``region``)."""
    definition = t078.child_definition()
    definition["input_schema"]["properties"]["region"] = {"type": "string"}
    definition["input_schema"]["required"] = ["source", "region"]
    return definition


# ---------------------------------------------------------------------------
# submission and driver helpers
# ---------------------------------------------------------------------------
def submit_parent(stack, creator, definition: dict, input_value: dict) -> dict:
    """Create one real debug parent Run (draft save + debug submission)."""
    application_id = t027.application_with_definition(creator, definition)
    revision = t027.draft_revision(creator, application_id)
    response = t027.submit_debug(
        creator, application_id, draft_revision=revision, input_value=input_value
    )
    assert response.status == 202, response.text
    return response.json


def execute_once(stack, **env_kwargs):
    """One real ``cmd/supervisor exec --once`` with the sealed object root wired.

    The F04B recovery seam (``presetexec.Deps.NodeOutputs`` -> ``dbAccess.NodeData``)
    opens node data from the supervisor's ``ORCHARA_OBJECT_DIR``. The supervisor
    deployment always needs that root (personal-memory recall uses it too); the
    T033 helper does not set it, so every T089 attempt supplies it explicitly. A
    run without it degrades to re-executing completed nodes (the documented
    fail-open of the seam), which would hide the reuse assertions.
    """
    extra = dict(env_kwargs.pop("extra", {}) or {})
    extra.setdefault("ORCHARA_OBJECT_DIR", str(stack.object_dir))
    return t078.execute_once(stack, extra=extra, **env_kwargs)


def wait_resume(stack, parent_run_id: str, *, child_run_id: str | None = None, timeout: float = 20.0):
    """Poll the durable waiting row until the one resume job exists."""
    from support import t081

    def ready():
        row = t081.waiting_row(stack.dbname, parent_run_id)
        if not row or not row.get("resume_job_id"):
            return None
        if child_run_id is not None and row.get("checkpoint_id") != "child:" + child_run_id:
            return None
        return row

    return t081.wait_for(ready, timeout=timeout)


def wait_decision(stack, parent_run_id: str, *, timeout: float = 20.0):
    from support import t081

    return t081.wait_for(
        lambda: t081.waiting_decision(stack.dbname, parent_run_id), timeout=timeout
    )


def fail_child_with_bad_output(
    stack, child_run_id: str, lease: dict, *, execution_unit_id: str = "unit-t081"
) -> None:
    """Drive a real child to failed through the real finalizer.

    The proposal and exit are the documented internal protocol; the bytes are
    test-owned, and the child's frozen output_schema rejects them
    (``child_output_schema_invalid``), which is the child's stable failure code.
    """
    artifacts = t033.artifact_client(stack, "worker")
    artifact_id, _sha = t033.upload_run_artifact(
        stack,
        artifacts,
        lease,
        content=b'{"bogus":1}',
        purpose="final_output",
        media_type="application/json",
        relation="output",
    )
    supervisor = t029.execution_client(stack, "supervisor")
    proposed = supervisor.submit_result(
        child_run_id,
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id=execution_unit_id,
        evidence=t029.exit_evidence(lease),
    )
    assert exited.status == 200, exited.text


def children(stack, session, parent_run_id: str) -> list[dict]:
    response = session.client.get(f"/api/v1/runs/{parent_run_id}/children")
    assert response.status == 200, response.text
    return response.json["items"]


def child_output(stack, session, child_run_id: str) -> dict:
    row = t078.run_row(stack.dbname, child_run_id)
    assert row["output_artifact_id"], row
    content = t033.artifact_content(session, row["output_artifact_id"])
    assert content.status == 200, content.text
    return json.loads(content.raw)


def node_results(session, run_id: str, *, expected: int) -> list[dict]:
    from support import t042

    return t042.wait_node_results(session, run_id, expected=expected)["items"]


def node_data_document(session, items: list[dict], node_id: str) -> dict:
    from support import t083

    return t083.node_data_document(session, items, node_id)


def node_states(dbname: str, run_id: str) -> dict[str, list[str]]:
    from support import t083

    return t083.node_states(dbname, run_id)


__all__ = [name for name in dir() if not name.startswith("_")]
