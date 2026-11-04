"""T061 acceptance: migration 0039 separates document/index/chunk revisions.

The index is versioned independently of the source revision: one
``platform.knowledge_index_versions`` row per (document, embedding model
revision) build, an activation pointer on the document, and a chunk identity
scoped by index version (so a rebuild of the same source revision with another
embedding model cannot collide with the old vectors). The migration is additive
to every earlier knowledge table, the chain stays contiguous, re-running the real
migrate is a no-op, and ``orchara_worker`` still gets nothing from the schema.
"""

from __future__ import annotations

import json
import shutil

from support import f00b
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

DOCUMENT_COLUMNS = (
    "id",
    "knowledge_base_id",
    "source_artifact_id",
    "revision",
    "state",
    "active_revision",
    "active_index_id",
    "active_embedding_model_revision",
    "embedding_model_revision",
    "error_code",
)
INDEX_VERSION_COLUMNS = (
    "id",
    "document_id",
    "version",
    "document_revision",
    "embedding_model_revision",
    "embedding_dimension",
    "state",
    "chunk_count",
    "parser_version",
    "chunker_version",
    "error_code",
    "created_at",
    "updated_at",
    "activated_at",
    "superseded_at",
)
CHUNK_COLUMNS = (
    "id",
    "document_id",
    "document_revision",
    "index_version_id",
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


def migration_version(name_fragment: str, dbname: str) -> str:
    names = st.migration_chain(dbname)
    matches = [version for version, name in names.items() if name_fragment in name]
    assert len(matches) == 1, (name_fragment, names)
    return matches[0]


def test_the_migration_chain_keeps_factorised_index_versions(migrated_database) -> None:
    _dsn, dbname = migrated_database
    version = migration_version("index_revisions", dbname)
    # The version number is assigned against the current worktree tip; the test
    # pins the name and asserts the ledger's continuity instead of a literal
    # number, so a merge-time renumber does not silently match the wrong file.
    assert st.migration_chain(dbname)[version] == f"{version}_index_revisions.sql"

    for table, required in (
        ("knowledge_documents", DOCUMENT_COLUMNS),
        ("knowledge_index_versions", INDEX_VERSION_COLUMNS),
        ("knowledge_chunks", CHUNK_COLUMNS),
    ):
        columns = columns_of(dbname, table)
        assert set(required) <= set(columns), (table, sorted(columns))

    documents = columns_of(dbname, "knowledge_documents")
    assert documents["active_index_id"]["is_nullable"] == "YES"
    assert documents["active_embedding_model_revision"]["is_nullable"] == "YES"
    chunks = columns_of(dbname, "knowledge_chunks")
    assert chunks["index_version_id"]["is_nullable"] == "YES"
    jobs = columns_of(dbname, "background_jobs")
    assert jobs["expected_index_version_id"]["is_nullable"] == "YES"

    assert {"document_id"} <= foreign_key_columns(dbname, "knowledge_index_versions")
    assert {"index_version_id"} <= foreign_key_columns(dbname, "knowledge_chunks")
    assert {"active_index_id"} <= foreign_key_columns(dbname, "knowledge_documents")
    assert {"expected_index_version_id"} <= foreign_key_columns(dbname, "background_jobs")


def test_the_chunk_identity_is_the_index_version_and_old_unique_row_identity_is_gone(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    # The old (document_id, document_revision, chunk_no) identity would make a
    # same-revision rebuild under a second embedding model collide with the old
    # vectors. The unique identity is now (index_version_id, chunk_no).
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_indexes WHERE schemaname = 'platform' "
        "AND indexname = 'knowledge_chunks_index_version_no'",
    ) == "1"
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint WHERE conname = 'knowledge_chunks_unique_no'",
    ) == "0"
    # At most one building index per document *per source revision*: a newer
    # revision may start while the older revision's job is still in flight (the
    # activation guard fences it), but the same revision cannot build twice.
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_indexes WHERE schemaname = 'platform' "
        "AND indexname = 'knowledge_index_versions_one_building'",
    ) == "1"
    definition = st.sql(
        dbname,
        "SELECT pg_get_indexdef('platform.knowledge_index_versions_one_building'::regclass)",
    )
    assert "document_revision" in definition, definition
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'knowledge_index_versions_failed_code'",
    ) == "1"
    assert st.sql(
        dbname,
        "SELECT count(*) FROM pg_constraint "
        "WHERE conname = 'knowledge_index_versions_ready_activated'",
    ) == "1"


def test_repeat_migrate_is_a_noop_and_worker_gets_no_new_privileges(
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


def test_the_upgrade_backfills_a_ready_document_into_an_active_index(
    fresh_database, go_binaries, tmp_path
) -> None:
    """A database built before 0039 keeps working: the migration attaches the
    already-readable document/chunks to one active index version instead of
    violating the new ready->active-index invariant."""
    dsn, dbname = fresh_database
    partial = tmp_path / "migrations-before-t061"
    partial.mkdir()
    migrations_dir = st.PRODUCT_ROOT / "db" / "migrations"
    for path in sorted(migrations_dir.glob("*.sql")):
        if "index_revisions" in path.name:
            continue
        shutil.copy(path, partial / path.name)
    completed = st.run_admin(
        go_binaries["admin"], ["migrate", "--migrations-dir", str(partial)], dsn=dsn
    )
    assert completed.returncode == 0, completed.stderr
    assert "index_revisions" not in "".join(st.migration_chain(dbname).values())

    owner = f00b.seed_principal(dbname, "t061-upgrade-owner")
    object_dir = tmp_path / "upgrade-objects"
    object_dir.mkdir()
    source = f00b.seed_artifact(
        dbname,
        object_dir,
        owner,
        content=b"legacy source text\n",
        state="ready",
        sealed_content=b"legacy source text\n",
    )
    body = f00b.seed_artifact(
        dbname,
        object_dir,
        owner,
        content=b"legacy chunk body",
        state="ready",
        sealed_content=b"legacy chunk body",
        purpose="knowledge_chunk",
    )
    base_id = f00b.new_uuid()
    document_id = f00b.new_uuid()
    chunk_id = f00b.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_bases (id, name, owner_id) "
        f"VALUES ({st.quote_literal(base_id)}::uuid, 'upgrade kb', "
        f"{st.quote_literal(owner)}::uuid)",
    )
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_documents "
        "(id, knowledge_base_id, source_artifact_id, revision, state, active_revision, "
        " parser_version, chunker_version, embedding_model_revision) "
        f"VALUES ({st.quote_literal(document_id)}::uuid, {st.quote_literal(base_id)}::uuid, "
        f"{st.quote_literal(source.id)}::uuid, 1, 'ready', 1, "
        "'orchara-text-parser/1', 'orchara-paragraph-overlap-chunker/1', 'local-hash-v1')",
    )
    citation = json.dumps(
        {
            "document_id": document_id,
            "document_revision": 1,
            "chunk_no": 0,
            "source_artifact_id": source.id,
            "source_sha256": "0" * 64,
            "parser_version": "orchara-text-parser/1",
            "chunker_version": "orchara-paragraph-overlap-chunker/1",
            "embedding_model_revision": "local-hash-v1",
        }
    )
    st.sql(
        dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, embedding, embedding_dimension) "
        f"VALUES ({st.quote_literal(chunk_id)}::uuid, "
        f"{st.quote_literal(document_id)}::uuid, 1, 0, "
        f"{st.quote_literal(body.id)}::uuid, {st.quote_literal(citation)}::jsonb, "
        "'local-hash-v1', array_fill(0.0::double precision, ARRAY[384]), 384)",
    )

    # Apply the full chain: only 0039 is pending. It must backfill without
    # violating the new ready->active-index constraint.
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    version = migration_version("index_revisions", dbname)
    assert st.migration_chain(dbname)[version].endswith("_index_revisions.sql")

    row = st.sql_rows(
        dbname,
        "SELECT d.active_index_id::text AS active_index_id, "
        "d.active_embedding_model_revision, iv.version, iv.document_revision, "
        "iv.embedding_model_revision, iv.state, iv.chunk_count, "
        "iv.activated_at::text AS activated_at "
        "FROM platform.knowledge_documents AS d "
        "JOIN platform.knowledge_index_versions AS iv ON iv.id = d.active_index_id "
        f"WHERE d.id = {st.quote_literal(document_id)}::uuid",
    )
    assert len(row) == 1, row
    assert int(row[0]["version"]) == 1
    assert int(row[0]["document_revision"]) == 1
    assert row[0]["embedding_model_revision"] == "local-hash-v1"
    assert row[0]["active_embedding_model_revision"] == "local-hash-v1"
    assert row[0]["state"] == "ready"
    assert int(row[0]["chunk_count"]) == 1
    assert row[0]["activated_at"] is not None
    chunk = st.sql_rows(
        dbname,
        "SELECT index_version_id::text AS index_version_id FROM platform.knowledge_chunks "
        f"WHERE id = {st.quote_literal(chunk_id)}::uuid",
    )
    assert chunk[0]["index_version_id"] == row[0]["active_index_id"]
