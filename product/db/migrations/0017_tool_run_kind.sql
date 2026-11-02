-- F05/T030: the preset tool_run background-job kind.
--
-- Baseline: contracts/database.sql fixes eight background-job kinds, and 0009
-- copied that list into `background_jobs_kind_check`. T030 adds the ninth kind
-- `tool_run`: the maintenance process claims it, downloads one caller-owned
-- ready CSV/XLSX artifact, executes the trusted preset sales-statistics tool as
-- a real subprocess, registers the summary/log/diagnostics artifacts and
-- completes the job.
--
-- This is a *documented non-contract extension* (ADR 0005 / the T030 report):
-- the frozen contract has no tool-run endpoint or kind yet. T033 binds preset
-- tools to the sandboxed run/worker protocol and F04A owns the real Python node
-- execution; until then `tool_run` is the honest bridge that reuses the existing
-- durable background-job pipeline instead of faking a run. The contract is not
-- edited: `contracts/database.sql` and `contracts/openapi.json` stay the frozen
-- baseline, and tools/sync_spec.py --check stays clean.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. The ninth kind `tool_run` in `background_jobs_kind_check` (T030). The Go
--      whitelist in internal/background/job.go and the CHECK list here are
--      extended together; an unknown kind is still refused by both.
--
-- Deliberate boundaries:
--   * No new table and no business row: a tool run is one background job plus
--     the artifacts it registers through the existing job-scoped upload path.
--     There is no tool/tool-version table (T037 owns builds and versions) and no
--     run/result/event protocol (T031/F04A own it).
--   * The retry table lives in Go (internal/background/job.go): tool rejection
--     codes are business failures (no retry); timeout/crash/storage IO are
--     infrastructure failures (bounded retry), exactly like the other kinds.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC keep getting nothing from `platform`: this
--     migration changes a CHECK constraint only, and states the existing
--     boundary explicitly so a future audit does not have to infer it.

CREATE SCHEMA IF NOT EXISTS platform;

-- 0009 created the check inline (PostgreSQL named it background_jobs_kind_check).
-- Drop-and-recreate is idempotent in the same migration run and on a deliberate
-- replay: the DROP is guarded, the ADD always follows it.
DO $$
BEGIN
    ALTER TABLE platform.background_jobs
        DROP CONSTRAINT IF EXISTS background_jobs_kind_check;
END
$$;

ALTER TABLE platform.background_jobs
    ADD CONSTRAINT background_jobs_kind_check CHECK (kind IN (
        'artifact_finalize', 'route_request', 'tool_build', 'document_ingest',
        'application_evaluate', 'model_validate', 'delete_content', 'catalog_index',
        'tool_run'
    ));

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

COMMENT ON CONSTRAINT background_jobs_kind_check ON platform.background_jobs IS
    'Closed background-job kind vocabulary. T030 (0017) appends tool_run as a documented non-contract bridge for the trusted preset tool; T033/F04A will bind it to the run/sandbox protocol. Any later kind extends this list in its own migration and in internal/background/job.go together.';
