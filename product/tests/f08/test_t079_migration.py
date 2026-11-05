"""F08/T079: the router interaction migration.

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

SLUG = "_router_interactions.sql"


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
    return {
        row["indexname"]
        for row in st.sql_rows(
            dbname,
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'platform' "
            f"AND tablename = '{table}'",
        )
    }


def test_the_t079_migration_is_one_link_of_a_continuous_chain(
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


def test_the_candidate_snapshot_and_decision_tables_exist(migrated_database) -> None:
    _dsn, dbname = migrated_database
    for table in ("request_routing_candidates", "request_routing_decisions"):
        assert st.sql(
            dbname, f"SELECT to_regclass('platform.{table}')::text"
        ) == "platform." + table, table

    candidates = columns_of(dbname, "request_routing_candidates")
    for name in (
        "request_id",
        "application_version_id",
        "rank",
        "application_name",
        "version_no",
        "capability_card",
        "card_sha256",
        "visible_at",
    ):
        assert name in candidates, (name, candidates)
    # The ordinal is an integer and there is deliberately no score/probability
    # column: ROUTING-MEMORY-KNOWLEDGE §7 forbids presenting an uncalibrated score.
    assert candidates["rank"]["data_type"] == "integer", candidates["rank"]
    assert candidates["rank"]["is_nullable"] == "NO", candidates["rank"]
    for forbidden in ("score", "probability", "confidence", "rank_score"):
        assert forbidden not in candidates, (forbidden, candidates)
    definitions = constraint_definitions(dbname, "request_routing_candidates")
    assert "rank > 0" in definitions, definitions
    assert "length(card_sha256) = 64" in definitions, definitions
    assert "routing_candidates_rank" in index_names(dbname, "request_routing_candidates")

    decisions = columns_of(dbname, "request_routing_decisions")
    for name in (
        "request_id",
        "attempt",
        "decision",
        "selected_version_id",
        "model_call_id",
        "raw_decision",
        "decided_at",
    ):
        assert name in decisions, (name, decisions)
    decision_defs = constraint_definitions(dbname, "request_routing_decisions")
    for value in ("select", "clarify", "no_match", "explicit_version", "degraded"):
        assert "'" + value + "'" in decision_defs, decision_defs
    assert "attempt > 0" in decision_defs, decision_defs
    assert "FOREIGN KEY (model_call_id) REFERENCES platform.platform_model_calls(id)" in decision_defs
    assert "PRIMARY KEY (request_id, attempt)" in decision_defs.replace("\n", " "), decision_defs


def test_the_request_gains_the_consumed_candidate_selection_column(migrated_database) -> None:
    _dsn, dbname = migrated_database
    requests = columns_of(dbname, "requests")
    assert "selected_version_id" in requests, requests
    assert requests["selected_version_id"]["is_nullable"] == "YES", requests["selected_version_id"]
    definitions = constraint_definitions(dbname, "requests")
    assert (
        "FOREIGN KEY (selected_version_id) REFERENCES platform.application_versions(id)"
        in definitions
    ), definitions
    # The T074 budget backstop is unchanged.
    assert "control_calls <= 3" in definitions, definitions


def test_the_router_tables_keep_the_worker_privilege_boundary(migrated_database) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    for table in ("request_routing_candidates", "request_routing_decisions"):
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
