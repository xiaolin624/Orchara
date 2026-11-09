"""F04A/T042 acceptance: a linear start -> python -> end debug run on real runsc.

The product path is exercised end to end: a real draft saved through the public
API, the real debug submission (sealed run input + linked CSV task_input), the
compiled ``cmd/supervisor exec --once`` claiming the attempt and running the
fixed preset tool in WSL2/runsc, the contract's run-scoped artifact routes, the
non-contract node-results report, and the owner-only node results / artifact
content reads.

Every assertion reads the real stack (PostgreSQL, the mTLS listener, the object
store, the guest sandbox). Nothing is mocked. If WSL2/runsc is unavailable the
module skips with the exact failing command; the report records that honestly.
"""

from __future__ import annotations

import json

import pytest

from support import execution_support as execution
from support import run_observations as observations
from support import stack as st
from support import t027, t031, t033, t042
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


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def queue_linear_run(stack, *, definition: dict | None = None, input_value: dict | None = None):
    owner = t033.golden_owner(stack)
    application_id = t042.application_for(owner, definition=definition)
    csv_id = t042.ready_csv(stack, owner)
    run = t042.submit_linear_run(owner, application_id, artifact_id=csv_id, input_value=input_value)
    return owner, run, csv_id


def test_linear_run_exposes_data_files_and_logs(t033_stack) -> None:
    owner, run, _csv_id = queue_linear_run(t033_stack)
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    sandbox = report["sandbox"]
    assert sandbox["started"] is True and sandbox["exit_code"] == 0, sandbox
    assert sandbox["cleanup"]["sandbox_list_matches"] == 0, sandbox

    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state
    output_id = observations.output_artifact_id(t033_stack.dbname, run["id"])
    assert output_id, state

    # --- the owner read: data, files and log are separately mapped -------------
    response = observations.node_results_response(owner.creator, run["id"])
    assert response.status == 200, response.text
    body = response.json
    assert set(body) >= {"items", "next_cursor", "generated_at"}, body
    items = body["items"]
    assert len(items) == 4, items
    kinds = {item["kind"] for item in items}
    assert kinds == {"data", "file", "log"}, kinds
    for item in items:
        assert item["node_id"] == "stats", item
        assert set(item) == {
            "node_id", "kind", "artifact_id", "label", "media_type", "size_bytes", "sha256",
        }, item
        assert len(item["sha256"]) == 64, item
        assert item["size_bytes"] >= 0, item

    data_rows = t042.artifacts_of_kind(items, "data")
    file_rows = t042.artifacts_of_kind(items, "file")
    log_rows = t042.artifacts_of_kind(items, "log")
    assert len(data_rows) == 1 and len(file_rows) == 2 and len(log_rows) == 1, items
    assert {row["label"] for row in file_rows} == {"summary.json", "summary.csv"}, file_rows

    # Data is the canonical validated output.json.data, not raw output.json.
    data_content = observations.artifact_content(owner.creator, data_rows[0]["artifact_id"])
    assert data_content.status == 200, data_content.text
    data_document = json.loads(data_content.raw)
    assert data_document == EXPECTED_SUMMARY, data_document
    assert set(data_document) == {"total", "regions", "order_count"}, data_document
    assert b'"artifacts"' not in data_content.raw

    # Each admitted file round-trips its real bytes.
    rows_by_label = {row["label"]: row for row in file_rows}
    summary_json = observations.artifact_content(owner.creator, rows_by_label["summary.json"]["artifact_id"])
    assert summary_json.status == 200, summary_json.text
    assert json.loads(summary_json.raw) == EXPECTED_SUMMARY, summary_json.raw
    summary_csv = observations.artifact_content(owner.creator, rows_by_label["summary.csv"]["artifact_id"])
    assert summary_csv.status == 200, summary_csv.text
    assert summary_csv.raw == t033.EXPECTED_SUMMARY_CSV, summary_csv.raw

    # The log is separate, capped and never listed as run data.
    log_content = observations.artifact_content(owner.creator, log_rows[0]["artifact_id"])
    assert log_content.status == 200, log_content.text
    assert b"stdout:" in log_content.raw and b"stderr:" in log_content.raw, log_content.raw
    assert len(log_content.raw) <= 64 * 1024, len(log_content.raw)
    listed = t033.list_run_artifacts(owner.creator, run["id"])
    assert listed.status == 200, listed.text
    listed_ids = [item["id"] for item in listed.json["items"]]
    assert log_rows[0]["artifact_id"] not in listed_ids
    assert data_rows[0]["artifact_id"] in listed_ids
    assert {row["artifact_id"] for row in file_rows} <= set(listed_ids)
    assert output_id in listed_ids
    for artifact_id in listed_ids:
        payload = observations.artifact_content(owner.creator, artifact_id)
        assert payload.status == 200, payload.text
        if payload.raw[:1] == b"{":
            document = json.loads(payload.raw)
            assert not (isinstance(document, dict) and "data" in document and "artifacts" in document), (
                "the raw sandbox output.json became an authorized artifact"
            )

    # The end node sealed the mapped final output.
    final_content = observations.artifact_content(owner.creator, output_id)
    assert final_content.status == 200, final_content.text
    final_document = json.loads(final_content.raw)
    assert final_document == {"report": "sales summary", "summary": EXPECTED_SUMMARY}, final_document

    # Provenance rows: purpose/state and the exact kind mapping.
    rows = observations.node_result_rows(t033_stack.dbname, run["id"])
    assert len(rows) == 4, rows
    by_kind = {}
    for row in rows:
        by_kind.setdefault(row["kind"], []).append(row)
        assert row["state"] == "ready", row
    assert len(by_kind["data"]) == 1 and len(by_kind["file"]) == 2 and len(by_kind["log"]) == 1, by_kind
    assert {row["purpose"] for row in by_kind["data"] + by_kind["file"]} == {"node_output"}
    assert {row["purpose"] for row in by_kind["log"]} == {"log"}

    # node.status events: one per transition, metadata only.
    events = observations.node_events(t033_stack.dbname, run["id"])
    assert len(events) == 9, events
    observed = {}
    for event in events:
        payload = event["payload"]
        assert set(payload) == {"node_id", "state", "attempt_no"}, payload
        assert payload["attempt_no"] == 1, payload
        observed.setdefault(payload["node_id"], []).append(payload["state"])
    assert observed == {
        "start": ["pending", "running", "succeeded"],
        "stats": ["pending", "running", "succeeded"],
        "end": ["pending", "running", "succeeded"],
    }, observed

    # The owner-only rule holds for every other principal.
    assert observations.node_results_response(t033.minimal_owner(t033_stack).creator, run["id"]).status == 404
    assert observations.node_results_response(owner.admin, run["id"]).status == 404
    assert observations.node_results_response(owner.creator, "not-a-run").status == 404

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_node_output_schema_violation_fails_without_registering_data(t033_stack) -> None:
    # The fixed tool returns "total" as a decimal string; this draft says integer.
    bad_schema = dict(t042.PYTHON_OUTPUT_SCHEMA)
    bad_schema = {
        "type": "object",
        "properties": {"total": {"type": "integer"}},
        "required": ["total"],
        "additionalProperties": True,
    }
    owner, run, _csv = queue_linear_run(
        t033_stack, definition=t042.linear_definition(python_output_schema=bad_schema)
    )
    completed, report = execution.execute_once(t033_stack)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "node_output_invalid", report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_output_invalid", state
    assert state["output_artifact_id"] is None, state

    # No node result row, no node_output artifact of the tool, no success.
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    node_outputs = st.sql_rows(
        t033_stack.dbname,
        "SELECT a.id::text AS id, a.purpose FROM platform.artifacts AS a "
        "JOIN platform.run_artifact_uploads AS u ON u.artifact_id = a.id "
        f"WHERE u.run_id = {st.quote_literal(run['id'])}::uuid",
    )
    for artifact in node_outputs:
        assert artifact["purpose"] not in {"final_output"}, artifact
    events = observations.node_events(t033_stack.dbname, run["id"])
    observed = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["stats"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_invalid_run_input_and_unresolved_binding_are_refused_before_the_sandbox(
    t033_stack,
) -> None:
    # 1. The sealed run input does not match definition.input_schema.
    owner, run, _csv = queue_linear_run(t033_stack, input_value={"source": 5})
    completed, report = execution.execute_once(t033_stack)
    assert report["outcome"] == "refused", report
    assert report["error_code"] == "node_input_invalid", report
    assert report.get("sandbox") is None, "no sandbox may start for an invalid input"
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_input_invalid", state

    # 2. A python binding that names an ancestor pointer the compiler cannot
    # resolve statically: execution must refuse it as node_input_invalid.
    broken_inputs = {"file": {"ref": "node:start#/missing_artifact"}}
    app2 = t042.application_for(
        owner, definition=t042.linear_definition(python_inputs=broken_inputs), name="t042 binding"
    )
    csv2 = t042.ready_csv(t033_stack, owner)
    run2 = t042.submit_linear_run(owner, app2, artifact_id=csv2)
    completed2, report2 = execution.execute_once(t033_stack)
    assert report2["outcome"] == "refused", report2
    assert report2["error_code"] == "node_input_invalid", report2
    assert report2.get("sandbox") is None or report2["sandbox"]["started"] is False, report2
    state2 = observations.wait_run_state(t033_stack.dbname, run2["id"], {"failed"})
    assert state2["error_code"] == "node_input_invalid", state2


def test_node_results_report_refuses_stale_and_foreign_rows(t033_stack) -> None:
    owner, run, _csv = queue_linear_run(t033_stack)
    lease = t033.execution_client(t033_stack, "supervisor").claim_job(
        worker_id="t042-supervisor",
        execution_unit_id="t042-stale",
        supported_compilers=[t031.COMPILER_VERSION],
    ).json
    assert lease["run_id"] == run["id"], lease
    client = t033.artifact_client(t033_stack)

    # A foreign artifact id in a live attempt's scope is a 404, never a row.
    response = client.post(
        f"/internal/v1/runs/{run['id']}/node-results",
        {
            "attempt_token": lease["attempt_token"],
            "results": [{
                "node_id": "stats", "kind": "data",
                "artifact_id": "11111111-2222-4333-8444-555555555555",
                "label": "data",
            }],
        },
    )
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "artifact_not_found", response.text

    # A token that does not own a live lease is refused before any row is read.
    stale = client.post(
        f"/internal/v1/runs/{run['id']}/node-results",
        {
            "attempt_token": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "results": [{
                "node_id": "stats", "kind": "data",
                "artifact_id": "11111111-2222-4333-8444-555555555555",
                "label": "data",
            }],
        },
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt", stale.text
    assert st.sql_rows(
        t033_stack.dbname,
        "SELECT 1 FROM platform.run_node_outputs"
        f" WHERE run_id = {st.quote_literal(run['id'])}::uuid",
    ) == []


def test_cancel_stops_the_chain_without_a_success_or_node_result(t033_stack) -> None:
    t042.request_cancel_after_first_heartbeat(t033_stack.dbname)
    owner, run, _csv = queue_linear_run(t033_stack)
    env = execution.supervisor_exec_env(t033_stack, heartbeat="50ms", timeout="60s", result_wait="30s")
    with execution.FinalizeWorker(t033_stack):
        completed = execution.run_supervisor_exec(t033_stack, env)
    report = execution.exec_report(completed)
    assert report["outcome"] == "cancelled", (report, completed.stderr)
    # A cancel that lands before the unit starts has no reliable "cancelled"
    # sandbox fact; when the unit did start, the driver must report it killed.
    sandbox = report.get("sandbox")
    if sandbox and sandbox.get("started"):
        assert sandbox["cancelled"] is True, report
    state = observations.wait_run_state(
        t033_stack.dbname,
        run["id"],
        {"cancelled", "recovery_pending", "failed", "needs_reconciliation"},
        timeout=30.0,
    )
    # A confirmed cancel is the documented terminal outcome (EXECUTION §6): all
    # controlled units have exited and there is no unknown write, so the Run must
    # not be left `running` (or parked for recovery) after the supervisor exits.
    assert state["state"] == "cancelled", state
    assert state["cancel_requested_at"] is not None, state
    assert state["error_code"] is None, state
    assert state["finished_at"] is not None, state
    assert state["output_artifact_id"] is None, "a cancelled run must not publish a result"
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    events = observations.node_events(t033_stack.dbname, run["id"])
    observed = {}
    for event in events:
        observed.setdefault(event["payload"]["node_id"], []).append(event["payload"]["state"])
    assert observed["stats"] == ["pending", "running", "failed"], observed
    assert observed["end"] == ["skipped"], observed
    # The guest is the authority for "nothing survived": the driver's in-flight
    # cleanup counter can observe a unit that is still being deleted when the
    # cancel preempts the start (the same pre-existing race T033's stricter
    # assertion exercises).
    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_legacy_golden_agent_graph_still_runs_the_single_python_flow(t033_stack) -> None:
    """T033 regression in the T042 binary: the sealed golden graph contains an
    agent node, but exactly one preset python node and the legacy task_input, so
    the legacy path must still produce the documented artifacts and no node
    results (the legacy flow does not report node rows)."""
    owner = t033.golden_owner(t033_stack)
    csv_id = t042.ready_csv(t033_stack, owner)
    run = t033.debug_run_with_input(t033_stack, owner, csv_id)
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report
    output_id = observations.output_artifact_id(t033_stack.dbname, run["id"])
    assert output_id, report
    content = observations.artifact_content(owner.creator, output_id)
    assert content.status == 200 and content.raw == t033.EXPECTED_SUMMARY_JSON, content.raw
    csv = t033.artifact_by_purpose(t033_stack.dbname, run["id"], "node_output")
    assert csv is not None and csv["media_type"] == "text/csv", csv
    log = t033.artifact_by_purpose(t033_stack.dbname, run["id"], "log")
    assert log is not None, "the legacy log upload is missing"
    listed = t033.list_run_artifacts(owner.creator, run["id"])
    assert {item["id"] for item in listed.json["items"]} == {output_id, csv["id"]}
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    assert observations.node_events(t033_stack.dbname, run["id"]) == []
