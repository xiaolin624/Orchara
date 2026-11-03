"""T030 acceptance: migration 0017 adds the tool_run job kind.

The migration is additive on top of 0016 and changes exactly one thing: the
``background_jobs_kind_check`` whitelist gains ``tool_run`` (the documented
non-contract bridge for the trusted preset tool). There is no new table and no
business row. The real ``cmd/admin migrate`` applies it, a replay is a no-op,
the closed kind vocabulary still refuses an unknown value, and ``orchara_worker``
still gets nothing from ``platform``.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    go_binaries,
    migrated_database,
)

ALL_KINDS = (
    "artifact_finalize",
    "route_request",
    "tool_build",
    "document_ingest",
    "application_evaluate",
    "model_validate",
    "delete_content",
    "catalog_index",
    "tool_run",
)


def principal_id(dbname: str) -> str:
    return st.sql(dbname, "SELECT id::text FROM platform.principals LIMIT 1")


def kind_check_definition(dbname: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT conname, contype, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint "
        "WHERE conrelid = 'platform.background_jobs'::regclass "
        "AND conname = 'background_jobs_kind_check'",
    )
    assert len(rows) == 1, rows
    assert rows[0]["contype"] == "c"
    return rows[0]["definition"]


def test_0017_extends_the_kind_check_and_0018_is_the_chain_tail(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T041/T042 append 0025/0026; the earlier migrations keep their positions.
    assert versions == [f"{number:04d}" for number in range(1, 27)], versions
    assert ledgers[15]["name"] == "0016_execution_claims_and_quota.sql"
    assert ledgers[-9]["name"] == "0018_run_events.sql"
    assert ledgers[-8]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-7]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-6]["name"] == "0021_run_waitings.sql"
    assert ledgers[-5]["name"] == "0022_run_unknown_operations.sql"
    assert ledgers[-4]["name"] == "0023_tool_builds_and_review.sql"
    assert ledgers[-3]["name"] == "0024_run_recovery_evidence.sql"
    assert ledgers[-2]["name"] == "0025_run_capability_ledger.sql"
    assert ledgers[-1]["name"] == "0026_run_node_outputs.sql"

    definition = kind_check_definition(dbname)
    for kind in ALL_KINDS:
        assert kind in definition, (kind, definition)
    assert "sh -c id" not in definition.lower()
    # PostgreSQL renders the IN list as an ARRAY of text literals; it is still a
    # closed value list, and the unknown-kind insert below proves it.
    assert "ARRAY[" in definition.upper(), definition


def test_the_new_kind_is_insertable_and_unknown_kinds_are_still_refused(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = principal_id(dbname)
    dedup = "t030-migration:tool-run"
    inserted = st.psql(
        dbname,
        "WITH inserted AS ("
        "INSERT INTO platform.background_jobs "
        "(id, kind, target_id, owner_id, dedup_key, state) VALUES "
        f"(gen_random_uuid(), 'tool_run', gen_random_uuid(), {st.quote_literal(owner)}::uuid, "
        f"{st.quote_literal(dedup)}, 'ready') RETURNING id"
        ") SELECT id::text FROM inserted",
    )
    assert inserted.code == 0, inserted.stderr
    count = st.sql(
        dbname,
        "SELECT count(*) FROM platform.background_jobs WHERE kind = 'tool_run'",
    )
    assert int(count) == 1

    unknown = st.psql(
        dbname,
        "INSERT INTO platform.background_jobs "
        "(id, kind, target_id, owner_id, dedup_key, state) VALUES "
        f"(gen_random_uuid(), 'sh -c id', gen_random_uuid(), {st.quote_literal(owner)}::uuid, "
        "'t030-migration:unknown', 'ready')",
    )
    assert unknown.code != 0, unknown.stderr
    assert "background_jobs_kind_check" in unknown.stderr, unknown.stderr


def test_0017_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_the_worker_still_gets_nothing_from_platform(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for table in ("background_jobs", "artifacts", "idempotency_keys"):
        denied = st.psql(
            dbname,
            f"SELECT count(*) FROM platform.{table}",
            user="orchara_worker",
            password=st.worker_password(),
        )
        assert denied.code != 0, (table, denied.stderr)
        assert "permission denied" in denied.stderr.lower(), (table, denied.stderr)

    grants = st.sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    assert grants == [], grants

    control = st.psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t030-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert control.code == 0, control.stderr


def test_the_migration_adds_no_table_or_business_row(migrated_database) -> None:
    _dsn, dbname = migrated_database
    expected_tables = {
        "artifact_admissions",
        "artifacts",
        "background_job_artifacts",
        "background_jobs",
        "idempotency_keys",
    }
    tables = {
        row["table_name"]
        for row in st.sql_rows(
            dbname,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'platform'",
        )
    }
    assert expected_tables <= tables, tables
    assert "tool_runs" not in tables
    assert "tools" not in tables
    count = st.sql(dbname, "SELECT count(*) FROM platform.background_jobs")
    assert int(count) == 0, count
