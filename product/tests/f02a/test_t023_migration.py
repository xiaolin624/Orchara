"""T023: migration 0010 creates the draft/version/review tables and privileges.

These assertions read the real catalogue of a database migrated by the real
``cmd/admin migrate`` binary. Nothing is mocked: the point of the ticket is that
the constraints and denials are in the database, not only in Go.
"""

from __future__ import annotations

from support import f02a as f
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLES = (
    "application_drafts",
    "application_versions",
    "publication_reviews",
    "version_dependencies",
)


def _rows(database: str, query: str) -> list[dict]:
    return st.sql_rows(database, query)


def test_0010_is_recorded_once_and_replaying_migrate_is_a_noop(f02a_stack) -> None:
    ledgers = st.sql_rows(
        f02a_stack.dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    # The chain now ends with T034's 0020; 0010 must still be recorded exactly
    # once at its own position.
    assert versions == [f"{number:04d}" for number in range(1, 21)], versions
    assert ledgers[9]["name"] == "0010_application_drafts.sql"
    assert ledgers[-3]["name"] == "0018_run_events.sql"
    assert ledgers[-2]["name"] == "0019_run_artifact_uploads.sql"
    assert ledgers[-1]["name"] == "0020_active_time_and_deadline.sql"

    replay = st.migrate_or_fail(f02a_stack.binaries["admin"], f02a_stack.dsn)
    assert "no pending migrations" in replay, replay


def test_the_four_tables_are_platform_qualified_and_jsonb_typed(f02a_stack) -> None:
    rows = _rows(
        f02a_stack.dbname,
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'platform' AND table_name IN "
        "('application_drafts','application_versions','publication_reviews','version_dependencies') "
        "ORDER BY table_name",
    )
    assert [row["table_name"] for row in rows] == sorted(TABLES)

    columns = _rows(
        f02a_stack.dbname,
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'application_drafts' "
        "ORDER BY ordinal_position",
    )
    by_name = {row["column_name"]: row for row in columns}
    assert set(by_name) == {
        "application_id", "revision", "execution_revision",
        "definition", "layout", "capability_card", "updated_at",
    }
    for field in ("definition", "layout", "capability_card"):
        assert by_name[field]["data_type"] == "jsonb", by_name[field]
        assert by_name[field]["is_nullable"] == "NO"
    assert by_name["revision"]["data_type"] == "bigint"
    assert by_name["execution_revision"]["data_type"] == "bigint"
    assert "1" in (by_name["execution_revision"]["column_default"] or "")


def test_the_draft_pk_is_one_row_per_application_and_revisions_are_positive(f02a_stack) -> None:
    constraints = _rows(
        f02a_stack.dbname,
        "SELECT conname, contype FROM pg_constraint "
        "WHERE conrelid = 'platform.application_drafts'::regclass ORDER BY conname",
    )
    kinds = {row["conname"]: row["contype"] for row in constraints}
    # p = primary key, f = foreign key, c = check. The single-row-per-application
    # rule is the primary key, not an application-level convention.
    assert kinds.get("application_drafts_pkey") == "p"
    checks = _rows(
        f02a_stack.dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid = 'platform.application_drafts'::regclass AND contype = 'c'",
    )
    definitions = " ".join(row["definition"] for row in checks)
    for clause in (
        "jsonb_typeof(definition) = 'object'",
        "jsonb_typeof(layout) = 'object'",
        "jsonb_typeof(capability_card) = 'object'",
        "revision > 0",
        "execution_revision > 0",
    ):
        assert clause in definitions, definitions

    # A non-positive revision is refused by the database even if Go is bypassed.
    try:
        st.sql(
            f02a_stack.dbname,
            "INSERT INTO platform.application_drafts "
            "(application_id, revision, execution_revision, definition) "
            "VALUES (gen_random_uuid(), 0, 1, '{}'::jsonb)",
        )
    except AssertionError as failure:  # st.sql raises AssertionError on a psql error
        assert "check constraint" in str(failure).lower(), failure
    else:
        raise AssertionError("revision <= 0 was accepted")


def test_the_active_version_fk_is_composite_restrict_and_nullable(f02a_stack) -> None:
    rows = _rows(
        f02a_stack.dbname,
        "SELECT conname, confdeltype, pg_get_constraintdef(oid) AS definition "
        "FROM pg_constraint "
        "WHERE conrelid = 'platform.applications'::regclass "
        "  AND conname = 'active_version_belongs_to_app'",
    )
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["confdeltype"] == "r", row  # ON DELETE RESTRICT
    definition = row["definition"]
    assert "FOREIGN KEY (id, active_version_id)" in definition, definition
    assert "REFERENCES platform.application_versions(application_id, id)" in definition, definition

    # Nullable: the "never published" application state is representable.
    nullable = st.sql_rows(
        f02a_stack.dbname,
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'applications' "
        "  AND column_name = 'active_version_id'",
    )
    assert nullable == [{"is_nullable": "YES"}]


def test_publication_reviews_report_id_fk_lands_with_t026(f02a_stack) -> None:
    fks = _rows(
        f02a_stack.dbname,
        "SELECT conname, confrelid::regclass::text AS target "
        "FROM pg_constraint "
        "WHERE conrelid = 'platform.publication_reviews'::regclass AND contype = 'f'",
    )
    assert {row["target"] for row in fks} == {
        "platform.applications",
        "platform.principals",
        "platform.evaluation_reports",
    }, fks
    assert any(
        row["conname"] == "publication_reviews_report_id_fkey"
        and row["target"] == "platform.evaluation_reports"
        for row in fks
    ), "T026's 0013 must add the deferred report FK; 0010 must not fabricate evaluation_reports"

    report_columns = st.sql_rows(
        f02a_stack.dbname,
        "SELECT is_nullable, data_type FROM information_schema.columns "
        "WHERE table_schema = 'platform' AND table_name = 'publication_reviews' "
        "  AND column_name = 'report_id'",
    )
    assert report_columns == [{"is_nullable": "NO", "data_type": "uuid"}]


def test_version_dependencies_are_fk_bound_and_self_edges_are_refused(f02a_stack) -> None:
    check = st.sql(
        f02a_stack.dbname,
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conrelid = 'platform.version_dependencies'::regclass AND contype = 'c'",
    )
    assert "parent_version_id <> child_version_id" in check, check

    fks = _rows(
        f02a_stack.dbname,
        "SELECT confrelid::regclass::text AS target FROM pg_constraint "
        "WHERE conrelid = 'platform.version_dependencies'::regclass AND contype = 'f' "
        "ORDER BY target",
    )
    assert [row["target"] for row in fks] == ["platform.application_versions"] * 2, fks


def test_the_runtime_role_can_read_and_write_nothing_in_platform(f02a_stack) -> None:
    assert st.sql(
        f02a_stack.dbname,
        "SELECT has_schema_privilege('orchara_worker', 'platform', 'USAGE')",
    ) == "f"
    for table in TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            granted = st.sql(
                f02a_stack.dbname,
                f"SELECT has_table_privilege('orchara_worker', 'platform.{table}', '{privilege}')",
            )
            assert granted == "f", f"orchara_worker has {privilege} on {table}"

    schema_acl = st.sql(
        f02a_stack.dbname,
        "SELECT coalesce(nspacl::text, '') FROM pg_namespace WHERE nspname = 'platform'",
    )
    # A PUBLIC grant would render as an entry with an empty grantee (",=...");
    # the 0010 (and 0006-0009) REVOKE must leave none.
    assert ",=" not in schema_acl and not schema_acl.startswith("={"), schema_acl


def test_a_seeded_draft_and_version_round_trip_the_real_constraints(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-migration-owner")
    application = f.create_application_or_fail(owner, "migration constraints")

    row = f.draft_row(f02a_stack.dbname, application["id"])
    assert row["revision"] == 1 and row["execution_revision"] == 1
    assert row["definition"] == {} and row["layout"] == {} and row["capability_card"] == {}

    versions = f.seed_published_versions(
        f02a_stack.dbname,
        application["id"],
        owner_id=owner.principal_id,
        count=2,
    )
    assert [entry["version_no"] for entry in versions] == [1, 2]
    active = f.application_row(f02a_stack.dbname, application["id"])["active_version_id"]
    assert active == versions[-1]["id"]

    # The composite FK refuses a version id from another application.
    other = f.create_application_or_fail(owner, "another application")
    try:
        st.sql(
            f02a_stack.dbname,
            f"UPDATE platform.applications "
            f"SET active_version_id = {st.quote_literal(versions[0]['id'])}::uuid "
            f"WHERE id = {st.quote_literal(other['id'])}::uuid",
        )
    except AssertionError as failure:
        assert "foreign key" in str(failure).lower(), failure
    else:
        raise AssertionError("an active_version_id from another application was accepted")
