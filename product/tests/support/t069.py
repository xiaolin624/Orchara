"""F11/T069 real-stack helpers: the shared deletion foundation.

Everything here is real: the throwaway PostgreSQL 16 database migrated by the
compiled ``cmd/admin``, the compiled ``cmd/api`` behind HTTP, real sealed bytes in
a filesystem object store, the real internal mTLS listener and the compiled
maintenance process. The helpers only drive those surfaces and read the
authoritative rows back; no deletion state is faked.

The deletion-job vocabulary is mirrored from ``internal/deletion``:

* resource types: ``artifact``, ``memory``, ``knowledge_document``,
  ``conversation`` (T074 owns the last one's authority);
* scopes (the closed vocabulary in the contract's ``DeletionJob``):
  ``object_store``, ``search_index``, ``retrieval_cache``, ``checkpoints``,
  ``sandbox_temp``, ``third_party_history``, ``backup_catalog``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from urllib.parse import quote

from support import f00b, t024, t028, t041, t055, t058
from support import stack as st

# The contract's DeletionAccepted / DeletionJob field sets.
DELETION_ACCEPTED_FIELDS = {"id", "state", "resource_id"}
DELETION_JOB_FIELDS = {"id", "state", "remaining_scopes"}
DELETION_STATES = ("pending", "running", "completed", "failed")

RESOURCE_ARTIFACT = "artifact"
RESOURCE_MEMORY = "memory"
RESOURCE_DOCUMENT = "knowledge_document"
RESOURCE_CONVERSATION = "conversation"

SCOPES = (
    "backup_catalog",
    "checkpoints",
    "object_store",
    "retrieval_cache",
    "sandbox_temp",
    "search_index",
    "third_party_history",
)
# T069 cannot prove any class of derived copy gone, so every fresh job reports
# all scopes outstanding; T071 (first three) and T072 (last four) complete them.
T071_SCOPES = ("object_store", "search_index", "retrieval_cache")
T072_SCOPES = ("checkpoints", "sandbox_temp", "third_party_history", "backup_catalog")

ERROR_RESOURCE_NOT_FOUND = "resource_not_found"
ERROR_RESOURCE_IN_USE = "resource_in_use"
ERROR_PERSONAL_SCOPE = "personal_scope_forbidden"
ERROR_VALIDATION = "validation_failed"

_PATHS = {
    RESOURCE_ARTIFACT: "/api/v1/artifacts/",
    RESOURCE_MEMORY: "/api/v1/memories/",
    RESOURCE_DOCUMENT: "/api/v1/knowledge-documents/",
    RESOURCE_CONVERSATION: "/api/v1/conversations/",
}


# ---------------------------------------------------------------------------
# HTTP contract paths
# ---------------------------------------------------------------------------
def resource_path(resource_type: str, resource_id: str) -> str:
    return _PATHS[resource_type] + quote(str(resource_id), safe="")


def delete_resource(
    session,
    resource_type: str,
    resource_id: str,
    *,
    client=None,
    csrf: str | None = None,
):
    target = client or session.client
    return target.request(
        "DELETE",
        resource_path(resource_type, resource_id),
        csrf=session.csrf if csrf is None else csrf,
    )


def deletion_path(deletion_id: str) -> str:
    return "/api/v1/deletions/" + quote(str(deletion_id), safe="")


def get_deletion(session, deletion_id: str, *, client=None):
    target = client or session.client
    return target.get(deletion_path(deletion_id))


def deletion_of(response) -> dict:
    assert response.status == 202, response.text
    body = response.json
    assert set(body) == DELETION_ACCEPTED_FIELDS, body
    assert body["state"] in DELETION_STATES, body
    assert uuid.UUID(body["id"]) and uuid.UUID(body["resource_id"])
    return body


# ---------------------------------------------------------------------------
# setup: real owners and ready artifacts
# ---------------------------------------------------------------------------
def owner(stack, *, login: str | None = None):
    """A real creator with a real golden draft (the T029/T041 owner)."""
    return t041.owner(stack, login=login)


def other_owner(stack):
    return t041.secondary_owner(stack)


def upload_ready_artifact(
    stack,
    session,
    content: bytes | str,
    *,
    purpose: str = "task_input",
    media_type: str = t028.TEXT,
    finalize: bool = True,
) -> str:
    """One real upload -> PUT -> complete -> artifact_finalize -> ready artifact."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    reserved = t028.create_upload(
        session, media_type=media_type, size_bytes=len(data), purpose=purpose
    )
    assert reserved.status == 201, reserved.text
    ticket = reserved.json
    uploaded = t024.put_link(ticket["upload_url"], data)
    assert uploaded.status == 204, uploaded.status
    completed = t028.complete_upload(
        session, ticket["artifact_id"], hashlib.sha256(data).hexdigest()
    )
    assert completed.status == 202, completed.text
    artifact_id = ticket["artifact_id"]
    if finalize:
        t028.finalize_once(stack)
        view = t028.wait_for_artifact_state(session, artifact_id, {"ready"})
        assert view["state"] == "ready", view
    return artifact_id


def create_memory(stack, session, application_id: str, text: str) -> dict:
    return t058.create_memory_ok(session, application_id, text)


def application_for(session, *, name: str = "t069 deletion") -> str:
    """One real application owned by the session's creator."""
    from support import t027

    return t027.application_with_definition(
        session, t027.chain_definition([]), name=name
    )


def ready_document(stack, session, content: bytes | str, *, log_path: Path):
    """Create a knowledge base, upload/finalize the source and ingest to ready.

    The maintenance process runs for the block (both pools), exactly like the F10
    suites; the returned ``(base_id, document_id)`` are read back from PostgreSQL.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    base = t055.create_knowledge_base(session, "t069 knowledge")
    assert base.status == 201, base.text
    base_id = base.json["id"]
    source = t055.upload_text_artifact(session, data)
    t055.finalize_once(stack)
    artifact = t055.wait_for_artifact_state(session, source, {"ready"})
    assert artifact["state"] == "ready", artifact
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(session, base_id, source)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(session, document_id, {"ready", "failed"})
        assert view["state"] == "ready", view
    return base_id, document_id


# ---------------------------------------------------------------------------
# authoritative rows
# ---------------------------------------------------------------------------
_JOB_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, resource_type, "
    "resource_id::text AS resource_id, deletion_revision, state, "
    "remaining_scopes::text AS remaining_scopes, "
    "scope_details::text AS scope_details, completed_scopes::text AS completed_scopes, "
    "attempt_count, error_code, started_at::text AS started_at, "
    "updated_at::text AS updated_at, created_at::text AS created_at, "
    "completed_at::text AS completed_at"
)


def deletion_job(dbname: str, deletion_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT " + _JOB_COLUMNS + " FROM platform.deletion_jobs "
        f"WHERE id = {st.quote_literal(deletion_id)}::uuid",
    )
    return rows[0] if rows else None


def deletion_jobs(
    dbname: str, *, resource_type: str | None = None, resource_id: str | None = None
) -> list[dict]:
    clauses = ["TRUE"]
    if resource_type is not None:
        clauses.append("resource_type = " + st.quote_literal(resource_type))
    if resource_id is not None:
        clauses.append("resource_id = " + st.quote_literal(resource_id) + "::uuid")
    return st.sql_rows(
        dbname,
        "SELECT " + _JOB_COLUMNS + " FROM platform.deletion_jobs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at, id",
    )


def remaining_scopes(job: dict) -> list[str]:
    value = job.get("remaining_scopes")
    return json.loads(value) if isinstance(value, str) else list(value or [])


def scope_details(job: dict) -> dict:
    value = job.get("scope_details")
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def completed_scopes(job: dict) -> list[str]:
    value = job.get("completed_scopes")
    return json.loads(value) if isinstance(value, str) else list(value or [])


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, "
        "staging_object_key, expected_sha256, sha256, media_type, size_bytes, "
        "state, revision, deleted_at::text AS deleted_at, purged_at::text AS purged_at "
        "FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def memory_row(dbname: str, memory_id: str) -> dict | None:
    return t058.row_of_memory(dbname, memory_id)


def document_row(dbname: str, document_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, knowledge_base_id::text AS knowledge_base_id, "
        "source_artifact_id::text AS source_artifact_id, revision, state, "
        "deleted_at::text AS deleted_at, active_index_id::text AS active_index_id "
        "FROM platform.knowledge_documents "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    return rows[0] if rows else None


def run_rows(dbname: str, run_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, state, revision, "
        "cancel_requested_at::text AS cancel_requested_at, "
        "deleted_at::text AS deleted_at, error_code, "
        "input_artifact_id::text AS input_artifact_id, "
        "execution_snapshot_id::text AS execution_snapshot_id "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    return rows[0] if rows else None


def run_snapshot_manifest_artifact(dbname: str, run_id: str) -> str:
    """The frozen execution-manifest artifact a real run references."""
    rows = st.sql_rows(
        dbname,
        "SELECT s.manifest_artifact_id::text AS manifest_artifact_id "
        "FROM platform.runs AS r "
        "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
        f"WHERE r.id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["manifest_artifact_id"]


def run_artifact_list(session, run_id: str):
    return session.client.get(
        "/api/v1/runs/" + quote(str(run_id), safe="") + "/artifacts"
    )


def run_events(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT seq, type, payload::text AS payload FROM platform.run_events "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY seq",
    )


def audit_events(dbname: str, action: str, *, resource_id: str | None = None) -> list[dict]:
    clause = ""
    if resource_id is not None:
        clause = " AND resource_id = " + st.quote_literal(resource_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, action, actor_id::text AS actor_id, resource_type, "
        "resource_id::text AS resource_id, metadata::text AS metadata "
        "FROM platform.audit_events WHERE action = "
        + st.quote_literal(action)
        + clause
        + " ORDER BY created_at, id",
    )


def seeded_artifact(stack, owner_id: str, content: bytes, **kwargs):
    """A real ready artifact row (f00b helper) for a state the public path refuses."""
    return f00b.seed_artifact(
        stack.dbname, stack.object_dir, owner_id, content=content,
        state="ready", sealed_content=content, **kwargs
    )
