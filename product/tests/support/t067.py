"""F10/T067 real-stack helpers: the PostgreSQL retrieval cache.

The cache lives in PostgreSQL (there is no Redis on this host). These helpers let
a test read the real cache rows, corrupt an entry while keeping it under the
*current* key (the "eviction was lost and the row is still live" case), and start
a second real API process with the cache disabled for the comparison run.

Nothing here mocks the endpoint or the validation: every assertion goes through
real HTTP and the real ``platform.knowledge_retrieval_cache`` rows.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

from support import f00b, t024, t055, t064
from support import stack as st

CACHE_ENV = "ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE"
CACHE_TABLE = "platform.knowledge_retrieval_cache"

# Marks a body that must never come back from a poisoned cache row.
POISON_BODY = "T067-POISONED-CACHED-BODY-MUST-NEVER-BE-SERVED"

EMBEDDING_REVISION = "local-hash-v1"


# ---------------------------------------------------------------------------
# a knowledge base with several real ingested documents
# ---------------------------------------------------------------------------
def setup_kb_with_documents(
    stack,
    log_name: str,
    *,
    login: str = "t067.alice",
    contents: list[bytes],
) -> tuple[f00b.Session, str, list[str], list[str]]:
    """One owner, one KB, N documents that reached ready through the real jobs."""
    admin = f00b.admin_session(stack)
    owner = f00b.invite_and_activate(stack, admin, login, password=f00b.USER_PASSWORD)
    base_id = t055.create_knowledge_base(owner, login + " base").json["id"]
    artifact_ids = [t055.prepare_source(stack, owner, content) for content in contents]
    log_path = stack.server.log_path.parent / f"maintenance-t067-{log_name}.log"
    document_ids: list[str] = []
    with t055.ingest_pools(stack, log_path):
        for artifact_id in artifact_ids:
            accepted = t055.ingest_document(owner, base_id, artifact_id)
            assert accepted.status == 202, accepted.text
            document_ids.append(accepted.json["id"])
        for document_id in document_ids:
            view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
            assert view["state"] == "ready", (
                view,
                t055.document_row(stack.dbname, document_id),
            )
    return owner, base_id, artifact_ids, document_ids


# ---------------------------------------------------------------------------
# cache rows (the authoritative view for the test)
# ---------------------------------------------------------------------------
def cache_rows(
    dbname: str,
    *,
    principal_id: str | None = None,
    base_id: str | None = None,
    key_prefix: str | None = None,
) -> list[dict]:
    where = []
    if principal_id is not None:
        where.append(f"principal_id = {st.quote_literal(principal_id)}::uuid")
    if base_id is not None:
        where.append(f"knowledge_base_id = {st.quote_literal(base_id)}::uuid")
    if key_prefix is not None:
        where.append(f"cache_key LIKE {st.quote_literal(key_prefix + '%')}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = st.sql_rows(
        dbname,
        "SELECT cache_key, principal_id::text AS principal_id, "
        "knowledge_base_id::text AS knowledge_base_id, acl_epoch, kb_revision, "
        "source_revision_digest, source_document_count, embedding_model_revision, "
        "canonical_query, query_sha256, top_k, item_count, body_bytes, "
        "items::text AS items, created_at::text AS created_at, "
        "expires_at::text AS expires_at, last_hit_at::text AS last_hit_at, hit_count "
        f"FROM {CACHE_TABLE}{clause} "
        "ORDER BY created_at DESC, cache_key DESC",
    )
    for row in rows:
        row["items"] = json.loads(row["items"])
    return rows


def cache_row_with_prefix(dbname: str, key_prefix: str) -> dict:
    rows = cache_rows(dbname, key_prefix=key_prefix)
    assert len(rows) == 1, (key_prefix, rows)
    return rows[0]


def cache_row_by_key(dbname: str, cache_key: str) -> dict | None:
    rows = cache_rows(dbname, key_prefix=cache_key)
    matching = [row for row in rows if row["cache_key"] == cache_key]
    return matching[0] if matching else None


def cache_count(dbname: str, *, principal_id: str | None = None) -> int:
    return len(cache_rows(dbname, principal_id=principal_id))


def expire_cache_row(dbname: str, cache_key: str) -> None:
    """Make one live row expired without deleting it (TTL is not authorization)."""
    st.sql(
        dbname,
        f"UPDATE {CACHE_TABLE} SET created_at = now() - interval '2 hours', "
        f"expires_at = now() - interval '1 hour' "
        f"WHERE cache_key = {st.quote_literal(cache_key)}",
    )


def delete_cache_rows(dbname: str, *, principal_id: str | None = None) -> None:
    clause = (
        f" WHERE principal_id = {st.quote_literal(principal_id)}::uuid"
        if principal_id is not None
        else ""
    )
    st.sql(dbname, f"DELETE FROM {CACHE_TABLE}{clause}")


# ---------------------------------------------------------------------------
# stored items and live-row corruption
# ---------------------------------------------------------------------------
def stored_item(dbname: str, item: dict) -> dict:
    """The exact stored-item shape for one retrieval response item.

    The body artifact id/hash are read from PostgreSQL and the sealed object row,
    not trusted from the response, so the stored item is the real provenance.
    """
    chunk = t064.chunk_row(dbname, item["chunk_id"])
    artifact = t055.artifact_row(dbname, chunk["body_artifact_id"])
    return {
        "chunk_id": item["chunk_id"],
        "document_id": item["document_id"],
        "document_revision": int(item["document_revision"]),
        "chunk_no": int(item["chunk_no"]),
        "span": item["span"],
        "citation": item["citation"],
        "body_artifact_id": chunk["body_artifact_id"],
        "body_sha256": artifact["sha256"],
        "text": item["text"],
        "score": float(item["score"]),
    }


def replace_entry_items(dbname: str, cache_key: str, items: list[dict]) -> None:
    """Overwrite a live row's items under its *current* key.

    This is the lost-eviction adversary: the entry is still live at the key the
    server will recompute now, but it claims content that has since been deleted,
    revoked or belongs to another principal. Only the mandatory hit-time re-check
    can stop it from being returned.
    """
    body_bytes = sum(len(item["text"].encode("utf-8")) + len(json.dumps(item["citation"])) for item in items)
    # ensure_ascii keeps the SQL literal ASCII: the psql client on this host can
    # have a non-UTF8 client_encoding, while jsonb decodes \uXXXX escapes itself.
    encoded = json.dumps(items, ensure_ascii=True)
    st.sql(
        dbname,
        f"UPDATE {CACHE_TABLE} SET items = {st.quote_literal(encoded)}::jsonb, "
        f"item_count = {len(items)}, body_bytes = {body_bytes} "
        f"WHERE cache_key = {st.quote_literal(cache_key)}",
    )


def poison_live_entry(
    dbname: str,
    *,
    key_prefix: str,
    stale_item: dict,
    keep_items: list[dict],
    poison_text: str = POISON_BODY,
) -> tuple[str, list[dict]]:
    """Replace a live entry's items with its own plus one stale item.

    ``keep_items`` is the entry's current real item list (from the cache row);
    ``stale_item`` is the deleted/revoked/foreign item to inject at the front with
    a poison body. Returns ``(cache_key, injected_items)``.
    """
    row = cache_row_with_prefix(dbname, key_prefix)
    poisoned = dict(stale_item)
    poisoned["text"] = poison_text
    items = [poisoned] + list(keep_items or row["items"])
    replace_entry_items(dbname, row["cache_key"], items)
    return row["cache_key"], items


def warm_cache(
    session: f00b.Session, base_id: str, query: str, *, top_k: int
) -> st.Response:
    """One real request that stores the entry (asserts the response is a store)."""
    response = t055.retrieval_query(session, base_id, query, top_k=top_k)
    assert response.status == 200, response.text
    cache = response.json["retrieval"]["cache"]
    assert cache["enabled"] is True, cache
    assert cache["hit"] is False, cache
    return response


# ---------------------------------------------------------------------------
# extra real API processes (comparison run / documented dev-stub faults)
# ---------------------------------------------------------------------------
@contextmanager
def server_with_env(
    api_server_factory,
    stack,
    directory: Path,
    *,
    cache: str | None = None,
    faults: str | None = None,
):
    """Start one extra real API process over the same database and object store.

    ``cache``/``faults`` are the exact deployment values of
    ``ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE`` / ``ORCHARA_KNOWLEDGE_ANSWER_FAULTS``.
    Every process is real: this is a deployment difference, not a mock.
    """
    extra: dict[str, str] = {}
    if cache is not None:
        extra[CACHE_ENV] = cache
    if faults is not None:
        extra[t064.ANSWER_FAULTS_ENV] = faults
    server, _certificates = t024.start_second_api(
        api_server_factory,
        stack,
        directory,
        signing_key=stack.signing_key,
        extra=extra,
    )
    try:
        yield server
    finally:
        server.stop()


@contextmanager
def cache_disabled_server(api_server_factory, stack, directory: Path):
    """Start one extra real API process with ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=off.

    Same database and object store as the primary server; only the deployment
    switch differs, so a cache-on/cache-off comparison is two real processes.
    """
    with server_with_env(api_server_factory, stack, directory, cache="off") as server:
        yield server
