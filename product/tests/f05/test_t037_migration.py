"""T037: migration 0023 adds the tool build/review facts the baseline lacks.

The contract table (0012) keeps its columns and state enum; 0023 only *adds*
guarded, documented non-contract columns that the build guard, the read model and
the review/revoke endpoints need. Every assertion reads the real catalogue of a
database migrated by the real ``cmd/admin migrate`` chain.
"""

from __future__ import annotations

from support import f00b, t025
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
)

TABLE = "tool_versions"

BUILD_COLUMNS = {
    "revision",
    "source_sha256",
    "dependency_lock_sha256",
    "build_log_artifact_id",
    "sbom_artifact_id",
    "build_bundle_digest",
    "build_error_code",
    "build_started_at",
    "build_finished_at",
}
REVIEW_COLUMNS = {
    "reviewed_by",
    "reviewed_at",
    "review_decision",
    "review_reason",
    "revoked_at",
    "revoked_by",
    "revoke_reason",
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


def foreign_keys_of(dbname: str, table: str) -> dict[str, str]:
    rows = st.sql_rows(
        dbname,
        "SELECT a.attname AS column_name, confrelid::regclass::text AS target "
        "FROM pg_constraint AS c "
        "CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum) "
        "JOIN pg_attribute AS a ON a.attrelid = c.conrelid AND a.attnum = k.attnum "
        f"WHERE c.contype = 'f' AND c.conrelid = 'platform.{table}'::regclass",
    )
    return {row["column_name"]: row["target"] for row in rows}


def constraint_definitions(dbname: str, table: str) -> str:
    rows = st.sql_rows(
        dbname,
        "SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        f"WHERE conrelid = 'platform.{table}'::regclass AND contype = ANY(ARRAY['c','u','p'])",
    )
    return " ".join(row["definition"] for row in rows)


# ---------------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------------
def test_0023_is_recorded_once_at_the_chain_tail(migrated_database) -> None:
    _dsn, dbname = migrated_database
    ledgers = st.sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in ledgers]
    assert versions == [f"{number:04d}" for number in range(1, 24)], versions
    assert ledgers[-1]["name"] == "0023_tool_builds_and_review.sql"


def test_0023_replays_as_a_noop(migrated_database, go_binaries) -> None:
    dsn, _dbname = migrated_database
    replay = st.migrate_or_fail(go_binaries["admin"], dsn)
    assert "no pending migrations" in replay, replay


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_tool_versions_keeps_the_contract_columns_and_state_enum(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, TABLE)
    assert {
        "id",
        "owner_id",
        "source_artifact_id",
        "input_schema",
        "output_schema",
        "dependency_lock",
        "connector_versions",
        "image_digest",
        "risk_class",
        "state",
        "manifest_sha256",
        "approved_by",
        "approved_at",
        "created_at",
    } <= set(columns), sorted(columns)
    definitions = constraint_definitions(dbname, TABLE)
    for state in ("submitted", "building", "ready", "failed", "revoked"):
        assert state in definitions, (state, definitions)
    assert "image_digest IS NOT NULL" in definitions, definitions


def test_0023_adds_the_documented_build_and_review_columns(migrated_database) -> None:
    _dsn, dbname = migrated_database
    columns = columns_of(dbname, TABLE)
    added = BUILD_COLUMNS | REVIEW_COLUMNS
    assert added <= set(columns), sorted(added - set(columns))

    assert columns["revision"]["data_type"] == "bigint"
    assert columns["revision"]["is_nullable"] == "NO"
    assert "1" in (columns["revision"]["column_default"] or "")
    for name in (
        "source_sha256",
        "dependency_lock_sha256",
        "build_bundle_digest",
        "build_error_code",
        "review_decision",
        "review_reason",
        "revoke_reason",
    ):
        assert columns[name]["data_type"] == "text", (name, columns[name])
        assert columns[name]["is_nullable"] == "YES", (name, columns[name])
    for name in (
        "build_started_at",
        "build_finished_at",
        "reviewed_at",
        "revoked_at",
    ):
        assert columns[name]["data_type"] == "timestamp with time zone", (name, columns[name])
    for name in ("build_log_artifact_id", "sbom_artifact_id", "reviewed_by", "revoked_by"):
        assert columns[name]["data_type"] == "uuid", (name, columns[name])

    foreign_keys = foreign_keys_of(dbname, TABLE)
    assert foreign_keys["build_log_artifact_id"] == "platform.artifacts"
    assert foreign_keys["sbom_artifact_id"] == "platform.artifacts"
    assert foreign_keys["reviewed_by"] == "platform.users"
    assert foreign_keys["revoked_by"] == "platform.users"
    # The contract's own references stay intact.
    assert foreign_keys["approved_by"] == "platform.users"
    assert foreign_keys["source_artifact_id"] == "platform.artifacts"

    definitions = constraint_definitions(dbname, TABLE)
    assert "approved" in definitions and "rejected" in definitions, definitions
    assert "revision > 0" in definitions, definitions
    assert "build_bundle_digest" in definitions, definitions


def test_the_database_refuses_invalid_review_and_digest_facts(migrated_database) -> None:
    _dsn, dbname = migrated_database
    owner = f00b.principal_id_for_login(dbname)
    tool_id = t025.seed_tool_version(dbname, owner, approved=False)

    def statement(assignments: str) -> str:
        return (
            "UPDATE platform.tool_versions SET " + assignments +
            f" WHERE id = {st.quote_literal(tool_id)}::uuid"
        )

    rejected = {
        "unknown review decision": statement("review_decision = 'maybe'"),
        "malformed bundle digest": statement("build_bundle_digest = 'not-a-digest'"),
        "zero revision": statement("revision = 0"),
        "revoked without timestamp": statement("state = 'revoked'"),
    }
    for label, sql in rejected.items():
        result = st.psql(dbname, sql)
        assert result.code != 0, f"the database accepted {label}"

    # A valid review fact is accepted on the real row.
    st.sql(dbname, statement("review_decision = 'rejected', reviewed_at = now(), review_reason = 't037'"))
    row = st.sql_rows(
        dbname,
        "SELECT review_decision, review_reason FROM platform.tool_versions "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )[0]
    assert row == {"review_decision": "rejected", "review_reason": "t037"}, row


def test_worker_and_public_get_nothing_from_tool_versions(migrated_database) -> None:
    _dsn, dbname = migrated_database
    statements = {
        "select": "SELECT count(*) FROM platform.tool_versions",
        "update": "UPDATE platform.tool_versions SET state = state",
    }
    for label, statement in statements.items():
        result = st.psql(dbname, statement, user="orchara_worker", password=st.worker_password())
        assert result.code != 0, f"worker was allowed to run: {label}"
        assert "permission denied" in result.stderr.lower(), result.stderr

    public_grants = st.sql_rows(
        dbname,
        "SELECT acl.privilege_type AS privilege FROM pg_class AS c "
        "CROSS JOIN aclexplode(COALESCE(c.relacl, acldefault('r', c.relowner))) AS acl "
        f"WHERE c.oid = 'platform.{TABLE}'::regclass AND acl.grantee = 0",
    )
    assert public_grants == [], public_grants
