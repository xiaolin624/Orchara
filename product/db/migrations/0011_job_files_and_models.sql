-- F00B/T024: job-scoped artifact access, candidate model profiles and the model
-- call ledger.
--
-- Baseline: contracts/database.sql defines `model_profile_versions`,
-- `platform_model_calls` and (partially) the job/artifact relationship. Every
-- column that is not in the contract baseline is listed below with its reason;
-- a later ticket that needs another column adds it in its own migration instead
-- of extending 0011.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `platform_model_calls.params_sha256 text` — the contract baseline has no
--      place to store the request parameters of a logical call, but
--      IMPLEMENTATION-DETAILS §4/§5 require "same logical key, different
--      parameters" to be a conflict rather than silently rebilled. The digest is
--      the JCS SHA-256 of the request params (internal/jcs), so a replayed
--      logical key can be proven identical. It never stores the params
--      themselves.
--   2. `platform.background_job_artifacts` is new: the contract relates jobs to
--      artifacts only through `input_artifact_id`/`result_artifact_id`, which
--      cannot express "this job produced this artifact while it is still
--      uploading/scanning" nor more than one output. Job-scoped access (T024's
--      artifact-uploads/complete/download/status) needs that provenance row, so
--      it lives here with an explicit owner (T024) and a mention that T026 reads
--      it too.
--
-- Cross-ticket foreign keys deliberately absent today:
--   * `platform_model_calls.request_id` stays a plain `uuid` column: the
--     `platform.requests` table is owned by F05/F07 and does not exist in this
--     batch. F05 (or F07 for the deletion path) adds the FK
--     `platform_model_calls.request_id -> platform.requests(id)` in its own
--     migration once `requests` exists; until then no code may treat that
--     column as a verified reference. The contract's XOR CHECK still holds:
--     exactly one of request_id/background_job_id is set.
--   * `model_profile_versions.evaluation_report_id` stays a plain `uuid`
--     column: `platform.evaluation_reports` is created by T026 (0012). T026
--     adds the FK in its migration; until then nothing may treat it as
--     validated.
--   * `platform_model_calls.background_job_id` DOES reference
--     `platform.background_jobs(id)` because that table exists since 0009;
--     the T024 ledger always sets it and leaves request_id NULL.
--
-- Privileges follow 0007–0010: the runtime role `orchara_worker` gets no
-- `platform` USAGE and no table/sequence privileges; the internal mTLS API is
-- the only door a worker/maintenance process has.

CREATE SCHEMA IF NOT EXISTS platform;

-- Model profile versions are immutable registrations. A changed provider
-- configuration is a new version; revocation is `revoked_at`, never a delete.
CREATE TABLE IF NOT EXISTS platform.model_profile_versions (
    id                    uuid PRIMARY KEY,
    name                  text NOT NULL,
    provider              text NOT NULL,
    model_id              text NOT NULL,
    endpoint_ref          text NOT NULL,
    secret_ref            text NOT NULL,
    config                jsonb NOT NULL,
    data_destination      text NOT NULL,
    manifest_sha256       text NOT NULL UNIQUE,
    manifest_artifact_id  uuid NOT NULL REFERENCES platform.artifacts (id),
    validation_state      text NOT NULL DEFAULT 'pending'
                          CHECK (validation_state IN ('pending', 'running', 'passed', 'failed')),
    -- Deferred FK: platform.evaluation_reports is created by T026 (0012).
    evaluation_report_id  uuid,
    revoked_at            timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- The controlled, non-Run model call ledger. Every attempt is one row; a
-- physical attempt number is monotonically increasing per (scope_key,
-- logical_key), and the UNIQUE constraint is what makes a concurrent duplicate
-- attempt impossible rather than merely unlikely.
CREATE TABLE IF NOT EXISTS platform.platform_model_calls (
    id                        uuid PRIMARY KEY,
    owner_id                  uuid NOT NULL REFERENCES platform.principals (id),
    -- Deferred FK: platform.requests is owned by F05/F07.
    request_id                uuid,
    background_job_id         uuid REFERENCES platform.background_jobs (id),
    model_profile_version_id  uuid NOT NULL REFERENCES platform.model_profile_versions (id),
    scope_key                 text NOT NULL,
    logical_key               text NOT NULL,
    physical_attempt          integer NOT NULL CHECK (physical_attempt > 0),
    state                     text NOT NULL CHECK (state IN (
                                  'prepared', 'dispatching', 'succeeded', 'failed', 'unknown', 'cancelled'
                              )),
    result_artifact_id        uuid REFERENCES platform.artifacts (id),
    currency                  text NOT NULL,
    reserved_amount           numeric(20, 8) NOT NULL CHECK (reserved_amount >= 0),
    charged_amount            numeric(20, 8) CHECK (charged_amount >= 0),
    tokens_in                 bigint,
    tokens_out                bigint,
    -- Deviation 1: request-parameter digest for "same key, different params".
    params_sha256             text CHECK (params_sha256 IS NULL OR length(params_sha256) = 64),
    created_at                timestamptz NOT NULL DEFAULT now(),
    -- The contract's exactly-one-owner rule for a ledger row.
    CHECK ((request_id IS NULL) <> (background_job_id IS NULL)),
    UNIQUE (scope_key, logical_key, physical_attempt)
);

-- Job-scoped artifact provenance. A job may read an artifact only when the job
-- itself names it as input/result or when this table links it to the job.
CREATE TABLE IF NOT EXISTS platform.background_job_artifacts (
    background_job_id  uuid NOT NULL REFERENCES platform.background_jobs (id) ON DELETE CASCADE,
    artifact_id        uuid NOT NULL REFERENCES platform.artifacts (id) ON DELETE CASCADE,
    relation           text NOT NULL CHECK (relation IN ('input', 'output')),
    purpose            text NOT NULL CHECK (length(btrim(purpose)) > 0),
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (background_job_id, artifact_id)
);

-- Diagnostics read one job's ledger rows in attempt order; the unique key serves
-- the (scope_key, logical_key, physical_attempt) point lookup.
CREATE INDEX IF NOT EXISTS platform_model_calls_job
    ON platform.platform_model_calls (background_job_id, created_at, id);
CREATE INDEX IF NOT EXISTS platform_model_calls_owner
    ON platform.platform_model_calls (owner_id, created_at DESC);
-- Reverse lookup "which jobs may read this artifact" is the scope check.
CREATE INDEX IF NOT EXISTS background_job_artifacts_artifact
    ON platform.background_job_artifacts (artifact_id);

-- Privileges: identical boundary to 0007–0010. New tables inherit it because
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

COMMENT ON TABLE platform.model_profile_versions IS
    'Immutable registered model profile version (IMPLEMENTATION-DETAILS §2). endpoint_ref/secret_ref are deployer-registered references, never URLs or secrets; go reads the real credential. validation_state=passed is required before a published application may reference it; revocation is revoked_at.';
COMMENT ON COLUMN platform.model_profile_versions.evaluation_report_id IS
    'Deferred FK (T024 decision, ADR 0004): evaluation_reports is created by T026 (0012), which adds the FK. Until then this column is a plain uuid and nothing may treat it as verified.';
COMMENT ON COLUMN platform.model_profile_versions.secret_ref IS
    'A reference (env:NAME or file:NAME) to a deployer-registered credential. The value never travels through the API, the worker, logs or this table.';
COMMENT ON TABLE platform.platform_model_calls IS
    'The controlled model call ledger (BACKGROUND-JOBS §3). Exactly one of request_id (F05/F07) and background_job_id owns the row; T024 always sets background_job_id. Every physical attempt has a row with real usage, reserved/charged amounts and a monotonically increasing attempt number.';
COMMENT ON COLUMN platform.platform_model_calls.request_id IS
    'Deferred FK (T024 decision, ADR 0004): platform.requests is owned by F05/F07 and does not exist in this batch. F05 adds the FK in its own migration; the contract XOR CHECK is still enforced.';
COMMENT ON COLUMN platform.platform_model_calls.params_sha256 IS
    'Deviation from database.sql (T024): JCS SHA-256 of the request params, so the same logical key with different parameters is a 409 conflict instead of a silently different billed call. The params themselves are not stored.';
COMMENT ON TABLE platform.background_job_artifacts IS
    'Deviation from database.sql (T024): job -> artifact provenance for job-scoped access. A job may read an artifact only when it is the job input/result or linked here; T026 reads the same rows.';
COMMENT ON COLUMN platform.background_job_artifacts.purpose IS
    'The artifact purpose declared at upload (report/log/receipt/parsed_data). It is the job-scope declaration, not an authorization by itself.';
