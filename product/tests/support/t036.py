"""F03/T036 real-stack helpers: cancel, cancelling and the unknown guard.

Everything here is real: the T031/T035 stack (throwaway PostgreSQL migrated with
the compiled ``cmd/admin`` chain, the compiled API behind HTTP with the internal
mTLS listener, a real filesystem object store with HMAC-signed links). The cancel
endpoint and both unknown-operation bridges are real HTTP/mTLS calls; every
assertion reads PostgreSQL.

Boundaries (documented in the T036 report):

* ``POST /internal/v1/runs/{id}/unknown-operations`` and its ``/resolve`` sibling
  are non-contract bridges for F06-owned real operations/receipts.
* The contract Run schema has no cancel fields, so the owner detail carries
  ``X-Run-Cancel-Requested-At`` (the existing T033 extension).
"""

from __future__ import annotations

import uuid

from support import stack as st
from support import t029, t033, t035

# Re-exports so a T036 test imports one module.
T036Stack = t035.T035Stack
Session = t035.Session
admin_session = t035.admin_session
session_for = t035.session_for
invite_and_activate = t035.invite_and_activate
new_uuid = t035.new_uuid
golden_owner = t035.golden_owner
minimal_owner = t035.minimal_owner
queued_run = t035.queued_run
claim_lease = t035.claim_lease
exit_evidence = t035.exit_evidence
run_state_row = t035.run_state_row
job_row = t035.job_row
jobs_for_run = t035.jobs_for_run
attempt_row = t035.attempt_row
attempts_for_run = t035.attempts_for_run
quota_counter = t035.quota_counter
quota_allocations = t035.quota_allocations
allocations_for_holder = t035.allocations_for_holder
allocation_for_job = t035.allocation_for_job
assert_run_shape = t035.assert_run_shape
artifact_row = t035.artifact_row
seed_output_artifact = t035.seed_output_artifact
link_output = t035.link_output
result_body = t035.result_body
reported_result = t035.reported_result
run_path = t035.run_path
run_events = t035.run_events
run_event_count = t035.run_event_count
latest_run_status = t035.latest_run_status
waiting_row = t035.waiting_row
waiting_count = t035.waiting_count
resume_jobs = t035.resume_jobs
assert_no_resume = t035.assert_no_resume
set_run_max_active_ms = t035.set_run_max_active_ms
shift_attempt_started_at_seconds = t035.shift_attempt_started_at_seconds
set_run_active_elapsed_ms = t035.set_run_active_elapsed_ms
seed_child_run = t035.seed_child_run
set_run_state = t035.set_run_state
wait_until = t035.wait_until
DEADLINE_CODE = t035.DEADLINE_CODE
GOLDEN_MAX_ACTIVE_MS = t035.GOLDEN_MAX_ACTIVE_MS
execution_client = t035.execution_client

# Cancel vocabulary (mirrors the Go service and contract state enum).
STATE_CANCELLING = "cancelling"
STATE_CANCELLED = "cancelled"
STATE_NEEDS_RECONCILIATION = "needs_reconciliation"
RUN_STATE_CONFLICT = "run_state_conflict"
RUN_TERMINAL = "run_terminal"
EVENT_CONFLICT = "event_conflict"
STALE_ATTEMPT = "stale_attempt"
UNKNOWN_CODE = "unknown_operations"
UNKNOWN_REASON = "dispatch_timeout"
CANCEL_HEADER = "X-Run-Cancel-Requested-At"


def cancel_path(run_id: str) -> str:
    return f"/api/v1/runs/{run_id}/cancel"


def cancel_run(session: Session, run_id: str, *, csrf: str | None = None):
    """The owner's real cancel request (CSRF by default, like the page)."""
    return session.client.post(cancel_path(run_id), csrf=session.csrf if csrf is None else csrf)


def cancel_headers(response) -> dict:
    raw = response.header(CANCEL_HEADER)
    return {"cancel_requested_at": None if raw in (None, "") else raw}


# ---------------------------------------------------------------------------
# the unknown-operation bridge (worker|supervisor mTLS)
# ---------------------------------------------------------------------------
class UnknownOperationClient(t035.WaitingInternalClient):
    def record_unknown(
        self,
        run_id: str,
        *,
        attempt_token: str,
        operation_id: str | None = None,
        reason: str = UNKNOWN_REASON,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {
                "attempt_token": attempt_token,
                "operation_id": operation_id or new_uuid(),
                "reason": reason,
            }
        return self.post(f"/internal/v1/runs/{run_id}/unknown-operations", body, **kwargs)

    def resolve_unknown(
        self,
        run_id: str,
        operation_id: str,
        *,
        attempt_token: str,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {"attempt_token": attempt_token}
        return self.post(
            f"/internal/v1/runs/{run_id}/unknown-operations/{operation_id}/resolve",
            body,
            **kwargs,
        )


def unknown_client(stack: T036Stack, role: str = "supervisor") -> UnknownOperationClient:
    return UnknownOperationClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def unknown_operation_row(dbname: str, run_id: str, operation_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, run_id::text AS run_id, operation_id::text AS operation_id, "
        "reason, created_at::text AS created_at, resolved_at::text AS resolved_at "
        "FROM platform.run_unknown_operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND operation_id = {st.quote_literal(operation_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def unknown_operations(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, operation_id::text AS operation_id, reason, "
        "created_at::text AS created_at, resolved_at::text AS resolved_at "
        "FROM platform.run_unknown_operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY created_at, id",
    )


def unresolved_unknown_count(dbname: str, run_id: str) -> int:
    return int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.run_unknown_operations "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid AND resolved_at IS NULL",
        )
    )


def waiting_row(dbname: str, run_id: str) -> dict | None:
    """The full waiting evidence including T036's cancel marker."""
    rows = st.sql_rows(
        dbname,
        "SELECT run_id::text AS run_id, kind, checkpoint_id, decision::text AS decision, "
        "decision_at::text AS decision_at, exit_confirmed_at::text AS exit_confirmed_at, "
        "resume_job_id::text AS resume_job_id, resume_artifact_id::text AS resume_artifact_id, "
        "cancel_requested_at::text AS cancel_requested_at, "
        "created_at::text AS created_at, updated_at::text AS updated_at "
        "FROM platform.run_waitings "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def waiting_cancel_marker(dbname: str, run_id: str) -> str | None:
    row = waiting_row(dbname, run_id)
    return None if row is None else row["cancel_requested_at"]


def run_cancel_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, state, phase, revision, "
        "cancel_requested_at::text AS cancel_requested_at, error_code, "
        "finished_at::text AS finished_at, output_artifact_id::text AS output_artifact_id "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def assert_cancelled(row: dict) -> None:
    assert row["state"] == STATE_CANCELLED, row
    assert row["phase"] == "idle", row
    assert row["finished_at"] is not None, row
    assert row["cancel_requested_at"] is not None, row


# ---------------------------------------------------------------------------
# run-bound artifact calls used to prove the refusal / receipt split
# ---------------------------------------------------------------------------
def run_artifact_client(stack: T036Stack, role: str = "worker") -> t033.RunArtifactClient:
    return t033.artifact_client(stack, role)


def create_run_upload(client, lease: dict, content: bytes = b'{"ok":true}') -> dict:
    created = client.create_upload(
        lease["run_id"],
        {
            "attempt_token": lease["attempt_token"],
            "media_type": "application/json",
            "size_bytes": len(content),
            "purpose": "node_output",
        },
    )
    assert created.status == 201, created.text
    return created.json


def registration_body(lease: dict, artifact_id: str) -> dict:
    return {
        "attempt_token": lease["attempt_token"],
        "artifact_id": artifact_id,
        "relation": "output",
    }


def new_operation_id() -> str:
    return str(uuid.uuid4())
