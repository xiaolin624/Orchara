-- F13A/T052: the evaluation runner's per-case run ledger and the hooks the
-- later F13A tickets extend.
--
-- Baseline: contracts/database.sql defines `evaluation_jobs` and
-- `evaluation_reports` (created by 0013). T026 deliberately added no
-- evaluation write endpoint and no per-case storage; the report's per-case
-- detail lives inside the sealed result artifact. T052 adds the runner, which
-- needs one more durable fact: which platform Run executed which case.
--
-- This migration creates:
--
--   1. `platform.evaluation_case_runs` — one row per evaluation case and the
--      Run the platform actually created for it. Every row is written by the
--      server (never by the maintenance handler), inside the same transaction
--      that creates the Run, so a case's run identity cannot be claimed after
--      the fact and cannot be forged.
--
-- and extends `platform.evaluation_jobs` with four additive columns:
--
--   2. `application_id` — the application the evaluation belongs to. The
--      snapshot already names it; this column makes the owner/application
--      predicate of the later reads a single-table fact.
--   3. `cancel_requested_at` — the evaluation-level cancel intent. T062 owns the
--      cancellation transition; T052 only provides the column so that ticket
--      does not have to redesign this table.
--   4. `progress_total` / `progress_done` — the coarse progress counters T065
--      exposes. They are counted from `evaluation_case_runs`, never invented.
--
-- Deliberate deviations from contracts/database.sql, with their owners:
--   * `evaluation_case_runs` is not a contract table. The contract's Report has
--     four fields and no per-case shape, so the artifact stays the record of
--     truth and this table is only the join/index T056 (per-case UI), T062
--     (ownership/cancellation integrity) and T065 (progress) build on.
--   * `evaluation_jobs.application_id` is nullable and has no NOT NULL: a
--     migration must not rewrite rows a previous ticket created (a
--     kind='model' evaluation has no application). T062 may tighten it for
--     kind='application' once every writer sets it; T052 always sets it.
--
-- Deliberate boundaries:
--   * No second report writer. `evaluation_reports` is untouched; the
--     completion guard stays the only writer of a report row.
--   * No new case/verdict authority: the sealed report artifact carries the
--     per-assertion evidence, and this table only records the run link and the
--     coarse state so a live progress read never parses an artifact.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * Privileges match 0007–0032: PUBLIC gets nothing on `platform`, and
--     `orchara_worker` gets no schema USAGE and no table/sequence privileges.
--     Evaluation rows are reached only through the authenticated API or the
--     maintenance identity's job-scoped endpoints.

CREATE SCHEMA IF NOT EXISTS platform;

-- One case of one evaluation, and the Run the platform created for it. The
-- primary key makes "one run per case" a database fact; `run_id` is UNIQUE so a
-- run can never be attributed to two cases.
CREATE TABLE IF NOT EXISTS platform.evaluation_case_runs (
    evaluation_id uuid NOT NULL REFERENCES platform.evaluation_jobs (id) ON DELETE CASCADE,
    case_id       text NOT NULL,
    -- Stable presentation order (the dataset's case order). T056 renders cases
    -- in this order, not in "whatever the runner finished first" order.
    ordinal       integer NOT NULL CHECK (ordinal >= 0),
    -- NULL until the server has created the run for this case. A blocked case
    -- (an unsupported target) never gets one.
    run_id        uuid UNIQUE REFERENCES platform.runs (id),
    -- The last observed state of the case: the run's own state once it exists,
    -- or the platform's per-case outcome vocabulary for a case with no run.
    state         text NOT NULL CHECK (state IN (
                      'pending', 'blocked',
                      'queued', 'running', 'waiting_approval', 'waiting_children',
                      'cancelling', 'recovery_pending', 'needs_reconciliation',
                      'succeeded', 'failed', 'cancelled'
                  )),
    -- A safe, closed reason code (never free text, never provider output).
    reason_code   text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT evaluation_case_runs_case_id_shape
        CHECK (length(btrim(case_id)) > 0 AND length(case_id) <= 200),
    CONSTRAINT evaluation_case_runs_reason_code_bounded
        CHECK (reason_code IS NULL OR length(reason_code) <= 128),
    PRIMARY KEY (evaluation_id, case_id)
);

-- T056/T065 read one evaluation's cases in order.
CREATE INDEX IF NOT EXISTS evaluation_case_runs_order
    ON platform.evaluation_case_runs (evaluation_id, ordinal);
-- T065 counts terminal cases cheaply.
CREATE INDEX IF NOT EXISTS evaluation_case_runs_state
    ON platform.evaluation_case_runs (evaluation_id, state);

-- The four additive evaluation_jobs columns described in the header. ADD COLUMN
-- with IF NOT EXISTS keeps a repeat migrate a no-op.
ALTER TABLE platform.evaluation_jobs
    ADD COLUMN IF NOT EXISTS application_id uuid REFERENCES platform.applications (id);
ALTER TABLE platform.evaluation_jobs
    ADD COLUMN IF NOT EXISTS cancel_requested_at timestamptz;
ALTER TABLE platform.evaluation_jobs
    ADD COLUMN IF NOT EXISTS progress_total integer NOT NULL DEFAULT 0;
ALTER TABLE platform.evaluation_jobs
    ADD COLUMN IF NOT EXISTS progress_done integer NOT NULL DEFAULT 0;

ALTER TABLE platform.evaluation_jobs
    DROP CONSTRAINT IF EXISTS evaluation_jobs_progress_shape;
ALTER TABLE platform.evaluation_jobs
    ADD CONSTRAINT evaluation_jobs_progress_shape
    CHECK (progress_total >= 0 AND progress_done >= 0 AND progress_done <= progress_total);

-- T062/T065 read an application's evaluation history.
CREATE INDEX IF NOT EXISTS evaluation_jobs_application
    ON platform.evaluation_jobs (application_id, created_at DESC)
    WHERE application_id IS NOT NULL;

-- Same privilege boundary as 0007–0032: the database never grants the worker a
-- direct path, and nothing is inherited from PUBLIC.
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

COMMENT ON TABLE platform.evaluation_case_runs IS
    'F13A/T052 per-case run ledger (non-contract): which platform Run executed which evaluation case. Written only by the trusted API server, in the same transaction that creates the Run and seals its input, so a case run cannot be claimed or forged after the fact. The per-assertion truth stays in the sealed report artifact; this table is the join T056 (per-case UI), T062 (ownership/cancel integrity) and T065 (progress) use.';
COMMENT ON COLUMN platform.evaluation_case_runs.run_id IS
    'The platform Run created for this case as the evaluation owner. NULL only for a case the runner could not run (an unsupported target or a refused collection); a NULL run_id never contributes to a passed report.';
COMMENT ON COLUMN platform.evaluation_case_runs.state IS
    'Last observed case state: the run''s own state once it exists, or the per-case outcome (blocked) when no run was created. T065 counts the rest states to surface progress without parsing the report artifact.';
COMMENT ON COLUMN platform.evaluation_case_runs.ordinal IS
    'The dataset''s case order, so the per-case UI (T056) renders a stable order.';
COMMENT ON COLUMN platform.evaluation_jobs.application_id IS
    'F13A/T052: the application the evaluation belongs to (NULL for kind=model until a later ticket decides otherwise). T062/T065 read ownership and history through it instead of joining the snapshot.';
COMMENT ON COLUMN platform.evaluation_jobs.cancel_requested_at IS
    'F13A/T052 column, T062 owns the transition: the evaluation-level cancel intent, distinct from a run cancel. NULL means no intent was recorded.';
COMMENT ON COLUMN platform.evaluation_jobs.progress_total IS
    'F13A/T052/T065: the number of cases in the submitted dataset. Counted from the sealed dataset by the runner; never estimated from wall-clock time.';
COMMENT ON COLUMN platform.evaluation_jobs.progress_done IS
    'F13A/T052/T065: how many case runs have reached a rest state (succeeded/failed/cancelled/recovery_pending/needs_reconciliation). Only the server writes it, from evaluation_case_runs.';
