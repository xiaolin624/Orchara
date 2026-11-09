"""F04B/T089 acceptance: call_app inside a real graph run, its child Runs and hand-over.

Every case goes through the product's real path: a real draft saved through the
public API (validated by the Go workflow compiler), the real debug submission,
the compiled ``cmd/supervisor exec --once`` claiming the attempt and running the
structured region, the contract ``spawnChild`` internal operation creating the
child Run, the F03/T035 waiting hand-over (proposal + exit + one resume job), the
F07/T081 child-terminal hook, the F04B recovery seam (``NodeOutputs``), the
owner-only children/node-results reads and the PostgreSQL run facts.

Fixture boundary, stated honestly: the child application version is the T075
fixture (real ``POST /validate`` -> sealed canonical manifest bytes -> version
row; the publication review transaction is the documented skipped step). The
expected child failure in the failure-propagation case is produced by the real
finalizer rejecting test-owned output bytes (T078/T081 precedent), not by a
mocked child.
"""

from __future__ import annotations

import json

from support import run_observations as observations
from support import t025, t027, t029, t033, t075, t078, t081, t089
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

JOIN_ENVELOPE = {
    "results": {
        "child": {"status": "succeeded", "value": {"echo": "alpha"}},
        "direct": {"status": "skipped"},
    }
}

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


def _child_version(stack, creator, *, definition: dict | None = None) -> dict:
    return t078.create_child_version(stack, creator, definition=definition)


def _live_children(stack, parent_id: str) -> list[str]:
    return [
        child_id
        for child_id in t078.child_run_ids(stack.dbname, parent_id)
        if t078.run_row(stack.dbname, child_id)["state"] not in TERMINAL_STATES
    ]


def _drive_until_terminal(
    stack, parent_id: str, *, max_steps: int = 12, diagnostics: list[str] | None = None
) -> list[dict]:
    """Drive real supervisor attempts until the parent Run is terminal.

    The F04B/T085 scheduler runs ready branches concurrently and parks the whole
    attempt when *any* branch waits, so a fork with two call_app branches may
    create one or both child Runs in the first attempt (the cancelled sibling is
    re-driven on the next one). This helper therefore executes whatever is
    runnable: a live child Run first, and a parent resume only after every known
    child is terminal and the one resume job exists.
    """
    reports: list[dict] = []
    for _ in range(max_steps):
        live = _live_children(stack, parent_id)
        if live:
            report, completed = t089.execute_once(stack)
            if diagnostics is not None:
                diagnostics.append(completed.stderr or "")
            assert report["run_id"] in live, (report, live)
            assert report["outcome"] == "succeeded", report
            reports.append(report)
            continue
        t089.wait_resume(stack, parent_id)
        report, completed = t089.execute_once(stack)
        if diagnostics is not None:
            diagnostics.append(completed.stderr or "")
        assert report["run_id"] == parent_id, report
        reports.append(report)
        if report["outcome"] in TERMINAL_STATES:
            return reports
        assert report["outcome"] == "waiting_children", report
    raise AssertionError(f"the parent run {parent_id} did not converge: {reports}")


def _owner_and_child(stack):
    _admin, creator = t075.creator(stack)
    child = _child_version(stack, creator)
    return creator, child


def _prepare_parent(stack, creator, definition: dict, input_value: dict):
    """Create the application, assert publish validation, then submit the debug run."""
    application_id = t027.application_with_definition(creator, definition)
    validation = t025.validate(creator, application_id)
    assert validation.status == 200, validation.text
    revision = t027.draft_revision(creator, application_id)
    response = t027.submit_debug(
        creator, application_id, draft_revision=revision, input_value=input_value
    )
    assert response.status == 202, response.text
    return application_id, validation, response.json


def _assert_waiting_handover(stack, parent_id: str, child_id: str) -> dict:
    waiting = t081.waiting_row(stack.dbname, parent_id)
    assert waiting is not None, "the parent did not park in waiting_children"
    assert waiting["kind"] == "children", waiting
    assert waiting["checkpoint_id"] == "child:" + child_id, waiting
    assert waiting["exit_confirmed_at"] is not None, waiting
    assert waiting["decision"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting
    jobs = t081.job_rows_for_run(stack.dbname, parent_id)
    assert [job["kind"] for job in jobs] == ["start"], jobs
    assert [job["state"] for job in jobs] == ["held"], jobs
    return waiting


def test_single_child_waits_then_resumes_with_the_original_structured_result(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.single_child_definition(child["id"])
    application_id, validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    assert validation.json == {"valid": True, "errors": []}, validation.text
    manifest_hash = validation.header("X-Manifest-SHA256")
    assert manifest_hash and len(manifest_hash) == 64, manifest_hash
    parent_id = parent["id"]

    # 1. The real supervisor claims the parent, spawns exactly one child and parks.
    first, _ = t089.execute_once(t033_stack)
    assert first["status"] == "executed", first
    assert first["run_id"] == parent_id, first
    assert first["outcome"] == "waiting_children", first

    child_ids = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert len(child_ids) == 1, child_ids
    child_id = child_ids[0]
    _assert_waiting_handover(t033_stack, parent_id, child_id)
    receipt = t078.run_children_row(t033_stack.dbname, parent_id, "call_app:child")
    assert receipt is not None, t078.run_children_rows(t033_stack.dbname, parent_id)
    assert receipt["child_version_id"] == child["id"], receipt
    assert receipt["depth"] == 1, receipt
    sealed_input = t078.artifact_bytes(t033_stack, receipt["input_artifact_id"])
    assert sealed_input == b'{"source":"alpha"}', sealed_input
    assert receipt["required"] is True, receipt
    parent_row = t078.run_row(t033_stack.dbname, parent_id)
    child_row = t078.run_row(t033_stack.dbname, child_id)
    assert child_row["state"] == "queued", child_row
    assert child_row["application_version_id"] == child["id"], child_row
    assert child_row["checkpoint_thread_id"] != parent_row["checkpoint_thread_id"]
    assert child_row["execution_snapshot_id"] != parent_row["execution_snapshot_id"]
    assert child_row["kind"] == "published", child_row

    # 2. The next real supervisor invocation runs the child's whole graph.
    second, _ = t089.execute_once(t033_stack)
    assert second["status"] == "executed", second
    assert second["run_id"] == child_id, second
    assert second["outcome"] == "succeeded", second
    child_row = observations.wait_run_state(t033_stack.dbname, child_id, {"succeeded"})
    assert child_row["output_artifact_id"], child_row
    child_output = t089.child_output(t033_stack, creator, child_id)
    assert child_output == {"echo": "alpha"}, child_output

    # 3. Only now (child terminal + parent exit confirmed) does exactly one resume
    # job exist; the F07/T081 terminal hook owns it, not the test.
    decision = t089.wait_decision(t033_stack, parent_id)
    assert decision == {"kind": "children", "outcome": "succeeded", "child_run_id": child_id}, decision
    resumed = t089.wait_resume(t033_stack, parent_id, child_run_id=child_id)
    jobs = t081.resume_jobs(t033_stack.dbname, parent_id)
    assert len(jobs) == 1 and jobs[0]["state"] == "ready", jobs
    ordered = st.sql_rows(
        t033_stack.dbname,
        "SELECT (child.finished_at <= waiting.decision_at) AS ordered "
        "FROM platform.runs AS child, platform.run_waitings AS waiting "
        f"WHERE child.id = {st.quote_literal(child_id)}::uuid "
        f"AND waiting.run_id = {st.quote_literal(parent_id)}::uuid",
    )
    assert ordered[0]["ordered"], ordered
    assert resumed["resume_job_id"] == jobs[0]["id"]

    # 4. The resume re-enters the call_app node, replays the *same* child (never a
    # second one) and finishes the parent with the child's structured result.
    third, _ = t089.execute_once(t033_stack)
    assert third["status"] == "executed", third
    assert third["run_id"] == parent_id, third
    assert third["outcome"] == "succeeded", third
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})
    assert parent_row["output_artifact_id"], parent_row
    envelope = observations.node_data_document(
        creator,
        observations.node_result_items(creator, parent_id, expected=3),
        "join",
    )
    assert envelope == JOIN_ENVELOPE, envelope
    output_content = observations.artifact_content(creator, parent_row["output_artifact_id"])
    assert json.loads(output_content.raw) == JOIN_ENVELOPE, output_content.raw
    assert t078.child_count(t033_stack.dbname, parent_id) == 1
    assert len(t078.run_children_rows(t033_stack.dbname, parent_id)) == 1

    # 5. The node output is the child's structured result plus platform facts; no
    # framework thread/namespace/messages object is exposed as a public field.
    items = observations.node_result_items(creator, parent_id, expected=3)
    child_rows = [
        item for item in items
        if item["node_id"] == "child" and item["kind"] == "data"
    ]
    assert len(child_rows) == 1, items
    assert child_rows[0]["label"] == "child-run:" + child_id, child_rows[0]
    child_doc = observations.node_data_document(creator, items, "child")
    assert child_doc == {"echo": "alpha"}, child_doc
    for forbidden in ("thread", "checkpoint_thread_id", "namespace", "messages", "subgraph"):
        assert forbidden not in child_doc, (forbidden, child_doc)

    children = t089.children(t033_stack, creator, parent_id)
    assert [item["id"] for item in children] == [child_id], children
    assert children[0]["parent_run_id"] == parent_id
    assert children[0]["state"] == "succeeded"

    # 6. The durable node.status sequence keeps the resumption observable. T085's
    # final skip publication emits no skips during the waiting hand-over, so the
    # unselected branch is skipped exactly once, in the resumed attempt.
    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["child"] == ["pending", "running", "pending", "running", "succeeded"], states
    assert states["direct"] == ["skipped"], states
    assert states["choose"] == ["pending", "running", "succeeded", "succeeded"], states
    assert states["join"] == ["pending", "running", "succeeded"], states
    assert states["end"] == ["pending", "running", "succeeded"], states


def test_parallel_call_apps_create_two_independent_children_and_join_both(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.parallel_definition(child["id"])
    _application_id, validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"marker": "parent-only"}
    )
    assert validation.json == {"valid": True, "errors": []}, validation.text
    parent_id = parent["id"]

    # 1. The first attempt launches both fork branches concurrently. Both call_app
    # nodes create their own child and the scheduler parks the attempt at the first
    # stable child checkpoint; a cancelled sibling is re-driven on the next attempt.
    diagnostics: list[str] = []
    first, first_completed = t089.execute_once(t033_stack)
    diagnostics.append(first_completed.stderr or "")
    assert first["run_id"] == parent_id and first["outcome"] == "waiting_children", first
    waiting = t081.waiting_row(t033_stack.dbname, parent_id)
    assert waiting is not None and waiting["kind"] == "children", waiting
    assert waiting["checkpoint_id"].startswith("child:"), waiting
    assert waiting["exit_confirmed_at"] is not None, waiting
    assert waiting["decision"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting
    spawned = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert 1 <= len(spawned) <= 2, spawned
    assert waiting["checkpoint_id"] in {"child:" + child_id for child_id in spawned}, waiting

    # 2. Execute every live child; a resumed parent may spawn the cancelled
    # sibling later, so the driver keeps going until the run is terminal.
    reports = _drive_until_terminal(t033_stack, parent_id, diagnostics=diagnostics)
    assert reports[-1]["run_id"] == parent_id, reports
    assert reports[-1]["outcome"] == "succeeded", reports
    if diagnostics and any("refused" in entry or "not recorded" in entry for entry in diagnostics):
        print("SUPERVISOR_STDERR", json.dumps(diagnostics))
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})
    child_ids = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert len(child_ids) == 2, (child_ids, reports)
    assert parent_row["output_artifact_id"]

    # 3. Both invocations are independent child Runs: different ids, invocation
    # keys, checkpoint threads, snapshots, outputs and structured results.
    receipts = t078.run_children_rows(t033_stack.dbname, parent_id)
    assert len(receipts) == 2, receipts
    assert {row["parent_invocation_key"] for row in receipts} == {
        "call_app:a", "call_app:b",
    }, receipts
    assert {row["child_version_id"] for row in receipts} == {child["id"]}, receipts
    rows = [t078.run_row(t033_stack.dbname, run_id) for run_id in child_ids]
    assert rows[0]["state"] == rows[1]["state"] == "succeeded", rows
    assert rows[0]["checkpoint_thread_id"] != rows[1]["checkpoint_thread_id"], rows
    assert rows[0]["execution_snapshot_id"] != rows[1]["execution_snapshot_id"], rows
    assert rows[0]["output_artifact_id"] != rows[1]["output_artifact_id"], rows
    parent_thread = t078.run_row(t033_stack.dbname, parent_id)["checkpoint_thread_id"]
    assert all(row["checkpoint_thread_id"] != parent_thread for row in rows), rows

    outputs = {
        run_id: t089.child_output(t033_stack, creator, run_id)
        for run_id in child_ids
    }
    assert sorted(entry["echo"] for entry in outputs.values()) == ["alpha", "beta"], outputs
    assert outputs[child_ids[0]] != outputs[child_ids[1]], outputs

    # 4. The join envelope carries both sources independently and the end maps it.
    items = observations.node_result_items(creator, parent_id, expected=3)
    results = observations.node_data_document(creator, items, "join")["results"]
    assert set(results) == {"a", "b"}, results
    assert results["a"] == {"status": "succeeded", "value": {"echo": "alpha"}}, results["a"]
    assert results["b"] == {"status": "succeeded", "value": {"echo": "beta"}}, results["b"]
    final = json.loads(observations.artifact_content(creator, parent_row["output_artifact_id"]).raw)
    assert final == {"results": results}, final

    # 5. Each call_app node reports its own child result; no cross-talk and no
    # framework object in the node data.
    by_node = {}
    for item in items:
        if item["kind"] == "data" and item["node_id"] in ("a", "b"):
            by_node.setdefault(item["node_id"], []).append(item)
    assert set(by_node) == {"a", "b"}, by_node
    assert all(len(entries) == 1 for entries in by_node.values()), by_node
    docs = {
        node_id: observations.node_data_document(creator, items, node_id)
        for node_id in ("a", "b")
    }
    assert docs["a"] == {"echo": "alpha"}, docs
    assert docs["b"] == {"echo": "beta"}, docs
    labels = {
        node_id: by_node[node_id][0]["label"] for node_id in ("a", "b")
    }
    assert set(labels.values()) == {"child-run:" + run_id for run_id in child_ids}, labels

    states = observations.node_states(t033_stack.dbname, parent_id)
    print("PARALLEL_NODE_STATES", json.dumps(states, sort_keys=True))
    for node_id in ("a", "b"):
        # T085's concurrent scheduler cancels the sibling branch once one branch
        # parks: that cancelled attempt may record a transient `failed` node.status
        # before the run moves to waiting_children. The run itself never fails and
        # the resumed attempt settles both nodes as succeeded (asserted below), so
        # only the final state and the successful result are the T089 contract.
        assert states[node_id][-1] == "succeeded", states
        assert "succeeded" in states[node_id], states
    children = t089.children(t033_stack, creator, parent_id)
    assert {item["id"] for item in children} == set(child_ids)


def test_two_sequential_calls_to_the_same_child_are_not_deduped(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.same_child_twice_definition(child["id"])
    _application_id, validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    assert validation.json == {"valid": True, "errors": []}, validation.text
    parent_id = parent["id"]

    first, _ = t089.execute_once(t033_stack)
    assert first["run_id"] == parent_id and first["outcome"] == "waiting_children", first
    first_child = t078.child_run_ids(t033_stack.dbname, parent_id)[0]
    second, _ = t089.execute_once(t033_stack)
    assert second["run_id"] == first_child and second["outcome"] == "succeeded", second
    t089.wait_resume(t033_stack, parent_id, child_run_id=first_child)

    # The same parent node path runs the second call_app node: a *new* child Run
    # because the invocation key is derived from the node id, never the app id.
    third, _ = t089.execute_once(t033_stack)
    assert third["run_id"] == parent_id, third
    assert third["outcome"] == "waiting_children", third
    children = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert len(children) == 2, children
    second_child = [item for item in children if item != first_child][0]
    fourth, _ = t089.execute_once(t033_stack)
    assert fourth["run_id"] == second_child and fourth["outcome"] == "succeeded", fourth
    t089.wait_resume(t033_stack, parent_id, child_run_id=second_child)
    fifth, _ = t089.execute_once(t033_stack)
    assert fifth["run_id"] == parent_id and fifth["outcome"] == "succeeded", fifth
    observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})

    receipts = t078.run_children_rows(t033_stack.dbname, parent_id)
    assert len(receipts) == 2, receipts
    assert [row["parent_invocation_key"] for row in receipts] == [
        "call_app:first", "call_app:second",
    ], receipts
    assert {row["child_version_id"] for row in receipts} == {child["id"]}, receipts
    rows = [t078.run_row(t033_stack.dbname, run_id) for run_id in (first_child, second_child)]
    assert rows[0]["checkpoint_thread_id"] != rows[1]["checkpoint_thread_id"], rows
    outputs = {
        run_id: t089.child_output(t033_stack, creator, run_id)
        for run_id in (first_child, second_child)
    }
    assert outputs == {
        first_child: {"echo": "first"},
        second_child: {"echo": "second"},
    }, outputs

    items = observations.node_result_items(creator, parent_id, expected=4)
    results = observations.node_data_document(creator, items, "join")["results"]
    assert results["second"] == {"status": "succeeded", "value": {"echo": "second"}}, results
    assert results["direct"] == {"status": "skipped"}, results
    labels = sorted(
        item["label"] for item in items if item["node_id"] in ("first", "second")
    )
    assert labels == sorted(["child-run:" + first_child, "child-run:" + second_child]), labels


def test_a_required_child_failure_fails_the_parent_with_the_childs_code(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.single_child_definition(child["id"])
    _application_id, validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    assert validation.json == {"valid": True, "errors": []}, validation.text
    parent_id = parent["id"]

    first, _ = t089.execute_once(t033_stack)
    assert first["outcome"] == "waiting_children", first
    child_id = t078.child_run_ids(t033_stack.dbname, parent_id)[0]

    # The child's own frozen output schema rejects the finalizer's bytes; the
    # child fails with its documented stable code.
    child_lease = t081.claim_child(t033_stack, expected_child_id=child_id)
    t089.fail_child_with_bad_output(t033_stack, child_id, child_lease)
    child_row = observations.wait_run_state(t033_stack.dbname, child_id, {"failed"})
    assert child_row["error_code"] == "child_output_schema_invalid", child_row

    decision = t089.wait_decision(t033_stack, parent_id)
    assert decision == {
        "kind": "children", "outcome": "failed", "child_run_id": child_id,
    }, decision
    t089.wait_resume(t033_stack, parent_id, child_run_id=child_id)

    # The resumed attempt re-reads the same failed child and propagates its code;
    # no second child Run is created to "retry" the unknown result.
    resumed, _ = t089.execute_once(t033_stack)
    assert resumed["status"] == "executed", resumed
    assert resumed["run_id"] == parent_id, resumed
    assert resumed["outcome"] == "failed", resumed
    assert resumed["error_code"] == "child_output_schema_invalid", resumed
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"failed"})
    assert parent_row["error_code"] == "child_output_schema_invalid", parent_row
    assert parent_row["output_artifact_id"] is None, parent_row
    assert t078.child_count(t033_stack.dbname, parent_id) == 1
    assert len(t078.run_children_rows(t033_stack.dbname, parent_id)) == 1

    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["child"][-1] == "failed", states
    # T085's final skip publication emits no skips during the waiting hand-over;
    # the failed resumed attempt skips the not-yet-reachable tail exactly once.
    assert states["end"] == ["skipped"], states
    assert states["join"] == ["skipped"], states


def test_the_waiting_handover_releases_and_reoccupies_the_execution_slot(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.single_child_definition(child["id"])
    _application_id, _validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    parent_id = parent["id"]
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0
    t081.assert_slot_consistent(t033_stack.dbname)

    first, _ = t089.execute_once(t033_stack)
    assert first["outcome"] == "waiting_children", first
    child_id = t078.child_run_ids(t033_stack.dbname, parent_id)[0]
    waiting = _assert_waiting_handover(t033_stack, parent_id, child_id)
    assert waiting["exit_confirmed_at"] is not None

    # The Python execution slot was released by the exit confirmation, before any
    # resume existed.
    start_job = t081.job_rows_for_run(t033_stack.dbname, parent_id)[0]
    allocations = t081.allocations_for_job(t033_stack.dbname, start_job["id"])
    assert len(allocations) == 1, allocations
    assert allocations[0]["scope"] == "global", allocations
    assert allocations[0]["dimension"] == "execution_slots", allocations
    assert allocations[0]["released_at"] is not None, allocations
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0
    assert t081.child_counter(t033_stack.dbname)["used"] == 0
    t081.assert_slot_consistent(t033_stack.dbname)

    # The child's independent attempt takes the child pool, not the Python slot.
    child_lease = t081.claim_child(t033_stack, expected_child_id=child_id)
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0
    assert t081.child_counter(t033_stack.dbname)["used"] == 1
    child_allocations = t081.allocations_for_job(t033_stack.dbname, child_lease["job_id"])
    assert len(child_allocations) == 2, child_allocations
    assert {row["scope"] for row in child_allocations} == {
        "global", "parent:" + parent_id,
    }, child_allocations
    assert all(row["released_at"] is None for row in child_allocations), child_allocations

    # Complete the real child attempt through the documented protocol so the
    # child succeeds and the terminal hook creates the one resume job.
    artifacts = t033.artifact_client(t033_stack, "worker")
    artifact_id, _sha = t033.upload_run_artifact(
        t033_stack, artifacts, child_lease, content=b'{"echo":"alpha"}',
        purpose="final_output", media_type="application/json", relation="output",
    )
    supervisor = t029.execution_client(t033_stack, "supervisor")
    proposed = supervisor.submit_result(
        child_id, attempt_token=child_lease["attempt_token"],
        suggested_state="succeeded", output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        child_lease["attempt_token"], execution_unit_id="unit-t081",
        evidence=t029.exit_evidence(child_lease),
    )
    assert exited.status == 200, exited.text
    observations.wait_run_state(t033_stack.dbname, child_id, {"succeeded"})
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0
    assert t081.child_counter(t033_stack.dbname)["used"] == 0
    assert all(
        row["released_at"] is not None
        for row in t081.allocations_for_job(t033_stack.dbname, child_lease["job_id"])
    ), t081.allocations_for_job(t033_stack.dbname, child_lease["job_id"])
    t081.assert_slot_consistent(t033_stack.dbname)

    resume_row = t089.wait_resume(t033_stack, parent_id, child_run_id=child_id)
    resume_jobs = t081.resume_jobs(t033_stack.dbname, parent_id)
    assert len(resume_jobs) == 1 and resume_jobs[0]["state"] == "ready", resume_jobs
    assert resume_row["resume_job_id"] == resume_jobs[0]["id"]
    # No slot is held by the queued resume before it is claimed.
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0

    # The real supervisor claims the resume: the Python slot is re-occupied,
    # the resumed attempt adopts the original child result and finishes.
    final, _ = t089.execute_once(t033_stack)
    assert final["run_id"] == parent_id and final["outcome"] == "succeeded", final
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})
    assert parent_row["output_artifact_id"]
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0
    assert t081.child_counter(t033_stack.dbname)["used"] == 0
    resume_allocations = t081.allocations_for_job(t033_stack.dbname, resume_jobs[0]["id"])
    assert len(resume_allocations) == 1, resume_allocations
    assert resume_allocations[0]["released_at"] is not None, resume_allocations
    # Each job's allocation row was released exactly once (one row per holder).
    assert len(t081.allocations_for_job(t033_stack.dbname, start_job["id"])) == 1
    t081.assert_slot_consistent(t033_stack.dbname)
    assert t078.child_count(t033_stack.dbname, parent_id) == 1


def test_mapping_errors_are_located_by_the_publish_gate(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)

    # 1. A binding whose statically known type the child schema refuses.
    application_id = t027.application_with_definition(
        creator, t089.mapping_type_definition(child["id"])
    )
    validation = t025.validate(creator, application_id)
    assert validation.status == 200, validation.text
    errors = t025.validation_errors(validation)
    mismatch = [error for error in errors if error["code"] == "io_mapping_type_mismatch"]
    assert mismatch, errors
    t025.assert_location(
        mismatch[0], code="io_mapping_type_mismatch", pointer="/nodes/2/inputs/source",
        node_id="child",
    )
    assert mismatch[0]["message"], mismatch[0]

    # The debug submission is refused before any Run/job exists.
    revision = t027.draft_revision(creator, application_id)
    refused = t027.submit_debug(
        creator, application_id, draft_revision=revision, input_value={"requires_approval": True}
    )
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == "validation_failed", refused.text
    assert any(
        error["code"] == "io_mapping_type_mismatch"
        and error["pointer"] == "/nodes/2/inputs/source"
        for error in refused.json["error"]["details"]["errors"]
    ), refused.text

    # 2. A required child input with no mapping (stricter frozen child version).
    strict = _child_version(t033_stack, creator, definition=t089.strict_child_definition())
    strict_application = t027.application_with_definition(
        creator, t089.mapping_missing_definition(strict["id"])
    )
    strict_validation = t025.validate(creator, strict_application)
    assert strict_validation.status == 200, strict_validation.text
    strict_errors = t025.validation_errors(strict_validation)
    missing = [error for error in strict_errors if error["code"] == "io_mapping_missing_field"]
    assert missing, strict_errors
    located = {
        (error.get("node_id"), error["pointer"])
        for error in missing
    }
    assert located == {
        ("child", "/nodes/2/inputs/region"),
        ("direct", "/nodes/3/inputs/region"),
    }, located
    strict_revision = t027.draft_revision(creator, strict_application)
    strict_refused = t027.submit_debug(
        creator, strict_application, draft_revision=strict_revision,
        input_value={"requires_approval": True},
    )
    assert strict_refused.status == 422, strict_refused.text
    assert strict_refused.json["error"]["code"] == "validation_failed", strict_refused.text


def test_the_executor_refuses_a_binding_it_cannot_resolve_at_run_time(t033_stack) -> None:
    creator, child = _owner_and_child(t033_stack)
    definition = t089.defective_binding_definition(child["id"])
    application_id, validation, parent = _prepare_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    # The publication gate cannot see values: the optional input pointer is a
    # legal reference, so this definition validates and runs.
    assert validation.json == {"valid": True, "errors": []}, validation.text
    parent_id = parent["id"]

    report, _completed = t089.execute_once(t033_stack)
    assert report["status"] == "executed", report
    assert report["run_id"] == parent_id, report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == "node_input_invalid", report
    # The executor keeps its located pointer (<node>.<input field>) in the
    # operator report; the owner API exposes only the stable code.
    assert "child.source" in report.get("error", ""), report
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"failed"})
    assert parent_row["error_code"] == "node_input_invalid", parent_row
    assert t078.child_count(t033_stack.dbname, parent_id) == 0
    assert len(t078.run_children_rows(t033_stack.dbname, parent_id)) == 0

    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["child"] == ["pending", "running", "failed"], states
    assert states["join"] == ["skipped"], states
    assert states["end"] == ["skipped"], states
