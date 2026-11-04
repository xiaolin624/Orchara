"""F09/T054: migration 0033 creates the contract memory tables.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` chain: the contract columns, the closed candidate state
vocabulary, the one-memory-per-candidate unique key, the human-only owner foreign
keys and the unchanged worker privilege boundary.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

VERSION = "0033"
NAME = "0033_memory_candidates.sql"
CANDIDATES = "memory_candidates"
MEMORIES = "memories"


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def primary_key_columns(dbname: str, table: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) WITH ORDINALITY AS k(attnum, ordinality) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.conrelid = 'platform.{table}'::regclass AND c.contype = 'p' "
        "ORDER BY k.ordinality",
    )
    return [row["column_name"] for row in rows]


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


def test_migration_0033_is_the_next_link_of_the_chain(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    # The chain invariant is continuity from 0001 (asserted by migration_chain),
    # never a pinned tail: later tickets (0034 already) append migrations.
    chain = st.migration_chain(dbname)
    assert chain[VERSION] == NAME, chain

    # Repeating migrate on the same database is a no-op, not a second apply.
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "no pending migrations" in (completed.stdout + completed.stderr), completed.stdout
    assert st.migration_chain(dbname)[VERSION] == NAME


def test_memory_candidates_has_the_contract_columns_and_keys(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, CANDIDATES)
    assert set(columns) == {
        "id", "owner_id", "application_id", "body_artifact_id", "source_run_id",
        "logical_key", "state", "expires_at", "created_at", "decided_at", "decided_by",
        "decision_key",
    }, columns
    for name in ("id", "owner_id", "application_id", "body_artifact_id", "state", "expires_at"):
        assert columns[name]["is_nullable"] == "NO", (name, columns[name])
    assert columns["source_run_id"]["is_nullable"] == "YES", columns["source_run_id"]
    assert columns["owner_id"]["data_type"] == "uuid", columns["owner_id"]
    assert columns["body_artifact_id"]["data_type"] == "uuid", columns["body_artifact_id"]
    assert "now()" in (columns["created_at"]["column_default"] or ""), columns["created_at"]

    assert primary_key_columns(dbname, CANDIDATES) == ["id"]
    foreign_keys = foreign_keys_of(dbname, CANDIDATES)
    assert foreign_keys["owner_id"] == "platform.users", foreign_keys
    assert foreign_keys["application_id"] == "platform.applications", foreign_keys
    assert foreign_keys["body_artifact_id"] == "platform.artifacts", foreign_keys
    assert foreign_keys["source_run_id"] == "platform.runs", foreign_keys

    definitions = constraint_definitions(dbname, CANDIDATES)
    for state in ("pending", "confirmed", "rejected", "expired"):
        assert "'" + state + "'" in definitions, definitions
    assert "UNIQUE (source_run_id, logical_key)" in definitions, definitions


def test_only_one_memory_can_come_from_one_candidate(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, MEMORIES)
    assert set(columns) == {
        "id", "owner_id", "application_id", "body_artifact_id", "candidate_id",
        "revision", "confirmed_at", "confirmed_by", "deleted_at",
    }, columns
    for name in (
        "id", "owner_id", "application_id", "body_artifact_id", "revision",
        "confirmed_at", "confirmed_by",
    ):
        assert columns[name]["is_nullable"] == "NO", (name, columns[name])
    assert "1" in (columns["revision"]["column_default"] or ""), columns["revision"]

    assert primary_key_columns(dbname, MEMORIES) == ["id"]
    foreign_keys = foreign_keys_of(dbname, MEMORIES)
    assert foreign_keys["owner_id"] == "platform.users", foreign_keys
    assert foreign_keys["application_id"] == "platform.applications", foreign_keys
    assert foreign_keys["body_artifact_id"] == "platform.artifacts", foreign_keys
    assert foreign_keys["candidate_id"] == "platform.memory_candidates", foreign_keys
    assert foreign_keys["confirmed_by"] == "platform.users", foreign_keys

    definitions = constraint_definitions(dbname, MEMORIES)
    assert "UNIQUE (candidate_id)" in definitions, definitions


def test_migration_0033_keeps_the_worker_privilege_boundary(migrated_database) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return  # a deployment without the worker role has no boundary to check
    for table in (CANDIDATES, MEMORIES):
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
