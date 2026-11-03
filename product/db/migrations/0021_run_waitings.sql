-- F03/T035: the waiting condition of a Run, its stored resolution and the single
-- resume job.
--
-- A worker's `waiting_approval` / `waiting_children` result proposal is not a
-- terminal answer: the Run stops executing at a fixed checkpoint and waits for an
-- external fact (an approval decision, or a child Run's result) *and* for the
-- supervisor's exit evidence. This table is the one durable record of that
-- hand-over. It deliberately stores no approval, operation, child or conversation
-- rows: F06 owns approvals/operations and F07 owns children/conversations, and a
-- synthetic internal fixture (the non-contract waiting-resolution endpoint) is the
-- documented bridge until they exist.
--
-- Columns:
--   * `run_id` PK/REFERENCES runs — one waiting condition per Run, so the resume
--     machinery cannot be started twice for the same wait.
--   * `kind` — which external fact is awaited. `approval` pairs with the Run state
--     `waiting_approval`, `children` with `waiting_children`.
--   * `checkpoint_id` — the contract's checkpoint the worker stopped at. NOT NULL
--     because a waiting proposal without a checkpoint cannot be resumed at all
--     (T031 already requires it in the request; this is the durable copy).
--   * `decision` (jsonb, NULL until a resolution arrives) — a *safe* document,
--     never a raw Command/thread/user value. Approval:
--     {"kind":"approval","outcome":"approved|rejected|expired","reference":"<opaque>"}.
--     Children: {"kind":"children","outcome":"succeeded|failed","child_run_id":"<uuid>"}.
--     The CHECK keeps the union closed and refuses a document whose shape does not
--     match `kind`.
--   * `decision_at` — the database clock fact that the decision was stored
--     (`now()`), paired 1:1 with `decision`. The decision document itself carries
--     no client timestamp: one clock, decided by PostgreSQL.
--   * `exit_confirmed_at` — set only by the supervisor's exit-evidence path (the
--     ordinary worker cannot certify its own exit). A resume is impossible before
--     this fact exists.
--   * `resume_job_id` — the one resume job, UNIQUE. Together with
--     `one_live_job_per_run` (0014) this is what makes a second resume impossible
--     in the database rather than by a Go check.
--   * `resume_artifact_id` — the sealed canonical resume payload (`resume_input`
--     artifact) that the resume job names. Also recorded on `jobs.resume_artifact_id`
--     so the claim can sign it; this column is the audit copy.
--   * `created_at`/`updated_at` — written explicitly (no trigger): the trusted API
--     process is the only writer.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. This table does not exist in the contract baseline. F03 needs the waiting
--      hand-over durable and F06/F07 have not built approvals/children yet; the
--      table is a documented non-contract extension (ADR 0005), like
--      `artifact_admissions` (0015) and `run_artifact_uploads` (0019).
--      `tools/sync_spec.py --check` must stay clean (it does: only generated
--      contract files are compared).
--   2. No approval/operation/child tables and no columns for them. The decision is
--      a small safe JSON document; F06/F07 will reference their own rows and may
--      add a typed foreign key in their own migration.
--   3. CHECK `resume_job_id IS NULL OR exit_confirmed_at IS NOT NULL`: a resume
--      can never be attached before the exit fact. The Go transaction writes both
--      in one UPDATE, so no intermediate row violates it.
--
-- Deliberate boundaries:
--   * No trigger, no second writer: T035's `maybe_enqueue_resume` transaction
--     writes this row together with the job/run/event rows.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007-0020: the worker goes through the mTLS endpoints.

CREATE TABLE IF NOT EXISTS platform.run_waitings (
    run_id             uuid PRIMARY KEY REFERENCES platform.runs (id),
    kind               text NOT NULL,
    checkpoint_id      text NOT NULL,
    decision           jsonb,
    decision_at        timestamptz,
    exit_confirmed_at  timestamptz,
    resume_job_id      uuid UNIQUE REFERENCES platform.jobs (id),
    resume_artifact_id uuid REFERENCES platform.artifacts (id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT run_waitings_kind_check CHECK (kind IN ('approval', 'children')),
    CONSTRAINT run_waitings_checkpoint_nonblank
        CHECK (length(btrim(checkpoint_id)) > 0),
    -- The decision document and its database-clock timestamp exist together.
    CONSTRAINT run_waitings_decision_pair
        CHECK ((decision IS NULL) = (decision_at IS NULL)),
    CONSTRAINT run_waitings_decision_object
        CHECK (decision IS NULL OR jsonb_typeof(decision) = 'object'),
    -- The union is closed and must agree with `kind`; there is no free-form
    -- Command/thread/user payload.
    CONSTRAINT run_waitings_decision_shape CHECK (
        decision IS NULL
        OR (decision->>'kind' = kind AND (
            (kind = 'approval'
                AND decision->>'outcome' IN ('approved', 'rejected', 'expired')
                AND decision ? 'reference'
                AND NOT (decision ? 'child_run_id'))
            OR (kind = 'children'
                AND decision->>'outcome' IN ('succeeded', 'failed')
                AND decision ? 'child_run_id'
                AND NOT (decision ? 'reference'))
        ))
    ),
    -- A resume is attached only after the exit evidence exists; the Go transaction
    -- writes resume_job_id and exit_confirmed_at in one statement.
    CONSTRAINT run_waitings_resume_requires_exit
        CHECK (resume_job_id IS NULL OR exit_confirmed_at IS NOT NULL),
    CONSTRAINT run_waitings_resume_artifact_requires_exit
        CHECK (resume_artifact_id IS NULL OR exit_confirmed_at IS NOT NULL)
);

-- "which runs are waiting / what is outstanding" is an operator read; the PK
-- serves the per-Run read and the UNIQUE(resume_job_id) serves the resume lookup.
CREATE INDEX IF NOT EXISTS run_waitings_kind_created
    ON platform.run_waitings (kind, created_at);

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

COMMENT ON TABLE platform.run_waitings IS
    'F03/T035 waiting hand-over: one row per Run that stopped at waiting_approval/waiting_children. Stores the fixed checkpoint, the safe resolution document, the supervisor exit fact and the single resume job. Not a contract table; F06/F07 own the real approvals/children and this is the documented bridge (ADR 0005).';
COMMENT ON COLUMN platform.run_waitings.kind IS
    'The awaited external fact: approval (Run state waiting_approval) or children (Run state waiting_children). Matched against runs.state by the resolution endpoint; a mismatch is run_state_conflict.';
COMMENT ON COLUMN platform.run_waitings.checkpoint_id IS
    'The contract checkpoint_id of the waiting result proposal (T031 requires it in the request). A waiting condition without a fixed checkpoint can never be resumed.';
COMMENT ON COLUMN platform.run_waitings.decision IS
    'Safe resolution document, NULL until a resolution arrives: approval {kind,outcome(approved|rejected|expired),reference}, children {kind,outcome(succeeded|failed),child_run_id}. Never a raw Command, thread id or user identifier; the CHECK keeps the union closed.';
COMMENT ON COLUMN platform.run_waitings.decision_at IS
    'Database-clock time the decision was stored. Paired 1:1 with decision; the decision document intentionally carries no client-supplied timestamp.';
COMMENT ON COLUMN platform.run_waitings.exit_confirmed_at IS
    'Set only by the supervisor exit-evidence path. A resume is impossible before this fact: the ordinary worker cannot certify its own exit (EXECUTION.md §4).';
COMMENT ON COLUMN platform.run_waitings.resume_job_id IS
    'The one resume job (UNIQUE). Combined with jobs.one_live_job_per_run this makes a second resume impossible in the database, not merely in Go. Written in the same statement as exit_confirmed_at.';
COMMENT ON COLUMN platform.run_waitings.resume_artifact_id IS
    'The sealed canonical resume payload artifact (purpose resume_input) the resume job carries; the audit copy of jobs.resume_artifact_id.';
COMMENT ON CONSTRAINT run_waitings_decision_shape ON platform.run_waitings IS
    'The stored decision must match the wait kind and stay inside the closed union; arbitrary worker/Command payloads are refused.';
