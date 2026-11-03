"""T038: migration 0024 creates the audited recovery-evidence table and the two
recovery columns on Runs.

Every assertion reads a database migrated by the real ``cmd/admin migrate``
binary. The checks mirror what the recover transaction depends on: one evidence
row per (Run, attempt), a non-unique ``resumed_job_id`` link so every recovery
cycle names the job it re-armed, a real principal as the author, a canonical
SHA-256, and the paired reason/since columns. The worker role keeps zero
``platform`` privileges, and replaying 0024 is a no-op.
"""

from __future__ import annotations

import json

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
    application_id = t027.seed_application(dbname, owner_id, name="t038 recovery host")
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


def seed_run(dbname: str, owner_id: str, snapshot_id: str, *, state: str = "recovery_pending") -> str:
    input_artifact = t025.insert_artifact(dbname, owner_id, purpose="run_input", sha256="b" * 64)
    run_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency, phase) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(owner_id)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot_id)}::uuid, {st.quote_literal('a' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        f"{st.quote_literal(state)}, 1.00, 'USD', 'idle')",
    )
    return run_id


def seed_job(dbname: str, run_id: str, *, state: str = "held") -> str:
    job_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(run_id)}::uuid, 'start', "
        f"{st.quote_literal(state)})",
    )
    return job_id


def seed_attempt(dbname: str, job_id: str, run_id: str) -> str:
    token = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) VALUES ("
        f"{st.quote_literal(token)}::uuid, {st.quote_literal(job_id)}::uuid, "
        f"{st.quote_literal(run_id)}::uuid, 't038-worker', 'unit-1', "
        "now() - interval '5 minutes', now() - interval '5 minutes')",
    )
    return token


def evidence_payload(unit: str = "unit-1") -> str:
    return json.dumps(
        {
            "evidence_kind": "operator_recovery",
            "execution_unit_id": unit,
            "observed_at": "2026-11-03T02:15:04.123456Z",
            "process": {"pid": 4123, "command": "ps", "result": "absent"},
            "sandbox": {"sandbox_id": "sandbox-unit-1", "command": "runsc list", "result": "absent"},
        },
        sort_keys=True,
    )


def insert_evidence(
    dbname: str,
    *,
    run_id: str,
    token: str | None,
    actor: str,
    sha: str = "c" * 64,
    resumed_job_id: str | None = None,
    evidence: str | None = None,
) -> str:
    evidence_id = t029.new_uuid()
    token_sql = "NULL" if token is None else f"{st.quote_literal(token)}::uuid"
    job_sql = "NULL" if resumed_job_id is None else f"{st.quote_literal(resumed_job_id)}::uuid"
    st.sql(
        dbname,
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, attempt_token, evidence, evidence_sha256, recorded_by, resumed_job_id) "
        "VALUES ("
        f"{st.quote_literal(evidence_id)}::uuid, {st.quote_literal(run_id)}::uuid, "
        f"{token_sql}, {st.quote_literal(evidence or evidence_payload())}::jsonb, "
        f"{st.quote_literal(sha)}, {st.quote_literal(actor)}::uuid, {job_sql})",
    )
    return evidence_id


def assert_rejected(dbname: str, statement: str, *, fragment: str) -> None:
    result = st.psql(dbname, statement)
    assert result.code != 0, f"the database accepted: {statement}"
    assert fragment in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0024_is_recorded_once_at_the_chain_tail(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    assert versions == [f"{number:04d}" for number in range(1, 27)], versions
    assert ledgers[-4]["name"] == "0023_tool_builds_and_review.sql"
    assert ledgers[-3]["name"] == "0024_run_recovery_evidence.sql"
    assert ledgers[-2]["name"] == "0025_run_capability_ledger.sql"
    assert ledgers[-1]["name"] == "0026_run_node_outputs.sql"
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_0024_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    st.sql(dbname, "DELETE FROM platform.schema_migrations WHERE version = '0024'")
    applied = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "0024_run_recovery_evidence.sql" in applied, applied
    ledgers = st.sql_rows(
        dbname, "SELECT version, name FROM platform.schema_migrations WHERE version = '0024'"
    )
    assert ledgers == [
        {"version": "0024", "name": "0024_run_recovery_evidence.sql"}
    ], ledgers
    constraints = {
        row["conname"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'platform.run_recovery_evidence'::regclass",
        )
    }
    for name in (
        "run_recovery_evidence_run_attempt_key",
        "run_recovery_evidence_evidence_object",
        "run_recovery_evidence_sha256_format",
    ):
        assert name in constraints, constraints
    # The requeued job is a link, not a uniqueness rule: a later recovery cycle
    # re-arms the same job and must still be able to name it.
    assert "run_recovery_evidence_resumed_job_id_key" not in constraints, constraints
    # The Run columns exist and are paired.
    run_constraints = {
        row["conname"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'platform.runs'::regclass",
        )
    }
    assert "runs_recovery_pair_check" in run_constraints, run_constraints
    assert "runs_recovery_reason_check" in run_constraints, run_constraints


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_run_recovery_evidence_shape_foreign_keys_and_index(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "run_recovery_evidence")
    assert set(columns) == {
        "id",
        "run_id",
        "attempt_token",
        "evidence",
        "evidence_sha256",
        "recorded_by",
        "recorded_at",
        "resumed_job_id",
    }, columns
    assert columns["run_id"]["is_nullable"] == "NO"
    assert columns["evidence"]["data_type"] == "jsonb"
    assert columns["evidence_sha256"]["is_nullable"] == "NO"
    assert columns["recorded_by"]["is_nullable"] == "NO"
    assert columns["recorded_at"]["column_default"] == "now()"
    assert columns["attempt_token"]["is_nullable"] == "YES"
    assert columns["resumed_job_id"]["is_nullable"] == "YES"

    foreign_keys = {
        row["conname"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'platform.run_recovery_evidence'::regclass AND contype = 'f'",
        )
    }
    for name in (
        "run_recovery_evidence_run_id_fkey",
        "run_recovery_evidence_attempt_token_fkey",
        "run_recovery_evidence_recorded_by_fkey",
        "run_recovery_evidence_resumed_job_id_fkey",
    ):
        assert name in foreign_keys, foreign_keys

    indexes = st.sql_rows(
        dbname,
        "SELECT indexname FROM pg_indexes "
        "WHERE schemaname = 'platform' AND tablename = 'run_recovery_evidence'",
    )
    assert "run_recovery_evidence_run" in {row["indexname"] for row in indexes}, indexes

    # The Run columns are nullable text/timestamptz, exactly the parked pair.
    run_columns = columns_of(dbname, "runs")
    assert "recovery_reason" in run_columns, run_columns
    assert run_columns["recovery_reason"]["data_type"] == "text"
    assert run_columns["recovery_reason"]["is_nullable"] == "YES"
    assert "recovery_since" in run_columns, run_columns
    assert run_columns["recovery_since"]["data_type"] == "timestamp with time zone"
    assert run_columns["recovery_since"]["is_nullable"] == "YES"


def test_recovery_evidence_identity_and_safe_values_are_enforced(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot(dbname, owner)
    run_id = seed_run(dbname, owner, snapshot)
    job_id = seed_job(dbname, run_id)
    token = seed_attempt(dbname, job_id, run_id)
    run_literal = f"{st.quote_literal(run_id)}::uuid"

    first = insert_evidence(
        dbname, run_id=run_id, token=token, actor=owner, resumed_job_id=job_id
    )
    assert first
    # One evidence row per (Run, attempt): a repeat is a duplicate.
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, attempt_token, evidence, evidence_sha256, recorded_by) VALUES ("
        f"{st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, "
        f"{st.quote_literal(token)}::uuid, {st.quote_literal(evidence_payload())}::jsonb, "
        f"{st.quote_literal('d' * 64)}, {st.quote_literal(owner)}::uuid)",
        fragment="duplicate key",
    )
    # A later recovery cycle (new attempt) may and must name the same re-armed
    # job: the audit link is retained, only per-attempt uniqueness is enforced.
    other_run = seed_run(dbname, owner, snapshot)
    other_job = seed_job(dbname, other_run)
    other_token = seed_attempt(dbname, other_job, other_run)
    second = insert_evidence(
        dbname, run_id=other_run, token=other_token, actor=owner, resumed_job_id=job_id
    )
    assert second
    named = st.sql_rows(
        dbname,
        "SELECT attempt_token::text AS attempt_token FROM platform.run_recovery_evidence "
        f"WHERE resumed_job_id = {st.quote_literal(job_id)}::uuid ORDER BY attempt_token",
    )
    assert len(named) == 2, named
    assert {row["attempt_token"] for row in named} == {token, other_token}, named
    # A real principal is required (FK) and the hash is a lowercase SHA-256.
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, attempt_token, evidence, evidence_sha256, recorded_by) VALUES ("
        f"{st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, NULL, "
        f"{st.quote_literal(evidence_payload())}::jsonb, {st.quote_literal('d' * 64)}, "
        f"{st.quote_literal(t029.new_uuid())}::uuid)",
        fragment="foreign key",
    )
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, attempt_token, evidence, evidence_sha256, recorded_by) VALUES ("
        f"{st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, NULL, "
        f"{st.quote_literal(evidence_payload())}::jsonb, 'NOT-A-HASH', "
        f"{st.quote_literal(owner)}::uuid)",
        fragment="check constraint",
    )
    # Evidence must be a JSON object (free text/array is refused).
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, attempt_token, evidence, evidence_sha256, recorded_by) VALUES ("
        f"{st.quote_literal(t029.new_uuid())}::uuid, {run_literal}, NULL, "
        f"'[1,2]'::jsonb, {st.quote_literal('d' * 64)}, {st.quote_literal(owner)}::uuid)",
        fragment="check constraint",
    )

    # The parked pair: both NULL or both set, and the reason is a closed code.
    st.sql(
        dbname,
        f"UPDATE platform.runs SET recovery_reason = 'lease_expired', recovery_since = now() "
        f"WHERE id = {run_literal}",
    )
    assert_rejected(
        dbname,
        f"UPDATE platform.runs SET recovery_since = NULL WHERE id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        f"UPDATE platform.runs SET recovery_reason = 'takeover' WHERE id = {run_literal}",
        fragment="check constraint",
    )
    st.sql(
        dbname,
        f"UPDATE platform.runs SET recovery_reason = NULL, recovery_since = NULL "
        f"WHERE id = {run_literal}",
    )


def test_worker_and_public_get_no_access_to_recovery_evidence(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for statement in (
        "SELECT * FROM platform.run_recovery_evidence",
        "INSERT INTO platform.run_recovery_evidence "
        "(id, run_id, evidence, evidence_sha256, recorded_by) VALUES "
        "('00000000-0000-4000-8000-000000000000'::uuid, "
        "'00000000-0000-4000-8000-000000000000'::uuid, '{}'::jsonb, "
        f"{st.quote_literal('e' * 64)}, "
        "'00000000-0000-4000-8000-000000000000'::uuid)",
        "UPDATE platform.runs SET recovery_reason = 'lease_expired', recovery_since = now()",
    ):
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {statement}"
        assert "permission denied" in result.stderr.lower(), result.stderr
