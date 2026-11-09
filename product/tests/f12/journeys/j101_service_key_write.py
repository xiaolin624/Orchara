"""T101/J6 + J6b: the three-way gate on one real external write, and its gap.

``J6`` is the journey GitHub issue #102's AC2 was missing: a service principal
authenticates with an API key (scopes ``application:read`` + ``application:write``),
holds a resource grant on the application, and submits a **genuinely published**
version through ``POST /api/v1/requests``. The Run belongs to the service principal
and its sealed manifest carries the administrator-approved write-risk connector
policy, frozen through the tool's ``connector_versions`` binding before the
evaluation snapshot compiled. The write operation parks for approval and the
designated approver is the service account's *responsible human* — the product
resolves it in ``internal/approvals/service.go:187-210`` (``approverForOwnerTx``).
That human approves through the contract route; the worker's resumed attempt
performs exactly one physical HTTP write to the loopback endpoint, and the
endpoint's own request log is the independent write count.

What the journey cannot claim, and what ``J6b`` records as ``blocked``: the
published *graph* does not invoke the connector. There is no connector node type
in the graph executor (``internal/presetexec/graphrun.go`` node dispatch) and the
agent tool call is always ``capability_kind="tool"`` (``internal/presetexec/
agent.go``), which runs the fixed preset tool in the sandbox and ignores
``tool_versions.connector_versions`` (``internal/capabilities/service.go`` tool
branch). So J6 uses the same documented worker capability protocol T048/T050/J2
use — a real mTLS worker call with the Run's live attempt token — to perform the
authorized write. J6b runs the same published version end to end through the real
supervisor: the graph succeeds and emits **zero** connector operations and zero
external writes.

Truth sources (never the API echo): the loopback endpoint's request log, the
durable PostgreSQL ledger (``platform.approvals/operations/operation_attempts``,
``api_keys``, ``resource_grants``, ``applications/application_versions``), and the
sealed object bytes under the real object directory (manifest, proposal, receipt,
request input, run output).
"""

from __future__ import annotations

import hashlib
import json

from support import execution_support as execution
from support import f00b, f02a, f08, t027, t029, t033, t035, t041, t042, t048, t050, t068
from support import stack as st

SCOPE_READ = "application:read"
SCOPE_WRITE = "application:write"

WRITE_ID = "t101-j6-order-1"
LOGICAL_KEY = "t101-j6:orders:submit:1"
PARAMS = {"path": "/v1/orders", "body": {"amount": 1250, "currency": "CNY"}}
TASK_DOCUMENT = json.dumps({"source": "sales_sample.csv"}, ensure_ascii=False)

WORKER_ID = "t101-j6-supervisor"
START_UNIT = "t101-j6-start-unit"
RESUME_UNIT = "t101-j6-resume-unit"


# ---------------------------------------------------------------------------
# small helpers (no product behaviour is replaced)
# ---------------------------------------------------------------------------
def _bearer_session(stack, principal_id: str, secret: str) -> f02a.Session:
    """A session-shaped bearer client: no cookie, no CSRF token, key only."""
    return f02a.Session(
        client=f02a.bearer_client(stack, secret),
        csrf=None,
        principal_id=principal_id,
        cookie="",
    )


def _key_row(dbname: str, key_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, principal_id::text, key_hash, to_json(scopes)::text AS scopes, "
        "expires_at::text, revoked_at::text FROM platform.api_keys "
        f"WHERE id = {st.quote_literal(key_id)}::uuid",
    )
    assert len(rows) == 1, rows
    row = dict(rows[0])
    row["scopes"] = json.loads(row["scopes"])
    return row


def _principal_row(dbname: str, principal_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT p.id::text AS id, p.kind, p.disabled_at::text AS disabled_at, "
        "p.responsible_user_id::text AS responsible_user_id, "
        "u.principal_id::text AS human_principal_id "
        "FROM platform.principals AS p "
        "LEFT JOIN platform.users AS u ON u.principal_id = p.responsible_user_id "
        f"WHERE p.id = {st.quote_literal(principal_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def _grant_rows(dbname: str, principal_id: str, application_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text, principal_id::text, resource_type, resource_id::text, action, "
        "expires_at::text FROM platform.resource_grants "
        f"WHERE principal_id = {st.quote_literal(principal_id)}::uuid "
        f"AND resource_id = {st.quote_literal(application_id)}::uuid "
        "ORDER BY id",
    )


def _request_input_bytes(stack, request_id: str) -> tuple[dict, bytes]:
    row = f08.request_row(stack.dbname, request_id)
    assert row is not None
    artifact = f08.artifact_row(stack.dbname, row["input_artifact_id"])
    return artifact, (stack.object_dir / artifact["object_key"]).read_bytes()


def _run_row(stack, run_id: str) -> dict:
    """The durable Run facts (owner/kind/version/manifest) beyond the contract view."""
    row = dict(t027.run_row(stack.dbname, run_id))
    row.update(t050.run_row(stack.dbname, run_id))
    return row


def _connector_operations(stack, run_id: str) -> list[dict]:
    return [
        row
        for row in t041.operation_rows(stack.dbname, run_id)
        if row["capability_kind"] == "connector"
    ]


# ---------------------------------------------------------------------------
# genuine publication: evaluation -> submit -> review, with the write policy
# frozen into the published manifest
# ---------------------------------------------------------------------------
def _publish_write_version(stack, server, admin, record) -> dict:
    policy = t050.write_policy(stack, admin, origin=server.url)
    record.http("POST", "/api/v1/admin/connectors (write-risk policy)", None)
    policy_row = t048.policy_row(stack.dbname, policy["id"])
    record.check(
        "the administrator registered one approved write-risk connector policy",
        policy_row["risk_class"] == "write"
        and policy_row["approved_at"] is not None
        and policy_row["revoked_at"] is None
        and policy_row["policy_sha256"] == policy["policy_sha256"],
        {
            "policy_id": policy_row["id"],
            "risk_class": policy_row["risk_class"],
            "origin": policy_row["origin"],
        },
    )

    # The tool<->connector binding is the documented T048/T050 non-API seam. It
    # must happen before the evaluation compiles its snapshot so both the
    # evaluation manifest and the published manifest close over the policy.
    owner = t068.sales_application(stack, login="t101.j6.publisher")
    record.command(
        "t068.sales_application: real creator -> seeded preset tool version -> "
        "start -> python(sales_stats) -> end draft"
    )
    t048.bind_tool_connectors(
        stack.dbname, [policy["id"]], tool_id=t042.PRESET_TOOL_ID
    )
    record.command(
        "UPDATE platform.tool_versions SET connector_versions = [policy] "
        "(documented T048 tool<->connector binding seam; asserted through the sealed manifest)"
    )

    log_path = stack.server.log_path.parent / "t101-j6-evaluation-pools.log"
    outcome = t068._genuine_outcome(
        stack, owner, t068.five_category_cases(), log_path=log_path
    )
    evaluation = outcome["evaluation"]
    report = outcome["report"]
    sealed_report = outcome["sealed"]
    cases = sealed_report.get("cases", [])
    record.command(
        "t068 real five-category evaluation: cmd/maintenance pools + real "
        "cmd/supervisor exec --once (WSL2/runsc) per case"
    )
    record.check(
        "the real five-category evaluation produced a passed report",
        report["status"] == "passed"
        and len(cases) == 5
        and all(case.get("status") == "passed" for case in cases),
        {
            "report_id": report["id"],
            "status": report["status"],
            "cases": [(case.get("id"), case.get("status")) for case in cases],
        },
    )

    snapshot = t027.snapshot_row(stack.dbname, evaluation["snapshot_id"])
    manifest_sha = snapshot["manifest_sha256"]
    submitted = t068.submit_publication(
        owner.creator,
        owner.application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["id"],
    )
    record.http("POST", "/api/v1/applications/{id}/submit", submitted)
    review = t068.review_row(stack.dbname, submitted.json["id"])
    record.check(
        "the creator submits the publication against the frozen evaluation report",
        submitted.status == 202
        and review["manifest_sha256"] == manifest_sha
        and review["state"] == "submitted"
        and review["submitted_by"] == owner.creator.principal_id,
        {
            "review_id": review["id"],
            "state": review["state"],
            "manifest_sha256": review["manifest_sha256"],
        },
    )
    approved = t068.decided_review(
        admin,
        submitted.json["id"],
        decision="approve",
        manifest_sha256=manifest_sha,
    )
    record.http("POST", "/api/v1/reviews/{id}/decision (admin approve)", approved)
    version_id = approved.json.get("version_id")
    record.check(
        "the administrator's review decision activates a real published version",
        approved.status == 200
        and bool(version_id)
        and t068.active_version_id(stack.dbname, owner.application_id) == version_id,
        approved.text,
    )

    versions = [
        row
        for row in t068.version_rows(stack.dbname, owner.application_id)
        if row["id"] == version_id
    ]
    assert len(versions) == 1, versions
    version = versions[0]
    sealed_manifest_bytes = t068.sealed_bytes(stack, version["manifest_artifact_id"])
    manifest = json.loads(sealed_manifest_bytes)
    record.check(
        "the published manifest bytes hash to the version's frozen digest",
        hashlib.sha256(sealed_manifest_bytes).hexdigest() == version["manifest_sha256"]
        and version["manifest_sha256"] == manifest_sha,
        {"version_id": version_id, "manifest_sha256": version["manifest_sha256"]},
    )
    expected_policy = {
        "version_id": policy["id"],
        "policy_sha256": policy_row["policy_sha256"],
    }
    record.check(
        "the published manifest carries the exact write-risk connector policy",
        expected_policy in manifest.get("connector_policies", []),
        manifest.get("connector_policies"),
    )
    record.check(
        "the published manifest also freezes the preset tool the graph declares",
        any(
            artifact.get("id") == t042.PRESET_TOOL_ID
            for artifact in manifest.get("tool_artifacts", [])
        ),
        [artifact.get("id") for artifact in manifest.get("tool_artifacts", [])],
    )
    record.note(
        "publication",
        {
            "method": "genuine evaluation -> submit -> admin review decision",
            "evaluation_id": evaluation["id"],
            "evaluation_report_id": report["id"],
            "evaluation_cases": len(cases),
            "review_id": submitted.json["id"],
            "version_id": version_id,
            "manifest_sha256": manifest_sha,
            "policy_id": policy["id"],
            "policy_sha256": policy_row["policy_sha256"],
        },
    )
    return {
        "policy": policy,
        "policy_row": policy_row,
        "owner": owner,
        "application_id": owner.application_id,
        "version_id": version_id,
        "version": version,
        "manifest": manifest,
        "manifest_sha256": manifest_sha,
        "evaluation": evaluation,
        "report": report,
        "log_path": str(log_path),
    }


# ---------------------------------------------------------------------------
# J6: service key -> published task -> human-approved write -> exactly one write
# ---------------------------------------------------------------------------
def run(stack, record) -> dict:
    record.command("python -m pytest tests/f12/test_t101_journeys.py -k service_key_write")
    record.truth_source(
        "loopback connector server request log (server.records) = physical write count"
    )
    record.truth_source(
        "platform.approvals / operations / operation_attempts / api_keys / resource_grants "
        "/ applications / application_versions via psql"
    )
    record.truth_source(
        "sealed object bytes: published manifest, request input, approval proposal, "
        "connector receipt, run output (read from the real object directory, hash re-checked)"
    )
    admin = f00b.admin_session(stack)
    server = t048.LocalConnectorServer()
    server.respond_json({"ok": True, "write_id": WRITE_ID})
    try:
        published = _publish_write_version(stack, server, admin, record)
        application_id = published["application_id"]
        version_id = published["version_id"]
        policy = published["policy"]
        policy_row = published["policy_row"]

        # -- 1. the service account, its key, and the resource ACL -----------
        responsible = f00b.invite_and_activate(
            stack, admin, "t101.j6.responsible", roles=("user",)
        )
        publisher = published["owner"].creator
        service_id = f02a.create_service_account(
            admin, "t101 j6 writer", responsible.principal_id
        )
        record.http("POST", "/api/v1/admin/service-accounts", None)
        principal = _principal_row(stack.dbname, service_id)
        record.check(
            "the run owner is a named service principal whose responsible human is the approver-to-be",
            principal["kind"] == "service"
            and principal["disabled_at"] is None
            and principal["responsible_user_id"] == responsible.principal_id
            and principal["human_principal_id"] == responsible.principal_id,
            principal,
        )
        key_id, secret = f02a.create_api_key(
            admin, service_id, [SCOPE_READ, SCOPE_WRITE]
        )
        record.http("POST", "/api/v1/admin/api-keys (read+write scopes)", None)
        key_row = _key_row(stack.dbname, key_id)
        record.check(
            "the API key stores only the secret's SHA-256 with exactly the requested scopes",
            key_row["key_hash"] == hashlib.sha256(secret.encode()).hexdigest()
            and key_row["scopes"] == [SCOPE_READ, SCOPE_WRITE]
            and secret not in json.dumps(key_row),
            {"scopes": key_row["scopes"], "key_id": key_id},
        )
        writer = _bearer_session(stack, service_id, secret)
        me = writer.client.get("/api/v1/auth/me")
        record.http("GET", "/api/v1/auth/me (service key)", me)
        record.check(
            "the bearer credential is kind=service with no roles and no CSRF token",
            me.status == 200
            and me.json.get("kind") == "service"
            and me.json.get("roles") == []
            and me.json.get("csrf_token") is None,
            me.json,
        )

        conversation = f08.conversation(writer)
        record.http("POST", "/api/v1/conversations (bearer, write scope)", None)
        csv_id = t033.upload_artifact(stack, writer, t033.SALES_CSV)
        record.http(
            "POST", "/api/v1/artifacts/uploads + PUT + complete + finalize (bearer)", None
        )

        # -- 2. missing any one gate refuses the very same request -----------
        no_grant = f08.submit_request(
            writer,
            conversation,
            TASK_DOCUMENT,
            artifact_ids=[csv_id],
            version_id=version_id,
            key=f00b.new_uuid(),
        )
        record.http("POST", "/api/v1/requests (scope, no ACL grant)", no_grant)
        st.assert_error(no_grant, 404, "resource_not_found")
        record.check(
            "with the write scope but no read grant the published version is invisible (404)",
            no_grant.status == 404,
            no_grant.text,
        )
        record.check(
            "the refused request created no Request and no Run",
            f08.request_rows(stack.dbname, conversation) == []
            and f08.run_rows(stack.dbname, conversation) == [],
            {"requests": f08.request_rows(stack.dbname, conversation)},
        )

        grant_id = f02a.grant_read(admin, service_id, application_id)
        record.http("POST", "/api/v1/admin/grants (read)", None)
        grants = _grant_rows(stack.dbname, service_id, application_id)
        record.check(
            "the grant is one live read grant for the service principal on the application",
            len(grants) == 1
            and grants[0]["id"] == grant_id
            and grants[0]["action"] == "read",
            grants,
        )

        read_key_id, read_secret = f02a.create_api_key(
            admin, service_id, [SCOPE_READ]
        )
        reader = _bearer_session(stack, service_id, read_secret)
        read_only = f08.submit_request(
            reader,
            conversation,
            TASK_DOCUMENT,
            artifact_ids=[csv_id],
            version_id=version_id,
            key=f00b.new_uuid(),
        )
        record.http("POST", "/api/v1/requests (read-only key, same grant)", read_only)
        st.assert_error(read_only, 403, "insufficient_scope")
        record.check(
            "the same request with a read-only key is refused before the ACL (403 insufficient_scope)",
            read_only.status == 403,
            read_only.text,
        )

        submitted = f08.submit_request(
            writer,
            conversation,
            TASK_DOCUMENT,
            artifact_ids=[csv_id],
            version_id=version_id,
            key=f00b.new_uuid(),
        )
        record.http("POST", "/api/v1/requests (scope + grant, bearer only)", submitted)
        request = f08.request_of(submitted)
        run_id = request["run_id"]
        record.check(
            "scope + grant together dispatch the published version (202, dispatched)",
            submitted.status == 202
            and request["state"] == "dispatched"
            and bool(run_id),
            request,
        )
        record.check(
            "the response pins the explicit application version",
            submitted.header("X-Request-Application-Version-Id") == version_id,
            submitted.header_values("X-Request-Application-Version-Id"),
        )
        request_row = f08.request_row(stack.dbname, request["id"])
        record.check(
            "the durable Request belongs to the service principal and pins the version",
            request_row["owner_id"] == service_id
            and request_row["requested_version_id"] == version_id
            and request_row["run_id"] == run_id,
            request_row,
        )
        artifact, sealed_input = _request_input_bytes(stack, request["id"])
        record.check(
            "the Run input is the exact sealed task document, byte for byte",
            sealed_input.decode("utf-8") == TASK_DOCUMENT
            and artifact["sha256"]
            == hashlib.sha256(TASK_DOCUMENT.encode("utf-8")).hexdigest(),
            {"artifact_sha256": artifact["sha256"], "purpose": artifact["purpose"]},
        )
        run_row = _run_row(stack, run_id)
        record.check(
            "the dispatched Run is a published Run owned by the service principal",
            run_row["kind"] == "published"
            and run_row["owner_id"] == service_id
            and run_row["application_version_id"] == version_id
            and run_row["manifest_sha256"] == published["manifest_sha256"],
            {
                "kind": run_row["kind"],
                "owner_id": run_row["owner_id"],
                "application_version_id": run_row["application_version_id"],
            },
        )

        # -- 3. the live worker attempt performs the write-risk operation -----
        claim = t029.execution_client(stack, "supervisor").claim_job(
            worker_id=WORKER_ID, execution_unit_id=START_UNIT
        )
        assert claim.status == 200, claim.text
        lease = claim.json
        record.command(
            "POST /internal/v1/jobs/claim (supervisor mTLS; the published Run's live attempt)"
        )
        record.check(
            "the supervisor claims exactly the dispatched Run's start job",
            lease["run_id"] == run_id and lease["job_kind"] == "start",
            {"run_id": lease["run_id"], "job_kind": lease["job_kind"]},
        )
        lease_policies = lease["manifest"].get("connector_policies", [])
        record.check(
            "the live lease manifest carries the frozen write policy",
            any(
                entry.get("version_id") == policy["id"]
                and entry.get("policy_sha256") == policy_row["policy_sha256"]
                for entry in lease_policies
            ),
            lease_policies,
        )

        invoked = t050.invoke_write(
            stack,
            lease,
            policy_id=policy["id"],
            params=PARAMS,
            logical_key=LOGICAL_KEY,
        )
        record.http(
            "POST /internal/v1/runs/{id}/capabilities (worker mTLS, kind=connector)", invoked
        )
        invoked_body = invoked.json
        record.check(
            "the write-risk connector call parks as awaiting_approval",
            invoked.status == 200
            and invoked_body["state"] == "awaiting_approval"
            and invoked_body.get("approval_id")
            and invoked_body.get("operation_id"),
            invoked_body,
        )
        operation_id = invoked_body["operation_id"]
        approval_id = invoked_body["approval_id"]

        approval = t050.approval_row(stack.dbname, approval_id)
        operation = t050.operation(stack.dbname, run_id, operation_id)
        record.check(
            "the approval is pending on the exact operation of this Run",
            approval["state"] == "pending"
            and approval["run_id"] == run_id
            and approval["operation_id"] == operation_id,
            approval,
        )
        record.check(
            "the approver is the service account's responsible human, not the service principal",
            approval["approver_id"] == responsible.principal_id
            and approval["approver_id"] != service_id
            and _principal_row(stack.dbname, approval["approver_id"])["kind"] == "user",
            {
                "approver_id": approval["approver_id"],
                "service_id": service_id,
                "responsible_id": responsible.principal_id,
            },
        )
        record.check(
            "the approval is bound to the operation's exact params hash and write risk",
            approval["params_sha256"] == operation["params_sha256"]
            and operation["capability_kind"] == "connector"
            and operation["risk_class"] == "write"
            and operation["capability_version"] == policy["id"],
            {
                "approval_sha256": approval["params_sha256"],
                "operation_sha256": operation["params_sha256"],
                "operation_state": operation["state"],
            },
        )
        proposal = t050.proposal(stack, approval)
        record.check(
            "the sealed proposal carries the exact target, params literal and frozen policy digest",
            proposal["target"]["origin"] == server.url
            and proposal["target"]["method"] == "POST"
            and proposal["target"]["path_prefix"] == "/v1"
            and proposal["target"]["path"] == "/v1/orders"
            and proposal["params"]["body"] == PARAMS["body"]
            and proposal["risk_class"] == "write"
            and proposal["policy_sha256"] == policy_row["policy_sha256"]
            and proposal["manifest_sha256"] == published["manifest_sha256"],
            {
                "target": proposal["target"],
                "params_sha256": proposal.get("params_sha256"),
                "policy_sha256": proposal.get("policy_sha256"),
            },
        )
        record.check(
            "no external write and no unauthorized attempt happened before the human decision",
            server.records == []
            and t041.attempt_rows(stack.dbname, operation_id) == [],
            {"records": len(server.records)},
        )

        # -- 4. the human approval cannot be stood in for --------------------
        machine_decision = t050.decide(
            writer, approval_id, params_sha256=approval["params_sha256"]
        )
        record.http(
            "POST /api/v1/approvals/{id}/decision (service key, write scope)",
            machine_decision,
        )
        st.assert_error(machine_decision, 404, "approval_not_found")
        record.check(
            "the service key itself cannot decide its own Run's approval (404, not the approver)",
            machine_decision.status == 404,
            machine_decision.text,
        )
        stranger = t041.secondary_owner(stack)
        stranger_decision = t050.decide(
            stranger.creator, approval_id, params_sha256=approval["params_sha256"]
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (stranger)", stranger_decision)
        record.check(
            "an unrelated human cannot even see the approval (404)",
            stranger_decision.status == 404,
            stranger_decision.text,
        )
        admin_decision = t050.decide(
            admin, approval_id, params_sha256=approval["params_sha256"]
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (admin)", admin_decision)
        record.check(
            "the administrator is refused as approver (403 approver_mismatch)",
            admin_decision.status == 403
            and admin_decision.json["error"]["code"] == t050.APPROVER_MISMATCH,
            admin_decision.text,
        )
        wrong_hash = t050.decide(
            responsible, approval_id, params_sha256="0" * 64
        )
        record.http("POST", "/api/v1/approvals/{id}/decision (wrong params hash)", wrong_hash)
        record.check(
            "the designated human must present the exact params hash (409 approval_params_mismatch)",
            wrong_hash.status == 409
            and wrong_hash.json["error"]["code"] == t050.PARAMS_MISMATCH
            and t050.approval_row(stack.dbname, approval_id)["state"] == "pending",
            wrong_hash.text,
        )
        record.check(
            "none of the refused decisions authorized or sent anything",
            server.records == []
            and t050.operation(stack.dbname, run_id, operation_id)["state"]
            == "awaiting_approval",
            {"records": len(server.records)},
        )

        # -- 5. the designated human approves; the resume stays gated ---------
        parked = t035.submit_waiting(
            t041.worker_client(stack),
            lease,
            checkpoint_id="capability:" + operation_id,
        )
        record.http(
            "POST /internal/v1/runs/{id}/result (waiting_approval, checkpoint capability:<op>)",
            parked,
        )
        record.check(
            "the worker's waiting proposal parks the attempt on the operation checkpoint",
            parked.status == 200,
            parked.text,
        )
        decided = t050.decide(
            responsible, approval_id, params_sha256=approval["params_sha256"]
        )
        record.http(
            "POST /api/v1/approvals/{id}/decision (the responsible human approves)", decided
        )
        record.check(
            "the responsible human's approval is stored with that human as decided_by",
            decided.status == 200
            and decided.json["state"] == "approved"
            and t050.approval_row(stack.dbname, approval_id)["decided_by"]
            == responsible.principal_id,
            decided.text,
        )
        record.check(
            "the decision alone still sends nothing and creates no resume",
            server.records == []
            and t035.resume_jobs(stack.dbname, run_id) == [],
            {"records": len(server.records)},
        )
        confirmed = t035.confirm_waiting_exit(
            stack, lease, execution_unit_id=START_UNIT
        )
        assert confirmed.status == 200, confirmed.text
        jobs = t035.resume_jobs(stack.dbname, run_id)
        record.command(
            "POST /internal/v1/attempts/{token}/exited (supervisor mTLS; exit fact)"
        )
        record.check(
            "the confirmed exit creates exactly one resume job for the same Run",
            len(jobs) == 1
            and jobs[0]["state"] in {"ready", "leased"}
            and t050.run_row(stack.dbname, run_id)["state"] in {"queued", "running"},
            jobs,
        )

        # -- 6. the resumed attempt performs exactly one physical write ------
        resumed_claim = t029.execution_client(stack, "supervisor").claim_job(
            worker_id=WORKER_ID, execution_unit_id=RESUME_UNIT
        )
        assert resumed_claim.status == 200, resumed_claim.text
        resumed = resumed_claim.json
        record.check(
            "the supervisor claims the one resume job of the same Run",
            resumed["run_id"] == run_id and resumed["job_kind"] == "resume",
            {"run_id": resumed["run_id"], "job_kind": resumed["job_kind"]},
        )
        sent = t050.invoke_write(
            stack,
            resumed,
            policy_id=policy["id"],
            params=PARAMS,
            logical_key=LOGICAL_KEY,
            operation_id=operation_id,
        )
        record.http(
            "POST /internal/v1/runs/{id}/capabilities (resumed worker, same operation)",
            sent,
        )
        record.check(
            "the resumed capability call dispatches and succeeds",
            sent.status == 200 and sent.json["state"] == "succeeded",
            sent.text,
        )
        record.check(
            "the real loopback endpoint received exactly one physical write",
            len(server.records) == 1
            and server.records[0]["method"] == "POST"
            and server.records[0]["path"] == "/v1/orders"
            and json.loads(server.records[0]["body"]) == PARAMS["body"],
            {
                "records": len(server.records),
                "method": server.records[0]["method"],
                "path": server.records[0]["path"],
                "body": server.records[0]["body"],
            },
        )
        record.check(
            "the credential was injected by the platform, never by the worker",
            "x-connector-secret" in server.records[0]["headers"],
            {"headers": sorted(server.records[0]["headers"])},
        )
        operation = t050.operation(stack.dbname, run_id, operation_id)
        attempts = t041.attempt_rows(stack.dbname, operation_id)
        record.check(
            "the operation is succeeded with exactly one succeeded attempt",
            operation["state"] == "succeeded"
            and len(attempts) == 1
            and attempts[0]["state"] == "succeeded"
            and operation["risk_class"] == "write",
            {"operation_state": operation["state"], "attempts": [a["state"] for a in attempts]},
        )
        receipt, receipt_bytes = t048.receipt(stack, operation["receipt_artifact_id"])
        record.check(
            "the sealed receipt wraps the endpoint's real 2xx body",
            receipt["status"] == 200
            and receipt["body"].get("ok") is True
            and receipt["body"].get("write_id") == WRITE_ID
            and receipt["capability_kind"] == "connector",
            receipt_bytes[:300].decode("utf-8", "replace"),
        )
        replay = t050.invoke_write(
            stack,
            resumed,
            policy_id=policy["id"],
            params=PARAMS,
            logical_key=LOGICAL_KEY,
            operation_id=operation_id,
        )
        record.check(
            "a duplicate delivery of the same logical call never sends a second write",
            replay.status == 200
            and replay.json["state"] == "succeeded"
            and len(server.records) == 1,
            {"state": replay.json.get("state"), "records": len(server.records)},
        )

        # -- 7. finish the published Run through the real F03 finalizer ------
        output_id, _output_sha = t033.upload_run_artifact(
            stack,
            t033.artifact_client(stack, "worker"),
            resumed,
            content=receipt_bytes,
            purpose="final_output",
            relation="output",
        )
        record.command(
            "worker artifact protocol: createWorkerUpload -> PUT -> completeWorkerUpload -> "
            "artifact_finalize -> register(relation=output)"
        )
        record.check(
            "the Run's final output is the real sealed connector receipt",
            output_id is not None,
        )
        proposed = t029.execution_client(stack, "worker").submit_result(
            run_id,
            attempt_token=resumed["attempt_token"],
            suggested_state="succeeded",
            output_artifact_id=output_id,
        )
        record.check(
            "the worker's succeeded proposal is stored on the live attempt",
            proposed.status == 200 and proposed.json["state"] in {"running", "succeeded"},
            proposed.text,
        )
        exited = t029.execution_client(stack, "supervisor").confirm_exit(
            resumed["attempt_token"],
            execution_unit_id=RESUME_UNIT,
            evidence=t029.exit_evidence(resumed, started=True, exit_code=0),
        )
        record.check(
            "the supervisor exit lets the real finalizer commit the terminal Run",
            exited.status == 200,
            exited.text,
        )
        final_run = _run_row(stack, run_id)
        record.check(
            "the published Run is succeeded with the receipt-linked output and no error",
            final_run["state"] == "succeeded"
            and final_run["error_code"] is None
            and final_run["output_artifact_id"] == output_id
            and final_run["kind"] == "published"
            and final_run["owner_id"] == service_id
            and final_run["finished_at"] is not None,
            {
                "state": final_run["state"],
                "output_artifact_id": final_run["output_artifact_id"],
                "finished_at": final_run["finished_at"],
            },
        )
        owner_view = writer.client.get(f"/api/v1/runs/{run_id}")
        record.http("GET", f"/api/v1/runs/{run_id} (service key, read scope)", owner_view)
        record.check(
            "the service key can read its own succeeded Run through the contract view",
            owner_view.status == 200 and owner_view.json["state"] == "succeeded",
            owner_view.text,
        )
        output = t033.artifact_content(writer, output_id)
        record.check(
            "the Run output download equals the sealed receipt bytes",
            output.status == 200 and output.raw == receipt_bytes,
            {"status": output.status, "bytes": len(output.raw)},
        )

        record.note(
            "three_way_gate",
            {
                "scope": "application:read + application:write on the API key",
                "acl": f"unexpired read grant {grant_id} on {application_id}",
                "human": f"responsible human {responsible.principal_id} approved approval {approval_id}",
                "external_write": "1 physical POST /v1/orders observed by the endpoint",
                "approval_approver_is_human": True,
                "service_key_decision_status": machine_decision.status,
            },
        )
        record.note(
            "ac2",
            {
                "verdict": "satisfied at the platform/authorization boundary",
                "scope_and_acl_on_one_call": "no grant -> 404; read-only key -> 403; both -> 202",
                "human_approval": (
                    "the service-owned Run's write operation is approved only by its responsible "
                    "human (403 admin, 404 stranger/service key), bound to the exact params hash"
                ),
                "exactly_one_write": "endpoint request log = 1; replay = 1; one attempt",
                "not_claimed": (
                    "the published graph itself does not invoke the connector: J6b records that "
                    "product gap as blocked. The write uses the documented worker capability "
                    "protocol with the Run's live attempt token (same seam as T048/T050/J2)."
                ),
            },
        )
        record.note("worker_seam", _worker_seam_note())
    except BaseException:
        server.close()
        raise
    return {
        "server": server,
        "published": published,
        "writer": writer,
        "service_id": service_id,
        "responsible_id": responsible.principal_id,
        "publisher_id": publisher.principal_id,
        "application_id": application_id,
        "version_id": version_id,
        "policy_id": policy["id"],
        "conversation_id": conversation,
        "csv_id": csv_id,
        "fanout_key_id": read_key_id,
        "writes": len(server.records),
        "result": {
            "request_id": request["id"],
            "run_id": run_id,
            "approval_id": approval_id,
            "operation_id": operation_id,
            "output_artifact_id": output_id,
            "writes": len(server.records),
            "run_state": final_run["state"],
        },
    }


# ---------------------------------------------------------------------------
# J6b: the same published version really executes, and the graph emits no write
# ---------------------------------------------------------------------------
def run_graph_dispatch_gap(stack, record, state: dict) -> dict:
    """Run the same published version through the real supervisor graph executor.

    The Run is dispatched by the same service key and executed by the real
    ``cmd/supervisor exec --once`` (the preset python node runs in WSL2/runsc).
    The journey shows, with the real product, that the published graph completes
    without producing a connector operation or an external write: the node type
    that would emit one does not exist in this build. It is therefore recorded as
    ``blocked`` rather than passed.
    """
    server = state["server"]
    published = state["published"]
    writer = state["writer"]
    application_id = state["application_id"]
    version_id = state["version_id"]
    service_id = state["service_id"]
    try:
        record.command(
            "python -m pytest tests/f12/test_t101_journeys.py -k service_key_write"
        )
        record.truth_source(
            "platform.operations (zero connector operations for this Run) via psql"
        )
        record.truth_source(
            "loopback connector server request log (unchanged physical write count)"
        )
        record.truth_source(
            "real supervisor report + platform.runs state/output artifact"
        )
        writes_before = len(server.records)
        submitted = f08.submit_request(
            writer,
            state["conversation_id"],
            TASK_DOCUMENT,
            artifact_ids=[state["csv_id"]],
            version_id=version_id,
            key=f00b.new_uuid(),
        )
        record.http(
            "POST /api/v1/requests (same service key, same published version)", submitted
        )
        request = f08.request_of(submitted)
        run_id = request["run_id"]
        completed, report = execution.execute_once(stack)
        record.command(
            "cmd/supervisor exec --once (real graph execution of the published Run, WSL2/runsc)"
        )
        record.check(
            "the real supervisor executes the published graph to success",
            completed.returncode == 0 and report.get("outcome") == "succeeded",
            {"returncode": completed.returncode, "outcome": report.get("outcome")},
        )
        run_row = _run_row(stack, run_id)
        record.check(
            "the graph Run is a published Run of the service principal pinned to the version",
            run_row["state"] == "succeeded"
            and run_row["error_code"] is None
            and run_row["kind"] == "published"
            and run_row["owner_id"] == service_id
            and run_row["application_version_id"] == version_id,
            {
                "state": run_row["state"],
                "kind": run_row["kind"],
                "owner_id": run_row["owner_id"],
            },
        )
        node_results = t042.wait_node_results(writer, run_id, expected=4)
        record.check(
            "the executed graph produced its real node outputs (python + end)",
            len(node_results["items"]) >= 4,
            [item["label"] or item["node_id"] for item in node_results["items"]],
        )
        output_id = t033.output_artifact_id(stack.dbname, run_id)
        record.check(
            "the graph Run has a real linked final output artifact",
            output_id is not None,
            {"output_artifact_id": output_id},
        )
        operations = _connector_operations(stack, run_id)
        record.check(
            "the graph produced zero connector operations (no write node exists to emit one)",
            operations == [],
            operations,
        )
        record.check(
            "the endpoint's physical write count did not change during the graph Run",
            len(server.records) == writes_before,
            {"before": writes_before, "after": len(server.records)},
        )
        snapshot = t027.snapshot_row(stack.dbname, run_row["execution_snapshot_id"])
        record.check(
            "the Run's frozen manifest really carries the write policy the graph cannot call",
            snapshot["application_version_id"] == version_id
            and snapshot["manifest_sha256"] == published["manifest_sha256"],
            {
                "snapshot_kind": snapshot["kind"],
                "manifest_sha256": snapshot["manifest_sha256"],
            },
        )
        record.block(
            "product gap (graph-originated controlled write): the published graph executes "
            "successfully but cannot itself invoke a connector. The graph executor's node "
            "dispatch table has no connector node (internal/presetexec/graphrun.go) and the "
            "agent tool call is always capability_kind=\"tool\" (internal/presetexec/agent.go), "
            "resolved by the tool branch that ignores tool_versions.connector_versions "
            "(internal/capabilities/service.go); the F04B approval node explicitly never "
            "authorizes a later external write (internal/presetexec/approval.go). So the same "
            "Run's manifest carries the approved write policy, yet the graph emits zero "
            "connector operations and the endpoint sees zero writes. J6 closes the AC2 "
            "authorization/exactly-once chain through the documented worker capability seam; "
            "'the published task itself triggers the write' needs the product change described "
            "in reports/f12/T101-journeys.md §5."
        )
        record.note(
            "gap",
            {
                "run_state": run_row["state"],
                "connector_operations": len(operations),
                "endpoint_writes_during_graph_run": len(server.records) - writes_before,
                "manifest_carries_write_policy": True,
                "blocked_clause": (
                    "graph-originated connector dispatch (a connector-capable node, or an "
                    "agent/tool dispatch that honors connector_versions)"
                ),
                "product_files": [
                    "internal/presetexec/graphrun.go (node dispatch table)",
                    "internal/presetexec/agent.go (tool call is capability_kind=tool)",
                    "internal/capabilities/service.go (tool branch ignores connector_versions)",
                    "internal/presetexec/approval.go (node approval never authorizes a write)",
                ],
            },
        )
        return {
            "request_id": request["id"],
            "run_id": run_id,
            "run_state": run_row["state"],
            "connector_operations": 0,
            "endpoint_writes": len(server.records),
        }
    finally:
        server.close()


def _worker_seam_note() -> dict:
    return {
        "what_the_run_proves": (
            "service-key scope + resource ACL + responsible-human approval + exactly one "
            "physical write, all on a real published-version Run"
        ),
        "seam": (
            "the write operation is invoked by a real mTLS worker capability call with the "
            "Run's live attempt token (T048/T050/J2 seam), not by a graph node"
        ),
        "why": (
            "the graph executor has no connector node and the agent tool call is kind=tool, so "
            "no published graph can emit a connector operation in this build"
        ),
        "recorded_as": "J6b = blocked (graph-originated dispatch); J6 = passed for AC2's gate",
    }
