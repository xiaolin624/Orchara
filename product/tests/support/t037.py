"""F05/T037 real-stack helpers: tool submit, sandboxed build, review and revoke.

The stack is T033's: a throwaway PostgreSQL database migrated with the real
``cmd/admin`` chain, the compiled ``cmd/api`` behind HTTP with the real internal
mTLS listener and filesystem object store, and the compiled ``cmd/maintenance``
binary. Submission/list/review/revoke are real HTTP; the build is a real
``tool_build`` job claimed by a real maintenance process and executed by the
checked-in ``deploy/sandbox/build_tool.py`` inside WSL2/runsc.

Only test fixtures are synthetic: the source zips and the expected schema/lock
documents. Every state assertion reads the real API response or PostgreSQL.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import time
import zipfile
from pathlib import Path

from support import f00b, t027, t028, t033
from support import stack as st

T037Stack = t033.T033Stack
Session = t033.Session
admin_session = t033.admin_session
session_for = t033.session_for
invite_and_activate = t033.invite_and_activate
new_uuid = t033.new_uuid
sql_utf8 = t033.sql_utf8
artifact_row = t033.artifact_row
artifact_content = t033.artifact_content
sandbox_available = t033.sandbox_available
rootfs_digest = t033.rootfs_digest
put_link = t033.put_link

PRODUCT_ROOT = st.PRODUCT_ROOT
SANDBOX_DISTRO = t033.SANDBOX_DISTRO
SANDBOX_ROOTFS = t033.SANDBOX_ROOTFS
SANDBOX_STATE_ROOT = t033.SANDBOX_STATE_ROOT

BUILD_KINDS = "tool_build,artifact_finalize"
MAX_SOURCE_BYTES = 20 * 1024 * 1024


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------
DEFAULT_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"region": {"type": "string"}},
    "required": ["region"],
    "additionalProperties": False,
}
DEFAULT_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"total": {"type": "string"}},
    "required": ["total"],
    "additionalProperties": False,
}
DEFAULT_LOCK = {"requirements": []}


def source_zip(entries: dict[str, bytes] | None = None) -> bytes:
    """A real zip source bundle written with the stdlib zip writer."""
    if entries is None:
        entries = {
            "tool/__init__.py": b"VALUE = 1\n",
            "tool/main.py": b"from . import VALUE\nprint(VALUE)\n",
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def setup_py_source(body: str) -> bytes:
    return source_zip({"setup.py": body.encode("utf-8"), "tool/main.py": b"VALUE = 2\n"})


def upload_source_on(stack: T037Stack, session: Session, content: bytes) -> str:
    """Upload and finalize one real tool_source zip through T028."""
    ticket_response = t028.create_upload(
        session, media_type="application/zip", size_bytes=len(content), purpose="tool_source"
    )
    assert ticket_response.status == 201, ticket_response.text
    ticket = ticket_response.json
    put = put_link(ticket["upload_url"], content)
    assert put.status in (200, 204), put.status
    sha = hashlib.sha256(content).hexdigest()
    completed = t028.complete_upload(session, ticket["artifact_id"], sha)
    assert completed.status == 202, completed.text
    t028.finalize_once(stack)
    t028.wait_for_artifact_state(session, ticket["artifact_id"], {"ready"})
    return ticket["artifact_id"]


def submit_body(
    source_artifact_id: str,
    *,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    dependency_lock: dict | None = None,
    risk_class: str = "pure",
    connector_version_ids: list[str] | None = None,
) -> dict:
    return {
        "source_artifact_id": source_artifact_id,
        "input_schema": DEFAULT_INPUT_SCHEMA if input_schema is None else input_schema,
        "output_schema": DEFAULT_OUTPUT_SCHEMA if output_schema is None else output_schema,
        "dependency_lock": DEFAULT_LOCK if dependency_lock is None else dependency_lock,
        "risk_class": risk_class,
        "connector_version_ids": [] if connector_version_ids is None else connector_version_ids,
    }


def submit_tool(session: Session, body: dict, *, csrf: bool = True) -> st.Response:
    return session.client.post(
        "/api/v1/tools", body=body, csrf=session.csrf if csrf else None
    )


def list_tools(
    session: Session, *, cursor: str | None = None, limit: int | None = None
) -> st.Response:
    query = []
    if cursor is not None:
        query.append("cursor=" + cursor)
    if limit is not None:
        query.append("limit=" + str(limit))
    suffix = ("?" + "&".join(query)) if query else ""
    return session.client.get("/api/v1/tools" + suffix)


def get_tool(session: Session, tool_id: str) -> st.Response:
    return session.client.get(f"/api/v1/tools/{tool_id}")


def get_build(session: Session, tool_id: str) -> st.Response:
    return session.client.get(f"/api/v1/tools/{tool_id}/build")


def new_idempotency_key() -> str:
    return "t037-" + new_uuid()


def review(
    session: Session,
    tool_id: str,
    *,
    decision: str,
    manifest_sha256: str,
    reason: str = "t037 review",
    key: str | None = None,
    csrf: bool = True,
) -> st.Response:
    return session.client.post(
        f"/api/v1/tools/{tool_id}/review",
        body={"decision": decision, "manifest_sha256": manifest_sha256, "reason": reason},
        csrf=session.csrf if csrf else None,
        headers={"Idempotency-Key": key or new_idempotency_key()},
    )


def revoke(
    session: Session,
    tool_id: str,
    *,
    reason: str = "t037 revoke",
    csrf: bool = True,
) -> st.Response:
    return session.client.post(
        f"/api/v1/tools/{tool_id}/revoke",
        body={"reason": reason},
        csrf=session.csrf if csrf else None,
    )


# ---------------------------------------------------------------------------
# build process (real maintenance + real runsc)
# ---------------------------------------------------------------------------
def build_env(stack: T037Stack, *, timeout: str = "5m") -> dict[str, str]:
    env = f00b.maintenance_env(stack, heartbeat_interval="500ms", retry_backoff="100ms")
    env.update(
        {
            "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            "ORCHARA_WSL_DISTRO": SANDBOX_DISTRO,
            "ORCHARA_SANDBOX_PYTHON_ROOTFS": SANDBOX_ROOTFS,
            "ORCHARA_SANDBOX_ROOTFS_DIGEST": rootfs_digest(),
            "ORCHARA_SANDBOX_STATE_ROOT": SANDBOX_STATE_ROOT,
            "ORCHARA_SANDBOX_TIMEOUT": timeout,
            "ORCHARA_MAINTENANCE_TOOL_BUILD_TIMEOUT": "5m",
        }
    )
    return env


def build_args() -> list[str]:
    return [
        "run",
        "--once",
        "--kinds",
        BUILD_KINDS,
        "--system-io-slots",
        "1",
        "--work-slots",
        "1",
        "--tool-build-timeout",
        "5m",
    ]


def run_build_once(
    stack: T037Stack, *, env: dict[str, str] | None = None, timeout: int = 600
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    completed = subprocess.run(
        [str(stack.binaries["maintenance"]), *build_args()],
        cwd=PRODUCT_ROOT,
        env=env or build_env(stack),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    return completed, f00b.parse_json_lines(completed.stdout)


def start_build(
    stack: T037Stack, *, env: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(stack.binaries["maintenance"]), *build_args()],
        cwd=PRODUCT_ROOT,
        env=env or build_env(stack),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
    )


def finish_build(process: subprocess.Popen[str], *, timeout: int = 600) -> list[dict]:
    stdout, stderr = process.communicate(timeout=timeout)
    process.t037_stderr = stderr  # type: ignore[attr-defined]
    assert process.returncode == 0, (process.returncode, stdout, stderr)
    return f00b.parse_json_lines(stdout)


def submit_and_build(
    stack: T037Stack,
    session: Session,
    source: bytes,
    *,
    body: dict | None = None,
    env: dict[str, str] | None = None,
) -> tuple[dict, dict, list[dict]]:
    """Upload + submit + one real build. Returns (tool, build facts, reports)."""
    artifact_id = upload_source_on(stack, session, source)
    response = submit_tool(session, body or submit_body(artifact_id))
    assert response.status == 202, response.text
    tool = response.json
    completed, reports = run_build_once(stack, env=env)
    assert completed.returncode == 0, (completed.returncode, completed.stdout, completed.stderr)
    facts = wait_build_finished(stack, session, tool["id"])
    return tool, facts, reports


# ---------------------------------------------------------------------------
# SQL read models / polling
# ---------------------------------------------------------------------------
TOOL_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, state, revision, source_artifact_id::text AS "
    "source_artifact_id, source_sha256, dependency_lock_sha256, manifest_sha256, image_digest, "
    "build_log_artifact_id::text AS build_log_artifact_id, "
    "sbom_artifact_id::text AS sbom_artifact_id, build_bundle_digest, build_error_code, "
    "build_started_at::text AS build_started_at, build_finished_at::text AS build_finished_at, "
    "approved_by::text AS approved_by, approved_at::text AS approved_at, "
    "reviewed_by::text AS reviewed_by, "
    "reviewed_at::text AS reviewed_at, review_decision, review_reason, "
    "revoked_at::text AS revoked_at, revoked_by::text AS revoked_by, revoke_reason, "
    "connector_versions::text AS connector_versions, input_schema::text AS input_schema, "
    "output_schema::text AS output_schema, dependency_lock::text AS dependency_lock, "
    "risk_class, created_at::text AS created_at"
)


def tool_row(dbname: str, tool_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT " + TOOL_COLUMNS + " FROM platform.tool_versions "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def tool_jobs(dbname: str, tool_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, state, error_code, attempt_count, dedup_key, "
        "expected_revision, expected_sha256, input_artifact_id::text AS input_artifact_id, "
        "result_artifact_id::text AS result_artifact_id, finished_at::text AS finished_at "
        "FROM platform.background_jobs "
        f"WHERE kind = 'tool_build' AND target_id = {st.quote_literal(tool_id)}::uuid "
        "ORDER BY created_at, id",
    )


def build_artifacts(dbname: str, job_id: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT l.purpose, a.id::text AS id, a.state, a.media_type, a.size_bytes, a.sha256 "
        "FROM platform.background_job_artifacts AS l "
        "JOIN platform.artifacts AS a ON a.id = l.artifact_id "
        f"WHERE l.background_job_id = {st.quote_literal(job_id)}::uuid "
        "ORDER BY l.created_at, a.id",
    )
    grouped: dict[str, dict] = {}
    for row in rows:
        grouped.setdefault(row["purpose"], row)
    return grouped


def wait_until(predicate, *, timeout: float = 180.0, interval: float = 0.1):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition never became true (last={last!r})")


def wait_tool_state(
    dbname: str, tool_id: str, states: set[str], *, timeout: float = 300.0
) -> dict:
    def check():
        row = tool_row(dbname, tool_id)
        return row if row["state"] in states else None

    return wait_until(check, timeout=timeout)


def wait_build_finished(
    stack: T037Stack, session: Session, tool_id: str, *, timeout: float = 300.0
) -> dict:
    row = wait_tool_state(stack.dbname, tool_id, {"ready", "failed"}, timeout=timeout)
    response = get_build(session, tool_id)
    assert response.status == 200, response.text
    return response.json


def wait_job_state(
    dbname: str, tool_id: str, states: set[str], *, timeout: float = 300.0
) -> dict:
    def check():
        jobs = tool_jobs(dbname, tool_id)
        if jobs and jobs[0]["state"] in states:
            return jobs[0]
        return None

    return wait_until(check, timeout=timeout)


def job_artifact_owner(dbname: str, artifact_id: str) -> str:
    return artifact_row(dbname, artifact_id)["owner_id"]


# ---------------------------------------------------------------------------
# independent bundle-digest oracle
# ---------------------------------------------------------------------------
def canonical_json_bytes(value) -> bytes:
    """RFC 8785 JCS for the simple ASCII documents used by these tests.

    The fixtures contain only strings/arrays/empty objects, for which compact
    sorted-key JSON is exactly the RFC 8785 form. A float or escaped non-ASCII
    key would make this oracle insufficient; the implementation canonicalises
    with internal/jcs and the report states the rule.
    """
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def bundle_digest(source: bytes, dependency_lock: dict, sbom: bytes) -> str:
    """The documented content bundle digest, computed independently of Go.

    SHA-256 over the lexicographically sorted source/lock/SBOM bytes. It is a
    content bundle identity, not an OCI image digest.
    """
    parts = sorted([source, canonical_json_bytes(dependency_lock), sbom])
    return "sha256:" + hashlib.sha256(b"".join(parts)).hexdigest()


def download_artifact_bytes(session: Session, artifact_id: str) -> bytes:
    response = artifact_content(session, artifact_id)
    assert response.status == 200, (response.status, response.text)
    return response.raw


# ---------------------------------------------------------------------------
# drafts referencing a built tool (T025 shared vs private-debug rules)
# ---------------------------------------------------------------------------
def application_with_tool_definition(session: Session, tool_id: str) -> str:
    return t027.application_with_definition(session, t027.tool_definition(tool_id))


def validate_application(session: Session, application_id: str) -> st.Response:
    return session.client.post(
        f"/api/v1/applications/{application_id}/validate", csrf=session.csrf
    )


def submit_debug_run(session: Session, application_id: str) -> st.Response:
    return t027.submit_debug(
        session,
        application_id,
        draft_revision=t027.draft_revision(session, application_id),
        input_value={},
    )
