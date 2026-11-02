-- F00B/T026: evaluation jobs/reports and the pool boundary they rely on.
--
-- Baseline: contracts/database.sql defines `evaluation_reports` and
-- `evaluation_jobs` (plus the two foreign keys that point at the report table).
-- This migration creates those two tables, adds the two deferred foreign keys
-- left by T023/T024 now that the report table exists, and nothing else:
--
--   1. `platform.evaluation_jobs` — the business state of one evaluation
--      (queued/running/succeeded/failed/cancelled), linked 1:1 to the
--      `application_evaluate` background job that executes it.
--   2. `platform.evaluation_reports` — the immutable completion product. A
--      report row exists only after the server-side completion guard has read
--      the sealed report artifact, verified its `manifest_sha256`/`dataset_sha256`
--      binding and accepted the passed/failed/not_run verdict. A failed report
--      is a *successful* evaluation job (business outcome); an infrastructure
--      failure produces no report at all (BACKGROUND-JOBS §3).
--   3. `publication_reviews.report_id -> evaluation_reports(id)` — the FK T023's
--      0010 deliberately deferred. F02B owns writing reviews; T026 only supplies
--      the referenced table and the constraint.
--   4. `model_profile_versions.evaluation_report_id -> evaluation_reports(id)` —
--      the FK T024's 0011 deliberately deferred.
--
-- One deliberate divergence from contracts/database.sql, with its owner:
--
--   * `evaluation_jobs.snapshot_id` is created as a **plain uuid with no FK**.
--     The contract references `execution_snapshots(id)`, but that table is owned
--     by T027 (0014) and does not exist in this batch. T026 accepts `kind=
--     application` structurally (the CLI/CHECK allow it) but cannot execute such
--     an evaluation: there is no snapshot row to bind. T027 creates
--     `platform.execution_snapshots` and **must add the FK in its own migration**
--     (`ALTER TABLE platform.evaluation_jobs ADD CONSTRAINT
--     evaluation_jobs_snapshot_fkey FOREIGN KEY (snapshot_id) REFERENCES
--     platform.execution_snapshots(id)`). Until then no code may treat
--     `snapshot_id` as a verified reference. The contract CHECK that exactly one
--     of snapshot_id/model_profile_version_id is set is created verbatim, so the
--     structural shape is real today.
--
-- Deliberate boundaries:
--   * No deletion model: F07 owns `deletion_jobs`/tombstone bookkeeping for
--     evaluations. T026 only stops a deleted dataset from producing a report.
--   * No evaluation write endpoint: the public contract's
--     `POST /applications/{id}/evaluations` is F13A's (trusted runner). T026's
--     control interface is `cmd/admin evaluations enqueue` plus the maintenance
--     handler.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * Privileges follow 0007–0012: PUBLIC gets nothing on `platform`,
--     `orchara_worker` gets no schema USAGE and no table/sequence privileges.
--     Evaluation rows are reached only through the authenticated API or the
--     maintenance identity.

CREATE SCHEMA IF NOT EXISTS platform;

-- The immutable report document metadata. `result_artifact_id` is the sealed
-- report artifact the completion transaction read and verified; the artifact is
-- never activated by this migration or by T026 (the finalize guard owns that).
CREATE TABLE IF NOT EXISTS platform.evaluation_reports (
    id                 uuid PRIMARY KEY,
    manifest_sha256    text NOT NULL CHECK (length(manifest_sha256) = 64),
    dataset_sha256     text NOT NULL CHECK (length(dataset_sha256) = 64),
    result_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    status             text NOT NULL CHECK (status IN ('passed', 'failed', 'not_run')),
    created_at         timestamptz NOT NULL DEFAULT now()
);

-- One evaluation business row. The background job is its only executor; the
-- UNIQUE makes "one job per evaluation" a database fact rather than a convention.
CREATE TABLE IF NOT EXISTS platform.evaluation_jobs (
    id                       uuid PRIMARY KEY,
    kind                     text NOT NULL CHECK (kind IN ('application', 'model')),
    owner_id                 uuid NOT NULL REFERENCES platform.principals (id),
    background_job_id        uuid NOT NULL UNIQUE REFERENCES platform.background_jobs (id),
    -- Deferred FK: platform.execution_snapshots is created by T027 (0014).
    snapshot_id              uuid,
    model_profile_version_id uuid REFERENCES platform.model_profile_versions (id),
    dataset_artifact_id      uuid NOT NULL REFERENCES platform.artifacts (id),
    report_id                uuid REFERENCES platform.evaluation_reports (id),
    state                    text NOT NULL CHECK (state IN (
                                 'queued', 'running', 'succeeded', 'failed', 'cancelled'
                             )),
    error_code               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    finished_at              timestamptz,
    -- Contract CHECK: exactly one candidate identity, chosen by kind.
    CHECK ((kind = 'application' AND snapshot_id IS NOT NULL AND model_profile_version_id IS NULL)
        OR (kind = 'model' AND model_profile_version_id IS NOT NULL AND snapshot_id IS NULL))
);

-- Owner-or-admin list/read diagnostics scan one owner's evaluations. The
-- background_job_id lookup is served by its UNIQUE constraint.
CREATE INDEX IF NOT EXISTS evaluation_jobs_owner_state
    ON platform.evaluation_jobs (owner_id, state, created_at DESC);

-- Reverse lookup "which evaluation produced this report" is the report read's
-- owner predicate (evaluation_reports has no owner column of its own).
CREATE INDEX IF NOT EXISTS evaluation_jobs_report
    ON platform.evaluation_jobs (report_id) WHERE report_id IS NOT NULL;

-- Deferred FK from 0010: publication_reviews.report_id. Guarded because
-- ADD CONSTRAINT has no IF NOT EXISTS. F02B writes review rows; T026 only makes
-- the reference real.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'publication_reviews_report_id_fkey'
           AND conrelid = 'platform.publication_reviews'::regclass
    ) THEN
        ALTER TABLE platform.publication_reviews
            ADD CONSTRAINT publication_reviews_report_id_fkey
            FOREIGN KEY (report_id) REFERENCES platform.evaluation_reports (id);
    END IF;
END
$$;

-- Deferred FK from 0011: model_profile_versions.evaluation_report_id.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'model_profile_versions_evaluation_report_id_fkey'
           AND conrelid = 'platform.model_profile_versions'::regclass
    ) THEN
        ALTER TABLE platform.model_profile_versions
            ADD CONSTRAINT model_profile_versions_evaluation_report_id_fkey
            FOREIGN KEY (evaluation_report_id) REFERENCES platform.evaluation_reports (id);
    END IF;
END
$$;

-- Privileges: identical boundary to 0007–0012. New tables inherit it because
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

COMMENT ON TABLE platform.evaluation_jobs IS
    'Evaluation business state (contracts/database.sql). queued -> running -> succeeded|failed|cancelled. succeeded means the report was generated and accepted; the report status may be failed. The background_job_id UNIQUE makes the application_evaluate job the only executor, and report_id is only set by the server-side completion guard in the same transaction that accepts the report.';
COMMENT ON COLUMN platform.evaluation_jobs.snapshot_id IS
    'Deferred FK (T026 decision, ADR 0004): platform.execution_snapshots is created by T027 (0014), which must add the FK in its migration. T026 accepts kind=application structurally but cannot execute it until the snapshot table exists; until then this column is a plain uuid and nothing may treat it as a verified reference.';
COMMENT ON COLUMN platform.evaluation_jobs.report_id IS
    'The immutable evaluation_reports row the completion guard accepted. NULL until a report passes the binding/status guard; a job failure never sets it.';
COMMENT ON COLUMN platform.evaluation_jobs.dataset_artifact_id IS
    'The sealed dataset the evaluation runs against. The completion guard re-reads it: a tombstone or a change of its recorded sha256 refuses the report (source_deleted/dataset_not_ready).';
COMMENT ON COLUMN platform.evaluation_jobs.error_code IS
    'Stable failure code when state=failed (same vocabulary as the background job). Cleared on success.';
COMMENT ON TABLE platform.evaluation_reports IS
    'Immutable evaluation report metadata (contract Report). A row exists only after the completion transaction read the sealed report artifact, verified manifest_sha256/dataset_sha256 binding and the passed/failed/not_run rules. A failed report is a successful job; infrastructure failures create no report row.';
COMMENT ON COLUMN platform.evaluation_reports.status IS
    'passed/failed/not_run. passed requires at least one case and one check, all cases passed and a non-empty result; not_run can never become passed; the guard rejects any document whose status disagrees with its cases (report_invalid).';
COMMENT ON COLUMN platform.evaluation_reports.dataset_sha256 IS
    'The dataset artifact sha256 the report declares; equals platform.artifacts.sha256 of evaluation_jobs.dataset_artifact_id, re-read in the completion transaction.';
COMMENT ON COLUMN platform.evaluation_reports.result_artifact_id IS
    'The sealed, ready report artifact (purpose=report) linked to the job. Never a client-supplied path or body; the guard reads the bytes through the object store.';
COMMENT ON CONSTRAINT publication_reviews_report_id_fkey ON platform.publication_reviews IS
    'Deferred FK completed by T026 (0013): a publication review must reference a real evaluation report.';
COMMENT ON CONSTRAINT model_profile_versions_evaluation_report_id_fkey ON platform.model_profile_versions IS
    'Deferred FK completed by T026 (0013): a model profile version may reference a real evaluation report. T026 does not write this column; the model-validation pipeline (F13A) owns that link.';
