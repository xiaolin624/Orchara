"""F06/T051 real-stack helpers: unknown external writes and evidence reconciliation.

The stack is T050's (throwaway PostgreSQL migrated through 0032, the compiled
``cmd/api``, real mTLS capability calls, real object bytes, the real object
store, a real loopback connector endpoint). The lost-receipt server is a real
HTTP/1.1 server: it reads the complete request (the write is real) and then closes
the connection without writing any response, so Go's client observes a
connection loss and the platform cannot know whether the remote side applied the
write.

Only tested surfaces are used: the contract mTLS ``invokeCapability`` and owner
``reconcileOperation``, the non-contract capability-results report the worker
already uses, and the T050 approval decision. Every assertion reads PostgreSQL,
the sealed object bytes or the connector server's own request log.
"""

from __future__ import annotations

import json
import socket
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import f00b, t028, t035, t041, t048, t050
from support import stack as st

# Stable reconcile / run vocabulary mirrored from the Go services.
CONCLUSION_SUCCESS = "confirmed_success"
CONCLUSION_NOT_SENT = "confirmed_not_sent"
CONCLUSION_FAILURE = "confirmed_failure"
STATE_UNKNOWN = "unknown"
STATE_SUCCEEDED = "succeeded"
STATE_FAILED = "failed"
STATE_NEEDS_RECONCILIATION = "needs_reconciliation"
LIMITATION_NO_IDEMPOTENCY = "no_idempotency_or_query_support"


class _DropHandler(BaseHTTPRequestHandler):
    """The real write endpoint. ``responder`` decides success vs. dropped receipt."""

    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": body.decode("utf-8", "replace"),
        }
        self.server.records.append(record)
        answer = self.server.responder(record)
        if answer is None:
            # The write reached the server; the client never gets a receipt.
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.connection.close()
            return
        status, headers, payload = answer
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, *_args) -> None:  # keep pytest output clean
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class LostReceiptServer:
    """A real loopback connector endpoint that can drop its response."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        # Default: a successful JSON answer (the control case).
        self.responder = lambda _record: (
            200,
            {"Content-Type": "application/json"},
            json.dumps({"ok": True, "write_id": "w-real-1"}).encode(),
        )
        self._httpd = _Server(("127.0.0.1", 0), _DropHandler)
        self._httpd.records = self.records
        self._httpd.responder = lambda record: self.responder(record)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def respond_success(self, payload: dict | None = None) -> None:
        body = json.dumps(payload if payload is not None else {"ok": True, "write_id": "w-real-1"})
        self.responder = lambda _record: (
            200,
            {"Content-Type": "application/json"},
            body.encode(),
        )

    def respond_drop(self) -> None:
        """Perform the real write, then close the connection with no response."""
        self.responder = lambda _record: None

    def respond_status(self, status: int, payload: dict | None = None) -> None:
        body = json.dumps(payload if payload is not None else {"ok": False})
        self.responder = lambda _record: (
            status,
            {"Content-Type": "application/json"},
            body.encode(),
        )

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


# ---------------------------------------------------------------------------
# the unknown write: register, park, approve, resume, lose the receipt
# ---------------------------------------------------------------------------
def prepare_approved_write(
    stack,
    server: LostReceiptServer,
    owner,
    *,
    logical_key: str = "agent:approval-node:tool:call-1",
    params: dict | None = None,
) -> dict:
    """A write-risk connector call that has passed approval and is about to send.

    Mirrors T050's exact flow: invoke parks the operation, the owner approves the
    exact params hash, the supervisor confirms the waiting attempt exited, the
    resume job re-arms the run, and a resumed lease is claimed. No request has
    reached the endpoint yet.
    """
    policy = t050.write_policy(stack, owner.admin, origin=server.url)
    t048.bind_tool_connectors(stack.dbname, [policy["id"]])
    _, run, lease = t041.claimed_run(stack, run_owner=owner)
    first = t050.invoke_write(
        stack,
        lease,
        policy_id=policy["id"],
        params=params or t050.default_params(),
        logical_key=logical_key,
    )
    assert first.status == 200, first.text
    assert first.json["state"] == "awaiting_approval", first.text
    approval = t050.approval_row(stack.dbname, first.json["approval_id"])
    assert approval is not None
    decided = t050.decide(
        owner.creator, approval["id"], params_sha256=approval["params_sha256"]
    )
    assert decided.status == 200, decided.text
    # Park the waiting proposal and confirm the exit the way T050 does.
    worker = t035.execution_client(stack, "worker")
    parked = t035.submit_waiting(
        worker, lease, checkpoint_id="capability:" + first.json["operation_id"]
    )
    assert parked.status == 200, parked.text
    assert t035.confirm_waiting_exit(stack, lease, execution_unit_id="t041-unit").status == 200
    assert len(t035.resume_jobs(stack.dbname, run["id"])) == 1
    resumed = t029_resumed_lease(stack)
    return {
        "owner": owner,
        "policy": policy,
        "run": run,
        "lease": resumed,
        "operation_id": first.json["operation_id"],
        "approval": approval,
        "logical_key": logical_key,
        "params": params or t050.default_params(),
    }


def t029_resumed_lease(stack):
    from support import t029

    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t051-supervisor", execution_unit_id="t051-resume-unit"
    )
    assert response.status == 200, response.text
    return response.json


def invoke_capability(stack, lease: dict, prepared: dict):
    return t048.invoke(
        stack,
        lease,
        policy_id=prepared["policy"]["id"],
        params=prepared["params"],
        logical_key=prepared["logical_key"],
        operation_id=prepared["operation_id"],
    )


def submit_failed_result(stack, lease: dict, *, error_code: str = "agent_unknown") -> None:
    worker = t035.execution_client(stack, "worker")
    response = worker.submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="failed",
        error_code=error_code,
    )
    assert response.status == 200, response.text


def submit_succeeded_result(stack, lease: dict, output_artifact_id: str) -> None:
    worker = t035.execution_client(stack, "worker")
    response = worker.submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=output_artifact_id,
    )
    assert response.status == 200, response.text


def confirm_exit(stack, lease: dict) -> None:
    response = t035.execution_client(stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="t051-resume-unit",
        evidence=t035.exit_evidence(lease, started=True, exit_code=0),
    )
    assert response.status == 200, response.text


def seed_output_artifact(stack, owner_id: str) -> str:
    return t035.seed_output_artifact(stack, owner_id)


def link_output(stack, run_id: str, artifact_id: str, attempt_token: str) -> None:
    t035.link_output(stack.dbname, run_id, artifact_id, attempt_token=attempt_token)


# ---------------------------------------------------------------------------
# evidence artifacts
# ---------------------------------------------------------------------------
def seed_evidence_artifact(
    stack,
    owner_id: str,
    *,
    operation_id: str,
    run_id: str,
    content: dict | None = None,
) -> str:
    """A real ready artifact (object bytes + row) owned by the owner."""
    payload = {
        "operation_id": operation_id,
        "run_id": run_id,
        "provider_receipt": {"write_id": "w-real-1", "status": "accepted"},
    }
    if content is not None:
        payload = content
    body = json.dumps(payload).encode()
    artifact = f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner_id,
        media_type="application/json",
        purpose="task_input",
        content=body,
        state="ready",
        staging=False,
        sealed_content=body,
    )
    return artifact.id


def tombstone_evidence(stack, artifact_id: str) -> None:
    st.sql(
        stack.dbname,
        "UPDATE platform.artifacts SET deleted_at = now() "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


# ---------------------------------------------------------------------------
# the owner reconcile call
# ---------------------------------------------------------------------------
def reconcile_path(operation_id: str) -> str:
    return "/api/v1/operations/" + operation_id + "/reconcile"


def reconcile(
    session,
    operation_id: str,
    *,
    conclusion: str,
    evidence_artifact_id: str,
    idempotency_key: str | None = None,
    csrf: str | None = None,
    body: dict | None = None,
):
    if body is None:
        body = {"conclusion": conclusion, "evidence_artifact_id": evidence_artifact_id}
    return session.client.post(
        reconcile_path(operation_id),
        body=body,
        csrf=session.csrf if csrf is None else csrf,
        headers={"Idempotency-Key": idempotency_key or str(uuid.uuid4())},
    )


def reconcile_without_key(session, operation_id: str, *, conclusion: str, evidence: str):
    return session.client.post(
        reconcile_path(operation_id),
        body={"conclusion": conclusion, "evidence_artifact_id": evidence},
        csrf=session.csrf,
    )


# ---------------------------------------------------------------------------
# database / API read models
# ---------------------------------------------------------------------------
def operation_row(dbname: str, run_id: str, operation_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, logical_key, capability_version, capability_kind, "
        "risk_class, params_sha256, params_artifact_id::text, state, "
        "receipt_artifact_id::text, provider_idempotency_key, dispatched_at::text, "
        "reconciled_by::text, reconciliation_evidence_id::text, "
        "reconciliation_conclusion, reconciliation_key, reconciliation_limitation, "
        "last_error_code, created_at::text, updated_at::text "
        "FROM platform.operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND id = {st.quote_literal(operation_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def operation_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text, state, logical_key FROM platform.operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY created_at, id",
    )


def attempt_rows(dbname: str, operation_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT attempt_no, state, attempt_token::text, result_artifact_id::text, "
        "error_code, created_at::text AS created_at "
        "FROM platform.operation_attempts "
        f"WHERE operation_id = {st.quote_literal(operation_id)}::uuid ORDER BY attempt_no",
    )


def physical_write_count(dbname: str, operation_id: str) -> int:
    return len(attempt_rows(dbname, operation_id))


def connector_request_count(server: LostReceiptServer) -> int:
    """The real write count observed by the endpoint itself."""
    return len(server.records)


def unknown_row(dbname: str, run_id: str, operation_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, run_id::text, operation_id::text, reason, "
        "created_at::text, resolved_at::text "
        "FROM platform.run_unknown_operations "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
        f"AND operation_id = {st.quote_literal(operation_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def run_state(dbname: str, run_id: str) -> dict:
    return t050.run_row(dbname, run_id)


def ledger(session, run_id: str) -> dict:
    response = session.client.get(f"/api/v1/runs/{run_id}/capabilities")
    assert response.status == 200, response.text
    return response.json


def ledger_operation(session, run_id: str, operation_id: str) -> dict:
    body = ledger(session, run_id)
    for item in body["items"]:
        if item["id"] == operation_id:
            return item
    raise AssertionError(f"operation {operation_id} not in ledger: {body['items']}")
