"""F04B/T087 acceptance: the human approval node on the real stack.

Every scenario goes through the product's real path: a real draft saved through
the public API, the real debug submission (sealed manifest + frozen input), the
compiled ``cmd/supervisor exec --once`` claiming the attempt and stopping at the
approval checkpoint, the worker-capacity node-approval route, the contract
approval decision path, the one F03/T035 resume transaction, and PostgreSQL /
object-root reads for the durable facts.

The approval is a **business decision**: the tests assert it writes no
``platform.operations`` row and grants no capability; a later write-risk connector
invocation in the same live run must still park for its own operation approval.

Boundaries (reported, not mocked): dev-stub is irrelevant here (no model call); the
approval-only graphs run no sandbox, so runsc is only needed by the supervisor
process bootstrapping the executor itself.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from support import run_observations as observations
from support import t029, t033, t035, t041, t083, t087
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
    t033_stack_fast_approval_sweep,
)


@pytest.fixture(scope="module")
def sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def canonical_proposal_sha256(proposal: dict) -> str:
    """The independent expected params hash (JCS == sorted-key compact JSON here)."""
    canonical = json.dumps(
        proposal, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def assert_waiting_for_approval(stack, run_id: str) -> dict:
    state = t087.run_row(stack.dbname, run_id)
    assert state["state"] == "waiting_approval", state
    assert state["phase"] == "waiting", state
    waiting = t035.waiting_row(stack.dbname, run_id)
    assert waiting is not None and waiting["kind"] == t035.KIND_APPROVAL, waiting
    assert waiting["resume_job_id"] is None, waiting
    return waiting


# ---------------------------------------------------------------------------
# 1. server-built, idempotent node approval (no operation, no capability grant)
# ---------------------------------------------------------------------------
def test_node_approval_is_a_server_built_business_decision_and_replays_idempotently(
    t033_stack,
) -> None:
    owner, _application_id, run = t087.queue_approval_run(t033_stack, t087.approval_definition())
    lease = t087.claim_next_run(t033_stack)
    assert lease["run_id"] == run["id"], (lease, run)

    response = t087.prepare_node_approval(t033_stack, lease)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {
        "id", "state", "expires_at", "params_sha256", "proposal_artifact_id", "decided_at",
    }, body
    assert body["state"] == "pending", body
    approval = t087.approval_row(t033_stack.dbname, body["id"])
    assert approval is not None, body
    assert approval["operation_id"] is None, approval
    assert approval["node_key"] == "node:approve1", approval
    assert approval["approver_id"] == owner.creator.principal_id, approval
    assert approval["expires_at"], approval
    assert body["params_sha256"] == approval["params_sha256"]
    assert approval["params_sha256"] == canonical_proposal_sha256(
        {"subject": "order-42", "amount": "1250.00"}
    ), approval

    # The sealed proposal is built from server facts: the run, the sealed node,
    # the server canonical hash and the resolved proposal object. The worker's
    # request text never becomes a proposal field other than the bound object.
    proposal = t087.proposal_document(t033_stack, approval)
    assert proposal["kind"] == "node_approval", proposal
    assert proposal["run_id"] == run["id"], proposal
    assert proposal["node_id"] == "approve1" and proposal["node_key"] == "node:approve1", proposal
    assert proposal["title"] == "Approve the order", proposal
    assert proposal["proposal"] == {"subject": "order-42", "amount": "1250.00"}, proposal
    assert proposal["params_sha256"] == approval["params_sha256"], proposal
    assert proposal["manifest_sha256"] and proposal["snapshot_id"], proposal
    assert proposal["snapshot_sha256"], proposal
    assert "start" in proposal["definition_nodes"] and "approve1" in proposal["definition_nodes"], proposal
    assert proposal["expires_at"], proposal

    # The approver reads it through the real contract surfaces; a stranger sees
    # nothing and the administrator is not the approver.
    fetched = t087.get_approval(owner.creator, approval["id"])
    assert fetched.status == 200, fetched.text
    assert fetched.json["state"] == "pending", fetched.json
    content = observations.artifact_content(owner.creator, approval["proposal_artifact_id"])
    assert content.status == 200, content.text
    assert json.loads(content.raw)["node_id"] == "approve1"
    stranger = t033.minimal_owner(t033_stack)
    assert t087.get_approval(stranger.creator, approval["id"]).status == 404

    # The durable approval-required event is a projection of the committed row.
    events = t087.run_events(t033_stack.dbname, run["id"], "approval.required")
    assert len(events) == 1, events
    assert events[0]["payload"] == {
        "approval_id": approval["id"],
        "expires_at": proposal["expires_at"],
    }, events[0]

    # Idempotent replay of the same canonical proposal: same row, no second
    # approval, no second event.
    replay = t087.prepare_node_approval(t033_stack, lease)
    assert replay.status == 201, replay.text
    assert replay.json["id"] == approval["id"], replay.json
    assert replay.json["state"] == "pending", replay.json
    assert len(t087.approval_rows(t033_stack.dbname, run_id=run["id"])) == 1
    assert len(t087.run_events(t033_stack.dbname, run["id"], "approval.required")) == 1

    # A node approval is not an operation: no operations row, no usage ledger,
    # no capability grant, no node success and no published node output.
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert t087.usage_ledger_rows(t033_stack.dbname, run["id"]) == []
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    assert observations.node_states(t033_stack.dbname, run["id"]) == {}


# ---------------------------------------------------------------------------
# 1b. the sealed definition owns the title and the approval window
# ---------------------------------------------------------------------------
def test_the_server_takes_the_approval_title_and_window_from_the_sealed_definition(
    t033_stack,
) -> None:
    owner, _application_id, run = t087.queue_approval_run(
        t033_stack, t087.approval_definition()
    )
    sealed = next(
        node
        for node in t087.approval_definition()["nodes"]
        if node["type"] == "approval"
    )["config"]
    lease = t087.claim_next_run(t033_stack)
    forged = t087.prepare_body(lease, title="Forged title", expires_seconds=86400)
    response = t087.prepare_node_approval(t033_stack, lease, body=forged)
    assert response.status == 201, response.text

    approval = t087.approval_row(t033_stack.dbname, response.json["id"])
    assert approval is not None, response.json
    proposal = t087.proposal_document(t033_stack, approval)
    # The sealed node config owns both fields; the worker request cannot change
    # either one.
    assert proposal["title"] == sealed["title"], proposal
    expires = datetime.fromisoformat(proposal["expires_at"].replace("Z", "+00:00"))
    remaining = (expires - datetime.now(timezone.utc)).total_seconds()
    window = sealed["expires_seconds"]
    assert window - 60 < remaining <= window + 60, (
        remaining,
        proposal["expires_at"],
        window,
    )

    # The stored row and the durable event agree with the sealed window, not the
    # forged 86400s one.
    row_expires = datetime.fromisoformat(approval["expires_at"])
    assert abs((row_expires - expires).total_seconds()) < 2, (
        approval["expires_at"],
        proposal["expires_at"],
    )
    events = t087.run_events(t033_stack.dbname, run["id"], "approval.required")
    assert len(events) == 1, events
    event_expires = datetime.fromisoformat(
        events[0]["payload"]["expires_at"].replace("Z", "+00:00")
    )
    assert abs((event_expires - expires).total_seconds()) < 2, events[0]


# ---------------------------------------------------------------------------
# 2. approve -> one resume -> the same run continues and finishes once
# ---------------------------------------------------------------------------
def test_approved_node_approval_resumes_the_same_run_once(t033_stack, sandbox) -> None:
    owner, _application_id, run = t087.queue_approval_run(t033_stack, t087.approval_definition())

    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "waiting_approval", report
    assert report["error_code"] == "approval_required", report
    assert report["run_state"] == "waiting_approval", report
    waiting = assert_waiting_for_approval(t033_stack, run["id"])

    approval = t087.approval_for_node(t033_stack.dbname, run["id"], "approve1")
    assert approval is not None, t087.approval_rows(t033_stack.dbname)
    assert waiting["checkpoint_id"] == "approval:" + approval["id"], waiting
    assert approval["state"] == "pending", approval
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["approve1"] == ["pending", "running"], states

    decided = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert decided.status == 200, decided.text
    assert decided.json["state"] == "approved", decided.json
    resume = t035.resume_job(t033_stack.dbname, run["id"])
    assert resume["state"] == "ready", resume

    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Exactly one approval and exactly one resume job: the replay reused the
    # original row instead of creating a second one.
    approvals = t087.approval_rows(t033_stack.dbname, run_id=run["id"])
    assert len(approvals) == 1, approvals
    assert approvals[0]["state"] == "approved", approvals[0]
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 1

    # The node published the decision as data (only after approval) and the end
    # node sealed it into the final output.
    rows = observations.node_result_rows(t033_stack.dbname, run["id"])
    data_rows = [row for row in rows if row["node_id"] == "approve1" and row["kind"] == "data"]
    assert len(data_rows) == 1, rows
    document = observations.artifact_document(owner.creator, data_rows[0]["artifact_id"])
    assert document["approval_id"] == approval["id"], document
    assert document["decision"] == "approved", document
    assert document["params_sha256"] == approval["params_sha256"], document
    assert document["proposal"] == {"subject": "order-42", "amount": "1250.00"}, document
    final = observations.artifact_document(
        owner.creator, observations.output_artifact_id(t033_stack.dbname, run["id"])
    )
    assert final == {"approval_id": approval["id"], "decision": "approved"}, final

    # Two attempts touched the node: the first parked, the resumed one published.
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["approve1"] == [
        "pending", "running", "pending", "running", "succeeded"
    ], states
    assert states["end"] == ["pending", "running", "succeeded"], states
    # One approval.required event: the replay created no second approval.
    assert len(t087.run_events(t033_stack.dbname, run["id"], "approval.required")) == 1
    # A business approval grants no capability: still zero operations/usage.
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert t087.usage_ledger_rows(t033_stack.dbname, run["id"]) == []
    # No sandbox ran for the approval identity graph.
    assert t033.no_orphan_evidence()["runsc_list"] == []

# ---------------------------------------------------------------------------
# 2b. a real resume reuses the completed join instead of firing it again
# ---------------------------------------------------------------------------
def test_a_real_resume_reuses_the_join_and_never_fires_it_twice(
    t033_stack, sandbox
) -> None:
    owner, _application_id, run = t087.queue_approval_run(
        t033_stack, t087.approval_after_join_definition(), csv=True,
        name="t087 join replay",
    )

    # First process: both python branches run, the join fires once, then the
    # approval parks the run before the end.
    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "waiting_approval", report
    assert report["error_code"] == "approval_required", report
    waiting = assert_waiting_for_approval(t033_stack, run["id"])
    approval = t087.approval_for_node(t033_stack.dbname, run["id"], "approve1")
    assert approval is not None and approval["state"] == "pending", approval
    assert waiting["checkpoint_id"] == "approval:" + approval["id"], waiting

    rows_before = observations.node_result_rows(t033_stack.dbname, run["id"])
    join_rows_before = [row for row in rows_before if row["node_id"] == "join"]
    assert len(join_rows_before) == 1, join_rows_before
    for node_id in ("left", "right"):
        node_rows = [
            row for row in rows_before if row["node_id"] == node_id and row["kind"] == "data"
        ]
        assert len(node_rows) == 1, (node_id, rows_before)
    join_before = observations.artifact_document(
        owner.creator, join_rows_before[0]["artifact_id"]
    )
    assert set(join_before["results"]) == {"left", "right"}, join_before
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["join"] == ["pending", "running", "succeeded"], states

    decided = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert decided.status == 200 and decided.json["state"] == "approved", decided.text
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 1

    # Second real process: the durable join output is reused; no second join data
    # row, no second join execution, and the same envelope reaches the end.
    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["outcome"] == "succeeded", report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    rows_after = observations.node_result_rows(t033_stack.dbname, run["id"])
    join_rows_after = [row for row in rows_after if row["node_id"] == "join"]
    assert join_rows_after == join_rows_before, (
        "the resumed attempt must reuse the one join artifact",
        join_rows_before,
        join_rows_after,
    )
    for node_id in ("left", "right"):
        node_rows = [
            row for row in rows_after if row["node_id"] == node_id and row["kind"] == "data"
        ]
        assert len(node_rows) == 1, (node_id, rows_after)
    join_after = observations.artifact_document(
        owner.creator, join_rows_after[0]["artifact_id"]
    )
    assert join_after == join_before, (join_before, join_after)

    final = observations.artifact_document(
        owner.creator, observations.output_artifact_id(t033_stack.dbname, run["id"])
    )
    assert final["approval_id"] == approval["id"], final
    assert final["decision"] == "approved", final
    assert final["results"] == join_after["results"], final

    # The reuse emits one terminal fact per attempt; the join never fires twice
    # in one attempt.
    join_succeeded = [
        event
        for event in observations.node_events(t033_stack.dbname, run["id"])
        if event["payload"]["node_id"] == "join"
        and event["payload"]["state"] == "succeeded"
    ]
    attempts = [event["payload"]["attempt_no"] for event in join_succeeded]
    assert attempts == [1, 2], attempts
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["join"] == ["pending", "running", "succeeded", "succeeded"], states
    assert t033.no_orphan_evidence()["runsc_list"] == []


# ---------------------------------------------------------------------------
# 3. reject -> terminal approval_rejected, zero external writes, no resume
# ---------------------------------------------------------------------------
def test_rejected_node_approval_terminates_the_run_without_a_resume(t033_stack, sandbox) -> None:
    owner, _application_id, run = t087.queue_approval_run(t033_stack, t087.approval_definition())
    _completed, report = t087.exec_once(t033_stack)
    assert report["outcome"] == "waiting_approval", report
    approval = t087.approval_for_node(t033_stack.dbname, run["id"], "approve1")
    assert approval is not None

    decided = t087.decide(
        owner.creator, approval["id"], decision="reject",
        params_sha256=approval["params_sha256"],
    )
    assert decided.status == 200, decided.text
    assert decided.json["state"] == "rejected", decided.json

    state = t087.run_row(t033_stack.dbname, run["id"])
    assert state["state"] == "failed", state
    assert state["error_code"] == t087.REJECTED_CODE, state
    assert observations.output_artifact_id(t033_stack.dbname, run["id"]) is None
    assert t035.resume_jobs(t033_stack.dbname, run["id"]) == []
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert t087.usage_ledger_rows(t033_stack.dbname, run["id"]) == []
    # The node never reports success and the end node never runs.
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["approve1"] == ["pending", "running"], states
    assert "end" not in states, states

    # There is no resume job to continue: a further exec claims nothing and the
    # run stays terminally rejected.
    completed, report = t087.exec_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "idle", report
    state = t087.run_row(t033_stack.dbname, run["id"])
    assert state["state"] == "failed" and state["error_code"] == t087.REJECTED_CODE, state


# ---------------------------------------------------------------------------
# 4. expiry -> terminal approval_expired, the decision is refused
# ---------------------------------------------------------------------------
def test_expired_node_approval_terminates_the_run_and_refuses_the_decision(
    t033_stack, sandbox
) -> None:
    owner, _application_id, run = t087.queue_approval_run(t033_stack, t087.approval_definition())
    _completed, report = t087.exec_once(t033_stack)
    assert report["outcome"] == "waiting_approval", report
    approval = t087.approval_for_node(t033_stack.dbname, run["id"], "approve1")
    assert approval is not None

    # Move the real deadline into the past. This stack uses a 5m test-only sweep
    # cadence, so this case deterministically exercises the decision path's due
    # check; the automatic sweeper has its own fast-sweep case below.
    t087.point_expiry_past(t033_stack.dbname, approval["id"])
    refused = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == t087.EXPIRED_CODE, refused.json
    expired = t087.approval_row(t033_stack.dbname, approval["id"])
    assert expired["state"] == "expired", expired

    state = t087.run_row(t033_stack.dbname, run["id"])
    assert state["state"] == "failed", state
    assert state["error_code"] == t087.EXPIRED_CODE, state
    assert observations.output_artifact_id(t033_stack.dbname, run["id"]) is None
    assert t035.resume_jobs(t033_stack.dbname, run["id"]) == []
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert observations.node_result_rows(t033_stack.dbname, run["id"]) == []


# ---------------------------------------------------------------------------
# 4b. the deadline alone terminates a parked run (automatic expiry sweeper)
# ---------------------------------------------------------------------------
def test_a_parked_approval_expires_automatically_without_a_decision(
    t033_stack_fast_approval_sweep, sandbox
) -> None:
    stack = t033_stack_fast_approval_sweep
    owner, _application_id, run = t087.queue_approval_run(
        stack, t087.approval_definition()
    )
    _completed, report = t087.exec_once(stack)
    assert report["outcome"] == "waiting_approval", report
    waiting = assert_waiting_for_approval(stack, run["id"])
    approval = t087.approval_for_node(stack.dbname, run["id"], "approve1")
    assert approval is not None and approval["state"] == "pending", approval
    assert waiting["checkpoint_id"] == "approval:" + approval["id"], waiting

    # Make the server-owned deadline due. Nobody calls the decision endpoint:
    # only the real API process's sweeper can terminate this Run.
    t087.point_expiry_past(stack.dbname, approval["id"])

    def failed_run():
        row = t087.run_row(stack.dbname, run["id"])
        return row if row["state"] == "failed" else None

    state = t087.wait_until(failed_run, timeout=10.0)
    assert state["error_code"] == t087.EXPIRED_CODE, state
    assert state["finished_at"] is not None, state

    expired = t087.approval_row(stack.dbname, approval["id"])
    assert expired["state"] == "expired", expired
    assert expired["decided_at"] is not None, expired
    assert expired["decided_by"] is None, expired
    assert expired["decision_key"] == "sweeper:expired", expired

    # The one waiting resolution is the expired decision; it creates no resume
    # job and no approval output.
    decision = t035.waiting_decision(stack.dbname, run["id"])
    assert decision == {
        "kind": t035.KIND_APPROVAL,
        "outcome": "expired",
        "reference": approval["id"],
    }, decision
    assert t035.waiting_row(stack.dbname, run["id"])["resume_job_id"] is None
    assert t035.resume_jobs(stack.dbname, run["id"]) == []
    assert observations.output_artifact_id(stack.dbname, run["id"]) is None
    assert observations.node_result_rows(stack.dbname, run["id"]) == []
    states = observations.node_states(stack.dbname, run["id"])
    assert states["approve1"] == ["pending", "running"], states
    assert "end" not in states, states

    # A late human decision is refused with the stable code: the run is already
    # terminally expired, not resumable.
    refused = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == t087.EXPIRED_CODE, refused.json


# ---------------------------------------------------------------------------
# 4c. a failed hand-off rolls the expiry back; the next sweep retries it
# ---------------------------------------------------------------------------
def test_an_expiry_handoff_failure_rolls_back_and_is_retried(
    t033_stack_fast_approval_sweep, sandbox
) -> None:
    stack = t033_stack_fast_approval_sweep
    owner, _application_id, run = t087.queue_approval_run(
        stack, t087.approval_definition()
    )
    _completed, report = t087.exec_once(stack)
    assert report["outcome"] == "waiting_approval", report
    assert_waiting_for_approval(stack, run["id"])
    approval = t087.approval_for_node(stack.dbname, run["id"], "approve1")
    assert approval is not None and approval["state"] == "pending", approval

    # Force the waiting-decision update to fail inside the expiry transaction.
    t087.install_waiting_decision_fault(stack.dbname)
    try:
        t087.point_expiry_past(stack.dbname, approval["id"])
        t087.wait_until(
            lambda: "t087 injected waiting handoff failure" in stack.server.logs(),
            timeout=5.0,
        )
        # The failed hand-off rolled the whole transaction back: the approval is
        # still pending, not a dead `expired` row the sweep would skip forever.
        still = t087.approval_row(stack.dbname, approval["id"])
        assert still["state"] == "pending", still
        assert still["decided_at"] is None, still
        state = t087.run_row(stack.dbname, run["id"])
        assert state["state"] == "waiting_approval", state
        waiting = t035.waiting_row(stack.dbname, run["id"])
        assert waiting["decision"] is None, waiting
        assert waiting["resume_job_id"] is None, waiting
    finally:
        t087.clear_waiting_decision_fault(stack.dbname)

    def failed_run():
        row = t087.run_row(stack.dbname, run["id"])
        return row if row["state"] == "failed" else None

    state = t087.wait_until(failed_run, timeout=10.0)
    assert state["error_code"] == t087.EXPIRED_CODE, state
    expired = t087.approval_row(stack.dbname, approval["id"])
    assert expired["state"] == "expired", expired
    decision = t035.waiting_decision(stack.dbname, run["id"])
    assert decision == {
        "kind": t035.KIND_APPROVAL,
        "outcome": "expired",
        "reference": approval["id"],
    }, decision
    assert t035.resume_jobs(stack.dbname, run["id"]) == []
    assert observations.output_artifact_id(stack.dbname, run["id"]) is None


# ---------------------------------------------------------------------------
# 4d. the decision-path expiry uses the same atomic hand-off
# ---------------------------------------------------------------------------
def test_a_decision_path_expiry_handoff_failure_also_rolls_back(
    t033_stack_fast_approval_sweep, sandbox
) -> None:
    stack = t033_stack_fast_approval_sweep
    owner, _application_id, run = t087.queue_approval_run(
        stack, t087.approval_definition()
    )
    _completed, report = t087.exec_once(stack)
    assert report["outcome"] == "waiting_approval", report
    assert_waiting_for_approval(stack, run["id"])
    approval = t087.approval_for_node(stack.dbname, run["id"], "approve1")
    assert approval is not None and approval["state"] == "pending", approval

    t087.install_waiting_decision_fault(stack.dbname)
    try:
        t087.point_expiry_past(stack.dbname, approval["id"])
        refused = t087.decide(
            owner.creator, approval["id"], params_sha256=approval["params_sha256"]
        )
        # The expiry could not be handed off durably, so the decision request
        # fails instead of committing a dead `expired` row.
        assert refused.status == 500, refused.text
        still = t087.approval_row(stack.dbname, approval["id"])
        assert still["state"] == "pending", still
        assert still["decided_at"] is None, still
        state = t087.run_row(stack.dbname, run["id"])
        assert state["state"] == "waiting_approval", state
        waiting = t035.waiting_row(stack.dbname, run["id"])
        assert waiting["decision"] is None, waiting
    finally:
        t087.clear_waiting_decision_fault(stack.dbname)

    def failed_run():
        row = t087.run_row(stack.dbname, run["id"])
        return row if row["state"] == "failed" else None

    state = t087.wait_until(failed_run, timeout=10.0)
    assert state["error_code"] == t087.EXPIRED_CODE, state
    assert t087.approval_row(stack.dbname, approval["id"])["state"] == "expired"
    assert t035.waiting_decision(stack.dbname, run["id"]) == {
        "kind": t035.KIND_APPROVAL,
        "outcome": "expired",
        "reference": approval["id"],
    }


# ---------------------------------------------------------------------------
# 5. two approvals in one run (T091 rehearsal): two waits, two decisions, one end
# ---------------------------------------------------------------------------
def test_two_approvals_wait_twice_and_converge_on_one_run(t033_stack, sandbox) -> None:
    owner, _application_id, run = t087.queue_approval_run(
        t033_stack, t087.two_approval_definition(), name="t087 two approvals"
    )

    _completed, report = t087.exec_once(t033_stack)
    assert report["outcome"] == "waiting_approval", report
    assert_waiting_for_approval(t033_stack, run["id"])
    first = t087.approval_for_node(t033_stack.dbname, run["id"], "a1")
    assert first is not None and first["state"] == "pending"
    assert t087.approval_for_node(t033_stack.dbname, run["id"], "a2") is None
    decided = t087.decide(owner.creator, first["id"], params_sha256=first["params_sha256"])
    assert decided.status == 200 and decided.json["state"] == "approved", decided.text

    _completed, report = t087.exec_once(t033_stack)
    assert report["outcome"] == "waiting_approval", report
    second = t087.approval_for_node(t033_stack.dbname, run["id"], "a2")
    assert second is not None and second["state"] == "pending"
    assert first["id"] != second["id"]
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 1
    decided = t087.decide(owner.creator, second["id"], params_sha256=second["params_sha256"])
    assert decided.status == 200 and decided.json["state"] == "approved", decided.text

    _completed, report = t087.exec_once(t033_stack)
    assert report["status"] == "executed" and report["outcome"] == "succeeded", report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Each wait produced its own approval and its own resume job; no row or job
    # was duplicated by the replay.
    approvals = t087.approval_rows(t033_stack.dbname, run_id=run["id"])
    assert len(approvals) == 2, approvals
    assert [row["node_key"] for row in approvals] == ["node:a1", "node:a2"], approvals
    assert all(row["state"] == "approved" and row["operation_id"] is None for row in approvals)
    jobs = t035.resume_jobs(t033_stack.dbname, run["id"])
    assert len(jobs) == 2 and len({job["id"] for job in jobs}) == 2, jobs

    # Each approval node published exactly one data output; the first was reused
    # by the later attempts instead of executing twice.
    rows = observations.node_result_rows(t033_stack.dbname, run["id"])
    for node_id, approval in (("a1", first), ("a2", second)):
        node_rows = [row for row in rows if row["node_id"] == node_id]
        assert len(node_rows) == 1, (node_id, node_rows)
        document = observations.artifact_document(
            owner.creator, node_rows[0]["artifact_id"]
        )
        assert document["approval_id"] == approval["id"], (node_id, document)
    final = observations.artifact_document(
        owner.creator, observations.output_artifact_id(t033_stack.dbname, run["id"])
    )
    assert final == {"first": first["id"], "second": second["id"]}, final
    states = observations.node_states(t033_stack.dbname, run["id"])
    assert states["a2"] == ["pending", "running", "pending", "running", "succeeded"], states
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []


# ---------------------------------------------------------------------------
# 6. a node approval never authorizes the later write operation
# ---------------------------------------------------------------------------
def test_a_node_approval_never_authorises_a_later_write_operation(t033_stack) -> None:
    owner = t033.golden_owner(t033_stack)
    policy = t087.bind_write_connector(t033_stack, owner.admin)
    _owner, _application_id, run = t087.queue_approval_run(
        t033_stack, t087.approval_then_python_definition(),
        owner=owner, name="t087 no future write",
    )
    lease = t087.claim_next_run(t033_stack, execution_unit_id="t087-write-unit")

    prepared = t087.prepare_node_approval(t033_stack, lease)
    assert prepared.status == 201, prepared.text
    approval = t087.approval_row(t033_stack.dbname, prepared.json["id"])
    assert approval["operation_id"] is None, approval
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []

    approved = t087.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert approved.status == 200 and approved.json["state"] == "approved", approved.text
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []

    # A write-risk connector call in the same live run still needs its own
    # operation-level approval: the business decision above authorized nothing.
    invoked = t087.invoke_write(
        t033_stack, lease,
        policy_id=policy["id"],
        logical_key="agent:stats:tool:call-1",
    )
    assert invoked.status == 200, invoked.text
    body = invoked.json
    assert body["state"] == "awaiting_approval", body
    assert body["operation_id"], body
    assert body["approval_id"], body

    operations = t087.operation_rows(t033_stack.dbname, run["id"])
    assert len(operations) == 1, operations
    assert operations[0]["id"] == body["operation_id"], operations
    assert operations[0]["state"] == "awaiting_approval", operations[0]
    write_approval = t087.approval_row(t033_stack.dbname, body["approval_id"])
    assert write_approval is not None, body
    assert write_approval["operation_id"] == body["operation_id"], write_approval
    assert write_approval["node_key"] != "node:approve1", write_approval
    assert write_approval["state"] == "pending", write_approval
    # The two approvals are distinct records with distinct identities.
    assert len(t087.approval_rows(t033_stack.dbname, run_id=run["id"])) == 2
    node_approval = t087.approval_row(t033_stack.dbname, approval["id"])
    assert node_approval["operation_id"] is None and node_approval["state"] == "approved"
    assert t087.usage_ledger_rows(t033_stack.dbname, run["id"]) == []


# ---------------------------------------------------------------------------
# 7. a changed proposal is a new approval, never the old decision
# ---------------------------------------------------------------------------
def test_a_changed_proposal_is_a_new_approval_and_the_old_decision_does_not_apply(
    t033_stack,
) -> None:
    owner, _application_id, run = t087.queue_approval_run(t033_stack, t087.approval_definition())
    lease = t087.claim_next_run(t033_stack)

    first = t087.prepare_node_approval(t033_stack, lease)
    assert first.status == 201, first.text
    first_id = first.json["id"]
    approved = t087.decide(
        owner.creator, first_id, params_sha256=first.json["params_sha256"]
    )
    assert approved.status == 200 and approved.json["state"] == "approved", approved.text

    changed = t087.prepare_node_approval(
        t033_stack, lease,
        body=t087.prepare_body(lease, proposal={"subject": "order-42", "amount": "999.00"}),
    )
    assert changed.status == 201, changed.text
    assert changed.json["id"] != first_id, changed.json
    assert changed.json["state"] == "pending", changed.json
    assert changed.json["params_sha256"] != first.json["params_sha256"]
    second_id = changed.json["id"]
    rows = t087.approval_rows(t033_stack.dbname, run_id=run["id"])
    assert len(rows) == 2, rows
    assert t087.approval_row(t033_stack.dbname, first_id)["state"] == "approved"
    assert t087.approval_row(t033_stack.dbname, second_id)["state"] == "pending"

    # Replaying the changed proposal returns the same new row (no third approval).
    replay = t087.prepare_node_approval(
        t033_stack, lease,
        body=t087.prepare_body(lease, proposal={"subject": "order-42", "amount": "999.00"}),
    )
    assert replay.status == 201 and replay.json["id"] == second_id, replay.json
    assert len(t087.approval_rows(t033_stack.dbname, run_id=run["id"])) == 2

    # The first approval's hash cannot decide the new proposal.
    mismatch = t087.decide(
        owner.creator, second_id, params_sha256=first.json["params_sha256"]
    )
    assert mismatch.status == 409, mismatch.text
    assert mismatch.json["error"]["code"] == "approval_params_mismatch", mismatch.json
    assert t087.approval_row(t033_stack.dbname, second_id)["state"] == "pending"
