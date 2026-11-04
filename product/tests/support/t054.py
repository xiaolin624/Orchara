"""F09/T054 real-stack helpers: memory candidates and the confirmed memory read model.

The stack is T054's (real PostgreSQL migrated through 0033, the compiled
``cmd/api``, the real internal mTLS listener, the real filesystem object store and
a real debug run claimed by the real supervisor). The candidate is proposed over
mTLS with the run's live attempt token, the decision is a real cookie+CSRF
request, and every assertion about bytes reads the sealed object from disk or the
row from PostgreSQL 閳?never the API echo.

Two truths stay separate on purpose:

* ``platform.memory_candidates.state`` is the *stored* state;
* the API projection reports the *effective* state (a pending row past its
  ``expires_at`` is shown as ``expired``), which is what makes expiry a server
  boundary rather than a status field the page has to interpret.
"""

from __future__ import annotations

import datetime
import json
import secrets
import uuid
from urllib.parse import urlencode

from support import f00b, t024, t029, t041
from support import stack as st

CANDIDATE_TTL = datetime.timedelta(days=7)  # config.DefaultMemoryCandidateTTL


def parse_time(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def same_instant(left: datetime.datetime, right: datetime.datetime) -> bool:
    """Compare two timestamps as instants, ignoring the RFC3339 second truncation."""
    delta = abs((left - right).total_seconds())
    return delta < 1.0

# The memory error vocabulary mirrored from internal/memory.
CANDIDATE_EXPIRED = "memory_candidate_expired"
CANDIDATE_REJECTED = "memory_candidate_rejected"
CANDIDATE_CONFIRMED = "memory_candidate_confirmed"
RESOURCE_NOT_FOUND = "resource_not_found"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
PERSONAL_SCOPE_FORBIDDEN = "personal_scope_forbidden"

CANDIDATE_STATES = ("pending", "confirmed", "rejected", "expired")
DECISION_STATES = ("confirmed", "rejected")

SESSION_PASSWORD = "t054-user-" + secrets.token_urlsafe(18)


# ---------------------------------------------------------------------------
# stack helpers
# ---------------------------------------------------------------------------
def new_owner(stack, *, login: str | None = None):
    """A real creator with a real golden draft (the T029/T041 owner)."""
    return t041.owner(stack, login=login)


def owned_run(stack, run_owner=None):
    """Create one debug run and claim it as the real supervisor."""
    return t041.claimed_run(stack, run_owner=run_owner)


def worker(stack):
    return t029.execution_client(stack, "worker")


def supervisor(stack):
    return t029.execution_client(stack, "supervisor")


def maintenance(stack):
    return t029.execution_client(stack, "maintenance")


# ---------------------------------------------------------------------------
# internal propose
# ---------------------------------------------------------------------------
def propose_body(
    lease: dict, *, text: str, logical_key: str, attempt_token: str | None = None
) -> dict:
    return {
        "attempt_token": lease["attempt_token"] if attempt_token is None else attempt_token,
        "logical_key": logical_key,
        "text": text,
    }


def propose(
    stack,
    lease: dict,
    *,
    text: str = "The user prefers concise weekly summaries.",
    logical_key: str = "agent:memory-node:memory:1",
    attempt_token: str | None = None,
    role: str = "worker",
):
    client = worker(stack) if role == "worker" else t029.execution_client(stack, role)
    return client.post(
        f"/internal/v1/runs/{lease['run_id']}/memory-candidates",
        propose_body(lease, text=text, logical_key=logical_key, attempt_token=attempt_token),
    )


# ---------------------------------------------------------------------------
# contract paths
# ---------------------------------------------------------------------------
def candidate_path(candidate_id: str) -> str:
    return "/api/v1/memory-candidates/" + candidate_id


def decision_path(candidate_id: str) -> str:
    return candidate_path(candidate_id) + "/decision"


def list_candidates(session, **query):
    suffix = ("?" + urlencode(query)) if query else ""
    return session.client.get("/api/v1/memory-candidates" + suffix)


def decide(
    session,
    candidate_id: str,
    *,
    decision: str = "confirm",
    idempotency_key: str | None = None,
    csrf: str | None = None,
    client=None,
):
    target = client or session.client
    headers = {}
    if idempotency_key is not False:
        headers["Idempotency-Key"] = idempotency_key or str(uuid.uuid4())
    return target.post(
        decision_path(candidate_id),
        body={"decision": decision},
        csrf=session.csrf if csrf is None else csrf,
        headers=headers,
    )


def create_memory(
    session,
    *,
    application_id: str,
    text: str,
    confirmed: bool | None = True,
    csrf: str | None = None,
    client=None,
):
    body: dict = {"application_id": application_id, "text": text}
    if confirmed is not None:
        body["confirmed"] = confirmed
    target = client or session.client
    return target.post(
        "/api/v1/memories",
        body=body,
        csrf=session.csrf if csrf is None else csrf,
    )


def list_memories(session, **query):
    suffix = ("?" + urlencode(query)) if query else ""
    return session.client.get("/api/v1/memories" + suffix)


# ---------------------------------------------------------------------------
# service credentials (the F01 personal-data boundary)
# ---------------------------------------------------------------------------
def service_key_client(
    stack,
    admin: f00b.Session,
    *,
    display_name: str = "svc-memory",
    scopes: tuple[str, ...] = ("application:read", "application:write"),
):
    """Create a real service principal + scoped key, and a bearer HTTP client."""
    service = admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": display_name, "responsible_user_id": admin.principal_id},
        csrf=admin.csrf,
    )
    assert service.status == 201, service.text
    key = admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": service.json["id"],
            "scopes": list(scopes),
            "expires_at": "2099-01-01T00:00:00Z",
        },
        csrf=admin.csrf,
    )
    assert key.status == 201, key.text
    client = stack.client()
    return _BearerClient(client, key.json["secret"])


class _BearerClient:
    """A real HTTP client that authenticates every request with one API key."""

    def __init__(self, client, secret: str) -> None:
        self._client = client
        self._secret = secret
        # The shared helpers read ``session.client``/``session.csrf``; a bearer
        # client is its own transport and has no CSRF token to present.
        self.client = self
        self.csrf = None

    def get(self, path: str, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = "Bearer " + self._secret
        return self._client.get(path, headers=headers, **kwargs)

    def post(self, path: str, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = "Bearer " + self._secret
        return self._client.post(path, headers=headers, **kwargs)


# ---------------------------------------------------------------------------
# durable truth
# ---------------------------------------------------------------------------
CANDIDATE_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, application_id::text AS application_id, "
    "body_artifact_id::text AS body_artifact_id, source_run_id::text AS source_run_id, "
    "logical_key, state, expires_at::text AS expires_at, created_at::text AS created_at, "
    "decided_at::text AS decided_at, decided_by::text AS decided_by, decision_key"
)

MEMORY_COLUMNS = (
    "id::text AS id, owner_id::text AS owner_id, application_id::text AS application_id, "
    "body_artifact_id::text AS body_artifact_id, candidate_id::text AS candidate_id, "
    "revision, confirmed_at::text AS confirmed_at, confirmed_by::text AS confirmed_by, "
    "deleted_at::text AS deleted_at"
)


def candidate_rows(dbname: str, *, owner_id: str | None = None) -> list[dict]:
    where = "TRUE"
    if owner_id:
        where = f"owner_id = '{owner_id}'::uuid"
    return st.sql_rows(
        dbname,
        f"SELECT {CANDIDATE_COLUMNS} FROM platform.memory_candidates "
        f"WHERE {where} ORDER BY created_at, id",
    )


def candidate_row(dbname: str, candidate_id: str) -> dict | None:
    rows = [row for row in candidate_rows(dbname) if row["id"] == candidate_id]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def memory_rows(dbname: str, *, owner_id: str | None = None) -> list[dict]:
    where = "TRUE"
    if owner_id:
        where = f"owner_id = '{owner_id}'::uuid"
    return st.sql_rows(
        dbname,
        f"SELECT {MEMORY_COLUMNS} FROM platform.memories "
        f"WHERE {where} ORDER BY confirmed_at, id",
    )


def memory_row(dbname: str, memory_id: str) -> dict | None:
    rows = [row for row in memory_rows(dbname) if row["id"] == memory_id]
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def artifact_row(dbname: str, artifact_id: str) -> dict:
    return t029.artifact_row(dbname, artifact_id)


def artifact_bytes(stack, artifact: dict) -> bytes:
    return (stack.object_dir / artifact["object_key"]).read_bytes()


def candidate_text(stack, candidate: dict) -> str:
    return artifact_bytes(stack, artifact_row(stack.dbname, candidate["body_artifact_id"])).decode()


def memory_text(stack, memory: dict) -> str:
    return artifact_bytes(stack, artifact_row(stack.dbname, memory["body_artifact_id"])).decode()


def memory_artifact_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.artifacts"))


def memory_artifact_ids(dbname: str, purpose: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        f"SELECT id::text AS id FROM platform.artifacts WHERE purpose = '{purpose}' "
        "ORDER BY created_at, id",
    )
    return [row["id"] for row in rows]


# ---------------------------------------------------------------------------
# time control (the expiry boundary)
# ---------------------------------------------------------------------------
def set_expiry(dbname: str, candidate_id: str, *, delta_seconds: int) -> None:
    """Move one candidate's deadline by a whole number of seconds from now()."""
    st.sql(
        dbname,
        "UPDATE platform.memory_candidates SET expires_at = now() "
        f"+ interval '{int(delta_seconds)} seconds' "
        f"WHERE id = '{candidate_id}'::uuid",
    )


def set_state(dbname: str, candidate_id: str, state: str) -> None:
    assert state in CANDIDATE_STATES, state
    st.sql(
        dbname,
        "UPDATE platform.memory_candidates SET state = "
        f"'{state}' WHERE id = '{candidate_id}'::uuid",
    )


def candidate_application_of(stack, lease: dict) -> str:
    """The application scope the server must derive from the run, not the body."""
    rows = st.sql_rows(
        stack.dbname,
        "SELECT s.application_id::text AS application_id "
        "FROM platform.runs AS r "
        "JOIN platform.execution_snapshots AS s ON s.id = r.execution_snapshot_id "
        f"WHERE r.id = '{lease['run_id']}'::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["application_id"]


def run_owner_of(stack, lease: dict) -> str:
    rows = st.sql_rows(
        stack.dbname,
        f"SELECT owner_id::text AS owner_id FROM platform.runs "
        f"WHERE id = '{lease['run_id']}'::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["owner_id"]


# ---------------------------------------------------------------------------
# run-state helper for the failure boundary
# ---------------------------------------------------------------------------
def set_run_state(dbname: str, run_id: str, state: str) -> None:
    """Seed a non-running run state; the live attempt token is left untouched.

    This is setup, not the behaviour under test: with the attempt still the live
    lease, the only reason a proposal can be refused is the run-state check the
    endpoint is supposed to perform.
    """
    st.sql(
        dbname,
        f"UPDATE platform.runs SET state = '{state}' WHERE id = '{run_id}'::uuid",
    )
def decision_receipt_count(dbname: str) -> int:
    return int(st.sql(
        dbname,
        "SELECT count(*) FROM platform.idempotency_keys "
        "WHERE path LIKE '/api/v1/memory-candidates/%'",
    ))


def decision_receipts(dbname: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT principal_id::text AS principal_id, path, key, request_sha256, "
        "response_code, response_json::text AS response_json "
        "FROM platform.idempotency_keys "
        "WHERE path LIKE '/api/v1/memory-candidates/%' ORDER BY key",
    )
# ---------------------------------------------------------------------------
# object-store failure boundaries
# ---------------------------------------------------------------------------
def object_path(stack, object_key: str):
    return stack.object_dir / object_key


def remove_object(stack, object_key: str) -> None:
    object_path(stack, object_key).unlink()


def write_object(stack, object_key: str, content: bytes) -> None:
    object_path(stack, object_key).write_bytes(content)

