"""C5.03: the shared run-observation capability reads the real committed facts.

One real linear run exercises the four capabilities the graph acceptance batch
used to duplicate inside ticket modules: the owner-only node results, the sealed
node/final content bytes, the durable run/node status events and the owner-only
SSE replay. Each shared read is compared with a fact obtained independently from
PostgreSQL or the raw owner response, so a wrong forwarding wrapper cannot pass.
The supervisor report is a process outcome, never the committed Run state.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from support import execution_support as execution
from support import run_observations as observations
from support import t033, t042
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

# WORKFLOW-SPEC §6: the documented public sales sample.
EXPECTED_SUMMARY = {
    "total": "250.00",
    "regions": {"东区": "200.00", "西区": "50.00"},
    "order_count": 3,
}

EXPECTED_NODE_STATES = {
    "start": ["pending", "running", "succeeded"],
    "stats": ["pending", "running", "succeeded"],
    "end": ["pending", "running", "succeeded"],
}


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def queue_linear_run(stack):
    owner = t033.golden_owner(stack)
    application_id = t042.application_for(owner)
    csv_id = t042.ready_csv(stack, owner)
    run = t042.submit_linear_run(owner, application_id, artifact_id=csv_id)
    return owner, run


def test_shared_observations_read_the_committed_run_facts(t033_stack) -> None:
    stack = t033_stack
    owner, run = queue_linear_run(stack)
    run_id = run["id"]

    completed, report = execution.execute_once(stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report

    # The committed Run terminal state is read from PostgreSQL, not inferred
    # from the supervisor's process report.
    state = observations.wait_run_state(stack.dbname, run_id, {"succeeded"})
    assert state["state"] == "succeeded", state
    assert state["finished_at"] is not None, state
    assert state["error_code"] is None, state

    # --- owner node results -------------------------------------------------
    response = observations.node_results_response(owner.creator, run_id)
    assert response.status == 200, response.text
    assert set(response.json) >= {"items", "next_cursor", "generated_at"}, response.json
    items = observations.node_result_items(owner.creator, run_id, expected=4)
    assert {item["node_id"] for item in items} == {"stats"}, items
    assert {item["kind"] for item in items} == {"data", "file", "log"}, items
    assert observations.node_results_response(
        t033.minimal_owner(stack).creator, run_id
    ).status == 404
    assert observations.node_results_response(owner.admin, run_id).status == 404

    # --- sealed content bytes ----------------------------------------------
    data_rows = observations.node_data_rows(items, "stats")
    assert len(data_rows) == 1, data_rows
    data_content = observations.artifact_content(owner.creator, data_rows[0]["artifact_id"])
    assert data_content.status == 200, data_content.text
    assert hashlib.sha256(data_content.raw).hexdigest() == data_rows[0]["sha256"]
    assert observations.node_data_document(owner.creator, items, "stats") == EXPECTED_SUMMARY

    # --- durable node.status facts -----------------------------------------
    events = observations.node_events(stack.dbname, run_id)
    assert len(events) == 9, events
    for event in events:
        assert set(event["payload"]) == {"node_id", "state", "attempt_no"}, event
        assert event["payload"]["attempt_no"] == 1, event
    assert observations.node_states(stack.dbname, run_id) == EXPECTED_NODE_STATES
    assert observations.sse_node_states(stack, owner.creator, run_id) == EXPECTED_NODE_STATES

    # --- final output (committed pointer + sealed bytes) --------------------
    output_id = observations.output_artifact_id(stack.dbname, run_id)
    assert output_id == state["output_artifact_id"], (output_id, state)
    final_content = observations.artifact_content(owner.creator, output_id)
    assert final_content.status == 200, final_content.text
    final = json.loads(final_content.raw)
    assert final == {"report": "sales summary", "summary": EXPECTED_SUMMARY}, final
    assert observations.final_output_document(owner.creator, stack.dbname, run_id) == final
