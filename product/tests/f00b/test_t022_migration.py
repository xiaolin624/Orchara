"""T022 acceptance: migration 0009 and its privilege wall.

The two tables are the contract's baseline plus the documented T022 deviations
(``artifacts.revision``, the completion bookkeeping columns, the result-event
digest). PostgreSQL enforces the columns, the closed kind list, the finished_at
invariant and the dedup/event uniqueness; ``orchara_worker`` and PUBLIC get
nothing from `platform`.
"""

from __future__ import annotations

import pytest

from support import f00b
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

BASELINE_ARTIFACT_COLUMNS = (
    "id",
    "owner_id",
    "object_key",
    "staging_object_key",
    "expected_sha256",
    "purpose",
    "sha256",
    "media_type",
    "size_bytes",
    "state",
    "created_at",
    "deleted_at",
    "purged_at",
)
T022_ARTIFACT_COLUMNS = ("revision",)
BASELINE_JOB_COLUMNS = (
    "id",
    "kind",
    "target_id",
    "owner_id",
    "expected_revision",
    "expected_sha256",
    "dedup_key",
    "input_artifact_id",
    "result_artifact_id",
    "state",
    "available_at",
    "lease_token",
    "lease_deadline",
    "worker_id",
    "attempt_count",
    "result_event_id",
    "error_code",
    "created_at",
)
T022_JOB_COLUMNS = ("finished_at", "failure_class", "last_error_at", "result_event_digest")


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def test_artifacts_matches_the_contract_plus_revision(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "artifacts")
    for name in BASELINE_ARTIFACT_COLUMNS:
        assert name in columns, f"missing contract column {name}"
    for name in T022_ARTIFACT_COLUMNS:
        assert name in columns, f"missing T022 column {name}"
    assert columns["revision"]["data_type"] == "bigint"
    assert columns["revision"]["is_nullable"] == "NO"
    assert columns["revision"]["column_default"].startswith("1")
    assert columns["state"]["data_type"] == "text"


def test_background_jobs_matches_the_contract_plus_completion_columns(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "background_jobs")
    for name in BASELINE_JOB_COLUMNS:
        assert name in columns, f"missing contract column {name}"
    for name in T022_JOB_COLUMNS:
        assert name in columns, f"missing T022 column {name}"

    constraints = {
        row["conname"]: row["contype"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname, contype FROM pg_constraint "
            "WHERE conrelid = 'platform.background_jobs'::regclass",
        )
    }
    assert constraints.get("background_jobs_pkey") == "p"
    assert constraints.get("background_jobs_kind_dedup") == "u"
    assert constraints.get("background_jobs_result_event_id_key") == "u"
    assert constraints.get("background_jobs_finished_matches_state") == "c"
    assert "c" in constraints.values()

    indexes = {
        row["indexname"]
        for row in st.sql_rows(
            dbname,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'platform' AND tablename = 'background_jobs'",
        )
    }
    assert "background_jobs_ready" in indexes  # contract claim index
    assert "background_jobs_lease_expiry" in indexes
    assert "background_jobs_owner" in indexes


def test_the_database_refuses_an_unknown_kind(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    result = st.psql(
        dbname,
        "INSERT INTO platform.background_jobs (id, kind, target_id, owner_id, dedup_key, state) "
        f"VALUES (gen_random_uuid(), 'sh -c id', gen_random_uuid(), '{owner}'::uuid, 'k', 'ready')",
    )
    assert result.code != 0
    assert "check constraint" in result.stderr.lower(), result.stderr


def test_the_finished_at_invariant_is_enforced(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    target = f00b.new_uuid()
    # A non-terminal job must not claim a completion time.
    result = st.psql(
        dbname,
        "INSERT INTO platform.background_jobs "
        "(id, kind, target_id, owner_id, dedup_key, state, finished_at) "
        f"VALUES (gen_random_uuid(), 'artifact_finalize', '{target}'::uuid, '{owner}'::uuid, "
        "'ready-with-finish', 'ready', now())",
    )
    assert result.code != 0
    assert "check constraint" in result.stderr.lower(), result.stderr
    # A terminal job must carry one.
    result = st.psql(
        dbname,
        "INSERT INTO platform.background_jobs "
        "(id, kind, target_id, owner_id, dedup_key, state, error_code) "
        f"VALUES (gen_random_uuid(), 'artifact_finalize', '{target}'::uuid, '{owner}'::uuid, "
        "'failed-without-finish', 'failed', 'source_deleted')",
    )
    assert result.code != 0
    assert "check constraint" in result.stderr.lower(), result.stderr


def test_the_worker_and_public_get_nothing_from_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "artifacts select": "SELECT count(*) FROM platform.artifacts",
        "artifacts insert": "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, purpose, media_type, state) VALUES "
        "(gen_random_uuid(), gen_random_uuid(), 'sealed/x', 'p', 'text/plain', 'scanning')",
        "jobs select": "SELECT count(*) FROM platform.background_jobs",
        "jobs insert": "INSERT INTO platform.background_jobs "
        "(id, kind, target_id, owner_id, dedup_key) VALUES "
        "(gen_random_uuid(), 'artifact_finalize', gen_random_uuid(), gen_random_uuid(), 'k')",
        "jobs update": "UPDATE platform.background_jobs SET state = 'failed' WHERE false",
        "jobs delete": "DELETE FROM platform.background_jobs WHERE false",
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
    for table in ("artifacts", "background_jobs"):
        public_grants = st.sql_rows(
            dbname,
            "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
            "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
            f"WHERE c.oid = 'platform.{table}'::regclass AND acl.grantee = 0",
        )
        assert public_grants == [], (table, public_grants)

    # Control: the same connection still writes the table it is granted, so the
    # denials above are privileges rather than a broken connection.
    result = st.psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t022-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert result.code == 0, result.stderr


def test_re_running_the_chain_is_a_no_op(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    assert "no pending migrations" in (completed.stdout + completed.stderr)
