"""T055 acceptance: migration 0033 creates the knowledge tables with their seams.

The ticket's deviation is explicit: pgvector is not available in this cluster, so
the embedding is a 384-dimension ``double precision[]`` with a CHECK, and the
contract's ``vector`` type is not used. The migration is additive, the chain stays
contiguous, re-running the real migrate is a no-op, and ``orchara_worker`` still
gets nothing from the ``platform`` schema.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    go_binaries,
    migrated_database,
)

KNOWLEDGE_BASE_COLUMNS = ("id", "name", "owner_id", "revision", "deleted_at")
DOCUMENT_COLUMNS = (
    "id",
    "knowledge_base_id",
    "source_artifact_id",
    "revision",
    "state",
    "active_revision",
    "parser_version",
    "chunker_version",
    "embedding_model_revision",
    "error_code",
    "created_at",
)
CHUNK_COLUMNS = (
    "id",
    "document_id",
    "document_revision",
    "chunk_no",
    "body_artifact_id",
    "citation",
    "embedding_model_revision",
    "embedding",
    "embedding_dimension",
)


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def foreign_key_columns(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"] for row in rows}


def test_the_migration_chain_keeps_0033_and_the_three_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    names = st.migration_chain(dbname)
    assert names["0033"] == "0033_knowledge.sql"
    # Contiguity: one version per number, no gap, up to the last applied one.
    for offset, version in enumerate(sorted(names)):
        assert version == f"{offset + 1:04d}", (version, names)

    for table, required in (
        ("knowledge_bases", KNOWLEDGE_BASE_COLUMNS),
        ("knowledge_documents", DOCUMENT_COLUMNS),
        ("knowledge_chunks", CHUNK_COLUMNS),
    ):
        columns = columns_of(dbname, table)
        assert set(required) <= set(columns), (table, sorted(columns))
    # The matrix is a real array, not a vector column, and there is no pgvector
    # dependency in the installed extension set.
    assert "ARRAY" in columns_of(dbname, "knowledge_chunks")["embedding"]["data_type"]
    assert columns_of(dbname, "knowledge_chunks")["embedding"]["is_nullable"] == "YES"
    assert st.sql(dbname, "SELECT count(*) FROM pg_extension WHERE extname = 'vector'") == "0"


def test_foreign_keys_are_real_and_the_kind_check_still_admits_document_ingest(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    assert {"knowledge_base_id", "source_artifact_id"} <= foreign_key_columns(
        dbname, "knowledge_documents"
    )
    assert {"document_id", "body_artifact_id"} <= foreign_key_columns(dbname, "knowledge_chunks")
    assert "owner_id" in foreign_key_columns(dbname, "knowledge_bases")
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint WHERE conname = 'background_jobs_kind_check' "
        "AND pg_get_constraintdef(oid) LIKE '%document_ingest%'",
    ) == "1"


def test_unique_chunk_number_per_revision_and_the_dimension_check(migrated_database) -> None:
    _dsn, dbname = migrated_database
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint WHERE conname = 'knowledge_chunks_unique_no'",
    ) == "1"
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'knowledge_chunks_embedding_dimension_local'",
    ) == "1"
    # A wrong-dimension embedding is refused by the database itself, so the Go
    # constant cannot silently disagree with the migration.
    refusal = st.psql(
        dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, embedding, embedding_dimension) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 1, 0, gen_random_uuid(), '{}'::jsonb, "
        "'local-hash-v1', '{1,2,3}'::double precision[], 3)",
    )
    assert refusal.code != 0, refusal.output
    assert "knowledge_chunks_embedding_dimension_local" in refusal.output


def test_repeat_migrate_is_a_noop_and_worker_gets_no_platform_privileges(go_binaries, migrated_database) -> None:
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
