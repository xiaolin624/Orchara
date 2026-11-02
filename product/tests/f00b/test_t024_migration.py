"""T024 acceptance: migration 0011 and its cross-ticket foreign-key honesty.

Three tables are created: the contract baseline ``model_profile_versions`` and
``platform_model_calls`` (plus the params digest), and the T024 provenance table
``background_job_artifacts``. One foreign key is still deliberately deferred:
``platform_model_calls.request_id`` (F05/F07 owns ``requests``).
``model_profile_versions.evaluation_report_id`` was deferred by 0011 and is now
bound by T026's 0013, which creates ``evaluation_reports``. The contract's XOR
rule and attempt uniqueness are real database constraints, and ``orchara_worker``
still gets nothing from `platform`.
"""

from __future__ import annotations

from pathlib import Path

from support import f00b, t024
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

MODEL_PROFILE_COLUMNS = (
    "id",
    "name",
    "provider",
    "model_id",
    "endpoint_ref",
    "secret_ref",
    "config",
    "data_destination",
    "manifest_sha256",
    "manifest_artifact_id",
    "validation_state",
    "evaluation_report_id",
    "revoked_at",
    "created_at",
)
PLATFORM_MODEL_CALL_COLUMNS = (
    "id",
    "owner_id",
    "request_id",
    "background_job_id",
    "model_profile_version_id",
    "scope_key",
    "logical_key",
    "physical_attempt",
    "state",
    "result_artifact_id",
    "currency",
    "reserved_amount",
    "charged_amount",
    "tokens_in",
    "tokens_out",
    "params_sha256",
    "created_at",
)
JOB_ARTIFACT_COLUMNS = ("background_job_id", "artifact_id", "relation", "purpose", "created_at")


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def foreign_key_columns(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"] for row in rows}


def constraints_of(dbname: str, table: str) -> dict[str, str]:
    return {
        row["conname"]: row["contype"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname, contype FROM pg_constraint "
            f"WHERE conrelid = 'platform.{table}'::regclass",
        )
    }


def test_the_three_tables_match_the_contract_baseline(migrated_database) -> None:
    _dsn, dbname = migrated_database
    assert set(MODEL_PROFILE_COLUMNS) <= set(columns_of(dbname, "model_profile_versions"))
    assert set(PLATFORM_MODEL_CALL_COLUMNS) <= set(columns_of(dbname, "platform_model_calls"))
    assert set(JOB_ARTIFACT_COLUMNS) <= set(columns_of(dbname, "background_job_artifacts"))

    calls = columns_of(dbname, "platform_model_calls")
    assert calls["physical_attempt"]["data_type"] == "integer"
    assert calls["reserved_amount"]["data_type"] == "numeric"
    assert calls["params_sha256"]["is_nullable"] == "YES"

    profile = columns_of(dbname, "model_profile_versions")
    assert profile["validation_state"]["column_default"].startswith("'pending'")
    assert profile["evaluation_report_id"]["is_nullable"] == "YES"

    links = constraints_of(dbname, "background_job_artifacts")
    assert links.get("background_job_artifacts_pkey") == "p"
    assert "background_job_artifacts_relation_check" in links
    assert "c" in links.values()


def test_the_foreign_keys_match_their_deferral_owners(migrated_database) -> None:
    _dsn, dbname = migrated_database
    call_fks = foreign_key_columns(dbname, "platform_model_calls")
    assert "request_id" not in call_fks, call_fks
    assert "background_job_id" in call_fks, call_fks
    assert "owner_id" in call_fks
    assert "model_profile_version_id" in call_fks
    assert "result_artifact_id" in call_fks

    profile_fks = foreign_key_columns(dbname, "model_profile_versions")
    # T024 deferred this one to T026; 0013 now binds it to evaluation_reports.
    assert "evaluation_report_id" in profile_fks, profile_fks
    assert "manifest_artifact_id" in profile_fks


def test_the_xor_and_attempt_uniqueness_are_database_facts(migrated_database, tmp_path: Path) -> None:
    _dsn, dbname = migrated_database
    owner = t024.owner_id(dbname)
    candidate = t024.seed_candidate(dbname, tmp_path / "objects", owner)
    job_id = f00b.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.background_jobs (id, kind, target_id, owner_id, dedup_key, state) "
        f"VALUES ({st.quote_literal(job_id)}::uuid, 'model_validate', "
        f"{st.quote_literal(candidate['id'])}::uuid, {st.quote_literal(owner)}::uuid, "
        "'t024-migration', 'ready')",
    )

    def insert_call(*, request_id: str | None, background_job_id: str | None, attempt: int = 1) -> st.PsqlResult:
        request_sql = "NULL" if request_id is None else f"{st.quote_literal(request_id)}::uuid"
        job_sql = "NULL" if background_job_id is None else f"{st.quote_literal(background_job_id)}::uuid"
        return st.psql(
            dbname,
            "INSERT INTO platform.platform_model_calls "
            "(id, owner_id, background_job_id, request_id, model_profile_version_id, scope_key, "
            " logical_key, physical_attempt, state, currency, reserved_amount) VALUES "
            f"(gen_random_uuid(), {st.quote_literal(owner)}::uuid, {job_sql}, {request_sql}, "
            f"{st.quote_literal(candidate['id'])}::uuid, {st.quote_literal(job_id)}, 'k', "
            f"{int(attempt)}, 'prepared', 'USD', 0.00010000)",
        )

    both_null = insert_call(request_id=None, background_job_id=None)
    assert both_null.code != 0
    assert "check constraint" in both_null.stderr.lower(), both_null.stderr

    both_set = insert_call(request_id=f00b.new_uuid(), background_job_id=job_id)
    assert both_set.code != 0
    assert "check constraint" in both_set.stderr.lower(), both_set.stderr

    first = insert_call(request_id=None, background_job_id=job_id)
    assert first.code == 0, first.stderr

    duplicate = insert_call(request_id=None, background_job_id=job_id)
    assert duplicate.code != 0
    assert "duplicate key" in duplicate.stderr.lower(), duplicate.stderr

    next_attempt = insert_call(request_id=None, background_job_id=job_id, attempt=2)
    assert next_attempt.code == 0, next_attempt.stderr


def test_the_provenance_table_is_constrained(migrated_database, tmp_path: Path) -> None:
    _dsn, dbname = migrated_database
    owner = t024.owner_id(dbname)
    candidate = t024.seed_candidate(dbname, tmp_path / "objects", owner)
    job_id = f00b.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.background_jobs (id, kind, target_id, owner_id, dedup_key, state) "
        f"VALUES ({st.quote_literal(job_id)}::uuid, 'model_validate', "
        f"{st.quote_literal(candidate['id'])}::uuid, {st.quote_literal(owner)}::uuid, "
        "'t024-provenance', 'ready')",
    )
    artifact = f00b.seed_artifact(dbname, tmp_path / "objects", owner, state="ready", staging=False,
                                  sealed_content=b"x", content=b"x")
    statement = (
        "INSERT INTO platform.background_job_artifacts "
        "(background_job_id, artifact_id, relation, purpose) VALUES "
        f"({st.quote_literal(job_id)}::uuid, {st.quote_literal(artifact.id)}::uuid, 'output', 'report')"
    )
    assert st.psql(dbname, statement).code == 0
    duplicate = st.psql(dbname, statement)
    assert duplicate.code != 0
    assert "duplicate key" in duplicate.stderr.lower(), duplicate.stderr
    bad_relation = st.psql(
        dbname,
        "INSERT INTO platform.background_job_artifacts "
        "(background_job_id, artifact_id, relation, purpose) VALUES "
        f"({st.quote_literal(job_id)}::uuid, gen_random_uuid(), 'sideways', 'report')",
    )
    assert bad_relation.code != 0
    missing_job = st.psql(
        dbname,
        "INSERT INTO platform.background_job_artifacts "
        "(background_job_id, artifact_id, relation, purpose) VALUES "
        f"(gen_random_uuid(), {st.quote_literal(artifact.id)}::uuid, 'output', 'report')",
    )
    assert missing_job.code != 0
    assert "foreign key" in missing_job.stderr.lower(), missing_job.stderr


def test_the_worker_and_public_get_nothing_from_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "model profiles select": "SELECT count(*) FROM platform.model_profile_versions",
        "model calls select": "SELECT count(*) FROM platform.platform_model_calls",
        "job artifacts select": "SELECT count(*) FROM platform.background_job_artifacts",
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
    for table in ("model_profile_versions", "platform_model_calls", "background_job_artifacts"):
        public_grants = st.sql_rows(
            dbname,
            "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
            "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
            f"WHERE c.oid = 'platform.{table}'::regclass AND acl.grantee = 0",
        )
        assert public_grants == [], (table, public_grants)

    # Control: the same restricted connection still writes its own table, so the
    # denials above are privileges and not a broken connection.
    control = st.psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t024-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert control.code == 0, control.stderr


def test_re_running_the_chain_is_a_no_op(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    assert "no pending migrations" in (completed.stdout + completed.stderr)
