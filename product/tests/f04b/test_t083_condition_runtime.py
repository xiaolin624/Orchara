"""F04B/T083 acceptance: real condition branching and join-once runs.

Every case goes through the product's real path: a real draft saved through the
public API (validated by the Go workflow compiler), the real debug submission
(sealed run input + linked CSV task_input), the compiled ``cmd/supervisor
exec --once`` claiming the attempt and running the selected python branch(es) in
WSL2/runsc, the run-scoped mTLS artifact routes, the non-contract node-results
report, the owner-only SSE replay and the PostgreSQL run facts.

Nothing is mocked. The only fixtures are the documented public sales CSV and the
expected summary from WORKFLOW-SPEC §6.
"""

from __future__ import annotations

import json

import pytest

from support import execution_support as execution
from support import t025, t027, t033, t042, t083
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


@pytest.fixture(scope="module")
def sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def assert_succeeded(report: dict) -> None:
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    sandbox_report = report["sandbox"]
    assert sandbox_report["started"] is True and sandbox_report["exit_code"] == 0, sandbox_report
    assert sandbox_report["cleanup"]["sandbox_list_matches"] == 0, sandbox_report


def test_condition_true_runs_only_the_selected_branch_and_join_once(
    t033_stack, sandbox
) -> None:
    owner, application_id, run = t083.queue_graph_run(
        t033_stack,
        t083.branch_definition(),
        input_value={"requires_approval": True},
        name="t083 condition true",
    )
    # The same fixture the executor is about to run passes publish validation.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert_succeeded(report)
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    items = t083.node_results(owner.creator, run["id"], expected=6)
    assert {item["node_id"] for item in items} == {"left", "choose", "join"}, items
    # The unselected branch has no result rows at all.
    assert [item for item in items if item["node_id"] == "right"] == []
    assert len(t083.node_data_rows(items, "join")) == 1, "the join must run exactly once"

    condition = t083.node_data_document(owner.creator, items, "choose")
    assert condition == {"port": "yes", "matched_case": 0}, condition

    join = t083.node_data_document(owner.creator, items, "join")
    results = join["results"]
    assert set(results) == {"left", "right"}, results
    assert results["left"]["status"] == "succeeded", results["left"]
    assert results["left"]["value"] == EXPECTED_SUMMARY, results["left"]
    assert results["right"] == {"status": "skipped"}, results["right"]

    final = t083.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final == {"results": results}, final

    states = t083.node_states(t033_stack.dbname, run["id"])
    assert states == {
        "start": ["pending", "running", "succeeded"],
        "choose": ["pending", "running", "succeeded"],
        "left": ["pending", "running", "succeeded"],
        "right": ["skipped"],
        "join": ["pending", "running", "succeeded"],
        "end": ["pending", "running", "succeeded"],
    }, states
    # The owner-only SSE replay projects the same durable node.status sequence.
    assert t083.sse_node_states(t033_stack, owner.creator, run["id"]) == states

    # Exactly one join firing: one succeeded event, and no retries.
    join_succeeded = [
        event
        for event in t042.node_events(t033_stack.dbname, run["id"])
        if event["payload"]["node_id"] == "join" and event["payload"]["state"] == "succeeded"
    ]
    assert len(join_succeeded) == 1, join_succeeded

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_condition_default_port_runs_the_no_branch(t033_stack, sandbox) -> None:
    owner, _application_id, run = t083.queue_graph_run(
        t033_stack,
        t083.branch_definition(),
        input_value={"requires_approval": False},
        name="t083 condition default",
    )
    completed, report = execution.execute_once(t033_stack)
    assert report["outcome"] == "succeeded", (completed.stderr, report)

    items = t083.node_results(owner.creator, run["id"], expected=6)
    assert {item["node_id"] for item in items} == {"right", "choose", "join"}, items
    condition = t083.node_data_document(owner.creator, items, "choose")
    assert condition == {"port": "no", "matched_case": "default"}, condition
    results = t083.node_data_document(owner.creator, items, "join")["results"]
    assert results["left"] == {"status": "skipped"}, results["left"]
    assert results["right"]["status"] == "succeeded", results["right"]
    assert results["right"]["value"] == EXPECTED_SUMMARY, results["right"]

    states = t083.node_states(t033_stack.dbname, run["id"])
    assert states["left"] == ["skipped"], states
    assert states["right"] == ["pending", "running", "succeeded"], states
    assert len(t083.node_data_rows(items, "join")) == 1


def test_parallel_fork_runs_both_branches_and_joins_once(t033_stack, sandbox) -> None:
    owner, application_id, run = t083.queue_graph_run(
        t033_stack,
        t083.parallel_definition(),
        input_value={},
        name="t083 parallel fork",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert report["outcome"] == "succeeded", (completed.stderr, report)

    items = t083.node_results(owner.creator, run["id"], expected=9)
    assert {item["node_id"] for item in items} == {"left", "right", "join"}, items
    assert len(t083.node_data_rows(items, "join")) == 1, "one join firing for two branches"
    results = t083.node_data_document(owner.creator, items, "join")["results"]
    assert set(results) == {"left", "right"}, results
    for source in ("left", "right"):
        assert results[source]["status"] == "succeeded", results[source]
        assert results[source]["value"] == EXPECTED_SUMMARY, results[source]

    states = t083.node_states(t033_stack.dbname, run["id"])
    for source in ("left", "right"):
        assert states[source] == ["pending", "running", "succeeded"], states
    assert states["join"] == ["pending", "running", "succeeded"], states


def test_nested_conditions_converge_at_each_join(t033_stack, sandbox) -> None:
    owner, application_id, run = t083.queue_graph_run(
        t033_stack,
        t083.nested_definition(),
        input_value={"mode": "deep"},
        name="t083 nested condition",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert report["outcome"] == "succeeded", (completed.stderr, report)

    # deep data/files/log + three condition/join data artifacts.
    items = t083.node_results(owner.creator, run["id"], expected=8)
    assert {item["node_id"] for item in items} == {"deep", "choose", "choose2", "join2", "join"}, (
        items
    )
    assert len(t083.node_data_rows(items, "join2")) == 1, "the inner join runs once"
    assert len(t083.node_data_rows(items, "join")) == 1, "the outer join runs once"

    inner = t083.node_data_document(owner.creator, items, "join2")
    inner_results = inner["results"]
    assert inner_results["deep"]["status"] == "succeeded", inner_results
    assert inner_results["shallow"] == {"status": "skipped"}, inner_results

    outer = t083.node_data_document(owner.creator, items, "join")["results"]
    assert outer["join2"]["status"] == "succeeded", outer
    assert outer["join2"]["value"] == inner, "the nested region converges before the parent"
    assert outer["direct"] == {"status": "skipped"}, outer

    states = t083.node_states(t033_stack.dbname, run["id"])
    assert states["deep"] == ["pending", "running", "succeeded"], states
    assert states["shallow"] == ["skipped"], states
    assert states["direct"] == ["skipped"], states
    for join_id in ("join2", "join"):
        assert states[join_id] == ["pending", "running", "succeeded"], states


def test_join_without_an_activated_required_source_fails_closed(t033_stack, sandbox) -> None:
    definition = t083.branch_definition(required=("left",), optional=("right",))
    owner, application_id, run = t083.queue_graph_run(
        t033_stack,
        definition,
        input_value={"requires_approval": False},
        name="t083 invalid activation",
    )
    # Publish validation only checks that the sources cover the real
    # predecessors; the runtime must refuse the region with no activated required
    # source.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    # A deterministic node refusal is a committed failed result, not an executor
    # crash: the supervisor exits 0 because the attempt itself completed. The
    # authoritative fact is the run's failed state and its stable code.
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "invalid_activation", report

    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "invalid_activation", state
    assert state["output_artifact_id"] is None, state

    states = t083.node_states(t033_stack.dbname, run["id"])
    assert states["right"] == ["pending", "running", "succeeded"], states
    assert states["left"] == ["skipped"], states
    assert states["join"] == ["failed"], states
    assert states["end"] == ["skipped"], states

    # No join envelope and no sealed final output for a failed region.
    assert t033.output_artifact_id(t033_stack.dbname, run["id"]) is None
    rows = t042.node_result_rows(t033_stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] == "join"] == [], rows


def test_cross_region_edge_is_located_on_validate_and_debug(t033_stack) -> None:
    owner, application_id = t083.application_for(
        t033_stack, t083.escaping_definition(), name="t083 cross region"
    )

    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200, validation.text
    errors = t025.validation_errors(validation)
    cross = [error for error in errors if error["code"] == "cross_region_edge"]
    assert len(cross) == 1, errors
    assert cross[0]["pointer"] == "/edges/10", cross[0]
    assert cross[0].get("node_id") == "choose2", cross[0]
    # F10: the message names both endpoints so the canvas can explain the repair
    # even though the contract error item has no edge id.
    assert "choose2->escapee" in cross[0]["message"], cross[0]["message"]

    revision = t027.draft_revision(owner.creator, application_id)
    refused = t027.submit_debug(
        owner.creator,
        application_id,
        draft_revision=revision,
        input_value={"mode": "deep"},
        artifact_ids=[],
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed", refused.text
    details = refused.json["error"]["details"]["errors"]
    matching = [error for error in details if error["code"] == "cross_region_edge"]
    assert matching, details
    assert matching[0]["pointer"] == "/edges/10", matching[0]
    assert matching[0].get("node_id") == "choose2", matching[0]

    # A refused submission creates no execution identity at all.
    assert t083.count_runs_for_application(t033_stack.dbname, application_id) == 0
    assert t083.count_jobs_for_application(t033_stack.dbname, application_id) == 0


def test_publish_accepts_the_structured_region_fixtures(t033_stack) -> None:
    # One real owner for the whole case: the golden draft id is seeded once per
    # database, so the three applications share the owner session.
    owner = t033.golden_owner(t033_stack)
    for name, definition in (
        ("t083 publish branch", t083.branch_definition()),
        ("t083 publish parallel", t083.parallel_definition()),
        ("t083 publish nested", t083.nested_definition()),
    ):
        application_id = t027.application_with_definition(owner.creator, definition, name=name)
        response = t025.validate(owner.creator, application_id)
        assert response.status == 200, response.text
        assert response.json == {"valid": True, "errors": []}, (name, response.text)
        manifest_hash = response.header("X-Manifest-SHA256")
        assert manifest_hash and len(manifest_hash) == 64, (name, manifest_hash)
