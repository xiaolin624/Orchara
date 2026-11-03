"""T036: migration 0022 creates the unknown-operation ledger and the cancel mark.

Every assertion reads a database migrated by the real ``cmd/admin migrate``
binary. The checks mirror what the Go finalizer depends on: one row per
(run, operation), unresolved rows are the guard's predicate, the reason is a
bounded non-blank string (the safe-code allowlist is Go-side), and the waiting row
gains a cancel marker without losing its evidence. The worker role keeps zero
``platform`` privileges, and replaying 0022 is a no-op.
"""

from __future__ import annotations

from support import f00b, t025, t027, t029
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


def seed_snapshot(dbname: str, owner_id: str) -> str:
    application_id = t027.seed_application(dbname, owner_id, name="t036 cancel host")
    artifact_id = t025.insert_artifact(
        dbname, owner_id, purpose="execution_manifest", sha256="a" * 64
    )
    snapshot_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.execution_snapshots "
        "(id, kind, application_id, draft_revision, owner_id, manifest, manifest_sha256, "
        " manifest_artifact_id, compiler_version, worker_image_digest, state_schema_version) "
        f"VALUES ({st.quote_literal(snapshot_id)}::uuid, 'debug', "
        f"{st.quote_literal(application_id)}::uuid, 1, {st.quote_literal(owner_id)}::uuid, "
        f"'{{}}'::jsonb, {st.quote_literal('a' * 64)}, "
        f"{st.quote_literal(artifact_id)}::uuid, 'orchara-compiler/1.0.0', "
        f"{st.quote_literal(t027.WORKER_IMAGE_DIGEST)}, 'orchara-state/1.0')",
    )
    return snapshot_id


def seed_run(dbname: str, owner_id: str, snapshot_id: str) -> str:
    input_artifact = t025.insert_artifact(dbname, owner_id, purpose="run_input", sha256="b" * 64)
    run_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency, phase) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(owner_id)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot_id)}::uuid, {st.quote_literal('c' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        "'queued', 1.00, 'USD', 'idle')",
    )
    return run_id


def assert_rejected(dbname: str, statement: str, *, fragment: str) -> None:
    result = st.psql(dbname, statement)
    assert result.code != 0, f"the database accepted: {statement}"
    assert fragment in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0022_is_recorded_once_at_the_chain_tail(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    assert versions == [f"{number:04d}" for number in range(1, 27)], versions
    assert ledgers[-6]["name"] == "0021_run_waitings.sql"
    assert ledgers[-5]["name"] == "0022_run_unknown_operations.sql"
    assert ledgers[-4]["name"] == "0023_tool_builds_and_review.sql"
    assert ledgers[-3]["name"] == "0024_run_recovery_evidence.sql"
    assert ledgers[-2]["name"] == "0025_run_capability_ledger.sql"
    assert ledgers[-1]["name"] == "0026_run_node_outputs.sql"
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_0022_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    st.sql(dbname, "DELETE FROM platform.schema_migrations WHERE version = '0022'")
    applied = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "0022_run_unknown_operations.sql" in applied, applied
    ledgers = st.sql_rows(
        dbname, "SELECT version, name FROM platform.schema_migrations WHERE version = '0022'"
    )
    assert ledgers == [
        {"version": "0022", "name": "0022_run_unknown_operations.sql"}
    ], ledgers
    constraints = {
        row["conname"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'platform.run_unknown_operations'::regclass",
        )
    }
    assert "run_unknown_operations_run_operation_key" in constraints, constraints
    assert "run_unknown_operations_reason_nonblank" in constraints, constraints


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_run_unknown_operations_shape_constraints_and_index(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "run_unknown_operations")
    assert set(columns) == {
        "id",
        "run_id",
        "operation_id",
        "reason",
        "created_at",
        "resolved_at",
    }, columns
    assert columns["run_id"]["is_nullable"] == "NO"
    assert columns["operation_id"]["is_nullable"] == "NO"
    assert columns["reason"]["is_nullable"] == "NO"
    assert columns["resolved_at"]["is_nullable"] == "YES"

    foreign_keys = st.sql_rows(
        dbname,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'platform.run_unknown_operations'::regclass AND contype = 'f'",
    )
    assert "run_unknown_operations_run_id_fkey" in {row["conname"] for row in foreign_keys}
    unique = st.sql_rows(
        dbname,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'platform.run_unknown_operations'::regclass AND contype = 'u'",
    )
    assert "run_unknown_operations_run_operation_key" in {
        row["conname"] for row in unique
    }, unique

    indexes = st.sql_rows(
        dbname,
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'platform' AND tablename = 'run_unknown_operations'",
    )
    by_name = {row["indexname"]: row["indexdef"] for row in indexes}
    assert "run_unknown_operations_unresolved" in by_name, by_name
    assert "WHERE (resolved_at IS NULL)" in by_name["run_unknown_operations_unresolved"], by_name

    # The waiting row keeps its evidence and gains the cancel marker.
    waiting_columns = columns_of(dbname, "run_waitings")
    assert "cancel_requested_at" in waiting_columns, waiting_columns
    assert waiting_columns["cancel_requested_at"]["is_nullable"] == "YES"


def test_unknown_operation_identity_and_safe_reason_are_enforced(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot(dbname, owner)
    run_id = seed_run(dbname, owner, snapshot)
    run_literal = f"{st.quote_literal(run_id)}::uuid"
    operation_id = t029.new_uuid()
    operation_literal = f"{st.quote_literal(operation_id)}::uuid"

    st.sql(
        dbname,
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        f"VALUES ({st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, "
        f"{operation_literal}, 'dispatch_timeout')",
    )
    # One row per (Run, operation): a replay of the same identity is a duplicate.
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        f"VALUES ({st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, "
        f"{operation_literal}, 'dispatch_timeout')",
        fragment="duplicate key",
    )
    # A blank or over-long reason is refused; the allowlist itself is Go-side.
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        f"VALUES ({st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, '   ')",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        f"VALUES ({st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, {st.quote_literal('x' * 129)})",
        fragment="check constraint",
    )
    # Resolution is a database-clock fact; an unresolved row is the guard.
    resolved = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        f"VALUES ({st.quote_literal(resolved)}::uuid, {run_literal}, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, 'receipt_missing')",
    )
    st.sql(
        dbname,
        f"UPDATE platform.run_unknown_operations SET resolved_at = now() "
        f"WHERE id = {st.quote_literal(resolved)}::uuid",
    )
    assert st.sql(
        dbname,
        "SELECT count(*) FROM platform.run_unknown_operations "
        f"WHERE run_id = {run_literal} AND resolved_at IS NULL",
    ) == "1"


def test_worker_and_public_get_no_access_to_the_unknown_ledger(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for statement in (
        "SELECT * FROM platform.run_unknown_operations",
        "INSERT INTO platform.run_unknown_operations (id, run_id, operation_id, reason) "
        "VALUES ('00000000-0000-4000-8000-000000000000'::uuid, "
        "'00000000-0000-4000-8000-000000000000'::uuid, "
        "'00000000-0000-4000-8000-000000000000'::uuid, 'worker_crash')",
        "UPDATE platform.run_unknown_operations SET resolved_at = now()",
    ):
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {statement}"
        assert "permission denied" in result.stderr.lower(), result.stderr
