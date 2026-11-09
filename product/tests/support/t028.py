"""F05/T028 real-stack helpers: user uploads, admission and sealed reads.

The stack is the T024-shaped real one: throwaway PostgreSQL migrated with the
real ``cmd/admin migrate`` chain, the compiled ``cmd/api`` behind real HTTP with
a real object store and HMAC-signed object links, the compiled ``cmd/maintenance``
process that runs the real ``artifact_finalize`` handler, and the real internal
mTLS listener used only to claim a job deterministically in the delete/finalize
race test.

No endpoint is mocked: bytes move over the signed URLs, the handler runs in a
real process, and every assertion about state/admission/jobs is read from
PostgreSQL.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

from support.runtime_stack import Stack
from support import f00b, f02a, t024, t025
from support import stack as st

# Re-exports so a T028 test imports one module.
T028Stack = Stack
Session = f02a.Session
admin_session = f00b.admin_session
session_for = f00b.session_for
invite_and_activate = f00b.invite_and_activate
create_service_account = f02a.create_service_account
create_api_key = f02a.create_api_key
new_uuid = f00b.new_uuid
sql_utf8 = t025.sql_utf8
put_link = t024.put_link
get_link = t024.get_link
tamper_token = t024.tamper_token

KIND_ARTIFACT_FINALIZE = "artifact_finalize"
MAX_OBJECT_BYTES = 20 * 1024 * 1024

TEXT = "text/plain"
CSV = "text/csv"
JSON = "application/json"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SUPPORTED_MEDIA_TYPES = (TEXT, CSV, JSON, XLSX)
UPLOAD_PURPOSES = ("task_input", "tool_source", "knowledge", "evaluation_dataset")


# ---------------------------------------------------------------------------
# HTTP: the contract endpoints
# ---------------------------------------------------------------------------
def create_upload(
    session: Session,
    *,
    media_type: str = TEXT,
    size_bytes: int | None = None,
    purpose: str = "task_input",
    body: dict | None = None,
    csrf: bool = True,
    headers: dict[str, str] | None = None,
) -> st.Response:
    if body is None:
        if size_bytes is None:
            raise ValueError("size_bytes is required when body is not supplied")
        body = {"media_type": media_type, "size_bytes": size_bytes, "purpose": purpose}
    return session.client.post(
        "/api/v1/artifacts/uploads",
        body=body,
        csrf=session.csrf if csrf else None,
        headers=headers,
    )


def complete_upload(
    session: Session,
    artifact_id: str,
    sha256: str,
    *,
    body: dict | None = None,
    csrf: bool = True,
) -> st.Response:
    payload = {"sha256": sha256} if body is None else body
    return session.client.post(
        f"/api/v1/artifacts/{artifact_id}/complete",
        body=payload,
        csrf=session.csrf if csrf else None,
    )


def get_artifact(session: Session, artifact_id: str) -> st.Response:
    return session.client.get(f"/api/v1/artifacts/{artifact_id}")


def download_artifact(session: Session, artifact_id: str) -> st.Response:
    return session.client.get(f"/api/v1/artifacts/{artifact_id}/download")


def read_content(session: Session, artifact_id: str) -> st.Response:
    return session.client.get(f"/api/v1/artifacts/{artifact_id}/content")


def quarantine_reason(response: st.Response) -> str | None:
    return response.header("X-Artifact-Quarantine-Reason")


# ---------------------------------------------------------------------------
# media type allowlist constants used by refusal tests
# ---------------------------------------------------------------------------
def unsupported_media_types() -> tuple[str, ...]:
    return ("application/pdf", "text/html", "application/zip", "image/png", "text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# bytes: deterministic fixtures for the admission cases
# ---------------------------------------------------------------------------
def sha256_hex(content: bytes | str) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def xlsx_bytes(entries: dict[str, bytes] | None = None) -> bytes:
    """A real zip archive, written with the stdlib zip writer."""
    if entries is None:
        entries = {
            "[Content_Types].xml": b'<?xml version="1.0"?><Types/>',
            "xl/workbook.xml": b'<?xml version="1.0"?><workbook/>',
            "xl/worksheets/sheet1.xml": b'<?xml version="1.0"?><worksheet/>',
        }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def macro_xlsx_bytes() -> bytes:
    return xlsx_bytes(
        {
            "[Content_Types].xml": b'<?xml version="1.0"?><Types/>',
            "xl/workbook.xml": b'<?xml version="1.0"?><workbook/>',
            "xl/vbaProject.bin": b"macro bytes",
        }
    )


def oversized_content() -> bytes:
    """One byte over the platform's single-object ceiling."""
    return b"x" * (MAX_OBJECT_BYTES + 1)


# ---------------------------------------------------------------------------
# maintenance: the real finalize handler
# ---------------------------------------------------------------------------
def run_finalize(stack: T028Stack) -> tuple[object, list[dict]]:
    return f00b.maintenance_once(stack, "--kinds", KIND_ARTIFACT_FINALIZE)


def finalize_once(stack: T028Stack) -> list[dict]:
    completed, reports = run_finalize(stack)
    assert f00b.summary_of(reports)["failed"] + f00b.summary_of(reports)["succeeded"] >= 1, reports
    return reports


# ---------------------------------------------------------------------------
# read models
# ---------------------------------------------------------------------------
def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, staging_object_key, "
        "expected_sha256, sha256, size_bytes, media_type, purpose, state, revision, "
        "deleted_at::text AS deleted_at, created_at::text AS created_at "
        "FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def admission_row(dbname: str, artifact_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, declared_media_type, sniffed_media_type, "
        "detected_format, verdict, reason, checked_at::text AS checked_at "
        "FROM platform.artifact_admissions "
        f"WHERE artifact_id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def artifact_jobs(dbname: str, artifact_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, error_code, failure_class, attempt_count, "
        "expected_revision, expected_sha256, input_artifact_id::text AS input_artifact_id, "
        "dedup_key, finished_at::text AS finished_at "
        "FROM platform.background_jobs "
        f"WHERE target_id = {st.quote_literal(artifact_id)}::uuid AND kind = 'artifact_finalize' "
        "ORDER BY created_at, id",
    )


def object_keys(object_dir: Path, prefix: str) -> set[str]:
    return f00b.object_keys(object_dir, prefix)


def write_object(object_dir: Path, key: str, content: bytes) -> Path:
    return f00b.write_object(object_dir, key, content)


def attempt_sealed_key(job_id: str, attempt_token: str) -> str:
    return f00b.attempt_sealed_key(job_id, attempt_token)


def tombstone_artifact(dbname: str, artifact_id: str) -> None:
    """Write the F07 tombstone columns the way the deletion owner will.

    The DELETE endpoint and deletion jobs belong to F07; this helper only sets
    the columns F07's tombstone propagation is specified to set (deleted_at plus
    state=deleted, revision bumped), so the finalize guard's race behaviour is
    tested against real rows without implementing F07.
    """
    st.sql(
        dbname,
        "UPDATE platform.artifacts "
        "SET deleted_at = now(), state = 'deleted', revision = revision + 1 "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid AND deleted_at IS NULL",
    )


def seed_user_artifact(
    dbname: str,
    object_dir: Path,
    owner_id: str,
    *,
    media_type: str = TEXT,
    purpose: str = "task_input",
    content: bytes = b"seed bytes\n",
    state: str = "scanning",
    expected_sha256: str | None = None,
    size_bytes: int | None = None,
    staging: bool = True,
) -> f00b.SeededArtifact:
    """Insert a real user-upload-shaped artifact row for the adversarial cases.

    Used only where the public upload path intentionally refuses to create the
    state (for example a disallowed declared media type or an over-limit staging
    object). The row, its staging bytes and the finalize job are real.
    """
    return f00b.seed_artifact(
        dbname,
        object_dir,
        owner_id,
        content=content,
        state=state,
        expected_sha256=expected_sha256,
        size_bytes=size_bytes,
        staging=staging,
        media_type=media_type,
        purpose=purpose,
    )


def enqueue_finalize(stack: T028Stack, artifact: f00b.SeededArtifact) -> dict:
    owner = artifact_owner(stack.dbname, artifact.id)
    return f00b.enqueue_ok(
        stack,
        kind=KIND_ARTIFACT_FINALIZE,
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="t028-finalize:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
        input_artifact_id=artifact.id,
    )


def artifact_owner(dbname: str, artifact_id: str) -> str:
    return artifact_row(dbname, artifact_id)["owner_id"]


def job_count(dbname: str, *, kind: str = KIND_ARTIFACT_FINALIZE) -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.background_jobs WHERE kind = {st.quote_literal(kind)}"))


def count_rows(dbname: str, table: str, where: str = "TRUE") -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.{table} WHERE {where}"))


def job_row(dbname: str, job_id: str) -> dict:
    return f00b.job_row(dbname, job_id)


def object_bytes(object_dir: Path, object_key: str) -> bytes:
    return (object_dir / object_key).read_bytes()


def wait_for_artifact_state(
    session: Session,
    artifact_id: str,
    states: set[str],
    *,
    timeout: float = 20.0,
) -> dict:
    """Poll the public GET endpoint until the artifact reaches a state."""

    import time

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = get_artifact(session, artifact_id)
        if response.status == 200:
            last = response.json
            if last.get("state") in states:
                return last
        time.sleep(0.05)
    raise AssertionError(f"artifact {artifact_id} never reached {states}: {last}")


def assert_artifact_view(body: dict) -> None:
    """The contract Artifact schema is exactly four additionalProperties:false fields."""
    assert set(body) == {"id", "state", "media_type", "size_bytes"}, body
    assert isinstance(body["id"], str)
    assert isinstance(body["state"], str)
    assert isinstance(body["media_type"], str)
    assert isinstance(body["size_bytes"], int) and body["size_bytes"] >= 0


def assert_upload(body: dict) -> None:
    assert set(body) == {"artifact_id", "upload_url", "expires_at"}, body
    assert isinstance(body["artifact_id"], str)
    assert body["upload_url"].startswith("http://") or body["upload_url"].startswith("https://")
    assert "expires_at" in body


def assert_download(body: dict) -> None:
    assert set(body) == {"url", "expires_at"}, body
    assert body["url"].startswith("http://") or body["url"].startswith("https://")


def json_object(value) -> str:
    return json.dumps(value)
