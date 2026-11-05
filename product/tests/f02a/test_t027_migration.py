"""T027: migration 0014 creates the execution identity tables and the deferred FK.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. The shape, constraints and privilege wall are
database facts, not Go conventions.
"""

from __future__ import annotations

from support import f00b, t025
from support import stack as st
from support import t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLES = (
    "execution_snapshots",
    "runs",
    "jobs",
    "run_artifacts",
    "idempotency_keys",
)

COLUMNS = {
    "execution_snapshots": (
        "id", "kind", "application_id", "application_version_id", "draft_revision",
        "owner_id", "manifest", "manifest_sha256", "manifest_artifact_id",
        "compiler_version", "worker_image_digest", "state_schema_version", "created_at",
    ),
    "runs": (
        "id", "request_id", "conversation_id", "parent_run_id", "parent_invocation_key",
        "owner_id", "kind", "execution_snapshot_id", "application_version_id",
        "manifest_sha256", "input_artifact_id", "output_artifact_id",
        "checkpoint_thread_id", "state", "revision", "next_event_seq", "budget_limit",
        "currency", "phase", "active_elapsed_ms", "cancel_requested_at", "error_code",
        "started_at", "finished_at", "deleted_at", "created_at",
        # T034: the frozen active-time limit (NULL = unknown/no deadline).
        "max_active_ms",
        # T038: the parked recovery reason/since pair (non-contract extension).
        "recovery_reason", "recovery_since",
    ),
    "jobs": (
        "id", "run_id", "kind", "state", "available_at", "current_attempt_token",
        "resume_artifact_id", "created_at",
    ),
    "run_artifacts": ("run_id", "artifact_id", "attempt_token", "relation"),
    "idempotency_keys": (
        "principal_id", "method", "path", "key", "request_sha256", "response_code",
        "response_artifact_id", "response_json", "expires_at",
    ),
}


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def foreign_keys_of(dbname: str, table: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT c.conname, a.attname AS column_name, confrelid::regclass::text AS target "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass "
        "ORDER BY c.conname, column_name",
    )


def constraint_definitions(dbname: str, table: str, kinds: tuple[str, ...] = ("c",)) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass "
        f"AND contype = ANY(ARRAY{list(kinds)})",
    )
    return " ".join(row["definition"] for row in rows)


def test_0014_is_recorded_once_at_its_position(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # The chain is contiguous from 0001 and each migration is recorded exactly
    # once. (A pinned "ends at 00NN" list rotted when T041/T042/T045 appended
    # migrations; the invariant is what this test is for, plus the ticket's own
    # row at its position.)
    assert versions == [f"{number:04d}" for number in range(1, len(versions) + 1)], versions
    names = {row["version"]: row["name"] for row in ledgers}
    assert names["0014"] == "0014_execution_snapshots_and_runs.sql"
    assert names["0018"] == "0018_run_events.sql"
    assert names["0019"] == "0019_run_artifact_uploads.sql"
    assert names["0020"] == "0020_active_time_and_deadline.sql"
    assert names["0021"] == "0021_run_waitings.sql"
    assert names["0022"] == "0022_run_unknown_operations.sql"
    assert names["0023"] == "0023_tool_builds_and_review.sql"
    assert names["0024"] == "0024_run_recovery_evidence.sql"


def test_0014_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_the_five_tables_match_the_contract_baseline(migrated_database) -> None:
    _dsn, dbname = migrated_database
    rows = st.sql_rows(
        dbname,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'platform' AND table_name = ANY(ARRAY['"
        + "','".join(TABLES)
        + "']) ORDER BY table_name",
    )
    assert [row["table_name"] for row in rows] == sorted(TABLES), rows

    for table, expected in COLUMNS.items():
        columns = columns_of(dbname, table)
        assert set(columns) == set(expected), (table, sorted(columns))

    snapshot = columns_of(dbname, "execution_snapshots")
    assert snapshot["manifest"]["data_type"] == "jsonb"
    assert snapshot["draft_revision"]["is_nullable"] == "YES"
    run = columns_of(dbname, "runs")
    assert run["budget_limit"]["data_type"] == "numeric"
    assert run["budget_limit"]["is_nullable"] == "NO"
    assert run["request_id"]["is_nullable"] == "YES"
    assert run["conversation_id"]["is_nullable"] == "YES"
    assert run["deleted_at"]["is_nullable"] == "YES"
    keys = columns_of(dbname, "idempotency_keys")
    assert keys["response_json"]["data_type"] == "jsonb"
    assert keys["request_sha256"]["is_nullable"] == "NO"
    assert keys["expires_at"]["is_nullable"] == "NO"


def test_the_foreign_keys_are_real_and_the_deferred_ones_are_documented(migrated_database) -> None:
    _dsn, dbname = migrated_database

    def targets(table: str) -> dict[str, str]:
        return {row["column_name"]: row["target"] for row in foreign_keys_of(dbname, table)}

    snapshots = targets("execution_snapshots")
    assert snapshots["application_id"] == "platform.applications"
    assert snapshots["application_version_id"] == "platform.application_versions"
    assert snapshots["owner_id"] == "platform.principals"
    assert snapshots["manifest_artifact_id"] == "platform.artifacts"

    runs = targets("runs")
    assert runs["owner_id"] == "platform.principals"
    assert runs["execution_snapshot_id"] == "platform.execution_snapshots"
    assert runs["application_version_id"] == "platform.application_versions"
    assert runs["input_artifact_id"] == "platform.artifacts"
    assert runs["output_artifact_id"] == "platform.artifacts"
    assert runs["parent_run_id"] == "platform.runs"

    jobs = targets("jobs")
    assert jobs["run_id"] == "platform.runs"
    assert jobs["resume_artifact_id"] == "platform.artifacts"

    linked = targets("run_artifacts")
    assert linked["run_id"] == "platform.runs"
    assert linked["artifact_id"] == "platform.artifacts"

    receipts = targets("idempotency_keys")
    assert receipts["principal_id"] == "platform.principals"
    assert receipts["response_artifact_id"] == "platform.artifacts"

    # The deferred foreign keys: F05/F07 were plain uuids in T027's chain; T074's
    # 0044_conversations_and_requests.sql completed both references. The columns
    # and their FK targets are pinned so a later migration must be explicit.
    assert columns_of(dbname, "runs")["request_id"]["data_type"] == "uuid"
    assert columns_of(dbname, "runs")["conversation_id"]["data_type"] == "uuid"
    assert runs["request_id"] == "platform.requests", runs
    assert runs["conversation_id"] == "platform.conversations", runs
    assert targets("jobs")["current_attempt_token"] == "platform.run_attempts"
    assert targets("run_artifacts")["attempt_token"] == "platform.run_attempts"


def test_the_debug_and_published_identity_checks_are_database_facts(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    application_id = t027.seed_application(dbname, owner, name="t027 identity checks")
    artifact = t025.insert_artifact(dbname, owner, purpose="execution_manifest", sha256="a" * 64)
    digest = "a" * 64

    def insert_snapshot(*, kind: str, version_sql: str, revision_sql: str):
        return st.psql(
            dbname,
            "INSERT INTO platform.execution_snapshots "
            "(id, kind, application_id, application_version_id, draft_revision, owner_id, "
            " manifest, manifest_sha256, manifest_artifact_id, compiler_version, "
            " worker_image_digest, state_schema_version) VALUES "
            f"(gen_random_uuid(), {st.quote_literal(kind)}, {st.quote_literal(application_id)}::uuid, "
            f"{version_sql}, {revision_sql}, {st.quote_literal(owner)}::uuid, '{{}}'::jsonb, "
            f"{st.quote_literal(digest)}, {st.quote_literal(artifact)}::uuid, "
            f"'orchara-compiler/1.0.0', {st.quote_literal(t027.WORKER_IMAGE_DIGEST)}, 'orchara-state/1.0')",
        )

    # A debug snapshot without a draft revision and a published snapshot without
    # a version are both refused by the contract CHECK.
    debug_without_revision = insert_snapshot(kind="debug", version_sql="NULL", revision_sql="NULL")
    assert debug_without_revision.code != 0
    assert "check constraint" in debug_without_revision.stderr.lower(), debug_without_revision.stderr
    published_without_version = insert_snapshot(kind="published", version_sql="NULL", revision_sql="1")
    assert published_without_version.code != 0
    assert "check constraint" in published_without_version.stderr.lower(), published_without_version.stderr
    # A debug snapshot that also carries a version is refused.
    debug_with_version = insert_snapshot(
        kind="debug", version_sql=f"{st.quote_literal(t027.new_uuid())}::uuid", revision_sql="1"
    )
    assert debug_with_version.code != 0
    # And a non-positive draft revision is refused by the tightening CHECK.
    zero_revision = insert_snapshot(kind="debug", version_sql="NULL", revision_sql="0")
    assert zero_revision.code != 0
    assert "check constraint" in zero_revision.stderr.lower(), zero_revision.stderr

    # A malformed digest is refused by the tightening CHECK.
    bad_digest = st.psql(
        dbname,
        "INSERT INTO platform.execution_snapshots "
        "(id, kind, application_id, draft_revision, owner_id, manifest, manifest_sha256, "
        " manifest_artifact_id, compiler_version, worker_image_digest, state_schema_version) "
        f"VALUES (gen_random_uuid(), 'debug', {st.quote_literal(application_id)}::uuid, 1, "
        f"{st.quote_literal(owner)}::uuid, '{{}}'::jsonb, 'not-a-digest', "
        f"{st.quote_literal(artifact)}::uuid, 'orchara-compiler/1.0.0', "
        f"{st.quote_literal(t027.WORKER_IMAGE_DIGEST)}, 'orchara-state/1.0')",
    )
    assert bad_digest.code != 0
    assert "check constraint" in bad_digest.stderr.lower(), bad_digest.stderr

    # The positive case exists too: a real debug snapshot is representable.
    assert t027.seed_execution_snapshot(dbname, owner, application_id=application_id)


def test_the_contract_run_checks_are_present(migrated_database) -> None:
    _dsn, dbname = migrated_database
    definitions = constraint_definitions(dbname, "runs")
    for clause in (
        "kind = 'debug'::text",
        "parent_run_id IS NOT NULL",
        "request_id IS NOT NULL",
        "conversation_id IS NOT NULL",
        "kind = 'published'::text",
        "application_version_id IS NULL",
        "parent_invocation_key IS NOT NULL",
        "budget_limit >= (0)::numeric",
        "phase = ANY",
        "state = ANY",
        "length(manifest_sha256) = 64",
    ):
        assert clause in definitions, (clause, definitions)
    jobs = constraint_definitions(dbname, "jobs")
    assert "kind = ANY" in jobs and "state = ANY" in jobs, jobs
    receipts = constraint_definitions(dbname, "idempotency_keys")
    assert "length(request_sha256) = 64" in receipts, receipts
    assert "jsonb_typeof(response_json)" in receipts, receipts


def test_a_second_debug_run_cannot_share_the_checkpoint_thread(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot = t027.seed_execution_snapshot(dbname, owner)
    artifact = t025.insert_artifact(dbname, owner, purpose="run_input", sha256="b" * 64)

    def insert_run(checkpoint: str):
        run_id = t027.new_uuid()
        return st.psql(
            dbname,
            "INSERT INTO platform.runs "
            "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
            " checkpoint_thread_id, state, budget_limit, currency, phase) VALUES "
            f"({st.quote_literal(run_id)}::uuid, {st.quote_literal(owner)}::uuid, 'debug', "
            f"{st.quote_literal(snapshot)}::uuid, {st.quote_literal('c' * 64)}, "
            f"{st.quote_literal(artifact)}::uuid, {st.quote_literal(checkpoint)}::uuid, "
            "'queued', 1.00, 'USD', 'idle')",
        )

    checkpoint = t027.new_uuid()
    assert insert_run(checkpoint).code == 0
    duplicate = insert_run(checkpoint)
    assert duplicate.code != 0
    assert "duplicate key" in duplicate.stderr.lower(), duplicate.stderr


def test_the_debug_submission_indexes_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    indexes = {
        row["indexname"]: row["tablename"]
        for row in st.sql_rows(
            dbname,
            "SELECT indexname, tablename FROM pg_indexes WHERE schemaname = 'platform' "
            "AND tablename = ANY(ARRAY['execution_snapshots','runs','jobs','run_artifacts','idempotency_keys'])",
        )
    }
    for name in (
        "execution_snapshots_application",
        "one_active_top_run_per_conversation",
        "runs_owner_time",
        "runs_parent",
        "one_live_job_per_run",
        "jobs_ready",
        "idempotency_keys_expiry",
    ):
        assert name in indexes, (name, indexes)
    assert indexes["jobs_ready"] == "jobs"
    assert indexes["idempotency_keys_expiry"] == "idempotency_keys"


def test_the_evaluation_snapshot_foreign_key_is_completed(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot_ids = {
        row["conname"]: row["column_name"]
        for row in foreign_keys_of(dbname, "evaluation_jobs")
        if row["target"] == "platform.execution_snapshots"
    }
    assert snapshot_ids == {"evaluation_jobs_snapshot_fkey": "snapshot_id"}, snapshot_ids

    dataset = t025.insert_artifact(dbname, owner, purpose="dataset", sha256="d" * 64)
    job_id = t027.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.background_jobs (id, kind, target_id, owner_id, dedup_key, state) "
        f"VALUES ({st.quote_literal(job_id)}::uuid, 'application_evaluate', "
        f"{st.quote_literal(t027.new_uuid())}::uuid, {st.quote_literal(owner)}::uuid, "
        f"{st.quote_literal('t027-evaluation-fk')}, 'ready')",
    )

    def insert_evaluation(snapshot_id: str):
        return st.psql(
            dbname,
            "INSERT INTO platform.evaluation_jobs "
            "(id, kind, owner_id, background_job_id, snapshot_id, dataset_artifact_id, state) "
            f"VALUES (gen_random_uuid(), 'application', {st.quote_literal(owner)}::uuid, "
            f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(snapshot_id)}::uuid, "
            f"{st.quote_literal(dataset)}::uuid, 'queued')",
        )

    dangling = insert_evaluation(t027.new_uuid())
    assert dangling.code != 0, "the snapshot FK accepted a dangling reference"
    assert "foreign key" in dangling.stderr.lower(), dangling.stderr

    snapshot = t027.seed_execution_snapshot(dbname, owner)
    assert insert_evaluation(snapshot).code == 0


def test_the_worker_and_public_get_nothing_from_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "execution snapshots select": "SELECT count(*) FROM platform.execution_snapshots",
        "runs select": "SELECT count(*) FROM platform.runs",
        "jobs insert": "INSERT INTO platform.jobs (id, run_id, kind, state) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'start', 'ready')",
        "run artifacts select": "SELECT count(*) FROM platform.run_artifacts",
        "idempotency insert": "INSERT INTO platform.idempotency_keys "
        "(principal_id, method, path, key, request_sha256, expires_at) VALUES "
        "(gen_random_uuid(), 'POST', '/x', 'k', repeat('a', 64), now())",
    }
    for label, statement in statements.items():
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {label}"
        assert "permission denied" in result.stderr.lower(), result.stderr

    grants = st.sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    assert grants == [], grants
    for table in TABLES:
        public_grants = st.sql_rows(
            dbname,
            "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
            "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
            f"WHERE c.oid = 'platform.{table}'::regclass AND acl.grantee = 0",
        )
        assert public_grants == [], (table, public_grants)
