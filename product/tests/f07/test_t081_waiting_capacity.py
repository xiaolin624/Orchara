"""F07/T081 acceptance: waiting capacity, fair child claiming and convergence.

Every case drives the compiled API over real HTTP/mTLS, real PostgreSQL 16.4, the
real ``cmd/supervisor exec`` binary and the real quota rows. Child Runs are only
ever created by the contract ``spawnChild`` operation.

Timing: every condition is polled from the authoritative row; the only fixed
durations are process timeouts and the lease-expiry fixture (T038's 120 s age,
well outside the 30 s lease TTL). No sub-5-second wall-clock assertion exists.
"""

from __future__ import annotations

from support import t029, t033, t035, t036, t038, t075, t078, t081
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t029_stack,
)
from support import stack as st

CHILD_DEFINITION = t078.child_definition()


def _child_version(stack, creator) -> dict:
    return t078.create_child_version(stack, creator, definition=CHILD_DEFINITION)


def _parent_application(stack, creator, child_version_id: str) -> str:
    definition = t078.parent_definition([child_version_id])
    return t081.parent_application(stack, creator, definition)


def _claim_parent(stack, creator, application_id: str, source: str) -> tuple[dict, dict]:
    parent = t081.submit_parent_run(stack, creator, application_id, {"source": source})
    lease = t081.claim_lease(stack)
    assert lease is not None and lease["run_id"] == parent["id"], (lease, parent)
    lease["_execution_unit_id"] = "unit-t081"
    return parent, lease


def _successful_child_proposal(stack, child_id: str, lease: dict, *, output: bytes) -> None:
    """A real succeeded proposal with schema-conforming output bytes."""
    artifacts = t033.artifact_client(stack, "worker")
    artifact_id, _sha = t033.upload_run_artifact(
        stack, artifacts, lease, content=output,
        purpose="final_output", media_type="application/json", relation="output",
    )
    supervisor = t029.execution_client(stack, "supervisor")
    proposed = supervisor.submit_result(
        child_id, attempt_token=lease["attempt_token"],
        suggested_state="succeeded", output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-t081",
        evidence=t029.exit_evidence(lease),
    )
    assert exited.status == 200, exited.text


def _failed_child_proposal(stack, child_id: str, lease: dict) -> None:
    """A real succeeded proposal whose bytes violate the child's output schema.

    The T078 finalizer rejects it (child_output_schema_invalid) and fails the
    child; the parent side is T081's.
    """
    artifacts = t033.artifact_client(stack, "worker")
    artifact_id, _sha = t033.upload_run_artifact(
        stack, artifacts, lease, content=b'{"bogus":1}',
        purpose="final_output", media_type="application/json", relation="output",
    )
    supervisor = t029.execution_client(stack, "supervisor")
    proposed = supervisor.submit_result(
        child_id, attempt_token=lease["attempt_token"],
        suggested_state="succeeded", output_artifact_id=artifact_id,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-t081",
        evidence=t029.exit_evidence(lease),
    )
    assert exited.status == 200, exited.text


def test_twenty_waiting_parents_do_not_starve_the_child_pool(t029_stack) -> None:
    """20 parents parked in waiting_children; children still claim and converge.

    The Python execution-slot pool is saturated to one *running* top-level Run
    before the children execute, so a child claim can only succeed on the
    independent child pool. The child executions are real supervisor invocations.
    """
    admin, creator = t075.creator(t029_stack)
    chain = st.migration_chain(t029_stack.dbname)
    assert "0050_f07_waiting_capacity.sql" in chain.values(), chain
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])

    # The child pool is seeded by the migration with the documented capacity.
    counter = t081.child_counter(t029_stack.dbname)
    assert counter is not None, "0050 must seed global/child_slots"
    assert counter["capacity"] == 8, counter
    assert counter["used"] == 0, counter

    # One top-level Run holds the only Python execution slot for the whole test.
    # It is a real Queued->running claim; it is never executed or exited.
    t081.set_execution_capacity(t029_stack.dbname, 40)
    saturating = t081.submit_parent_run(
        t029_stack, creator, application_id, {"source": "saturating"}
    )
    saturating_lease = t081.claim_lease(t029_stack)
    assert saturating_lease is not None and saturating_lease["run_id"] == saturating["id"]
    assert t081.execution_counter(t029_stack.dbname)["used"] == 1

    # 20 real parents: claim -> spawnChild -> waiting_children -> exit. Each exit
    # releases its Python slot (used returns to the one saturating Run), so the
    # pool is never the reason a parent cannot be claimed. The child pool is
    # deliberately closed (capacity 0) during this *setup* phase so the claim
    # under test is unambiguously the parent's own start job; the independent
    # child-pool behaviour is proven in the second phase below.
    t081.set_child_capacity(t029_stack.dbname, 0)
    parents = []
    for index in range(20):
        source = f"parent-{index}"
        parent, lease = _claim_parent(t029_stack, creator, application_id, source)
        child = t081.spawn_child(
            t029_stack, parent["id"], lease, logical_key="call_app:child",
            application_version_id=child_version["id"], input_value={"source": source},
        )
        t081.park_waiting(t029_stack, lease, child_run_id=child["id"])
        parents.append({"parent": parent, "lease": lease, "child": child})

    # Every parent is waiting, released its Python slot, and no child has run yet.
    for entry in parents:
        row = t081.run_row(t029_stack.dbname, entry["parent"]["id"])
        assert row["state"] == "waiting_children", row
        waiting = t081.waiting_row(t029_stack.dbname, entry["parent"]["id"])
        assert waiting is not None and waiting["kind"] == "children", waiting
        assert waiting["exit_confirmed_at"] is not None, waiting
        jobs = t081.job_rows_for_run(t029_stack.dbname, entry["parent"]["id"])
        assert [job["state"] for job in jobs] == ["held"], jobs
        assert all(
            allocation["released_at"] is not None
            for allocation in t081.allocations_for_job(t029_stack.dbname, jobs[0]["id"])
        ), t081.allocations_for_job(t029_stack.dbname, jobs[0]["id"])
        child_row = t081.run_row(t029_stack.dbname, entry["child"]["id"])
        assert child_row["state"] == "queued", child_row

    assert t081.execution_counter(t029_stack.dbname)["used"] == 1
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    t081.assert_slot_consistent(t029_stack.dbname)

    # Saturate the Python execution pool: the one saturating Run owns the only
    # slot (0016's CHECK keeps capacity >= used). A new top-level claim cannot
    # succeed; only children on the independent pool can.
    t081.set_execution_capacity(t029_stack.dbname, 1)

    # One child slot: every real supervisor invocation claims exactly one child
    # and the child graph (start -> end) completes.
    t081.set_child_capacity(t029_stack.dbname, 1)
    child_ids = {entry["child"]["id"] for entry in parents}
    completed = []
    for _ in range(20):
        report, _completed = t081.execute_once(t029_stack)
        assert report["status"] == "executed", report
        assert report["outcome"] == "succeeded", report
        assert report["run_id"] in child_ids, report
        completed.append(report["run_id"])
        # The Python pool stays saturated by the same running top-level Run.
        assert t081.execution_counter(t029_stack.dbname)["used"] == 1

    assert set(completed) == child_ids, (len(set(completed)), len(child_ids))

    for child_id in child_ids:
        row = t081.run_row(t029_stack.dbname, child_id)
        assert row["state"] == "succeeded", row
        assert row["output_artifact_id"], row
        assert row["finished_at"] is not None, row

    # The child pool is fully released; the Python pool is still exactly the one
    # saturating top-level Run. No top-level quota was ever consumed by a child.
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    assert t081.execution_counter(t029_stack.dbname)["used"] == 1
    for entry in parents:
        counter = t081.parent_counter(t029_stack.dbname, entry["parent"]["id"])
        assert counter is not None and counter["used"] == 0, counter
    t081.assert_slot_consistent(t029_stack.dbname)

    # The terminal hook (not the T035 bridge) resolved each parent's wait and
    # queued exactly one resume job; those parents are queued but blocked on the
    # saturated Python pool, which is the documented independent-pool behaviour.
    for entry in parents:
        parent_id = entry["parent"]["id"]
        decision = t081.waiting_decision(t029_stack.dbname, parent_id)
        assert decision is not None, (parent_id, t081.waiting_row(t029_stack.dbname, parent_id))
        assert decision["kind"] == "children" and decision["outcome"] == "succeeded", decision
        assert decision["child_run_id"] == entry["child"]["id"], decision
        jobs = t081.resume_jobs(t029_stack.dbname, parent_id)
        assert len(jobs) == 1 and jobs[0]["state"] == "ready", jobs
        parent_row = t081.run_row(t029_stack.dbname, parent_id)
        assert parent_row["state"] == "queued", parent_row

    # Nothing is claimable any more: 20 parent resume jobs are blocked by the
    # saturated Python pool and every child slot was returned.
    assert t081.claim_lease(t029_stack) is None
    # The saturating Run is untouched and still owns the only Python slot.
    assert t081.run_row(t029_stack.dbname, saturating["id"])["state"] == "running"
    assert t029.attempt_count(t029_stack.dbname) == 20 + 20 + 1


def test_per_parent_parallel_limit_and_fair_claiming(t029_stack) -> None:
    """One parent cannot monopolise the child pool; rotation lets the other advance.

    Parent A enqueues four children, parent B one. The real claim endpoint must
    hand A the first, then B (rotation, even though A's remaining jobs are older),
    cap A at three concurrent child attempts, and hand A its fourth only after a
    slot is released. The assertions read the authoritative counters/allocations.
    """
    admin, creator = t075.creator(t029_stack)
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])
    t081.set_execution_capacity(t029_stack.dbname, 20)
    t081.set_child_capacity(t029_stack.dbname, 0)

    parent_a, lease_a = _claim_parent(t029_stack, creator, application_id, "a-parent")
    children_a = [
        t081.spawn_child(
            t029_stack, parent_a["id"], lease_a, logical_key=f"a-{index}",
            application_version_id=child_version["id"], input_value={"source": f"a-{index}"},
        )
        for index in range(4)
    ]
    parent_b, lease_b = _claim_parent(t029_stack, creator, application_id, "b-parent")
    child_b = t081.spawn_child(
        t029_stack, parent_b["id"], lease_b, logical_key="b-0",
        application_version_id=child_version["id"], input_value={"source": "b-0"},
    )
    assert len(t081.run_children_rows(t029_stack.dbname, parent_a["id"])) == 4
    t081.set_child_capacity(t029_stack.dbname, 8)

    # 1. A gets the first claim although both parents are unclaimed: the older
    # child wins, and the rotation fact is recorded on the parent Run.
    first = t081.claim_child(t029_stack)
    assert first["run_id"] == children_a[0]["id"], first
    row_a = t081.run_row(t029_stack.dbname, parent_a["id"])
    assert row_a["state"] == "running"
    rotation_a = t081.run_row(t029_stack.dbname, parent_a["id"])["last_child_claim_at"]
    assert rotation_a is not None, row_a
    assert t081.run_row(t029_stack.dbname, parent_b["id"])["last_child_claim_at"] is None

    # 2. B's child is newer than A's remaining jobs, yet it is claimed next: A
    # just consumed a child claim, so it rotates behind B.
    second = t081.claim_child(t029_stack)
    assert second["run_id"] == child_b["id"], second
    assert t081.run_row(t029_stack.dbname, parent_b["id"])["last_child_claim_at"] is not None

    # 3. A's second and third children claim (A now holds three concurrent).
    third = t081.claim_child(t029_stack)
    fourth = t081.claim_child(t029_stack)
    assert {third["run_id"], fourth["run_id"]} == {children_a[1]["id"], children_a[2]["id"]}

    # 4. The per-parent cap refuses A's fourth while three of its children run.
    assert t081.claim_lease(t029_stack) is None, "the fourth child exceeded the per-parent cap"
    counter_a = t081.parent_counter(t029_stack.dbname, parent_a["id"])
    assert counter_a == {
        "scope": "parent:" + parent_a["id"], "dimension": "child_slots",
        "used": 3, "capacity": 3,
    }, counter_a
    counter_b = t081.parent_counter(t029_stack.dbname, parent_b["id"])
    assert counter_b["used"] == 1, counter_b
    assert t081.child_counter(t029_stack.dbname)["used"] == 4
    t081.assert_slot_consistent(t029_stack.dbname)
    for lease in (first, second, third, fourth):
        allocations = t081.allocations_for_job(t029_stack.dbname, lease["job_id"])
        assert len(allocations) == 2, allocations
        assert {row["scope"] for row in allocations} == {"global", "parent:" + (
            parent_a["id"] if lease in (first, third, fourth) else parent_b["id"]
        )}
        assert all(row["released_at"] is None for row in allocations), allocations

    # 5. Free one A slot through the real result+exit protocol; A's fourth job is
    # then claimable and the counters move by exactly one. The first child
    # succeeds, so fail-fast propagation is not involved here.
    _successful_child_proposal(
        t029_stack, children_a[0]["id"], first, output=b'{"echo":"a-0"}'
    )
    t081.wait_run_state(t029_stack.dbname, children_a[0]["id"], {"succeeded"})
    fifth = t081.claim_child(t029_stack)
    assert fifth["run_id"] == children_a[3]["id"], fifth
    counter_a = t081.parent_counter(t029_stack.dbname, parent_a["id"])
    assert counter_a["used"] == 3, counter_a
    assert t081.child_counter(t029_stack.dbname)["used"] == 4
    t081.assert_slot_consistent(t029_stack.dbname)
    assert all(
        row["released_at"] is not None
        for row in t081.allocations_for_job(t029_stack.dbname, first["job_id"])
    )

def _ordering(stack, *, earlier_run: str, later_run: str, column: str = "finished_at") -> bool:
    """Authoritative database-clock ordering between two Run timestamps."""
    rows = st.sql_rows(
        stack.dbname,
        f"SELECT (earlier.{column} <= later.{column}) AS ordered "
        "FROM platform.runs AS earlier, platform.runs AS later "
        f"WHERE earlier.id = {st.quote_literal(earlier_run)}::uuid "
        f"AND later.id = {st.quote_literal(later_run)}::uuid",
    )
    assert len(rows) == 1, rows
    return bool(rows[0]["ordered"])


def test_parent_cancel_waits_for_a_running_child_and_releases_once(t029_stack) -> None:
    """Cancel converges only after the controlled child stopped; no double release.

    The parent is waiting (no live attempt of its own) while one child holds a real
    live lease. The public cancel records the intent, moves the parent to
    cancelling and propagates the intent to the child. The parent must stay
    cancelling until the child's exit evidence commits its terminal state; then the
    lifecycle hook re-drives the finalizer exactly once. Every late replay must
    leave the allocation ledger untouched.
    """
    admin, creator = t075.creator(t029_stack)
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])
    parent, lease = _claim_parent(t029_stack, creator, application_id, "cancel-parent")
    child = t081.spawn_child(
        t029_stack, parent["id"], lease, logical_key="call_app:child",
        application_version_id=child_version["id"], input_value={"source": "cancel-parent"},
    )
    t081.park_waiting(t029_stack, lease, child_run_id=child["id"])

    child_lease = t081.claim_child(t029_stack, expected_child_id=child["id"])
    child_job = child_lease["job_id"]
    allocations = t081.allocations_for_job(t029_stack.dbname, child_job)
    assert len(allocations) == 2, allocations
    assert all(row["released_at"] is None for row in allocations), allocations

    cancelled = t036.cancel_run(creator, parent["id"])
    assert cancelled.status == 202, cancelled.text
    parent_row = t081.run_row(t029_stack.dbname, parent["id"])
    assert parent_row["state"] == "cancelling", parent_row
    assert parent_row["cancel_requested_at"] is not None, parent_row
    assert parent_row["finished_at"] is None, parent_row
    # The intent reached the child; it is still a live controlled unit.
    child_row = t081.wait_run_state(t029_stack.dbname, child["id"], {"cancelling"})
    assert child_row["finished_at"] is None, child_row
    # No decision was fabricated: cancel wins over the waiting resume.
    assert t081.waiting_decision(t029_stack.dbname, parent["id"]) is None
    assert t081.run_row(t029_stack.dbname, parent["id"])["state"] == "cancelling"
    assert all(
        row["released_at"] is None
        for row in t081.allocations_for_job(t029_stack.dbname, child_job)
    )

    # The child's own exit evidence is what lets the parent converge; the child
    # finalizer commits cancelled because the propagated cancel intent wins.
    supervisor = t029.execution_client(t029_stack, "supervisor")
    exited = supervisor.confirm_exit(
        child_lease["attempt_token"], execution_unit_id="unit-t081",
        evidence=t029.exit_evidence(child_lease),
    )
    assert exited.status == 200, exited.text
    t081.wait_run_state(t029_stack.dbname, child["id"], {"cancelled"})
    t081.wait_run_state(t029_stack.dbname, parent["id"], {"cancelled"})
    assert _ordering(t029_stack, earlier_run=child["id"], later_run=parent["id"]), (
        t081.run_row(t029_stack.dbname, child["id"]),
        t081.run_row(t029_stack.dbname, parent["id"]),
    )

    # Both child-pool allocations were released exactly once.
    allocations = t081.allocations_for_job(t029_stack.dbname, child_job)
    assert len(allocations) == 2, allocations
    assert all(row["released_at"] is not None for row in allocations), allocations
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    assert t081.parent_counter(t029_stack.dbname, parent["id"])["used"] == 0
    t081.assert_slot_consistent(t029_stack.dbname)

    # A cancel race replay never releases a second time and never re-emits a
    # terminal transition: a repeated cancel, a repeated exit and a child cancel
    # are all idempotent no-ops on the ledger.
    ledger_before = t081.release_ledger(t029_stack.dbname)
    counters_before = (t081.child_counter(t029_stack.dbname), t081.parent_counter(
        t029_stack.dbname, parent["id"]))
    assert t036.cancel_run(creator, parent["id"]).status == 202
    assert supervisor.confirm_exit(
        child_lease["attempt_token"], execution_unit_id="unit-t081",
        evidence=t029.exit_evidence(child_lease),
    ).status == 200
    assert t036.cancel_run(creator, child["id"]).status == 202
    assert t081.release_ledger(t029_stack.dbname) == ledger_before
    assert (t081.child_counter(t029_stack.dbname), t081.parent_counter(
        t029_stack.dbname, parent["id"])) == counters_before
    assert t081.run_row(t029_stack.dbname, parent["id"])["state"] == "cancelled"
    assert t081.run_row(t029_stack.dbname, child["id"])["state"] == "cancelled"


def test_a_required_child_failure_fails_the_parent_after_it_stopped(t029_stack) -> None:
    """A required child's failure propagates; convergence waits for the child.

    The real supervisor parks the parent, the child's real result is rejected by
    its frozen output schema (child_output_schema_invalid), the lifecycle hook
    resolves the wait as failed, and a second real supervisor invocation resumes
    the parent, which fails with the child's stable code.
    """
    admin, creator = t075.creator(t029_stack)
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])
    parent = t081.submit_parent_run(t029_stack, creator, application_id, {"source": "fail-source"})

    first, _ = t081.execute_once(t029_stack)
    assert first["status"] == "executed" and first["run_id"] == parent["id"], first
    assert first["outcome"] == "waiting_children", first
    waiting = t081.waiting_row(t029_stack.dbname, parent["id"])
    assert waiting is not None and waiting["kind"] == "children", waiting
    child_id = waiting["checkpoint_id"].split(":", 1)[1]
    child_lease = t081.claim_child(t029_stack, expected_child_id=child_id)
    _failed_child_proposal(t029_stack, child_id, child_lease)
    child_row = t081.wait_run_state(t029_stack.dbname, child_id, {"failed"})
    assert child_row["error_code"] == "child_output_schema_invalid", child_row

    # The hook (not the bridge) resolved the wait once, deterministically naming
    # the failed child, and queued exactly one resume job.
    decision = t081.wait_for(
        lambda: t081.waiting_decision(t029_stack.dbname, parent["id"]), timeout=15.0
    )
    assert decision == {"kind": "children", "outcome": "failed",
                       "child_run_id": child_id}, decision
    jobs = t081.resume_jobs(t029_stack.dbname, parent["id"])
    assert len(jobs) == 1 and jobs[0]["state"] == "ready", jobs
    decided_after_child = st.sql_rows(
        t029_stack.dbname,
        "SELECT (child.finished_at <= waiting.decision_at) AS ordered "
        "FROM platform.runs AS child, platform.run_waitings AS waiting "
        f"WHERE child.id = {st.quote_literal(child_id)}::uuid "
        f"AND waiting.run_id = {st.quote_literal(parent['id'])}::uuid",
    )
    assert decided_after_child[0]["ordered"], decided_after_child

    resumed, _ = t081.execute_once(t029_stack)
    assert resumed["status"] == "executed", resumed
    assert resumed["run_id"] == parent["id"], resumed
    assert resumed["outcome"] == "failed", resumed
    parent_row = t081.wait_run_state(t029_stack.dbname, parent["id"], {"failed"})
    assert parent_row["error_code"] == "child_output_schema_invalid", parent_row
    assert _ordering(t029_stack, earlier_run=child_id, later_run=parent["id"])
    # Exactly one child Run exists: the resume replayed the delegation, it did not
    # create a second one.
    assert len(t081.run_children_rows(t029_stack.dbname, parent["id"])) == 1
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    assert t081.parent_counter(t029_stack.dbname, parent["id"])["used"] == 0
    assert len(t029.attempts_for_run(t029_stack.dbname, parent["id"])) == 2


def test_child_pool_allocation_is_released_once_on_recovery(t029_stack, tmp_path) -> None:
    """The recovery path releases both child-pool allocations exactly once.

    A live child attempt's lease expires, the real ``sweep-leases`` parks it and
    keeps the slot (T038's documented conservative park), and ``runs recover``
    releases the slot once and requeues it. A re-claim re-arms the same allocation
    rows; a repeated recover is refused and changes no ledger row.
    """
    admin, creator = t075.creator(t029_stack)
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])
    parent, lease = _claim_parent(t029_stack, creator, application_id, "recovery-parent")
    child = t081.spawn_child(
        t029_stack, parent["id"], lease, logical_key="call_app:child",
        application_version_id=child_version["id"], input_value={"source": "recovery-parent"},
    )
    t081.park_waiting(t029_stack, lease, child_run_id=child["id"])
    child_lease = t081.claim_child(t029_stack, expected_child_id=child["id"])
    child_job = child_lease["job_id"]
    assert t081.child_counter(t029_stack.dbname)["used"] == 1
    assert t081.parent_counter(t029_stack.dbname, parent["id"])["used"] == 1

    t038.expire_lease(t029_stack.dbname, child_lease["attempt_token"])
    _completed, report = t038.sweep(t029_stack, "--grace", "1s")
    assert report["parked"] == 1, report
    parked = t081.wait_run_state(t029_stack.dbname, child["id"], {"recovery_pending"})
    assert parked["finished_at"] is None, parked
    # The conservative park keeps the slot allocated: the unit is not yet proven
    # gone.
    assert all(
        row["released_at"] is None
        for row in t081.allocations_for_job(t029_stack.dbname, child_job)
    ), t081.allocations_for_job(t029_stack.dbname, child_job)

    evidence = t038.recovery_evidence("unit-t081")
    t038.recover_ok(t029_stack, child["id"], evidence=evidence, directory=tmp_path)
    t081.wait_run_state(t029_stack.dbname, child["id"], {"queued"})
    allocations = t081.allocations_for_job(t029_stack.dbname, child_job)
    assert len(allocations) == 2, allocations
    assert all(row["released_at"] is not None for row in allocations), allocations
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    assert t081.parent_counter(t029_stack.dbname, parent["id"])["used"] == 0
    t081.assert_slot_consistent(t029_stack.dbname)

    # Re-claiming re-arms exactly the same allocation rows (the UNIQUE key).
    reclaimed = t081.claim_child(t029_stack, expected_child_id=child["id"])
    allocations = t081.allocations_for_job(t029_stack.dbname, reclaimed["job_id"])
    assert len(allocations) == 2, allocations
    assert all(row["released_at"] is None for row in allocations), allocations
    assert t081.child_counter(t029_stack.dbname)["used"] == 1
    assert t081.parent_counter(t029_stack.dbname, parent["id"])["used"] == 1

    ledger_before = t081.release_ledger(t029_stack.dbname)
    _completed, refused = t038.recover(t029_stack, child["id"], evidence=evidence,
                                       directory=tmp_path)
    assert refused.get("status") == "refused", refused
    assert t081.release_ledger(t029_stack.dbname) == ledger_before
    assert t081.child_counter(t029_stack.dbname)["used"] == 1


def test_a_child_that_finished_before_the_parent_parks_is_reconciled(t029_stack) -> None:
    """The crash/fast-child window: the terminal hook saw a running parent.

    A fast child can reach a terminal state *before* its parent's waiting
    proposal+exit are recorded, so the post-commit hook is a no-op. The wait then
    has no decision even though the parent is parked. The bounded Reconcile that
    runs before the next claim must resolve it from the durable rows (this is the
    same replay a process restart depends on).
    """
    admin, creator = t075.creator(t029_stack)
    child_version = _child_version(t029_stack, creator)
    application_id = _parent_application(t029_stack, creator, child_version["id"])
    parent, lease = _claim_parent(t029_stack, creator, application_id, "fast-parent")
    child = t081.spawn_child(
        t029_stack, parent["id"], lease, logical_key="call_app:child",
        application_version_id=child_version["id"], input_value={"source": "fast-parent"},
    )

    # The real supervisor executes the child while the parent is still running.
    report, _completed = t081.execute_once(t029_stack)
    assert report["run_id"] == child["id"] and report["outcome"] == "succeeded", report
    assert t081.run_row(t029_stack.dbname, parent["id"])["state"] == "running"

    # Only now does the parent park: no child-terminal event will follow, so the
    # wait is unresolved by design.
    t081.park_waiting(t029_stack, lease, child_run_id=child["id"])
    waiting = t081.waiting_row(t029_stack.dbname, parent["id"])
    assert waiting["decision"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting

    # The next real claim first replays the lifecycle reconcile; it resolves the
    # wait and claims the single resume job in the same request.
    resumed = t081.claim_lease(t029_stack)
    assert resumed is not None and resumed["run_id"] == parent["id"], resumed
    decision = t081.waiting_decision(t029_stack.dbname, parent["id"])
    assert decision == {"kind": "children", "outcome": "succeeded",
                       "child_run_id": child["id"]}, decision
    jobs = t081.resume_jobs(t029_stack.dbname, parent["id"])
    assert len(jobs) == 1 and jobs[0]["state"] == "leased", jobs
    assert len(t029.attempts_for_run(t029_stack.dbname, parent["id"])) == 2
    # The child pool was released when the child exited; the resume attempt is a
    # top-level attempt and takes the Python execution slot again.
    assert t081.child_counter(t029_stack.dbname)["used"] == 0
    assert t081.execution_counter(t029_stack.dbname)["used"] == 1
