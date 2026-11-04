"""T033: migration 0019 creates the run-bound upload provenance.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` chain. The table is deliberately small: artifact PK, run FK,
attempt FK, purpose (contract enum) and created_at, plus the two access indexes.
"""

from __future__ import annotations

from support import f00b, t025, t027, t029
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLE = "run_artifact_uploads"
PURPOSES = ("node_output", "final_output", "log", "receipt")


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def index_names(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT indexname FROM pg_indexes "
        f"WHERE schemaname = 'platform' AND tablename = '{table}'",
    )
    return {row["indexname"] for row in rows}


def foreign_keys_of(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name, confrelid::regclass::text AS target "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"]: row["target"] for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = ANY(ARRAY['c','u','p'])",
    )
    return " ".join(row["definition"] for row in rows)


class Seeded:
    """One real artifact + run + attempt that a provenance row can reference."""

    def __init__(self, dbname: str) -> None:
        self.owner = f00b.principal_id_for_login(dbname)
        self.artifact = t025.insert_artifact(dbname, self.owner, purpose="run_input",
                                             sha256="a" * 64)
        self.snapshot = t027.seed_execution_snapshot(dbname, self.owner)
        self.input_artifact = t025.insert_artifact(dbname, self.owner, purpose="run_input",
                                                   sha256="b" * 64)
        self.run_id = t029.new_uuid()
        st.sql(
            dbname,
            "INSERT INTO platform.runs "
            "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
            " checkpoint_thread_id, state, budget_limit, currency) VALUES ("
            f"{st.quote_literal(self.run_id)}::uuid, {st.quote_literal(self.owner)}::uuid, "
            f"'debug', {st.quote_literal(self.snapshot)}::uuid, {st.quote_literal('f' * 64)}, "
            f"{st.quote_literal(self.input_artifact)}::uuid, "
            f"{st.quote_literal(t029.new_uuid())}::uuid, 'queued', 1.00, 'USD')",
        )
        self.job_id = t029.new_uuid()
        st.sql(
            dbname,
            "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
            f"{st.quote_literal(self.job_id)}::uuid, {st.quote_literal(self.run_id)}::uuid, "
            "'start', 'ready')",
        )
        self.attempt_token = t029.new_uuid()
        st.sql(
            dbname,
            "INSERT INTO platform.run_attempts "
            "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
            "VALUES ("
            f"{st.quote_literal(self.attempt_token)}::uuid, {st.quote_literal(self.job_id)}::uuid, "
            f"{st.quote_literal(self.run_id)}::uuid, 'migration-worker', 'unit-migration', "
            "now() + interval '60 seconds', now())",
        )


def insert_upload(dbname: str, seeded: Seeded, *, purpose: str = "final_output") -> None:
    st.sql(
        dbname,
        "INSERT INTO platform.run_artifact_uploads "
        "(artifact_id, run_id, attempt_token, purpose) VALUES ("
        f"{st.quote_literal(seeded.artifact)}::uuid, {st.quote_literal(seeded.run_id)}::uuid, "
        f"{st.quote_literal(seeded.attempt_token)}::uuid, {st.quote_literal(purpose)})",
    )


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0019_is_recorded_once_at_the_chain_tail(migrated_database) -> None:
    _dsn, dbname = migrated_database
    # 0019 keeps its own position; later tickets append more rows. The chain is
    # contiguous from 0001 with one row per version, never a pinned tail.
    names = st.migration_chain(dbname)
    assert names["0019"] == "0019_run_artifact_uploads.sql"


def test_0019_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_run_artifact_uploads_matches_the_documented_shape(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, TABLE)
    assert set(columns) == {"artifact_id", "run_id", "attempt_token", "purpose", "created_at"}, sorted(columns)
    assert columns["artifact_id"]["data_type"] == "uuid"
    assert columns["artifact_id"]["is_nullable"] == "NO"
    assert columns["run_id"]["data_type"] == "uuid"
    assert columns["run_id"]["is_nullable"] == "NO"
    assert columns["attempt_token"]["data_type"] == "uuid"
    assert columns["attempt_token"]["is_nullable"] == "NO"
    assert columns["purpose"]["data_type"] == "text"
    assert columns["purpose"]["is_nullable"] == "NO"
    assert columns["created_at"]["data_type"] == "timestamp with time zone"
    assert "now()" in columns["created_at"]["column_default"]

    assert foreign_keys_of(dbname, TABLE) == {
        "artifact_id": "platform.artifacts",
        "run_id": "platform.runs",
        "attempt_token": "platform.run_attempts",
    }
    definitions = constraint_definitions(dbname, TABLE)
    assert "PRIMARY KEY (artifact_id)" in definitions, definitions
    for purpose in PURPOSES:
        assert purpose in definitions, (purpose, definitions)


def test_the_run_and_attempt_indexes_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    indexes = index_names(dbname, TABLE)
    assert "run_artifact_uploads_run" in indexes, indexes
    assert "run_artifact_uploads_attempt" in indexes, indexes
    assert "run_artifact_uploads_pkey" in indexes, indexes


def test_the_database_refuses_an_invalid_provenance(migrated_database) -> None:
    _dsn, dbname = migrated_database
    seeded = Seeded(dbname)
    insert_upload(dbname, seeded)
    assert len(
        st.sql_rows(
            dbname,
            "SELECT 1 FROM platform.run_artifact_uploads "
            f"WHERE run_id = {st.quote_literal(seeded.run_id)}::uuid",
        )
    ) == 1

    second_artifact = t025.insert_artifact(dbname, seeded.owner, purpose="run_input",
                                           sha256="c" * 64)
    rejected = {
        "unknown purpose": (
            "INSERT INTO platform.run_artifact_uploads "
            "(artifact_id, run_id, attempt_token, purpose) VALUES ("
            f"{st.quote_literal(second_artifact)}::uuid, "
            f"{st.quote_literal(seeded.run_id)}::uuid, "
            f"{st.quote_literal(seeded.attempt_token)}::uuid, 'tool_summary')"
        ),
        "dangling artifact": (
            "INSERT INTO platform.run_artifact_uploads "
            "(artifact_id, run_id, attempt_token, purpose) VALUES ("
            f"{st.quote_literal(t029.new_uuid())}::uuid, "
            f"{st.quote_literal(seeded.run_id)}::uuid, "
            f"{st.quote_literal(seeded.attempt_token)}::uuid, 'log')"
        ),
        "dangling run": (
            "INSERT INTO platform.run_artifact_uploads "
            "(artifact_id, run_id, attempt_token, purpose) VALUES ("
            f"{st.quote_literal(second_artifact)}::uuid, "
            f"{st.quote_literal(t029.new_uuid())}::uuid, "
            f"{st.quote_literal(seeded.attempt_token)}::uuid, 'log')"
        ),
        "dangling attempt": (
            "INSERT INTO platform.run_artifact_uploads "
            "(artifact_id, run_id, attempt_token, purpose) VALUES ("
            f"{st.quote_literal(second_artifact)}::uuid, "
            f"{st.quote_literal(seeded.run_id)}::uuid, "
            f"{st.quote_literal(t029.new_uuid())}::uuid, 'log')"
        ),
        "duplicate artifact": (
            "INSERT INTO platform.run_artifact_uploads "
            "(artifact_id, run_id, attempt_token, purpose) VALUES ("
            f"{st.quote_literal(seeded.artifact)}::uuid, "
            f"{st.quote_literal(seeded.run_id)}::uuid, "
            f"{st.quote_literal(seeded.attempt_token)}::uuid, 'log')"
        ),
    }
    for label, statement in rejected.items():
        result = st.psql(dbname, statement)
        assert result.code != 0, f"the database accepted {label}"


def test_worker_and_public_get_nothing_from_run_artifact_uploads(migrated_database) -> None:
    _dsn, dbname = migrated_database
    seeded = Seeded(dbname)
    insert_upload(dbname, seeded)
    statements = {
        "select": "SELECT count(*) FROM platform.run_artifact_uploads",
        "delete": "DELETE FROM platform.run_artifact_uploads",
    }
    for label, statement in statements.items():
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {label}"
        assert "permission denied" in result.stderr.lower(), result.stderr

    grants = st.sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform' "
        f"AND table_name = '{TABLE}'",
    )
    assert grants == [], grants
    public_grants = st.sql_rows(
        dbname,
        "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        f"WHERE c.oid = 'platform.{TABLE}'::regclass AND acl.grantee = 0",
    )
    assert public_grants == [], public_grants
