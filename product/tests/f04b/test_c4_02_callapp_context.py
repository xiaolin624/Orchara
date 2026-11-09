"""C4.02/T153 real-stack acceptance: the shared explicit call_app execution contract.

Both cases drive the product's real path: a real draft saved through the public
API (validated by the Go workflow compiler), the real debug submission, the
compiled ``cmd/supervisor exec --once`` claiming the attempt, the contract
``spawnChild`` internal operation creating the child Run, the F03/T035 waiting
hand-over, the F07/T081 child-terminal hook, the F04B recovery seam and the real
PostgreSQL run facts.

The Go execution authority is ``internal/presetexec`` (ADR 0034); this module is
the real-process acceptance, not a semantic proof. It pins the C4.02 criteria that
are observable from outside the executor:

* the linear ``start -> call_app -> end`` chain (which the graph scheduler never
  sees) parks and resumes through the scheduler-owned waiting hand-over: the
  call_app node publishes only its own pending/running facts, the Run is not
  terminal while it waits, and the one waiting row carries the checkpoint and the
  confirmed exit;
* the graph chain reuses a completed call_app node's sealed output on the resumed
  attempt (the node reports only its terminal ``succeeded`` fact), while the two
  sequential calls to the same fixed child version stay two independent child
  Runs with distinct ``call_app:<node>`` invocation keys.
"""

from __future__ import annotations

import json

from support import run_observations as observations
from support import t025, t027, t035, t075, t078, t081, t089
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

CREATE = {"valid": True, "errors": []}


def _child_version(stack, creator) -> dict:
    return t078.create_child_version(stack, creator)


def _assert_scheduler_owned_waiting(
    stack, parent_id: str, child_id: str, *, attempts: int = 1
) -> dict:
    """The durable hand-over only the owning scheduler can write."""
    waiting = t081.waiting_row(stack.dbname, parent_id)
    assert waiting is not None, "the parent did not park in waiting_children"
    assert waiting["kind"] == "children", waiting
    assert waiting["checkpoint_id"] == "child:" + child_id, waiting
    assert waiting["exit_confirmed_at"] is not None, waiting
    assert waiting["decision"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting
    # The parked attempt's job is held; the current stop has no resume job yet.
    jobs = t081.job_rows_for_run(stack.dbname, parent_id)
    assert len(jobs) == attempts, jobs
    assert jobs[-1]["state"] == "held", jobs
    assert jobs[-1]["kind"] == ("start" if attempts == 1 else "resume"), jobs
    assert all(job["state"] in {"held", "completed"} for job in jobs), jobs
    assert len(t081.resume_jobs(stack.dbname, parent_id)) == attempts - 1, jobs
    return waiting


def test_linear_call_app_waiting_is_committed_only_by_the_owning_scheduler(t033_stack) -> None:
    """The linear ``start -> call_app -> end`` chain never commits the waiting result
    itself: the node only publishes its own facts and the scheduler submits the one
    checkpoint and confirms the exit."""
    _admin, creator = t075.creator(t033_stack)
    child = _child_version(t033_stack, creator)
    definition = t078.parent_definition([child["id"]])
    application_id = t027.application_with_definition(creator, definition)
    validation = t025.validate(creator, application_id)
    assert validation.json == CREATE, validation.text
    revision = t027.draft_revision(creator, application_id)
    submitted = t027.submit_debug(
        creator, application_id, draft_revision=revision,
        input_value={"source": "parent-source"},
    )
    assert submitted.status == 202, submitted.text
    parent_id = submitted.json["id"]

    # 1. The real supervisor claims the linear parent, spawns exactly one child and
    # hands the run over. The node itself published only pending/running facts and
    # the Run is not terminal: the scheduler wrote the waiting row and the exit.
    first, _ = t078.execute_once(t033_stack)
    assert first["status"] == "executed", first
    assert first["run_id"] == parent_id, first
    assert first["outcome"] == "waiting_children", first
    child_ids = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert len(child_ids) == 1, child_ids
    child_id = child_ids[0]
    _assert_scheduler_owned_waiting(t033_stack, parent_id, child_id)
    parent_row = t078.run_row(t033_stack.dbname, parent_id)
    assert parent_row["state"] == "waiting_children", parent_row
    assert parent_row["error_code"] is None, parent_row
    assert parent_row["output_artifact_id"] is None, parent_row
    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["child"] == ["pending", "running"], states["child"]
    assert states["start"] == ["pending", "running", "succeeded"], states["start"]
    receipt = t078.run_children_row(t033_stack.dbname, parent_id, "call_app:child")
    assert receipt is not None, t078.run_children_rows(t033_stack.dbname, parent_id)
    assert receipt["child_version_id"] == child["id"], receipt
    assert receipt["depth"] == 1, receipt
    assert t078.artifact_bytes(t033_stack, receipt["input_artifact_id"]) == b'{"source":"parent-source"}'
    assert t078.run_row(t033_stack.dbname, child_id)["state"] == "queued"

    # 2. The child runs its own graph to a terminal result; only then does the
    # F07/T081 terminal hook create the one resume job.
    second, _ = t078.execute_once(t033_stack)
    assert second["run_id"] == child_id and second["outcome"] == "succeeded", second
    decision = t089.wait_decision(t033_stack, parent_id)
    assert decision == {
        "kind": "children", "outcome": "succeeded", "child_run_id": child_id,
    }, decision
    resumed = t078.wait_for_resume_job(t033_stack.dbname, parent_id)
    assert resumed["resume_job_id"], resumed

    # 3. The resumed attempt replays the same child (never a second Run) and the
    # linear scheduler finishes the chain with the child's structured result.
    third, _ = t078.execute_once(t033_stack)
    assert third["status"] == "executed", third
    assert third["run_id"] == parent_id, third
    assert third["outcome"] == "succeeded", third
    parent_row = observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})
    assert parent_row["output_artifact_id"], parent_row
    output = observations.artifact_content(creator, parent_row["output_artifact_id"])
    assert json.loads(output.raw) == {"echo": "parent-source"}, output.raw
    assert t078.child_count(t033_stack.dbname, parent_id) == 1
    assert len(t078.run_children_rows(t033_stack.dbname, parent_id)) == 1
    assert len(t078.attempt_rows(t033_stack.dbname, parent_id)) == 2

    # 4. The resumed attempt is observable as the same node running again (no
    # duplicated wait and no node-authored terminal fact in the first attempt).
    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["child"] == ["pending", "running", "pending", "running", "succeeded"], states
    assert states["end"] == ["pending", "running", "succeeded"], states["end"]
    assert t035.waiting_count(t033_stack.dbname) == 1
    assert t081.execution_counter(t033_stack.dbname)["used"] == 0


def test_graph_call_app_reuses_its_sealed_output_and_keeps_the_two_logical_calls(t033_stack) -> None:
    """Two sequential calls to the same fixed version stay independent child Runs,
    and the resumed attempt reuses the completed call_app output instead of
    delegating again."""
    _admin, creator = t075.creator(t033_stack)
    child = _child_version(t033_stack, creator)
    definition = t089.same_child_twice_definition(child["id"])
    parent = t089.submit_parent(
        t033_stack, creator, definition, {"requires_approval": True}
    )
    parent_id = parent["id"]

    # 1. The first graph attempt spawns the first child and parks.
    first, _ = t089.execute_once(t033_stack)
    assert first["run_id"] == parent_id and first["outcome"] == "waiting_children", first
    first_child = t078.child_run_ids(t033_stack.dbname, parent_id)[0]
    _assert_scheduler_owned_waiting(t033_stack, parent_id, first_child)

    # 2. The first child runs; the resumed attempt replays it, registers the first
    # call_app output and parks on the second call to the same version.
    second, _ = t089.execute_once(t033_stack)
    assert second["run_id"] == first_child and second["outcome"] == "succeeded", second
    t089.wait_resume(t033_stack, parent_id, child_run_id=first_child)
    third, _ = t089.execute_once(t033_stack)
    assert third["run_id"] == parent_id and third["outcome"] == "waiting_children", third
    children = t078.child_run_ids(t033_stack.dbname, parent_id)
    assert len(children) == 2, children
    second_child = [item for item in children if item != first_child][0]
    _assert_scheduler_owned_waiting(
        t033_stack, parent_id, second_child, attempts=2
    )

    # 3. The second child runs; the final attempt reuses the completed first
    # call_app output (terminal fact only, no pending/running) and replays the
    # second child, then finishes the graph.
    fourth, _ = t089.execute_once(t033_stack)
    assert fourth["run_id"] == second_child and fourth["outcome"] == "succeeded", fourth
    t089.wait_resume(t033_stack, parent_id, child_run_id=second_child)
    fifth, _ = t089.execute_once(t033_stack)
    assert fifth["status"] == "executed", fifth
    assert fifth["run_id"] == parent_id and fifth["outcome"] == "succeeded", fifth
    observations.wait_run_state(t033_stack.dbname, parent_id, {"succeeded"})
    assert len(t078.attempt_rows(t033_stack.dbname, parent_id)) == 3

    # 4. The logical-key identity survives: two independent child Runs of the same
    # fixed version, distinct invocation keys, threads, snapshots and outputs.
    receipts = t078.run_children_rows(t033_stack.dbname, parent_id)
    assert len(receipts) == 2, receipts
    assert [row["parent_invocation_key"] for row in receipts] == [
        "call_app:first", "call_app:second",
    ], receipts
    assert {row["child_version_id"] for row in receipts} == {child["id"]}, receipts
    rows = [t078.run_row(t033_stack.dbname, run_id) for run_id in (first_child, second_child)]
    assert rows[0]["checkpoint_thread_id"] != rows[1]["checkpoint_thread_id"], rows
    assert rows[0]["execution_snapshot_id"] != rows[1]["execution_snapshot_id"], rows
    outputs = {
        run_id: t089.child_output(t033_stack, creator, run_id)
        for run_id in (first_child, second_child)
    }
    assert outputs == {
        first_child: {"echo": "first"},
        second_child: {"echo": "second"},
    }, outputs

    # 5. The recovered node states prove the reuse: `first` reports pending/running
    # once (the attempt that registered its output) and only `succeeded` on the
    # final attempt; `second` re-runs its own wait and settles once.
    states = observations.node_states(t033_stack.dbname, parent_id)
    assert states["first"] == [
        "pending", "running", "pending", "running", "succeeded", "succeeded",
    ], states["first"]
    assert states["second"] == [
        "pending", "running", "pending", "running", "succeeded",
    ], states["second"]
    assert states["direct"] == ["skipped"], states["direct"]
    assert states["join"] == ["pending", "running", "succeeded"], states["join"]
    assert states["end"] == ["pending", "running", "succeeded"], states["end"]

    # 6. The join envelope (the region's direct sources) carries the second
    # call_app result, and each node result row names its own child Run.
    items = observations.node_result_items(creator, parent_id, expected=4)
    results = observations.node_data_document(creator, items, "join")["results"]
    assert set(results) == {"second", "direct"}, results
    assert results["second"] == {"status": "succeeded", "value": {"echo": "second"}}, results
    assert results["direct"] == {"status": "skipped"}, results
    labels = sorted(
        item["label"] for item in items if item["node_id"] in ("first", "second")
    )
    assert labels == sorted(
        ["child-run:" + first_child, "child-run:" + second_child]
    ), labels
    parent_row = t078.run_row(t033_stack.dbname, parent_id)
    final = json.loads(observations.artifact_content(creator, parent_row["output_artifact_id"]).raw)
    assert final == {"results": results}, final
