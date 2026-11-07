"""T101/J2: the controlled write-operation approval journey.

The connector is a real loopback HTTP server (``t048.LocalConnectorServer``): when
the approval succeeds the guard really sends one request to it, and the server's
own request log is the independent count of external writes. The rest is the real
product: the admin connector registry over HTTP, the real mTLS capability call,
``platform.approvals``/``platform.operations`` rows as the durable truth, and the
sealed proposal artifact read back from the object directory.

The journey also executes the ticket's failure/recovery boundaries: a changed
params object cannot reuse the approval, a wrong params hash is refused, a reject
fails the Run before any egress, an expired approval fails it, a cancelled Run
invalidates the pending approval, and a write whose receipt is lost parks the Run
in ``needs_reconciliation`` without ever resending.

One documented non-API step is the tool<->connector binding
(``platform.tool_versions.connector_versions``): the public ``POST /api/v1/tools``
does accept ``connector_version_ids`` but requires a whole new tool version plus
its build/review chain, so this ticket uses the existing T048/T050 test seam and
says so in the report. The assertions do not trust that row; they trust the
connector server's request log and PostgreSQL.
"""

from __future__ import annotations

import json

from support import f00b, t029, t035, t041, t048, t050, t051
from support import stack as st

import t101_support

WRITE_ID = "t101-write-1"


def _park(stack, lease: dict, operation_id: str) -> None:
    response = t035.submit_waiting(
        t035.execution_client(stack, "worker"),
        lease,
        checkpoint_id="capability:" + operation_id,
    )
    assert response.status == 200, response.text


def _confirm_exit(stack, lease: dict, *, unit: str = "t041-unit") -> None:
    response = t035.confirm_waiting_exit(stack, lease, execution_unit_id=unit)
    assert response.status == 200, response.text


def _resumed_lease(stack, run_id: str, *, unit: str):
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t101-approval-supervisor", execution_unit_id=unit
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run_id, (lease["run_id"], run_id)
    return lease


def _sealed_proposal(stack, approval: dict) -> dict:
    artifact = t041.artifact_row(stack.dbname, approval["proposal_artifact_id"])
    return json.loads((stack.object_dir / artifact["object_key"]).read_bytes())


def _prepare_write(
    stack, server, owner, policy: dict, *, logical_key: str = "agent:approval-node:tool:call-1"
):
    """Invoke one write-risk connector call against the registered policy."""
    server.respond_json({"ok": True, "write_id": WRITE_ID})
    _, run, lease = t041.claimed_run(stack, run_owner=owner)
    invoked = t050.invoke_write(
        stack,
        lease,
        policy_id=policy["id"],
        params=t050.default_params(),
        logical_key=logical_key,
    )
    assert invoked.status == 200, invoked.text
    body = invoked.json
    assert body["state"] == "awaiting_approval", body
    approval = t050.approval_row(stack.dbname, body["approval_id"])
    assert approval is not None
    return {
        "policy": policy,
        "run": run,
        "lease": lease,
        "operation_id": body["operation_id"],
        "approval": approval,
        "invoked": body,
    }


def run(stack, record) -> dict:
    record.command("python -m pytest tests/f12/test_t101_journeys.py -k write_approval")
    admin = f00b.admin_session(stack)
    server = t048.LocalConnectorServer()
    lost = t051.LostReceiptServer()
    try:
        # One golden owner for the whole module stack: the golden draft's fixed
        # application id can exist once per database, and every boundary below is
        # a separate real Run of the same owner's draft.
        owner = t101_support.golden_owner(stack)
        record.note("connector_origin", server.url)
        record.truth_source("connector server request log (server.records) = physical write count")
        record.truth_source(
            "platform.approvals / platform.operations / platform.operation_attempts via psql"
        )
        record.truth_source("sealed proposal artifact bytes under the real object directory")

        # The admin registry call is real; the tool<->connector binding is the
        # documented T048/T050 seam (the public tools API would need a full new
        # tool version plus its build/review chain).
        policy = t050.write_policy(stack, owner.admin, origin=server.url)
        t048.bind_tool_connectors(stack.dbname, [policy["id"]])
        record.http("POST", "/api/v1/admin/connectors", None)
        record.command(
            "UPDATE platform.tool_versions SET connector_versions = [policy] "
            "(documented non-API binding seam, asserted through the real manifest)"
        )

        # -- 1. the write parks with a server-built, parameter-bound proposal --
        prepared = _prepare_write(stack, server, owner, policy)
        approval = prepared["approval"]
        operation_id = prepared["operation_id"]
        run_id = prepared["run"]["id"]
        record.http("POST", "/internal/v1/runs/{id}/capabilities (mTLS worker)")
        record.check(
            "the write-risk call parks as awaiting_approval and creates one approval",
            approval["state"] == "pending"
            and approval["run_id"] == run_id
            and approval["operation_id"] == operation_id,
            approval,
        )
        record.check(
            "the approver is the Run owner",
            approval["approver_id"] == owner.creator.principal_id,
            approval["approver_id"],
        )
        operation = t050.operation(stack.dbname, run_id, operation_id)
        record.check(
            "the approval is bound to the operation's exact params hash",
            approval["params_sha256"] == operation["params_sha256"],
            {"approval": approval["params_sha256"], "operation": operation["params_sha256"]},
        )
        proposal = _sealed_proposal(stack, approval)
        record.check(
            "the sealed proposal carries the exact target and the exact params literal",
            proposal["target"]["origin"] == server.url
            and proposal["target"]["method"] == "POST"
            and proposal["target"]["path"] == "/v1/orders"
            and proposal["params"]["body"] == {"amount": 1250, "currency": "CNY"}
            and proposal["risk_class"] == "write"
            and proposal["params_sha256"] == approval["params_sha256"],
            proposal,
        )
        record.check(
            "no external write happened before the human decision",
            server.records == [],
            server.records,
        )

        # -- 2. only the approver may decide; a stranger cannot even read -------
        stranger = t041.secondary_owner(stack)
        foreign = t050.get_approval(stranger.creator, approval["id"])
        record.http("GET", "/api/v1/approvals/{id} (stranger)", foreign)
        st.assert_error(foreign, 404, t050.APPROVAL_NOT_FOUND)
        admin_read = t050.get_approval(owner.admin, approval["id"])
        record.check(
            "an unrelated creator sees a 404, the admin may read but is not the approver",
            foreign.status == 404 and admin_read.status == 200,
            {"stranger": foreign.status, "admin": admin_read.status},
        )
        admin_decision = t050.decide(
            owner.admin, approval["id"], params_sha256=approval["params_sha256"]
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (admin)", admin_decision)
        record.check(
            "the administrator is refused as approver (approver_mismatch)",
            admin_decision.status == 403
            and admin_decision.json["error"]["code"] == t050.APPROVER_MISMATCH,
            admin_decision.text,
        )

        # -- 3. exact-params binding: changed params and wrong hash are refused -
        changed = t050.invoke_write(
            stack,
            prepared["lease"],
            policy_id=prepared["policy"]["id"],
            params={"path": "/v1/orders", "body": {"amount": 999999, "currency": "CNY"}},
            logical_key="agent:approval-node:tool:call-1",
            operation_id=operation_id,
        )
        record.http("POST", "/internal/v1/runs/{id}/capabilities (changed params)", changed)
        record.check(
            "a changed params object on the same logical call is a conflict",
            changed.status == 409 and changed.json["error"]["code"] == "operation_conflict",
            changed.text,
        )
        wrong_hash = t050.decide(
            owner.creator, approval["id"], params_sha256="0" * 64
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (wrong hash)", wrong_hash)
        record.check(
            "the decision must present the exact params hash",
            wrong_hash.status == 409
            and wrong_hash.json["error"]["code"] == t050.PARAMS_MISMATCH,
            wrong_hash.text,
        )
        record.check(
            "the refused attempts did not mutate the approval or send anything",
            t050.approval_row(stack.dbname, approval["id"])["state"] == "pending"
            and server.records == [],
            server.records,
        )

        # -- 4. approve -> exactly one real external write, no replay ----------
        _park(stack, prepared["lease"], operation_id)
        decided = t050.decide(
            owner.creator, approval["id"], params_sha256=approval["params_sha256"]
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (owner approve)", decided)
        record.check(
            "the owner's approval is stored", decided.status == 200, decided.text
        )
        record.check(
            "the approval path itself never sends (resume still pending the exit fact)",
            server.records == [] and t035.resume_jobs(stack.dbname, run_id) == [],
            {"records": server.records},
        )
        _confirm_exit(stack, prepared["lease"])
        jobs = t035.resume_jobs(stack.dbname, run_id)
        record.check(
            "the confirmed exit creates exactly one resume job",
            len(jobs) == 1,
            jobs,
        )
        resumed = _resumed_lease(stack, run_id, unit="t101-resume-unit")
        sent = t050.invoke_write(
            stack,
            resumed,
            policy_id=prepared["policy"]["id"],
            params=t050.default_params(),
            logical_key="agent:approval-node:tool:call-1",
            operation_id=operation_id,
        )
        record.http("POST", "/internal/v1/runs/{id}/capabilities (resumed)", sent)
        record.check(
            "the resumed call succeeds",
            sent.status == 200 and sent.json["state"] == "succeeded",
            sent.text,
        )
        record.check(
            "the endpoint observed exactly one physical write",
            len(server.records) == 1,
            server.records,
        )
        operation = t050.operation(stack.dbname, run_id, operation_id)
        attempts = t041.attempt_rows(stack.dbname, operation_id)
        record.check(
            "the operation is succeeded with one attempt and a real receipt",
            operation["state"] == "succeeded"
            and operation["receipt_artifact_id"]
            and len(attempts) == 1
            and attempts[0]["state"] == "succeeded",
            {"operation": operation, "attempts": attempts},
        )
        receipt, receipt_bytes = t048.receipt(stack, operation["receipt_artifact_id"])
        record.check(
            "the sealed receipt wraps the endpoint's real 2xx body",
            receipt.get("status", 200) == 200
            and receipt["body"].get("ok") is True
            and receipt["body"].get("write_id") == WRITE_ID,
            receipt_bytes[:400],
        )
        replay = t050.invoke_write(
            stack,
            resumed,
            policy_id=prepared["policy"]["id"],
            params=t050.default_params(),
            logical_key="agent:approval-node:tool:call-1",
            operation_id=operation_id,
        )
        record.check(
            "a duplicate delivery of the same logical call never sends a second write",
            replay.status == 200
            and replay.json["state"] == "succeeded"
            and len(server.records) == 1,
            {"state": replay.json.get("state"), "records": len(server.records)},
        )
        record.note("approved_write", {"records": len(server.records), "operation": operation["state"]})

        # -- 5. reject boundary: run fails, nothing is sent --------------------
        rejected = _prepare_write(stack, server, owner, policy, logical_key="agent:approval-node:tool:call-reject")
        _park(stack, rejected["lease"], rejected["operation_id"])
        reject_decision = t050.decide(
            owner.creator,
            rejected["approval"]["id"],
            decision="reject",
            params_sha256=rejected["approval"]["params_sha256"],
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (reject)", reject_decision)
        record.check(
            "reject is stored and the operation fails without any egress",
            reject_decision.status == 200
            and t050.approval_row(stack.dbname, rejected["approval"]["id"])["state"]
            == "rejected"
            and t050.operation(
                stack.dbname, rejected["run"]["id"], rejected["operation_id"]
            )["last_error_code"]
            == t050.REJECTED_CODE,
            reject_decision.text,
        )
        _confirm_exit(stack, rejected["lease"])
        rejected_run = t050.run_row(stack.dbname, rejected["run"]["id"])
        record.check(
            "the rejected Run is failed with approval_rejected and has no resume",
            rejected_run["state"] == "failed"
            and rejected_run["error_code"] == t050.REJECTED_CODE
            and t035.resume_jobs(stack.dbname, rejected["run"]["id"]) == []
            and len(server.records) == 1,
            rejected_run,
        )

        # -- 6. expiry boundary: approvable window is real ---------------------
        expired = _prepare_write(stack, server, owner, policy, logical_key="agent:approval-node:tool:call-expire")
        _park(stack, expired["lease"], expired["operation_id"])
        t050.point_expiry_past(stack.dbname, expired["approval"]["id"])
        late = t050.decide(
            owner.creator,
            expired["approval"]["id"],
            params_sha256=expired["approval"]["params_sha256"],
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (expired)", late)
        record.check(
            "an expired approval cannot be approved",
            late.status == 409 and late.json["error"]["code"] == t050.APPROVAL_EXPIRED,
            late.text,
        )
        _confirm_exit(stack, expired["lease"])
        expired_run = t050.run_row(stack.dbname, expired["run"]["id"])
        record.check(
            "the expired Run fails with approval_expired and nothing is sent",
            expired_run["state"] == "failed"
            and expired_run["error_code"] == t050.EXPIRED_CODE
            and t035.resume_jobs(stack.dbname, expired["run"]["id"]) == []
            and len(server.records) == 1,
            expired_run,
        )
        record.command(
            "point_expiry_past: UPDATE platform.approvals SET expires_at = now() - interval '1 minute' "
            "(time's effect; the decision path re-checks it lazily)"
        )

        # -- 7. cancellation invalidates the pending approval ------------------
        cancelled = _prepare_write(stack, server, owner, policy, logical_key="agent:approval-node:tool:call-cancel")
        cancelled_run = cancelled["run"]
        _park(stack, cancelled["lease"], cancelled["operation_id"])
        cancel = owner.creator.client.post(
            f"/api/v1/runs/{cancelled_run['id']}/cancel", body={}, csrf=owner.creator.csrf
        )
        record.http("POST", f"/api/v1/runs/{cancelled_run['id']}/cancel", cancel)
        record.check("cancellation accepted", cancel.status == 202, cancel.text)
        invalidated = t050.approval_row(stack.dbname, cancelled["approval"]["id"])
        record.check(
            "cancelling the Run invalidates its pending approval",
            invalidated["state"] == "invalidated",
            invalidated,
        )
        late_cancel = t050.decide(
            owner.creator,
            cancelled["approval"]["id"],
            params_sha256=cancelled["approval"]["params_sha256"],
        )
        record.check(
            "a decision after cancellation is refused",
            late_cancel.status == 409,
            late_cancel.text,
        )
        _confirm_exit(stack, cancelled["lease"])
        record.check(
            "the cancelled path never sent",
            len(server.records) == 1,
            server.records,
        )

        # -- 8. lost receipt: needs_reconciliation, never a resend -------------
        lost.respond_success()
        lost_prepared = t051.prepare_approved_write(stack, lost, owner)
        record.check(
            "control: the approved lost-receipt run has not sent yet",
            lost.records == [],
            lost.records,
        )
        lost.respond_drop()
        unknown = t051.invoke_capability(stack, lost_prepared["lease"], lost_prepared)
        record.http(
            "POST", "/internal/v1/runs/{id}/capabilities (receipt connection dropped)", unknown
        )
        record.check(
            "the real write is received and the response is lost (state=unknown)",
            unknown.status == 200 and unknown.json["state"] == t051.STATE_UNKNOWN,
            unknown.text,
        )
        record.check(
            "the endpoint observed exactly one physical write",
            len(lost.records) == 1,
            lost.records,
        )
        lost_run_id = lost_prepared["run"]["id"]
        lost_operation_id = lost_prepared["operation_id"]
        operation = t051.operation_row(stack.dbname, lost_run_id, lost_operation_id)
        record.check(
            "the operation is unknown with one dispatching attempt and no receipt",
            operation["state"] == t051.STATE_UNKNOWN
            and operation["receipt_artifact_id"] is None
            and len(t051.attempt_rows(stack.dbname, lost_operation_id)) == 1,
            operation,
        )
        duplicate = t051.invoke_capability(stack, lost_prepared["lease"], lost_prepared)
        record.check(
            "a duplicate delivery of the unknown operation never resends",
            duplicate.status == 200
            and duplicate.json["state"] == t051.STATE_UNKNOWN
            and len(lost.records) == 1,
            {"state": duplicate.json.get("state"), "records": len(lost.records)},
        )
        t051.submit_failed_result(stack, lost_prepared["lease"])
        t051.confirm_exit(stack, lost_prepared["lease"])
        lost_run = t051.run_state(stack.dbname, lost_run_id)
        record.check(
            "after the attempt exits the Run parks in needs_reconciliation",
            lost_run["state"] == t051.STATE_NEEDS_RECONCILIATION
            and lost_run["error_code"] == "unknown_operations"
            and lost_run["finished_at"] is None,
            lost_run,
        )
        evidence_bytes = json.dumps(
            {
                "operation_id": lost_operation_id,
                "run_id": lost_run_id,
                "provider_receipt": {"write_id": WRITE_ID, "status": "accepted"},
            }
        ).encode()
        evidence_id = t033_upload(stack, owner, evidence_bytes)
        settled = t051.reconcile(
            owner.creator,
            lost_operation_id,
            conclusion=t051.CONCLUSION_SUCCESS,
            evidence_artifact_id=evidence_id,
        )
        record.http("POST", "/api/v1/operations/{id}/reconcile", settled)
        record.check(
            "the owner's evidence settles the unknown write as succeeded",
            settled.status == 200 and settled.json["state"] == t051.STATE_SUCCEEDED,
            settled.text,
        )
        record.check(
            "reconciliation used the owner's uploaded evidence and never resent",
            t051.physical_write_count(stack.dbname, lost_operation_id) == 1
            and len(lost.records) == 1,
            {"attempts": t051.physical_write_count(stack.dbname, lost_operation_id)},
        )
        record.note(
            "lost_receipt",
            {
                "records": len(lost.records),
                "run_state": lost_run["state"],
                "settled_state": settled.json["state"],
            },
        )
    finally:
        lost.close()
        server.close()

    return {
        "approval_id": approval["id"],
        "run_id": run_id,
        "operation_id": operation_id,
        "writes": 1,
        "reconciled": True,
    }


def t033_upload(stack, owner, content: bytes) -> str:
    """Real upload -> complete -> artifact_finalize for the reconciliation evidence."""
    from support import t033

    return t033.upload_artifact(
        stack, owner.creator, content, purpose="task_input", media_type="application/json"
    )
