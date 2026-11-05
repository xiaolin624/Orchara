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

import pytest

from support import t029, t033, t035, t041, t083, t087
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
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
    content = t033.artifact_content(owner.creator, approval["proposal_artifact_id"])
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
    assert t087.node_result_rows(t033_stack.dbname, run["id"]) == []
    assert t087.node_states(t033_stack.dbname, run["id"]) == {}


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
    assert t087.node_result_rows(t033_stack.dbname, run["id"]) == []
    states = t087.node_states(t033_stack.dbname, run["id"])
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
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state

    # Exactly one approval and exactly one resume job: the replay reused the
    # original row instead of creating a second one.
    approvals = t087.approval_rows(t033_stack.dbname, run_id=run["id"])
    assert len(approvals) == 1, approvals
    assert approvals[0]["state"] == "approved", approvals[0]
    assert len(t035.resume_jobs(t033_stack.dbname, run["id"])) == 1

    # The node published the decision as data (only after approval) and the end
    # node sealed it into the final output.
    rows = t087.node_result_rows(t033_stack.dbname, run["id"])
    data_rows = [row for row in rows if row["node_id"] == "approve1" and row["kind"] == "data"]
    assert len(data_rows) == 1, rows
    document = t087.artifact_document(t033_stack, owner, data_rows[0]["artifact_id"])
    assert document["approval_id"] == approval["id"], document
    assert document["decision"] == "approved", document
    assert document["params_sha256"] == approval["params_sha256"], document
    assert document["proposal"] == {"subject": "order-42", "amount": "1250.00"}, document
    final = t087.artifact_document(
        t033_stack, owner, t087.output_artifact_id(t033_stack.dbname, run["id"])
    )
    assert final == {"approval_id": approval["id"], "decision": "approved"}, final

    # Two attempts touched the node: the first parked, the resumed one published.
    states = t087.node_states(t033_stack.dbname, run["id"])
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
    assert t087.output_artifact_id(t033_stack.dbname, run["id"]) is None
    assert t035.resume_jobs(t033_stack.dbname, run["id"]) == []
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert t087.usage_ledger_rows(t033_stack.dbname, run["id"]) == []
    # The node never reports success and the end node never runs.
    assert t087.node_result_rows(t033_stack.dbname, run["id"]) == []
    states = t087.node_states(t033_stack.dbname, run["id"])
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

    # The test moves the real deadline into the past (the deployment has no expiry
    # sweeper; the contract's decision path is the only decision entry).
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
    assert t087.output_artifact_id(t033_stack.dbname, run["id"]) is None
    assert t035.resume_jobs(t033_stack.dbname, run["id"]) == []
    assert t087.operation_rows(t033_stack.dbname, run["id"]) == []
    assert t087.node_result_rows(t033_stack.dbname, run["id"]) == []


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
    state = t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
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
    rows = t087.node_result_rows(t033_stack.dbname, run["id"])
    for node_id, approval in (("a1", first), ("a2", second)):
        node_rows = [row for row in rows if row["node_id"] == node_id]
        assert len(node_rows) == 1, (node_id, node_rows)
        document = t087.artifact_document(t033_stack, owner, node_rows[0]["artifact_id"])
        assert document["approval_id"] == approval["id"], (node_id, document)
    final = t087.artifact_document(
        t033_stack, owner, t087.output_artifact_id(t033_stack.dbname, run["id"])
    )
    assert final == {"first": first["id"], "second": second["id"]}, final
    states = t087.node_states(t033_stack.dbname, run["id"])
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
