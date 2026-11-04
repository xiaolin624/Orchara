"""F10/T055 real-stack helpers: knowledge bases, ingestion and retrieval.

The stack is the T028-shaped real one: a throwaway PostgreSQL database migrated by
the real ``cmd/admin migrate`` chain, the compiled ``cmd/api`` behind real HTTP
with the real filesystem object store and HMAC-signed links, the real internal
mTLS listener, and a real ``cmd/maintenance`` process running the real
``document_ingest`` and ``artifact_finalize`` handlers in two pools.

Nothing here mocks the pipeline under test: bytes move through the existing
signed upload/complete/finalize path, the handler runs in a separate process, and
every state assertion is read either from the public HTTP API or from PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from pathlib import Path

from support import f00b, t024, t026, t028
from support import stack as st

# One stack type for the whole suite (the T028 stack already has every primitive).
T055Stack = t028.T028Stack
Session = t028.Session
admin_session = f00b.admin_session
session_for = f00b.session_for
invite_and_activate = f00b.invite_and_activate
create_service_account = t028.create_service_account
create_api_key = t028.create_api_key
new_uuid = f00b.new_uuid
sha256_hex = t028.sha256_hex
put_link = t024.put_link
artifact_row = t028.artifact_row
object_bytes = t028.object_bytes
write_object = t028.write_object
wait_for_artifact_state = t028.wait_for_artifact_state
finalize_once = t028.finalize_once

PRODUCT_ROOT = st.PRODUCT_ROOT

KIND_DOCUMENT_INGEST = "document_ingest"
KIND_ARTIFACT_FINALIZE = "artifact_finalize"

TEXT = "text/plain"
MARKDOWN = "text/markdown"
CSV = "text/csv"

PURPOSE_KNOWLEDGE = "knowledge"
PURPOSE_CHUNK = "knowledge_chunk"

_LONG_POLICY = "退款政策细则：" + ("退款需在订单完成后 30 天内提交申请，逾期不再受理。" * 80)

SAMPLE_DOCUMENT = (
    "# 销售资料\n"
    "\n"
    "华北地区十月的销售额是 1234.50 元，订单数为 42。\n"
    "\n"
    "华东地区十月的销售额是 987.25 元，订单数为 31。\n"
    "\n"
    "退款政策：订单完成后 30 天内可申请退款。\n"
    "\n"
    + _LONG_POLICY
    + "\n"
)

EMPTY_DOCUMENT = b"   \n\n\t \n"
INVALID_UTF8_DOCUMENT = b"valid start \xff\xfe broken tail\n"


# ---------------------------------------------------------------------------
# HTTP: the contract operations
# ---------------------------------------------------------------------------
def create_knowledge_base(
    session: Session,
    name: str = "t055-knowledge",
    *,
    csrf: bool = True,
    headers: dict[str, str] | None = None,
    client=None,
) -> st.Response:
    http = client or session.client
    return http.post(
        "/api/v1/knowledge-bases",
        body={"name": name},
        csrf=session.csrf if csrf else None,
        headers=headers,
    )


def list_knowledge_bases(
    session: Session, *, limit: int | None = None, cursor: str | None = None, client=None
) -> st.Response:
    http = client or session.client
    query = []
    if limit is not None:
        query.append(f"limit={limit}")
    if cursor is not None:
        query.append("cursor=" + cursor)
    suffix = ("?" + "&".join(query)) if query else ""
    return http.get("/api/v1/knowledge-bases" + suffix)


def get_knowledge_base(session: Session, base_id: str, *, client=None) -> st.Response:
    http = client or session.client
    return http.get("/api/v1/knowledge-bases/" + base_id)


def ingest_document(
    session: Session,
    base_id: str,
    artifact_id: str,
    *,
    csrf: bool = True,
    body: dict | None = None,
    client=None,
) -> st.Response:
    http = client or session.client
    payload = {"source_artifact_id": artifact_id} if body is None else body
    return http.post(
        f"/api/v1/knowledge-bases/{base_id}/documents",
        body=payload,
        csrf=session.csrf if csrf else None,
    )


def list_documents(session: Session, base_id: str, **kwargs) -> st.Response:
    return session.client.get(f"/api/v1/knowledge-bases/{base_id}/documents", **kwargs)


def get_document(session: Session, document_id: str, *, client=None) -> st.Response:
    http = client or session.client
    return http.get("/api/v1/knowledge-documents/" + document_id)


def document_diagnostics(session: Session, document_id: str, *, client=None) -> st.Response:
    http = client or session.client
    return http.get(f"/api/v1/knowledge-documents/{document_id}/diagnostics")


def retrieval_query(
    session: Session,
    base_id: str,
    query: str,
    *,
    top_k: int | None = None,
    csrf: bool = True,
    client=None,
) -> st.Response:
    http = client or session.client
    body: dict = {"query": query}
    if top_k is not None:
        body["top_k"] = top_k
    return http.post(
        f"/api/v1/knowledge-bases/{base_id}/retrieval-queries",
        body=body,
        csrf=session.csrf if csrf else None,
    )


def get_citation(session: Session, chunk_id: str, *, client=None) -> st.Response:
    http = client or session.client
    return http.get("/api/v1/knowledge-citations/" + chunk_id)


# ---------------------------------------------------------------------------
# the existing upload -> finalize -> ready path
# ---------------------------------------------------------------------------
def upload_text_artifact(
    session: Session,
    content: bytes | str,
    *,
    media_type: str = TEXT,
    purpose: str = PURPOSE_KNOWLEDGE,
) -> str:
    """Reserve, PUT and complete one source artifact; return its id (scanning)."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    reserved = t028.create_upload(
        session, media_type=media_type, size_bytes=len(data), purpose=purpose
    )
    assert reserved.status == 201, reserved.text
    ticket = reserved.json
    uploaded = put_link(ticket["upload_url"], data)
    assert uploaded.status == 204, uploaded.status
    completed = t028.complete_upload(session, ticket["artifact_id"], sha256_hex(data))
    assert completed.status == 202, completed.text
    return ticket["artifact_id"]


def ready_text_artifact(
    stack: T055Stack,
    session: Session,
    content: bytes | str,
    *,
    media_type: str = TEXT,
) -> str:
    """Upload and finalize one source artifact through the real pipeline."""
    artifact_id = upload_text_artifact(session, content, media_type=media_type)
    wait_for_artifact_state(session, artifact_id, {"ready"}, timeout=30.0)
    return artifact_id


def seed_ready_source_artifact(
    stack: T055Stack,
    owner_id: str,
    content: bytes,
    *,
    media_type: str = TEXT,
) -> str:
    """Insert a real ready artifact for a state the public upload path refuses.

    Used only for the invalid-UTF-8 case: the T028 admission gate would quarantine
    that content, so the ingestion handler's own decoding refusal could not
    otherwise be exercised. The row and its sealed bytes are real.
    """
    seeded = f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner_id,
        content=content,
        state="ready",
        sealed_content=content,
        media_type=media_type,
        purpose=PURPOSE_KNOWLEDGE,
    )
    return seeded.id


# ---------------------------------------------------------------------------
# the real maintenance process
# ---------------------------------------------------------------------------
def start_ingest_pools(stack: T055Stack, log_path: Path):
    """Start one real maintenance process with both required pools."""
    return t026.start_pools(
        stack,
        work_slots=2,
        system_io_slots=2,
        kinds=KIND_DOCUMENT_INGEST + "," + KIND_ARTIFACT_FINALIZE,
        log_path=log_path,
    )


def stop_ingest_pools(process, handle, log_path: Path) -> str:
    return t026.stop_process(process, handle, log_path)


@contextmanager
def ingest_pools(stack: T055Stack, log_path: Path):
    """Run one real maintenance process (both pools) for the block."""
    process, handle = start_ingest_pools(stack, log_path)
    try:
        yield process
    finally:
        stop_ingest_pools(process, handle, log_path)


def prepare_source(
    stack: T055Stack,
    session: Session,
    content: bytes | str,
    *,
    media_type: str = TEXT,
) -> str:
    """Upload + finalize one source artifact through the real pipeline."""
    artifact_id = upload_text_artifact(session, content, media_type=media_type)
    finalize_once(stack)
    wait_for_artifact_state(session, artifact_id, {"ready"})
    return artifact_id


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def knowledge_base_row(dbname: str, base_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, name, owner_id::text AS owner_id, revision, "
        "deleted_at::text AS deleted_at, created_at::text AS created_at "
        "FROM platform.knowledge_bases "
        f"WHERE id = {st.quote_literal(base_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def document_row(dbname: str, document_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, knowledge_base_id::text AS knowledge_base_id, "
        "source_artifact_id::text AS source_artifact_id, revision, state, "
        "active_revision, parser_version, chunker_version, embedding_model_revision, "
        "error_code, deleted_at::text AS deleted_at, created_at::text AS created_at "
        "FROM platform.knowledge_documents "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def documents_for_artifact(dbname: str, artifact_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, revision, state, error_code "
        "FROM platform.knowledge_documents "
        f"WHERE source_artifact_id = {st.quote_literal(artifact_id)}::uuid "
        "ORDER BY revision",
    )


def chunk_rows(dbname: str, document_id: str, revision: int | None = None) -> list[dict]:
    where = f"document_id = {st.quote_literal(document_id)}::uuid"
    if revision is not None:
        where += f" AND document_revision = {int(revision)}"
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, document_id::text AS document_id, document_revision, "
        "chunk_no, body_artifact_id::text AS body_artifact_id, citation::text AS citation, "
        "embedding_model_revision, embedding_dimension, "
        "coalesce(array_length(embedding, 1), 0) AS embedding_length "
        "FROM platform.knowledge_chunks "
        f"WHERE {where} ORDER BY chunk_no",
    )


def chunk_count(dbname: str, document_id: str, revision: int | None = None) -> int:
    return len(chunk_rows(dbname, document_id, revision))


def document_jobs(dbname: str, document_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, error_code, failure_class, attempt_count, "
        "expected_revision, input_artifact_id::text AS input_artifact_id, "
        "dedup_key, finished_at::text AS finished_at "
        "FROM platform.background_jobs "
        "WHERE kind = 'document_ingest' "
        f"AND target_id = {st.quote_literal(document_id)}::uuid "
        "ORDER BY created_at, id",
    )


def artifact_state(dbname: str, artifact_id: str) -> dict:
    return artifact_row(dbname, artifact_id)


def citation_of(chunk: dict) -> dict:
    return json.loads(chunk["citation"])


def object_text(object_dir: Path, artifact: dict) -> str:
    """Read one artifact's sealed bytes as text (the test's own truth read)."""
    return object_bytes(object_dir, artifact["object_key"]).decode("utf-8")


# ---------------------------------------------------------------------------
# bounded waits
# ---------------------------------------------------------------------------
def wait_for_document_state(
    session: Session,
    document_id: str,
    states: set[str],
    *,
    timeout: float = 60.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = get_document(session, document_id)
        if response.status == 200:
            last = response.json
            if last.get("state") in states:
                return last
        time.sleep(0.05)
    raise AssertionError(f"document {document_id} never reached {states}: {last}")


def wait_for_artifact(dbname: str, artifact_id: str, states: set[str], *, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = artifact_state(dbname, artifact_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"artifact {artifact_id} never reached {states}: {last}")


def wait_until(predicate, *, timeout: float = 60.0, interval: float = 0.05, what: str) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last error: {last}")
