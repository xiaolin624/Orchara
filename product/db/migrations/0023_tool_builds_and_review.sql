-- F05/T037: the build and review facts the contract baseline tool_versions row
-- does not carry.
--
-- 0012 created `platform.tool_versions` as exactly the contracts/database.sql
-- baseline shape, with a comment saying F05 owns submission/building/approval.
-- This migration is that owner: it adds the columns the tool-build guard and the
-- review/revoke endpoints need, while keeping every contract column, the state
-- enum (`submitted|building|ready|failed|revoked`) and the baseline CHECK
-- `state <> 'ready' OR image_digest IS NOT NULL` untouched.
--
-- Contract columns stay the contract:
--   * `manifest_sha256` is still the globally UNIQUE content identity computed by
--     submitTool over source hash + schemas + lock + risk + connectors;
--   * `image_digest` is written only when the build guard commits state=ready. It
--     is the deterministic **content bundle digest** `sha256:<hex>` described
--     below -- NOT an OCI image. F15A builds OCI images; this ticket must never
--     call the value a container image.
--   * `approved_at` is still the only "approved" projection; two reviews are
--     distinguished by the added `review_decision` column so a rejection's reason
--     is durable without pretending the build failed.
--
-- Non-contract columns (documented extension, ADR 0005; `sync_spec --check` only
-- compares generated contract files, so it stays clean):
--   1. `revision` -- optimistic-concurrency token for the tool row. submit starts
--      at 1; the build guard/review/revoke CAS on it. It is deliberately not in
--      the contract ToolVersion projection (the read model exposes only the
--      contract fields).
--   2. `source_sha256` -- the admitted source artifact's sha256 at submission. The
--      build guard re-checks the artifact still has this hash; a changed source
--      can never produce a ready build.
--   3. `dependency_lock_sha256` -- SHA-256 of the RFC 8785 JCS bytes of
--      `dependency_lock`, bound into the build. The sandbox receives exactly the
--      canonical bytes, so the hash is reproducible.
--   4. `build_log_artifact_id` / `sbom_artifact_id` -- the two job-scoped build
--      artifacts (purposes `build_log` and `sbom`). They are linked to the
--      tool_build background job and must be ready+job-linked before the guard
--      accepts a build.
--   5. `build_bundle_digest` -- the same `sha256:<hex>` bundle digest stored in
--      `image_digest`, kept under an honest name for diagnostics and the admin
--      review UI. Definition: SHA-256 over the concatenation of the source zip
--      bytes, the canonical dependency-lock bytes and the SBOM bytes, sorted
--      lexicographically; it identifies the content bundle, not a container.
--   6. `build_started_at` / `build_finished_at` / `build_error_code` -- the build
--      outcome. The error code is a safe closed platform code; the sandbox log
--      itself is an artifact, never a column.
--   7. `reviewed_by` / `reviewed_at` / `review_decision` / `review_reason` -- the
--      admin decision. `review_decision` is `approved` or `rejected`; a reject
--      leaves `state=ready` (build success is not approval) and sets no
--      `approved_at`.
--   8. `revoked_at` / `revoked_by` / `revoke_reason` -- the documented non-contract
--      revocation. A revoked tool is rejected by the T025 dependency closure with
--      the existing `dependency_revoked` code; its history stays readable.
--
-- Deliberate boundaries:
--   * No new table, no queue table, no audit table (the existing
--     `background_jobs` kind `tool_build` and `platform.audit_events` are used).
--     `tool_build` already exists in the 0009/0017 CHECK and the Go whitelist.
--   * No privilege change: `orchara_worker`/PUBLIC still get nothing from
--     `platform`; the build handler goes through job-scoped mTLS endpoints.
--   * Every statement is schema-qualified and replay-safe (guarded ADD COLUMN /
--     ADD CONSTRAINT via the catalogue).
--   * Constraints are intentionally NULL-tolerant: T025/T027/T030/T033 seed
--     ready tools with SQL and no build facts, and those fixtures must keep
--     working. The build guard, not a table constraint, is the only writer of a
--     ready row's digest.
--
-- Deliberate deviations from contracts/database.sql: every column above except
-- `revision` is new; `revision` is also new. They are a non-contract extension
-- owned by F05/T037. A later contract revision may fold the review/revocation
-- fields into the ToolVersion schema; until then the read model keeps the exact
-- seven-field contract projection and build facts travel through the documented
-- non-contract GET /api/v1/tools/{id}/build.

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS revision bigint NOT NULL DEFAULT 1;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS source_sha256 text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS dependency_lock_sha256 text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS build_log_artifact_id uuid
        REFERENCES platform.artifacts (id);

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS sbom_artifact_id uuid
        REFERENCES platform.artifacts (id);

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS build_bundle_digest text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS build_started_at timestamptz;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS build_finished_at timestamptz;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS build_error_code text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS reviewed_by uuid
        REFERENCES platform.users (principal_id);

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS reviewed_at timestamptz;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS review_decision text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS review_reason text;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS revoked_at timestamptz;

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS revoked_by uuid
        REFERENCES platform.users (principal_id);

ALTER TABLE platform.tool_versions
    ADD COLUMN IF NOT EXISTS revoke_reason text;

-- Guarded constraints: ADD CONSTRAINT has no IF NOT EXISTS, so each is added
-- only when absent. The DO block keeps the whole migration replayable.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_revision_positive'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_revision_positive CHECK (revision > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_source_sha256_format'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_source_sha256_format
            CHECK (source_sha256 IS NULL OR source_sha256 ~ '^[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_dependency_lock_sha256_format'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_dependency_lock_sha256_format
            CHECK (dependency_lock_sha256 IS NULL OR dependency_lock_sha256 ~ '^[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_bundle_digest_format'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_bundle_digest_format
            CHECK (build_bundle_digest IS NULL OR build_bundle_digest ~ '^sha256:[0-9a-f]{64}$');
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_review_decision_check'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_review_decision_check
            CHECK (review_decision IS NULL OR review_decision IN ('approved', 'rejected'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_revocation_consistent'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        -- A revoked row must carry the revocation timestamp; every other state
        -- may be revoked_at NULL. (No pre-0023 row is revoked, so this validates.)
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_revocation_consistent
            CHECK (state <> 'revoked' OR revoked_at IS NOT NULL);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_build_error_code_bounded'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_build_error_code_bounded
            CHECK (build_error_code IS NULL
                   OR (length(btrim(build_error_code)) > 0 AND length(build_error_code) <= 128));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_review_reason_bounded'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_review_reason_bounded
            CHECK (review_reason IS NULL OR length(review_reason) <= 2000);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'tool_versions_revoke_reason_bounded'
           AND conrelid = 'platform.tool_versions'::regclass
    ) THEN
        ALTER TABLE platform.tool_versions
            ADD CONSTRAINT tool_versions_revoke_reason_bounded
            CHECK (revoke_reason IS NULL OR length(revoke_reason) <= 2000);
    END IF;
END
$$;

-- List/read-model indexes. The owner index from 0012 already serves the creator
-- list; these add the shared (approved) catalog path and the admin review queue.
CREATE INDEX IF NOT EXISTS tool_versions_created
    ON platform.tool_versions (created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS tool_versions_approved_created
    ON platform.tool_versions (created_at DESC, id DESC)
    WHERE approved_at IS NOT NULL;

CREATE INDEX IF NOT EXISTS tool_versions_review_queue
    ON platform.tool_versions (created_at, id)
    WHERE state = 'ready' AND approved_at IS NULL;

CREATE INDEX IF NOT EXISTS tool_versions_state
    ON platform.tool_versions (state, created_at DESC);

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

COMMENT ON COLUMN platform.tool_versions.revision IS
    'F05/T037 optimistic-concurrency token for the tool row (submit=1; build guard/review/revoke CAS on it). Non-contract extension; not in the ToolVersion projection.';
COMMENT ON COLUMN platform.tool_versions.source_sha256 IS
    'F05/T037 admitted source artifact sha256 at submission. The build guard refuses ready when the source artifact/hash no longer matches.';
COMMENT ON COLUMN platform.tool_versions.dependency_lock_sha256 IS
    'F05/T037 SHA-256 of the RFC 8785 JCS bytes of dependency_lock; the sandbox receives exactly those canonical bytes.';
COMMENT ON COLUMN platform.tool_versions.build_log_artifact_id IS
    'F05/T037 job-scoped build log artifact (purpose build_log). Must be ready and linked to the tool_build job before a build becomes ready.';
COMMENT ON COLUMN platform.tool_versions.sbom_artifact_id IS
    'F05/T037 job-scoped SBOM artifact (purpose sbom). Part of the bundle digest and required for a ready build.';
COMMENT ON COLUMN platform.tool_versions.build_bundle_digest IS
    'F05/T037 content bundle digest sha256:<hex> = SHA-256 of the lexicographically sorted source zip bytes + canonical dependency-lock bytes + SBOM bytes, concatenated. This is NOT an OCI image; F15A builds images. image_digest stores the same value for the contract dependency check.';
COMMENT ON COLUMN platform.tool_versions.build_started_at IS
    'F05/T037 when the first tool_build job attempt claimed the row (state submitted -> building).';
COMMENT ON COLUMN platform.tool_versions.build_finished_at IS
    'F05/T037 when the build guard committed ready or failed.';
COMMENT ON COLUMN platform.tool_versions.build_error_code IS
    'F05/T037 safe closed build failure code (never sandbox log text or a credential).';
COMMENT ON COLUMN platform.tool_versions.reviewed_by IS
    'F05/T037 admin user who approved/rejected; NULL until a review decision.';
COMMENT ON COLUMN platform.tool_versions.reviewed_at IS
    'F05/T037 database clock of the review decision.';
COMMENT ON COLUMN platform.tool_versions.review_decision IS
    'F05/T037 approved|rejected. A rejected build stays state=ready and approved_at NULL: build success is not approval.';
COMMENT ON COLUMN platform.tool_versions.review_reason IS
    'F05/T037 admin-supplied review reason (bounded, never a secret).';
COMMENT ON COLUMN platform.tool_versions.revoked_at IS
    'F05/T037 non-contract revocation timestamp; a revoked tool is rejected by T025 dependency validation with dependency_revoked.';
COMMENT ON COLUMN platform.tool_versions.revoked_by IS
    'F05/T037 admin user who revoked the tool.';
COMMENT ON COLUMN platform.tool_versions.revoke_reason IS
    'F05/T037 admin-supplied revocation reason (bounded, never a secret).';
