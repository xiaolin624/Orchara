"""F02A/T027 real-stack helpers: debug submission, frozen snapshots and runs.

The stack is the real one: a throwaway PostgreSQL database migrated with the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` behind real HTTP and the
real filesystem object store (the canonical manifest and the canonical debug
input are read back from it, never synthesised by a test).

Two boundaries are documented in the T027 report:

* Deployment inputs are T025's placeholders: ``WORKER_IMAGE_DIGEST`` is the same
  fixed ``sha256:aaaa…`` value the golden manifest uses and the runtime lock is
  the real P01 lock file. No worker image has been built (F04A/F15A).
* Referenced application versions / tool versions / model candidates are seeded
  with SQL because F05 owns tool builds and F02B owns publication. The debug
  checks themselves run in Go over those real rows and constraints.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass
from pathlib import Path

from support import f00b, f02a, t025
from support import stack as st

ADMIN_LOGIN = f02a.ADMIN_LOGIN
ADMIN_PASSWORD = f00b.ADMIN_PASSWORD
USER_PASSWORD = f02a.USER_PASSWORD

# Fixed deployment identity shared with the T025 golden snapshot.
WORKER_IMAGE_DIGEST = t025.WORKER_IMAGE_DIGEST
TOOL_IMAGE_DIGEST = t025.TOOL_IMAGE_DIGEST
GOLDEN_APPLICATION_ID = t025.GOLDEN_APPLICATION_ID
GOLDEN_SHA256 = (t025.SNAPSHOTS / "sales" / "manifest.sha256").read_text(encoding="ascii").strip()

# Re-exports so a T027 test imports one module.
Session = f02a.Session
admin_session = f02a.admin_session
session_for = f02a.session_for
invite_and_activate = f02a.invite_and_activate
create_application_or_fail = f02a.create_application_or_fail
create_service_account = f02a.create_service_account
create_api_key = f02a.create_api_key
grant_read = f02a.grant_read
get_application = f02a.get_application
get_draft = f02a.get_draft
save_draft = f02a.save_draft
draft_row = f02a.draft_row
full_card = f02a.full_card
new_uuid = f02a.new_uuid
seed_principal = t025.seed_principal
seed_application = t025.seed_application
seed_model_version = t025.seed_model_version
seed_tool_version = t025.seed_tool_version
seed_published_version = t025.seed_published_version
seed_golden_draft = t025.seed_golden_draft
golden_definition = t025.golden_definition
golden_card = t025.golden_card
p05_case = t025.p05_case
sql_utf8 = t025.sql_utf8


@dataclass
class T027Stack:
    server: st.ApiServer
    dsn: str
    dbname: str
    object_dir: Path
    binaries: dict

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)


# ---------------------------------------------------------------------------
# HTTP: the contract endpoints
# ---------------------------------------------------------------------------
def session_on(server: st.ApiServer, login: str, password: str) -> Session:
    """A real session against a specific API process (used for config variants)."""
    client = st.HttpClient(server.base_url)
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    assert response.status == 200, response.text
    cookie = client.cookie(st.SESSION_COOKIE)
    assert cookie, "login did not set the session cookie"
    return Session(client, response.json["csrf_token"], response.json["principal_id"], cookie)


def debug_path(application_id: str) -> str:
    return f"/api/v1/applications/{application_id}/debug-runs"


def run_path(run_id: str) -> str:
    return f"/api/v1/runs/{run_id}"


def new_idempotency_key() -> str:
    """A fresh operation name; it is not a credential and not derived from data."""
    return "t027-" + secrets.token_urlsafe(18)


def debug_body(
    *,
    draft_revision: int = 1,
    input_value: dict | None = None,
    artifact_ids: list[str] | tuple[str, ...] = (),
) -> dict:
    return {
        "draft_revision": draft_revision,
        "input": {"text": "hello"} if input_value is None else input_value,
        "artifact_ids": list(artifact_ids),
    }


def submit_debug(
    session: Session,
    application_id: str,
    *,
    draft_revision: int = 1,
    input_value: dict | None = None,
    artifact_ids: list[str] | tuple[str, ...] = (),
    key: str | None = None,
    csrf: bool = True,
    headers: dict[str, str] | None = None,
) -> st.Response:
    request_headers = dict(headers or {})
    if key is not None:
        request_headers["Idempotency-Key"] = key
    else:
        request_headers["Idempotency-Key"] = new_idempotency_key()
    return session.client.post(
        debug_path(application_id),
        body=debug_body(
            draft_revision=draft_revision,
            input_value=input_value,
            artifact_ids=artifact_ids,
        ),
        csrf=session.csrf if csrf else None,
        headers=request_headers,
    )


def submit_debug_raw(
    session: Session,
    application_id: str,
    *,
    raw_body: str | bytes,
    key: str | None = None,
    csrf: bool = True,
) -> st.Response:
    headers = {"Idempotency-Key": new_idempotency_key() if key is None else key}
    return session.client.post(
        debug_path(application_id),
        raw_body=raw_body,
        csrf=session.csrf if csrf else None,
        headers=headers,
    )


def get_run(session: Session, run_id: str) -> st.Response:
    return session.client.get(run_path(run_id))


def run_fields(run: dict) -> None:
    """Assert the contract's ten Run fields and value types."""
    assert set(run) == {
        "id",
        "state",
        "revision",
        "application_version_id",
        "parent_run_id",
        "output_artifact_id",
        "error_code",
        "kind",
        "execution_snapshot_id",
        "phase",
    }, run
    assert isinstance(run["id"], str) and uuid.UUID(run["id"])
    assert run["state"] in {
        "queued",
        "running",
        "waiting_approval",
        "waiting_children",
        "cancelling",
        "recovery_pending",
        "needs_reconciliation",
        "succeeded",
        "failed",
        "cancelled",
    }
    assert isinstance(run["revision"], int) and run["revision"] >= 1
    assert run["application_version_id"] is None
    assert run["parent_run_id"] is None
    assert run["output_artifact_id"] is None
    assert run["error_code"] is None
    assert run["kind"] == "debug"
    assert isinstance(run["execution_snapshot_id"], str) and uuid.UUID(run["execution_snapshot_id"])
    assert run["phase"] == "idle"


def import_body(body: dict) -> dict:
    """A body with an extra top-level key (the contract allows exactly three)."""
    return {**body, "unexpected": True}


# ---------------------------------------------------------------------------
# definition builders for dependency cases (same shape T025 validates)
# ---------------------------------------------------------------------------
BASE_POLICY = {
    "max_active_seconds": 900,
    "max_child_depth": 3,
    "max_child_calls": 12,
    "budget_limit": "1.00",
    "currency": "USD",
}


def definition(nodes: list[dict], edges: list[dict], policy: dict | None = None) -> dict:
    return {
        "dsl_version": "1.0",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "nodes": nodes,
        "edges": edges,
        "policy": dict(BASE_POLICY if policy is None else policy),
    }


def start_node(node_id: str = "start") -> dict:
    return {"id": node_id, "type": "start", "inputs": {}, "output_schema": {"type": "object"}, "config": {}}


def end_node(node_id: str = "end") -> dict:
    return {"id": node_id, "type": "end", "inputs": {}, "output_schema": {"type": "object"}, "config": {}}


def chain_definition(nodes: list[dict], policy: dict | None = None) -> dict:
    """A start -> nodes... -> end definition with the linear edges."""
    chain = [start_node()] + nodes + [end_node()]
    edges = [
        {"source": chain[index]["id"], "target": chain[index + 1]["id"]}
        for index in range(len(chain) - 1)
    ]
    return definition(chain, edges, policy)


def model_definition(model_id: str, policy: dict | None = None) -> dict:
    return chain_definition(
        [
            {
                "id": "analysis",
                "type": "model",
                "inputs": {"task": {"literal": "hello"}},
                "output_schema": {"type": "object"},
                "config": {
                    "model_profile_version": model_id,
                    "prompt_template": "{{task}}",
                    "response_mode": "text",
                },
            }
        ],
        policy,
    )


def tool_definition(tool_id: str) -> dict:
    return chain_definition(
        [
            {
                "id": "stats",
                "type": "python",
                "inputs": {},
                "output_schema": {"type": "object"},
                "config": {"tool_version": tool_id},
            }
        ]
    )


def subapp_definition(version_id: str) -> dict:
    return chain_definition(
        [
            {
                "id": "child",
                "type": "call_app",
                "inputs": {"source": {"ref": "input#/source"}},
                "output_schema": {"type": "object"},
                "config": {"application_version": version_id},
            }
        ]
    )


# ---------------------------------------------------------------------------
# draft setup over real HTTP
# ---------------------------------------------------------------------------
def draft_revision(session: Session, application_id: str) -> int:
    """The current draft revision (the strong ETag) a debug submission must name."""
    etag = get_draft(session, application_id).header("ETag")
    assert etag and etag.startswith('"') and etag.endswith('"'), etag
    return int(etag[1:-1])


def application_with_definition(session: Session, draft: dict, *, name: str = "debug case") -> str:
    """Create an application and save one executable-content draft; return its id.

    Saving bumps the draft revision to 2, so a submission must name the revision
    returned by :func:`draft_revision` (or 2 explicitly).
    """
    application = create_application_or_fail(session, name)
    revision = get_draft(session, application["id"]).header("ETag")
    saved = save_draft(
        session,
        application["id"],
        revision,
        definition=draft,
        capability_card=full_card(),
    )
    assert saved.status == 200, saved.text
    return application["id"]


# ---------------------------------------------------------------------------
# SQL seeding for referenced rows
# ---------------------------------------------------------------------------
def seed_artifact(
    dbname: str,
    owner_id: str,
    *,
    state: str = "ready",
    deleted: bool = False,
    content: bytes = b'{"seed":true}',
    purpose: str = "user_upload",
) -> str:
    """Insert one real artifact row in the requested state."""
    artifact_id = new_uuid()
    object_key = "sealed/" + os.urandom(32).hex()
    sha = hashlib.sha256(content).hexdigest()
    deleted_at = "now()" if deleted else "NULL"
    sql_utf8(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision, deleted_at) "
        f"VALUES ({_literal(artifact_id)}::uuid, {_literal(owner_id)}::uuid, {_literal(object_key)}, "
        f"{_literal(purpose)}, {_literal(sha)}, 'application/json', {len(content)}, "
        f"{_literal(state)}, 1, {deleted_at})",
    )
    return artifact_id


def seed_execution_snapshot(
    dbname: str,
    owner_id: str,
    *,
    application_id: str | None = None,
    manifest_sha256: str | None = None,
    draft_revision: int = 1,
    kind: str = "debug",
    application_version_id: str | None = None,
) -> str:
    """Seed one real execution snapshot (used where another ticket needs the FK).

    The artifact row is real; its bytes are not written because the T026 tests
    that reference a snapshot never read them. T027's own tests always create
    snapshots through the endpoint.
    """
    application_id = application_id or seed_application(
        dbname, owner_id, name="t027 snapshot host"
    )
    manifest_sha256 = manifest_sha256 or hashlib.sha256(
        ("snapshot-" + new_uuid()).encode()
    ).hexdigest()
    artifact_id = t025.insert_artifact(
        dbname, owner_id, purpose="execution_manifest", sha256=manifest_sha256
    )
    snapshot_id = new_uuid()
    version_sql = (
        "NULL" if application_version_id is None else f"{_literal(application_version_id)}::uuid"
    )
    revision_sql = "NULL" if kind == "published" else str(int(draft_revision))
    sql_utf8(
        dbname,
        "INSERT INTO platform.execution_snapshots "
        "(id, kind, application_id, application_version_id, draft_revision, owner_id, manifest, "
        " manifest_sha256, manifest_artifact_id, compiler_version, worker_image_digest, "
        " state_schema_version) VALUES ("
        f"{_literal(snapshot_id)}::uuid, {_literal(kind)}, {_literal(application_id)}::uuid, "
        f"{version_sql}, {revision_sql}, {_literal(owner_id)}::uuid, '{{}}'::jsonb, "
        f"{_literal(manifest_sha256)}, {_literal(artifact_id)}::uuid, 'orchara-compiler/1.0.0', "
        f"{_literal(WORKER_IMAGE_DIGEST)}, 'orchara-state/1.0')",
    )
    return snapshot_id


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _jsonb(value) -> str:
    return _literal(json.dumps(value, ensure_ascii=False)) + "::jsonb"


# ---------------------------------------------------------------------------
# database assertions
# ---------------------------------------------------------------------------
def snapshot_row(dbname: str, snapshot_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, application_id::text AS application_id, "
        "application_version_id::text AS application_version_id, draft_revision, "
        "owner_id::text AS owner_id, manifest::text AS manifest, manifest_sha256, "
        "manifest_artifact_id::text AS manifest_artifact_id, compiler_version, "
        "worker_image_digest, state_schema_version "
        "FROM platform.execution_snapshots "
        f"WHERE id = {_literal(snapshot_id)}::uuid",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    row["manifest"] = json.loads(row["manifest"])
    return row


def run_row(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, request_id::text AS request_id, "
        "conversation_id::text AS conversation_id, parent_run_id::text AS parent_run_id, "
        "owner_id::text AS owner_id, kind, execution_snapshot_id::text AS execution_snapshot_id, "
        "application_version_id::text AS application_version_id, manifest_sha256, "
        "input_artifact_id::text AS input_artifact_id, output_artifact_id::text AS output_artifact_id, "
        "checkpoint_thread_id::text AS checkpoint_thread_id, state, revision, next_event_seq, "
        "budget_limit::text AS budget_limit, currency, phase, active_elapsed_ms, "
        "max_active_ms "
        "FROM platform.runs "
        f"WHERE id = {_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def jobs_for_run(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, state, available_at::text AS available_at, "
        "current_attempt_token::text AS current_attempt_token, resume_artifact_id::text AS resume_artifact_id "
        "FROM platform.jobs "
        f"WHERE run_id = {_literal(run_id)}::uuid ORDER BY created_at, id",
    )


def run_artifact_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT artifact_id::text AS artifact_id, attempt_token::text AS attempt_token, relation "
        "FROM platform.run_artifacts "
        f"WHERE run_id = {_literal(run_id)}::uuid ORDER BY artifact_id",
    )


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, purpose, sha256, "
        "media_type, size_bytes, state, revision, deleted_at::text AS deleted_at "
        "FROM platform.artifacts "
        f"WHERE id = {_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def idempotency_rows(dbname: str, path: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT principal_id::text AS principal_id, method, path, key, request_sha256, "
        "response_code, response_json::text AS response_json "
        "FROM platform.idempotency_keys "
        f"WHERE path = {_literal(path)} ORDER BY key",
    )


def count(dbname: str, table: str, where: str = "TRUE") -> int:
    return int(sql_utf8(dbname, f"SELECT count(*) FROM platform.{table} WHERE {where}"))


def count_for_application(dbname: str, table: str, application_id: str) -> int:
    return count(dbname, table, f"application_id = {_literal(application_id)}::uuid")


def execution_rows(dbname: str, application_id: str) -> dict:
    """The execution-identity row counts one debug submission must produce."""
    return {
        "snapshots": count_for_application(dbname, "execution_snapshots", application_id),
        "runs": count(dbname, "runs", f"execution_snapshot_id IN (SELECT id FROM platform.execution_snapshots WHERE application_id = {_literal(application_id)}::uuid)"),
        "jobs": count(dbname, "jobs", f"run_id IN (SELECT id FROM platform.runs WHERE execution_snapshot_id IN (SELECT id FROM platform.execution_snapshots WHERE application_id = {_literal(application_id)}::uuid))"),
        "versions": count_for_application(dbname, "application_versions", application_id),
        "reviews": count_for_application(dbname, "publication_reviews", application_id),
        "background_jobs": count(dbname, "background_jobs"),
        "evaluations": count(dbname, "evaluation_jobs"),
    }


def no_execution_rows(dbname: str, application_id: str) -> None:
    rows = execution_rows(dbname, application_id)
    assert rows == {
        "snapshots": 0,
        "runs": 0,
        "jobs": 0,
        "versions": 0,
        "reviews": 0,
        "background_jobs": 0,
        "evaluations": 0,
    }, rows


def object_bytes(object_dir: Path, object_key: str) -> bytes:
    return (object_dir / object_key).read_bytes()


def canonical_json(value) -> bytes:
    """RFC 8785 JCS for the simple test objects used here (sorted, compact)."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
