"""F11/T073: the diagnostic-grant migration.

Every assertion reads the real catalogue of a database migrated by the compiled
``cmd/admin`` chain. The chain invariant is continuity, never a pinned tail; this
ticket finds its own migration by slug so the merger can renumber it.
"""

from __future__ import annotations

import json

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_diagnostic_grants.sql"


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, udt_name, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def test_the_t073_migration_is_one_link_of_a_continuous_chain(
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


def test_diagnostic_grants_carries_the_contract_plus_the_object_snapshot(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "diagnostic_grants")
    expected = {
        "id": ("uuid", "NO"),
        "owner_id": ("uuid", "NO"),
        "grantee_id": ("uuid", "NO"),
        "run_id": ("uuid", "NO"),
        "purposes": ("ARRAY", "NO"),
        "object_scope": ("jsonb", "NO"),
        "expires_at": ("timestamp with time zone", "NO"),
        "revoked_at": ("timestamp with time zone", "YES"),
        "created_at": ("timestamp with time zone", "NO"),
    }
    for name, (data_type, nullable) in expected.items():
        assert name in columns, (name, columns)
        row = columns[name]
        actual_type = row["data_type"]
        if name == "purposes":
            assert row["udt_name"] == "_text", row
        assert actual_type == data_type, (name, actual_type)
        assert row["is_nullable"] == nullable, (name, row)
    assert "[]" in (columns["object_scope"]["column_default"] or ""), columns["object_scope"]
    assert "now()" in (columns["created_at"]["column_default"] or ""), columns["created_at"]

    definitions = " ".join(
        row["definition"]
        for row in st.sql_rows(
            dbname,
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = 'platform.diagnostic_grants'::regclass",
        )
    )
    assert "cardinality(purposes) > 0" in definitions, definitions
    assert "jsonb_typeof(object_scope) = 'array'" in definitions, definitions
    assert "expires_at > created_at" in definitions, definitions
    assert "revoked_at >= created_at" in definitions, definitions

    foreign_keys = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid = 'platform.diagnostic_grants'::regclass "
        "AND contype = 'f' ORDER BY conname",
    )
    referenced = " ".join(row["definition"] for row in foreign_keys)
    assert "platform.users(principal_id)" in referenced, referenced
    assert "platform.principals(id)" in referenced, referenced
    assert "platform.runs(id)" in referenced, referenced

    indexes = {
        row["indexname"]
        for row in st.sql_rows(
            dbname,
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'platform' "
            "AND tablename = 'diagnostic_grants' ORDER BY indexname",
        )
    }
    assert {"diagnostic_grants_live", "diagnostic_grants_owner"} <= indexes, indexes
    live = st.sql(
        dbname,
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'platform' "
        "AND indexname = 'diagnostic_grants_live'",
    )
    assert "WHERE (revoked_at IS NULL)" in live, live

    comment = st.sql(
        dbname,
        "SELECT obj_description('platform.diagnostic_grants'::regclass)",
    )
    assert comment and "F11/T073" in comment, comment
    assert "typed whitelist" in comment, comment


def test_diagnostic_grants_keeps_the_worker_privilege_boundary(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        allowed = st.sql(
            dbname,
            "SELECT has_table_privilege('orchara_worker', 'platform.diagnostic_grants', "
            f"'{privilege}')::text",
        )
        assert allowed == "false", (privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage


def test_the_grant_table_has_no_training_or_consent_columns(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "diagnostic_grants")
    # The grant is diagnostic consent only; no training/consent state is bundled
    # into it (DATA-POLICY §训练关系).
    for forbidden in ("training", "consent", "opt_in", "opt_out", "export"):
        assert not any(forbidden in name for name in columns), (forbidden, list(columns))
    assert json.dumps(sorted(columns)) == json.dumps(
        sorted(
            [
                "id",
                "owner_id",
                "grantee_id",
                "run_id",
                "purposes",
                "object_scope",
                "expires_at",
                "revoked_at",
                "created_at",
            ]
        )
    )
