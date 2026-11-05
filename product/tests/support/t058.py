"""F09/T058 real-stack helpers: memory detail, strong-ETag revision and deletion.

The stack is T054's (real PostgreSQL migrated through the T058 migration, the
compiled ``cmd/api``, the real internal mTLS listener, the real filesystem object
store and a real debug run claimed by the real supervisor). Every memory is
created through the real contract entry (``POST /api/v1/memories``); every
assertion about content reads the sealed object from disk or the row from
PostgreSQL, never the API echo alone.

Two truths stay separate on purpose:

* ``platform.memories.revision`` is the authoritative version and the strong
  ETag source;
* ``platform.policy_epochs(scope='memory:<owner>:<application>')`` is the cache /
  recall epoch. T058 raises it in the same transaction as the revision change;
  T063/T067 consume it. A test reads both rows directly.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote, urlencode

from support import f00b, t027, t029, t041, t054
from support import stack as st

# The T058 error vocabulary mirrored from internal/memory + internal/api.
RESOURCE_NOT_FOUND = "resource_not_found"
VALIDATION_FAILED = "validation_failed"
PRECONDITION_REQUIRED = "precondition_required"
PRECONDITION_FAILED = "precondition_failed"
PERSONAL_SCOPE_FORBIDDEN = "personal_scope_forbidden"

DELETION_STATES = ("pending", "running", "completed", "failed")
MEMORY_FIELDS = {"id", "application_id", "revision", "text"}
DELETION_FIELDS = {"id", "state", "resource_id"}


# ---------------------------------------------------------------------------
# setup: real owners, real applications
# ---------------------------------------------------------------------------
def owner(stack, *, login: str | None = None) -> t029.Owner:
    """A real creator with a real golden draft (the T029/T041/T054 owner)."""
    return t041.owner(stack, login=login)


def other_owner(stack) -> t029.Owner:
    """A second real creator with their own application (no shared rows)."""
    return t041.secondary_owner(stack)


def new_application(session, *, name: str = "t058 second application") -> str:
    """A second real application owned by the same creator."""
    return t027.application_with_definition(session, t027.chain_definition([]), name=name)


def create_memory_ok(session, application_id: str, text: str) -> dict:
    response = t054.create_memory(session, application_id=application_id, text=text)
    assert response.status == 201, response.text
    return response.json


# ---------------------------------------------------------------------------
# contract paths and requests
# ---------------------------------------------------------------------------
def memory_path(memory_id: str) -> str:
    return "/api/v1/memories/" + quote(str(memory_id), safe="")


def detail(session, memory_id: str):
    return session.client.get(memory_path(memory_id))


def update(
    session,
    memory_id: str,
    *,
    text: str | None = None,
    if_match: str | None = None,
    confirmed: bool | None = True,
    csrf: str | None = None,
    client=None,
):
    """One PATCH /memories/{id}.

    ``if_match=None`` omits the header (the 428 case); ``if_match=False`` is a
    convenience for the same omission. Any string is sent verbatim, including a
    garbled one, so the server's own parser is what is observed.
    """
    body: dict = {}
    if text is not None:
        body["text"] = text
    if confirmed is not None:
        body["confirmed"] = confirmed
    headers = {}
    if if_match not in (None, False):
        headers["If-Match"] = if_match
    target = client or session.client
    return target.request(
        "PATCH",
        memory_path(memory_id),
        body=body,
        csrf=session.csrf if csrf is None else csrf,
        headers=headers,
    )


def delete(session, memory_id: str, *, csrf: str | None = None, client=None):
    target = client or session.client
    return target.request(
        "DELETE",
        memory_path(memory_id),
        csrf=session.csrf if csrf is None else csrf,
    )


def list_memories(session, **query):
    suffix = ("?" + urlencode(query)) if query else ""
    return session.client.get("/api/v1/memories" + suffix)


def etag_of(response) -> str:
    value = response.header("ETag")
    assert value, response.text
    return value


def expected_etag(revision: int) -> str:
    """The strong ETag the server must issue for a revision (`"1"`, `"2"`, ...)."""
    return '"' + str(revision) + '"'


# ---------------------------------------------------------------------------
# service credentials (the F01 personal-data boundary)
# ---------------------------------------------------------------------------
def service_client(stack, *, scopes: tuple[str, ...] = ("application:read", "application:write")):
    admin = f00b.admin_session(stack)
    return t054.service_key_client(stack, admin, display_name="svc-t058", scopes=scopes)


# ---------------------------------------------------------------------------
# durable truth
# ---------------------------------------------------------------------------
def epoch_of(dbname: str, owner_id: str, application_id: str) -> int:
    """The current memory cache epoch of one (owner, application) scope (0 if none)."""
    scope = "memory:" + owner_id.lower() + ":" + application_id.lower()
    return int(
        st.sql(
            dbname,
            "SELECT COALESCE((SELECT revision FROM platform.policy_epochs WHERE scope = "
            + st.quote_literal(scope)
            + "), 0)",
        )
    )


DELETION_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, resource_type, resource_id::text AS resource_id, "
    "deletion_revision, state, remaining_scopes::text AS remaining_scopes, "
    "scope_details::text AS scope_details, completed_scopes::text AS completed_scopes, "
    "attempt_count, error_code, created_at::text AS created_at, "
    "completed_at::text AS completed_at"
)

# F11/T069: the contract DeletionJob's closed remaining-scope vocabulary.
DELETION_SCOPES = (
    "backup_catalog",
    "checkpoints",
    "object_store",
    "retrieval_cache",
    "sandbox_temp",
    "search_index",
    "third_party_history",
)


def deletion_jobs(dbname: str, *, resource_id: str | None = None) -> list[dict]:
    where = "TRUE"
    if resource_id:
        where = "resource_id = " + st.quote_literal(resource_id) + "::uuid"
    return st.sql_rows(
        dbname,
        f"SELECT {DELETION_COLUMNS} FROM platform.deletion_jobs "
        f"WHERE {where} ORDER BY created_at, id",
    )


def deletion_job(dbname: str, resource_id: str) -> dict | None:
    rows = deletion_jobs(dbname, resource_id=resource_id)
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def deletion_scopes(job: dict) -> list[str]:
    """The contract's remaining scope names (F11/T069 changed this from objects)."""
    import json

    return json.loads(job["remaining_scopes"])


def deletion_scope_details(job: dict) -> dict:
    import json

    return json.loads(job["scope_details"])


def memory_body_artifacts(dbname: str, *, owner_id: str | None = None) -> list[dict]:
    where = "purpose = 'memory_body'"
    if owner_id:
        where += " AND owner_id = " + st.quote_literal(owner_id) + "::uuid"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, sha256, "
        "size_bytes, state, created_at::text AS created_at FROM platform.artifacts "
        f"WHERE {where} ORDER BY created_at, id",
    )


def artifact_of(dbname: str, artifact_id: str) -> dict:
    return t054.artifact_row(dbname, artifact_id)


def row_of_memory(dbname: str, memory_id: str) -> dict | None:
    return t054.memory_row(dbname, memory_id)


def text_of_memory(stack, row: dict) -> str:
    return t054.memory_text(stack, row)


def epoch_scope(owner_id: str, application_id: str) -> str:
    return "memory:" + owner_id.lower() + ":" + application_id.lower()


def new_uuid() -> str:
    return str(uuid.uuid4())

