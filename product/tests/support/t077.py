"""F08/T077 real-stack helpers: the authorized purpose catalog, search and rebuild.

Everything here drives the real platform: a throwaway PostgreSQL 16 database
migrated by the compiled ``cmd/admin`` binary, the compiled ``cmd/api`` behind
real cookie HTTP, the compiled ``cmd/admin catalog ...`` rebuild process and the
compiled maintenance process for the ``catalog_index`` handler. Published versions
are real compiler output: the capability card is saved through the draft API, the
manifest is produced by ``POST .../validate`` and read back from the sealed object
store. No search result or projection row is fabricated by the assertions; the
only hand-written rows are the deliberately stale projection entries the tests
insert to prove the authoritative re-check (the production rebuild never writes
them).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from urllib.parse import quote

from support import f00b, t025, t027
from support import stack as st

SEARCH = "/api/v1/catalog/search"
CATALOG_PAGE = "/catalog"
CATALOG_ASSET = "/assets/catalog.js"

SEARCH_ITEM_FIELDS = {
    "application_id",
    "application_name",
    "version_id",
    "version_no",
    "manifest_sha256",
    "capability_card",
}
RETRIEVAL_FIELDS = {
    "source",
    "stale",
    "index_state",
    "projection_epoch",
    "rechecked",
    "rejected_by_recheck",
}
CARD_FIELDS = {
    "purpose",
    "includes",
    "excludes",
    "required_inputs",
    "expected_outputs",
    "examples",
}

ERROR_INVALID_CURSOR = "invalid_cursor"
ERROR_VALIDATION = "validation_failed"
ERROR_VERSION_REVOKED = "version_revoked"
ERROR_VERSION_ARCHIVED = "version_archived"
ERROR_RESOURCE_NOT_FOUND = "resource_not_found"

REBUILD_OK = "ok"
REBUILD_FAILED = "failed"


# ---------------------------------------------------------------------------
# a real compiled published version with a chosen capability card
# ---------------------------------------------------------------------------
@dataclass
class CatalogVersion:
    application_id: str
    version_id: str
    version_no: int
    manifest_sha256: str
    manifest_artifact_id: str
    manifest: dict
    card: dict

    @property
    def purpose(self) -> str:
        return self.card["purpose"]


def card(
    *,
    purpose: str,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    required_inputs: list[str] | None = None,
    expected_outputs: list[str] | None = None,
    examples: list[str] | None = None,
) -> dict:
    return {
        "purpose": purpose,
        "includes": includes if includes is not None else ["aggregation"],
        "excludes": excludes if excludes is not None else ["external writes"],
        "required_inputs": required_inputs if required_inputs is not None else ["sales csv"],
        "expected_outputs": expected_outputs if expected_outputs is not None else ["report"],
        "examples": examples if examples is not None else ["monthly report", "weekly digest"],
    }


def publish_version(
    stack,
    session,
    *,
    purpose: str,
    application_id: str | None = None,
    name: str = "t077 catalog application",
    version_no: int = 1,
    includes: list[str] | None = None,
    excludes: list[str] | None = None,
    required_inputs: list[str] | None = None,
    expected_outputs: list[str] | None = None,
    examples: list[str] | None = None,
    active: bool = True,
    write_outbox: bool = True,
) -> CatalogVersion:
    """Compile and publish one immutable version through the real compiler.

    When ``application_id`` is given the existing application gains another
    version; otherwise a fresh application is created. The review/version rows are
    the exact shape T068's approval transaction writes, and the outbox row is the
    exact payload T068/T070 write in the same transaction, so the rebuild consumes
    real signals rather than a test-only shortcut.
    """
    capability_card = card(
        purpose=purpose,
        includes=includes,
        excludes=excludes,
        required_inputs=required_inputs,
        expected_outputs=expected_outputs,
        examples=examples,
    )
    definition = t027.chain_definition([])
    if application_id is None:
        application_id = t027.create_application_or_fail(session, name)["id"]
    revision = t027.get_draft(session, application_id).header("ETag")
    saved = t027.save_draft(
        session,
        application_id,
        revision,
        definition=definition,
        capability_card=capability_card,
    )
    assert saved.status == 200, saved.text

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
    assert manifest["capability_card"] == capability_card

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
        f"'t077 published fixture', {lit(row['artifact_id'])}::uuid)",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.application_versions "
        "(id, application_id, version_no, review_id, manifest, manifest_sha256, "
        " manifest_artifact_id, capability_card, compiler_version, worker_image_digest, "
        " state_schema_version) VALUES ("
        f"{lit(version_id)}::uuid, {lit(application_id)}::uuid, {version_no}, "
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
    if active:
        t025.sql_utf8(
            stack.dbname,
            "UPDATE platform.applications SET active_version_id = "
            f"{lit(version_id)}::uuid WHERE id = {lit(application_id)}::uuid",
        )
    if write_outbox:
        payload = {
            "op": "publish",
            "application_id": application_id,
            "version_id": version_id,
            "version_no": version_no,
            "manifest_sha256": manifest_sha,
            "previous_active_version_id": None,
            "active_version_id": version_id,
        }
        t025.sql_utf8(
            stack.dbname,
            "INSERT INTO platform.outbox (id, kind, aggregate_id, payload) VALUES ("
            f"{lit(f00b.new_uuid())}::uuid, 'catalog_index', {lit(version_id)}::uuid, "
            f"{jsonb(payload)})",
        )
    return CatalogVersion(
        application_id=application_id,
        version_id=version_id,
        version_no=version_no,
        manifest_sha256=manifest_sha,
        manifest_artifact_id=row["artifact_id"],
        manifest=manifest,
        card=capability_card,
    )


# ---------------------------------------------------------------------------
# the search HTTP surface
# ---------------------------------------------------------------------------
def search(
    session,
    q: str | None = None,
    *,
    cursor: str | None = None,
    limit: int | None = None,
    query: str | None = None,
):
    if query is None:
        parts = []
        if q is not None:
            parts.append("q=" + quote(q, safe=""))
        if cursor is not None:
            parts.append("cursor=" + quote(cursor, safe=""))
        if limit is not None:
            parts.append("limit=" + str(limit))
        query = "&".join(parts)
    path = SEARCH + ("?" + query if query else "")
    return session.client.get(path)


def search_ok(response) -> dict:
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"items", "next_cursor", "retrieval"}, body
    assert set(body["retrieval"]) == RETRIEVAL_FIELDS, body["retrieval"]
    for item in body["items"]:
        assert set(item) == SEARCH_ITEM_FIELDS, item
        assert set(item["capability_card"]) == CARD_FIELDS, item
    return body


def items_of(response) -> list[dict]:
    return search_ok(response)["items"]


def version_ids(response) -> list[str]:
    return [item["version_id"] for item in items_of(response)]


# ---------------------------------------------------------------------------
# the rebuild process (the compiled admin binary)
# ---------------------------------------------------------------------------
def rebuild(stack, *args: str) -> subprocess.CompletedProcess[str]:
    return st.run_admin(
        stack.binaries["admin"], ["catalog", "rebuild", *args], dsn=stack.dsn
    )


def rebuild_ok(stack, *args: str) -> dict:
    completed = rebuild(stack, *args)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["status"] == REBUILD_OK, report
    return report


def rebuild_fails(stack, *args: str) -> dict:
    completed = rebuild(stack, *args)
    assert completed.returncode == 1, (completed.returncode, completed.stdout, completed.stderr)
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["status"] == REBUILD_FAILED, report
    return report


def status(stack) -> dict:
    completed = st.run_admin(stack.binaries["admin"], ["catalog", "status"], dsn=stack.dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def index_once(stack, version_id: str) -> dict:
    completed = st.run_admin(
        stack.binaries["admin"], ["catalog", "index", "--version", version_id], dsn=stack.dsn
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def run_catalog_maintenance(stack) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    return f00b.maintenance_once(stack, "--kinds", "catalog_index")


# ---------------------------------------------------------------------------
# authoritative rows and the deliberate stale rows
# ---------------------------------------------------------------------------
def lit(value: str) -> str:
    return st.quote_literal(value)


def jsonb(value) -> str:
    return lit(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def state_row(dbname: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT id, state, projection_epoch, rebuild_started_at::text AS rebuild_started_at, "
        "rebuilt_at::text AS rebuilt_at, last_error_code, last_error_at::text AS last_error_at, "
        "expected_count, indexed_count, updated_at::text AS updated_at "
        "FROM platform.catalog_index_state WHERE id = 'catalog'",
    )
    return rows[0] if rows else None


def entry_rows(dbname: str, *, version_id: str | None = None) -> list[dict]:
    clause = "TRUE"
    if version_id is not None:
        clause = f"e.application_version_id = {lit(version_id)}::uuid"
    return st.sql_rows(
        dbname,
        "SELECT e.application_version_id::text AS application_version_id, "
        "e.application_id::text AS application_id, e.search_text, "
        "e.source_manifest_sha256, e.updated_at::text AS updated_at, "
        "v.revoked_at::text AS revoked_at, a.archived_at::text AS archived_at "
        "FROM platform.application_catalog_entries AS e "
        "JOIN platform.application_versions AS v ON v.id = e.application_version_id "
        "JOIN platform.applications AS a ON a.id = v.application_id "
        f"WHERE {clause} ORDER BY e.application_version_id",
    )


def insert_projection_row(
    dbname: str,
    version: CatalogVersion,
    *,
    search_text: str | None = None,
    manifest_sha256: str | None = None,
    application_id: str | None = None,
) -> None:
    """Insert the deliberately stale projection row the tests own.

    This models a projection written before the authoritative row changed (or a
    corrupted row that names another application). The production rebuild never
    writes such a row; search must filter it from the authoritative tables.
    """
    t025.sql_utf8(
        dbname,
        "INSERT INTO platform.application_catalog_entries "
        "(application_version_id, application_id, search_text, source_manifest_sha256) "
        "VALUES ("
        f"{lit(version.version_id)}::uuid, "
        f"{lit(application_id or version.application_id)}::uuid, "
        f"{lit(search_text if search_text is not None else version.purpose)}, "
        f"{lit(manifest_sha256 or version.manifest_sha256)}) "
        "ON CONFLICT (application_version_id) DO UPDATE SET "
        "application_id = EXCLUDED.application_id, "
        "search_text = EXCLUDED.search_text, "
        "source_manifest_sha256 = EXCLUDED.source_manifest_sha256, "
        "updated_at = now()",
    )


def truncate_projection(dbname: str) -> None:
    """Simulate an index restart: the projection rows are gone, the ledger is not.

    The readiness check must trust the live eligible/valid-entry counts over the
    ledger, so a `ready` ledger with an empty projection still falls back.
    """
    st.sql(dbname, "TRUNCATE platform.application_catalog_entries")


def outbox_rows(dbname: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, aggregate_id::text AS aggregate_id, "
        "payload::text AS payload, delivered_at::text AS delivered_at, attempts "
        "FROM platform.outbox ORDER BY available_at, id",
    )


def knowledge_counts(dbname: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in ("knowledge_chunks", "knowledge_index_versions", "knowledge_documents"):
        counts[table] = int(
            st.sql(dbname, f"SELECT count(*) FROM platform.{table}")
        )
    return counts


def seed_knowledge_rows(stack, owner_id: str) -> dict[str, str]:
    """Seed one real knowledge document/chunk/index-version set.

    The rows are ordinary platform rows (no catalog code writes them); T077's
    separation proof is that a catalog rebuild leaves these counts and bytes
    untouched.
    """
    source_artifact = t025.insert_artifact(
        stack.dbname, owner_id, purpose="knowledge_source", sha256=None
    )
    body_artifact = t025.insert_artifact(
        stack.dbname, owner_id, purpose="knowledge_chunk", sha256=None
    )
    base_id = f00b.new_uuid()
    document_id = f00b.new_uuid()
    index_version_id = f00b.new_uuid()
    chunk_id = f00b.new_uuid()
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.knowledge_bases (id, name, owner_id) VALUES ("
        f"{lit(base_id)}::uuid, 't077 knowledge base', {lit(owner_id)}::uuid)",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.knowledge_documents "
        "(id, knowledge_base_id, source_artifact_id, revision, state) VALUES ("
        f"{lit(document_id)}::uuid, {lit(base_id)}::uuid, "
        f"{lit(source_artifact)}::uuid, 2, 'uploaded')",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.knowledge_index_versions "
        "(id, document_id, version, document_revision, embedding_model_revision, "
        " embedding_dimension, state, chunk_count) VALUES ("
        f"{lit(index_version_id)}::uuid, {lit(document_id)}::uuid, 1, 2, "
        "'t077-local-384', 384, 'building', 1)",
    )
    t025.sql_utf8(
        stack.dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, index_version_id) VALUES ("
        f"{lit(chunk_id)}::uuid, {lit(document_id)}::uuid, 2, 0, "
        f"{lit(body_artifact)}::uuid, {jsonb({'chunk_no': 0})}, 't077-local-384', "
        f"{lit(index_version_id)}::uuid)",
    )
    return {
        "document_id": document_id,
        "index_version_id": index_version_id,
        "chunk_id": chunk_id,
    }


def knowledge_signature(dbname: str) -> str:
    """A stable identity of the knowledge projection, hashes included."""
    payload = st.sql(
        dbname,
        "SELECT json_build_object("
        "'chunks', (SELECT count(*) FROM platform.knowledge_chunks), "
        "'index_versions', (SELECT count(*) FROM platform.knowledge_index_versions), "
        "'documents', (SELECT count(*) FROM platform.knowledge_documents), "
        "'chunk_hashes', (SELECT coalesce(json_agg(c.id::text ORDER BY c.id), '[]'::json) "
        "                FROM platform.knowledge_chunks AS c), "
        "'index_states', (SELECT coalesce(json_agg(i.state ORDER BY i.id), '[]'::json) "
        "                 FROM platform.knowledge_index_versions AS i)"
        ")::text",
    )
    return payload


# ---------------------------------------------------------------------------
# a long-lived psql session that holds a table lock (interrupted rebuild)
# ---------------------------------------------------------------------------
class TableLock:
    """Hold one real PostgreSQL lock in a separate client connection.

    The interrupted-rebuild test starts the compiled admin rebuild, waits until
    its ledger says `rebuilding`, then terminates it while it is blocked on this
    lock. The lock session is a real psql client, not a fake.
    """

    def __init__(self, database: str, table: str, *, hold_seconds: int = 120) -> None:
        env = dict(os.environ)
        env["PGPASSWORD"] = st.pg_dev.load_secret()
        self._process = subprocess.Popen(
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
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                f"BEGIN; LOCK TABLE platform.{table} IN ACCESS EXCLUSIVE MODE; "
                f"SELECT pg_sleep({hold_seconds});",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            stdin=subprocess.DEVNULL,
        )
        self._database = database
        self._table = table

    def wait_until_held(self, timeout: float = 60.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            held = st.sql(
                self._database,
                "SELECT count(*) FROM pg_locks AS l JOIN pg_class AS c ON c.oid = l.relation "
                f"WHERE c.relname = '{self._table}' AND l.mode = 'AccessExclusiveLock' "
                "AND l.granted",
            )
            if held != "0":
                return
            if self._process.poll() is not None:
                raise AssertionError("the lock session exited before taking the lock")
            time.sleep(0.1)
        raise AssertionError(f"lock on platform.{self._table} was never granted")

    def release(self) -> None:
        if self._process.poll() is None:
            self._process.kill()
        try:
            self._process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            self._process.terminate()
            self._process.wait(timeout=5)


def wait_until(predicate, *, timeout: float = 30.0, interval: float = 0.1):
    """Poll a cheap authoritative condition instead of sleeping a fixed time."""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = predicate()
        if last:
            return last
        time.sleep(interval)
    return last


def start_rebuild(stack) -> subprocess.Popen[str]:
    """Start the real compiled rebuild as a process the test can terminate."""
    env = st.admin_env(stack.dsn)
    return subprocess.Popen(
        [str(stack.binaries["admin"]), "catalog", "rebuild", "--all"],
        cwd=st.PRODUCT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

