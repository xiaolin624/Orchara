"""T020 acceptance for migration 0008: the api_keys table and its privilege wall.

The key table is part of the real schema: the identity contract's columns,
uniqueness of the stored hash and the non-empty scope check are enforced by
PostgreSQL, not only by Go. The runtime role ``orchara_worker`` and PUBLIC get
nothing, which is the same boundary 0006/0007 established for their tables.
"""

from __future__ import annotations

import pytest

from conftest import (
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
    migrate_or_fail,
    psql,
    set_worker_password,
    sql,
    sql_rows,
    worker_password,
)


@pytest.fixture
def migrated_database(fresh_database, go_binaries):
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    set_worker_password(dbname)
    return fresh_database


def test_the_api_keys_table_matches_the_contract(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'api_keys' "
        "ORDER BY ordinal_position",
    )
    assert columns == [
        {"column_name": "id", "data_type": "uuid", "is_nullable": "NO"},
        {"column_name": "principal_id", "data_type": "uuid", "is_nullable": "NO"},
        {"column_name": "key_hash", "data_type": "text", "is_nullable": "NO"},
        {"column_name": "scopes", "data_type": "ARRAY", "is_nullable": "NO"},
        {
            "column_name": "expires_at",
            "data_type": "timestamp with time zone",
            "is_nullable": "NO",
        },
        {
            "column_name": "revoked_at",
            "data_type": "timestamp with time zone",
            "is_nullable": "YES",
        },
    ]

    constraints = {
        row["conname"]: row["contype"]
        for row in sql_rows(
            dbname,
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'platform.api_keys'::regclass",
        )
    }
    assert constraints.get("api_keys_pkey") == "p"
    assert constraints.get("api_keys_key_hash_key") == "u"
    assert constraints.get("api_keys_principal_id_fkey") == "f"
    assert "c" in constraints.values(), constraints  # cardinality(scopes) > 0

    indexes = {
        row["indexname"]
        for row in sql_rows(
            dbname,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'platform' AND tablename = 'api_keys'",
        )
    }
    assert "api_keys_key_hash_key" in indexes
    assert "api_keys_principal" in indexes

    # An empty scope set is refused by the table, not only by the Go validator.
    sql(
        dbname,
        "INSERT INTO platform.principals (id, kind, display_name) "
        "VALUES (gen_random_uuid(), 'user', 't020-schema-owner')",
    )
    owner = sql(dbname, "SELECT id FROM platform.principals WHERE display_name = 't020-schema-owner'")
    result = psql(
        dbname,
        "INSERT INTO platform.api_keys (id, principal_id, key_hash, scopes, expires_at) "
        f"VALUES (gen_random_uuid(), '{owner}'::uuid, 't020-empty-scopes', "
        "ARRAY[]::text[], now() + interval '1 hour')",
    )
    assert result.code != 0
    assert "check constraint" in result.stderr.lower(), result.stderr
    # And the stored hash is unique.
    result = psql(
        dbname,
        "INSERT INTO platform.api_keys (id, principal_id, key_hash, scopes, expires_at) "
        f"VALUES (gen_random_uuid(), '{owner}'::uuid, 't020-duplicate-hash', "
        "ARRAY['application:read'], now() + interval '1 hour')",
    )
    assert result.code == 0, result.stderr
    result = psql(
        dbname,
        "INSERT INTO platform.api_keys (id, principal_id, key_hash, scopes, expires_at) "
        f"VALUES (gen_random_uuid(), '{owner}'::uuid, 't020-duplicate-hash', "
        "ARRAY['application:read'], now() + interval '1 hour')",
    )
    assert result.code != 0
    assert "duplicate key" in result.stderr.lower() or "unique" in result.stderr.lower()


def test_the_worker_and_public_get_nothing_from_the_key_table(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "insert": "INSERT INTO platform.api_keys "
        "(id, principal_id, key_hash, scopes, expires_at) VALUES "
        "(gen_random_uuid(), gen_random_uuid(), 'worker-forged', "
        "ARRAY['application:read'], now())",
        "update": "UPDATE platform.api_keys SET key_hash = 'worker-forged' WHERE false",
        "delete": "DELETE FROM platform.api_keys WHERE false",
    }
    for verb, statement in statements.items():
        result = psql(dbname, statement, user="orchara_worker", password=worker_password())
        assert result.code != 0, f"{verb} on platform.api_keys was allowed"
        assert "permission denied" in result.stderr.lower(), result.stderr

    grants = sql_rows(
        dbname,
        "SELECT privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform' "
        "AND table_name = 'api_keys'",
    )
    assert grants == [], grants
    public_grants = sql_rows(
        dbname,
        "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        "WHERE c.oid = 'platform.api_keys'::regclass AND acl.grantee = 0",
    )
    assert public_grants == [], public_grants

    # Control: the same connection still writes the table it is granted.
    result = psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t020-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=worker_password(),
    )
    assert result.code == 0, result.stderr
