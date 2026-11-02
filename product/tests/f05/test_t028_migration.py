"""T028 acceptance: migration 0015 adds the upload admission record.

The migration is additive on top of 0014 and only adds what the user-upload
path needs: one insert-once record per artifact carrying the sniffed media
type, the detected format, the verdict and a safe reason. There is no deletion
model, no request/conversation table and no tool table here (F07/T037 own
those). The last migration is still applied by the real ``cmd/admin migrate``
on an empty database, re-running it is a no-op, and ``orchara_worker`` still
gets nothing from ``platform``.
"""

from __future__ import annotations

import os

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    go_binaries,
    migrated_database,
)

ADMISSION_COLUMNS = (
    "artifact_id",
    "declared_media_type",
    "sniffed_media_type",
    "detected_format",
    "verdict",
    "reason",
    "checked_at",
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


def foreign_key_columns(dbname: str, table: str) -> set[str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"] for row in rows}


def constraints_of(dbname: str, table: str) -> dict[str, str]:
    return {
        row["conname"]: row["contype"]
        for row in st.sql_rows(
            dbname,
            "SELECT conname, contype FROM pg_constraint "
            f"WHERE conrelid = 'platform.{table}'::regclass",
        )
    }


def test_the_migration_chain_ends_at_0015(migrated_database) -> None:
    _dsn, dbname = migrated_database
    versions = st.sql(
        dbname,
        "SELECT string_agg(version, ',' ORDER BY version) FROM platform.schema_migrations",
    )
    # T030 appends 0017; 0015 must stay at its own position.
    assert versions.split(",")[-1] == "0017", versions
    assert len(versions.split(",")) == 17, versions
    assert "0015" in versions.split(","), versions

    # The one table this ticket adds is present with exactly the columns the
    # upload admission path writes. A table name that does not exist is the
    # expected red state before the migration lands.
    assert set(ADMISSION_COLUMNS) <= set(columns_of(dbname, "artifact_admissions"))

    # No deletion model, request/conversation table or tool table was smuggled
    # into this migration: those are F07/T037 work.
    existing = {
        row["table_name"]
        for row in st.sql_rows(
            dbname,
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'platform'",
        )
    }
    assert not ({"deletion_jobs", "deletion_scopes", "requests", "conversations"} & existing), existing
    assert "artifact_admissions" in existing, existing


def test_the_admission_record_is_constrained_and_foreign_keyed(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = st.sql(
        dbname,
        "SELECT id::text FROM platform.principals LIMIT 1",
    )
    artifact_id = st.sql(
        dbname,
        "WITH inserted AS ("
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, staging_object_key, purpose, media_type, size_bytes, state, revision) "
        "VALUES (gen_random_uuid(), "
        f"{st.quote_literal(owner)}::uuid, {st.quote_literal('sealed/' + os.urandom(32).hex())}, "
        f"{st.quote_literal('staging/' + os.urandom(32).hex())}, 'task_input', 'text/plain', 12, "
        "'uploading', 1) RETURNING id) SELECT id::text FROM inserted",
    )
    assert artifact_id

    constraints = constraints_of(dbname, "artifact_admissions")
    assert constraints.get("artifact_admissions_pkey") == "p"
    assert "artifact_admissions_verdict_check" in constraints
    assert "artifact_admissions_detected_format_check" in constraints
    assert "artifact_admissions_reason_matches_verdict" in constraints
    assert "artifact_admissions_accepted_has_sniff" in constraints

    assert foreign_key_columns(dbname, "artifact_admissions") == {"artifact_id"}

    columns = columns_of(dbname, "artifact_admissions")
    assert columns["sniffed_media_type"]["is_nullable"] == "YES"
    assert columns["detected_format"]["is_nullable"] == "YES"
    assert columns["reason"]["is_nullable"] == "YES"
    assert columns["verdict"]["is_nullable"] == "NO"
    assert columns["checked_at"]["column_default"].startswith("now()")

    insert = (
        "INSERT INTO platform.artifact_admissions "
        "(artifact_id, declared_media_type, sniffed_media_type, detected_format, verdict, reason) "
        f"VALUES ({st.quote_literal(artifact_id)}::uuid, %s, %s, %s, %s, %s)"
    )
    accepted = st.psql(
        dbname,
        insert % ("'text/plain'", "'text/plain'", "'text'", "'accepted'", "NULL"),
    )
    assert accepted.code == 0, accepted.stderr

    duplicate = st.psql(
        dbname,
        insert % ("'text/plain'", "'text/plain'", "'text'", "'accepted'", "NULL"),
    )
    assert duplicate.code != 0
    assert "duplicate key" in duplicate.stderr.lower(), duplicate.stderr

    # Accepted without a sniffed fact is not a record the platform may store.
    bad_accepted = st.psql(
        dbname,
        "INSERT INTO platform.artifact_admissions "
        "(artifact_id, declared_media_type, verdict) VALUES "
        f"({st.quote_literal(artifact_id)}::uuid, 'text/plain', 'accepted')",
    )
    assert bad_accepted.code != 0

    # A rejection always carries a safe reason.
    rejected_without_reason = st.psql(
        dbname,
        "INSERT INTO platform.artifact_admissions "
        "(artifact_id, declared_media_type, verdict) VALUES "
        f"(gen_random_uuid(), 'text/plain', 'rejected')",
    )
    assert rejected_without_reason.code != 0

    missing_artifact = st.psql(
        dbname,
        "INSERT INTO platform.artifact_admissions "
        "(artifact_id, declared_media_type, sniffed_media_type, detected_format, verdict, reason) "
        "VALUES (gen_random_uuid(), 'text/plain', 'text/plain', 'text', 'rejected', 'bad')",
    )
    assert missing_artifact.code != 0
    assert "foreign key" in missing_artifact.stderr.lower(), missing_artifact.stderr


def test_the_worker_and_public_get_nothing_from_the_admission_table(migrated_database) -> None:
    _dsn, dbname = migrated_database
    denied = st.psql(
        dbname,
        "SELECT count(*) FROM platform.artifact_admissions",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert denied.code != 0
    assert "permission denied" in denied.stderr.lower(), denied.stderr

    grants = st.sql_rows(
        dbname,
        "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE grantee = 'orchara_worker' AND table_schema = 'platform'",
    )
    assert grants == [], grants

    public_grants = st.sql_rows(
        dbname,
        "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        "WHERE c.oid = 'platform.artifact_admissions'::regclass AND acl.grantee = 0",
    )
    assert public_grants == [], public_grants

    control = st.psql(
        dbname,
        "INSERT INTO app.workers (worker_id) VALUES ('t028-privilege-control') "
        "ON CONFLICT (worker_id) DO UPDATE SET heartbeat_at = now()",
        user="orchara_worker",
        password=st.worker_password(),
    )
    assert control.code == 0, control.stderr


def test_re_running_the_chain_is_a_no_op(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    completed = st.run_admin(go_binaries["admin"], ["migrate"], dsn=dsn)
    assert completed.returncode == 0, completed.stderr
    assert "no pending migrations" in (completed.stdout + completed.stderr)
