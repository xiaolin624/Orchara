"""T068: the publication/review schema is applied by the real migration chain.

The assertions read the real PostgreSQL catalogue of a database migrated by the
compiled ``cmd/admin migrate`` binary. They are name-based (the merger renumbers
concurrent migrations), so they prove the ticket's schema landed, not a number.
"""

from __future__ import annotations

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

MIGRATION_SUFFIX = "_publication_reviews_and_outbox.sql"


def _version_of(dbname: str, suffix: str) -> str:
    chain = st.migration_chain(dbname)
    matches = [version for version, name in chain.items() if name.endswith(suffix)]
    assert len(matches) == 1, f"exactly one migration named *{suffix} must exist: {chain}"
    return matches[0]


def _columns(dbname: str, table: str) -> dict[str, dict]:
    rows = st.sql_rows(
        dbname,
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        f"WHERE table_schema = 'platform' AND table_name = '{table}' "
        "ORDER BY ordinal_position",
    )
    return {row["column_name"]: row for row in rows}


def _indexes(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT indexname, indexdef FROM pg_indexes "
        f"WHERE schemaname = 'platform' AND tablename = '{table}'",
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


def test_the_ticket_migration_is_applied_once_and_replaying_is_a_noop(
    t068_stack,
) -> None:
    version = _version_of(t068_stack.dbname, MIGRATION_SUFFIX)
    assert version.isdigit() and len(version) == 4, version
    replay = st.migrate_or_fail(t068_stack.binaries["admin"], t068_stack.dsn)
    assert "no pending migrations" in replay, replay


def test_publication_reviews_carry_the_frozen_decision_evidence(t068_stack) -> None:
    columns = _columns(t068_stack.dbname, "publication_reviews")
    expected = {
        "id", "application_id", "submitted_by", "snapshot", "manifest_sha256",
        "report_id", "state", "reviewer_id", "decided_at", "created_at",
        # T068 additions: the admin decision reason and the frozen bindings.
        "reason", "snapshot_id", "evaluation_id", "dataset_sha256",
        "manifest_artifact_id", "version_id",
    }
    assert expected <= set(columns), set(columns) - expected
    assert columns["reason"]["data_type"] == "text"
    assert columns["snapshot_id"]["data_type"] == "uuid"
    assert columns["evaluation_id"]["data_type"] == "uuid"
    assert columns["dataset_sha256"]["data_type"] == "text"
    assert columns["manifest_artifact_id"]["data_type"] == "uuid"
    assert columns["version_id"]["data_type"] == "uuid"

    # At most one live (submitted) review per frozen content; the frozen hash is
    # the content identity, so a duplicate submission cannot become a second row.
    indexes = _indexes(t068_stack.dbname, "publication_reviews")
    submitted = {
        name: definition
        for name, definition in indexes.items()
        if "UNIQUE" in definition.upper() and "state = 'submitted'" in definition
    }
    assert len(submitted) == 1, submitted
    assert "application_id" in next(iter(submitted.values()))
    assert "manifest_sha256" in next(iter(submitted.values()))


def test_versions_are_one_per_review_and_design_revocation_as_state(
    t068_stack,
) -> None:
    columns = _columns(t068_stack.dbname, "application_versions")
    # T070's revocation is an UPDATE on this row, never a delete/rewrite.
    assert {"revoked_at", "revoked_by", "revoke_reason"} <= set(columns), columns
    assert columns["revoke_reason"]["data_type"] == "text"

    constraints = st.sql_rows(
        t068_stack.dbname,
        "SELECT conname, contype, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint "
        "WHERE conrelid = 'platform.application_versions'::regclass",
    )
    unique_review = [
        row for row in constraints
        if row["contype"] == "u" and "review_id" in row["definition"]
    ]
    assert len(unique_review) == 1, constraints
    checks = " ".join(
        row["definition"] for row in constraints if row["contype"] == "c"
    )
    # The revocation fields move together: revoked_at revoked_by (nullable)
    # revoke_reason, or all three NULL. The database refuses a half revocation.
    assert "revoked_at IS NULL" in checks and "revoke_reason IS NOT NULL" in checks, checks


def test_outbox_exists_with_the_contract_shape(t068_stack) -> None:
    columns = _columns(t068_stack.dbname, "outbox")
    assert set(columns) == {
        "id", "kind", "aggregate_id", "payload", "available_at", "delivered_at",
        "attempts",
    }, set(columns)
    assert columns["payload"]["data_type"] == "jsonb"
    assert columns["attempts"]["data_type"] == "integer"
    indexes = _indexes(t068_stack.dbname, "outbox")
    ready = {name: definition for name, definition in indexes.items()
             if "delivered_at IS NULL" in definition}
    assert len(ready) == 1, indexes


def test_worker_and_public_have_no_grant_on_the_new_rows(t068_stack) -> None:
    rows = st.sql_rows(
        t068_stack.dbname,
        "SELECT table_name, privilege_type, grantee FROM information_schema.role_table_grants "
        "WHERE table_schema = 'platform' AND table_name IN ('outbox','publication_reviews') "
        "AND grantee IN ('orchara_worker','PUBLIC')",
    )
    assert rows == [], rows
