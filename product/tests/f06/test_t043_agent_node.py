"""F04A/T043 acceptance: the controlled agent node on the real stack.

The product path is exercised end to end: a real draft with the four-node
``start -> python -> agent -> end`` chain, the real debug submission, the compiled
``cmd/supervisor exec --once`` claiming the attempt, the real T041 capability
proxy (worker mTLS + live attempt token) for two model turns and one tool grant,
the real WSL2/runsc sandbox for the python node and the agent's preset tool call,
the non-contract node-results report, the owner-only reads, and the real ledger.

The model provider is the documented deterministic dev stub (labelled stub); the
tool execution and every artifact/ledger row are real. If WSL2/runsc is
unavailable the module skips with the exact failing command.
"""

from __future__ import annotations

import json
import re

import pytest

from support import stack as st
from support import t025, t033, t041, t042, t043
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

EXPECTED_SUMMARY = {
    "total": "250.00",
    "regions": {"东区": "200.00", "西区": "50.00"},
    "order_count": 3,
}

DIGEST_TEXT = re.compile(r"^[0-9a-f]{16}$")


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def content_document(session, artifact_id: str) -> dict:
    response = t033.artifact_content(session, artifact_id)
    assert response.status == 200, response.text
    return json.loads(response.raw)


def rows_by_node(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["node_id"], []).append(row)
    return grouped


def test_agent_chain_runs_the_preset_tool_and_validates_the_final_output(t033_stack) -> None:
    owner, run, _csv_id = t043.queue_agent_run(t033_stack)
    completed, report = t043.run_agent_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    sandbox = report["sandbox"]
    assert sandbox["started"] is True and sandbox["exit_code"] == 0, sandbox
    assert sandbox["cleanup"]["sandbox_list_matches"] == 0, sandbox

    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state
    output_id = t033.output_artifact_id(t033_stack.dbname, run["id"])
    assert output_id, state

    # --- owner read: stats rows + one agent data row + tool files/log ---------
    body = t042.wait_node_results(owner.creator, run["id"], expected=9)
    items = body["items"]
    assert len(items) == 9, items
    grouped = rows_by_node(items)
    assert set(grouped) == {"stats", "analysis"}, grouped
    stats_kinds = sorted(row["kind"] for row in grouped["stats"])
    assert stats_kinds == ["data", "file", "file", "log"], stats_kinds
    analysis_kinds = sorted(row["kind"] for row in grouped["analysis"])
    assert analysis_kinds == ["data", "file", "file", "file", "log"], analysis_kinds

    analysis_data = [row for row in grouped["analysis"] if row["kind"] == "data"]
    assert len(analysis_data) == 1, analysis_data
    assert analysis_data[0]["label"] == "data", analysis_data[0]
    analysis_document = content_document(owner.creator, analysis_data[0]["artifact_id"])
    assert set(analysis_document) == {"text"}, analysis_document
    assert DIGEST_TEXT.match(analysis_document["text"]), analysis_document

    # The end node mapped the agent output into the definition output schema.
    final_document = content_document(owner.creator, output_id)
    assert final_document == analysis_document, final_document

    # The tool call's real files round-trip their bytes.
    analysis_files = {
        row["label"]: row for row in grouped["analysis"] if row["kind"] == "file"
    }
    assert set(analysis_files) == {"context-trace", "summary.json", "summary.csv"}, analysis_files
    summary_json = content_document(owner.creator, analysis_files["summary.json"]["artifact_id"])
    assert summary_json == EXPECTED_SUMMARY, summary_json
    summary_csv = t033.artifact_content(owner.creator,
                                        analysis_files["summary.csv"]["artifact_id"])
    assert summary_csv.status == 200
    assert summary_csv.raw == t033.EXPECTED_SUMMARY_CSV, summary_csv.raw

    # The tool log is a capped log artifact, never run data.
    analysis_log = [row for row in grouped["analysis"] if row["kind"] == "log"]
    assert len(analysis_log) == 1 and analysis_log[0]["label"] == "tool-log-stub-call-1"
    log_content = t033.artifact_content(owner.creator, analysis_log[0]["artifact_id"])
    assert log_content.status == 200 and b"stdout:" in log_content.raw, log_content.raw

    # Provenance: data/file are node_output; the agent's data is exactly one row.
    rows = t043.node_rows(t033_stack.dbname, run["id"])
    assert len(rows) == 9, rows
    by_node = rows_by_node(rows)
    for row in by_node["analysis"]:
        assert row["state"] == "ready", row
        expected_purpose = "log" if row["kind"] == "log" else "node_output"
        assert row["purpose"] == expected_purpose, row
    assert sum(1 for row in by_node["analysis"] if row["kind"] == "data") == 1

    # node.status: four nodes, pending -> running -> succeeded, attempt_no=1.
    events = t043.node_events(t033_stack.dbname, run["id"])
    assert len(events) == 12, events
    observed: dict[str, list[str]] = {}
    for event in events:
        payload = event["payload"]
        assert set(payload) == {"node_id", "state", "attempt_no"}, payload
        assert payload["attempt_no"] == 1, payload
        observed.setdefault(payload["node_id"], []).append(payload["state"])
    assert observed == {
        "start": ["pending", "running", "succeeded"],
        "stats": ["pending", "running", "succeeded"],
        "analysis": ["pending", "running", "succeeded"],
        "end": ["pending", "running", "succeeded"],
    }, observed

    # --- the T041 ledger: 2 model operations + 1 tool operation --------------
    operations = t041.operation_rows(t033_stack.dbname, run["id"])
    assert len(operations) == 3, operations
    by_key = {row["logical_key"]: row for row in operations}
    assert set(by_key) == {
        "agent:analysis:model:1", "agent:analysis:tool:stub-call-1",
        "agent:analysis:model:2",
    }, by_key
    model_ops = [by_key["agent:analysis:model:1"], by_key["agent:analysis:model:2"]]
    tool_ops = [by_key["agent:analysis:tool:stub-call-1"]]
    for operation in operations:
        assert operation["state"] == "succeeded", operation
        attempts = t041.attempt_rows(t033_stack.dbname, operation["id"])
        assert len(attempts) == 1, (operation, attempts)
        assert attempts[0]["state"] == "succeeded", attempts[0]
        assert attempts[0]["attempt_token"] == t041_attempt_token(t033_stack, run["id"])

    # The first model receipt carries the validated tool call; the second is the
    # structured final answer. No receipt carries a raw provider object.
    first_receipt = t041.receipt_document(t033_stack, model_ops[0]["receipt_artifact_id"])
    assert [call["id"] for call in first_receipt["tool_calls"]] == ["stub-call-1"]
    assert first_receipt["tool_calls"][0]["name"] == "sales_stats"
    assert first_receipt["stub"] is True, first_receipt
    second_receipt = t041.receipt_document(t033_stack, model_ops[1]["receipt_artifact_id"])
    assert second_receipt["tool_calls"] == [], second_receipt
    assert set(second_receipt["structured"]) == {"text"}, second_receipt
    assert second_receipt["structured"] == analysis_document, second_receipt

    # The tool operation's receipt is the real sandboxed tool data.
    tool_receipt = t041.receipt_document(t033_stack, tool_ops[0]["receipt_artifact_id"])
    assert tool_receipt == EXPECTED_SUMMARY, tool_receipt

    ledger = t041.ledger_items(owner.creator, run["id"])
    assert len(ledger) == 3, ledger
    ledger_models = [item for item in ledger if item["capability_kind"] == "model"]
    ledger_tools = [item for item in ledger if item["capability_kind"] == "tool"]
    assert len(ledger_models) == 2 and len(ledger_tools) == 1, ledger
    for item in ledger_models:
        assert item["state"] == "succeeded" and item["attempts"] == 1, item
        assert item["stub"] is True, item
        assert item["structured_output_keys"] == ["text"], item
    summaries = [call for item in ledger_models for call in item["tool_call_summary"]]
    assert [(call["id"], call["name"]) for call in summaries] == [
        ("stub-call-1", "sales_stats")
    ], summaries
    assert ledger_tools[0]["state"] == "succeeded", ledger_tools[0]
    assert ledger_tools[0]["receipt_artifact_id"] == tool_ops[0]["receipt_artifact_id"], (
        ledger_tools[0], tool_ops[0]
    )

    # Only the owner may read the node results and the ledger.
    assert t042.node_results(t033.minimal_owner(t033_stack).creator, run["id"]).status == 404
    assert t041.ledger(t033.minimal_owner(t033_stack).creator, run["id"]).status == 404

    # Logs are not run data and the agent's one data row is listed.
    listed = t033.list_run_artifacts(owner.creator, run["id"])
    assert listed.status == 200, listed.text
    listed_ids = [item["id"] for item in listed.json["items"]]
    assert analysis_data[0]["artifact_id"] in listed_ids
    assert analysis_log[0]["artifact_id"] not in listed_ids
    assert output_id in listed_ids

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def t041_attempt_token(stack, run_id: str) -> str:
    rows = st.sql_rows(
        stack.dbname,
        "SELECT token::text AS token FROM platform.run_attempts "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY started_at DESC LIMIT 1",
    )
    assert rows, run_id
    return rows[0]["token"]


def test_agent_final_output_schema_failure_is_located_and_never_succeeds(
    t033_stack,
) -> None:
    # A machine-readable type array is outside the executor's restricted schema
    # subset (T042 documents the subset: a string type only). The capability
    # server accepts the stub's {note: null} projection, and the agent's own node
    # validation must then fail located, not invent success. This is the honest
    # e2e path to a node_output_invalid: the dev stub otherwise always satisfies
    # the requested response_schema.
    note_schema = {
        "type": "object",
        "properties": {"note": {"type": ["string", "null"]}},
        "required": ["note"],
        "additionalProperties": False,
    }
    definition = t043.agent_definition(
        tool_versions=[],
        agent_output_schema=note_schema,
        end_inputs={"note": {"ref": "node:analysis#/note"}},
        output_schema={
            "type": "object",
            "properties": {"note": {}},
            "required": ["note"],
        },
    )
    owner, run, _csv = t043.queue_agent_run(t033_stack, definition=definition)
    completed, report = t043.run_agent_once(t033_stack)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "node_output_invalid", report
    assert "analysis" in report.get("error", "") and "#/note" in report.get("error", ""), report
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_output_invalid", state
    assert state["output_artifact_id"] is None, state

    analysis_rows = [
        row for row in t043.node_rows(t033_stack.dbname, run["id"]) if row["node_id"] == "analysis"
    ]
    assert analysis_rows == [], analysis_rows
    events = t043.node_events(t033_stack.dbname, run["id"])
    observed: dict[str, list[str]] = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["analysis"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed

    final_artifacts = st.sql_rows(
        t033_stack.dbname,
        "SELECT a.id::text AS id FROM platform.artifacts AS a "
        "JOIN platform.run_artifact_uploads AS u ON u.artifact_id = a.id "
        f"WHERE u.run_id = {st.quote_literal(run['id'])}::uuid "
        "AND a.purpose = 'final_output'",
    )
    assert final_artifacts == [], final_artifacts
    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_agent_refuses_an_unavailable_tool_before_the_model_call(t033_stack) -> None:
    owner, run, _csv = t043.queue_agent_run(t033_stack)
    # The manifest was compiled while the tool was ready; the capability catalog
    # is now un-callable. The executor must refuse before spending a model call.
    t043.set_tool_state(t033_stack.dbname, "failed")
    try:
        completed, report = t043.run_agent_once(t033_stack)
    finally:
        t043.set_tool_state(t033_stack.dbname, "ready")
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "tool_not_available", report
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "tool_not_available", state
    assert t041.operation_rows(t033_stack.dbname, run["id"]) == []
    analysis_rows = [
        row for row in t043.node_rows(t033_stack.dbname, run["id"]) if row["node_id"] == "analysis"
    ]
    assert analysis_rows == [], analysis_rows
    events = t043.node_events(t033_stack.dbname, run["id"])
    observed: dict[str, list[str]] = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["analysis"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed


def test_agent_revoked_model_is_a_platform_outcome(t033_stack) -> None:
    owner, run, _csv = t043.queue_agent_run(t033_stack)
    t041.revoke_model(t033_stack.dbname)
    try:
        completed, report = t043.run_agent_once(t033_stack)
    finally:
        st.sql(
            t033_stack.dbname,
            "UPDATE platform.model_profile_versions SET revoked_at = NULL "
            f"WHERE id = {st.quote_literal(t025.GOLDEN_MODEL_ID)}::uuid",
        )
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "model_profile_unavailable", report
    assert t041.operation_rows(t033_stack.dbname, run["id"]) == []
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "model_profile_unavailable", state


def test_agent_turn_limit_fails_without_a_final_output(t033_stack) -> None:
    definition = t043.agent_definition(max_turns=1)
    owner, run, _csv = t043.queue_agent_run(t033_stack, definition=definition)
    completed, report = t043.run_agent_once(t033_stack)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "agent_turn_limit", report
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "agent_turn_limit", state
    assert state["output_artifact_id"] is None, state

    # One model turn plus the tool grant it requested: both operations are real,
    # but the node never reports a data row or a final output.
    operations = t041.operation_rows(t033_stack.dbname, run["id"])
    assert sorted(row["capability_kind"] for row in operations) == ["model", "tool"], operations
    analysis_rows = [
        row for row in t043.node_rows(t033_stack.dbname, run["id"]) if row["node_id"] == "analysis"
    ]
    assert analysis_rows == [], analysis_rows
    events = t043.node_events(t033_stack.dbname, run["id"])
    observed: dict[str, list[str]] = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["analysis"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed
    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_agent_cancel_mid_loop_stops_without_success(t033_stack) -> None:
    try:
        t043.request_cancel_on_first_operation(t033_stack.dbname)
        owner, run, _csv = t043.queue_agent_run(t033_stack)
        completed, report = t043.run_agent_once(t033_stack)
        assert report["outcome"] == "cancelled", (report, completed.stderr)
        assert report["error_code"] == "cancel_requested", report
        state = t033.wait_run_state(
            t033_stack.dbname, run["id"], {"cancelled", "cancelling", "running", "recovery_pending"}
        )
        assert state["output_artifact_id"] is None, "a cancelled run must not publish a result"
        analysis_rows = [
            row
            for row in t043.node_rows(t033_stack.dbname, run["id"])
            if row["node_id"] == "analysis"
        ]
        assert analysis_rows == [], analysis_rows
        events = t043.node_events(t033_stack.dbname, run["id"])
        observed: dict[str, list[str]] = {}
        for event in events:
            observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
        assert observed["analysis"] == ["pending", "running", "failed"], observed
        assert observed["end"] == ["skipped"], observed
        # The cancel was observed between the first model turn and its tool grant.
        operations = t041.operation_rows(t033_stack.dbname, run["id"])
        assert [row["capability_kind"] for row in operations] == ["model"], operations
        orphan = t033.no_orphan_evidence()
        assert orphan["runsc_list"] == [], orphan
    finally:
        t043.drop_cancel_trigger(t033_stack.dbname)
