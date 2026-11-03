"""T025: migration 0012 creates the tool-version read model and manifest dedupe.

Every assertion reads the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. No mocks: the point is that the constraints and
privilege boundary live in the database, not only in Go.
"""

from __future__ import annotations

from support import stack as st
from support import t025
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t025_stack,
)

TABLES = ("tool_versions", "manifest_artifacts")


def test_0012_is_recorded_once_and_replaying_migrate_is_a_noop(t025_stack) -> None:
    ledgers = st.sql_rows(
        t025_stack.dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # T036's 0022 is now the chain tail; 0012 must remain at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 25)], versions
    assert ledgers[11]["name"] == "0012_manifest_and_tool_versions.sql"
    assert ledgers[-7]["name"] == "0018_run_events.sql"
    assert ledgers[-6]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-5]["name"] == "0020_active_time_and_deadline.sql"
    assert ledgers[-4]["name"] == "0021_run_waitings.sql"
    assert ledgers[-3]["name"] == "0022_run_unknown_operations.sql"
    assert ledgers[-2]["name"] == "0023_tool_builds_and_review.sql"
    assert ledgers[-1]["name"] == "0024_run_recovery_evidence.sql"

    replay = st.migrate_or_fail(t025_stack.binaries["admin"], t025_stack.dsn)
    assert "no pending migrations" in replay, replay


def test_the_two_tables_are_platform_qualified(t025_stack) -> None:
    rows = st.sql_rows(
        t025_stack.dbname,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'platform' AND table_name IN ('tool_versions','manifest_artifacts') "
        "ORDER BY table_name",
    )
    assert [row["table_name"] for row in rows] == sorted(TABLES)


def test_tool_versions_match_the_contract_baseline(t025_stack) -> None:
    columns = st.sql_rows(
        t025_stack.dbname,
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'tool_versions' "
        "ORDER BY ordinal_position",
    )
    by_name = {row["column_name"]: row for row in columns}
    # The contract baseline columns are still exactly these; T037 adds the
    # documented build/review extension columns (pinned by its own migration test).
    contract_columns = {
        "id", "owner_id", "source_artifact_id", "input_schema", "output_schema",
        "dependency_lock", "connector_versions", "image_digest", "risk_class",
        "state", "manifest_sha256", "approved_by", "approved_at", "created_at",
    }
    assert contract_columns <= set(by_name), sorted(contract_columns - set(by_name))
    assert set(by_name) == contract_columns | {
        "revision", "source_sha256", "dependency_lock_sha256",
        "build_log_artifact_id", "sbom_artifact_id", "build_bundle_digest",
        "build_started_at", "build_finished_at", "build_error_code",
        "reviewed_by", "reviewed_at", "review_decision", "review_reason",
        "revoked_at", "revoked_by", "revoke_reason",
    }, sorted(set(by_name) - contract_columns)
    for field in ("input_schema", "output_schema", "dependency_lock", "connector_versions"):
        assert by_name[field]["data_type"] == "jsonb", by_name[field]

    fks = st.sql_rows(
        t025_stack.dbname,
        "SELECT confrelid::regclass::text AS target FROM pg_constraint "
        "WHERE conrelid = 'platform.tool_versions'::regclass AND contype = 'f' "
        "ORDER BY target",
    )
    # T037 adds build_log/sbom -> artifacts and reviewed_by/revoked_by -> users;
    # the contract's three reference targets remain the only distinct targets.
    assert {row["target"] for row in fks} == {
        "platform.artifacts",
        "platform.principals",
        "platform.users",
    }, fks

    checks = st.sql_rows(
        t025_stack.dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid = 'platform.tool_versions'::regclass AND contype = 'c'",
    )
    definitions = " ".join(row["definition"] for row in checks)
    for clause in (
        "state <> 'ready'::text",
        "image_digest IS NOT NULL",
        "risk_class = ANY",
        "state = ANY",
    ):
        assert clause in definitions, definitions


def test_a_ready_tool_cannot_exist_without_an_image_and_a_building_tool_can(t025_stack) -> None:
    owner = t025.seed_principal(t025_stack.dbname, "tool-constraints")
    # building without an image is exactly the pre-build state.
    t025.seed_tool_version(
        t025_stack.dbname, owner, state="building", image_digest=None, approved=False
    )
    try:
        st.sql(
            t025_stack.dbname,
            "INSERT INTO platform.tool_versions "
            "(id, owner_id, source_artifact_id, input_schema, output_schema, dependency_lock, "
            " risk_class, state, manifest_sha256) VALUES ("
            "gen_random_uuid(), '"
            + owner
            + "'::uuid, (SELECT id FROM platform.artifacts LIMIT 1), '{}'::jsonb, '{}'::jsonb, "
            "'{}'::jsonb, 'pure', 'ready', '"
            + "f" * 64
            + "')",
        )
    except AssertionError as failure:
        assert "check constraint" in str(failure).lower(), failure
    else:
        raise AssertionError("a ready tool with no image_digest was accepted")


def test_manifest_artifacts_dedeupe_on_application_revision_and_hash(t025_stack) -> None:
    owner = t025.seed_principal(t025_stack.dbname, "manifest-dedupe")
    application_id = t025.seed_application(
        t025_stack.dbname,
        owner,
        definition={"dsl_version": "1.0"},
        capability_card=t025.full_card(),
    )
    artifact_a = t025.insert_artifact(
        t025_stack.dbname, owner, purpose="execution_manifest", sha256="a" * 64
    )
    artifact_b = t025.insert_artifact(
        t025_stack.dbname, owner, purpose="execution_manifest", sha256="b" * 64
    )
    st.sql(
        t025_stack.dbname,
        "INSERT INTO platform.manifest_artifacts "
        "(application_id, execution_revision, manifest_sha256, artifact_id) VALUES ("
        f"'{application_id}'::uuid, 1, '{'a' * 64}', '{artifact_a}'::uuid)",
    )
    try:
        st.sql(
            t025_stack.dbname,
            "INSERT INTO platform.manifest_artifacts "
            "(application_id, execution_revision, manifest_sha256, artifact_id) VALUES ("
            f"'{application_id}'::uuid, 1, '{'a' * 64}', '{artifact_b}'::uuid)",
        )
    except AssertionError as failure:
        assert "duplicate key" in str(failure).lower(), failure
    else:
        raise AssertionError("the same manifest identity was registered twice")

    # A non-64-hex digest is refused by the CHECK, not only by Go.
    try:
        st.sql(
            t025_stack.dbname,
            "INSERT INTO platform.manifest_artifacts "
            "(application_id, execution_revision, manifest_sha256, artifact_id) VALUES ("
            f"'{application_id}'::uuid, 1, 'not-a-digest', '{artifact_a}'::uuid)",
        )
    except AssertionError as failure:
        assert "check constraint" in str(failure).lower(), failure
    else:
        raise AssertionError("a malformed manifest digest was accepted")


def test_the_runtime_role_can_read_nothing_of_the_new_tables(t025_stack) -> None:
    assert st.sql(
        t025_stack.dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')",
    ) == "f"
    for table in TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            granted = st.sql(
                t025_stack.dbname,
                f"SELECT has_table_privilege('orchara_worker', 'platform.{table}', '{privilege}')",
            )
            assert granted == "f", f"orchara_worker has {privilege} on {table}"
