"""F06/T047: the agent uses only authorized current-run sources inside a
deterministic token budget, truncating low-value context while protected facts
survive verbatim.

Real stack: PostgreSQL, the compiled API/supervisor binaries, the real debug
submission and capability ledger, and the real sandbox python/tool nodes. The
model provider remains the documented deterministic dev stub; the sealed T041
model-params artifact is the exact request the platform would have sent.

The test forces a small ``context_window`` through the model-profile config, so
truncation is observed on the real request rather than reconstructed from Go
unit-test helpers. F09 personal-memory storage does not exist in this batch; the
trace therefore records ``not_run`` and no memory message is injected.
"""

from __future__ import annotations

import json

import pytest

from support import stack as st
from support import t033, t041, t043
from support.fixtures import fresh_database  # noqa: F401 - pytest fixture


def agent_definition_with_request() -> dict:
    """The T043 four-node chain plus the start input as a second agent input.

    The extra binding is what makes a verbatim upstream string visible to the
    builder, so an over-budget build truncates that text instead of a protected
    fact.
    """
    definition = t043.agent_definition()
    agent = definition["nodes"][2]
    assert agent["type"] == "agent", agent
    agent["inputs"]["request"] = {"ref": "node:start#"}
    return definition


def content_document(session, artifact_id: str) -> dict:
    response = t033.artifact_content(session, artifact_id)
    assert response.status == 200, response.text
    return json.loads(response.raw)


def analysis_rows(dbname: str, run_id: str) -> list[dict]:
    return [
        row
        for row in t043.node_rows(dbname, run_id)
        if row["node_id"] == "analysis"
    ]


def test_budgeted_context_truncates_verbose_text_and_keeps_protected_facts(
    t033_stack,
) -> None:
    t041.set_model_config(
        t033_stack.dbname, {"context_window": 3072, "max_output_tokens": 1024}
    )
    long_source = "request-source-" + ("verbose-input " * 900)  # ~13 KB
    definition = agent_definition_with_request()
    owner, run, _csv = t043.queue_agent_run(
        t033_stack, definition=definition, input_value={"source": long_source}
    )
    completed, report = t043.run_agent_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report

    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # The trace is one durable file row of the agent node and is readable through
    # the ordinary owner-only artifact content route the /runs page uses.
    rows = analysis_rows(t033_stack.dbname, run["id"])
    trace_rows = [row for row in rows if row["kind"] == "file" and row["label"] == "context-trace"]
    assert len(trace_rows) == 1, rows
    trace = content_document(owner.creator, trace_rows[0]["artifact_id"])
    assert trace["version"] == 1 and trace["node_id"] == "analysis", trace
    turns = trace["turns"]
    assert len(turns) >= 1, trace
    last = turns[-1]
    assert last["budget_tokens"] == 3072 - 1024, last
    assert last["reserved_output_tokens"] == 1024, last
    assert 0 < last["estimated_tokens"] <= last["budget_tokens"], last
    assert last["memory_state"] == "not_run", last
    assert "stats" in last["protected"], last
    reasons = [entry["reason"] for entry in last["truncations"]]
    assert "verbose_upstream_text" in reasons, reasons
    source_kinds = {entry["kind"] for entry in last["sources"]}
    assert {"python", "start"} <= source_kinds, source_kinds
    # The trace names sources and keys only; it never carries raw content.
    encoded_trace = json.dumps(trace)
    assert "250.00" not in encoded_trace, encoded_trace
    assert "request-source-" not in encoded_trace, encoded_trace
    assert long_source not in encoded_trace

    # The sealed model params are the exact real request. The protected stats
    # amount is present verbatim; the verbose upstream text was shortened.
    model_operations = [
        row
        for row in t041.operation_rows(t033_stack.dbname, run["id"])
        if row["capability_kind"] == "model"
    ]
    assert len(model_operations) >= 1, model_operations
    first_params = json.loads(
        t041.artifact_bytes(
            t033_stack, t041.artifact_row(t033_stack.dbname, model_operations[0]["params_artifact_id"])
        )
    )
    messages = first_params["messages"]
    assert messages[0]["role"] == "system"
    user_content = messages[1]["content"]
    assert '"total":"250.00"' in user_content, user_content
    assert '"order_count":3' in user_content, user_content
    assert "...[truncated]" in user_content, user_content
    assert long_source not in user_content, "the full verbose input reached the model"
    assert len(user_content) < 8000, len(user_content)

    # The tool result the model saw still carries the exact total.
    tool_messages = [message for message in messages if message["role"] == "tool"]
    assert tool_messages, messages
    tool_document = json.loads(tool_messages[0]["content"])
    assert tool_document["total"] == "250.00", tool_document

    # No other run/user data: only this run's sealed nodes can be named.
    allowed_nodes = {"start", "stats", "analysis"}
    for entry in last["sources"]:
        if entry.get("node_id"):
            assert entry["node_id"] in allowed_nodes, entry

    # F09 memory storage is absent, so the platform injects nothing and says so.
    assert all(message["role"] in {"system", "user", "tool"} for message in messages)
    for message in messages:
        assert "confirmed_memory" not in message["content"]


def test_protected_minimum_over_budget_is_a_stable_context_failure(
    t033_stack,
) -> None:
    # context_window 200 - max_output_tokens 100 = a 100-token budget, smaller
    # than the fixed system message plus the protected inputs. The executor must
    # fail with the stable code instead of silently trimming a protected fact.
    t041.set_model_config(
        t033_stack.dbname, {"context_window": 200, "max_output_tokens": 100}
    )
    owner, run, _csv = t043.queue_agent_run(t033_stack, definition=agent_definition_with_request())
    completed, report = t043.run_agent_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "context_budget_exhausted", report
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "context_budget_exhausted", state
    assert state["output_artifact_id"] is None, state
    assert t041.operation_rows(t033_stack.dbname, run["id"]) == []
    assert analysis_rows(t033_stack.dbname, run["id"]) == []
    # The node was observed as failed, not silently skipped.
    events = t043.node_events(t033_stack.dbname, run["id"])
    observed = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["analysis"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed
