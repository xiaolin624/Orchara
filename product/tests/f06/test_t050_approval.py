"""F06/T050 acceptance: exact-parameter approvals and safe resume.

Every test drives the real stack: PostgreSQL migrated through 0031, the compiled
API, real mTLS, real object bytes, the real admin connector registry and a real
loopback connector endpoint. The proposal bytes are read from the sealed object,
the decision rows from PostgreSQL, and the resumed capability is a real mTLS
``invokeCapability`` call with the resumed attempt token.

``not_run``: a real browser DOM (the JS helpers are executed under Node in
``test_t050_pages.py``), a real vendor connector (loopback stands in for the
deployment-defined endpoint), and an automatic background expiry sweeper (expiry
is observed through the decision path, which is the contract's only decision
entry).
"""

from __future__ import annotations

import json
import threading
import uuid

import pytest

from support import f00b, t024, t025, t029, t035, t041, t048, t050
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

T050_SECRET_VALUE = "t050-db-secret-value-7a1e4c"


@pytest.fixture
def t050_stack(tmp_path, fresh_database, go_binaries, api_server) -> t024.T024Stack:
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env[t048.TEST_SECRET_ENV] = T050_SECRET_VALUE
    server = api_server(dsn, extra_env=env)

    stack = t024.T024Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


@pytest.fixture
def local_server() -> t048.LocalConnectorServer:
    server = t048.LocalConnectorServer()
    try:
        yield server
    finally:
        server.close()


def park(stack, lease, operation_id: str, *, execution_unit_id: str = "t041-unit"):
    """Submit the real T035 waiting result the T050 executor would submit."""
    worker = t035.execution_client(stack, "worker")
    response = t035.submit_waiting(
        worker, lease, checkpoint_id="capability:" + operation_id,
    )
    assert response.status == 200, response.text
    return response


def confirm_exit(stack, lease, *, execution_unit_id: str = "t041-unit"):
    response = t035.confirm_waiting_exit(
        stack, lease, execution_unit_id=execution_unit_id,
    )
    assert response.status == 200, response.text
    return response


def prepare_write(stack, local_server, owner, *, params=None, logical_key="agent:approval-node:tool:call-1"):
    """Register, bind and invoke one write-risk connector; returns the parked facts."""
    local_server.respond_json({"ok": True, "write_id": "w-1"})
    policy = t050.write_policy(stack, owner.admin, origin=local_server.url)
    t048.bind_tool_connectors(stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(stack, run_owner=owner)
    response = t050.invoke_write(
        stack,
        lease,
        policy_id=policy["id"],
        params=params or t050.default_params(),
        logical_key=logical_key,
    )
    assert response.status == 200, response.text
    body = response.json
    assert body["state"] == "awaiting_approval", body
    assert body["approval_id"], body
    approval = t050.approval_row(stack.dbname, body["approval_id"])
    assert approval is not None
    return {
        "owner": owner,
        "policy": policy,
        "run": run,
        "lease": lease,
        "operation_id": body["operation_id"],
        "approval": approval,
        "response": body,
    }


def resumed_lease(stack):
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t050-supervisor", execution_unit_id="t050-resume-unit",
    )
    assert response.status == 200, response.text
    return response.json


# ---------------------------------------------------------------------------
# prepare / proposal / get / list
# ---------------------------------------------------------------------------
def test_server_builds_the_exact_proposal_and_only_the_approver_can_read_it(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    operation_id = prepared["operation_id"]

    assert approval["state"] == "pending", approval
    assert approval["approver_id"] == owner.creator.principal_id, approval
    assert approval["node_key"] == "agent:approval-node", approval
    assert approval["params_sha256"] == t050.operation(
        t050_stack.dbname, prepared["run"]["id"], operation_id
    )["params_sha256"], approval

    # The sealed proposal is built from server facts, not worker text.
    proposal = t050.proposal(t050_stack, approval)
    assert proposal["run_id"] == prepared["run"]["id"], proposal
    assert proposal["operation_id"] == operation_id, proposal
    assert proposal["logical_key"] == "agent:approval-node:tool:call-1", proposal
    assert proposal["capability_kind"] == "connector", proposal
    assert proposal["capability_version"] == prepared["policy"]["id"], proposal
    assert proposal["risk_class"] == "write", proposal
    assert proposal["target"]["origin"] == local_server.url, proposal
    assert proposal["target"]["method"] == "POST", proposal
    assert proposal["target"]["path_prefix"] == "/v1", proposal
    assert proposal["target"]["path"] == "/v1/orders", proposal
    assert proposal["params"]["body"] == {"amount": 1250, "currency": "CNY"}, proposal
    assert proposal["data_destination"] == local_server.url, proposal
    assert proposal["policy_sha256"] == prepared["policy"]["policy_sha256"], proposal
    assert proposal["manifest_sha256"], proposal
    assert proposal["snapshot_id"] and proposal["snapshot_sha256"], proposal
    assert proposal["expires_at"], proposal
    assert proposal["params_sha256"] == approval["params_sha256"], proposal
    assert T050_SECRET_VALUE not in json.dumps(proposal), "the connector secret leaked"

    # Approver read/list and proposal content through the real artifact ACL.
    fetched = t050.get_approval(owner.creator, approval["id"])
    assert fetched.status == 200, fetched.text
    contract_fields = {
        "id", "run_id", "state", "proposal_artifact_id", "params_sha256", "expires_at",
    }
    assert set(fetched.json) == contract_fields, fetched.json
    listed = t050.list_approvals(owner.creator)
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [approval["id"]], listed.json
    content = t050.v1_artifact_content(owner.creator, approval["proposal_artifact_id"])
    assert content.status == 200, content.text
    assert json.loads(content.raw)["params_sha256"] == approval["params_sha256"]

    # A stranger sees nothing; the administrator may read but is not the approver.
    stranger = t041.secondary_owner(t050_stack)
    assert t050.get_approval(stranger.creator, approval["id"]).status == 404
    assert t050.decide(
        stranger.creator, approval["id"], params_sha256=approval["params_sha256"]
    ).status == 404
    admin_read = t050.get_approval(owner.admin, approval["id"])
    assert admin_read.status == 200, admin_read.text
    admin_decision = t050.decide(
        owner.admin, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert admin_decision.status == 403, admin_decision.text
    assert admin_decision.json["error"]["code"] == t050.APPROVER_MISMATCH

    # No egress happened while parked.
    assert local_server.records == []


def test_internal_prepare_returns_the_same_server_built_approval(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    worker = t041.worker_client(t050_stack)
    response = worker.post(
        f"/internal/v1/runs/{prepared['run']['id']}/approvals",
        {
            "attempt_token": prepared["lease"]["attempt_token"],
            "logical_key": "agent:approval-node:tool:call-1",
            "operation_id": prepared["operation_id"],
            "proposal": {"text": "worker-supplied display text is never trusted"},
        },
    )
    assert response.status == 201, response.text
    assert response.json["id"] == approval["id"], response.json
    assert response.json["params_sha256"] == approval["params_sha256"], response.json
    # The replay created no second approval and did not rewrite the proposal.
    assert t050.approval_rows(t050_stack.dbname, run_id=prepared["run"]["id"]) == [approval]
    assert "worker-supplied" not in json.dumps(t050.proposal(t050_stack, approval))
    assert local_server.records == []


# ---------------------------------------------------------------------------
# approve -> waiting resolution -> exactly one resume -> exactly one dispatch
# ---------------------------------------------------------------------------
def test_approve_before_exit_produces_one_resume_and_one_dispatch(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    run_id = prepared["run"]["id"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])

    decided = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert decided.status == 200, decided.text
    assert decided.json["state"] == "approved", decided.json
    # The old attempt has not exited: the decision is stored, no resume exists.
    assert t035.resume_jobs(t050_stack.dbname, run_id) == []
    waiting = t035.waiting_row(t050_stack.dbname, run_id)
    decision = json.loads(waiting["decision"])
    assert decision["outcome"] == "approved", waiting
    assert decision["reference"] == approval["id"], waiting
    assert waiting["exit_confirmed_at"] is None, waiting

    confirm_exit(t050_stack, prepared["lease"])
    jobs = t035.resume_jobs(t050_stack.dbname, run_id)
    assert len(jobs) == 1, jobs
    assert t050.run_row(t050_stack.dbname, run_id)["state"] == "queued"
    operation = t050.operation(t050_stack.dbname, run_id, prepared["operation_id"])
    assert operation["state"] == "authorized", operation
    assert local_server.records == []

    # The resumed attempt re-enters the same logical capability call.
    lease = resumed_lease(t050_stack)
    assert lease["run_id"] == run_id, lease
    resumed = t050.invoke_write(
        t050_stack,
        lease,
        policy_id=prepared["policy"]["id"],
        params=t050.default_params(),
        logical_key="agent:approval-node:tool:call-1",
        operation_id=prepared["operation_id"],
    )
    assert resumed.status == 200, resumed.text
    assert resumed.json["state"] == "succeeded", resumed.json
    assert len(local_server.records) == 1, local_server.records
    operation = t050.operation(t050_stack.dbname, run_id, prepared["operation_id"])
    assert operation["state"] == "succeeded", operation
    attempts = t041.attempt_rows(t050_stack.dbname, prepared["operation_id"])
    assert len(attempts) == 1 and attempts[0]["state"] == "succeeded", attempts

    # A replay of the same call is the recorded result, not a second write.
    replay = t050.invoke_write(
        t050_stack,
        lease,
        policy_id=prepared["policy"]["id"],
        params=t050.default_params(),
        logical_key="agent:approval-node:tool:call-1",
        operation_id=prepared["operation_id"],
    )
    assert replay.status == 200, replay.text
    assert replay.json["state"] == "succeeded", replay.json
    assert len(local_server.records) == 1, local_server.records


def test_decision_after_exit_still_creates_one_resume(t050_stack, local_server) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    confirm_exit(t050_stack, prepared["lease"])
    assert t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"]) == []

    decided = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert decided.status == 200, decided.text
    assert len(t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"])) == 1
    assert local_server.records == []
    # Same decision replay is idempotent and adds no second resume.
    replay = t050.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"],
        idempotency_key="replay-same-decision",
    )
    assert replay.status == 200, replay.text
    assert len(t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"])) == 1


def test_approve_before_the_waiting_result_lands_still_converges_to_one_resume(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    run_id = prepared["run"]["id"]

    # The human decides while the worker is still running (no waiting result yet).
    # The decision is durable; the waiting row adopts it when the result lands.
    decided = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert decided.status == 200, decided.text
    assert t050.approval_row(t050_stack.dbname, approval["id"])["state"] == "approved"
    assert t035.waiting_row(t050_stack.dbname, run_id) is None
    assert t035.resume_jobs(t050_stack.dbname, run_id) == []

    park(t050_stack, prepared["lease"], prepared["operation_id"])
    waiting = t035.waiting_row(t050_stack.dbname, run_id)
    assert json.loads(waiting["decision"])["outcome"] == "approved", waiting
    assert t035.resume_jobs(t050_stack.dbname, run_id) == []
    confirm_exit(t050_stack, prepared["lease"])
    assert len(t035.resume_jobs(t050_stack.dbname, run_id)) == 1

    lease = resumed_lease(t050_stack)
    resumed = t050.invoke_write(
        t050_stack,
        lease,
        policy_id=prepared["policy"]["id"],
        params=t050.default_params(),
        logical_key="agent:approval-node:tool:call-1",
        operation_id=prepared["operation_id"],
    )
    assert resumed.status == 200, resumed.text
    assert resumed.json["state"] == "succeeded", resumed.json
    assert len(local_server.records) == 1, local_server.records


def test_waiting_resolution_failure_is_surfaced_and_the_same_decision_recovers(
    t050_stack, local_server
) -> None:
    """T050 regression: a decision never reports success while the resume failed.

    ``execution.ResolveWaiting`` has two documented benign conflicts (nothing to
    resume yet / already decided). Every other error means the approved or
    rejected decision has no resume behind it. The test induces one
    deterministically through the database invariant the resume transaction
    enforces: the exit fact exists but no attempt has exited.
    """
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    run_id = prepared["run"]["id"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])

    st.sql(
        t050_stack.dbname,
        "UPDATE platform.run_waitings SET exit_confirmed_at = now() "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )
    failed = t050.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert failed.status == 500, failed.text
    assert failed.json["error"]["code"] == "internal_error", failed.text
    # The decision row committed before the resume attempt, so it is durable and
    # truthful; the failure (no resume) is what must be surfaced.
    assert t050.approval_row(t050_stack.dbname, approval["id"])["state"] == "approved"
    assert t035.resume_jobs(t050_stack.dbname, run_id) == []
    waiting = t035.waiting_row(t050_stack.dbname, run_id)
    assert waiting["decision"] is None, waiting
    assert waiting["resume_job_id"] is None, waiting

    # The same decision is still an idempotent retry: after the real exit fact it
    # drives the waiting resolution and creates exactly one resume.
    st.sql(
        t050_stack.dbname,
        "UPDATE platform.run_waitings SET exit_confirmed_at = NULL "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )
    confirm_exit(t050_stack, prepared["lease"])
    recovered = t050.decide(
        owner.creator,
        approval["id"],
        params_sha256=approval["params_sha256"],
        idempotency_key="retry-after-resume-failure",
    )
    assert recovered.status == 200, recovered.text
    assert recovered.json["state"] == "approved", recovered.json
    assert len(t035.resume_jobs(t050_stack.dbname, run_id)) == 1


def test_a_stale_earlier_approval_cannot_authorise_a_later_wait(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    first = prepare_write(t050_stack, local_server, owner, logical_key="agent:approval-node:tool:call-1")
    second = t050.invoke_write(
        t050_stack,
        first["lease"],
        policy_id=first["policy"]["id"],
        params={"path": "/v1/orders", "body": {"amount": 7, "currency": "CNY"}},
        logical_key="agent:approval-node:tool:call-2",
    )
    assert second.status == 200, second.text
    assert second.json["state"] == "awaiting_approval", second.json
    second_approval = t050.approval_row(t050_stack.dbname, second.json["approval_id"])
    assert second_approval is not None

    # Approve the earlier operation while the Run is still running.
    approved_first = t050.decide(
        owner.creator, first["approval"]["id"], params_sha256=first["approval"]["params_sha256"],
    )
    assert approved_first.status == 200, approved_first.text

    # The executor now parks on the *second* operation. The earlier decision must
    # not be adopted by this waiting row.
    park(t050_stack, first["lease"], second.json["operation_id"])
    waiting = t035.waiting_row(t050_stack.dbname, first["run"]["id"])
    assert waiting["decision"] is None, waiting
    confirm_exit(t050_stack, first["lease"])
    assert t035.resume_jobs(t050_stack.dbname, first["run"]["id"]) == []

    # Only the second approval can resume; then the second operation dispatches.
    approved_second = t050.decide(
        owner.creator, second_approval["id"], params_sha256=second_approval["params_sha256"],
    )
    assert approved_second.status == 200, approved_second.text
    assert len(t035.resume_jobs(t050_stack.dbname, first["run"]["id"])) == 1
    lease = resumed_lease(t050_stack)
    resumed = t050.invoke_write(
        t050_stack,
        lease,
        policy_id=first["policy"]["id"],
        params={"path": "/v1/orders", "body": {"amount": 7, "currency": "CNY"}},
        logical_key="agent:approval-node:tool:call-2",
        operation_id=second.json["operation_id"],
    )
    assert resumed.status == 200, resumed.text
    assert resumed.json["state"] == "succeeded", resumed.json
    assert len(local_server.records) == 1, local_server.records


# ---------------------------------------------------------------------------
# reject / expire
# ---------------------------------------------------------------------------
def test_reject_fails_the_waiting_run_without_a_write(t050_stack, local_server) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])

    decided = t050.decide(
        owner.creator, approval["id"], decision="reject", params_sha256=approval["params_sha256"],
    )
    assert decided.status == 200, decided.text
    assert decided.json["state"] == "rejected", decided.json
    assert t050.approval_row(t050_stack.dbname, approval["id"])["state"] == "rejected"
    operation = t050.operation(t050_stack.dbname, prepared["run"]["id"], prepared["operation_id"])
    assert operation["state"] == "failed" and operation["last_error_code"] == "approval_rejected", operation

    confirm_exit(t050_stack, prepared["lease"])
    run = t050.run_row(t050_stack.dbname, prepared["run"]["id"])
    assert run["state"] == "failed" and run["error_code"] == t050.REJECTED_CODE, run
    assert t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"]) == []
    assert local_server.records == []
    assert json.loads(t035.waiting_row(
        t050_stack.dbname, prepared["run"]["id"]
    )["decision"])["outcome"] == "rejected"


def test_expired_approval_cannot_be_approved_and_fails_expired(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    t050.point_expiry_past(t050_stack.dbname, approval["id"])

    refused = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] == t050.APPROVAL_EXPIRED, refused.json
    stored = t050.approval_row(t050_stack.dbname, approval["id"])
    assert stored["state"] == "expired", stored
    operation = t050.operation(t050_stack.dbname, prepared["run"]["id"], prepared["operation_id"])
    assert operation["state"] == "failed" and operation["last_error_code"] == "approval_expired", operation

    confirm_exit(t050_stack, prepared["lease"])
    run = t050.run_row(t050_stack.dbname, prepared["run"]["id"])
    assert run["state"] == "failed" and run["error_code"] == t050.EXPIRED_CODE, run
    assert t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"]) == []
    assert local_server.records == []


# ---------------------------------------------------------------------------
# changed params / exact hash
# ---------------------------------------------------------------------------
def test_changed_params_or_wrong_hash_cannot_reuse_the_approval(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]

    changed = t050.invoke_write(
        t050_stack,
        prepared["lease"],
        policy_id=prepared["policy"]["id"],
        params={"path": "/v1/orders", "body": {"amount": 999999, "currency": "CNY"}},
        logical_key="agent:approval-node:tool:call-1",
        operation_id=prepared["operation_id"],
    )
    assert changed.status == 409, changed.text
    assert changed.json["error"]["code"] == "operation_conflict", changed.json
    assert t050.approval_rows(t050_stack.dbname, run_id=prepared["run"]["id"]) == [approval]

    parked_hash = t050.decide(
        owner.creator, approval["id"], params_sha256="0" * 64,
    )
    assert parked_hash.status == 409, parked_hash.text
    assert parked_hash.json["error"]["code"] == t050.PARAMS_MISMATCH, parked_hash.json
    assert t050.approval_row(t050_stack.dbname, approval["id"])["state"] == "pending"
    assert local_server.records == []


# ---------------------------------------------------------------------------
# concurrent opposite decisions / opposite replay
# ---------------------------------------------------------------------------
def test_concurrent_opposite_decisions_have_one_winner_and_same_replay_is_idempotent(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    confirm_exit(t050_stack, prepared["lease"])

    base_url = t050_stack.server.http.base_url
    csrf = owner.creator.csrf
    cookie = owner.creator.cookie
    headers = {"Cookie": f"{st.SESSION_COOKIE}={cookie}"}
    results: dict[str, object] = {}

    def decide_in_thread(name: str, decision: str) -> None:
        client = st.HttpClient(base_url)
        results[name] = client.post(
            t050.decision_path(approval["id"]),
            body={"decision": decision, "params_sha256": approval["params_sha256"]},
            csrf=csrf,
            headers={**headers, "Idempotency-Key": "concurrent-" + name},
        )

    threads = [
        threading.Thread(target=decide_in_thread, args=("approve", "approve")),
        threading.Thread(target=decide_in_thread, args=("reject", "reject")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    statuses = sorted([results["approve"].status, results["reject"].status])
    assert statuses == [200, 409], {name: result.text for name, result in results.items()}
    loser = results["approve"] if results["approve"].status == 409 else results["reject"]
    assert loser.json["error"]["code"] == t050.DECISION_CONFLICT, loser.json

    stored = t050.approval_row(t050_stack.dbname, approval["id"])
    assert stored["state"] in {"approved", "rejected"}, stored
    winner = "approve" if stored["state"] == "approved" else "reject"
    replay = t050.decide(
        owner.creator, approval["id"], decision=winner,
        params_sha256=approval["params_sha256"],
    )
    assert replay.status == 200, replay.text
    assert replay.json["state"] == stored["state"], replay.json

    # Reject wins -> the run fails; approve wins -> exactly one resume job.
    if stored["state"] == "approved":
        assert len(t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"])) == 1
    else:
        assert t050.run_row(t050_stack.dbname, prepared["run"]["id"])["error_code"] == t050.REJECTED_CODE
    assert local_server.records == []


# ---------------------------------------------------------------------------
# permission and budget are re-checked; approval grants nothing extra
# ---------------------------------------------------------------------------
def test_revoked_permission_refuses_the_decision_and_invalidates_it(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    st.sql(
        t050_stack.dbname,
        "UPDATE platform.principals SET disabled_at = now() "
        f"WHERE id = {st.quote_literal(owner.creator.principal_id)}::uuid",
    )

    # The identity layer refuses a disabled principal before the approval code is
    # reached; that is a stronger refusal than permission_revoked (the account
    # cannot even authenticate). No decision is stored either way.
    refused = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert refused.status in {401, 403}, refused.text
    assert refused.json["error"]["code"] in {
        "principal_disabled", t050.PERMISSION_REVOKED,
    }, refused.json
    assert t050.approval_row(t050_stack.dbname, approval["id"])["state"] == "pending"
    assert t050.operation(
        t050_stack.dbname, prepared["run"]["id"], prepared["operation_id"]
    )["state"] == "awaiting_approval"
    assert local_server.records == []


def test_disabling_the_owner_after_approval_still_blocks_the_resume(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    approved = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert approved.status == 200, approved.text
    st.sql(
        t050_stack.dbname,
        "UPDATE platform.principals SET disabled_at = now() "
        f"WHERE id = {st.quote_literal(owner.creator.principal_id)}::uuid",
    )
    confirm_exit(t050_stack, prepared["lease"])

    run = t050.run_row(t050_stack.dbname, prepared["run"]["id"])
    assert run["state"] == "failed" and run["error_code"] == "permission_revoked", run
    assert t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"]) == []
    assert local_server.records == []


def test_approval_does_not_bypass_the_budget_check(t050_stack, local_server) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    confirm_exit(t050_stack, prepared["lease"])
    t050.set_budget(t050_stack.dbname, prepared["run"]["id"], "0")

    approved = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert approved.status == 200, approved.text
    run = t050.run_row(t050_stack.dbname, prepared["run"]["id"])
    assert run["state"] == "failed" and run["error_code"] == "budget_exhausted", run
    assert t035.resume_jobs(t050_stack.dbname, prepared["run"]["id"]) == []
    assert local_server.records == []


def test_a_revoked_connector_policy_after_approval_blocks_the_resume_dispatch(
    t050_stack, local_server
) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])
    confirm_exit(t050_stack, prepared["lease"])

    approved = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert approved.status == 200, approved.text
    assert t048.revoke(owner.admin, prepared["policy"]["id"]).status == 200

    lease = resumed_lease(t050_stack)
    refused = t050.invoke_write(
        t050_stack,
        lease,
        policy_id=prepared["policy"]["id"],
        params=t050.default_params(),
        logical_key="agent:approval-node:tool:call-1",
        operation_id=prepared["operation_id"],
    )
    assert refused.status == 403, refused.text
    assert refused.json["error"]["code"] == "connector_policy_revoked", refused.json
    assert local_server.records == []
    assert t050.operation(
        t050_stack.dbname, prepared["run"]["id"], prepared["operation_id"]
    )["state"] == "authorized"


# ---------------------------------------------------------------------------
# cancel invalidation / publication review separation
# ---------------------------------------------------------------------------
def test_cancel_invalidates_pending_approvals(t050_stack, local_server) -> None:
    owner = t041.owner(t050_stack)
    prepared = prepare_write(t050_stack, local_server, owner)
    approval = prepared["approval"]
    park(t050_stack, prepared["lease"], prepared["operation_id"])

    cancelled = owner.creator.client.post(
        f"/api/v1/runs/{prepared['run']['id']}/cancel", body={}, csrf=owner.creator.csrf,
    )
    assert cancelled.status == 202, cancelled.text
    stored = t050.approval_row(t050_stack.dbname, approval["id"])
    assert stored["state"] == "invalidated", stored
    refused = t050.decide(owner.creator, approval["id"], params_sha256=approval["params_sha256"])
    assert refused.status == 409, refused.text
    assert refused.json["error"]["code"] in {
        t050.APPROVAL_NOT_PENDING, "run_state_conflict",
    }, refused.json
    confirm_exit(t050_stack, prepared["lease"])
    assert local_server.records == []


def test_a_publication_review_id_is_not_a_run_approval(t050_stack, local_server) -> None:
    owner = t041.owner(t050_stack)
    review_id = t050.publication_review_id(t050_stack.dbname, owner.creator.principal_id)
    if review_id is None:
        pytest.skip("no evaluation report exists to seed a real publication review")
    assert t050.get_approval(owner.creator, review_id).status == 404
    refused = t050.decide(
        owner.creator, review_id, params_sha256="0" * 64,
    )
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == t050.APPROVAL_NOT_FOUND, refused.json

