-- F05/T033: run-bound artifact provenance.
--
-- The job-scoped flow (T024/T030) proves a handler's upload belongs to the job
-- it claimed. A Run has two parties instead: the supervisor claims the attempt
-- but the worker uploads. This table records *which attempt* reserved each
-- artifact for *which run*, so:
--
--   * only the attempt that uploaded an artifact may complete or register it;
--   * a superseded attempt cannot register an output for a later attempt;
--   * the artifact-status endpoint can answer "this attempt's artifact" without
--     trusting an artifact id supplied by the caller.
--
-- Provenance is intentionally insert-once and never updated: the upload is
-- reserved once, and a replay is idempotent because the row already names the
-- same attempt/run/purpose. Reuse `platform.run_artifacts` for the run link
-- (relation output/artifact/log); no new deletion model is introduced here.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `purpose` is closed to the contract's createWorkerUpload enum
--      (node_output|final_output|log|receipt). A run-scoped upload purpose is
--      not free text: the artifact list and the "logs are not data" rule depend
--      on it. A later purpose extends this CHECK in its own migration.
--   2. `attempt_token` is NOT NULL and references run_attempts. An artifact
--      reserved by a party that is not a live attempt has no owner to prove
--      provenance with, so the reservation is refused instead of stored.
--   3. No `deleted_at`/`purged_at` here: F07 owns unlink/purge and must delete
--      the provenance row together with the artifact (the artifact_id PK makes
--      a reused id impossible).
--
-- Deliberate boundaries:
--   * No new artifact columns, no second artifact table, no run-scoped deletion
--     model, no session/request/approval tables.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007-0018: the worker goes through the mTLS endpoints.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.run_artifact_uploads (
    artifact_id   uuid PRIMARY KEY REFERENCES platform.artifacts (id),
    run_id        uuid NOT NULL REFERENCES platform.runs (id),
    attempt_token uuid NOT NULL REFERENCES platform.run_attempts (token),
    purpose       text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT run_artifact_uploads_purpose_check CHECK (purpose IN (
        'node_output', 'final_output', 'log', 'receipt'
    ))
);

-- "which artifacts did this run/attempt produce" is the status/register path.
CREATE INDEX IF NOT EXISTS run_artifact_uploads_run
    ON platform.run_artifact_uploads (run_id, created_at);

CREATE INDEX IF NOT EXISTS run_artifact_uploads_attempt
    ON platform.run_artifact_uploads (attempt_token, created_at);

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

COMMENT ON TABLE platform.run_artifact_uploads IS
    'Run-bound upload provenance: which attempt reserved which artifact for which run and purpose. The register endpoint requires a ready artifact whose provenance names the live attempt; a superseded attempt cannot register current output.';
COMMENT ON COLUMN platform.run_artifact_uploads.artifact_id IS
    'One row per reserved artifact (PK). The artifact row itself owns owner/state/object keys; this table only proves the run/attempt/purpose that reserved it.';
COMMENT ON COLUMN platform.run_artifact_uploads.attempt_token IS
    'The live run_attempts.token that reserved the upload. Revoked/exited/superseded tokens are refused by the endpoints even though the provenance row remains as history.';
COMMENT ON COLUMN platform.run_artifact_uploads.purpose IS
    'The contract createWorkerUpload purpose enum. log is listed by purpose (never by object key) and is excluded from listRunArtifacts because stdout/stderr is not run data.';
