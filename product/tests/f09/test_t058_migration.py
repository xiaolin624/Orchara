"""F09/T058: the migration adds the contract deletion_jobs table.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` chain. The chain invariant is continuity (0001..NNNN, no
gap), never a pinned tail: a concurrent ticket appends its own migration and the
merger renumbers, so this ticket finds its own migration by slug and checks the
table it adds.

T058 adds no column to ``platform.memories``: the strong ETag of a memory is the
existing ``revision`` and the tombstone is the contract ``deleted_at``, so a
memory row written by T054 is already addressable by this ticket without a
backfill. The new table is the contract's ``deletion_jobs`` (a minimal slice of
the F07 deletion model; the F07 debt is recorded in the migration header and the
ADR).
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_memory_revision_and_deletion.sql"
DELETION_JOBS = "deletion_jobs"
MEMORY_COLUMNS = {
    "id", "owner_id", "application_id", "body_artifact_id", "candidate_id",
    "revision", "confirmed_at", "confirmed_by", "deleted_at",
}


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def foreign_keys_of(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name, confrelid::regclass::text AS target "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"]: row["target"] for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = ANY(ARRAY['c','u','p'])",
    )
    return " ".join(row["definition"] for row in rows)


def test_the_t058_migration_is_one_link_of_a_continuous_chain(
    migrated_database, go_binaries
) -> None:
    dsn, dbname = migrated_database
    # migration_chain asserts the 0001..NNNN continuity invariant itself.
    chain = st.migration_chain(dbname)
    versions = [version for version, name in chain.items() if name.endswith(SLUG)]
    assert len(versions) == 1, chain
    version = versions[0]
    assert chain[version] == version + SLUG, chain[version]

    # Repeating migrate on the same database is a no-op, not a second apply.
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "no pending migrations" in (completed.stdout + completed.stderr), completed.stdout
    assert st.migration_chain(dbname) == chain


def test_deletion_jobs_has_the_contract_columns_and_keys(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, DELETION_JOBS)
    assert set(columns) == {
        "id", "owner_id", "resource_type", "resource_id", "deletion_revision",
        "state", "remaining_scopes", "created_at", "completed_at",
    }, columns
    for name in (
        "id", "owner_id", "resource_type", "resource_id", "deletion_revision",
        "state", "remaining_scopes", "created_at",
    ):
        assert columns[name]["is_nullable"] == "NO", (name, columns[name])
    assert columns["completed_at"]["is_nullable"] == "YES", columns["completed_at"]
    assert columns["remaining_scopes"]["data_type"] == "jsonb", columns["remaining_scopes"]
    assert "now()" in (columns["created_at"]["column_default"] or ""), columns["created_at"]

    assert st.sql(dbname, "SELECT count(*) FROM platform.deletion_jobs") == "0"

    foreign_keys = foreign_keys_of(dbname, DELETION_JOBS)
    assert foreign_keys["owner_id"] == "platform.principals", foreign_keys

    definitions = constraint_definitions(dbname, DELETION_JOBS)
    for state in ("pending", "running", "completed", "failed"):
        assert "'" + state + "'" in definitions, definitions
    assert "jsonb_typeof(remaining_scopes) = 'array'" in definitions, definitions
    assert "UNIQUE (resource_type, resource_id, deletion_revision)" in definitions, definitions


def test_the_memory_row_keeps_t054_shape_so_revision_and_deleted_at_are_the_etag_seam(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    # T058 adds no memory column: the strong ETag is `revision` (T054 started it
    # at 1) and the tombstone is the contract `deleted_at`.
    assert set(columns_of(dbname, "memories")) == MEMORY_COLUMNS


def test_the_deletion_table_keeps_the_worker_privilege_boundary(migrated_database) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return  # a deployment without the worker role has no boundary to check
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        allowed = st.sql(
            dbname,
            f"SELECT has_table_privilege('orchara_worker', 'platform.{DELETION_JOBS}', "
            f"'{privilege}')::text",
        )
        assert allowed == "false", (privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
