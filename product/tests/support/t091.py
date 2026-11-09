"""F04B/T091 real-stack helpers: the ten-node workflow, two approvals, nested joins.

Everything here runs on the shared F04B/F06 stack (throwaway PostgreSQL migrated
through the real chain, the compiled ``cmd/api`` behind real HTTP, the real mTLS
internal listener, the real object store, the compiled ``cmd/supervisor exec
--once`` and WSL2/runsc). Nothing is mocked:

* the ten-node fixture is saved through the public draft API and validated by the
  real workflow compiler;
* the two-approval acceptance graph is submitted as a real debug run and executed
  by the real supervisor in separate processes after each approval decision;
* the fixed dependencies are real: the seeded golden model profile, the seeded
  preset python tool and a real published child application version created
  through the T078 producer;
* the knowledge base is a real owner-visible row (the retrieve node is validated
  only in the ten-node fixture; no document is ingested because the fixture is
  not executed).

The graph under test (T091 brief §2.3, adjusted per its "具体可调整" note):

    start -> choose(condition) --yes--> approve1(approval) --> fork(python)
                     |                                          |--> py(python, optional) --> join2
                     |                                          |--> agent(agent)        --> join2
                     |--no--> direct(python) --> join1 <---------------- join2 ----------+
    join1 -> approve2(approval) -> summarise(model) -> end

``choose``/``join1`` is the outer condition region; ``fork``/``join2`` is the
parallel region nested inside its yes branch. The end node reads the outer join
envelope **by source** (``node:join1#/results/join2/value`` and
``node:join1#/results``) plus the second approval and the model output.
"""

from __future__ import annotations

import json
from pathlib import Path

from support import t025, t027, t035, t042, t078, t083, t087, t088

GOLDEN_MODEL_ID = t025.GOLDEN_MODEL_ID
PRESET_TOOL_ID = t042.PRESET_TOOL_ID

FIXTURES = Path(__file__).resolve().parents[1] / "f04b" / "fixtures"
TEN_NODE_DEFINITION = FIXTURES / "ten_node_definition.json"
TEN_NODE_LAYOUT = FIXTURES / "ten_node_layout.json"

CHILD_PLACEHOLDER = "__CHILD_VERSION__"
KB_PLACEHOLDER = "__KNOWLEDGE_BASE_ID__"

AGENT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": False,
}

MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}

# A well-formed node output schema the fixed preset tool's data can never satisfy:
# the required key is not produced, so the node fails after its real sandbox ran
# (an explicit pure-compute error the optional branch may absorb).
FAILING_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {},
    "required": ["never_produced"],
    "additionalProperties": True,
}

ACCEPTANCE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "join1": {"type": "object"},
        "join2": {"type": "object"},
        "second_approval": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["join1", "join2", "second_approval", "summary"],
    "additionalProperties": False,
}

ACCEPTANCE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "requires_approval": {"type": "boolean"},
        "task": {"type": "string"},
    },
    "required": ["requires_approval", "task"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# the checked-in ten-node fixture, materialised with real dependency ids
# ---------------------------------------------------------------------------
def ten_node_definition(stack, owner) -> tuple[dict, dict, dict]:
    """Load the fixture and substitute the real child version / knowledge base.

    The committed fixture documents the ten-node document itself; the two
    placeholders are dependency ids that only exist in a live database (a
    published child version and an owner knowledge base), so the test creates
    them through the real product paths and substitutes them. Model profile and
    python tool ids are the fixed seeded ids and are written in the fixture.
    """
    definition = json.loads(TEN_NODE_DEFINITION.read_text(encoding="utf-8"))
    layout = json.loads(TEN_NODE_LAYOUT.read_text(encoding="utf-8"))
    child = t078.create_child_version(stack, owner.creator)
    base_id = t088.create_knowledge_base(owner.creator, "t091 ten-node knowledge")
    text = json.dumps(definition, ensure_ascii=False)
    assert CHILD_PLACEHOLDER in text and KB_PLACEHOLDER in text
    text = text.replace(CHILD_PLACEHOLDER, child["id"]).replace(KB_PLACEHOLDER, base_id)
    materialised = json.loads(text)
    assert CHILD_PLACEHOLDER not in json.dumps(materialised)
    assert KB_PLACEHOLDER not in json.dumps(materialised)
    return materialised, layout, {
        "child_version_id": child["id"],
        "child_application_id": child["application_id"],
        "knowledge_base_id": base_id,
    }


def fixture_node_types(definition: dict) -> set[str]:
    return {node["type"] for node in definition["nodes"]}


# ---------------------------------------------------------------------------
# the two-approval / nested-join acceptance definition
# ---------------------------------------------------------------------------
def _python(node_id: str, *, output_schema: dict | None = None) -> dict:
    node = t083.python_node(node_id)
    if output_schema is not None:
        node["output_schema"] = dict(output_schema)
    return node


def _approval(node_id: str, title: str, proposal: dict) -> dict:
    return t087.approval_node(node_id, title=title, proposal=proposal)


def _condition(node_id: str, *, join_id: str) -> dict:
    return t083.condition_node(
        node_id,
        join_id=join_id,
        input_name="requires_approval",
        cases=[{"input": "requires_approval", "op": "eq", "value": True, "port": "yes"}],
        default_port="no",
    )


def _agent_node(node_id: str, *, artifact_ref: str = "node:start#/artifact") -> dict:
    return {
        "id": node_id,
        "type": "agent",
        "inputs": {"artifact": {"ref": artifact_ref}},
        "output_schema": dict(AGENT_OUTPUT_SCHEMA),
        "config": {
            "model_profile_version": GOLDEN_MODEL_ID,
            "instructions": "Summarise the validated sales statistics in one line.",
            "max_turns": 2,
            "tool_versions": [],
            "subapp_versions": [],
        },
    }


def _model_node(node_id: str) -> dict:
    return {
        "id": node_id,
        "type": "model",
        "inputs": {"task": {"ref": "input#/task"}},
        "output_schema": dict(MODEL_OUTPUT_SCHEMA),
        "config": {
            "model_profile_version": GOLDEN_MODEL_ID,
            "prompt_template": "Summarize {{task}}",
            "response_mode": "json",
        },
    }


def _end_node() -> dict:
    node = t027.end_node()
    node["inputs"] = {
        "join1": {"ref": "node:join1#/results"},
        "join2": {"ref": "node:join1#/results/join2/value"},
        "second_approval": {"ref": "node:approve2#/approval_id"},
        "summary": {"ref": "node:summarise#/summary"},
    }
    node["output_schema"] = {"type": "object"}
    return node


def two_approval_definition(
    *,
    py_optional: bool = True,
    py_output_schema: dict | None = None,
    agent_optional: bool = False,
) -> dict:
    """The acceptance graph with selectable join2 required/optional semantics.

    * default: join2 required=[agent], optional=[py] (optional failure variant
      passes ``py_output_schema=FAILING_OUTPUT_SCHEMA``);
    * ``agent_optional=True``: required=[py], optional=[agent_out] — a pure python
      guard downstream of the agent, because publish validation rejects a direct
      optional agent; the capability / revoked-profile variant still proves the
      agent failure is not converged;
    * ``py_optional=False``: required=[py, agent] (the required fail-fast variant).
    """
    if agent_optional:
        # Publish validation rejects an optional source that names an agent
        # directly. Guard the branch with a pure python node; the agent failure
        # is still attributed to this branch and the runtime allowlist still
        # refuses to converge it.
        required, optional = ("py",), ("agent_out",)
    elif py_optional:
        required, optional = ("agent",), ("py",)
    else:
        required, optional = ("py", "agent"), ()

    nodes = [
        t027.start_node(),
        _condition("choose", join_id="join1"),
        _approval(
            "approve1",
            "First approval: run the sales analysis",
            {"subject": "order-42", "amount": "1250.00"},
        ),
        _python("direct"),
        _python("fork"),
        _python("py", output_schema=py_output_schema),
        _agent_node("agent"),
    ]
    if agent_optional:
        nodes.append(_python("agent_out"))
    nodes += [
        t083.join_node("join2", fork_id="fork", required=required, optional=optional),
        t083.join_node("join1", fork_id="choose", required=("join2", "direct")),
        _approval(
            "approve2",
            "Second approval: publish the final result",
            {"stage": "final", "subject": "order-42"},
        ),
        _model_node("summarise"),
        _end_node(),
    ]
    edges = [
        {"source": "start", "target": "choose"},
        {"source": "choose", "target": "approve1", "port": "yes"},
        {"source": "choose", "target": "direct", "port": "no"},
        {"source": "approve1", "target": "fork"},
        {"source": "fork", "target": "py"},
        {"source": "fork", "target": "agent"},
        {"source": "py", "target": "join2"},
    ]
    if agent_optional:
        edges += [
            {"source": "agent", "target": "agent_out"},
            {"source": "agent_out", "target": "join2"},
        ]
    else:
        edges.append({"source": "agent", "target": "join2"})
    edges += [
        {"source": "join2", "target": "join1"},
        {"source": "direct", "target": "join1"},
        {"source": "join1", "target": "approve2"},
        {"source": "approve2", "target": "summarise"},
        {"source": "summarise", "target": "end"},
    ]
    return {
        "dsl_version": "1.0",
        "input_schema": dict(ACCEPTANCE_INPUT_SCHEMA),
        "output_schema": dict(ACCEPTANCE_OUTPUT_SCHEMA),
        "nodes": nodes,
        "edges": edges,
        "policy": dict(t027.BASE_POLICY),
    }


def acceptance_definition_variants() -> dict[str, dict]:
    return {
        "success": two_approval_definition(),
        "optional_failure": two_approval_definition(
            py_optional=True, py_output_schema=FAILING_OUTPUT_SCHEMA
        ),
        "required_failure": two_approval_definition(py_optional=False),
        # The agent branch is optional, so a capability failure can only be
        # absorbed if it were a pure-read/compute error; it must never be.
        "capability_failure": two_approval_definition(agent_optional=True),
    }


# ---------------------------------------------------------------------------
# submission / execution / decisions
# ---------------------------------------------------------------------------
def submit_acceptance_run(stack, definition: dict, *, name: str = "t091 acceptance"):
    return t083.queue_graph_run(
        stack,
        definition,
        input_value={"requires_approval": True, "task": "Summarize the regional sales"},
        name=name,
    )


exec_once = t087.exec_once
claim_next_run = t087.claim_next_run

run_row = t027.run_row
approval_rows = t087.approval_rows
approval_for_node = t087.approval_for_node
decide = t087.decide
operation_rows = t087.operation_rows
usage_ledger_rows = t087.usage_ledger_rows

waiting_row = t035.waiting_row
resume_jobs = t035.resume_jobs
resume_job = t035.resume_job


def decide_node(owner, dbname: str, run_id: str, node_id: str) -> dict:
    """Approve the pending node approval and return the decided row."""
    approval = approval_for_node(dbname, run_id, node_id)
    assert approval is not None and approval["state"] == "pending", (node_id, approval)
    response = decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert response.status == 200, response.text
    assert response.json["state"] == "approved", response.text
    return approval


# ---------------------------------------------------------------------------
# durable reads
# ---------------------------------------------------------------------------
def count_jobs_for_application(dbname: str, application_id: str) -> int:
    return t083.count_jobs_for_application(dbname, application_id)


def count_runs_for_application(dbname: str, application_id: str) -> int:
    return t083.count_runs_for_application(dbname, application_id)


def revoke_golden_model(dbname: str) -> None:
    """Revoke the seeded golden profile between approval and the resumed attempt."""
    from support import t085

    t085.revoke_golden_model(dbname)
