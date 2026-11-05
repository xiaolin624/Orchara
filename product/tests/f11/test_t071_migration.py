"""F11/T071: the purge-support migration.

The ticket adds the one indexable scan path a cache purge needs (the cache
``items`` JSONB array is queried by containment) and nothing else: the deletion
job row, the artifact ``purged_at`` column and the ``delete_content`` kind already
exist. The migration is found by slug, never by a pinned number, so the merger can
renumber it.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_deletion_purge_paths.sql"
INDEX = "knowledge_retrieval_cache_items"


def test_the_t071_migration_is_one_link_of_a_continuous_chain(
    migrated_database, go_binaries
) -> None:
    dsn, dbname = migrated_database
    chain = st.migration_chain(dbname)
    versions = [version for version, name in chain.items() if name.endswith(SLUG)]
    assert len(versions) == 1, chain
    version = versions[0]
    assert chain[version] == version + SLUG, chain[version]

    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "no pending migrations" in (completed.stdout + completed.stderr), completed.stdout
    assert st.migration_chain(dbname) == chain


def test_the_cache_items_gin_index_is_the_purge_scan_path(migrated_database) -> None:
    _dsn, dbname = migrated_database
    rows = st.sql_rows(
        dbname,
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'platform' "
        "AND tablename = 'knowledge_retrieval_cache' "
        f"AND indexname = '{INDEX}'",
    )
    assert len(rows) == 1, rows
    definition = rows[0]["indexdef"]
    assert "USING gin" in definition, definition
    assert "(items" in definition, definition


def test_the_t071_migration_keeps_the_worker_privilege_boundary(migrated_database) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
    allowed = st.sql(
        dbname,
        "SELECT has_table_privilege('orchara_worker', "
        "'platform.knowledge_retrieval_cache', 'SELECT')::text",
    )
    assert allowed == "false", allowed
