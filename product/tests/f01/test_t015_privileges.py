"""T015 acceptance 3: the runtime role cannot modify identity business tables.

The worker is a separate PostgreSQL role that the execution probes use. It must
keep working for its own bookkeeping (``app.workers``) while being denied on
every ``platform`` table and on ``CREATE`` in the ``platform`` schema. The
control assertion matters: without it, "denied" could just mean "the connection
is broken".
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

PLATFORM_TABLES = (
    "principals",
    "users",
    "activation_tokens",
    "role_bindings",
    "sessions",
    "audit_events",
)

# One write statement per (table, verb). Each names real columns: PostgreSQL
# resolves columns before it checks table privileges, so a fake column would
# report a different error than the one under test.
WRITE_STATEMENTS: dict[str, dict[str, str]] = {
    "principals": {
        "insert": "INSERT INTO platform.principals (id, kind, display_name) "
        "VALUES (gen_random_uuid(), 'user', 'worker-forged')",
        "update": "UPDATE platform.principals SET display_name = 'worker-forged' WHERE false",
        "delete": "DELETE FROM platform.principals WHERE false",
    },
    "users": {
        "insert": "INSERT INTO platform.users (principal_id, login, password_hash) "
        "VALUES (gen_random_uuid(), 'worker-forged', 'x')",
        "update": "UPDATE platform.users SET login = login WHERE false",
        "delete": "DELETE FROM platform.users WHERE false",
    },
    "activation_tokens": {
        "insert": "INSERT INTO platform.activation_tokens (id, principal_id, token_hash, expires_at) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'worker-forged', now())",
        "update": "UPDATE platform.activation_tokens SET consumed_at = now() WHERE false",
        "delete": "DELETE FROM platform.activation_tokens WHERE false",
    },
    "role_bindings": {
        "insert": "INSERT INTO platform.role_bindings (principal_id, role) "
        "VALUES (gen_random_uuid(), 'admin')",
        "update": "UPDATE platform.role_bindings SET role = 'user' WHERE false",
        "delete": "DELETE FROM platform.role_bindings WHERE false",
    },
    "sessions": {
        "insert": "INSERT INTO platform.sessions (token_hash, principal_id, csrf_hash, expires_at) "
        "VALUES ('worker-forged', gen_random_uuid(), 'worker-forged', now())",
        "update": "UPDATE platform.sessions SET revoked_at = now() WHERE false",
        "delete": "DELETE FROM platform.sessions WHERE false",
    },
    "audit_events": {
        "insert": "INSERT INTO platform.audit_events (id, action, resource_type) "
        "VALUES (gen_random_uuid(), 'worker-forged', 'principal')",
        "update": "UPDATE platform.audit_events SET action = action WHERE false",
        "delete": "DELETE FROM platform.audit_events WHERE false",
    },
}


@pytest.fixture
def migrated_database(fresh_database, go_binaries):
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    set_worker_password(dbname)
    return fresh_database


def test_worker_cannot_write_any_platform_table(migrated_database) -> None:
    _dsn, dbname = migrated_database
    denials = []
    for table in PLATFORM_TABLES:
        for verb, statement in WRITE_STATEMENTS[table].items():
            result = psql(
                dbname, statement, user="orchara_worker", password=worker_password()
            )
            if result.code == 0:
                denials.append(f"{verb} on platform.{table} was allowed")
                continue
            assert "permission denied" in result.stderr.lower(), (
                f"{verb} on platform.{table} failed for the wrong reason: {result.stderr}"
            )
    assert denials == [], denials


def test_worker_cannot_create_objects_in_the_platform_schema(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for statement, label in (
        ("CREATE TABLE platform.worker_forged (id int)", "CREATE TABLE"),
        ("CREATE VIEW platform.worker_forged_view AS SELECT 1", "CREATE VIEW"),
    ):
        result = psql(dbname, statement, user="orchara_worker", password=worker_password())
        assert result.code != 0, f"worker was allowed to run {label}"
        assert "permission denied" in result.stderr.lower(), result.stderr
    assert (
        sql(
            dbname,
            "SELECT count(*) FROM pg_tables WHERE schemaname='platform' AND tablename='worker_forged'",
        )
        == "0"
    )


def test_worker_has_no_recorded_privileges_on_platform(migrated_database) -> None:
    """The denial comes from absence of grants, not from a client-side guard."""
    _dsn, dbname = migrated_database
    table_grants = sql_rows(
        dbname,
        "SELECT privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    schema_privileges = sql_rows(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE') AS usage, "
        "has_schema_privilege('orchara_worker', 'platform', 'CREATE') AS create",
    )[0]
    assert table_grants == [], table_grants
    assert schema_privileges == {"usage": False, "create": False}, schema_privileges


def test_worker_keeps_its_own_bookkeeping_access(migrated_database) -> None:
    """Control: the same connection can still write the table it is granted."""
    _dsn, dbname = migrated_database
    result = psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t015-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=worker_password(),
    )
    assert result.code == 0, result.stderr
    assert (
        sql(dbname, "SELECT count(*) FROM app.workers WHERE worker_id = 't015-privilege-control'")
        == "1"
    )
