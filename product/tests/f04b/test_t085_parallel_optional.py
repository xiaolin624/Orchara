"""F04B/T085 acceptance: parallel regions, optional/required and fail-fast.

Every case goes through the product's real path: a real draft saved through the
public API (validated by the Go workflow compiler), the real debug submission
(sealed run input + linked CSV task_input), the compiled ``cmd/supervisor exec
--once`` claiming the attempt and running the parallel python branches in
WSL2/runsc, the run-scoped mTLS artifact routes, the non-contract node-results
report, the owner-only SSE replay and the PostgreSQL run facts.

The T085 cases pin what T083 could not: two independent branches really overlap,
an optional pure-compute failure becomes error data in the join envelope without
failing the run, a required failure cancels the other in-flight branch and never
runs the join, and an agent (capability/authorization) failure is never downgraded
by an optional declaration.
"""

from __future__ import annotations

import pytest

from support import execution_support as execution
from support import t025, t027, t033, t042, t083, t085
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
    assert report["sandbox"]["started"] is True, report["sandbox"]
    assert report["sandbox"]["exit_code"] == 0, report["sandbox"]


def assert_executed(completed, report: dict) -> None:
    """The supervisor exits 0 only when the attempt itself was committed."""
    if completed.returncode != 0:
        print("SUPERVISOR STDERR:\n" + (completed.stderr or ""))
        print("REPORT: " + repr(report))
    assert completed.returncode == 0, (completed.stderr, report)


def test_parallel_branches_run_and_join_once(t033_stack, sandbox) -> None:
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.parallel_definition(),
        input_value={},
        name="t085 parallel",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert_executed(completed, report)
    assert_succeeded(report)
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Both python branches really ran (one data row each + files/log), and the
    # join fired exactly once for the region.
    items = t085.node_results(owner.creator, run["id"], expected=9)
    assert {item["node_id"] for item in items} == {"left", "right", "join"}, items
    assert len(t085.node_data_rows(items, "join")) == 1, "the join must run exactly once"
    results = t085.node_data_document(owner.creator, items, "join")["results"]
    assert set(results) == {"left", "right"}, results
    for source in ("left", "right"):
        assert results[source]["status"] == "succeeded", results[source]
        assert results[source]["value"] == EXPECTED_SUMMARY, results[source]
    final = t085.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final == {"results": results}, final

    states = t085.node_states(t033_stack.dbname, run["id"])
    for node_id in ("left", "right", "join", "end"):
        assert states[node_id] == ["pending", "running", "succeeded"], (node_id, states)

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_optional_pure_compute_failure_becomes_join_error_data(t033_stack, sandbox) -> None:
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.optional_failure_definition(),
        input_value={},
        name="t085 optional failure",
    )
    # The optional declaration is publish-legal: optional sources cover the real
    # predecessors exactly like required ones do.
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert_executed(completed, report)
    # The explicit pure-compute failure of the optional branch is error data, so
    # the attempt still succeeds.
    assert report["outcome"] == "succeeded", report
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["error_code"] is None, state

    items = t085.node_results(owner.creator, run["id"], expected=5)
    # left contributes data + two files + log; the optional branch produced no
    # output artifact at all (a failed node has no value), and the join ran once.
    assert {item["node_id"] for item in items} == {"left", "join"}, items
    assert [item for item in items if item["node_id"] == "right"] == [], items
    assert len(t085.node_data_rows(items, "join")) == 1, "the join must still run once"

    join = t085.node_data_document(owner.creator, items, "join")
    results = join["results"]
    assert results["left"]["status"] == "succeeded", results["left"]
    assert results["left"]["value"] == EXPECTED_SUMMARY, results["left"]
    assert results["right"] == {"status": "failed", "error_code": "node_output_invalid"}, (
        results["right"]
    )
    assert "value" not in results["right"], results["right"]

    # The converged error is sealed as the run's final output (error as data).
    final = t085.final_output_document(owner.creator, t033_stack.dbname, run["id"])
    assert final == {"results": results}, final

    states = t085.node_states(t033_stack.dbname, run["id"])
    assert states["right"] == ["pending", "running", "failed"], states
    assert states["left"] == ["pending", "running", "succeeded"], states
    assert states["join"] == ["pending", "running", "succeeded"], states
    assert states["end"] == ["pending", "running", "succeeded"], states


def test_required_failure_cancels_the_other_branch_and_never_joins(t033_stack, sandbox) -> None:
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.required_failure_definition(),
        input_value={},
        name="t085 required failure",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    # A deterministic failed result is committed, so the supervisor itself exits 0.
    assert_executed(completed, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "node_output_invalid", report

    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "node_output_invalid", state
    assert state["output_artifact_id"] is None, state

    states = t085.node_states(t033_stack.dbname, run["id"])
    assert states["right"] == ["pending", "running", "failed"], states
    # The other branch either finished before the failure or was cancelled; it
    # never reports success into a join that does not exist.
    assert states["left"] in (
        ["pending", "running", "succeeded"],
        ["pending", "running", "failed"],
    ), states
    assert states["join"] == ["skipped"], states
    assert states["end"] == ["skipped"], states

    # The join must not execute: no data row, no final output.
    rows = t042.node_result_rows(t033_stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] == "join"] == [], rows
    assert t033.output_artifact_id(t033_stack.dbname, run["id"]) is None

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_optional_never_swallows_an_agent_capability_failure(t033_stack, sandbox) -> None:
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.parallel_agent_definition(),
        input_value={},
        name="t085 optional agent",
    )
    # The graph is publishable while the seeded model profile is still passed…
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )
    # …then the capability becomes unusable before the attempt runs. This is an
    # authorization/availability failure, not a pure-compute error: optional must
    # not downgrade it.
    t085.revoke_golden_model(t033_stack.dbname)

    completed, report = execution.execute_once(t033_stack)
    assert_executed(completed, report)
    assert report["error_code"] == "model_profile_unavailable", report

    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == "model_profile_unavailable", state
    assert state["output_artifact_id"] is None, state

    states = t085.node_states(t033_stack.dbname, run["id"])
    assert states["analysis"] == ["pending", "running", "failed"], states
    assert states["analysis_out"] == ["skipped"], states
    assert states["join"] == ["skipped"], states
    assert states["end"] == ["skipped"], states

    rows = t042.node_result_rows(t033_stack.dbname, run["id"])
    assert [row for row in rows if row["node_id"] == "join"] == [], rows
    assert t033.output_artifact_id(t033_stack.dbname, run["id"]) is None

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def test_publish_refuses_an_optional_agent_source(t033_stack) -> None:
    owner, application_id = t083.application_for(
        t033_stack, t085.direct_optional_agent_definition(),
        name="t085 optional agent publish gate",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200, validation.text
    assert validation.json["valid"] is False, validation.text
    errors = t025.validation_errors(validation)
    located = [
        error for error in errors
        if error["code"] == "bad_join_sources"
        and error["pointer"] == "/nodes/3/config/optional_sources"
    ]
    assert located, errors
    assert located[0].get("node_id") == "join", located[0]

    # The same gate refuses a real debug submission and creates no execution
    # identity: a declaration the runtime will never honour cannot be published.
    artifact_id = t042.ready_csv(t033_stack, owner)
    revision = t027.draft_revision(owner.creator, application_id)
    refused = t027.submit_debug(
        owner.creator, application_id, draft_revision=revision,
        input_value={}, artifact_ids=[artifact_id],
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed", refused.text
    details = refused.json["error"]["details"]["errors"]
    assert any(
        error["code"] == "bad_join_sources"
        and error["pointer"] == "/nodes/3/config/optional_sources"
        for error in details
    ), details
    assert t083.count_runs_for_application(t033_stack.dbname, application_id) == 0
    assert t083.count_jobs_for_application(t033_stack.dbname, application_id) == 0


def test_nested_parallel_region_inside_a_condition_branch(t033_stack, sandbox) -> None:
    owner, application_id, run = t085.queue_graph_run(
        t033_stack,
        t085.parallel_in_condition_definition(),
        input_value={"mode": "deep"},
        name="t085 nested parallel",
    )
    validation = t025.validate(owner.creator, application_id)
    assert validation.status == 200 and validation.json == {"valid": True, "errors": []}, (
        validation.text
    )

    completed, report = execution.execute_once(t033_stack)
    assert_executed(completed, report)
    assert_succeeded(report)

    # choose's port data + fork/deepA/deepB python data (4 rows each) + both joins.
    items = t085.node_results(owner.creator, run["id"], expected=15)
    assert {item["node_id"] for item in items} == {
        "choose",
        "fork",
        "deepA",
        "deepB",
        "join2",
        "join",
    }, items
    assert len(t085.node_data_rows(items, "join2")) == 1, "the inner join runs once"
    assert len(t085.node_data_rows(items, "join")) == 1, "the outer join runs once"

    inner = t085.node_data_document(owner.creator, items, "join2")["results"]
    assert set(inner) == {"deepA", "deepB"}, inner
    for source in ("deepA", "deepB"):
        assert inner[source]["status"] == "succeeded", inner[source]
        assert inner[source]["value"] == EXPECTED_SUMMARY, inner[source]

    outer = t085.node_data_document(owner.creator, items, "join")["results"]
    assert outer["join2"]["status"] == "succeeded", outer
    assert outer["join2"]["value"] == {"results": inner}, (
        "the nested region converges at its own join before the parent"
    )
    assert outer["direct"] == {"status": "skipped"}, outer

    states = t085.node_states(t033_stack.dbname, run["id"])
    assert states["direct"] == ["skipped"], states
    for node_id in ("fork", "deepA", "deepB", "join2", "join", "end"):
        assert states[node_id] == ["pending", "running", "succeeded"], (node_id, states)

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan
