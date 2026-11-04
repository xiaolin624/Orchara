-- F13A/T065: async evaluation progress persisted by the trusted runner.
--
-- Baseline: contracts/database.sql defines `evaluation_jobs` and
-- `evaluation_reports` (0013); F13A/T052's 0035 added the per-case run ledger
-- `evaluation_case_runs` and the coarse `progress_total` / `progress_done`
-- counters. Those counters were only ever recomputed from a case run's observed
-- state, so a case the runner decides *without* a run (an unsupported target, a
-- refused collection) and a case verdict (passed/failed/blocked/not_run) were
-- not representable. T065 needs both to answer "how far is my evaluation?" and
-- "which cases failed?" honestly.
--
-- This migration adds:
--
--   1. `evaluation_case_runs.verdict` — the runner's per-case decision, written
--      only by the server-side job-scoped progress bridge (never by the
--      maintenance handler directly). `state` stays the observed run state, so
--      a cancelled run with a failed case verdict keeps both facts.
--   2. `evaluation_jobs.progress_failed` — the number of cases whose verdict is
--      `failed`, recomputed from the case rows; the completion guard also writes
--      the sealed report's final count in the same transaction that records the
--      report.
--
-- Deliberate deviations from contracts/database.sql, with their owners:
--   * `evaluation_case_runs.verdict` and `evaluation_jobs.progress_failed` are
--     non-contract columns. The contract's EvaluationJob is six fields and its
--     Report is four; the progress read is a documented non-contract surface
--     (ADR 0016) and these columns are its persisted truth. No contract
--     projection changes.
--
-- Deliberate boundaries:
--   * No second report writer: `evaluation_reports` is untouched; the
--     completion guard stays its only writer.
--   * `progress_done` keeps its meaning ("cases with a decision or a run that
--     reached a rest state"); the runner's progress report and the facts read
--     use the same expression, so neither can lower it on a retry.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * Privileges match 0007–0037: PUBLIC gets nothing on `platform`, and
--     `orchara_worker` gets no schema USAGE and no table/sequence privileges.

ALTER TABLE platform.evaluation_case_runs
    ADD COLUMN IF NOT EXISTS verdict text;

ALTER TABLE platform.evaluation_case_runs
    DROP CONSTRAINT IF EXISTS evaluation_case_runs_verdict_shape;
ALTER TABLE platform.evaluation_case_runs
    ADD CONSTRAINT evaluation_case_runs_verdict_shape
    CHECK (verdict IS NULL OR verdict IN ('passed', 'failed', 'blocked', 'not_run'));

-- T065 counts completed/failed decisions cheaply.
CREATE INDEX IF NOT EXISTS evaluation_case_runs_verdict
    ON platform.evaluation_case_runs (evaluation_id, verdict)
    WHERE verdict IS NOT NULL;

ALTER TABLE platform.evaluation_jobs
    ADD COLUMN IF NOT EXISTS progress_failed integer NOT NULL DEFAULT 0;

ALTER TABLE platform.evaluation_jobs
    DROP CONSTRAINT IF EXISTS evaluation_jobs_progress_failed_shape;
ALTER TABLE platform.evaluation_jobs
    ADD CONSTRAINT evaluation_jobs_progress_failed_shape
    CHECK (progress_failed >= 0 AND progress_failed <= progress_done);

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

COMMENT ON COLUMN platform.evaluation_case_runs.verdict IS
    'F13A/T065 non-contract: the runner''s per-case decision (passed/failed/blocked/not_run). Written only through the job-scoped progress bridge, which re-proves the job lease and the evaluation owner''s access. NULL until the runner has decided the case. The observed run state stays in `state`, so a cancelled run with a failed verdict keeps both facts.';
COMMENT ON COLUMN platform.evaluation_case_runs.state IS
    'Last observed case state: the run''s own state once it exists, or the per-case outcome (blocked) when no run was created. T065 counts the rest states together with a non-null verdict to surface progress without parsing the report artifact.';
COMMENT ON COLUMN platform.evaluation_jobs.progress_total IS
    'F13A/T052/T065: the number of cases in the submitted dataset. Counted from the sealed dataset by the runner; never estimated from wall-clock time.';
COMMENT ON COLUMN platform.evaluation_jobs.progress_done IS
    'F13A/T052/T065: how many cases have a decision (verdict) or a case run in a rest state. Written by the server from evaluation_case_runs; the completion guard writes the sealed report''s final count.';
COMMENT ON COLUMN platform.evaluation_jobs.progress_failed IS
    'F13A/T065: how many cases the runner decided as failed. Recomputed from evaluation_case_runs.verdict by the progress bridge and written from the sealed report by the completion guard.';
