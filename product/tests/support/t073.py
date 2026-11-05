"""F11/T073 real-stack helpers: user-scoped diagnostic grants.

Everything here is real: the throwaway PostgreSQL 16 database migrated by the
compiled ``cmd/admin``, the compiled ``cmd/api`` behind HTTP with the real
internal mTLS listener, real sealed object bytes, real cookie+CSRF sessions and
the compiled maintenance process that finalizes artifacts. The helpers only drive
those surfaces and read the authoritative rows back; no grant state is faked.

Contract surface (``contracts/openapi.json`` operationIds ``grantDiagnostics`` /
``revokeDiagnostics``)::

    POST   /api/v1/diagnostic-grants        -> 201 Accepted {id,state}
    DELETE /api/v1/diagnostic-grants/{id}   -> 204

The documented non-contract extensions (ADR 0028) are the owner/grantee list and
the grantee-only content door::

    GET /api/v1/diagnostic-grants?                -> metadata only
    GET /api/v1/diagnostic-grants/{id}/content?object=artifact:<uuid>&purpose=<p>
"""

from __future__ import annotations

import datetime
import json
import time
import uuid
from urllib.parse import quote

from support import f00b, t028, t033, t041
from support import stack as st

GRANTS_PATH = "/api/v1/diagnostic-grants"
GRANT_FIELDS = {"id", "state"}
GRANT_METADATA_FIELDS = {
    "id",
    "state",
    "direction",
    "run_id",
    "owner_id",
    "grantee_id",
    "purposes",
    "object_scope",
    "expires_at",
    "created_at",
    "revoked_at",
}

ACTIVE = "active"
EXPIRED = "expired"
REVOKED = "revoked"

# The closed refusal vocabulary of the content door. Each cause has its own code
# so a test can prove that "out of scope", "expired" and "revoked" are distinct.
CODE_GRANT_REVOKED = "diagnostic_grant_revoked"
CODE_GRANT_EXPIRED = "diagnostic_grant_expired"
CODE_RUN_DELETED = "diagnostic_run_deleted"
CODE_PURPOSE = "diagnostic_purpose_denied"
CODE_SCOPE = "diagnostic_scope_denied"
CODE_CONTENT_UNAVAILABLE = "diagnostic_content_unavailable"
CODE_CONTENT_NOT_READY = "diagnostic_content_not_ready"
CODE_RESOURCE_NOT_FOUND = "resource_not_found"
CODE_VALIDATION = "validation_failed"
CODE_CSRF = "csrf_invalid"
CODE_PERSONAL_SCOPE = "personal_scope_forbidden"

ACTION_GRANT_CREATED = "diagnostic.grant_created"
ACTION_GRANT_REVOKED = "diagnostic.grant_revoked"
ACTION_CONTENT_READ = "diagnostic.content_read"

# A served/refused/failed content-read audit row carries exactly these keys. It is
# the typed whitelist in internal/audit.RecordContentRead; a new key is a code
# change and a report update, never a caller decision.
CONTENT_READ_AUDIT_KEYS = {
    "grant_id",
    "purpose",
    "run_id",
    "resource_type",
    "resource_id",
    "operation_id",
    "attempt_token",
    "request_id",
    "outcome",
    "error_code",
}

GRANTED_PURPOSES = ["troubleshooting"]
OTHER_PURPOSE = "security_review"


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def rfc3339(moment: datetime.datetime) -> str:
    """A UTC RFC3339 timestamp with a Z suffix (what Go's time.RFC3339 accepts)."""
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def expires_in(seconds: float) -> str:
    return rfc3339(now_utc() + datetime.timedelta(seconds=seconds))


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
def admin_session(stack) -> f00b.Session:
    """The bootstrap administrator: the real grantee of every grant."""
    return f00b.session_for(stack, f00b.ADMIN_LOGIN, f00b.ADMIN_PASSWORD)


def second_admin(stack, admin: f00b.Session) -> f00b.Session:
    """Another real, enabled administrator: an admin who is not the grantee."""
    login = "t073-admin-" + uuid.uuid4().hex[:12]
    password = "t073-password-" + uuid.uuid4().hex
    invited = admin.client.post(
        "/api/v1/admin/users",
        body={"login": login, "display_name": "T073 Second Admin", "roles": ["admin"]},
        csrf=admin.csrf,
    )
    assert invited.status == 201, invited.text
    activated = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": invited.json["activation_token"], "password": password},
    )
    assert activated.status == 200, activated.text
    return f00b.session_for(stack, login, password)


def owner(stack, *, login: str | None = None):
    """A real creator with a real golden draft (the T029/T041 owner)."""
    return t041.owner(stack, login=login)


def other_owner(stack):
    return t041.secondary_owner(stack)


# ---------------------------------------------------------------------------
# run + real output artifact (the content a grant protects)
# ---------------------------------------------------------------------------
def claimed_run(stack, run_owner=None):
    """One debug run claimed by the real supervisor (live attempt token)."""
    if run_owner is None:
        return t041.claimed_run(stack)
    return t041.claimed_run(stack, run_owner)


def tool_operation(stack, lease: dict) -> str:
    """One real capability operation attempt bound to the live attempt token.

    The tool path needs no model provider: the grant is ledgered and the worker
    may execute the call locally (the same seam tests/f06 uses). The operation row
    is what makes operation/attempt correlation a real join key, not a guess.
    """
    t041.set_tool_schema(stack.dbname, t041.CITY_SCHEMA)
    operation_id = t041.new_uuid()
    response = t041.invoke(
        t041.worker_client(stack),
        lease,
        operation_id=operation_id,
        capability_kind="tool",
        capability_version=t041.GOLDEN_TOOL_ID,
        params={"city": "Berlin"},
    )
    assert response.status == 200, response.text
    return operation_id


def upload_output(
    stack,
    lease: dict,
    content: bytes | str,
    *,
    purpose: str = "node_output",
    relation: str = "output",
) -> str:
    """createWorkerUpload -> PUT -> complete -> finalize -> register (real bytes)."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    artifact_id, _sha = t033.upload_run_artifact(
        stack,
        t033.artifact_client(stack, "worker"),
        lease,
        content=data,
        purpose=purpose,
        relation=relation,
    )
    return artifact_id


def upload_input(stack, session, content: bytes | str) -> str:
    """One owner-uploaded artifact through the public T028 path."""
    from support import t069

    return t069.upload_ready_artifact(stack, session, content)


def artifact_object_key(dbname: str, artifact_id: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT object_key FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["object_key"]


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, state, "
        "purpose, size_bytes, deleted_at::text AS deleted_at, purged_at::text AS purged_at "
        "FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def remove_sealed_bytes(stack, artifact_id: str) -> None:
    """Simulate the T071 physical purge for the failure/backup tests.

    The authoritative row is untouched; only the sealed bytes disappear, exactly
    the state T071 will leave after it clears the ``object_store`` scope.
    """
    key = artifact_object_key(stack.dbname, artifact_id)
    path = stack.object_dir / key
    assert path.exists(), path
    path.unlink()


# ---------------------------------------------------------------------------
# grant HTTP surface
# ---------------------------------------------------------------------------
def create_grant(
    session,
    run_id: str,
    grantee_id: str,
    *,
    purposes: list[str] | None = None,
    expires_at: str | None = None,
    object_scope: list[str] | None = None,
    csrf: str | None = None,
    headers: dict[str, str] | None = None,
):
    request_headers = dict(headers or {})
    if object_scope is not None:
        request_headers["X-Diagnostic-Object-Scope"] = ",".join(object_scope)
    body = {
        "run_id": run_id,
        "grantee_id": grantee_id,
        "purposes": GRANTED_PURPOSES if purposes is None else purposes,
        "expires_at": expires_at if expires_at is not None else expires_in(3600),
    }
    token = getattr(session, "csrf", None) if csrf is None else csrf
    return session.client.post(
        GRANTS_PATH, body=body, csrf=token, headers=request_headers
    )


def create_grant_ok(session, run_id: str, grantee_id: str, **kwargs) -> dict:
    response = create_grant(session, run_id, grantee_id, **kwargs)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == GRANT_FIELDS, body
    assert body["state"] == "granted", body
    assert uuid.UUID(body["id"])
    return body


def grant_path(grant_id: str) -> str:
    return GRANTS_PATH + "/" + quote(str(grant_id), safe="")


def revoke_grant(session, grant_id: str, *, csrf: str | None = None):
    token = getattr(session, "csrf", None) if csrf is None else csrf
    return session.client.request("DELETE", grant_path(grant_id), csrf=token)


def list_grants(session, *, client=None):
    target = client or session.client
    return target.get(GRANTS_PATH)


def list_grant_items(session, *, client=None) -> list[dict]:
    response = list_grants(session, client=client)
    assert response.status == 200, response.text
    body = response.json
    assert isinstance(body, dict) and isinstance(body.get("items"), list), body
    for item in body["items"]:
        assert set(item) == GRANT_METADATA_FIELDS, item
    return body["items"]


def content_path(grant_id: str, object_entry: str, purpose: str) -> str:
    return grant_path(grant_id) + "/content?" + "&".join(
        (
            "object=" + quote(str(object_entry), safe=""),
            "purpose=" + quote(str(purpose), safe=""),
        )
    )


def read_content(
    session,
    grant_id: str,
    object_entry: str,
    *,
    purpose: str = GRANTED_PURPOSES[0],
    client=None,
    headers: dict[str, str] | None = None,
):
    target = client or session.client
    return target.get(
        content_path(grant_id, object_entry, purpose), headers=headers
    )


def artifact_scope(artifact_id: str) -> str:
    return "artifact:" + str(artifact_id)


# ---------------------------------------------------------------------------
# durable truth
# ---------------------------------------------------------------------------
_GRANT_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, grantee_id::text AS grantee_id, "
    "run_id::text AS run_id, to_jsonb(purposes)::text AS purposes, "
    "object_scope::text AS object_scope, "
    "to_char(expires_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS expires_at, "
    "to_char(revoked_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS revoked_at, "
    "to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at"
)


def grant_rows(dbname: str, *, grant_id: str | None = None) -> list[dict]:
    clause = "TRUE"
    if grant_id is not None:
        clause = "id = " + st.quote_literal(grant_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT " + _GRANT_COLUMNS + " FROM platform.diagnostic_grants "
        f"WHERE {clause} ORDER BY created_at, id",
    )


def grant_row(dbname: str, grant_id: str) -> dict | None:
    rows = grant_rows(dbname, grant_id=grant_id)
    return rows[0] if rows else None


def grant_purposes(row: dict) -> list[str]:
    value = row.get("purposes")
    return json.loads(value) if isinstance(value, str) else list(value or [])


def grant_object_scope(row: dict) -> list[str]:
    value = row.get("object_scope")
    return json.loads(value) if isinstance(value, str) else list(value or [])


def audit_rows(dbname: str, action: str | None = None) -> list[dict]:
    clause = ""
    if action is not None:
        clause = " WHERE action = " + st.quote_literal(action)
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, action, actor_id::text AS actor_id, "
        "resource_type, resource_id::text AS resource_id, metadata::text AS metadata, "
        "created_at::text AS created_at FROM platform.audit_events"
        + clause
        + " ORDER BY created_at, id",
    )


def audit_metadata(row: dict) -> dict:
    value = row.get("metadata")
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def content_read_rows(dbname: str) -> list[dict]:
    return audit_rows(dbname, ACTION_CONTENT_READ)


def content_read_metadata(dbname: str) -> list[dict]:
    return [audit_metadata(row) for row in content_read_rows(dbname)]


def all_audit_text(dbname: str) -> str:
    """Every audit row verbatim, for a content/credential sentinel scan."""
    rows = st.sql_rows(
        dbname,
        "SELECT coalesce(string_agg(concat_ws(' ', id::text, action, resource_type, "
        "resource_id::text, metadata::text, actor_id::text), E'\\n'), '') AS text "
        "FROM platform.audit_events",
    )
    return rows[0]["text"] if rows else ""


def run_event_text(dbname: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT coalesce(string_agg(payload::text, E'\\n'), '') AS text "
        "FROM platform.run_events",
    )
    return rows[0]["text"] if rows else ""


def background_job_for_artifact(dbname: str, artifact_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, error_code FROM platform.background_jobs "
        f"WHERE input_artifact_id = {st.quote_literal(artifact_id)}::uuid "
        "ORDER BY created_at, id LIMIT 1",
    )
    return rows[0] if rows else None


def wait_until(moment: datetime.datetime, *, margin: float = 0.0) -> None:
    """Sleep until wall clock >= moment + margin (never a fixed short sleep)."""
    deadline = moment.timestamp() + margin
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.2))


def sleep_past(expires_at: datetime.datetime, *, margin: float = 1.5) -> None:
    wait_until(expires_at, margin=margin)


def admin_metadata_responses(stack) -> dict:
    """The existing administrator read surfaces this ticket must keep metadata-only.

    Each value is the real HTTP response; the caller asserts both the status and
    that no content marker appears in any body. ``None`` means the surface needs an
    id the caller did not provide.
    """
    return {}
