"""T034: migration 0020 adds the active-time columns and backfills the deadline.

Every assertion reads a database migrated by the real ``cmd/admin migrate`` binary.
The backfill assertion re-runs migration 0020 itself (the ledger row is removed and
the real binary applies the file again; every statement is idempotent) against
pre-backfill rows, which is the only way to observe a migration backfill on a real
chain: no row can be inserted "before" 0020 on an up-to-date database.
"""

from __future__ import annotations

import json

from support import f00b, t025, t027, t029, t034
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = 'c'",
    )
    return " ".join(row["definition"] for row in rows)


def seed_snapshot_with_manifest(dbname: str, owner_id: str, manifest: dict) -> str:
    """One real debug snapshot whose JSONB projection carries ``manifest``."""
    application_id = t027.seed_application(dbname, owner_id, name="t034 policy host")
    manifest_sha256 = st.sha256_hex(json.dumps(manifest, sort_keys=True))
    artifact_id = t025.insert_artifact(
        dbname, owner_id, purpose="execution_manifest", sha256=manifest_sha256
    )
    snapshot_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.execution_snapshots "
        "(id, kind, application_id, draft_revision, owner_id, manifest, manifest_sha256, "
        " manifest_artifact_id, compiler_version, worker_image_digest, state_schema_version) "
        f"VALUES ({st.quote_literal(snapshot_id)}::uuid, 'debug', "
        f"{st.quote_literal(application_id)}::uuid, 1, {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(json.dumps(manifest))}::jsonb, {st.quote_literal(manifest_sha256)}, "
        f"{st.quote_literal(artifact_id)}::uuid, 'orchara-compiler/1.0.0', "
        f"{st.quote_literal(t027.WORKER_IMAGE_DIGEST)}, 'orchara-state/1.0')",
    )
    return snapshot_id


def seed_queued_run(
    dbname: str,
    owner_id: str,
    snapshot_id: str,
    *,
    max_active_ms: int | None = None,
) -> tuple[str, str]:
    """One real queued debug run + ready start job, with an optional limit."""
    input_artifact = t025.insert_artifact(dbname, owner_id, purpose="run_input", sha256="b" * 64)
    run_id = t029.new_uuid()
    max_sql = "NULL" if max_active_ms is None else str(int(max_active_ms))
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency, phase, max_active_ms) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(owner_id)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot_id)}::uuid, {st.quote_literal('c' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        f"'queued', 1.00, 'USD', 'idle', {max_sql})",
    )
    job_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(run_id)}::uuid, 'start', 'ready')",
    )
    return run_id, job_id


def seed_attempt(dbname: str, *, job_id: str, run_id: str) -> str:
    token = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
        f"VALUES ({st.quote_literal(token)}::uuid, {st.quote_literal(job_id)}::uuid, "
        f"{st.quote_literal(run_id)}::uuid, 't034-worker', 'unit-1', "
        "now() + interval '30 seconds', now())",
    )
    return token


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0020_is_recorded_once_in_the_chain(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T035 appends 0021; 0020 stays at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 22)], versions
    assert ledgers[-2]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-1]["name"] == "0021_run_waitings.sql"
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_runs_max_active_ms_is_nullable_and_positive_when_known(migrated_database) -> None:
    _dsn, dbname = migrated_database
    runs = columns_of(dbname, "runs")
    assert runs["max_active_ms"]["data_type"] == "bigint"
    assert runs["max_active_ms"]["is_nullable"] == "YES", runs["max_active_ms"]
    definitions = constraint_definitions(dbname, "runs")
    assert "max_active_ms IS NULL" in definitions, definitions
    assert "max_active_ms > 0" in definitions, definitions

    # NULL is representable (unknown/no deadline); 0 and negative are refused.
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot_with_manifest(dbname, owner, {"definition": {"policy": {}}})
    run_id, _job_id = seed_queued_run(dbname, owner, snapshot)
    assert t034.run_active_row(dbname, run_id)["max_active_ms"] is None
    for value in ("0", "-1"):
        rejected = st.psql(
            dbname,
            f"UPDATE platform.runs SET max_active_ms = {value} "
            f"WHERE id = {st.quote_literal(run_id)}::uuid",
        )
        assert rejected.code != 0, f"the database accepted max_active_ms = {value}"
        assert "check constraint" in rejected.stderr.lower(), rejected.stderr


def test_run_attempts_active_time_columns_settle_once(migrated_database) -> None:
    _dsn, dbname = migrated_database
    attempts = columns_of(dbname, "run_attempts")
    assert attempts["active_ms"]["data_type"] == "bigint"
    assert attempts["active_ms"]["is_nullable"] == "NO", attempts["active_ms"]
    assert attempts["active_ms"]["column_default"].startswith("0"), attempts["active_ms"]
    assert attempts["active_settled_at"]["data_type"] == "timestamp with time zone"
    assert attempts["active_settled_at"]["is_nullable"] == "YES"
    definitions = constraint_definitions(dbname, "run_attempts")
    assert "active_ms >= 0" in definitions, definitions

    # A real claim starts at zero/unsettled; a manual negative value is refused.
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot_with_manifest(dbname, owner, {"definition": {"policy": {}}})
    run_id, job_id = seed_queued_run(dbname, owner, snapshot)
    token = seed_attempt(dbname, job_id=job_id, run_id=run_id)
    row = t034.attempt_active_row(dbname, token)
    assert row["active_ms"] == 0, row
    assert row["active_settled_at"] is None, row
    rejected = st.psql(
        dbname,
        f"UPDATE platform.run_attempts SET active_ms = -1 "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )
    assert rejected.code != 0, "the database accepted negative active_ms"
    assert "check constraint" in rejected.stderr.lower(), rejected.stderr


def test_worker_and_public_get_no_access_to_the_new_columns(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for statement in (
        "SELECT active_ms FROM platform.run_attempts",
        "SELECT max_active_ms FROM platform.runs",
        "UPDATE platform.runs SET max_active_ms = 1000",
    ):
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {statement}"
        assert "permission denied" in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------
def test_0020_backfills_existing_runs_from_the_frozen_policy(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)

    policy_900 = seed_snapshot_with_manifest(
        dbname, owner,
        {"definition": {"policy": {"max_active_seconds": 900}}},
    )
    policy_120 = seed_snapshot_with_manifest(
        dbname, owner,
        {"definition": {"policy": {"max_active_seconds": 120}}},
    )
    no_policy = seed_snapshot_with_manifest(dbname, owner, {"definition": {}})
    fractional = seed_snapshot_with_manifest(
        dbname, owner,
        {"definition": {"policy": {"max_active_seconds": 12.5}}},
    )
    out_of_range = seed_snapshot_with_manifest(
        dbname, owner,
        {"definition": {"policy": {"max_active_seconds": 999999}}},
    )
    wrong_type = seed_snapshot_with_manifest(
        dbname, owner,
        {"definition": {"policy": {"max_active_seconds": "900"}}},
    )

    runs = {
        name: seed_queued_run(dbname, owner, snapshot)[0]
        for name, snapshot in {
            "policy_900": policy_900,
            "policy_120": policy_120,
            "no_policy": no_policy,
            "fractional": fractional,
            "out_of_range": out_of_range,
            "wrong_type": wrong_type,
        }.items()
    }
    # A pre-existing non-NULL value is never overwritten by the backfill.
    explicit = seed_queued_run(dbname, owner, policy_900, max_active_ms=5000)[0]

    # Remove the ledger row so the real runner applies 0020 again. Every statement
    # in the migration is idempotent; only the backfill has work left to do.
    st.sql(dbname, "DELETE FROM platform.schema_migrations WHERE version = '0020'")
    applied = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "0020_active_time_and_deadline.sql" in applied, applied

    assert t034.run_active_row(dbname, runs["policy_900"])["max_active_ms"] == 900_000
    assert t034.run_active_row(dbname, runs["policy_120"])["max_active_ms"] == 120_000
    assert t034.run_active_row(dbname, explicit)["max_active_ms"] == 5000
    # Unknown is unknown: never guessed as zero or truncated.
    for name in ("no_policy", "fractional", "out_of_range", "wrong_type"):
        assert t034.run_active_row(dbname, runs[name])["max_active_ms"] is None, name

    ledgers = st.sql_rows(
        dbname, "SELECT version, name FROM platform.schema_migrations WHERE version = '0020'"
    )
    assert ledgers == [{"version": "0020", "name": "0020_active_time_and_deadline.sql"}], ledgers
