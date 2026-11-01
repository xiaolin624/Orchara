"""T019 acceptance: the new authorization tables keep the 0006 privilege boundary.

The denial has to come from PostgreSQL grants, not from application code: the
runtime role ``orchara_worker`` must not be able to read or forge grants, the
policy epoch or applications, and PUBLIC must have nothing. The control at the
end proves the same connection still works for its own bookkeeping table, so
"denied" is not "the connection is broken".
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

AUTHORIZATION_TABLES = ("resource_grants", "policy_epochs", "applications")

# One real statement per (table, verb); PostgreSQL checks table privileges before
# it evaluates constraints, so the forged rows can never land.
WRITE_STATEMENTS: dict[str, dict[str, str]] = {
    "resource_grants": {
        "insert": "INSERT INTO platform.resource_grants "
        "(id, principal_id, resource_type, resource_id, action) VALUES "
        "(gen_random_uuid(), gen_random_uuid(), 'application', gen_random_uuid(), 'read')",
        "update": "UPDATE platform.resource_grants SET action = 'read' WHERE false",
        "delete": "DELETE FROM platform.resource_grants WHERE false",
    },
    "policy_epochs": {
        "insert": "INSERT INTO platform.policy_epochs (scope, revision) "
        "VALUES ('worker-forged', 0)",
        "update": "UPDATE platform.policy_epochs SET revision = 999 WHERE false",
        "delete": "DELETE FROM platform.policy_epochs WHERE false",
    },
    "applications": {
        "insert": "INSERT INTO platform.applications (id, owner_id, name) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'worker-forged')",
        "update": "UPDATE platform.applications SET name = 'worker-forged' WHERE false",
        "delete": "DELETE FROM platform.applications WHERE false",
    },
}


@pytest.fixture
def migrated_database(fresh_database, go_binaries):
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    set_worker_password(dbname)
    return fresh_database


def test_the_worker_cannot_write_the_authorization_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    allowed = []
    for table in AUTHORIZATION_TABLES:
        for verb, statement in WRITE_STATEMENTS[table].items():
            result = psql(dbname, statement, user="orchara_worker", password=worker_password())
            if result.code == 0:
                allowed.append(f"{verb} on platform.{table} was allowed")
                continue
            assert "permission denied" in result.stderr.lower(), (
                f"{verb} on platform.{table} failed for the wrong reason: {result.stderr}"
            )
    assert allowed == [], allowed
    # Nothing was created even by the refused statements.
    assert sql(dbname, "SELECT count(*) FROM platform.resource_grants") == "0"
    assert sql(dbname, "SELECT count(*) FROM platform.applications") == "0"
    assert sql(dbname, "SELECT count(*) FROM platform.policy_epochs") == "1"


def test_the_worker_holds_no_recorded_privilege_on_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    grants = sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform' "
        f"AND table_name IN ({', '.join(repr(name) for name in AUTHORIZATION_TABLES)})",
    )
    schema_privileges = sql_rows(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE') AS usage, "
        "has_schema_privilege('orchara_worker', 'platform', 'CREATE') AS create",
    )[0]
    assert grants == [], grants
    assert schema_privileges == {"usage": False, "create": False}, schema_privileges


def test_public_has_no_privilege_on_the_platform_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    public_grants = sql_rows(
        dbname,
        "SELECT c.relname AS relation, acl.privilege_type AS privilege "
        "FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        "WHERE c.relnamespace = 'platform'::regnamespace "
        "AND c.relkind = 'r' AND acl.grantee = 0 ORDER BY c.relname, acl.privilege_type",
    )
    assert public_grants == [], public_grants


def test_the_worker_keeps_its_own_bookkeeping_access(migrated_database) -> None:
    """Control: the same connection still writes the table it is granted."""
    _dsn, dbname = migrated_database
    result = psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t019-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=worker_password(),
    )
    assert result.code == 0, result.stderr
    assert (
        sql(dbname, "SELECT count(*) FROM app.workers WHERE worker_id = 't019-privilege-control'")
        == "1"
    )
