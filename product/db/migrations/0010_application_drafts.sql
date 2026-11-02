-- F02A/T023: application drafts, published versions, publication reviews and
-- the version-to-version dependency edges.
--
-- Baseline: contracts/database.sql defines `application_drafts`,
-- `publication_reviews`, `application_versions` and `version_dependencies`.
-- This migration creates the four tables with that shape; deviations and
-- deferred work are listed here and repeated in ADR 0004 §12.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `application_drafts.execution_revision bigint NOT NULL DEFAULT 1` — the
--      contract's draft row has a single counter, but F02A needs two:
--        * `revision` is the optimistic-concurrency counter used for the
--          ETag/If-Match write path (every accepted save increments it);
--        * `execution_revision` is the *executable content* counter (only
--          definition or capability_card changes increment it; layout-only
--          saves never do).
--      T025 uses execution_revision as the manifest's `source_revision`, which
--      is what makes "layout-only save keeps the execution hash" true instead
--      of relying on "do not save layout". T027 uses `revision` for the debug
--      `draft_revision`. The save path in internal/catalog maintains both in
--      one UPDATE, comparing jsonb values with IS DISTINCT FROM (jsonb equality
--      is the right proxy for the canonical-byte equality F02A needs here:
--      object key order and whitespace are not significant, numbers compare
--      numerically).
--   2. `publication_reviews.report_id` is created as `uuid NOT NULL` **without**
--      the contract's FK to `evaluation_reports`. That table belongs to
--      F00B/T026 and does not exist yet. T026 must add the FK in migration
--      0012 (or later) together with the table, and this column must not be
--      treated as a validated reference until then. Creating the FK now would
--      require T023 to invent a table it does not own.
--   3. `application_versions.review_id` keeps its contract FK because
--      publication_reviews is created here; that makes the published row
--      provenance-complete within one migration.
--   4. `applications.active_version_id` finally gets its foreign key. ADR 0003
--      §2 reserved it for F02A. The contract expresses it as a composite FK on
--      `(id, active_version_id) -> application_versions(application_id, id)`,
--      which is stronger than a single-column reference: it also guarantees the
--      active version belongs to that application. ON DELETE RESTRICT is the
--      explicit choice: published versions are immutable and a version is
--      withdrawn with `revoked_at`, never deleted, so a delete that would orphan
--      a live `active_version_id` is a bug and must fail rather than silently
--      null the application's current version. A NULL active_version_id is
--      exempt (MATCH SIMPLE), which is the "never published" state.
--   5. `application_drafts.definition/layout/capability_card` are jsonb objects
--      and are constrained to `jsonb_typeof(...) = 'object'`. The contract only
--      says `object`; the CHECK keeps a malformed write (an array/string) from
--      becoming a stored draft that every read has to tolerate.
--
-- Deliberate boundaries:
--   * No `evaluation_reports` table here (T026 owns it).
--   * No manifest generation, validate/debug/publish endpoints (T025/T027 and
--     F02B own those); this migration only provides the rows they will use.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker` gets no USAGE on the platform schema and no table
--     privileges, exactly like 0006-0009: draft/version/review rows are reached
--     only through the authenticated API.

CREATE SCHEMA IF NOT EXISTS platform;

-- Publication reviews are the review record for one frozen submission. The
-- report reference is intentionally FK-less for now (deviation 2).
CREATE TABLE IF NOT EXISTS platform.publication_reviews (
    id              uuid PRIMARY KEY,
    application_id  uuid NOT NULL REFERENCES platform.applications (id),
    submitted_by    uuid NOT NULL REFERENCES platform.principals (id),
    snapshot        jsonb NOT NULL,
    manifest_sha256 text NOT NULL,
    -- Deviation 2: contracts/database.sql REFERENCES evaluation_reports(id);
    -- T026 creates that table and adds the FK. No fabricated table here.
    report_id       uuid NOT NULL,
    state           text NOT NULL CHECK (state IN ('submitted', 'approved', 'rejected')),
    reviewer_id     uuid REFERENCES platform.principals (id),
    decided_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- F02B lists reviews per application; the primary key only serves the detail.
CREATE INDEX IF NOT EXISTS publication_reviews_application
    ON platform.publication_reviews (application_id, created_at DESC);

-- One immutable published version. `UNIQUE(application_id, id)` exists for the
-- composite active_version foreign key below, exactly as in the contract.
CREATE TABLE IF NOT EXISTS platform.application_versions (
    id                   uuid PRIMARY KEY,
    application_id       uuid NOT NULL REFERENCES platform.applications (id),
    version_no           integer NOT NULL CHECK (version_no > 0),
    review_id            uuid NOT NULL REFERENCES platform.publication_reviews (id),
    manifest             jsonb NOT NULL,
    manifest_sha256      text NOT NULL,
    manifest_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    capability_card      jsonb NOT NULL,
    compiler_version     text NOT NULL,
    worker_image_digest  text NOT NULL,
    state_schema_version text NOT NULL,
    revoked_at           timestamptz,
    created_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (application_id, version_no),
    -- Required by the composite FK on applications; also the contract's shape.
    UNIQUE (application_id, id)
);

CREATE TABLE IF NOT EXISTS platform.version_dependencies (
    parent_version_id uuid NOT NULL REFERENCES platform.application_versions (id),
    child_version_id  uuid NOT NULL REFERENCES platform.application_versions (id),
    PRIMARY KEY (parent_version_id, child_version_id),
    CHECK (parent_version_id <> child_version_id)
);

-- Dependency closure lookups walk children upward (T025); the PK only serves
-- "what does this version depend on".
CREATE INDEX IF NOT EXISTS version_dependencies_child
    ON platform.version_dependencies (child_version_id);

-- One draft row per application. Revision starts at 1 and is the ETag source;
-- execution_revision starts at 1 and only moves when executable content moves
-- (deviation 1).
CREATE TABLE IF NOT EXISTS platform.application_drafts (
    application_id     uuid PRIMARY KEY REFERENCES platform.applications (id),
    revision           bigint NOT NULL CHECK (revision > 0),
    execution_revision bigint NOT NULL DEFAULT 1 CHECK (execution_revision > 0),
    definition         jsonb NOT NULL,
    layout             jsonb NOT NULL DEFAULT '{}'::jsonb,
    capability_card    jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    -- Deviation 5: the API only ever stores objects; enforce it in the table.
    CHECK (jsonb_typeof(definition) = 'object'),
    CHECK (jsonb_typeof(layout) = 'object'),
    CHECK (jsonb_typeof(capability_card) = 'object')
);

-- Deviation 4: the foreign key F01 left to F02A. `ADD CONSTRAINT` has no
-- IF NOT EXISTS in PostgreSQL, so the guard keeps the migration idempotent.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'active_version_belongs_to_app'
           AND conrelid = 'platform.applications'::regclass
    ) THEN
        ALTER TABLE platform.applications
            ADD CONSTRAINT active_version_belongs_to_app
            FOREIGN KEY (id, active_version_id)
            REFERENCES platform.application_versions (application_id, id)
            ON DELETE RESTRICT;
    END IF;
END
$$;

-- Privileges. New tables inherit the 0006-0009 boundary: the runtime role cannot
-- read, modify or create anything in `platform`, and PUBLIC has nothing.
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

COMMENT ON TABLE platform.application_drafts IS
    'One editable draft per application. revision is the ETag/If-Match counter (every accepted save +1); execution_revision only changes when definition/capability_card content changes, so a layout-only save cannot change the execution hash.';
COMMENT ON COLUMN platform.application_drafts.revision IS
    'Optimistic-concurrency counter and ETag source. The save path updates with WHERE revision = <If-Match>; a zero-row update is a 412, never a silent overwrite.';
COMMENT ON COLUMN platform.application_drafts.execution_revision IS
    'Executable-content counter (deviation from database.sql, added by T023). Incremented only when the stored definition or capability_card jsonb value differs (IS DISTINCT FROM). T025 uses it as the manifest source_revision.';
COMMENT ON COLUMN platform.application_drafts.capability_card IS
    'jsonb object. {} is the honest "not filled in yet" state for a new draft; any non-empty card must have the full DraftInput shape (six keys, examples >= 2). See ADR 0004 §13 for the contract gap this works around.';
COMMENT ON TABLE platform.publication_reviews IS
    'Review record for one frozen submission. report_id has no FK until T026 creates platform.evaluation_reports; the review state machine moves submitted -> approved|rejected.';
COMMENT ON COLUMN platform.publication_reviews.report_id IS
    'The evaluation report bound to the reviewed snapshot. contracts/database.sql declares a FK to evaluation_reports(id); T026 creates that table later, so 0010 creates the column without the FK and T026 adds it. Until then this column is not a validated reference.';
COMMENT ON TABLE platform.application_versions IS
    'Immutable published versions. The read model returns manifest/manifest_sha256/version_no only; it never joins a draft. Revocation is revoked_at, never deletion.';
COMMENT ON TABLE platform.version_dependencies IS
    'Directed dependency edges between published versions (parent calls child). T025 uses the closure for sub-application readiness and cycle detection.';
