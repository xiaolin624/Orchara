"""F08/T082: the handover scope migration.

Every assertion reads the real catalogue of a database migrated by the compiled
``cmd/admin`` chain. The chain invariant is continuity, never a pinned tail; this
ticket finds its own migration by slug so the merger can renumber it.
"""

from __future__ import annotations

import uuid

from support import f00b, f08, t077
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

SLUG = "_handoff_scope_bounds.sql"


def constraint_definitions(dbname: str, table: str) -> str:
    return " ".join(
        row["definition"]
        for row in st.sql_rows(
            dbname,
            "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
            f"WHERE conrelid = 'platform.{table}'::regclass",
        )
    )


def test_the_t082_migration_is_one_link_of_a_continuous_chain(
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


def test_the_handover_scope_is_bounded_in_the_database(migrated_database) -> None:
    _dsn, dbname = migrated_database
    definitions = constraint_definitions(dbname, "request_handoffs")
    # Minimal scope is a database fact, not only a Go check: at most five source
    # Runs and at most five attachment ids can ever be recorded for one handover.
    assert "cardinality(source_run_ids) <= 5" in definitions, definitions
    assert "cardinality(artifact_ids) <= 5" in definitions, definitions
    assert "cardinality(source_run_ids) >= 1" in definitions, definitions


def test_over_broad_handover_rows_are_rejected_by_the_database(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    version = t077.publish_version(
        stack, owner.session, purpose="交接范围边界", name="T082 迁移边界应用")
    request = f08.request_of(f08.submit_request(
        owner.session, conversation_id, "a request without a handover",
        version_id=version.version_id, key=f00b.new_uuid(),
    ))
    row = f08.request_row(stack.dbname, request["id"])
    assert row["input_artifact_id"]

    def insert_handoff(source_run_ids: list[str], artifact_ids: list[str]) -> st.PsqlResult:
        sources = ("ARRAY[" + ",".join(
            st.quote_literal(value) + "::uuid" for value in source_run_ids) + "]"
            if source_run_ids else "ARRAY[]::uuid[]")
        artifacts = ("ARRAY[" + ",".join(
            st.quote_literal(value) + "::uuid" for value in artifact_ids) + "]"
            if artifact_ids else "ARRAY[]::uuid[]")
        query = (
            "INSERT INTO platform.request_handoffs "
            "(request_id, conversation_id, summary_artifact_id, source_run_ids, "
            " artifact_ids, confirmed_by) VALUES ("
            f"{st.quote_literal(request['id'])}::uuid, "
            f"{st.quote_literal(conversation_id)}::uuid, "
            f"{st.quote_literal(row['input_artifact_id'])}::uuid, "
            f"{sources}, {artifacts}, "
            f"{st.quote_literal(owner.principal_id)}::uuid)"
        )
        return st.psql(stack.dbname, query)

    six_runs = [f00b.new_uuid() for _ in range(6)]
    rejected = insert_handoff(six_runs, [])
    assert rejected.code != 0, rejected.output
    assert "request_handoffs_source_run_ids_bound" in rejected.output, rejected.output

    six_artifacts = [f00b.new_uuid() for _ in range(6)]
    rejected = insert_handoff([f00b.new_uuid()], six_artifacts)
    assert rejected.code != 0, rejected.output
    assert "request_handoffs_artifact_ids_bound" in rejected.output, rejected.output

    # The bound is an upper bound, not a ban: exactly five of each is accepted by
    # the database (the API still applies the ownership/summary gates).
    accepted = insert_handoff(
        [f00b.new_uuid() for _ in range(5)], [f00b.new_uuid() for _ in range(5)])
    assert accepted.code == 0, accepted.output
    assert uuid.UUID(f08.handoff_row(stack.dbname, request["id"])["request_id"])
