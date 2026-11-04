"""T067 acceptance: the PostgreSQL retrieval cache table and its guards.

No Redis exists on this host, so the cache is a PostgreSQL table. It is
rebuildable derived state: the authoritative facts stay in
``knowledge_bases``/``knowledge_documents``/``knowledge_chunks`` and every cache
component is stored so a hit can be re-derived and checked. The table is
additive, the migration chain stays contiguous, re-running the real migrate is a
no-op, and ``orchara_worker`` keeps zero ``platform`` privileges.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    go_binaries,
    migrated_database,
)

CACHE_COLUMNS = (
    "cache_key",
    "principal_id",
    "knowledge_base_id",
    "acl_epoch",
    "kb_revision",
    "source_revision_digest",
    "source_document_count",
    "embedding_model_revision",
    "canonical_query",
    "query_sha256",
    "top_k",
    "item_count",
    "body_bytes",
    "items",
    "created_at",
    "expires_at",
    "last_hit_at",
    "hit_count",
)


def test_the_migration_chain_applies_the_retrieval_cache_table(migrated_database) -> None:
    _dsn, dbname = migrated_database
    names = st.migration_chain(dbname)
    assert names["0037"] == "0037_knowledge_retrieval_cache.sql", names

    columns = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'knowledge_retrieval_cache' "
        "ORDER BY ordinal_position",
    )
    by_name = {row["column_name"]: row for row in columns}
    assert set(CACHE_COLUMNS) <= set(by_name), (
        sorted(set(CACHE_COLUMNS) - set(by_name)),
        sorted(by_name),
    )
    # The body is JSON so the stored component values stay debuggable together
    # with the returned items; the digest is a hex SHA-256.
    assert by_name["items"]["data_type"] == "jsonb"
    assert by_name["source_revision_digest"]["data_type"] == "text"
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'knowledge_retrieval_cache_pkey' "
        "AND conrelid = 'platform.knowledge_retrieval_cache'::regclass",
    ) == "1"


def test_the_cache_is_scoped_to_a_principal_and_a_knowledge_base(migrated_database) -> None:
    _dsn, dbname = migrated_database
    foreign_keys = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        "WHERE c.contype = 'f' "
        "AND c.conrelid = 'platform.knowledge_retrieval_cache'::regclass",
    )
    assert {"principal_id", "knowledge_base_id"} <= {
        row["column_name"] for row in foreign_keys
    }

    # Seed one real principal and knowledge base so the row's own constraints,
    # not a foreign key, are what the database refuses.
    owner_id = st.sql(
        dbname,
        "WITH inserted AS (INSERT INTO platform.principals (id, kind, display_name) "
        "VALUES (gen_random_uuid(), 'user', 't067 cache') RETURNING id) SELECT id FROM inserted",
    )
    base_id = st.sql(
        dbname,
        "WITH inserted AS (INSERT INTO platform.knowledge_bases (id, name, owner_id, revision) "
        f"VALUES (gen_random_uuid(), 't067 cache', {st.quote_literal(owner_id)}::uuid, 1) "
        "RETURNING id) SELECT id FROM inserted",
    )
    columns = (
        "cache_key, principal_id, knowledge_base_id, acl_epoch, kb_revision, "
        "source_revision_digest, source_document_count, embedding_model_revision, "
        "canonical_query, query_sha256, top_k, item_count, body_bytes, items, "
        "created_at, expires_at"
    )
    values = (
        f"repeat('d', 64), {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(base_id)}::uuid, 1, 1, repeat('e', 64), 1, 'local-hash-v1', "
        "'query', repeat('f', 64)"
    )
    # The database itself refuses an out-of-bounds top_k and an entry whose
    # expiry is not after its creation: these bounds are part of the key contract.
    for statement, constraint in (
        (
            "INSERT INTO platform.knowledge_retrieval_cache "
            f"({columns}) VALUES ({values}, 51, 1, 1, '[]'::jsonb, now(), "
            "now() + interval '1 hour')",
            "knowledge_retrieval_cache_top_k_check",
        ),
        (
            "INSERT INTO platform.knowledge_retrieval_cache "
            f"({columns}) VALUES ({values}, 8, 1, 1, '[]'::jsonb, "
            "now(), now() - interval '1 minute')",
            "knowledge_retrieval_cache_expiry_check",
        ),
    ):
        refusal = st.psql(dbname, statement)
        assert refusal.code != 0, refusal.output
        assert constraint in refusal.output, refusal.output


def test_repeat_migrate_is_a_noop_and_the_worker_gets_no_cache_privileges(
    go_binaries, migrated_database
) -> None:
    _dsn, dbname = migrated_database
    before = st.migration_chain(dbname)
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=_dsn)
    assert completed.returncode == 0, completed.stderr
    assert st.migration_chain(dbname) == before
    granted = st.sql(
        dbname,
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    assert granted == "0"
    public = st.sql(
        dbname,
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE grantee = 'PUBLIC' AND table_name = 'knowledge_retrieval_cache'",
    )
    assert public == "0"
