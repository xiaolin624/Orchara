"""F08/T074: the conversation/request migration.

Every assertion reads the real catalogue of a database migrated by the compiled
``cmd/admin`` chain. The chain invariant is continuity, never a pinned tail; this
ticket finds its own migration by slug so the merger can renumber it.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_conversations_and_requests.sql"


def columns_of(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default, udt_name "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    return " ".join(
        row["definition"]
        for row in st.sql_rows(
            dbname,
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            f"WHERE conrelid = 'platform.{table}'::regclass",
        )
    )


def index_names(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'platform' "
        f"AND tablename = '{table}'",
    )
    return {row["indexname"] for row in rows}


def test_the_t074_migration_is_one_link_of_a_continuous_chain(
    migrated_database, go_binaries
) -> None:
    dsn, dbname = migrated_database
    chain = st.migration_chain(dbname)
    versions = [version for version, name in chain.items() if name.endswith(SLUG)]
    assert len(versions) == 1, chain
    version = versions[0]
    assert chain[version] == version + SLUG, chain[version]

    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    assert "no pending migrations" in (completed.stdout + completed.stderr), completed.stdout
    assert st.migration_chain(dbname) == chain


def test_the_conversation_request_interaction_and_handover_tables_exist(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    for table in ("conversations", "requests", "interactions", "request_handoffs"):
        assert st.sql(
            dbname, f"SELECT to_regclass('platform.{table}')::text"
        ) == "platform." + table, table

    conversations = columns_of(dbname, "conversations")
    for name in ("id", "owner_id", "revision", "created_at", "deleted_at"):
        assert name in conversations, (name, conversations)
    assert conversations["deleted_at"]["is_nullable"] == "YES"
    assert "1" in (conversations["revision"]["column_default"] or "")
    assert "conversations_owner" in index_names(dbname, "conversations")

    requests = columns_of(dbname, "requests")
    for name in (
        "conversation_id",
        "owner_id",
        "input_artifact_id",
        "artifact_ids",
        "state",
        "revision",
        "error_code",
        "requested_version_id",
        "control_calls",
        "created_at",
        "updated_at",
    ):
        assert name in requests, (name, requests)
    assert requests["artifact_ids"]["udt_name"] == "_uuid", requests["artifact_ids"]
    assert requests["control_calls"]["data_type"] == "integer"
    definitions = constraint_definitions(dbname, "requests")
    for state_value in (
        "queued",
        "routing",
        "awaiting_input",
        "awaiting_selection",
        "dispatched",
        "cancelled",
        "failed",
    ):
        assert "'" + state_value + "'" in definitions, definitions
    assert "control_calls <= 3" in definitions, definitions
    assert "requests_queue" in index_names(dbname, "requests")

    # The partial unique index is the pre-dispatch half of the logical slot; the
    # live-top-run half is 0014's index, which must still exist.
    slot_index = st.sql_rows(
        dbname,
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'platform' "
        "AND indexname = 'one_active_request_per_conversation'",
    )
    assert slot_index, "one_active_request_per_conversation is missing"
    assert "UNIQUE" in slot_index[0]["indexdef"], slot_index
    assert "routing" in slot_index[0]["indexdef"], slot_index
    assert "awaiting_input" in slot_index[0]["indexdef"], slot_index
    assert "one_active_top_run_per_conversation" in index_names(dbname, "runs")

    interactions = columns_of(dbname, "interactions")
    for name in (
        "request_id",
        "kind",
        "state",
        "proposal_artifact_id",
        "response_artifact_id",
        "correlation_id",
        "expires_at",
        "answered_at",
    ):
        assert name in interactions, (name, interactions)
    assert interactions["proposal_artifact_id"]["is_nullable"] == "NO"
    assert interactions["response_artifact_id"]["is_nullable"] == "YES"
    for index in ("one_pending_interaction", "interactions_correlation"):
        assert index in index_names(dbname, "interactions"), index

    handoffs = columns_of(dbname, "request_handoffs")
    for name in (
        "request_id",
        "conversation_id",
        "summary_artifact_id",
        "source_run_ids",
        "artifact_ids",
        "confirmed_by",
        "confirmed_at",
        "from_version_id",
    ):
        assert name in handoffs, (name, handoffs)
    assert handoffs["source_run_ids"]["udt_name"] == "_uuid"


def test_the_three_deferred_foreign_keys_are_completed(migrated_database) -> None:
    _dsn, dbname = migrated_database
    runs = constraint_definitions(dbname, "runs")
    assert "FOREIGN KEY (request_id) REFERENCES platform.requests(id)" in runs, runs
    assert "FOREIGN KEY (conversation_id) REFERENCES platform.conversations(id)" in runs, runs
    model_calls = constraint_definitions(dbname, "platform_model_calls")
    assert "FOREIGN KEY (request_id) REFERENCES platform.requests(id)" in model_calls, model_calls


def test_the_request_migration_keeps_the_worker_privilege_boundary(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    for table in ("conversations", "requests", "interactions", "request_handoffs"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            allowed = st.sql(
                dbname,
                f"SELECT has_table_privilege('orchara_worker', 'platform.{table}', "
                f"'{privilege}')::text",
            )
            assert allowed == "false", (table, privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
