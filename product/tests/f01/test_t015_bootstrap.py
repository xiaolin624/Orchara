"""T015 acceptance 4: bootstrap creates exactly one administrator, once.

The command is exercised as a real process. Piped stdin is the non-terminal
path (a terminal would use the hidden reader from x/term); the password must
never appear in the command's output.
"""

from __future__ import annotations

import re

import pytest

from conftest import (
    bootstrap_or_fail,
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
    migrate_or_fail,
    popen_admin,
    run_bootstrap,
    sql,
    sql_rows,
)

ADMIN_PASSWORD = "t015-correct-horse-battery"


@pytest.fixture
def migrated_database(fresh_database, go_binaries):
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    return fresh_database


def _counts(dbname: str) -> dict[str, int]:
    return {
        "principals": int(sql(dbname, "SELECT count(*) FROM platform.principals")),
        "users": int(sql(dbname, "SELECT count(*) FROM platform.users")),
        "role_bindings": int(sql(dbname, "SELECT count(*) FROM platform.role_bindings")),
        "audit_events": int(sql(dbname, "SELECT count(*) FROM platform.audit_events")),
    }


def test_bootstrap_creates_exactly_one_audited_admin(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    output = bootstrap_or_fail(go_binaries["admin"], dsn, ADMIN_PASSWORD)

    # Prints the principal id and login only.
    principal_ids = re.findall(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", output
    )
    assert len(principal_ids) == 1, output
    assert "admin" in output, output
    assert ADMIN_PASSWORD not in output, "the bootstrap output echoed the password"
    assert "password" not in output.lower(), output

    assert _counts(dbname) == {
        "principals": 1,
        "users": 1,
        "role_bindings": 1,
        "audit_events": 1,
    }

    principal = sql_rows(
        dbname,
        "SELECT id, kind, display_name, disabled_at::text AS disabled_at FROM platform.principals",
    )[0]
    assert principal["id"] == principal_ids[0]
    assert principal["kind"] == "user"
    assert principal["display_name"] == "Platform Admin"
    assert principal["disabled_at"] in (None, "")

    user = sql_rows(
        dbname,
        "SELECT login, password_hash, must_change_password FROM platform.users",
    )[0]
    assert user["login"] == "admin"
    assert user["must_change_password"] is False
    assert user["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=2$"), user
    assert user["password_hash"] != ADMIN_PASSWORD
    assert ADMIN_PASSWORD not in user["password_hash"]

    assert sql(dbname, "SELECT role FROM platform.role_bindings") == "admin"

    audit = sql_rows(
        dbname,
        "SELECT actor_id::text AS actor_id, action, resource_type, resource_id::text AS resource_id "
        "FROM platform.audit_events",
    )
    assert len(audit) == 1, audit
    assert "bootstrap" in audit[0]["action"]
    assert audit[0]["resource_type"] == "principal"
    assert audit[0]["resource_id"] == principal["id"]
    # System action: no actor yet, because the admin did not exist before.
    assert audit[0]["actor_id"] in (None, "")


def test_second_bootstrap_is_refused(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    bootstrap_or_fail(go_binaries["admin"], dsn, ADMIN_PASSWORD)

    completed = run_bootstrap(
        go_binaries["admin"], dsn, "another-long-password", login="admin2"
    )
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, f"a second bootstrap succeeded:\n{combined}"
    assert "already exists" in combined.lower(), combined

    counts = _counts(dbname)
    assert counts["principals"] == 1, counts
    assert counts["users"] == 1, counts
    assert counts["role_bindings"] == 1, counts


def test_weak_password_is_refused_without_creating_anyone(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    completed = run_bootstrap(go_binaries["admin"], dsn, "too-short")
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, f"a weak password was accepted:\n{combined}"
    assert "12" in combined, combined
    assert _counts(dbname) == {
        "principals": 0,
        "users": 0,
        "role_bindings": 0,
        "audit_events": 0,
    }


def test_concurrent_bootstraps_leave_exactly_one_admin(migrated_database, go_binaries) -> None:
    """The advisory-lock + one-transaction rule must survive a real race."""
    dsn, dbname = migrated_database
    first = popen_admin(
        go_binaries["admin"],
        ["bootstrap", "--login", "admin-a", "--display-name", "Admin A"],
        dsn=dsn,
    )
    second = popen_admin(
        go_binaries["admin"],
        ["bootstrap", "--login", "admin-b", "--display-name", "Admin B"],
        dsn=dsn,
    )
    first_output, _ = first.communicate(ADMIN_PASSWORD + "\n", timeout=120)
    second_output, _ = second.communicate(ADMIN_PASSWORD + "\n", timeout=120)

    successes = [process for process in (first, second) if process.returncode == 0]
    assert len(successes) == 1, (
        f"expected exactly one winning bootstrap, got {first.returncode}/{second.returncode}\n"
        f"{first_output}\n{second_output}"
    )
    loser_output = second_output if first.returncode == 0 else first_output
    assert "already exists" in loser_output.lower(), loser_output

    assert _counts(dbname)["users"] == 1
    assert sql(dbname, "SELECT count(*) FROM platform.role_bindings WHERE role='admin'") == "1"
