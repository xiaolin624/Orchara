"""F08/T077: the catalog-index migration.

Every assertion reads the real catalogue of a database migrated by the compiled
``cmd/admin`` chain. The chain invariant is continuity, never a pinned tail; this
ticket finds its own migration by slug so the merger can renumber it (the
orchestrator pre-allocated 0051; the worktree chain is contiguous at 0045, so the
merger renumbers this file's number and nothing in the test pins it).
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_catalog_index.sql"


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default, udt_name "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    return " ".join(
        row["definition"]
        for row in st.sql_rows(
            dbname,
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            f"WHERE conrelid = 'platform.{table}'::regclass",
        )
    )


def index_definitions(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'platform' "
        f"AND tablename = '{table}'",
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


def test_the_t077_migration_is_one_link_of_a_continuous_chain(
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


def test_the_catalog_projection_and_ledger_tables_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for table in ("application_catalog_entries", "catalog_index_state"):
        assert st.sql(
            dbname, f"SELECT to_regclass('platform.{table}')::text"
        ) == "platform." + table, table

    entries = columns_of(dbname, "application_catalog_entries")
    for name in (
        "application_version_id",
        "search_text",
        "source_manifest_sha256",
        "updated_at",
        "application_id",
        "ts",
    ):
        assert name in entries, (name, entries)
    assert entries["application_version_id"]["is_nullable"] == "NO"
    assert entries["search_text"]["is_nullable"] == "NO"
    assert entries["source_manifest_sha256"]["is_nullable"] == "NO"
    assert entries["application_id"]["is_nullable"] == "NO"
    assert entries["ts"]["udt_name"] == "tsvector", entries["ts"]
    assert "now()" in (entries["updated_at"]["column_default"] or "")

    entry_indexes = index_definitions(dbname, "application_catalog_entries")
    assert any("gin" in definition.lower() for definition in entry_indexes.values()), (
        entry_indexes
    )
    assert any(
        "source_manifest_sha256" in definition for definition in entry_indexes.values()
    ), entry_indexes

    state = columns_of(dbname, "catalog_index_state")
    for name in (
        "id",
        "state",
        "projection_epoch",
        "rebuilt_at",
        "rebuild_started_at",
        "last_error_code",
        "last_error_at",
        "expected_count",
        "indexed_count",
        "updated_at",
    ):
        assert name in state, (name, state)
    assert state["id"]["is_nullable"] == "NO"
    definitions = constraint_definitions(dbname, "catalog_index_state")
    assert "'catalog'" in definitions, definitions
    for value in ("ready", "rebuilding", "failed"):
        assert "'" + value + "'" in definitions, definitions


def test_the_catalog_migration_keeps_the_worker_privilege_boundary(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    for table in ("application_catalog_entries", "catalog_index_state"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            allowed = st.sql(
                dbname,
                f"SELECT has_table_privilege('orchara_worker', 'platform.{table}', "
                f"'{privilege}')::text",
            )
            assert allowed == "false", (table, privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
