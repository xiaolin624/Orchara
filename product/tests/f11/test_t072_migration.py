"""F11/T072: the backup deletion ledger's database shape.

The name-based migration assertion follows the ticket's rule (a pinned number rots
when the merger renumbers): the chain applies every file with no gap and exactly
one helper row per version, and the T072 table is found by its name. The ledger
must stay independent of the live resource tables (no foreign keys in either
direction) and append-only in the database, not only in code.
"""

from __future__ import annotations

import uuid

from support import t069, t072
from support import stack as st

MIGRATION_NAME = "0046_deletion_copy_ledger.sql"


def test_the_migration_chain_includes_the_copy_ledger_by_name(t069_stack) -> None:
    stack = t069_stack
    chain = st.migration_chain(stack.dbname)
    matches = [version for version, name in chain.items() if name == MIGRATION_NAME]
    assert len(matches) == 1, chain
    version = matches[0]
    # Repeating `admin migrate` on the same database is a no-op.
    st.migrate_or_fail(stack.binaries["admin"], stack.dsn)
    assert st.migration_chain(stack.dbname) == chain, "a second migrate changed the chain"
    assert chain[version] == MIGRATION_NAME
    assert int(version) >= 42, version


def test_the_ledger_table_has_the_hash_chain_and_copy_class_columns(t069_stack) -> None:
    stack = t069_stack
    columns = {
        row["column_name"]: row
        for row in st.sql_rows(
            stack.dbname,
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'platform' AND table_name = 'deletion_ledger'",
        )
    }
    assert set(columns) == {
        "sequence",
        "deletion_job_id",
        "resource_type",
        "resource_id",
        "deletion_revision",
        "entry_kind",
        "scope",
        "copy_class",
        "scope_key",
        "record",
        "record_sha256",
        "prev_sha256",
        "created_at",
    }, sorted(columns)
    for required in ("deletion_job_id", "resource_type", "resource_id", "deletion_revision",
                     "entry_kind", "record", "record_sha256", "created_at"):
        assert columns[required]["is_nullable"] == "NO", (required, columns[required])

    # The closed vocabularies are enforced by the database, not only by Go.
    for bad in (
        "INSERT INTO platform.deletion_ledger "
        "(deletion_job_id, resource_type, resource_id, deletion_revision, entry_kind, record, "
        "record_sha256) VALUES "
        f"('{uuid.uuid4()}', 'artifact', '{uuid.uuid4()}', 1, 'invented_kind', '{{}}', "
        f"'{'0' * 64}')",
        "INSERT INTO platform.deletion_ledger "
        "(deletion_job_id, resource_type, resource_id, deletion_revision, entry_kind, scope, "
        "copy_class, scope_key, record, record_sha256) VALUES "
        f"('{uuid.uuid4()}', 'artifact', '{uuid.uuid4()}', 1, 'scope_cleared', 'object_store', "
        f"'invented_copy', '{'0' * 64}', '{{}}', '{'0' * 64}')",
        "INSERT INTO platform.deletion_ledger "
        "(deletion_job_id, resource_type, resource_id, deletion_revision, entry_kind, record, "
        "record_sha256) VALUES "
        f"('{uuid.uuid4()}', 'artifact', '{uuid.uuid4()}', 1, 'tombstone', '[]', "
        f"'{'0' * 64}')",
    ):
        refused = st.psql(stack.dbname, bad)
        assert refused.code != 0, bad
        assert "violates" in refused.output, refused.output


def test_the_ledger_is_independent_of_the_live_resource_tables(t069_stack) -> None:
    stack = t069_stack
    foreign_keys = st.sql_rows(
        stack.dbname,
        "SELECT conname, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint WHERE contype = 'f' "
        "AND (confrelid = 'platform.deletion_ledger'::regclass "
        "     OR conrelid = 'platform.deletion_ledger'::regclass)",
    )
    assert foreign_keys == [], foreign_keys
    indexes = {
        row["indexname"]
        for row in st.sql_rows(
            stack.dbname,
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'platform' AND tablename = 'deletion_ledger'",
        )
    }
    assert {"deletion_ledger_pkey", "deletion_ledger_job", "deletion_ledger_resource"} <= indexes, (
        indexes
    )
    triggers = {
        row["tgname"]
        for row in st.sql_rows(
            stack.dbname,
            "SELECT tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'platform' AND c.relname = 'deletion_ledger' "
            "AND NOT t.tgisinternal",
        )
    }
    assert triggers == {"deletion_ledger_no_update_delete", "deletion_ledger_no_truncate"}, (
        triggers
    )


def test_the_ledger_survives_a_repeat_delete_and_keeps_access_helpers_clean(t069_stack) -> None:
    # The ledger is not in the contract schema; the contract's deletion job
    # projection is unchanged (an entry does not alter GET /deletions).
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"migration shape")
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )
    view = t069.get_deletion(owner.creator, job["id"])
    assert set(view.json) == {"id", "state", "remaining_scopes"}, view.json
    assert t072.ledger_count(stack.dbname) == 1
