"""T065 acceptance: migration 0038 (the persisted per-case progress facts).

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. The migration name is looked up by name, never by a
hard-coded version: migration numbers are global and the merger renumbers
concurrent branches.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

MIGRATION_NAME = "evaluation_progress"


def _columns(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def _constraint_definitions(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT conname, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass",
    )
    return {row["conname"]: row["definition"] for row in rows}


def test_0038_adds_the_persisted_progress_facts(migrated_database, go_binaries) -> None:
    """The verdict and failed-case counter are real database facts."""
    dsn, dbname = migrated_database
    names = st.migration_chain(dbname)
    assert any(MIGRATION_NAME in name for name in names.values()), names

    cases = _columns(dbname, "evaluation_case_runs")
    assert "verdict" in cases, cases
    assert cases["verdict"]["data_type"] == "text", cases["verdict"]
    assert cases["verdict"]["is_nullable"] == "YES", cases["verdict"]

    jobs = _columns(dbname, "evaluation_jobs")
    assert "progress_failed" in jobs, jobs
    assert jobs["progress_failed"]["data_type"] == "integer", jobs["progress_failed"]
    assert jobs["progress_failed"]["is_nullable"] == "NO", jobs["progress_failed"]
    assert jobs["progress_failed"]["column_default"] == "0", jobs["progress_failed"]

    case_constraints = _constraint_definitions(dbname, "evaluation_case_runs")
    verdict_shape = case_constraints.get("evaluation_case_runs_verdict_shape", "")
    for allowed in ("passed", "failed", "blocked", "not_run"):
        assert allowed in verdict_shape, verdict_shape
    job_constraints = _constraint_definitions(dbname, "evaluation_jobs")
    assert "evaluation_jobs_progress_failed_shape" in job_constraints, job_constraints

    # A migration replay is a no-op: the chain is unchanged and the binary says
    # there is nothing pending.
    before = st.migration_chain(dbname)
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay
    assert st.migration_chain(dbname) == before
