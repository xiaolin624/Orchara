"""F02A/T025 real-stack helpers: validate, the manifest artifact and dependency seeding.

The stack is the real one: a throwaway PostgreSQL database migrated with the real
``cmd/admin migrate`` chain, the compiled ``cmd/api`` behind real HTTP and the
real object store on the filesystem. The manifest bytes the endpoint seals are
read back from that store and hashed, never synthesised by the test.

Two boundaries are deliberate:

* Worker image digest / runtime lock: this batch has not built a worker image
  (F04A/F15A). ``WORKER_IMAGE_DIGEST`` is a fixed placeholder deployment input;
  the runtime lock digest is the real SHA-256 of the P01 lock file
  (``probes/agent_min/requirements.lock.txt``) computed by the same Go code.
* Dependency rows are seeded with SQL: no ticket in this batch has a tool-build
  approval flow (F05) or a publish flow (F02B). The checks themselves run in Go
  against the real tables/constraints.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from support import f00b
from support import f02a
from support import stack as st

# Re-export the session/draft helpers so T025 tests import one module.
Session = f02a.Session
admin_session = f02a.admin_session
session_for = f02a.session_for
invite_and_activate = f02a.invite_and_activate
create_application_or_fail = f02a.create_application_or_fail
get_draft = f02a.get_draft
save_draft = f02a.save_draft
full_card = f02a.full_card
grant_read = f02a.grant_read
revoke_grant = f02a.revoke_grant
draft_row = f02a.draft_row
new_uuid = f02a.new_uuid

PRODUCT_ROOT = st.PRODUCT_ROOT
RUNTIME_LOCK_PATH = PRODUCT_ROOT / "probes" / "agent_min" / "requirements.lock.txt"
SNAPSHOTS = PRODUCT_ROOT / "tests" / "contracts" / "snapshots"
ADMIN_PASSWORD = f00b.ADMIN_PASSWORD

# Fixed deployment identity of the golden manifest (tests/contracts/snapshots).
WORKER_IMAGE_DIGEST = "sha256:" + "a" * 64
TOOL_IMAGE_DIGEST = "sha256:" + "b" * 64
GOLDEN_APPLICATION_ID = "33333333-3333-4333-8333-333333333333"
GOLDEN_TOOL_ID = "11111111-1111-4111-8111-111111111111"
GOLDEN_MODEL_ID = "22222222-2222-4222-8222-222222222222"


@dataclass
class T025Stack:
    server: st.ApiServer
    dsn: str
    dbname: str
    object_dir: Path
    binaries: dict

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)


def api_extra_env(
    object_dir: Path | None,
    *,
    worker_image_digest: str | None = WORKER_IMAGE_DIGEST,
    runtime_lock_path: Path | None = None,
) -> dict[str, str]:
    """Query-safe API environment: only the T025 inputs are set."""
    env: dict[str, str] = {}
    if object_dir is not None:
        env["ORCHARA_OBJECT_DIR"] = str(object_dir)
    if worker_image_digest is not None:
        env["ORCHARA_WORKER_IMAGE_DIGEST"] = worker_image_digest
    if runtime_lock_path is not None:
        env["ORCHARA_RUNTIME_LOCK_PATH"] = str(runtime_lock_path)
    return env


def runtime_lock_sha256() -> str:
    return hashlib.sha256(RUNTIME_LOCK_PATH.read_bytes()).hexdigest()


def golden_bytes(kind: str = "sales") -> tuple[bytes, str]:
    """The checked-in canonical manifest bytes and digest."""
    jcs = (SNAPSHOTS / kind / "manifest.jcs").read_bytes()
    sha = (SNAPSHOTS / kind / "manifest.sha256").read_text(encoding="ascii").strip()
    return jcs, sha


def golden_definition() -> dict:
    return json.loads((SNAPSHOTS / "sales" / "definition.json").read_text(encoding="utf-8"))


def golden_card() -> dict:
    return json.loads((SNAPSHOTS / "sales" / "capability_card.json").read_text(encoding="utf-8"))


def golden_layout() -> dict:
    return json.loads((SNAPSHOTS / "sales" / "layout.json").read_text(encoding="utf-8"))


def p05_case(name: str) -> dict:
    """One shared P05 corpus document (the validator's pinned fixtures)."""
    corpus = json.loads(
        (PRODUCT_ROOT / "probes" / "p05_dsl" / "corpus.json").read_text(encoding="utf-8")
    )
    for case in corpus["cases"]:
        if case["name"] == name:
            return case["input"]
    raise AssertionError(f"the P05 corpus has no case {name!r}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def validate(session: Session, application_id: str) -> st.Response:
    return session.client.post(
        f"/api/v1/applications/{application_id}/validate", csrf=session.csrf
    )


def validate_without_csrf(session: Session, application_id: str) -> st.Response:
    return session.client.post(f"/api/v1/applications/{application_id}/validate")


def validation_errors(response: st.Response) -> list[dict]:
    body = response.json
    assert set(body) == {"valid", "errors"}, body
    assert body["valid"] is False, body
    assert isinstance(body["errors"], list) and body["errors"], body
    for error in body["errors"]:
        assert set(error) <= {"node_id", "code", "pointer", "message"}, error
        assert set(error) >= {"code", "pointer", "message"}, error
        assert isinstance(error["code"], str) and error["code"], error
        assert isinstance(error["pointer"], str), error
        assert isinstance(error["message"], str) and error["message"], error
    return body["errors"]


def assert_location(error: dict, *, code: str, pointer: str, node_id: str | None = None) -> None:
    assert error["code"] == code, error
    assert error["pointer"] == pointer, error
    if node_id is not None:
        assert error.get("node_id") == node_id, error


# ---------------------------------------------------------------------------
# SQL seeding
# ---------------------------------------------------------------------------
def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _jsonb(value) -> str:
    return _literal(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def sql_utf8(database: str, query: str, timeout: int = 120) -> str:
    """Run SQL through psql with the statement on UTF-8 stdin.

    ``support.stack.psql`` passes the query on argv, which Python encodes with the
    Windows ANSI code page; a definition containing non-ASCII text (the spec's
    sales example does) would then reach PostgreSQL as invalid UTF-8. Describing
    the statement on stdin keeps the exact UTF-8 bytes. Everything else matches
    the shared helper (real psql client, ON_ERROR_STOP, -tA).
    """
    env = dict(os.environ)
    env["PGPASSWORD"] = st.pg_dev.load_secret()
    env["PGCLIENTENCODING"] = "UTF8"
    completed = subprocess.run(
        [
            st.pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(st.pg_dev.DEFAULT_PORT),
            "-U",
            st.pg_dev.DEFAULT_USER,
            "-d",
            database,
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ],
        input=query,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise AssertionError(f"SQL failed: {query}\n{completed.stdout}{completed.stderr}".strip())
    return completed.stdout.strip()


def insert_artifact(dbname: str, owner_id: str, *, purpose: str, sha256: str | None) -> str:
    artifact_id = new_uuid()
    object_key = "sealed/" + os.urandom(32).hex()
    digest = sha256 or hashlib.sha256(artifact_id.encode()).hexdigest()
    sql_utf8(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, sha256, media_type, size_bytes, state, revision) VALUES ("
        f"{_literal(artifact_id)}::uuid, {_literal(owner_id)}::uuid, {_literal(object_key)}, "
        f"{_literal(purpose)}, {_literal(digest)}, 'application/json', 2, 'ready', 1)",
    )
    return artifact_id


def seed_application(
    dbname: str,
    owner_id: str,
    *,
    application_id: str | None = None,
    name: str = "t025 application",
    definition: dict | None = None,
    layout: dict | None = None,
    capability_card: dict | None = None,
    revision: int = 1,
    execution_revision: int = 1,
) -> str:
    application_id = application_id or new_uuid()
    sql_utf8(
        dbname,
        "INSERT INTO platform.applications (id, owner_id, name) VALUES ("
        f"{_literal(application_id)}::uuid, {_literal(owner_id)}::uuid, {_literal(name)})",
    )
    sql_utf8(
        dbname,
        "INSERT INTO platform.application_drafts "
        "(application_id, revision, execution_revision, definition, layout, capability_card) VALUES ("
        f"{_literal(application_id)}::uuid, {int(revision)}, {int(execution_revision)}, "
        f"{_jsonb(definition if definition is not None else {})}, "
        f"{_jsonb(layout if layout is not None else {})}, "
        f"{_jsonb(capability_card if capability_card is not None else {})})",
    )
    return application_id


def seed_tool_version(
    dbname: str,
    owner_id: str,
    *,
    tool_id: str | None = None,
    state: str = "ready",
    image_digest: str | None = TOOL_IMAGE_DIGEST,
    approved: bool = True,
    approved_by: str | None = None,
) -> str:
    tool_id = tool_id or new_uuid()
    source_artifact = insert_artifact(dbname, owner_id, purpose="tool_source", sha256=None)
    manifest_sha = hashlib.sha256(("tool-" + tool_id).encode()).hexdigest()
    image = "NULL" if image_digest is None else _literal(image_digest)
    approver = "NULL" if approved_by is None else _literal(approved_by) + "::uuid"
    approved_at = "now()" if approved else "NULL"
    sql_utf8(
        dbname,
        "INSERT INTO platform.tool_versions "
        "(id, owner_id, source_artifact_id, input_schema, output_schema, dependency_lock, "
        " connector_versions, image_digest, risk_class, state, manifest_sha256, approved_by, approved_at) "
        "VALUES ("
        f"{_literal(tool_id)}::uuid, {_literal(owner_id)}::uuid, {_literal(source_artifact)}::uuid, "
        "'{}'::jsonb, '{}'::jsonb, '{}'::jsonb, '[]'::jsonb, "
        f"{image}, 'pure', {_literal(state)}, {_literal(manifest_sha)}, {approver}, {approved_at})",
    )
    return tool_id


def seed_model_version(
    dbname: str,
    owner_id: str,
    *,
    model_id: str | None = None,
    validation_state: str = "passed",
    revoked: bool = False,
) -> str:
    model_id = model_id or new_uuid()
    artifact = insert_artifact(dbname, owner_id, purpose="model_manifest", sha256=None)
    manifest_sha = hashlib.sha256(("model-" + model_id).encode()).hexdigest()
    sql_utf8(
        dbname,
        "INSERT INTO platform.model_profile_versions "
        "(id, name, provider, model_id, endpoint_ref, secret_ref, config, data_destination, "
        " manifest_sha256, manifest_artifact_id, validation_state, revoked_at) VALUES ("
        f"{_literal(model_id)}::uuid, {_literal('t025-model')}, 'stub', 'stub-model', "
        f"'env:ORCHARA_MODEL_ENDPOINT', 'env:ORCHARA_MODEL_SECRET_REF', '{{}}'::jsonb, 'local', "
        f"{_literal(manifest_sha)}, {_literal(artifact)}::uuid, {_literal(validation_state)}, "
        f"{'now()' if revoked else 'NULL'})",
    )
    return model_id


def seed_published_version(
    dbname: str,
    owner_id: str,
    application_id: str,
    *,
    version_id: str | None = None,
    version_no: int = 1,
    revoked: bool = False,
) -> str:
    """Seed a real published version plus the review/artifact rows it requires.

    Since T026's 0013 ``publication_reviews.report_id`` is a real FK, so the
    review points at a real (seeded) ``evaluation_reports`` row with its own
    ready result artifact. No evaluation job is created by this helper.
    """
    version_id = version_id or new_uuid()
    review_id = new_uuid()
    report_id = new_uuid()
    artifact = insert_artifact(dbname, owner_id, purpose="version_manifest", sha256=None)
    report_artifact = insert_artifact(dbname, owner_id, purpose="report", sha256=None)
    sql_utf8(
        dbname,
        "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES ("
        f"{_literal(report_id)}::uuid, "
        f"{_literal(hashlib.sha256(('manifest-' + report_id).encode()).hexdigest())}, "
        f"{_literal(hashlib.sha256(('dataset-' + report_id).encode()).hexdigest())}, "
        f"{_literal(report_artifact)}::uuid, 'passed')",
    )
    manifest = {"manifest_version": "1.0", "application_id": application_id, "seeded": version_no}
    manifest_sha = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sql_utf8(
        dbname,
        "INSERT INTO platform.publication_reviews "
        "(id, application_id, submitted_by, snapshot, manifest_sha256, report_id, state, "
        " reviewer_id, decided_at) VALUES ("
        f"{_literal(review_id)}::uuid, {_literal(application_id)}::uuid, "
        f"{_literal(owner_id)}::uuid, " + _jsonb(manifest) + ", "
        f"{_literal(manifest_sha)}, {_literal(report_id)}::uuid, 'approved', "
        f"{_literal(owner_id)}::uuid, now())",
    )
    # F02B/T070: the 0042 CHECK moves revoked_at/revoked_by/revoke_reason
    # together, so a seeded revoked row must carry all three (a revoked_at-only
    # row is refused by the database, exactly like a half revocation in code).
    sql_utf8(
        dbname,
        "INSERT INTO platform.application_versions "
        "(id, application_id, version_no, review_id, manifest, manifest_sha256, "
        " manifest_artifact_id, capability_card, compiler_version, worker_image_digest, "
        " state_schema_version, revoked_at, revoked_by, revoke_reason) VALUES ("
        f"{_literal(version_id)}::uuid, {_literal(application_id)}::uuid, {int(version_no)}, "
        f"{_literal(review_id)}::uuid, " + _jsonb(manifest) + ", "
        f"{_literal(manifest_sha)}, {_literal(artifact)}::uuid, '{{}}'::jsonb, "
        "'orchara-compiler/1.0.0', " + _literal(WORKER_IMAGE_DIGEST) + ", 'orchara-state/1.0', "
        + ("now(), " + _literal(owner_id) + "::uuid, 'seeded revoked version')" if revoked else "NULL, NULL, NULL)"),
    )
    return version_id


def seed_version_dependency(dbname: str, parent_version_id: str, child_version_id: str) -> None:
    sql_utf8(
        dbname,
        "INSERT INTO platform.version_dependencies (parent_version_id, child_version_id) VALUES ("
        f"{_literal(parent_version_id)}::uuid, {_literal(child_version_id)}::uuid)",
    )


def seed_principal(dbname: str, display_name: str = "t025-foreign") -> str:
    return sql_utf8(
        dbname,
        "WITH inserted AS ("
        "INSERT INTO platform.principals (id, kind, display_name) "
        f"VALUES (gen_random_uuid(), 'user', {_literal(display_name)}) RETURNING id"
        ") SELECT id FROM inserted",
    )


def seed_golden_draft(dbname: str, owner_id: str) -> str:
    """Seed the exact golden input: fixed application id, sales definition/card,
    the fixed referenced tool and model rows. Validate must then produce the
    checked-in canonical bytes and digest."""
    application_id = seed_application(
        dbname,
        owner_id,
        application_id=GOLDEN_APPLICATION_ID,
        name="golden sales",
        definition=golden_definition(),
        layout=golden_layout(),
        capability_card=golden_card(),
    )
    seed_tool_version(
        dbname,
        owner_id,
        tool_id=GOLDEN_TOOL_ID,
        image_digest=TOOL_IMAGE_DIGEST,
        approved=True,
        approved_by=owner_id,
    )
    seed_model_version(dbname, owner_id, model_id=GOLDEN_MODEL_ID, validation_state="passed")
    return application_id


# ---------------------------------------------------------------------------
# database assertions
# ---------------------------------------------------------------------------
def manifest_artifacts(dbname: str, application_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT m.application_id::text AS application_id, m.execution_revision, "
        "m.manifest_sha256, m.artifact_id::text AS artifact_id, a.object_key, a.sha256, "
        "a.size_bytes, a.state, a.deleted_at::text AS deleted_at "
        "FROM platform.manifest_artifacts AS m "
        "JOIN platform.artifacts AS a ON a.id = m.artifact_id "
        f"WHERE m.application_id = {_literal(application_id)}::uuid "
        "ORDER BY m.created_at",
    )


def manifest_artifact_bytes(object_dir: Path, row: dict) -> bytes:
    return (object_dir / row["object_key"]).read_bytes()


def count(dbname: str, table: str, where: str = "TRUE") -> int:
    return int(sql_utf8(dbname, f"SELECT count(*) FROM platform.{table} WHERE {where}"))


def count_where_application(dbname: str, table: str, application_id: str) -> int:
    return count(dbname, table, f"application_id = {_literal(application_id)}::uuid")
