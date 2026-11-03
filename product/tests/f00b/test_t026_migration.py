"""T026 acceptance: migration 0013 (evaluation jobs/reports + deferred FKs).

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. The point of the ticket is that the shape, the
constraints and the privilege wall are database facts, not Go conventions.
"""

from __future__ import annotations

from pathlib import Path

from support import f00b, t024, t027
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

EVALUATION_COLUMNS = (
    "id",
    "kind",
    "owner_id",
    "background_job_id",
    "snapshot_id",
    "model_profile_version_id",
    "dataset_artifact_id",
    "report_id",
    "state",
    "error_code",
    "created_at",
    "finished_at",
)
REPORT_COLUMNS = (
    "id",
    "manifest_sha256",
    "dataset_sha256",
    "result_artifact_id",
    "status",
    "created_at",
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


def foreign_keys_of(dbname: str, table: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT c.conname, "
        "       a.attname AS column_name, "
        "       confrelid::regclass::text AS target "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass "
        "ORDER BY c.conname, column_name",
    )


def test_0013_is_recorded_once_at_its_position(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T032 appends 0018; 0013 must stay at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 23)], versions
    assert ledgers[12]["name"] == "0013_evaluation_pool.sql"
    assert ledgers[-5]["name"] == "0018_run_events.sql"
    assert ledgers[-4]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-3]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-2]["name"] == "0021_run_waitings.sql"
    assert ledgers[-1]["name"] == "0022_run_unknown_operations.sql"


def test_0013_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_the_two_tables_match_the_contract_baseline(migrated_database) -> None:
    _dsn, dbname = migrated_database
    evaluation = columns_of(dbname, "evaluation_jobs")
    assert set(evaluation) == set(EVALUATION_COLUMNS), evaluation
    assert evaluation["kind"]["data_type"] == "text"
    assert evaluation["owner_id"]["is_nullable"] == "NO"
    assert evaluation["background_job_id"]["is_nullable"] == "NO"
    assert evaluation["dataset_artifact_id"]["is_nullable"] == "NO"
    assert evaluation["snapshot_id"]["is_nullable"] == "YES"
    assert evaluation["model_profile_version_id"]["is_nullable"] == "YES"
    assert evaluation["report_id"]["is_nullable"] == "YES"
    assert evaluation["finished_at"]["is_nullable"] == "YES"

    report = columns_of(dbname, "evaluation_reports")
    assert set(report) == set(REPORT_COLUMNS), report
    for field in ("manifest_sha256", "dataset_sha256", "result_artifact_id", "status"):
        assert report[field]["is_nullable"] == "NO", report[field]

    background_fks = {
        row["column_name"]: row["target"] for row in foreign_keys_of(dbname, "evaluation_jobs")
    }
    assert background_fks["background_job_id"] == "platform.background_jobs"
    assert background_fks["dataset_artifact_id"] == "platform.artifacts"
    assert background_fks["model_profile_version_id"] == "platform.model_profile_versions"
    assert background_fks["report_id"] == "platform.evaluation_reports"
    # T027 completes the deferred FK: a snapshot reference is now a real one.
    assert background_fks["snapshot_id"] == "platform.execution_snapshots"
    assert "evaluation_jobs_snapshot_fkey" in {
        row["conname"] for row in foreign_keys_of(dbname, "evaluation_jobs")
    }


def test_the_deferred_report_foreign_keys_are_bound(migrated_database, tmp_path: Path) -> None:
    _dsn, dbname = migrated_database
    review_fks = foreign_keys_of(dbname, "publication_reviews")
    assert any(
        row["conname"] == "publication_reviews_report_id_fkey"
        and row["column_name"] == "report_id"
        and row["target"] == "platform.evaluation_reports"
        for row in review_fks
    ), review_fks

    profile_fks = foreign_keys_of(dbname, "model_profile_versions")
    assert any(
        row["conname"] == "model_profile_versions_evaluation_report_id_fkey"
        and row["column_name"] == "evaluation_report_id"
        and row["target"] == "platform.evaluation_reports"
        for row in profile_fks
    ), profile_fks

    # The constraint is real, not just declared: a dangling report id is refused.
    owner = t024.owner_id(dbname)
    candidate = t024.seed_candidate(dbname, tmp_path / "objects", owner)
    dangling = f00b.new_uuid()
    result = st.psql(
        dbname,
        "UPDATE platform.model_profile_versions "
        f"SET evaluation_report_id = {st.quote_literal(dangling)}::uuid "
        f"WHERE id = {st.quote_literal(candidate['id'])}::uuid",
    )
    assert result.code != 0, "the report FK accepted a dangling reference"
    assert "foreign key" in result.stderr.lower(), result.stderr


def test_the_kind_xor_and_state_checks_are_database_facts(migrated_database, tmp_path: Path) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    candidate = t024.seed_candidate(dbname, tmp_path / "objects", owner)
    dataset = f00b.seed_artifact(
        dbname, tmp_path / "objects", owner, state="ready", staging=False, sealed_content=b"d"
    )

    def add_job(key: str) -> str:
        job_id = f00b.new_uuid()
        st.sql(
            dbname,
            "INSERT INTO platform.background_jobs (id, kind, target_id, owner_id, dedup_key, state) "
            f"VALUES ({st.quote_literal(job_id)}::uuid, 'application_evaluate', "
            f"{st.quote_literal(f00b.new_uuid())}::uuid, {st.quote_literal(owner)}::uuid, "
            f"{st.quote_literal(key)}, 'ready')",
        )
        return job_id

    def insert_evaluation(*, kind: str, job_id: str, snapshot: str | None, model: str | None):
        snapshot_sql = "NULL" if snapshot is None else f"{st.quote_literal(snapshot)}::uuid"
        model_sql = "NULL" if model is None else f"{st.quote_literal(model)}::uuid"
        return st.psql(
            dbname,
            "INSERT INTO platform.evaluation_jobs "
            "(id, kind, owner_id, background_job_id, snapshot_id, model_profile_version_id, "
            " dataset_artifact_id, state) VALUES "
            f"(gen_random_uuid(), {st.quote_literal(kind)}, {st.quote_literal(owner)}::uuid, "
            f"{st.quote_literal(job_id)}::uuid, {snapshot_sql}, {model_sql}, "
            f"{st.quote_literal(dataset.id)}::uuid, 'queued')",
        )

    assert insert_evaluation(kind="bogus", job_id=add_job("t026-kind"), snapshot=None, model=None).code != 0
    model_missing = insert_evaluation(
        kind="model", job_id=add_job("t026-model-missing"), snapshot=None, model=None
    )
    assert model_missing.code != 0
    assert "check constraint" in model_missing.stderr.lower(), model_missing.stderr

    both = insert_evaluation(
        kind="model",
        job_id=add_job("t026-both"),
        snapshot=f00b.new_uuid(),
        model=candidate["id"],
    )
    assert both.code != 0, "model kind accepted a snapshot id"
    assert "check constraint" in both.stderr.lower(), both.stderr

    application_ok = insert_evaluation(
        kind="application",
        job_id=add_job("t026-application"),
        snapshot=f00b.new_uuid(),
        model=None,
    )
    # T027's 0014 makes the reference real: a random uuid is now refused by the
    # FK, and a snapshot row created by the real migration is accepted.
    assert application_ok.code != 0, "the snapshot FK accepted a dangling reference"
    assert "foreign key" in application_ok.stderr.lower(), application_ok.stderr
    real_snapshot = t027.seed_execution_snapshot(dbname, owner)
    application_bound = insert_evaluation(
        kind="application",
        job_id=add_job("t026-application-bound"),
        snapshot=real_snapshot,
        model=None,
    )
    assert application_bound.code == 0, application_bound.stderr


def test_the_owner_indexes_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    indexes = {
        row["indexname"]
        for row in st.sql_rows(
            dbname,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'platform' AND tablename = 'evaluation_jobs'",
        )
    }
    assert "evaluation_jobs_owner_state" in indexes, indexes
    assert "evaluation_jobs_report" in indexes, indexes

    constraints = {
        row["conname"]: row["contype"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'platform.evaluation_jobs'::regclass",
        )
    }
    # background_job_id UNIQUE: one evaluation is executed by exactly one job.
    assert constraints.get("evaluation_jobs_background_job_id_key") == "u", constraints


def test_the_worker_and_public_get_nothing_from_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "evaluation jobs select": "SELECT count(*) FROM platform.evaluation_jobs",
        "evaluation jobs insert": "INSERT INTO platform.evaluation_jobs "
        "(id, kind, owner_id, background_job_id, dataset_artifact_id, state) VALUES "
        "(gen_random_uuid(), 'model', gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'queued')",
        "evaluation reports select": "SELECT count(*) FROM platform.evaluation_reports",
        "evaluation reports insert": "INSERT INTO platform.evaluation_reports "
        "(id, manifest_sha256, dataset_sha256, result_artifact_id, status) VALUES "
        "(gen_random_uuid(), repeat('a', 64), repeat('b', 64), gen_random_uuid(), 'passed')",
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
    for table in ("evaluation_jobs", "evaluation_reports"):
        public_grants = st.sql_rows(
            dbname,
            "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
            "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
            f"WHERE c.oid = 'platform.{table}'::regclass AND acl.grantee = 0",
        )
        assert public_grants == [], (table, public_grants)

    control = st.psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t026-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert control.code == 0, control.stderr
