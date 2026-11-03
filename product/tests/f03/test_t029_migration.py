"""T029: migration 0016 creates the attempt/quota tables and completes the two
foreign keys T027 deferred to F03.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. The shape, constraints, indexes, seed row and
privilege wall are database facts, not Go conventions.
"""

from __future__ import annotations

from support import f00b, t025, t027, t029
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLES = ("run_attempts", "quota_counters", "quota_allocations")

COLUMNS = {
    "run_attempts": (
        "token", "job_id", "run_id", "worker_id", "execution_unit_id", "lease_deadline",
        "heartbeat_at", "started_at", "exited_at", "exit_evidence",
        "checkpoint_connections", "revoked_at", "reported_result", "reported_at",
        "checkpoint_id",
        # T034: settled per-attempt active time and its idempotency marker.
        "active_ms", "active_settled_at",
    ),
    "quota_counters": ("scope", "dimension", "used", "capacity"),
    "quota_allocations": ("id", "scope", "dimension", "holder_key", "released_at"),
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


def index_names(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT indexname FROM pg_indexes "
        f"WHERE schemaname = 'platform' AND tablename = '{table}'",
    )
    return {row["indexname"] for row in rows}


def constraint_definitions(dbname: str, table: str, kinds: tuple[str, ...] = ("c",)) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass "
        f"AND contype = ANY(ARRAY{list(kinds)})",
    )
    return " ".join(row["definition"] for row in rows)


def seed_run_and_job(
    dbname: str,
    *,
    conversation_id: str | None = None,
    state: str = "queued",
) -> tuple[str, str]:
    """One real debug run (seeded snapshot/artifacts) and its start job."""
    owner = f00b.principal_id_for_login(dbname)
    snapshot = t027.seed_execution_snapshot(dbname, owner)
    input_artifact = t025.insert_artifact(dbname, owner, purpose="run_input", sha256="b" * 64)
    run_id = t029.new_uuid()
    conversation = (
        "NULL" if conversation_id is None else f"{st.quote_literal(conversation_id)}::uuid"
    )
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, conversation_id, owner_id, kind, execution_snapshot_id, manifest_sha256, "
        " input_artifact_id, checkpoint_thread_id, state, budget_limit, currency, phase) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {conversation}, "
        f"{st.quote_literal(owner)}::uuid, 'debug', {st.quote_literal(snapshot)}::uuid, "
        f"{st.quote_literal('c' * 64)}, {st.quote_literal(input_artifact)}::uuid, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, {st.quote_literal(state)}, 1.00, 'USD', 'idle')",
    )
    job_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(run_id)}::uuid, 'start', 'ready')",
    )
    return run_id, job_id


def insert_attempt(dbname: str, *, job_id: str, run_id: str, exited: bool = False, revoked: bool = False) -> str:
    token = t029.new_uuid()
    exited_at = "now()" if exited else "NULL"
    revoked_at = "now()" if revoked else "NULL"
    st.sql(
        dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at, "
        " exited_at, revoked_at) VALUES ("
        f"{st.quote_literal(token)}::uuid, {st.quote_literal(job_id)}::uuid, "
        f"{st.quote_literal(run_id)}::uuid, 't029-worker', 'unit-1', now() + interval '30 seconds', "
        f"now(), {exited_at}, {revoked_at})",
    )
    return token


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0016_is_recorded_once_at_the_chain_tail(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T029 appends 0016; T030/T032/T033 append 0017/0018/0019 and T034 appends
    # 0020 and T035 appends 0021. Every earlier migration stays at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 22)], versions
    assert ledgers[14]["name"] == "0015_artifact_admissions.sql"
    assert ledgers[-5]["name"] == "0017_tool_run_kind.sql"
    assert ledgers[-4]["name"] == "0018_run_events.sql"
    assert ledgers[-3]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-2]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-1]["name"] == "0021_run_waitings.sql"


def test_0016_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_the_three_tables_match_the_contract_baseline(migrated_database) -> None:
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

    attempts = columns_of(dbname, "run_attempts")
    assert attempts["token"]["data_type"] == "uuid"
    assert attempts["exit_evidence"]["data_type"] == "jsonb"
    assert attempts["exit_evidence"]["is_nullable"] == "YES"
    assert attempts["checkpoint_connections"]["data_type"] == "jsonb"
    assert attempts["checkpoint_connections"]["is_nullable"] == "NO"
    assert attempts["checkpoint_connections"]["column_default"] == "'[]'::jsonb"
    assert attempts["reported_result"]["data_type"] == "jsonb"
    assert attempts["heartbeat_at"]["is_nullable"] == "NO"
    assert attempts["lease_deadline"]["is_nullable"] == "NO"

    counters = columns_of(dbname, "quota_counters")
    assert counters["used"]["data_type"] == "integer"
    assert counters["capacity"]["data_type"] == "integer"

    allocations = columns_of(dbname, "quota_allocations")
    assert allocations["released_at"]["is_nullable"] == "YES"


def test_the_foreign_keys_are_real(migrated_database) -> None:
    _dsn, dbname = migrated_database

    def targets(table: str) -> dict[str, str]:
        return {row["column_name"]: row["target"] for row in foreign_keys_of(dbname, table)}

    attempts = targets("run_attempts")
    assert attempts["job_id"] == "platform.jobs"
    assert attempts["run_id"] == "platform.runs"

    # The two foreign keys T027 explicitly deferred to F03 now exist.
    jobs = targets("jobs")
    assert jobs["current_attempt_token"] == "platform.run_attempts"
    assert {
        row["conname"]
        for row in foreign_keys_of(dbname, "jobs")
        if row["column_name"] == "current_attempt_token"
    } == {"jobs_current_attempt_token_fkey"}

    linked = targets("run_artifacts")
    assert linked["attempt_token"] == "platform.run_attempts"
    assert {
        row["conname"]
        for row in foreign_keys_of(dbname, "run_artifacts")
        if row["column_name"] == "attempt_token"
    } == {"run_artifacts_attempt_token_fkey"}

    allocations = targets("quota_allocations")
    assert allocations["scope"] == "platform.quota_counters"
    assert allocations["dimension"] == "platform.quota_counters"

    # The completed FKs are enforced, not decorative.
    _run_id, job_id = seed_run_and_job(dbname)
    dangling = st.psql(
        dbname,
        "UPDATE platform.jobs SET current_attempt_token = "
        f"{st.quote_literal(t029.new_uuid())}::uuid "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    assert dangling.code != 0, "jobs.current_attempt_token accepted a dangling token"
    assert "foreign key" in dangling.stderr.lower(), dangling.stderr


# ---------------------------------------------------------------------------
# indexes and invariants
# ---------------------------------------------------------------------------
def test_the_live_attempt_index_is_the_database_invariant(migrated_database) -> None:
    _dsn, dbname = migrated_database
    assert "run_attempts_one_live_per_run" in index_names(dbname, "run_attempts")
    assert "run_attempts_live_lease" in index_names(dbname, "run_attempts")

    run_id, job_id = seed_run_and_job(dbname)
    first = insert_attempt(dbname, job_id=job_id, run_id=run_id)

    second = st.psql(
        dbname,
        "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
        "VALUES (gen_random_uuid(), "
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(run_id)}::uuid, "
        "'t029-worker', 'unit-2', now() + interval '30 seconds', now())",
    )
    assert second.code != 0, "a run accepted two live attempts"
    assert "duplicate key" in second.stderr.lower(), second.stderr

    # An exited attempt no longer occupies the live slot.
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET exited_at = now(), revoked_at = now() "
        f"WHERE token = {st.quote_literal(first)}::uuid",
    )
    replacement = insert_attempt(dbname, job_id=job_id, run_id=run_id)
    assert len(t029.attempts_for_run(dbname, run_id)) == 2

    # Revocation alone also frees the slot: a revoked attempt can never be live.
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET revoked_at = COALESCE(revoked_at, now()) "
        f"WHERE token = {st.quote_literal(replacement)}::uuid",
    )
    third = insert_attempt(dbname, job_id=job_id, run_id=run_id)
    rows = t029.attempts_for_run(dbname, run_id)
    assert {row["token"] for row in rows} == {first, replacement, third}, rows
    # Only one attempt is still live.
    live = [row for row in rows if row["exited_at"] is None and row["revoked_at"] is None]
    assert len(live) == 1, rows


# ---------------------------------------------------------------------------
# quota
# ---------------------------------------------------------------------------
def test_the_quota_seed_is_present_and_is_a_deployment_knob(migrated_database) -> None:
    _dsn, dbname = migrated_database
    counter = t029.quota_counter(dbname)
    assert counter == {
        "scope": "global",
        "dimension": "execution_slots",
        "used": 0,
        "capacity": 2,
    }, counter

    # The capacity is a deployment knob: an operator (or a test) can change it,
    # and the database refuses an over-commit or a negative count.
    st.sql(
        dbname,
        "UPDATE platform.quota_counters SET capacity = 5 "
        f"WHERE scope = {st.quote_literal(t029.QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(t029.QUOTA_DIMENSION)}",
    )
    assert t029.quota_counter(dbname)["capacity"] == 5

    over_commit = st.psql(
        dbname,
        "UPDATE platform.quota_counters SET used = capacity + 1 "
        f"WHERE scope = {st.quote_literal(t029.QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(t029.QUOTA_DIMENSION)}",
    )
    assert over_commit.code != 0
    assert "check constraint" in over_commit.stderr.lower(), over_commit.stderr

    negative = st.psql(
        dbname,
        "UPDATE platform.quota_counters SET used = -1 "
        f"WHERE scope = {st.quote_literal(t029.QUOTA_SCOPE)} "
        f"AND dimension = {st.quote_literal(t029.QUOTA_DIMENSION)}",
    )
    assert negative.code != 0
    assert "check constraint" in negative.stderr.lower(), negative.stderr

    # The allocation is unique per holder: the release/reclaim path cannot create
    # a second live row for the same holder key.
    definitions = constraint_definitions(dbname, "quota_allocations", ("u",))
    assert "UNIQUE (scope, dimension, holder_key)" in definitions, definitions


def test_worker_and_public_get_nothing_from_the_new_tables(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "run_attempts select": "SELECT count(*) FROM platform.run_attempts",
        "run_attempts insert": "INSERT INTO platform.run_attempts "
        "(token, job_id, run_id, worker_id, execution_unit_id, lease_deadline, heartbeat_at) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), 'w', 'u', now(), now())",
        "quota_counters select": "SELECT count(*) FROM platform.quota_counters",
        "quota_counters update": "UPDATE platform.quota_counters SET used = 0",
        "quota_allocations insert": "INSERT INTO platform.quota_allocations "
        "(id, scope, dimension, holder_key) "
        "VALUES (gen_random_uuid(), 'global', 'execution_slots', 'job:x')",
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
