"""F06/T050 real-stack helpers: exact-parameter approvals and safe resume.

The stack is T048's (throwaway PostgreSQL migrated through 0031, the compiled
``cmd/api``, real mTLS, real object bytes, a real loopback connector endpoint and
the real admin connector registry). The connector write is a real HTTP request
the guard would make; nothing about the proposal, the decision or the resume is
mocked.

The only non-contract surfaces used here are the ones earlier tickets already
documented for tests:

* the supervisor ``waiting_approval`` result / exit protocol (T035/T029) stands in
  for the executor's own exit evidence;
* ``platform.approvals`` reads are the durable audit truth, never the API echo.
"""

from __future__ import annotations

import json
import uuid

from support import f00b, t041, t048
from support import stack as st

# The approval error vocabulary mirrored from internal/approvals.
APPROVAL_NOT_FOUND = "approval_not_found"
APPROVER_MISMATCH = "approver_mismatch"
APPROVAL_NOT_PENDING = "approval_not_pending"
APPROVAL_EXPIRED = "approval_expired"
PARAMS_MISMATCH = "approval_params_mismatch"
DECISION_CONFLICT = "decision_conflict"
PERMISSION_REVOKED = "permission_revoked"
POLICY_REVOKED = "policy_revoked"

# Run failure codes a refused decision must produce.
REJECTED_CODE = "approval_rejected"
EXPIRED_CODE = "approval_expired"


def approval_path(approval_id: str) -> str:
    return "/api/v1/approvals/" + approval_id


def decision_path(approval_id: str) -> str:
    return approval_path(approval_id) + "/decision"


def decide(
    session: f00b.Session,
    approval_id: str,
    *,
    decision: str = "approve",
    params_sha256: str,
    idempotency_key: str | None = None,
    csrf: str | None = None,
    client=None,
):
    """POST one decision with the required Idempotency-Key header."""
    target = client or session.client
    return target.post(
        decision_path(approval_id),
        body={"decision": decision, "params_sha256": params_sha256},
        csrf=session.csrf if csrf is None else csrf,
        headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
    )


def get_approval(session: f00b.Session, approval_id: str):
    return session.client.get(approval_path(approval_id))


def list_approvals(session: f00b.Session, **query):
    from urllib.parse import urlencode

    suffix = ("?" + urlencode(query)) if query else ""
    return session.client.get("/api/v1/approvals" + suffix)


# ---------------------------------------------------------------------------
# connector setup + the parked invocation
# ---------------------------------------------------------------------------
def write_policy(
    stack,
    admin: f00b.Session,
    *,
    origin: str,
    path_prefix: str = "/v1",
    request_schema: dict | None = None,
    response_schema: dict | None = None,
) -> dict:
    return t048.register(
        stack,
        admin,
        origin=origin,
        method="POST",
        path_prefix=path_prefix,
        request_schema=request_schema,
        response_schema=response_schema,
        risk_class="write",
    )


def invoke_write(
    stack,
    lease: dict,
    *,
    policy_id: str,
    params: dict,
    logical_key: str,
    operation_id: str | None = None,
    attempt_token: str | None = None,
):
    return t048.invoke(
        stack,
        lease,
        policy_id=policy_id,
        params=params,
        logical_key=logical_key,
        operation_id=operation_id,
        attempt_token=attempt_token,
    )


def default_params() -> dict:
    return {"path": "/v1/orders", "body": {"amount": 1250, "currency": "CNY"}}


# ---------------------------------------------------------------------------
# database / artifact reads
# ---------------------------------------------------------------------------
def approval_rows(dbname: str, *, run_id: str | None = None) -> list[dict]:
    where = "TRUE"
    if run_id:
        where = f"run_id = {st.quote_literal(run_id)}::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, operation_id::text, node_key, "
        "proposal_artifact_id::text, params_sha256, approver_id::text, state, "
        "expires_at::text, decided_at::text, decided_by::text, decision_key "
        "FROM platform.approvals "
        f"WHERE {where} ORDER BY expires_at, id",
    )


def approval_row(dbname: str, approval_id: str) -> dict | None:
    rows = [
        row for row in approval_rows(dbname) if row["id"] == approval_id
    ]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def approval_for_operation(dbname: str, operation_id: str) -> dict | None:
    rows = [
        row
        for row in approval_rows(dbname)
        if row["operation_id"] == operation_id
    ]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def proposal_artifact(stack, approval: dict) -> dict:
    return t041.artifact_row(stack.dbname, approval["proposal_artifact_id"])


def proposal(stack, approval: dict) -> dict:
    artifact = proposal_artifact(stack, approval)
    payload = (stack.object_dir / artifact["object_key"]).read_bytes()
    return json.loads(payload)


def operation(dbname: str, run_id: str, operation_id: str) -> dict | None:
    return t041.operation_row(dbname, run_id, operation_id)


def point_expiry_past(dbname: str, approval_id: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.approvals SET expires_at = now() - interval '1 minute' "
        f"WHERE id = {st.quote_literal(approval_id)}::uuid",
    )


def run_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, state, error_code, revision, phase, cancel_requested_at::text, "
        "finished_at::text FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def set_budget(dbname: str, run_id: str, budget: str) -> None:
    st.sql(
        dbname,
        f"UPDATE platform.runs SET budget_limit = {st.quote_literal(budget)}::numeric "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )


def publication_review_id(dbname: str, owner_id: str) -> str | None:
    """Seed one real publication review row, or None when the chain cannot.

    Publication review is a *different aggregate* (an application submission
    reviewed by its own reviewer); the run-approval decision path must not see it.
    A real application and a real report row are required because the foreign keys
    are real; the report may name any existing sealed artifact as its result.
    """
    applications = st.sql_rows(
        dbname,
        "SELECT id::text FROM platform.applications "
        f"WHERE owner_id = {st.quote_literal(owner_id)}::uuid LIMIT 1",
    )
    artifacts = st.sql_rows(dbname, "SELECT id::text FROM platform.artifacts LIMIT 1")
    if not applications or not artifacts:
        return None
    report_id = str(uuid.uuid4())
    review_id = str(uuid.uuid4())
    st.sql(
        dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{st.quote_literal(report_id)}::uuid, {st.quote_literal('1' * 64)}, "
        f"{st.quote_literal('2' * 64)}, "
        f"{st.quote_literal(artifacts[0]['id'])}::uuid, 'not_run')",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.publication_reviews "
        "(id, application_id, submitted_by, snapshot, manifest_sha256, report_id, state) "
        "VALUES ("
        f"{st.quote_literal(review_id)}::uuid, "
        f"{st.quote_literal(applications[0]['id'])}::uuid, "
        f"{st.quote_literal(owner_id)}::uuid, '{{}}'::jsonb, "
        f"{st.quote_literal('3' * 64)}, "
        f"{st.quote_literal(report_id)}::uuid, 'submitted')",
    )
    return review_id


def v1_artifact_content(session: f00b.Session, artifact_id: str):
    return session.client.get(f"/api/v1/artifacts/{artifact_id}/content")
