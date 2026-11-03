"""T035: migration 0021 creates the durable waiting condition.

Every assertion reads a database migrated by the real ``cmd/admin migrate``
binary. The checks mirror what the Go resume transaction depends on: one row per
Run, a closed kind/decision union, the decision+decision_at pair, and the rule that
a resume job/artifact can only be attached after the supervisor exit fact. The
worker role keeps zero ``platform`` privileges, and replaying 0021 is a no-op.
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


def constraint_definitions(dbname: str, table: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = 'c'",
    )
    return " ".join(row["definition"] for row in rows)


def seed_snapshot(dbname: str, owner_id: str) -> str:
    application_id = t027.seed_application(dbname, owner_id, name="t035 waiting host")
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


def seed_run(dbname: str, owner_id: str, snapshot_id: str) -> tuple[str, str]:
    """One queued debug Run plus its ready start job."""
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
    job_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.jobs (id, run_id, kind, state) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(run_id)}::uuid, 'start', 'ready')",
    )
    return run_id, job_id


def assert_rejected(dbname: str, statement: str, *, fragment: str) -> None:
    result = st.psql(dbname, statement)
    assert result.code != 0, f"the database accepted: {statement}"
    assert fragment in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0021_is_recorded_once_at_the_chain_tail(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    assert versions == [f"{number:04d}" for number in range(1, 23)], versions
    assert ledgers[-3]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-2]["name"] == "0021_run_waitings.sql"
    assert ledgers[-1]["name"] == "0022_run_unknown_operations.sql"
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


def test_0021_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, dbname = migrated_database
    st.sql(dbname, "DELETE FROM platform.schema_migrations WHERE version = '0021'")
    applied = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "0021_run_waitings.sql" in applied, applied
    ledgers = st.sql_rows(
        dbname, "SELECT version, name FROM platform.schema_migrations WHERE version = '0021'"
    )
    assert ledgers == [{"version": "0021", "name": "0021_run_waitings.sql"}], ledgers
    # The constraints survived the replay.
    constraints = {
        row["conname"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'platform.run_waitings'::regclass",
        )
    }
    assert "run_waitings_kind_check" in constraints, constraints
    assert "run_waitings_decision_shape" in constraints, constraints


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_run_waitings_shape_and_foreign_keys(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "run_waitings")
    assert set(columns) == {
        "run_id",
        "kind",
        "checkpoint_id",
        "decision",
        "decision_at",
        "exit_confirmed_at",
        "resume_job_id",
        "resume_artifact_id",
        # T036 adds the durable cancel marker to the same waiting row; the waiting
        # evidence itself is unchanged.
        "cancel_requested_at",
        "created_at",
        "updated_at",
    }, columns
    assert columns["run_id"]["is_nullable"] == "NO"
    assert columns["kind"]["is_nullable"] == "NO"
    assert columns["checkpoint_id"]["is_nullable"] == "NO"
    assert columns["decision"]["data_type"] == "jsonb"
    assert columns["resume_job_id"]["is_nullable"] == "YES"

    foreign_keys = st.sql_rows(
        dbname,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'platform.run_waitings'::regclass AND contype = 'f' "
        "ORDER BY conname",
    )
    names = {row["conname"] for row in foreign_keys}
    assert "run_waitings_run_id_fkey" in names, names
    assert "run_waitings_resume_job_id_fkey" in names, names
    assert "run_waitings_resume_artifact_id_fkey" in names, names

    unique = st.sql_rows(
        dbname,
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'platform.run_waitings'::regclass AND contype = 'u'",
    )
    assert "run_waitings_resume_job_id_key" in {row["conname"] for row in unique}, unique

    definitions = constraint_definitions(dbname, "run_waitings")
    for fragment in (
        "'approval'",
        "'children'",
        "decision IS NULL",
        "decision_at IS NULL",
        "resume_job_id IS NULL",
        "exit_confirmed_at IS NOT NULL",
        "btrim(checkpoint_id)",
    ):
        assert fragment in definitions, definitions


def test_decision_shape_and_exit_pair_are_closed(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot(dbname, owner)
    run_id, job_id = seed_run(dbname, owner, snapshot)
    run_literal = f"{st.quote_literal(run_id)}::uuid"

    base = (
        "INSERT INTO platform.run_waitings (run_id, kind, checkpoint_id) VALUES ("
        f"{run_literal}, 'approval', 'ckpt-migration')"
    )
    st.sql(dbname, base)

    # The valid closed shapes are accepted (each attempt moves both paired columns
    # together: the CHECK pair is intentionally strict).
    def set_decision(payload: str, kind: str = "approval") -> None:
        st.sql(
            dbname,
            "UPDATE platform.run_waitings SET "
            f"decision = {st.quote_literal(payload)}::jsonb, decision_at = now(), "
            f"kind = {st.quote_literal(kind)} WHERE run_id = {run_literal}",
        )

    set_decision('{"kind":"approval","outcome":"approved","reference":"ref"}')
    child_id = t029.new_uuid()
    set_decision(
        '{"kind":"children","outcome":"succeeded","child_run_id":"' + child_id + '"}',
        kind="children",
    )
    st.sql(
        dbname,
        f"UPDATE platform.run_waitings SET decision = NULL, decision_at = NULL, kind = 'approval' "
        f"WHERE run_id = {run_literal}",
    )

    # A decision without its timestamp (or the reverse) is refused.
    assert_rejected(
        dbname,
        "UPDATE platform.run_waitings SET decision = "
        "'{\"kind\":\"approval\",\"outcome\":\"approved\",\"reference\":\"r\"}'::jsonb "
        f"WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    set_decision('{"kind":"approval","outcome":"approved","reference":"r"}')
    assert_rejected(
        dbname,
        f"UPDATE platform.run_waitings SET decision_at = NULL WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        f"UPDATE platform.run_waitings SET decision = NULL WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    st.sql(
        dbname,
        f"UPDATE platform.run_waitings SET decision = NULL, decision_at = NULL "
        f"WHERE run_id = {run_literal}",
    )

    # Wrong kind, wrong outcome, missing/mismatched field and raw Command payloads.
    assert_rejected(
        dbname,
        f"UPDATE platform.run_waitings SET kind = 'done' WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        "UPDATE platform.run_waitings SET decision = "
        "'{\"kind\":\"approval\",\"outcome\":\"maybe\",\"reference\":\"r\"}'::jsonb, "
        f"decision_at = now() WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        "UPDATE platform.run_waitings SET decision = "
        "'{\"kind\":\"approval\",\"outcome\":\"approved\"}'::jsonb, "
        f"decision_at = now() WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        "UPDATE platform.run_waitings SET decision = "
        "'{\"kind\":\"children\",\"outcome\":\"approved\",\"reference\":\"r\"}'::jsonb, "
        f"decision_at = now() WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    assert_rejected(
        dbname,
        "UPDATE platform.run_waitings SET decision = "
        "'{\"resume\":\"arbitrary command\",\"thread_id\":\"u\"}'::jsonb, "
        f"decision_at = now() WHERE run_id = {run_literal}",
        fragment="check constraint",
    )

    # A resume job/artifact without the exit fact is refused; with it, accepted.
    assert_rejected(
        dbname,
        f"UPDATE platform.run_waitings SET resume_job_id = {st.quote_literal(job_id)}::uuid "
        f"WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    artifact = t025.insert_artifact(dbname, owner, purpose="resume_input", sha256="d" * 64)
    assert_rejected(
        dbname,
        f"UPDATE platform.run_waitings SET resume_artifact_id = {st.quote_literal(artifact)}::uuid "
        f"WHERE run_id = {run_literal}",
        fragment="check constraint",
    )
    st.sql(
        dbname,
        f"UPDATE platform.run_waitings SET exit_confirmed_at = now() WHERE run_id = {run_literal}",
    )
    st.sql(
        dbname,
        "UPDATE platform.run_waitings SET decision = NULL, decision_at = NULL, "
        f"resume_job_id = {st.quote_literal(job_id)}::uuid, "
        f"resume_artifact_id = {st.quote_literal(artifact)}::uuid WHERE run_id = {run_literal}",
    )


def test_one_waiting_row_per_run(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    snapshot = seed_snapshot(dbname, owner)
    run_id, _job_id = seed_run(dbname, owner, snapshot)
    st.sql(
        dbname,
        "INSERT INTO platform.run_waitings (run_id, kind, checkpoint_id) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, 'approval', 'ckpt')",
    )
    assert_rejected(
        dbname,
        "INSERT INTO platform.run_waitings (run_id, kind, checkpoint_id) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, 'children', 'ckpt2')",
        fragment="duplicate key",
    )


def test_worker_and_public_get_no_access_to_run_waitings(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for statement in (
        "SELECT * FROM platform.run_waitings",
        "INSERT INTO platform.run_waitings (run_id, kind, checkpoint_id) "
        "VALUES ('00000000-0000-4000-8000-000000000000'::uuid, 'approval', 'x')",
        "UPDATE platform.run_waitings SET kind = 'children'",
    ):
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {statement}"
        assert "permission denied" in result.stderr.lower(), result.stderr


# ---------------------------------------------------------------------------
# top-level session slot: waiting still occupies the logical conversation slot
# ---------------------------------------------------------------------------
def seed_published_top_run(
    dbname: str,
    owner_id: str,
    *,
    conversation_id: str,
    request_id: str,
    state: str,
) -> str:
    application_id = t025.seed_application(dbname, owner_id, name="t035 top-level host")
    version_id = t025.seed_published_version(dbname, owner_id, application_id)
    snapshot_id = t027.seed_execution_snapshot(
        dbname,
        owner_id,
        application_id=application_id,
        kind="published",
        application_version_id=version_id,
    )
    input_artifact = t025.insert_artifact(dbname, owner_id, purpose="run_input", sha256=None)
    run_id = t029.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, request_id, conversation_id, owner_id, kind, execution_snapshot_id, "
        " application_version_id, manifest_sha256, input_artifact_id, checkpoint_thread_id, "
        " state, budget_limit, currency) VALUES ("
        f"{st.quote_literal(run_id)}::uuid, {st.quote_literal(request_id)}::uuid, "
        f"{st.quote_literal(conversation_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"'published', {st.quote_literal(snapshot_id)}::uuid, "
        f"{st.quote_literal(version_id)}::uuid, {st.quote_literal('e' * 64)}, "
        f"{st.quote_literal(input_artifact)}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        f"{st.quote_literal(state)}, 1.00, 'USD')",
    )
    return run_id


def test_waiting_still_occupies_the_top_level_session_slot(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    conversation = t029.new_uuid()
    first = seed_published_top_run(
        dbname,
        owner,
        conversation_id=conversation,
        request_id=t029.new_uuid(),
        state="waiting_approval",
    )

    # A waiting top-level Run is active for the one-active-top-run-per-conversation
    # rule: a second non-terminal top-level Run in the same conversation is refused
    # even though the first released its worker execution slot.
    rejected = st.psql(
        dbname,
        "INSERT INTO platform.runs "
        "(id, request_id, conversation_id, owner_id, kind, execution_snapshot_id, "
        " application_version_id, manifest_sha256, input_artifact_id, checkpoint_thread_id, "
        " state, budget_limit, currency) SELECT "
        f"{st.quote_literal(t029.new_uuid())}::uuid, {st.quote_literal(t029.new_uuid())}::uuid, "
        f"{st.quote_literal(conversation)}::uuid, {st.quote_literal(owner)}::uuid, "
        "kind, execution_snapshot_id, application_version_id, manifest_sha256, "
        "input_artifact_id, "
        f"{st.quote_literal(t029.new_uuid())}::uuid, 'queued', 1.00, 'USD' "
        f"FROM platform.runs WHERE id = {st.quote_literal(first)}::uuid",
    )
    assert rejected.code != 0, "waiting did not hold the top-level session slot"
    assert "one_active_top_run_per_conversation" in rejected.stderr.lower(), rejected.stderr

    # The slot is released only at a terminal state.
    st.sql(
        dbname,
        f"UPDATE platform.runs SET state = 'succeeded' WHERE id = {st.quote_literal(first)}::uuid",
    )
    second = seed_published_top_run(
        dbname,
        owner,
        conversation_id=conversation,
        request_id=t029.new_uuid(),
        state="queued",
    )
    assert second != first
