-- F02A/T025: the tool-version read model needed by the manifest dependency
-- closure, and the dedupe store for canonical execution-manifest bytes.
--
-- 0012 is the next free migration after T024's 0011. Two tables are created:
--
--   1. `platform.tool_versions` — exactly the `contracts/database.sql` baseline
--      shape. No earlier ticket created it: F05 owns tool submission/building/
--      approval writes, and it is not part of this batch. T025 owns the
--      dependency closure ("a python/agent reference is ready only when the
--      tool row is ready, approved and has an image digest"), so it creates the
--      table now as a read model, the same way F01 created the `applications`
--      read model before F02A owned application writes. This is NOT a claim
--      that tool building/approval exists: `approved_by`/`approved_at` are
--      written by F05 (or an administrative seed) and no T025 code ever writes
--      a tool row. The table is empty in a fresh database; tests seed real rows
--      and the readiness checks run in Go against the real constraints.
--
--   2. `platform.manifest_artifacts` — a T025-owned dedupe registry, NOT in
--      contracts/database.sql. The platform stores the exact canonical manifest
--      bytes (IMPLEMENTATION-DETAILS §4: "平台保留规范原始字节/对象，JSONB 只是
--      可查询投影"), and validate is a repeatable operation, so without an
--      identity for "these exact bytes for this application/revision/hash" every
--      call would create a new sealed object. The primary key
--      (application_id, execution_revision, manifest_sha256) is that identity:
--      a repeat validate returns the existing ready artifact instead of writing
--      a duplicate. T027's debug snapshots and F02B's published versions must
--      reuse the row (their `manifest_artifact_id`) rather than create a second
--      copy; deleting/rewriting the artifact would break the "same bytes" law.
--
-- Deliberate deviations / deferred work:
--   * `tool_versions.state <> 'ready' OR image_digest IS NOT NULL` is the
--     contract's CHECK, copied verbatim: a ready tool always has the digest the
--     manifest publishes. The dependency check additionally requires
--     `approved_at IS NOT NULL` for shared (publish-submit) execution; author
--     private debug may use the author's own ready tool without approval
--     (DATA-MODEL §2, encoded in internal/catalog).
--   * No `tool_build` job, builder CLI or approval endpoint: F05 owns those.
--     T025 only reads the row.
--   * Privileges follow 0007–0011: the runtime role `orchara_worker` has no
--     platform USAGE and no table/sequence privileges; PUBLIC has nothing.
--     Manifest bytes are read by the future worker through the internal mTLS
--     protocol, never by direct database access.

CREATE SCHEMA IF NOT EXISTS platform;

-- Tool versions: immutable built artifacts of a Python tool. `source_artifact`
-- is the submitted source bundle, `manifest_sha256` is the tool-build manifest
-- digest, `image_digest` is the fixed image the manifest publishes.
CREATE TABLE IF NOT EXISTS platform.tool_versions (
    id                 uuid PRIMARY KEY,
    owner_id           uuid NOT NULL REFERENCES platform.principals (id),
    source_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    input_schema       jsonb NOT NULL,
    output_schema      jsonb NOT NULL,
    dependency_lock    jsonb NOT NULL,
    connector_versions jsonb NOT NULL DEFAULT '[]',
    image_digest       text,
    risk_class         text NOT NULL CHECK (risk_class IN ('pure', 'read', 'write')),
    state              text NOT NULL CHECK (state IN ('submitted', 'building', 'ready', 'failed', 'revoked')),
    manifest_sha256    text NOT NULL UNIQUE,
    approved_by        uuid REFERENCES platform.users (principal_id),
    approved_at        timestamptz,
    created_at         timestamptz NOT NULL DEFAULT now(),
    -- Contract CHECK: a ready tool always carries the image digest the
    -- execution manifest pins. Approval is a separate fact (approved_at).
    CHECK (state <> 'ready' OR image_digest IS NOT NULL)
);

-- The dependency closure resolves a draft's references by primary key. The
-- owner index is for F05's future list endpoints and for "whose tool is this"
-- diagnostics; it is not used by the T025 closure, which only reads by id.
CREATE INDEX IF NOT EXISTS tool_versions_owner
    ON platform.tool_versions (owner_id, created_at DESC);

-- Canonical manifest bytes: one row per (application, execution content
-- revision, exact canonical SHA-256). The artifact is sealed and ready; the
-- bytes are the JCS form of the manifest whose digest is the key.
CREATE TABLE IF NOT EXISTS platform.manifest_artifacts (
    application_id     uuid NOT NULL REFERENCES platform.applications (id),
    execution_revision bigint NOT NULL CHECK (execution_revision > 0),
    manifest_sha256    text NOT NULL CHECK (length(manifest_sha256) = 64),
    artifact_id        uuid NOT NULL REFERENCES platform.artifacts (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (application_id, execution_revision, manifest_sha256)
);

-- Reverse lookup "which application/revision does this artifact belong to"
-- is needed by T027/F02B when they reuse a validate-produced artifact.
CREATE INDEX IF NOT EXISTS manifest_artifacts_artifact
    ON platform.manifest_artifacts (artifact_id);

-- Privileges: identical boundary to 0007–0011. New tables inherit it because
-- `orchara_worker` has no USAGE on the schema either.
REVOKE ALL ON SCHEMA platform FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orchara_worker') THEN
        EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA platform FROM orchara_worker';
        EXECUTE 'REVOKE ALL ON ALL SEQUENCES IN SCHEMA platform FROM orchara_worker';
        EXECUTE 'REVOKE USAGE ON SCHEMA platform FROM orchara_worker';
    END IF;
END
$$;

COMMENT ON TABLE platform.tool_versions IS
    'Built Python tool versions (contracts/database.sql baseline). Writes/build/approval are owned by F05 and do not exist in this batch; T025 creates the table as a read model because its manifest dependency closure must prove a referenced tool is ready, approved and image-pinned. No T025 code writes this table.';
COMMENT ON COLUMN platform.tool_versions.image_digest IS
    'The fixed image digest the execution manifest publishes in tool_artifacts[].image_digest. The contract CHECK forces it to be present once state=ready; the manifest builder fails closed if a ready row somehow has none.';
COMMENT ON COLUMN platform.tool_versions.approved_at IS
    'Organization approval timestamp (F05). Shared/submit execution requires it to be non-null together with state=ready; author-private debug may use the author''s own ready tool without it (DATA-MODEL §2).';
COMMENT ON TABLE platform.manifest_artifacts IS
    'T025-owned dedupe registry (deviation from database.sql) for exact canonical execution-manifest bytes: one sealed ready artifact per (application_id, execution_revision, manifest_sha256). Revalidate returns the existing artifact instead of writing a duplicate object; T027/F02B must reuse artifacts referenced here, never mint a second copy of the same bytes.';
COMMENT ON COLUMN platform.manifest_artifacts.manifest_sha256 IS
    'SHA-256 of the UTF-8 RFC 8785 JCS bytes (internal/jcs), the same value returned to clients in X-Manifest-SHA256 and stored on application_versions/execution_snapshots.';
