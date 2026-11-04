"""F04A/T042: migration 0026 adds per-node result provenance.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` chain: the composite key (run, node, kind, artifact), the
closed kind vocabulary, the bounded label, the run/artifact foreign keys and the
unchanged worker privilege boundary.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLE = "run_node_outputs"


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
        "SELECT a.attname AS column_name, "
        "confrelid::regclass::text AS target, "
        "pg_get_constraintdef(c.oid) AS definition "
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


def foreign_key_definitions(dbname: str, table: str) -> list[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = 'f'",
    )
    return [row["definition"] for row in rows]


def test_migration_0026_creates_the_node_output_table(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, TABLE)
    assert set(columns) == {
        "run_id", "node_id", "kind", "artifact_id", "label", "created_at",
    }, columns
    for name in ("run_id", "node_id", "kind", "artifact_id", "label"):
        assert columns[name]["is_nullable"] == "NO", (name, columns[name])
    assert columns["run_id"]["data_type"] == "uuid", columns["run_id"]
    assert columns["artifact_id"]["data_type"] == "uuid", columns["artifact_id"]
    assert columns["kind"]["data_type"] == "text", columns["kind"]
    assert "now()" in (columns["created_at"]["column_default"] or ""), columns["created_at"]

    assert primary_key_columns(dbname, TABLE) == [
        "run_id", "node_id", "kind", "artifact_id",
    ]

    foreign_keys = foreign_keys_of(dbname, TABLE)
    assert foreign_keys["run_id"] == "platform.runs", foreign_keys
    assert foreign_keys["artifact_id"] == "platform.artifacts", foreign_keys
    definitions = " ".join(foreign_key_definitions(dbname, TABLE))
    assert "ON DELETE CASCADE" in definitions, definitions

    definitions = constraint_definitions(dbname, TABLE)
    assert "'data'" in definitions and "'file'" in definitions and "'log'" in definitions, definitions
    assert "node_id ~" in definitions, definitions
    assert "length(label)" in definitions, definitions


def test_migration_0026_keeps_the_worker_privilege_boundary(migrated_database) -> None:
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
            f"SELECT has_table_privilege('orchara_worker', 'platform.{TABLE}', '{privilege}')::text",
        )
        assert allowed == "false", (privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
