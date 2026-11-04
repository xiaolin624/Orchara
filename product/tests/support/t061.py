"""F10/T061 real-stack helpers: versioned knowledge indexes and rebuilds.

The index lifecycle is exercised through the real stack only: PostgreSQL rows
(migrated by the real ``cmd/admin`` chain), the compiled ``cmd/api`` behind real
HTTP, the compiled ``cmd/maintenance`` running the real ``document_ingest`` and
``artifact_finalize`` handlers, and real sealed chunk bodies. The tests read
``platform.knowledge_index_versions`` / ``knowledge_chunks`` directly as the
independent truth; the API is never its own oracle.

Nothing here mocks the activation guard: the "late job" race parks a real
maintenance process group by running only the work pool (the handler then waits
for chunk artifacts that only the withheld system_io pool can finalize), makes the
new revision happen through the real reindex endpoint, and only then releases the
finalize pool so the old handler really finishes after the revision moved.
"""

from __future__ import annotations

import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from support import f00b, t024, t026, t055
from support import stack as st

EMBEDDING_REVISION_V1 = "local-hash-v1"
EMBEDDING_REVISION_V2 = "local-hash-v2"
DIMENSION = 384

TEXT = "text/plain"
PURPOSE_CHUNK = "knowledge_chunk"
KIND_DOCUMENT_INGEST = "document_ingest"
KIND_ARTIFACT_FINALIZE = "artifact_finalize"

# Environment for a second real deployment that produces the other documented
# deterministic stub revision (a real configuration change, not a mock).
STUB_V2_ENV = {
    "ORCHARA_EMBEDDING_PROVIDER": "stub",
    "ORCHARA_EMBEDDING_MODEL": EMBEDDING_REVISION_V2,
}
# The real provider path fails closed in this build (no adapter/credential); it is
# used to prove a failed rebuild keeps the previous active index intact.
REAL_PROVIDER_ENV = {
    "ORCHARA_EMBEDDING_PROVIDER": "openai",
    "ORCHARA_EMBEDDING_MODEL": "text-embedding-3-small",
}


# ---------------------------------------------------------------------------
# HTTP: the T061 non-contract rebuild entry
# ---------------------------------------------------------------------------
def reindex_path(document_id: str) -> str:
    return "/api/v1/knowledge-documents/" + _encode(document_id) + "/reindex"


def _encode(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")


def reindex(
    session: f00b.Session,
    document_id: str,
    *,
    source_artifact_id: str | None = None,
    csrf: bool = True,
    body: dict | None = None,
    client=None,
) -> st.Response:
    http = client or session.client
    if body is None:
        body = {}
        if source_artifact_id is not None:
            body["source_artifact_id"] = source_artifact_id
    return http.post(
        reindex_path(document_id),
        body=body,
        csrf=session.csrf if csrf else None,
    )


# ---------------------------------------------------------------------------
# database truth
# ---------------------------------------------------------------------------
def index_rows(dbname: str, document_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, document_id::text AS document_id, version, "
        "document_revision, embedding_model_revision, embedding_dimension, state, "
        "chunk_count, error_code, parser_version, chunker_version, "
        "activated_at::text AS activated_at, superseded_at::text AS superseded_at, "
        "created_at::text AS created_at, updated_at::text AS updated_at "
        "FROM platform.knowledge_index_versions "
        f"WHERE document_id = {st.quote_literal(document_id)}::uuid "
        "ORDER BY version",
    )


def active_index(dbname: str, document_id: str) -> dict | None:
    rows = st.sql_rows(
        dbname,
        "SELECT iv.id::text AS id, iv.version, iv.document_revision, "
        "iv.embedding_model_revision, iv.embedding_dimension, iv.state, "
        "iv.chunk_count, iv.error_code, iv.activated_at::text AS activated_at "
        "FROM platform.knowledge_documents AS d "
        "JOIN platform.knowledge_index_versions AS iv ON iv.id = d.active_index_id "
        f"WHERE d.id = {st.quote_literal(document_id)}::uuid",
    )
    return rows[0] if rows else None


def index_by_version(dbname: str, document_id: str, version: int) -> dict:
    rows = [row for row in index_rows(dbname, document_id) if int(row["version"]) == version]
    assert len(rows) == 1, (version, rows)
    return rows[0]


def chunk_rows_for_index(dbname: str, index_version_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, document_id::text AS document_id, document_revision, "
        "index_version_id::text AS index_version_id, chunk_no, "
        "body_artifact_id::text AS body_artifact_id, citation::text AS citation, "
        "embedding_model_revision, embedding_dimension, "
        "coalesce(array_length(embedding, 1), 0) AS embedding_length "
        "FROM platform.knowledge_chunks "
        f"WHERE index_version_id = {st.quote_literal(index_version_id)}::uuid "
        "ORDER BY chunk_no",
    )


def unscoped_chunk_count(dbname: str, document_id: str) -> int:
    rows = st.sql_rows(
        dbname,
        "SELECT count(*) AS n FROM platform.knowledge_chunks "
        f"WHERE document_id = {st.quote_literal(document_id)}::uuid",
    )
    return int(rows[0]["n"])


def document_jobs(dbname: str, document_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, error_code, failure_class, attempt_count, "
        "expected_revision, expected_index_version_id::text AS expected_index_version_id, "
        "input_artifact_id::text AS input_artifact_id, dedup_key, "
        "finished_at::text AS finished_at "
        "FROM platform.background_jobs "
        "WHERE kind = 'document_ingest' "
        f"AND target_id = {st.quote_literal(document_id)}::uuid "
        "ORDER BY created_at, id",
    )


def job_for_index(dbname: str, document_id: str, index_version_id: str) -> dict:
    jobs = [
        job
        for job in document_jobs(dbname, document_id)
        if job["expected_index_version_id"] == index_version_id
    ]
    assert len(jobs) == 1, (index_version_id, jobs)
    return jobs[0]


def scanning_chunk_artifacts(dbname: str, owner_id: str) -> int:
    rows = st.sql_rows(
        dbname,
        "SELECT count(*) AS n FROM platform.artifacts "
        f"WHERE owner_id = {st.quote_literal(owner_id)}::uuid "
        f"AND purpose = {st.quote_literal(PURPOSE_CHUNK)} AND state = 'scanning'",
    )
    return int(rows[0]["n"])


# ---------------------------------------------------------------------------
# a ready document through the real pipeline
# ---------------------------------------------------------------------------
def setup_ready_document(
    stack,
    *,
    log_name: str,
    login: str = "t061.alice",
    content: bytes | str = t055.SAMPLE_DOCUMENT,
):
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(stack, admin, login, password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(owner, login + " base").json["id"]
    artifact_id = t055.prepare_source(stack, owner, content)
    log_path = stack.server.log_path.parent / f"maintenance-t061-{log_name}.log"
    with t055.ingest_pools(stack, log_path):
        accepted = t055.ingest_document(owner, base_id, artifact_id)
        assert accepted.status == 202, accepted.text
        document_id = accepted.json["id"]
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
        assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    return owner, base_id, artifact_id, document_id


def wait_for_index_state(
    dbname: str,
    document_id: str,
    version: int,
    states: set[str],
    *,
    timeout: float = 60.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = index_by_version(dbname, document_id, version)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"index v{version} never reached {states}: {last}")


def wait_for_parked_handler(
    dbname: str, owner_id: str, *, timeout: float = 90.0
) -> int:
    """Block until a work-only maintenance handler has started uploading chunks.

    The whole chunk set is not required: the handler cannot finish without the
    withheld artifact_finalize pool, so seeing its first staged body proves it is
    in flight and parked. The test then moves the revision/delete and only starts
    the finalize pool afterwards, which is the real "late job" race.
    """
    deadline = time.time() + timeout
    last = 0
    while time.time() < deadline:
        last = scanning_chunk_artifacts(dbname, owner_id)
        if last >= 1:
            return last
        time.sleep(0.05)
    raise AssertionError(f"the parked handler never uploaded a scanning chunk artifact ({last})")


# ---------------------------------------------------------------------------
# direct activation switch used by the T067 cache test
# ---------------------------------------------------------------------------
def activate_staged_index_version(
    dbname: str, document_id: str, *, document_revision: int
) -> str:
    """Write the exact rows the T061 guard writes when it activates an index.

    Used by the T067 cache test, whose subject is cache invalidation on a
    revision/activation change (not the activation guard again): it copies the
    current active chunk set to a new index version for ``document_revision``,
    moves the document pointer and supersedes the old version. It is real SQL
    against the real rows, not a mocked activation.
    """
    rows = st.sql_rows(
        dbname,
        "SELECT d.active_index_id::text AS active_index_id, iv.version, "
        "iv.embedding_model_revision, iv.embedding_dimension "
        "FROM platform.knowledge_documents AS d "
        "JOIN platform.knowledge_index_versions AS iv ON iv.id = d.active_index_id "
        f"WHERE d.id = {st.quote_literal(document_id)}::uuid",
    )
    assert len(rows) == 1, rows
    previous = rows[0]
    new_id = f00b.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_index_versions "
        "(id, document_id, version, document_revision, embedding_model_revision, "
        " embedding_dimension, state, chunk_count, activated_at, created_at, updated_at) "
        f"VALUES ({st.quote_literal(new_id)}::uuid, "
        f"{st.quote_literal(document_id)}::uuid, {int(previous['version']) + 1}, "
        f"{int(document_revision)}, {st.quote_literal(previous['embedding_model_revision'])}, "
        f"{int(previous['embedding_dimension'])}, 'ready', "
        "(SELECT count(*) FROM platform.knowledge_chunks "
        f" WHERE index_version_id = {st.quote_literal(previous['active_index_id'])}::uuid), "
        "now(), now(), now())",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, index_version_id, chunk_no, "
        " body_artifact_id, citation, embedding_model_revision, embedding, embedding_dimension) "
        "SELECT gen_random_uuid(), document_id, "
        f"{int(document_revision)}, {st.quote_literal(new_id)}::uuid, chunk_no, "
        "body_artifact_id, citation || jsonb_build_object('document_revision', "
        f"{int(document_revision)}), embedding_model_revision, embedding, embedding_dimension "
        "FROM platform.knowledge_chunks "
        f"WHERE index_version_id = {st.quote_literal(previous['active_index_id'])}::uuid",
    )
    st.sql(
        dbname,
        "UPDATE platform.knowledge_index_versions SET state = 'superseded', "
        f"superseded_at = now() WHERE id = {st.quote_literal(previous['active_index_id'])}::uuid",
    )
    st.sql(
        dbname,
        "UPDATE platform.knowledge_documents SET "
        f"revision = {int(document_revision)}, active_revision = {int(document_revision)}, "
        f"active_index_id = {st.quote_literal(new_id)}::uuid, "
        "active_embedding_model_revision = "
        f"{st.quote_literal(previous['embedding_model_revision'])}, updated_at = now() "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    return new_id


# ---------------------------------------------------------------------------
# extra real processes: a second API deployment + its maintenance pools
# ---------------------------------------------------------------------------
@contextmanager
def server_with_env(api_server_factory, stack, directory: Path, *, extra: dict[str, str]):
    """One extra real API process over the same DB/object store (deployment change)."""
    server, certificates = t024.start_second_api(
        api_server_factory,
        stack,
        directory,
        signing_key=stack.signing_key,
        extra=extra,
    )
    try:
        yield server, certificates
    finally:
        server.stop()


@contextmanager
def maintenance_pools(
    stack,
    certificates,
    *,
    kinds: str,
    log_path: Path,
    work_slots: int = 2,
    system_io_slots: int = 2,
    extra_env: dict[str, str] | None = None,
):
    """A real ``cmd/maintenance run`` bound to a specific API process's mTLS certs."""
    env = f00b.maintenance_env(stack, extra=extra_env)
    client = certificates.client("maintenance")
    env["ORCHARA_INTERNAL_URL"] = certificates.internal_url
    env["ORCHARA_INTERNAL_CLIENT_CERT"] = str(client.cert)
    env["ORCHARA_INTERNAL_CLIENT_KEY"] = str(client.key)
    env["ORCHARA_INTERNAL_CA"] = str(certificates.ca.cert)
    handle = log_path.open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            *t026.pool_args(
                work_slots=work_slots, system_io_slots=system_io_slots, kinds=kinds
            ),
        ],
        cwd=st.PRODUCT_ROOT,
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:  # pragma: no cover - teardown must not mask
            process.kill()
            process.wait(timeout=5)
        handle.flush()
        handle.close()
