-- F11/T069: the shared deletion foundation (tombstone + durable deletion job).
--
-- Baseline: 0036 created the contract's `platform.deletion_jobs` for the F09/T058
-- memory delete, with `remaining_scopes` as an array of copy *descriptors*
-- (`{"kind":"object",...}`). The contract's `DeletionJob.remaining_scopes` is an
-- array of *scope names* (`contracts/openapi.json`), and F11 needs one vocabulary
-- that T071 (object/index/cache) and T072 (checkpoint/temp/third-party/backup)
-- can both consume without rewriting the job model. This migration therefore:
--
--   1. adds the per-job progress/retry/timestamp columns the T069 ticket needs:
--      `scope_details` (the copy descriptors per fixed scope),
--      `completed_scopes` (scopes already cleared; maintained by T071/T072),
--      `attempt_count`, `error_code`, `started_at`, `updated_at`. The closed job
--      state enum is unchanged (`pending|running|completed|failed`).
--   2. converts the legacy T058 `remaining_scopes` arrays of objects into the
--      fixed scope vocabulary. Mapping: object/artifact -> object_store,
--      embedding -> search_index. The old `memory_row` descriptor is not a
--      purge scope (the tombstone row is the no-resurrection authority and is
--      never deleted), so it is preserved in `scope_details` only conceptually
--      and is not turned into a scope. New T069 jobs always start with all seven
--      vocabulary scopes outstanding.
--   3. indexes the non-terminal jobs for the T071/T072 worker.
--
-- Deliberate deviations from contracts/database.sql (all additive; the owning
-- later tickets are named):
--   * `remaining_scopes` now carries the closed string vocabulary documented in
--     ADR 0020 / the T069 report (the contract only says `string[]`). The values
--     are `object_store|search_index|retrieval_cache|checkpoints|sandbox_temp|
--     third_party_history|backup_catalog`.
--   * `scope_details`, `completed_scopes`, `attempt_count`, `error_code`,
--     `started_at`, `updated_at` are not in the reference table. They are the
--     foundation's progress/retry fields required by the T069 ticket; T071/T072
--     own writing them per scope. `GET /api/v1/deletions/{id}` never exposes
--     them (the contract schema is additionalProperties:false).
--   * No new table is created here for the backup ledger (T072) or diagnostic
--     grants (T073); `platform.conversations` is T074's.
--
-- Zero grants: every statement is schema-qualified and `orchara_worker`/PUBLIC
-- keep no `platform` privileges, exactly like 0007-0041.

CREATE SCHEMA IF NOT EXISTS platform;

ALTER TABLE platform.deletion_jobs
    ADD COLUMN IF NOT EXISTS scope_details  jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS completed_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS attempt_count  integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS error_code     text,
    ADD COLUMN IF NOT EXISTS started_at     timestamptz,
    ADD COLUMN IF NOT EXISTS updated_at     timestamptz NOT NULL DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'deletion_jobs_attempt_count_nonnegative'
    ) THEN
        ALTER TABLE platform.deletion_jobs
            ADD CONSTRAINT deletion_jobs_attempt_count_nonnegative
            CHECK (attempt_count >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'deletion_jobs_completed_scopes_array'
    ) THEN
        ALTER TABLE platform.deletion_jobs
            ADD CONSTRAINT deletion_jobs_completed_scopes_array
            CHECK (jsonb_typeof(completed_scopes) = 'array');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'deletion_jobs_scope_details_object'
    ) THEN
        ALTER TABLE platform.deletion_jobs
            ADD CONSTRAINT deletion_jobs_scope_details_object
            CHECK (jsonb_typeof(scope_details) = 'object');
    END IF;
END
$$;

-- Legacy T058 rows: translate descriptor arrays into the fixed scope vocabulary
-- and keep the descriptors in scope_details. The legacy `memory_row` entry is
-- dropped from the purge scopes on purpose (it must never be purged).
WITH expanded AS (
    SELECT j.id,
           elem.value AS descriptor,
           CASE elem.value->>'kind'
               WHEN 'object' THEN 'object_store'
               WHEN 'artifact' THEN 'object_store'
               WHEN 'embedding' THEN 'search_index'
               ELSE NULL
           END AS scope
      FROM platform.deletion_jobs AS j
      CROSS JOIN LATERAL jsonb_array_elements(j.remaining_scopes) AS elem(value)
     WHERE jsonb_typeof(j.remaining_scopes) = 'array'
       AND jsonb_array_length(j.remaining_scopes) > 0
       AND jsonb_typeof(j.remaining_scopes->0) = 'object'
), grouped AS (
    SELECT id, scope, jsonb_agg(descriptor ORDER BY descriptor::text) AS items
      FROM expanded
     WHERE scope IS NOT NULL
     GROUP BY id, scope
), rebuilt AS (
    SELECT id,
           jsonb_agg(scope ORDER BY scope) AS remaining,
           jsonb_object_agg(scope, jsonb_build_object('items', items)) AS details
      FROM grouped
     GROUP BY id
)
UPDATE platform.deletion_jobs AS j
   SET remaining_scopes = rebuilt.remaining,
       scope_details    = rebuilt.details,
       completed_scopes = '[]'::jsonb,
       updated_at       = now()
  FROM rebuilt
 WHERE j.id = rebuilt.id;

-- A legacy job whose descriptors carried no purgeable scope (only `memory_row`)
-- still becomes a clean empty string array; a mixed-shape column would make the
-- contract projection ambiguous.
UPDATE platform.deletion_jobs
   SET remaining_scopes = '[]'::jsonb,
       scope_details    = '{}'::jsonb,
       updated_at       = now()
 WHERE jsonb_typeof(remaining_scopes) = 'array'
   AND jsonb_array_length(remaining_scopes) > 0
   AND jsonb_typeof(remaining_scopes->0) = 'object';

-- T071/T072 scan durable non-terminal jobs; the partial index keeps the worker
-- query bounded as history accumulates.
CREATE INDEX IF NOT EXISTS deletion_jobs_open
    ON platform.deletion_jobs (state, created_at)
    WHERE state IN ('pending', 'running', 'failed');

COMMENT ON COLUMN platform.deletion_jobs.remaining_scopes IS
    'F11/T069: closed scope vocabulary still outstanding, string[] in the contract DeletionJob order: object_store|search_index|retrieval_cache|checkpoints|sandbox_temp|third_party_history|backup_catalog. T071 owns the first three, T072 the last four; a scope is removed only when it is cleared.';
COMMENT ON COLUMN platform.deletion_jobs.scope_details IS
    'F11/T069: per-scope copy descriptors (object keys/artifact ids/document ids/run ids) the cleanup tickets use. Non-contract internal detail; never returned by GET /deletions.';
COMMENT ON COLUMN platform.deletion_jobs.completed_scopes IS
    'F11/T069: scopes already cleared by T071/T072; remaining_scopes excludes them.';
COMMENT ON COLUMN platform.deletion_jobs.attempt_count IS
    'F11/T069: cleanup attempts; T071/T072 increment on retry. The job row is never deleted.';
COMMENT ON COLUMN platform.deletion_jobs.error_code IS
    'F11/T069: the last safe cleanup/cancellation error code (non-contract; GET /deletions exposes only the contract fields).';

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
