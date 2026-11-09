"""F05/T033 real-stack helpers: run-bound artifacts, the sandbox and the executor.

The stack is T031's: throwaway PostgreSQL migrated with the real ``cmd/admin``
chain, the compiled ``cmd/api`` behind HTTP with the real internal mTLS listener,
a real filesystem object store with HMAC-signed links, and the compiled
``cmd/maintenance`` / ``cmd/supervisor`` binaries.

Everything here is real: the scoped artifact routes are real mTLS requests, the
bytes move through signed staging/sealed links, the artifact_finalize handler
runs in a real maintenance process, and the sandbox is a real runsc (gVisor)
execution inside WSL2. The only test-owned fixtures are the uploaded CSV and the
expected summary, which are the public sample from WORKFLOW-SPEC §6.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

from support import execution_support as execution
from support.runtime_stack import Stack
from support import f00b, stack as st, t024, t027, t028, t029, t031

# Re-exports so a T033 test imports one module.
Session = t031.Session
admin_session = t031.admin_session
session_for = t031.session_for
invite_and_activate = t031.invite_and_activate
new_uuid = t031.new_uuid
sql_utf8 = t031.sql_utf8
seed_golden_draft = t031.seed_golden_draft
golden_owner = t031.golden_owner
minimal_owner = t031.minimal_owner
draft_revision = t027.draft_revision
submit_debug = t027.submit_debug
run_path = t031.run_path
get_run = t031.get_run
set_cancel_requested = t029.set_cancel_requested
run_state_row = t031.run_state_row
job_row = t031.job_row
quota_counter = t031.quota_counter
attempt_row = t031.attempt_row
attempts_for_run = t031.attempts_for_run
wait_until = t031.wait_until
run_artifacts = t031.run_artifact_rows
execution_client = t031.execution_client
exit_evidence = t031.exit_evidence
new_signing_key = t024.new_signing_key
put_link = t024.put_link
get_link = t024.get_link
artifact_row = t031.artifact_row

PRODUCT_ROOT = st.PRODUCT_ROOT
PYTHON = "python"

# The documented public sales sample (WORKFLOW-SPEC §6): three rows, total
# 250.00, east 200.00, west 50.00, three orders. UTF-8 on purpose.
SALES_CSV = (
    "order_id,date,region,amount\n"
    "o-1,2026-01-02,东区,120.50\n"
    "o-2,2026-01-02,东区,79.50\n"
    "o-3,2026-01-03,西区,50.00\n"
).encode("utf-8")

EXPECTED_SUMMARY_JSON = (
    "{\n"
    '  "total": "250.00",\n'
    '  "regions": {\n'
    '    "东区": "200.00",\n'
    '    "西区": "50.00"\n'
    "  },\n"
    '  "order_count": 3\n'
    "}\n"
).encode("utf-8")

EXPECTED_SUMMARY_CSV = "region,total\n东区,200.00\n西区,50.00\n".encode("utf-8")

BAD_CSV = b"order_id,date,region,amount\no-1,2026-01-02,east,-1.00\n"
REJECTION_CODE = "negative_amount"

SANDBOX_DISTRO = execution.SANDBOX_DISTRO
SANDBOX_ROOTFS = execution.SANDBOX_ROOTFS
SANDBOX_STATE_ROOT = execution.SANDBOX_STATE_ROOT



# ---------------------------------------------------------------------------
# the run-scoped internal endpoints (real mTLS, worker capacity)
# ---------------------------------------------------------------------------
class RunArtifactClient(f00b.InternalClient):
    """The contract's five run-scoped artifact routes."""

    def create_upload(self, run_id: str, body: dict, **kwargs):
        return self.post(f"/internal/v1/runs/{run_id}/artifact-uploads", body, **kwargs)

    def complete(self, run_id: str, artifact_id: str, body: dict, **kwargs):
        return self.post(
            f"/internal/v1/runs/{run_id}/artifacts/{artifact_id}/complete", body, **kwargs
        )

    def download(self, run_id: str, body: dict, **kwargs):
        return self.post(f"/internal/v1/runs/{run_id}/artifact-downloads", body, **kwargs)

    def status(self, run_id: str, body: dict, **kwargs):
        return self.post(f"/internal/v1/runs/{run_id}/artifact-status", body, **kwargs)

    def register(self, run_id: str, body: dict, **kwargs):
        return self.post(f"/internal/v1/runs/{run_id}/artifacts", body, **kwargs)


def artifact_client(stack: Stack, role: str = "worker") -> RunArtifactClient:
    return RunArtifactClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


# ---------------------------------------------------------------------------
# real upload + finalize of the sales CSV through T028
# ---------------------------------------------------------------------------
def upload_artifact(
    stack: Stack,
    session: Session,
    content: bytes,
    *,
    purpose: str = "task_input",
    media_type: str = "text/csv",
) -> str:
    """Upload bytes through the public T028 path and finalize them for real."""
    ticket_response = t028.create_upload(
        session, media_type=media_type, size_bytes=len(content), purpose=purpose
    )
    assert ticket_response.status == 201, ticket_response.text
    ticket = ticket_response.json
    put = t024.put_link(ticket["upload_url"], content)
    assert put.status in (200, 204), put.status
    sha = hashlib.sha256(content).hexdigest()
    completed = t028.complete_upload(session, ticket["artifact_id"], sha)
    assert completed.status == 202, completed.text
    t028.finalize_once(stack)
    t028.wait_for_artifact_state(session, ticket["artifact_id"], {"ready"})
    assert t028.artifact_row(stack.dbname, ticket["artifact_id"])["state"] == "ready"
    return ticket["artifact_id"]


def debug_run_with_input(
    stack: Stack,
    owner,
    artifact_id: str,
    *,
    input_value: dict | None = None,
):
    """Submit one debug run whose inputs include the uploaded CSV artifact."""
    revision = draft_revision(owner.creator, owner.application_id)
    response = submit_debug(
        owner.creator,
        owner.application_id,
        draft_revision=revision,
        input_value=input_value or {"file": "input.csv", "media_type": "text/csv"},
        artifact_ids=[artifact_id],
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run


def claimed_with_sales_csv(
    stack: Stack,
    *,
    content: bytes = SALES_CSV,
    execution_unit_id: str = "t033-e2e",
) -> tuple[object, dict, str]:
    """A golden owner, one claimed lease and the uploaded CSV artifact id."""
    owner = golden_owner(stack)
    artifact_id = upload_artifact(stack, owner.creator, content)
    lease = claim_run_for_owner(
        stack, owner, execution_unit_id=execution_unit_id, artifact_id=artifact_id
    )
    return owner, lease, artifact_id


def claim_run_for_owner(
    stack: Stack,
    owner,
    *,
    execution_unit_id: str = "t033-unit",
    artifact_id: str | None = None,
    content: bytes = SALES_CSV,
) -> dict:
    """Upload one CSV (unless given) and claim one new debug run of the owner."""
    artifact = artifact_id or upload_artifact(stack, owner.creator, content)
    run = debug_run_with_input(stack, owner, artifact)
    response = execution_client(stack, "supervisor").claim_job(
        worker_id="t033-supervisor",
        execution_unit_id=execution_unit_id,
        supported_compilers=[t031.COMPILER_VERSION],
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


def upload_run_artifact(
    stack: Stack,
    client: RunArtifactClient,
    lease: dict,
    *,
    content: bytes,
    purpose: str = "node_output",
    media_type: str = "application/json",
    relation: str | None = None,
) -> tuple[str, str]:
    """createWorkerUpload -> PUT -> completeWorkerUpload -> finalize -> register."""
    run_id, token = lease["run_id"], lease["attempt_token"]
    created = client.create_upload(
        run_id,
        {
            "attempt_token": token,
            "media_type": media_type,
            "size_bytes": len(content),
            "purpose": purpose,
        },
    )
    assert created.status == 201, created.text
    ticket = created.json
    put = put_link(ticket["upload_url"], content)
    assert put.status in (200, 204), put.status
    sha = hashlib.sha256(content).hexdigest()
    completed = client.complete(
        run_id, ticket["artifact_id"], {"attempt_token": token, "sha256": sha}
    )
    assert completed.status == 202, completed.text
    t028.finalize_once(stack)
    wait_scoped_status(client, run_id, token, ticket["artifact_id"], {"ready"})
    if relation is not None:
        registered = client.register(
            run_id,
            {"attempt_token": token, "artifact_id": ticket["artifact_id"], "relation": relation},
        )
        assert registered.status == 200, registered.text
    return ticket["artifact_id"], sha


def wait_scoped_status(
    client: RunArtifactClient,
    run_id: str,
    attempt_token: str,
    artifact_id: str,
    states: set[str],
    *,
    timeout: float = 30.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.status(
            run_id, {"attempt_token": attempt_token, "artifact_id": artifact_id}
        )
        if response.status == 200:
            last = response.json
            if last.get("state") in states:
                return last
        time.sleep(0.05)
    raise AssertionError(f"artifact {artifact_id} never reached {states}: {last}")


# ---------------------------------------------------------------------------
# WSL / runsc inspection (small, bounded calls)
# ---------------------------------------------------------------------------
def wsl(command: str, *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wsl", "-d", SANDBOX_DISTRO, "-u", "root", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def sandbox_available() -> bool:
    completed = wsl("runsc --version", timeout=60)
    return completed.returncode == 0 and "runsc version" in completed.stdout


def runsc_list() -> list[str]:
    completed = wsl("runsc --network=none list", timeout=60)
    assert completed.returncode == 0, completed.stderr
    return [line for line in completed.stdout.splitlines() if "ID" not in line and line.strip()]


def sandbox_state_root() -> list[str]:
    completed = wsl(f"ls -1 {SANDBOX_STATE_ROOT} 2>/dev/null || true", timeout=60)
    return [line for line in completed.stdout.splitlines() if line.strip()]


def no_orphan_evidence() -> dict:
    """The independent orphan check: runsc list and the per-run state root."""
    return {"runsc_list": runsc_list(), "state_root": sandbox_state_root()}


# ---------------------------------------------------------------------------
# public reads + database assertions
# ---------------------------------------------------------------------------
def list_run_artifacts(
    session: Session, run_id: str, *, cursor: str | None = None, limit: int | None = None
):
    query = []
    if cursor is not None:
        query.append("cursor=" + cursor)
    if limit is not None:
        query.append("limit=" + str(limit))
    suffix = ("?" + "&".join(query)) if query else ""
    return session.client.get(f"/api/v1/runs/{run_id}/artifacts{suffix}")


def artifact_download(session: Session, artifact_id: str):
    return session.client.get(f"/api/v1/artifacts/{artifact_id}/download")


def provenance_row(dbname: str, artifact_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, run_id::text AS run_id, "
        "attempt_token::text AS attempt_token, purpose, created_at::text AS created_at "
        "FROM platform.run_artifact_uploads "
        f"WHERE artifact_id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def provenance_rows_for_run(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, purpose, attempt_token::text AS attempt_token "
        "FROM platform.run_artifact_uploads "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY created_at, artifact_id",
    )


def run_artifact_links(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, relation, attempt_token::text AS attempt_token "
        "FROM platform.run_artifacts "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY relation, artifact_id",
    )


def artifact_by_purpose(dbname: str, run_id: str, purpose: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT a.id::text AS id, a.state, a.media_type, a.sha256, a.size_bytes, a.purpose "
        "FROM platform.artifacts AS a "
        "JOIN platform.run_artifact_uploads AS u ON u.artifact_id = a.id "
        f"WHERE u.run_id = {st.quote_literal(run_id)}::uuid "
        f"AND a.purpose = {st.quote_literal(purpose)} ORDER BY a.created_at, a.id",
    )
    return rows[0] if rows else None


ARTIFACT_FIELDS = {"id", "state", "media_type", "size_bytes"}


def assert_artifact_shape(body: dict) -> None:
    assert set(body) == ARTIFACT_FIELDS, body
    assert isinstance(body["id"], str)
    assert isinstance(body["state"], str)
    assert isinstance(body["media_type"], str)
    assert isinstance(body["size_bytes"], int) and body["size_bytes"] >= 0
