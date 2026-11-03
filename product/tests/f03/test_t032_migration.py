"""T032: migration 0018 creates the durable run-event timeline.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary: the table is the contract's ``run_events`` baseline
plus the two documented operational indexes and the closed event vocabulary.
"""

from __future__ import annotations

from support import f00b, t025, t027, t029, t032
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLE = "run_events"


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


def constraint_definitions(
    dbname: str, table: str, kinds: tuple[str, ...] = ("c", "u", "p")
) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass "
        f"AND contype = ANY(ARRAY{list(kinds)})",
    )
    return " ".join(row["definition"] for row in rows)


def seed_run(dbname: str) -> str:
    """One real debug run (seeded snapshot/input artifact) to hang events on."""
    owner = f00b.principal_id_for_login(dbname)
    snapshot = t027.seed_execution_snapshot(dbname, owner)
    input_artifact = t025.insert_artifact(dbname, owner, purpose="run_input", sha256="e" * 64)
    run_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, owner_id, kind, execution_snapshot_id, manifest_sha256, input_artifact_id, "
        " checkpoint_thread_id, state, budget_limit, currency) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(owner)}::uuid, 'debug', "
        f"{st.quote_literal(snapshot)}::uuid, {st.quote_literal('f' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        "'queued', 1.00, 'USD')",
    )
    return run_id


def insert_event(
    dbname: str,
    run_id: str,
    *,
    seq: int = 1,
    event_id: str | None = None,
    event_type: str = "run.status",
    revision: int = 1,
    payload: str = '{"state":"queued"}',
) -> str:
    identifier = event_id or t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
        f"VALUES ({st.quote_literal(run_id)}::uuid, {seq}, "
        f"{st.quote_literal(identifier)}::uuid, {st.quote_literal(event_type)}, {revision}, "
        f"{st.quote_literal(payload)}::jsonb)",
    )
    return identifier


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0018_is_recorded_once_at_the_chain_tail(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T034/T035/T036 append 0020/0021/0022; 0018 stays at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 23)], versions
    assert ledgers[14]["name"] == "0015_artifact_admissions.sql"
    assert ledgers[-6]["name"] == "0017_tool_run_kind.sql"
    assert ledgers[-5]["name"] == "0018_run_events.sql"
    assert ledgers[-4]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-3]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-2]["name"] == "0021_run_waitings.sql"
    assert ledgers[-1]["name"] == "0022_run_unknown_operations.sql"


def test_0018_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_run_events_matches_the_contract_baseline(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, TABLE)
    assert set(columns) == {
        "run_id",
        "seq",
        "event_id",
        "type",
        "revision",
        "payload",
        "created_at",
    }, sorted(columns)
    assert columns["run_id"]["data_type"] == "uuid"
    assert columns["run_id"]["is_nullable"] == "NO"
    assert columns["seq"]["data_type"] == "bigint"
    assert columns["seq"]["is_nullable"] == "NO"
    assert columns["event_id"]["data_type"] == "uuid"
    assert columns["event_id"]["is_nullable"] == "NO"
    assert columns["revision"]["data_type"] == "bigint"
    assert columns["payload"]["data_type"] == "jsonb"
    assert columns["payload"]["is_nullable"] == "NO"
    assert columns["created_at"]["data_type"] == "timestamp with time zone"
    assert "now()" in columns["created_at"]["column_default"]

    assert foreign_keys_of(dbname, TABLE) == {"run_id": "platform.runs"}
    definitions = constraint_definitions(dbname, TABLE)
    assert "PRIMARY KEY (run_id, seq)" in definitions, definitions
    assert "UNIQUE (event_id)" in definitions, definitions
    assert "seq > 0" in definitions, definitions
    assert "revision > 0" in definitions, definitions
    assert "jsonb_typeof(payload) = 'object'" in definitions, definitions
    for event_type in (
        "run.status",
        "node.status",
        "approval.required",
        "artifact.ready",
        "usage.updated",
    ):
        assert event_type in definitions, (event_type, definitions)


def test_the_replay_and_retention_indexes_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    indexes = index_names(dbname, TABLE)
    assert "run_events_replay" in indexes, indexes
    assert "run_events_created_at" in indexes, indexes
    # (run_id, seq) is both the primary key and the explicit replay path.
    assert "run_events_pkey" in indexes, indexes


def test_the_database_refuses_an_invalid_event(migrated_database) -> None:
    _dsn, dbname = migrated_database
    run_id = seed_run(dbname)
    first = insert_event(dbname, run_id)

    rejected = {
        "seq 0": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 0, gen_random_uuid(), 'run.status', 1, "
            '\'{"state":"queued"}\'::jsonb)'
        ),
        "revision 0": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 2, gen_random_uuid(), 'run.status', 0, "
            '\'{"state":"queued"}\'::jsonb)'
        ),
        "scalar payload": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 2, gen_random_uuid(), 'run.status', 1, "
            "'42'::jsonb)"
        ),
        "unknown type": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 2, gen_random_uuid(), 'run.nope', 1, "
            '\'{"state":"queued"}\'::jsonb)'
        ),
        "duplicate event_id": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 2, "
            f"{st.quote_literal(first)}::uuid, 'run.status', 1, "
            '\'{"state":"running"}\'::jsonb)'
        ),
        "duplicate seq": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 1, gen_random_uuid(), 'run.status', 1, "
            '\'{"state":"running"}\'::jsonb)'
        ),
        "dangling run": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(t029.new_uuid())}::uuid, 1, gen_random_uuid(), "
            '\'run.status\', 1, \'{"state":"queued"}\'::jsonb)'
        ),
    }
    for label, statement in rejected.items():
        result = st.psql(dbname, statement)
        assert result.code != 0, f"the database accepted {label}"
    assert t032.run_event_count(dbname, run_id) == 1

    # A valid second event is accepted: the rejections above are constraints,
    # not an inability to append.
    insert_event(
        dbname,
        run_id,
        seq=2,
        event_type="run.status",
        revision=2,
        payload='{"state":"running"}',
    )
    assert [row["seq"] for row in t032.run_events(dbname, run_id)] == [1, 2]


def test_worker_and_public_get_nothing_from_run_events(migrated_database) -> None:
    _dsn, dbname = migrated_database
    run_id = seed_run(dbname)
    insert_event(dbname, run_id)

    statements = {
        "select": "SELECT count(*) FROM platform.run_events",
        "insert": (
            "INSERT INTO platform.run_events (run_id, seq, event_id, type, revision, payload) "
            f"VALUES ({st.quote_literal(run_id)}::uuid, 9, gen_random_uuid(), 'run.status', 1, "
            '\'{"state":"queued"}\'::jsonb)'
        ),
        "delete": "DELETE FROM platform.run_events",
    }
    for label, statement in statements.items():
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {label}"
        assert "permission denied" in result.stderr.lower(), result.stderr

    grants = st.sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform' "
        "AND table_name = 'run_events'",
    )
    assert grants == [], grants
    public_grants = st.sql_rows(
        dbname,
        "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        f"WHERE c.oid = 'platform.{TABLE}'::regclass AND acl.grantee = 0",
    )
    assert public_grants == [], public_grants
