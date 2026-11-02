"""F03/T029 real-stack helpers: claim, lease, heartbeat, attempt exit and runs read.

The stack is the T024-shaped real one (throwaway PostgreSQL migrated with the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` behind real HTTP with a real
filesystem object store and HMAC-signed sealed GET links, the real internal mTLS
listener) plus the T025 deployment inputs so ``debugDraft`` can freeze the
checked-in golden manifest.

Nothing is mocked: the claim/heartbeat/exit calls are real mTLS HTTP requests, the
manifest/input bytes are read back through the real signed links, and every
database assertion reads PostgreSQL.

Boundary recorded in the T029 report: the run is created through T027's
``debugDraft`` endpoint (the only production entry that exists in this batch);
a *published* run entry is F05/F02B. That is a producer boundary, not a mocked
claim path.
"""

from __future__ import annotations

import decimal
import json
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from support import f00b, f02a, t024, t025, t027
from support import stack as st

# Re-exports so a T029 test imports one module.
T029Stack = t024.T024Stack
Session = f02a.Session
admin_session = f00b.admin_session
session_for = f00b.session_for
invite_and_activate = f00b.invite_and_activate
create_application_or_fail = f02a.create_application_or_fail
new_uuid = f00b.new_uuid
sql_utf8 = t025.sql_utf8
submit_debug = t027.submit_debug
seed_golden_draft = t025.seed_golden_draft
snapshot_row = t027.snapshot_row
jobs_for_run = t027.jobs_for_run
run_artifact_rows = t027.run_artifact_rows
GOLDEN_APPLICATION_ID = t025.GOLDEN_APPLICATION_ID
GOLDEN_SHA256 = t027.GOLDEN_SHA256
WORKER_IMAGE_DIGEST = t025.WORKER_IMAGE_DIGEST
COMPILER_VERSION = "orchara-compiler/1.0.0"

# The fixed quota counter every claim takes from (migration 0016 seeds it).
QUOTA_SCOPE = "global"
QUOTA_DIMENSION = "execution_slots"


def api_extra_env(object_dir: Path, certificates, *, signing_key: str) -> dict[str, str]:
    """The T024 internal/signing environment plus T025's compiler deployment input."""
    env = t024.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env.update(t025.api_extra_env(None))
    return env


# ---------------------------------------------------------------------------
# internal mTLS client for the three F03 operations
# ---------------------------------------------------------------------------
class ExecutionInternalClient(f00b.InternalClient):
    """The T022 mTLS client plus claim/heartbeat/attempt-exit."""

    def claim_job(
        self,
        *,
        worker_id: str = "t029-supervisor",
        execution_unit_id: str = "unit-1",
        supported_compilers: list[str] | None = None,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {
                "worker_id": worker_id,
                "supported_compilers": (
                    [COMPILER_VERSION] if supported_compilers is None else list(supported_compilers)
                ),
                "execution_unit_id": execution_unit_id,
            }
        return self.post("/internal/v1/jobs/claim", body, **kwargs)

    def heartbeat_run(
        self,
        run_id: str,
        *,
        attempt_token: str | None = None,
        execution_unit_id: str = "unit-1",
        checkpoint_connections: list[dict] | None = None,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {
                "attempt_token": attempt_token,
                "execution_unit_id": execution_unit_id,
            }
            if checkpoint_connections is not None:
                body["checkpoint_connections"] = checkpoint_connections
        return self.post(f"/internal/v1/runs/{run_id}/heartbeat", body, **kwargs)

    def confirm_exit(
        self,
        attempt_id: str,
        *,
        execution_unit_id: str = "unit-1",
        evidence: dict | None = None,
        body: dict | None = None,
        **kwargs,
    ):
        if body is None:
            body = {
                "execution_unit_id": execution_unit_id,
                "evidence": {"started": False} if evidence is None else evidence,
            }
        return self.post(f"/internal/v1/attempts/{attempt_id}/exited", body, **kwargs)


def execution_client(stack: T029Stack, role: str = "supervisor") -> ExecutionInternalClient:
    return ExecutionInternalClient(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client(role),
    )


# ---------------------------------------------------------------------------
# setup: a real creator, the golden draft, and queued debug runs
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Owner:
    admin: Session
    creator: Session
    application_id: str


def golden_owner(stack: T029Stack, *, login: str | None = None) -> Owner:
    """An admin session plus a creator who owns a real golden draft."""
    admin = admin_session(stack)
    creator = invite_and_activate(
        stack,
        admin,
        login or ("t029-owner-" + uuid.uuid4().hex[:8]),
        roles=("creator",),
    )
    application_id = seed_golden_draft(stack.dbname, creator.principal_id)
    return Owner(admin=admin, creator=creator, application_id=application_id)


def minimal_owner(stack: T029Stack, *, login: str | None = None) -> Owner:
    """A second owner with a minimal (start->end) draft.

    The fixed golden application id can exist only once per database, so a test
    that needs two run owners uses this real HTTP path instead of seeding a second
    golden draft.
    """
    admin = admin_session(stack)
    creator = invite_and_activate(
        stack,
        admin,
        login or ("t029-other-" + uuid.uuid4().hex[:8]),
        roles=("creator",),
    )
    application_id = t027.application_with_definition(
        creator, t027.chain_definition([]), name="t029 minimal"
    )
    return Owner(admin=admin, creator=creator, application_id=application_id)


def queued_run(
    stack: T029Stack,
    owner: Owner,
    *,
    input_value: dict | None = None,
    draft_revision: int | None = None,
) -> dict:
    """Submit one debug run against the current draft revision and return the Run."""
    revision = (
        draft_revision
        if draft_revision is not None
        else t027.draft_revision(owner.creator, owner.application_id)
    )
    response = submit_debug(
        owner.creator,
        owner.application_id,
        draft_revision=revision,
        input_value={"text": "hello"} if input_value is None else input_value,
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run


def claim_lease(
    stack: T029Stack,
    owner: Owner,
    *,
    role: str = "supervisor",
    execution_unit_id: str = "unit-1",
    worker_id: str = "t029-supervisor",
    supported_compilers: list[str] | None = None,
) -> dict:
    """Create one queued run and claim it; fail loudly if no lease came back."""
    run = queued_run(stack, owner)
    response = execution_client(stack, role).claim_job(
        worker_id=worker_id,
        execution_unit_id=execution_unit_id,
        supported_compilers=supported_compilers,
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["run_id"] == run["id"], (lease, run)
    return lease


# ---------------------------------------------------------------------------
# contract field assertions
# ---------------------------------------------------------------------------
LEASE_FIELDS = {
    "job_id",
    "run_id",
    "attempt_token",
    "lease_deadline",
    "manifest",
    "protocol_version",
    "job_kind",
    "execution_snapshot_id",
    "manifest_sha256",
    "manifest_url",
    "input_artifact_id",
    "input_url",
    "resume_artifact_id",
    "resume_url",
}


def assert_lease(lease: dict) -> None:
    assert set(lease) == LEASE_FIELDS, lease
    for name in (
        "job_id",
        "run_id",
        "attempt_token",
        "execution_snapshot_id",
        "input_artifact_id",
    ):
        assert isinstance(lease[name], str) and uuid.UUID(lease[name]), (name, lease[name])
    assert lease["protocol_version"] == "1.0", lease
    assert lease["job_kind"] in {"start", "resume"}, lease
    assert isinstance(lease["manifest"], dict), lease
    assert isinstance(lease["manifest_sha256"], str) and len(lease["manifest_sha256"]) == 64
    assert lease["manifest_url"].startswith("http"), lease["manifest_url"]
    assert lease["input_url"].startswith("http"), lease["input_url"]
    assert lease["resume_artifact_id"] is None, lease
    assert lease["resume_url"] is None, lease


HEARTBEAT_REPLY_FIELDS = {"lease_deadline", "cancel_requested", "policy_revision", "budget_remaining"}


def assert_heartbeat_reply(body: dict) -> None:
    assert set(body) == HEARTBEAT_REPLY_FIELDS, body
    assert isinstance(body["lease_deadline"], str) and body["lease_deadline"]
    assert isinstance(body["cancel_requested"], bool), body
    assert isinstance(body["policy_revision"], int) and body["policy_revision"] >= 0, body
    assert isinstance(body["budget_remaining"], str), body


# ---------------------------------------------------------------------------
# signed sealed links: download the real bytes
# ---------------------------------------------------------------------------
def download(url: str, *, timeout: float = 30.0) -> bytes:
    """GET one signed sealed link; the token is the whole authority."""
    with urllib.request.urlopen(url, timeout=timeout) as response:
        assert response.status == 200, response.status
        return response.read()


def object_bytes(object_dir: Path, object_key: str) -> bytes:
    return (object_dir / object_key).read_bytes()


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def attempt_row(dbname: str, token: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT token::text AS token, job_id::text AS job_id, run_id::text AS run_id, "
        "worker_id, execution_unit_id, lease_deadline::text AS lease_deadline, "
        "heartbeat_at::text AS heartbeat_at, started_at::text AS started_at, "
        "exited_at::text AS exited_at, revoked_at::text AS revoked_at, "
        "exit_evidence::text AS exit_evidence, checkpoint_connections::text AS checkpoint_connections, "
        "reported_result::text AS reported_result, checkpoint_id "
        "FROM platform.run_attempts "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def attempts_for_run(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT token::text AS token, job_id::text AS job_id, run_id::text AS run_id, "
        "worker_id, execution_unit_id, lease_deadline::text AS lease_deadline, "
        "exited_at::text AS exited_at, revoked_at::text AS revoked_at "
        "FROM platform.run_attempts "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY started_at, token",
    )


def attempt_count(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT count(*) FROM platform.run_attempts"))


def quota_counter(dbname: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT scope, dimension, used, capacity FROM platform.quota_counters "
        f"WHERE scope = {st.quote_literal(QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(QUOTA_DIMENSION)}",
    )
    assert len(rows) == 1, rows
    return rows[0]


def quota_allocations(dbname: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, scope, dimension, holder_key, released_at::text AS released_at "
        "FROM platform.quota_allocations "
        f"WHERE scope = {st.quote_literal(QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(QUOTA_DIMENSION)} ORDER BY holder_key",
    )


def allocation_for_job(dbname: str, job_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, holder_key, released_at::text AS released_at "
        "FROM platform.quota_allocations "
        f"WHERE holder_key = {st.quote_literal('job:' + job_id)}",
    )
    assert len(rows) <= 1, rows
    return rows[0] if rows else None


def set_quota_capacity(dbname: str, capacity: int) -> None:
    assert capacity >= 0
    st.sql(
        dbname,
        "UPDATE platform.quota_counters SET capacity = "
        f"{int(capacity)} WHERE scope = {st.quote_literal(QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(QUOTA_DIMENSION)}",
    )


def run_state_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, phase, revision, next_event_seq, "
        "budget_limit::text AS budget_limit, currency, started_at::text AS started_at, "
        "cancel_requested_at::text AS cancel_requested_at, finished_at::text AS finished_at, "
        "input_artifact_id::text AS input_artifact_id, execution_snapshot_id::text AS execution_snapshot_id "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def job_row(dbname: str, job_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, run_id::text AS run_id, kind, state, "
        "available_at::text AS available_at, current_attempt_token::text AS current_attempt_token, "
        "resume_artifact_id::text AS resume_artifact_id "
        "FROM platform.jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, staging_object_key, "
        "purpose, sha256, media_type, size_bytes, state, revision, deleted_at::text AS deleted_at "
        "FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def policy_revision(dbname: str) -> int:
    return int(st.sql(dbname, "SELECT revision FROM platform.policy_epochs WHERE scope = 'acl'"))


def set_cancel_requested(dbname: str, run_id: str) -> None:
    """Write the F03 cancel intent the way T038's endpoint will (column only)."""
    st.sql(
        dbname,
        "UPDATE platform.runs SET cancel_requested_at = now() "
        f"WHERE id = {st.quote_literal(run_id)}::uuid AND cancel_requested_at IS NULL",
    )


def force_lease_expiry(dbname: str, token: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET lease_deadline = now() - interval '2 seconds' "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )


def revoke_attempt(dbname: str, token: str) -> None:
    """Revoke without exiting: the T031 recovery/pre-claim state."""
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET revoked_at = now() "
        f"WHERE token = {st.quote_literal(token)}::uuid AND revoked_at IS NULL",
    )


def delete_object(object_dir: Path, object_key: str) -> None:
    path = object_dir / object_key
    assert path.exists(), path
    path.unlink()


def tamper_object(object_dir: Path, object_key: str, content: bytes) -> None:
    path = object_dir / object_key
    assert path.exists(), path
    path.write_bytes(content)


def decimal_of(value: str) -> decimal.Decimal:
    return decimal.Decimal(value)


def run_path(run_id: str) -> str:
    return f"/api/v1/runs/{run_id}"


def list_runs(session: Session, *, cursor: str | None = None, limit: int | None = None):
    query = []
    if cursor is not None:
        query.append("cursor=" + urllib.parse.quote(cursor))
    if limit is not None:
        query.append("limit=" + urllib.parse.quote(str(limit)))
    suffix = ("?" + "&".join(query)) if query else ""
    return session.client.get("/api/v1/runs" + suffix)


def list_run_ids(session: Session, **kwargs) -> list[str]:
    response = list_runs(session, **kwargs)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"items", "next_cursor"}, body
    return [item["id"] for item in body["items"]]


def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02, what: str) -> None:
    f00b.wait_until(predicate, timeout=timeout, interval=interval, what=what)


def lease_deadline_epoch(value: str) -> float:
    import datetime as dt

    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()
