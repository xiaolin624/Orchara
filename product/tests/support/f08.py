"""F08/T074 real-stack helpers: conversations, requests, the queue and published versions.

Everything here drives the real platform: the throwaway PostgreSQL 16 database
migrated by the compiled ``cmd/admin`` binary, the compiled ``cmd/api`` behind
real cookie+CSRF HTTP, the real filesystem object store with sealed bytes and the
compiled maintenance process for artifact finalization.

The helpers only create real rows through the public API or through the exact row
shapes the publication transaction writes. They never fake a queue state: every
assertion about a request/slot/run is read back from PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from support import f00b, t025, t027, t068, t069
from support import stack as st

CONVERSATIONS = "/api/v1/conversations"
REQUESTS = "/api/v1/requests"

CONVERSATION_FIELDS = {"id", "created_at", "revision"}
REQUEST_FIELDS = {"id", "state", "run_id", "interaction", "error_code"}
REQUEST_STATES = (
    "queued",
    "routing",
    "awaiting_input",
    "awaiting_selection",
    "dispatched",
    "cancelled",
    "failed",
)
MESSAGE_FIELDS = {
    "id",
    "role",
    "request_id",
    "run_id",
    "body_artifact_id",
    "created_at",
    "kind",
}
ACCEPTED_FIELDS = {"id", "state"}

TERMINAL_RUN_STATES = ("succeeded", "failed", "cancelled")

ERROR_RESOURCE_NOT_FOUND = "resource_not_found"
ERROR_VALIDATION = "validation_failed"
ERROR_IDEMPOTENCY_CONFLICT = "idempotency_conflict"
ERROR_VERSION_REVOKED = "version_revoked"
ERROR_STATE_CONFLICT = "state_conflict"


# ---------------------------------------------------------------------------
# identities
# ---------------------------------------------------------------------------
@dataclass
class Account:
    login: str
    password: str
    session: f00b.Session

    @property
    def principal_id(self) -> str:
        return self.session.principal_id


def account(stack, *, login: str | None = None) -> Account:
    """A real creator account (the invite+activate path) usable for a second session."""
    admin = f00b.admin_session(stack)
    login = login or ("t074-owner-" + f00b.new_uuid()[:8])
    password = "t074-pass-" + f00b.new_uuid()[:12]
    session = f00b.invite_and_activate(stack, admin, login, roles=("creator",), password=password)
    return Account(login=login, password=password, session=session)


def second_session(stack, account_: Account) -> f00b.Session:
    """A second real login for the same principal (the concurrent-submit race)."""
    return t027.session_for(stack, account_.login, account_.password)


# ---------------------------------------------------------------------------
# conversation endpoints
# ---------------------------------------------------------------------------
def conversation_path(conversation_id: str) -> str:
    return CONVERSATIONS + "/" + quote(str(conversation_id), safe="")


def messages_path(conversation_id: str) -> str:
    return conversation_path(conversation_id) + "/messages"


def create_conversation(session):
    return session.client.post(CONVERSATIONS, csrf=session.csrf)


def conversation(session) -> str:
    response = create_conversation(session)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == ACCEPTED_FIELDS, body
    assert body["state"]
    uuid.UUID(body["id"])
    return body["id"]


def list_conversations(session, *, cursor: str | None = None, limit: int | None = None):
    query = []
    if cursor is not None:
        query.append("cursor=" + quote(cursor, safe=""))
    if limit is not None:
        query.append("limit=" + str(limit))
    path = CONVERSATIONS + ("?" + "&".join(query) if query else "")
    return session.client.get(path)


def get_conversation(session, conversation_id: str):
    return session.client.get(conversation_path(conversation_id))


def list_messages(session, conversation_id: str, *, cursor: str | None = None, limit=None):
    query = []
    if cursor is not None:
        query.append("cursor=" + quote(cursor, safe=""))
    if limit is not None:
        query.append("limit=" + str(limit))
    path = messages_path(conversation_id) + ("?" + "&".join(query) if query else "")
    return session.client.get(path)


# ---------------------------------------------------------------------------
# request endpoints
# ---------------------------------------------------------------------------
def request_path(request_id: str) -> str:
    return REQUESTS + "/" + quote(str(request_id), safe="")


def cancel_path(request_id: str) -> str:
    return request_path(request_id) + "/cancel"


def submit_request(
    session,
    conversation_id: str,
    message: str,
    *,
    artifact_ids: list[str] | None = None,
    version_id: str | None = None,
    handoff: dict | None = None,
    key: str | None = None,
    omit_key: bool = False,
    body: dict | None = None,
    headers: dict[str, str] | None = None,
):
    payload = body if body is not None else {
        "conversation_id": conversation_id,
        "message": message,
        "artifact_ids": list(artifact_ids or []),
    }
    if body is None and version_id is not None:
        payload["application_version_id"] = version_id
    if body is None and handoff is not None:
        payload["handoff"] = handoff
    request_headers = dict(headers or {})
    if not omit_key:
        request_headers["Idempotency-Key"] = key or f00b.new_uuid()
    return session.client.post(
        REQUESTS, body=payload, csrf=session.csrf, headers=request_headers
    )


def request_of(response) -> dict:
    assert response.status == 202, response.text
    body = response.json
    assert set(body) == REQUEST_FIELDS, body
    assert body["state"] in REQUEST_STATES, body
    uuid.UUID(body["id"])
    return body


def get_request(session, request_id: str):
    return session.client.get(request_path(request_id))


def cancel_request(session, request_id: str):
    return session.client.post(cancel_path(request_id), csrf=session.csrf)


def cancel_accepted(response) -> dict:
    assert response.status == 202, response.text
    body = response.json
    assert set(body) == ACCEPTED_FIELDS, body
    uuid.UUID(body["id"])
    return body


# ---------------------------------------------------------------------------
# publication: a real compiled manifest published as an immutable version
# ---------------------------------------------------------------------------
@dataclass
class PublishedVersion:
    application_id: str
    version_id: str
    version_no: int
    manifest_sha256: str
    manifest_artifact_id: str
    manifest: dict


def publish_version(
    stack,
    session,
    *,
    name: str = "t074 published",
    definition: dict | None = None,
) -> PublishedVersion:
    """Publish one real compiled draft as an immutable version.

    The manifest is produced by the real compiler (``POST .../validate``) and read
    back from the sealed object store; the review/version rows are the exact rows
    T068's approval transaction writes (an approved review bound to a report and
    the sealed manifest artifact), so ``catalog.ResolveRunnableVersion`` sees a
    normal published version. The heavy five-case evaluation evidence flow is
    T068's own suite, not repeated here.
    """
    definition = definition if definition is not None else t027.chain_definition([])
    application_id = t027.application_with_definition(session, definition, name=name)
    validated = t025.validate(session, application_id)
    assert validated.status == 200, validated.text
    manifest_sha = validated.header("X-Manifest-SHA256")
    assert manifest_sha and len(manifest_sha) == 64, manifest_sha
    rows = t025.manifest_artifacts(stack.dbname, application_id)
    assert rows, "validate sealed no manifest artifact"
    row = rows[-1]
    assert row["manifest_sha256"] == manifest_sha, row
    manifest_bytes = t025.manifest_artifact_bytes(stack.object_dir, row)
    assert hashlib.sha256(manifest_bytes).hexdigest() == manifest_sha
    manifest = json.loads(manifest_bytes)
    capability_card = manifest["capability_card"]

    version_id = f00b.new_uuid()
    review_id = f00b.new_uuid()
    report_id = f00b.new_uuid()
    report_artifact = t025.insert_artifact(
        stack.dbname, session.principal_id, purpose="report", sha256=None
    )
    dataset_sha = hashlib.sha256(("dataset-" + report_id).encode()).hexdigest()
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{lit(report_id)}::uuid, {lit(manifest_sha)}, {lit(dataset_sha)}, "
        f"{lit(report_artifact)}::uuid, 'passed')",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.publication_reviews "
        "(id, application_id, submitted_by, snapshot, manifest_sha256, report_id, "
        " state, reviewer_id, decided_at, reason, manifest_artifact_id) VALUES ("
        f"{lit(review_id)}::uuid, {lit(application_id)}::uuid, "
        f"{lit(session.principal_id)}::uuid, {jsonb(manifest)}, {lit(manifest_sha)}, "
        f"{lit(report_id)}::uuid, 'approved', {lit(session.principal_id)}::uuid, now(), "
        f"'t074 published fixture', {lit(row['artifact_id'])}::uuid)",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.application_versions "
        "(id, application_id, version_no, review_id, manifest, manifest_sha256, "
        " manifest_artifact_id, capability_card, compiler_version, worker_image_digest, "
        " state_schema_version) VALUES ("
        f"{lit(version_id)}::uuid, {lit(application_id)}::uuid, 1, "
        f"{lit(review_id)}::uuid, {jsonb(manifest)}, {lit(manifest_sha)}, "
        f"{lit(row['artifact_id'])}::uuid, {jsonb(capability_card)}, "
        f"{lit(manifest['compiler_version'])}, {lit(manifest['worker_image_digest'])}, "
        f"{lit(manifest['state_schema_version'])})",
    )
    t025.sql_utf8(
        stack.dbname,
        "UPDATE platform.publication_reviews SET version_id = "
        f"{lit(version_id)}::uuid WHERE id = {lit(review_id)}::uuid",
    )
    t025.sql_utf8(
        stack.dbname,
        "UPDATE platform.applications SET active_version_id = "
        f"{lit(version_id)}::uuid WHERE id = {lit(application_id)}::uuid",
    )
    return PublishedVersion(
        application_id=application_id,
        version_id=version_id,
        version_no=1,
        manifest_sha256=manifest_sha,
        manifest_artifact_id=row["artifact_id"],
        manifest=manifest,
    )


def revoke_version(dbname: str, version_id: str, actor_id: str, *, reason: str = "t074 revoked"):
    t025.sql_utf8(
        dbname,
        "UPDATE platform.application_versions SET revoked_at = now(), "
        f"revoked_by = {lit(actor_id)}::uuid, revoke_reason = {lit(reason)} "
        f"WHERE id = {lit(version_id)}::uuid",
    )


def public_attachment(stack, session, content: bytes | str, *, purpose: str = "task_input") -> str:
    """One real upload -> complete -> artifact_finalize -> ready artifact."""
    return t069.upload_ready_artifact(stack, session, content, purpose=purpose)


# ---------------------------------------------------------------------------
# authoritative rows
# ---------------------------------------------------------------------------
def lit(value: str) -> str:
    return st.quote_literal(value)


def jsonb(value) -> str:
    return lit(json.dumps(value, ensure_ascii=False)) + "::jsonb"


_CONVERSATION_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, revision, "
    "created_at::text AS created_at, deleted_at::text AS deleted_at"
)

_REQUEST_COLUMNS = (
    "r.id::text AS id, r.conversation_id::text AS conversation_id, "
    "r.owner_id::text AS owner_id, r.input_artifact_id::text AS input_artifact_id, "
    "r.state, r.revision, r.error_code, "
    "r.requested_version_id::text AS requested_version_id, "
    "r.control_calls, r.created_at::text AS created_at, "
    "u.id::text AS run_id, u.state AS run_state, "
    "u.application_version_id::text AS run_version_id"
)

_REQUEST_FROM = (
    "platform.requests AS r "
    "LEFT JOIN platform.runs AS u ON u.request_id = r.id AND u.deleted_at IS NULL"
)


def conversation_row(dbname: str, conversation_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT " + _CONVERSATION_COLUMNS + " FROM platform.conversations "
        f"WHERE id = {lit(conversation_id)}::uuid",
    )
    return rows[0] if rows else None


def conversation_rows(dbname: str, owner_id: str | None = None) -> list[dict]:
    clause = "TRUE"
    if owner_id is not None:
        clause += f" AND owner_id = {lit(owner_id)}::uuid"
    return st.sql_rows(
        dbname,
        "SELECT " + _CONVERSATION_COLUMNS + " FROM platform.conversations "
        f"WHERE {clause} ORDER BY created_at, id",
    )


def request_row(dbname: str, request_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT " + _REQUEST_COLUMNS + " FROM " + _REQUEST_FROM
        + f" WHERE r.id = {lit(request_id)}::uuid",
    )
    return rows[0] if rows else None


def request_rows(dbname: str, conversation_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT " + _REQUEST_COLUMNS + " FROM " + _REQUEST_FROM
        + f" WHERE r.conversation_id = {lit(conversation_id)}::uuid "
        "ORDER BY r.created_at, r.id",
    )


def run_rows(dbname: str, conversation_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, request_id::text AS request_id, state, revision, "
        "kind, application_version_id::text AS application_version_id, "
        "parent_run_id::text AS parent_run_id, input_artifact_id::text AS input_artifact_id, "
        "output_artifact_id::text AS output_artifact_id, execution_snapshot_id::text AS execution_snapshot_id, "
        "manifest_sha256, checkpoint_thread_id::text AS checkpoint_thread_id"
        " FROM platform.runs"
        f" WHERE conversation_id = {lit(conversation_id)}::uuid"
        " ORDER BY created_at, id",
    )


def live_top_runs(dbname: str, conversation_id: str) -> list[dict]:
    return [
        row
        for row in run_rows(dbname, conversation_id)
        if row["parent_run_id"] is None and row["state"] not in TERMINAL_RUN_STATES
    ]


def slot_holder_rows(dbname: str, conversation_id: str) -> list[dict]:
    """The logical conversation slot: one pre-dispatch request or one live top run."""
    holders = [
        row
        for row in request_rows(dbname, conversation_id)
        if row["state"] in ("routing", "awaiting_input", "awaiting_selection")
    ]
    holders.extend(live_top_runs(dbname, conversation_id))
    return holders


def background_jobs(dbname: str, *, target_id: str | None = None, kind: str | None = None) -> list[dict]:
    clauses = ["TRUE"]
    if target_id is not None:
        clauses.append(f"target_id = {lit(target_id)}::uuid")
    if kind is not None:
        clauses.append(f"kind = {lit(kind)}")
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, owner_id::text AS owner_id, "
        "dedup_key, state, attempt_count, expected_revision, input_artifact_id::text AS input_artifact_id "
        "FROM platform.background_jobs WHERE " + " AND ".join(clauses)
        + " ORDER BY created_at, id",
    )


def interaction_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, request_id::text AS request_id, kind, state, "
        "proposal_artifact_id::text AS proposal_artifact_id, "
        "response_artifact_id::text AS response_artifact_id, "
        "correlation_id::text AS correlation_id, expires_at::text AS expires_at, "
        "answered_at::text AS answered_at "
        "FROM platform.interactions "
        f"WHERE request_id = {lit(request_id)}::uuid ORDER BY created_at, id",
    )


def handoff_row(dbname: str, request_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT request_id::text AS request_id, conversation_id::text AS conversation_id, "
        "summary_artifact_id::text AS summary_artifact_id, "
        "source_run_ids::text AS source_run_ids, artifact_ids::text AS artifact_ids, "
        "confirmed_by::text AS confirmed_by, confirmed_at::text AS confirmed_at, "
        "from_version_id::text AS from_version_id "
        "FROM platform.request_handoffs "
        f"WHERE request_id = {lit(request_id)}::uuid",
    )
    return rows[0] if rows else None


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, purpose, "
        "sha256, media_type, size_bytes, state, revision, deleted_at::text AS deleted_at "
        "FROM platform.artifacts "
        f"WHERE id = {lit(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def run_artifact_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT ra.artifact_id::text AS artifact_id, ra.relation, "
        "a.object_key, a.sha256, a.size_bytes, a.state, a.purpose "
        "FROM platform.run_artifacts AS ra "
        "JOIN platform.artifacts AS a ON a.id = ra.artifact_id "
        f"WHERE ra.run_id = {lit(run_id)}::uuid ORDER BY a.purpose, ra.artifact_id",
    )


def artifact_bytes(stack, artifact_id: str) -> bytes:
    row = artifact_row(stack.dbname, artifact_id)
    return (stack.object_dir / row["object_key"]).read_bytes()


def run_artifact_bytes(stack, run_id: str) -> list[tuple[dict, bytes]]:
    out = []
    for row in run_artifact_rows(stack.dbname, run_id):
        out.append((row, (stack.object_dir / row["object_key"]).read_bytes()))
    return out


def snapshot_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT s.id::text AS id, s.kind, s.application_id::text AS application_id, "
        "s.application_version_id::text AS application_version_id, "
        "s.draft_revision, s.manifest_sha256, s.manifest_artifact_id::text AS manifest_artifact_id, "
        "s.manifest::text AS manifest "
        "FROM platform.runs AS r "
        "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
        f"WHERE r.id = {lit(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def idempotency_rows(dbname: str, *, path: str = REQUESTS) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT principal_id::text AS principal_id, method, path, key, request_sha256, "
        "response_code, response_json::text AS response_json, expires_at::text AS expires_at "
        "FROM platform.idempotency_keys "
        f"WHERE method = 'POST' AND path = {lit(path)} ORDER BY key",
    )


def model_call_rows(dbname: str, request_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, background_job_id::text AS background_job_id, "
        "request_id::text AS request_id, logical_key, state "
        "FROM platform.platform_model_calls "
        f"WHERE request_id = {lit(request_id)}::uuid",
    )


def audit_actions(dbname: str, resource_id: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT action FROM platform.audit_events "
        f"WHERE resource_id = {lit(resource_id)}::uuid ORDER BY created_at, id",
    )
    return [row["action"] for row in rows]


def deletion_jobs(dbname: str, *, resource_id: str) -> list[dict]:
    return t069.deletion_jobs(
        dbname, resource_type=t069.RESOURCE_CONVERSATION, resource_id=resource_id
    )


def assert_request_view(body: dict) -> None:
    assert set(body) == REQUEST_FIELDS, body
    assert body["state"] in REQUEST_STATES, body


def header(response, name: str) -> str | None:
    return response.header(name)
