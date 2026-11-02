"""T015 acceptance 1+2: the migration chain starts from empty and is explainable.

Every assertion runs the compiled ``cmd/admin migrate`` binary against a real
throwaway PostgreSQL database. There is no in-process fake database: a rollback
is proven by the absence of the table in PostgreSQL, not by a mocked call.
"""

from __future__ import annotations

from pathlib import Path

from conftest import (
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
    migrate_or_fail,
    popen_admin,
    run_admin,
    run_migrate,
    sql,
    sql_rows,
)

IDENTITY_TABLES = {
    "principals",
    "users",
    "activation_tokens",
    "role_bindings",
    "sessions",
    "audit_events",
}


def _ledger(dbname: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT version, name, sha256, applied_at::text AS applied_at "
        "FROM platform.schema_migrations ORDER BY version",
    )


def test_migrate_from_empty_database_records_the_chain(fresh_database, go_binaries) -> None:
    """Acceptance 1: a fresh ``CREATE DATABASE`` migrates from empty."""
    dsn, dbname = fresh_database
    output = migrate_or_fail(go_binaries["admin"], dsn)

    assert "applied" in output.lower(), output
    assert "0006_identity_core.sql" in output, output

    rows = _ledger(dbname)
    versions = [row["version"] for row in rows]
    assert versions == sorted(versions), f"versions are not ordered: {versions}"
    assert len(versions) == len(set(versions)), f"duplicate versions recorded: {versions}"
    # Every file the chain is required to contain is recorded, and the chain may
    # legitimately grow in later F01 tickets (0007+), so this is a subset check.
    assert {"0001", "0002", "0003", "0004", "0005", "0006"} <= set(versions)
    assert len(versions) >= 6
    assert "0006_identity_core.sql" in {row["name"] for row in rows}
    for row in rows:
        assert len(row["sha256"]) == 64, row
        assert row["applied_at"], row

    tables = {
        row["tablename"]
        for row in sql_rows(
            dbname,
            "SELECT tablename FROM pg_tables WHERE schemaname = 'platform'",
        )
    }
    assert IDENTITY_TABLES <= tables, tables
    # schema_migrations is the runner's own ledger.
    assert "schema_migrations" in tables


def test_rerun_reports_no_pending_and_changes_nothing(fresh_database, go_binaries) -> None:
    """Acceptance 2a: repeating the command is a no-op with a clear message."""
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    before = _ledger(dbname)

    completed = run_migrate(go_binaries["admin"], dsn)
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode == 0, combined
    assert "no pending migrations" in combined.lower(), combined

    after = _ledger(dbname)
    assert after == before, "a second migrate run modified the recorded history"


def test_failing_migration_rolls_back_and_is_not_recorded(
    fresh_database, go_binaries, tmp_path: Path
) -> None:
    """Acceptance 2b: one bad file fails loudly, after the good one committed."""
    dsn, dbname = fresh_database
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "0001_drill_good.sql").write_text(
        "CREATE TABLE platform.drill_good (id integer PRIMARY KEY);\n", encoding="utf-8"
    )
    (migrations_dir / "0002_drill_bad.sql").write_text(
        "CREATE TABLE platform.drill_bad (id integer PRIMARY KEY);\n"
        "INSERT INTO platform.missing_relation (id) VALUES (1);\n",
        encoding="utf-8",
    )

    completed = run_migrate(go_binaries["admin"], dsn, migrations_dir=migrations_dir)
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, f"a failing migration exited 0:\n{combined}"
    assert "0002_drill_bad.sql" in combined, combined
    assert "missing_relation" in combined, combined
    assert "does not exist" in combined.lower(), combined

    # The earlier file committed and is recorded; the failing file is not.
    assert [row["version"] for row in _ledger(dbname)] == ["0001"]
    assert (
        sql(
            dbname,
            "SELECT count(*) FROM pg_tables WHERE schemaname='platform' AND tablename='drill_good'",
        )
        == "1"
    )
    # Its partial DDL is rolled back with it.
    assert (
        sql(
            dbname,
            "SELECT count(*) FROM pg_tables WHERE schemaname='platform' AND tablename='drill_bad'",
        )
        == "0"
    )


def test_modified_applied_migration_fails_on_checksum(
    fresh_database, go_binaries, tmp_path: Path
) -> None:
    """Acceptance 2c: an applied migration is immutable; editing it fails loudly."""
    dsn, dbname = fresh_database
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration = migrations_dir / "0001_drill_one.sql"
    migration.write_text(
        "CREATE TABLE platform.drill_one (id integer PRIMARY KEY);\n", encoding="utf-8"
    )
    migrate_or_fail(go_binaries["admin"], dsn, migrations_dir=migrations_dir)
    recorded = _ledger(dbname)
    assert [row["version"] for row in recorded] == ["0001"]

    migration.write_text(
        "CREATE TABLE platform.drill_one (id integer PRIMARY KEY);\n-- edited after apply\n",
        encoding="utf-8",
    )
    completed = run_migrate(go_binaries["admin"], dsn, migrations_dir=migrations_dir)
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, f"a checksum drift exited 0:\n{combined}"
    assert "0001_drill_one.sql" in combined, combined
    assert "checksum" in combined.lower(), combined

    # The recorded history is untouched by the rejected run.
    assert _ledger(dbname) == recorded


def test_concurrent_migrate_runners_record_the_chain_once(fresh_database, go_binaries) -> None:
    """The session advisory lock serialises two runners on an empty database."""
    dsn, dbname = fresh_database
    first = popen_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    second = popen_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    first_output, _ = first.communicate(timeout=180)
    second_output, _ = second.communicate(timeout=180)

    assert first.returncode == 0, first_output
    assert second.returncode == 0, second_output

    versions = [row["version"] for row in _ledger(dbname)]
    assert len(versions) == len(set(versions)) >= 6, versions
    combined = (first_output + second_output).lower()
    assert "no pending migrations" in combined, combined


def test_migrate_refuses_without_a_database_dsn(go_binaries) -> None:
    """The command fails closed and names the missing environment variable."""
    completed = run_admin(go_binaries["admin"], ["migrate"], dsn=None)
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, combined
    assert "ORCHARA_DB_DSN" in combined, combined


def test_migrate_rejects_an_unknown_profile(fresh_database, go_binaries) -> None:
    """A typo in ORCHARA_PROFILE must not silently apply migrations."""
    dsn, dbname = fresh_database
    completed = run_admin(go_binaries["admin"], ["migrate"], dsn=dsn, profile="staging-ish")
    combined = (completed.stdout + completed.stderr).strip()
    assert completed.returncode != 0, combined
    assert "ORCHARA_PROFILE" in combined, combined
    assert sql(dbname, "SELECT count(*) FROM pg_tables WHERE schemaname='platform'") == "0"
