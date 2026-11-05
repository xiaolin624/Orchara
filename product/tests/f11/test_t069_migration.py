"""F11/T069: the deletion foundation migration.

Every assertion reads the real catalogue of a database migrated by the compiled
``cmd/admin`` chain. The chain invariant is continuity, never a pinned tail; this
ticket finds its own migration by slug so the merger can renumber it.
"""

from __future__ import annotations

import json
import uuid

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_deletion_foundation.sql"
SCOPE_VOCABULARY = {
    "object_store",
    "search_index",
    "retrieval_cache",
    "checkpoints",
    "sandbox_temp",
    "third_party_history",
    "backup_catalog",
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


def test_the_t069_migration_is_one_link_of_a_continuous_chain(
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


def test_deletion_jobs_carries_the_progress_retry_and_timestamp_fields(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, "deletion_jobs")
    for name in (
        "scope_details",
        "completed_scopes",
        "attempt_count",
        "error_code",
        "started_at",
        "updated_at",
    ):
        assert name in columns, (name, columns)
    assert columns["scope_details"]["data_type"] == "jsonb", columns["scope_details"]
    assert columns["completed_scopes"]["data_type"] == "jsonb", columns[
        "completed_scopes"
    ]
    assert columns["attempt_count"]["data_type"] == "integer", columns["attempt_count"]
    assert "0" in (columns["attempt_count"]["column_default"] or ""), columns[
        "attempt_count"
    ]
    assert columns["error_code"]["is_nullable"] == "YES", columns["error_code"]
    assert columns["started_at"]["is_nullable"] == "YES", columns["started_at"]
    assert columns["updated_at"]["is_nullable"] == "NO", columns["updated_at"]
    assert "now()" in (columns["updated_at"]["column_default"] or ""), columns[
        "updated_at"
    ]

    definitions = " ".join(
        row["definition"]
        for row in st.sql_rows(
            dbname,
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            "WHERE conrelid = 'platform.deletion_jobs'::regclass",
        )
    )
    for state in ("pending", "running", "completed", "failed"):
        assert "'" + state + "'" in definitions, definitions
    assert "attempt_count >= 0" in definitions, definitions
    assert "jsonb_typeof(completed_scopes) = 'array'" in definitions, definitions
    assert "jsonb_typeof(scope_details) = 'object'" in definitions, definitions
    assert "UNIQUE (resource_type, resource_id, deletion_revision)" in definitions, definitions

    indexes = st.sql_rows(
        dbname,
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'platform' "
        "AND tablename = 'deletion_jobs' ORDER BY indexname",
    )
    assert "deletion_jobs_open" in {row["indexname"] for row in indexes}, indexes


def test_the_legacy_t058_descriptor_array_is_converted_to_the_scope_vocabulary(
    migrated_database, go_binaries
) -> None:
    """An upgrade database with a T058 job is translated once, idempotently.

    The test removes the 0042 ledger row, inserts a real legacy row (the T058
    object-descriptor shape) and lets the compiled migration chain re-apply 0042
    on the same database. The catalog/table statements are idempotent; the data
    conversion is the part this test proves.
    """
    dsn, dbname = migrated_database
    owner_id = st.sql(dbname, "SELECT id::text FROM platform.principals LIMIT 1")
    job_id = str(uuid.uuid4())
    resource_id = str(uuid.uuid4())
    descriptors = [
        {
            "kind": "object",
            "object_key": "sealed/legacy-t058",
            "sha256": "a" * 64,
            "size_bytes": 12,
        },
        {"kind": "artifact", "artifact_id": resource_id},
        {"kind": "embedding", "memory_id": resource_id, "memory_revision": 1},
        {"kind": "memory_row", "memory_id": resource_id, "revision": 2},
    ]
    st.sql(
        dbname,
        "INSERT INTO platform.deletion_jobs "
        "(id, owner_id, resource_type, resource_id, deletion_revision, state, "
        "remaining_scopes) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"'memory', {st.quote_literal(resource_id)}::uuid, 2, 'pending', "
        f"{st.quote_literal(json.dumps(descriptors))}::jsonb)",
    )
    st.sql(dbname, "DELETE FROM platform.schema_migrations WHERE version = '0042'")

    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    row = st.sql_rows(
        dbname,
        "SELECT remaining_scopes::text AS remaining_scopes, "
        "scope_details::text AS scope_details FROM platform.deletion_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )[0]
    assert json.loads(row["remaining_scopes"]) == ["object_store", "search_index"], row
    details = json.loads(row["scope_details"])
    assert set(details) == {"object_store", "search_index"}, details
    kinds = {item["kind"] for item in details["object_store"]["items"]}
    assert kinds == {"object", "artifact"}, details["object_store"]
    assert [item["kind"] for item in details["search_index"]["items"]] == ["embedding"], details[
        "search_index"
    ]
    # `memory_row` is the no-resurrection tombstone, never a purge scope.
    assert "memory_row" not in row["remaining_scopes"], row
    assert "memory_row" not in row["scope_details"], row

    # The second migrate is a no-op and does not re-convert or duplicate anything.
    chain = st.migration_chain(dbname)
    again = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert again.returncode == 0, (again.stdout, again.stderr)
    assert "no pending migrations" in (again.stdout + again.stderr), again.stdout
    assert st.migration_chain(dbname) == chain
    assert (
        st.sql(
            dbname,
            "SELECT remaining_scopes::text FROM platform.deletion_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == '["object_store", "search_index"]'
    )


def test_the_fixed_scope_vocabulary_is_the_one_documented_for_later_tickets(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    # The vocabulary is code, not a database enum, so the independent catalogue
    # proof is the migration's own documented column comment: a silent rename of a
    # scope would make the comment and the contract projection drift.
    comment = st.sql(
        dbname,
        "SELECT col_description('platform.deletion_jobs'::regclass, "
        "  (SELECT attnum FROM pg_attribute "
        "    WHERE attrelid = 'platform.deletion_jobs'::regclass "
        "      AND attname = 'remaining_scopes'))",
    )
    assert comment and "F11/T069" in comment, comment
    for scope in SCOPE_VOCABULARY:
        assert scope in comment, scope


def test_the_deletion_foundation_keeps_the_worker_privilege_boundary(
    migrated_database,
) -> None:
    _dsn, dbname = migrated_database
    role = st.sql(
        dbname,
        "SELECT COALESCE((SELECT 'yes' FROM pg_roles WHERE rolname = 'orchara_worker'), 'no')",
    )
    if role != "yes":
        return
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        allowed = st.sql(
            dbname,
            f"SELECT has_table_privilege('orchara_worker', 'platform.deletion_jobs', "
            f"'{privilege}')::text",
        )
        assert allowed == "false", (privilege, allowed)
    usage = st.sql(
        dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')::text",
    )
    assert usage == "false", usage
