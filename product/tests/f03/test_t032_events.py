"""T032: durable run events and the worker event submission endpoint.

Every call is a real mTLS request to the compiled API against a real PostgreSQL
database. The assertions read ``platform.run_events`` rows and the Run's
``next_event_seq`` counter: seq allocation, idempotency, payload validation and
same-transaction rollback are database facts, not handler conventions.
"""

from __future__ import annotations

import json

from support import stack as st
from support import t027, t029, t031, t032
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t032_stack,
)

RUN_STATUS = "run.status"


def claimed_run(stack, owner, *, execution_unit_id: str = "unit-t032") -> dict:
    run = t032.queued_run(stack, owner)
    response = t032.execution_client(stack).claim_job(
        worker_id="t032-supervisor", execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


def submit_failed_result(stack, lease: dict, *, error_code: str = "tool_crashed") -> None:
    response = t032.execution_client(stack, "worker").submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="failed",
        error_code=error_code,
    )
    assert response.status == 200, response.text


def confirm_exit(stack, lease: dict) -> None:
    response = t032.execution_client(stack).confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-t032",
        evidence=t032.exit_evidence(lease),
    )
    assert response.status == 200, response.text


# ---------------------------------------------------------------------------
# controller transitions emit run.status in the business transaction
# ---------------------------------------------------------------------------
def test_debug_run_creation_emits_exactly_one_queued_event(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)

    events = t032.run_events(t032_stack.dbname, run["id"])
    assert len(events) == 1, events
    assert events[0]["type"] == RUN_STATUS
    assert events[0]["seq"] == 1
    assert events[0]["revision"] == 1
    assert t032.payload_of(events[0]) == {"state": "queued"}
    assert t032.next_event_seq(t032_stack.dbname, run["id"]) == 2


def test_claim_emits_exactly_one_running_event(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)

    statuses = t032.status_events(t032_stack.dbname, lease["run_id"])
    assert statuses == [(1, 1, {"state": "queued"}), (2, 2, {"state": "running"})], statuses
    assert t032.next_event_seq(t032_stack.dbname, lease["run_id"]) == 3


def test_result_and_terminal_finalizer_each_emit_exactly_one_event(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    submit_failed_result(t032_stack, lease)

    statuses = t032.status_events(t032_stack.dbname, lease["run_id"])
    assert statuses == [
        (1, 1, {"state": "queued"}),
        (2, 2, {"state": "running"}),
        (3, 2, {"state": "running"}),  # proposal: phase changes, state does not
    ], statuses

    confirm_exit(t032_stack, lease)

    statuses = t032.status_events(t032_stack.dbname, lease["run_id"])
    assert statuses == [
        (1, 1, {"state": "queued"}),
        (2, 2, {"state": "running"}),
        (3, 2, {"state": "running"}),
        (4, 3, {"state": "failed", "error_code": "tool_crashed"}),
    ], statuses
    events = t032.run_events(t032_stack.dbname, lease["run_id"])
    assert [row["seq"] for row in events] == [1, 2, 3, 4]
    assert len({row["event_id"] for row in events}) == 4
    assert t032.next_event_seq(t032_stack.dbname, lease["run_id"]) == 5

    # Idempotent replays never append a second terminal event.
    replay = t032.execution_client(t032_stack).confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-t032",
        evidence=t032.exit_evidence(lease),
    )
    assert replay.status == 200, replay.text
    late = t032.execution_client(t032_stack, "worker").submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="failed",
        error_code="tool_crashed",
    )
    assert late.status == 409, late.text
    assert len(t032.run_events(t032_stack.dbname, lease["run_id"])) == 4


def test_requeue_recovery_and_needs_reconciliation_are_projected(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)

    # Exit without a result: recovery_pending is committed and projected.
    parked = claimed_run(t032_stack, owner, execution_unit_id="unit-park")
    response = t032.execution_client(t032_stack).confirm_exit(
        parked["attempt_token"],
        execution_unit_id="unit-park",
        evidence=t032.exit_evidence(parked),
    )
    assert response.status == 200, response.text
    assert t032.status_events(t032_stack.dbname, parked["run_id"])[-1] == (
        3,
        3,
        {"state": "recovery_pending"},
    )

    # needs_reconciliation is non-terminal but a committed state transition.
    reconciling = claimed_run(t032_stack, owner, execution_unit_id="unit-reconcile")
    response = t032.execution_client(t032_stack, "worker").submit_result(
        reconciling["run_id"],
        attempt_token=reconciling["attempt_token"],
        suggested_state="needs_reconciliation",
    )
    assert response.status == 200, response.text
    response = t032.execution_client(t032_stack).confirm_exit(
        reconciling["attempt_token"],
        execution_unit_id="unit-reconcile",
        evidence=t032.exit_evidence(reconciling),
    )
    assert response.status == 200, response.text
    statuses = t032.status_events(t032_stack.dbname, reconciling["run_id"])
    assert statuses[-1][0] == 4 and statuses[-1][2] == {"state": "needs_reconciliation"}, statuses

    # started=false: the run returns to queued and that aggregate transition is
    # projected too (running -> queued, revision+1). This is last because the
    # requeued job becomes claimable again (the claim loop would pick it up).
    requeued = claimed_run(t032_stack, owner, execution_unit_id="unit-requeue")
    response = t032.execution_client(t032_stack).confirm_exit(
        requeued["attempt_token"],
        execution_unit_id="unit-requeue",
        evidence=t032.exit_evidence(requeued, started=False),
    )
    assert response.status == 200, response.text
    assert t032.status_events(t032_stack.dbname, requeued["run_id"]) == [
        (1, 1, {"state": "queued"}),
        (2, 2, {"state": "running"}),
        (3, 3, {"state": "queued"}),
    ]


# ---------------------------------------------------------------------------
# worker events: acceptance, seq allocation, idempotency, local_sequence
# ---------------------------------------------------------------------------
def test_a_worker_node_status_is_accepted_and_duplicate_ids_do_not_consume_seq(
    t032_stack,
) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    worker = t032.execution_client(t032_stack, "worker")
    run_id = lease["run_id"]

    first = t032.node_event(local_sequence=1, node_id="n1", state="running", attempt_no=1)
    response = t032.submit_events(worker, run_id, lease["attempt_token"], [first])
    assert response.status == 200, response.text
    assert set(response.json) == {"id", "state"}, response.json
    assert response.json == {"id": run_id, "state": "accepted"}, response.json

    rows = t032.run_events(t032_stack.dbname, run_id)
    assert len(rows) == 3, rows
    assert rows[-1]["type"] == "node.status"
    assert rows[-1]["seq"] == 3 and rows[-1]["revision"] == 2
    assert t032.payload_of(rows[-1]) == {
        "node_id": "n1",
        "state": "running",
        "attempt_no": 1,
    }
    counter = t032.next_event_seq(t032_stack.dbname, run_id)
    assert counter == 4

    # Same event_id + same content (even a different local_sequence, which is
    # diagnostic only) is the idempotent no-op: one row, no new seq.
    duplicate = {**first, "local_sequence": 7}
    response = t032.submit_events(worker, run_id, lease["attempt_token"], [duplicate])
    assert response.status == 200, response.text
    assert t032.next_event_seq(t032_stack.dbname, run_id) == counter
    assert t032.run_event_count(t032_stack.dbname, run_id) == 3

    # Same event_id, different content: the first observation is authoritative.
    conflict = t032.node_event(
        event_id=first["event_id"],
        local_sequence=2,
        node_id="n1",
        state="succeeded",
        attempt_no=1,
    )
    response = t032.submit_events(worker, run_id, lease["attempt_token"], [conflict])
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "event_conflict", response.text
    assert t032.next_event_seq(t032_stack.dbname, run_id) == counter
    assert t032.run_event_count(t032_stack.dbname, run_id) == 3


def test_two_new_events_in_one_batch_get_consecutive_seqs_in_request_order(
    t032_stack,
) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    worker = t032.execution_client(t032_stack, "worker")
    run_id = lease["run_id"]

    # local_sequence 10 then 2: the batch order is the committed order, and
    # local_sequence is never the display order.
    first = t032.node_event(local_sequence=10, node_id="n-a", state="running", attempt_no=1)
    second = t032.node_event(local_sequence=2, node_id="n-b", state="succeeded", attempt_no=2)
    response = t032.submit_events(worker, run_id, lease["attempt_token"], [first, second])
    assert response.status == 200, response.text

    rows = t032.run_events(t032_stack.dbname, run_id)
    assert [row["seq"] for row in rows] == [1, 2, 3, 4], rows
    assert t032.payload_of(rows[2])["node_id"] == "n-a", rows[2]
    assert t032.payload_of(rows[3])["node_id"] == "n-b", rows[3]
    assert t032.payload_of(rows[3])["attempt_no"] == 2
    assert t032.next_event_seq(t032_stack.dbname, run_id) == 5


def test_a_duplicate_id_from_another_run_is_a_conflict(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    first = claimed_run(t032_stack, owner, execution_unit_id="unit-a")
    second = claimed_run(t032_stack, owner, execution_unit_id="unit-b")
    worker = t032.execution_client(t032_stack, "worker")

    event = t032.node_event(node_id="cross-run")
    response = t032.submit_events(worker, first["run_id"], first["attempt_token"], [event])
    assert response.status == 200, response.text

    # The same globally unique event_id cannot be replayed into another Run.
    response = t032.submit_events(worker, second["run_id"], second["attempt_token"], [event])
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "event_conflict", response.text
    assert t032.run_event_count(t032_stack.dbname, second["run_id"]) == 2


# ---------------------------------------------------------------------------
# validation: only node.status, exactly its metadata payload
# ---------------------------------------------------------------------------
def test_worker_event_body_shape_is_closed_and_located(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    worker = t032.execution_client(t032_stack, "worker")
    run_id = lease["run_id"]
    before = t032.next_event_seq(t032_stack.dbname, run_id)

    def located(response, field: str) -> None:
        assert response.status == 422, (field, response.text)
        assert response.json["error"]["code"] == "validation_failed", (field, response.text)
        assert response.json["error"]["details"]["field"] == field, (field, response.text)

    token = lease["attempt_token"]
    located(t032.submit_events(worker, run_id, token, []), "events")

    too_many = [t032.node_event(local_sequence=index + 1) for index in range(101)]
    located(t032.submit_events(worker, run_id, token, too_many), "events")

    located(
        t032.submit_events(worker, run_id, token, [t032.node_event(event_id="not-a-uuid")]),
        "events[0].event_id",
    )
    located(
        t032.submit_events(
            worker, run_id, token, [t032.node_event(local_sequence=0)]
        ),
        "events[0].local_sequence",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [{**t032.node_event(), "type": "run.status"}],
        ),
        "events[0].type",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [{**t032.node_event(), "type": "usage.updated"}],
        ),
        "events[0].type",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [t032.node_event(payload={"node_id": "n", "state": "running"})],
        ),
        "events[0].payload.attempt_no",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [t032.node_event(payload={"node_id": "n", "state": "running", "attempt_no": 0})],
        ),
        "events[0].payload.attempt_no",
    )
    # Body-like keys are not metadata: the exact three-key allowlist refuses them
    # before any row is written.
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [
                t032.node_event(
                    payload={
                        "node_id": "n",
                        "state": "running",
                        "attempt_no": 1,
                        "content": "the final answer body must never ride an event",
                    }
                )
            ],
        ),
        "events[0].payload.content",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [t032.node_event(payload={"node_id": "n", "state": "running", "attempt_no": 1, "delta": "x"})],
        ),
        "events[0].payload.delta",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [t032.node_event(payload={"node_id": "n", "state": "running", "attempt_no": 1, "body": "x"})],
        ),
        "events[0].payload.body",
    )
    located(
        t032.submit_events(
            worker,
            run_id,
            token,
            [{"event_id": t032.new_uuid(), "local_sequence": 1, "type": "node.status"}],
        ),
        "events[0].payload",
    )

    bad_token = t032.submit_events(worker, run_id, "not-a-uuid", [t032.node_event()])
    located(bad_token, "attempt_token")

    unknown_field = t032.submit_events(
        worker,
        run_id,
        token,
        [t032.node_event()],
        body={"attempt_token": token, "events": [t032.node_event()], "extra": True},
    )
    assert unknown_field.status == 400, unknown_field.text
    assert unknown_field.json["error"]["code"] == "invalid_json", unknown_field.text

    assert t032.next_event_seq(t032_stack.dbname, run_id) == before
    assert t032.run_event_count(t032_stack.dbname, run_id) == 2


def test_the_event_endpoint_requires_a_live_attempt_bound_to_the_run(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    first = claimed_run(t032_stack, owner, execution_unit_id="unit-first")
    second = claimed_run(t032_stack, owner, execution_unit_id="unit-second")
    worker = t032.execution_client(t032_stack, "worker")

    # A foreign run's live token is not a credential for this run.
    response = t032.submit_events(
        worker, first["run_id"], second["attempt_token"], [t032.node_event()]
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text

    # A random token is stale, not a 404 that enumerates runs.
    response = t032.submit_events(
        worker, first["run_id"], t032.new_uuid(), [t032.node_event()]
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt", response.text

    # maintenance is not an execution role.
    response = t032.submit_events(
        t032.execution_client(t032_stack, "maintenance"),
        first["run_id"],
        first["attempt_token"],
        [t032.node_event()],
    )
    assert response.status == 403, response.text

    # Malformed and unknown run paths are the contract 404.
    for run_id in ("not-a-uuid", t032.new_uuid()):
        response = t032.submit_events(
            worker, run_id, first["attempt_token"], [t032.node_event()]
        )
        assert response.status == 404, (run_id, response.text)
        assert response.json["error"]["code"] == "run_not_found", response.text

    assert t032.run_event_count(t032_stack.dbname, first["run_id"]) == 2


def test_two_runs_have_independent_seq_spaces(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    first = claimed_run(t032_stack, owner, execution_unit_id="unit-space-a")
    second = claimed_run(t032_stack, owner, execution_unit_id="unit-space-b")
    worker = t032.execution_client(t032_stack, "worker")

    for lease, node in ((first, "node-a"), (second, "node-b")):
        response = t032.submit_events(
            worker,
            lease["run_id"],
            lease["attempt_token"],
            [t032.node_event(node_id=node)],
        )
        assert response.status == 200, response.text

    for lease in (first, second):
        rows = t032.run_events(t032_stack.dbname, lease["run_id"])
        assert [row["seq"] for row in rows] == [1, 2, 3], rows
        assert t032.next_event_seq(t032_stack.dbname, lease["run_id"]) == 4


def test_a_terminal_run_rejects_late_events(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    lease = claimed_run(t032_stack, owner)
    submit_failed_result(t032_stack, lease)
    confirm_exit(t032_stack, lease)
    worker = t032.execution_client(t032_stack, "worker")
    before = t032.run_event_count(t032_stack.dbname, lease["run_id"])

    response = t032.submit_events(
        worker, lease["run_id"], lease["attempt_token"], [t032.node_event()]
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "run_terminal", response.text
    assert t032.run_event_count(t032_stack.dbname, lease["run_id"]) == before


# ---------------------------------------------------------------------------
# same-transaction: an event failure rolls the business transition back
# ---------------------------------------------------------------------------
def test_an_event_insert_failure_rolls_the_claim_back(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    run = t032.queued_run(t032_stack, owner)
    worker = t032.execution_client(t032_stack)
    attempts_before = t029.attempt_count(t032_stack.dbname)
    used_before = t032.quota_counter(t032_stack.dbname)["used"]

    t032.fail_inserts_on(t032_stack.dbname, "run_events", trigger="t032_no_events")
    try:
        response = worker.claim_job(
            worker_id="t032-supervisor", execution_unit_id="unit-fail"
        )
        assert response.status == 500, response.text
        assert response.json["error"]["code"] == "internal_error", response.text

        # The business transition and its event are one transaction: nothing
        # partially committed, no orphan event, no attempt and no slot.
        state = t032.run_state_row(t032_stack.dbname, run["id"])
        assert state["state"] == "queued", state
        assert state["revision"] == 1, state
        jobs = t027.jobs_for_run(t032_stack.dbname, run["id"])
        assert [job["state"] for job in jobs] == ["ready"], jobs
        assert t029.attempt_count(t032_stack.dbname) == attempts_before
        assert t032.quota_counter(t032_stack.dbname)["used"] == used_before
        assert t032.status_events(t032_stack.dbname, run["id"]) == [
            (1, 1, {"state": "queued"})
        ]
    finally:
        t032.clear_insert_failure(t032_stack.dbname, "run_events", trigger="t032_no_events")

    # After removing the fault the same claim succeeds and appends its event.
    response = worker.claim_job(worker_id="t032-supervisor", execution_unit_id="unit-ok")
    assert response.status == 200, response.text
    assert t032.next_event_seq(t032_stack.dbname, run["id"]) == 3


def test_a_failure_after_the_creation_event_rolls_the_whole_debug_run_back(t032_stack) -> None:
    owner = t032.golden_owner(t032_stack)
    revision = t027.draft_revision(owner.creator, owner.application_id)
    runs_before = int(st.sql(t032_stack.dbname, "SELECT count(*) FROM platform.runs"))
    events_before = int(st.sql(t032_stack.dbname, "SELECT count(*) FROM platform.run_events"))

    # The debug transaction inserts the queued event before the start job; a
    # failure at the job step must roll the run *and* its event back.
    t032.fail_inserts_on(t032_stack.dbname, "jobs", trigger="t032_no_jobs")
    try:
        response = t027.submit_debug(
            owner.creator,
            owner.application_id,
            draft_revision=revision,
            input_value={"text": "t032 rollback"},
        )
        assert response.status == 500, response.text
        assert response.json["error"]["code"] == "internal_error", response.text
        assert int(st.sql(t032_stack.dbname, "SELECT count(*) FROM platform.runs")) == runs_before
        assert (
            int(st.sql(t032_stack.dbname, "SELECT count(*) FROM platform.run_events"))
            == events_before
        )
    finally:
        t032.clear_insert_failure(t032_stack.dbname, "jobs", trigger="t032_no_jobs")

    # The idempotency receipt rolled back too: the same submission now creates
    # the run and its first event exactly once.
    response = t027.submit_debug(
        owner.creator,
        owner.application_id,
        draft_revision=revision,
        input_value={"text": "t032 rollback"},
    )
    assert response.status == 202, response.text
    run = response.json
    assert t032.status_events(t032_stack.dbname, run["id"]) == [
        (1, 1, {"state": "queued"})
    ]
