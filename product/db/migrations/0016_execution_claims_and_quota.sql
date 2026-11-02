-- F03/T029: one execution attempt per claim, the lease/heartbeat facts the
-- supervisor protocol extends, and the global execution-slot quota that makes
-- concurrent claims all-or-nothing.
--
-- Baseline: contracts/database.sql defines `run_attempts`, `quota_counters` and
-- `quota_allocations`, and adds the two foreign keys
-- `jobs.current_attempt_token -> run_attempts(token)` and
-- `run_artifacts.attempt_token -> run_attempts(token)`. T027 created the two
-- columns without those foreign keys and explicitly deferred them to F03
-- (migration 0014, comment on both columns); 0016 completes them.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `run_attempts_one_live_per_run` is a new partial UNIQUE index on
--      `run_attempts(run_id) WHERE exited_at IS NULL AND revoked_at IS NULL`.
--      The contract only makes the token unique. One run may have a history of
--      attempts, but at most one attempt can be live; a second live attempt is a
--      database conflict, not a convention. A run that is waiting for a result
--      and a run being recovered stay representable: an exited/revoked attempt
--      frees the slot immediately.
--   2. Two operational indexes are added:
--      `run_attempts_live_lease (lease_deadline)` for the expiry sweep T031
--      owns, and `run_attempts_live_job (job_id)` for the heartbeat lookup. Both
--      are partial on the same "live" predicate as the uniqueness rule, so they
--      stay cheap as attempt history grows.
--   3. `exit_evidence` and `checkpoint_connections` are CHECKed to the shapes the
--      contract already fixes: evidence is a JSON object (the confirmAttemptExit
--      body), checkpoint_connections is a JSON array (the heartbeat body). A
--      writer can never park a scalar there and make the recorded evidence
--      unreadable.
--   4. The quota seed `('global','execution_slots','used'=0,'capacity'=2)` is
--      inserted by this migration (ON CONFLICT DO NOTHING). Two concurrent
--      execution slots is the batch's documented deployment default; capacity is
--      a deployment knob (an operator updates `platform.quota_counters.capacity`,
--      tests do the same). The claim path reads the row under a row lock and
--      fails closed when it is missing instead of inventing a capacity.
--
-- Deliberate boundaries:
--   * No requests/conversations, node_attempts, operations, approvals,
--     run_events, usage_ledger or background handlers: later F03/F05 tickets own
--     them. `run_attempts.reported_result`/`checkpoint_id` are created because
--     the contract baseline carries them; T029 never writes them (T031's
--     finalizer does).
--   * Quota release is `released_at` + `used-1` in one transaction, guarded by
--     `released_at IS NULL`, so a repeated exit cannot decrement twice. The
--     allocation row is reused for the same holder (`job:<job_id>`) because the
--     contract keeps it unique per holder; re-taking a released slot clears
--     `released_at` instead of inserting a second history row.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007–0015. The attempt row is written only by the trusted API process.

CREATE SCHEMA IF NOT EXISTS platform;

-- One claimed execution attempt. `token` is the worker credential and the
-- foreign-key target for `jobs.current_attempt_token`/`run_artifacts`.
CREATE TABLE IF NOT EXISTS platform.run_attempts (
    token                   uuid PRIMARY KEY,
    job_id                  uuid NOT NULL REFERENCES platform.jobs (id),
    run_id                  uuid NOT NULL REFERENCES platform.runs (id),
    worker_id               text NOT NULL,
    execution_unit_id       text NOT NULL,
    lease_deadline          timestamptz NOT NULL,
    heartbeat_at            timestamptz NOT NULL,
    started_at              timestamptz NOT NULL DEFAULT now(),
    exited_at               timestamptz,
    exit_evidence           jsonb,
    checkpoint_connections  jsonb NOT NULL DEFAULT '[]',
    revoked_at              timestamptz,
    reported_result         jsonb,
    reported_at             timestamptz,
    checkpoint_id           text,
    -- Deviation 3: the two JSONB evidence columns keep the contract's shape.
    CHECK (exit_evidence IS NULL OR jsonb_typeof(exit_evidence) = 'object'),
    CHECK (jsonb_typeof(checkpoint_connections) = 'array')
);

-- Deviation 1: at most one live attempt per run. An exited or revoked attempt
-- no longer occupies the slot, so recovery/resume can issue a new attempt.
CREATE UNIQUE INDEX IF NOT EXISTS run_attempts_one_live_per_run
    ON platform.run_attempts (run_id)
    WHERE exited_at IS NULL AND revoked_at IS NULL;

-- Deviation 2: the two operational lookups (expiry sweep, heartbeat by job).
CREATE INDEX IF NOT EXISTS run_attempts_live_lease
    ON platform.run_attempts (lease_deadline)
    WHERE exited_at IS NULL AND revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS run_attempts_live_job
    ON platform.run_attempts (job_id)
    WHERE exited_at IS NULL AND revoked_at IS NULL;

-- One global execution-slot counter. `used` counts live allocations; the claim
-- path updates it only while `used < capacity` in the same transaction that
-- takes an allocation.
CREATE TABLE IF NOT EXISTS platform.quota_counters (
    scope     text NOT NULL,
    dimension text NOT NULL,
    used      integer NOT NULL CHECK (used >= 0),
    capacity  integer NOT NULL CHECK (capacity >= 0),
    PRIMARY KEY (scope, dimension),
    CHECK (used <= capacity)
);

-- The per-holder allocation. The holder key is `job:<job_id>`; the unique
-- constraint is the idempotency key of both take and release. `released_at` is
-- cleared (not a new row) when the same holder takes a slot again.
CREATE TABLE IF NOT EXISTS platform.quota_allocations (
    id          uuid PRIMARY KEY,
    scope       text NOT NULL,
    dimension   text NOT NULL,
    holder_key  text NOT NULL,
    released_at timestamptz,
    FOREIGN KEY (scope, dimension)
        REFERENCES platform.quota_counters (scope, dimension),
    UNIQUE (scope, dimension, holder_key)
);

-- Deviation 4: the deployment default. Capacity is intentionally a data knob;
-- the migration never overwrites an operator's value on replay.
INSERT INTO platform.quota_counters (scope, dimension, used, capacity)
VALUES ('global', 'execution_slots', 0, 2)
ON CONFLICT (scope, dimension) DO NOTHING;

-- The two foreign keys T027 deferred to F03. ADD CONSTRAINT has no IF NOT EXISTS,
-- so the guard keeps this migration idempotent. Every pre-existing row has a NULL
-- token (0014 wrote no attempt), so no backfill is needed.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'jobs_current_attempt_token_fkey'
           AND conrelid = 'platform.jobs'::regclass
    ) THEN
        ALTER TABLE platform.jobs
            ADD CONSTRAINT jobs_current_attempt_token_fkey
            FOREIGN KEY (current_attempt_token) REFERENCES platform.run_attempts (token);
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'run_artifacts_attempt_token_fkey'
           AND conrelid = 'platform.run_artifacts'::regclass
    ) THEN
        ALTER TABLE platform.run_artifacts
            ADD CONSTRAINT run_artifacts_attempt_token_fkey
            FOREIGN KEY (attempt_token) REFERENCES platform.run_attempts (token);
    END IF;
END
$$;

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

COMMENT ON TABLE platform.run_attempts IS
    'One claimed execution attempt (contracts/database.sql). The token is the worker credential and the FK target for jobs.current_attempt_token/run_artifacts.attempt_token. At most one attempt per run is live; exited_at/revoked_at free the slot. T029 writes claim/heartbeat/exit evidence; T031 extends the same row into the unified finalizer.';
COMMENT ON COLUMN platform.run_attempts.execution_unit_id IS
    'The supervisor-preallocated execution unit this attempt is bound to. The exit endpoint refuses evidence from any other unit.';
COMMENT ON COLUMN platform.run_attempts.exit_evidence IS
    'Supervisor exit evidence, recorded verbatim as a JSON object. evidence.started=false is the durable "the process never started" proof T029 releases on; other values hold the job for the F03 finalizer.';
COMMENT ON COLUMN platform.run_attempts.checkpoint_connections IS
    'The checkpoint backend connections reported by the latest heartbeat (pid, backend_start, application_name). Written only by the heartbeat; recovery verification is T031.';
COMMENT ON COLUMN platform.run_attempts.reported_result IS
    'Reserved by the contract for the worker result proposal; T029 never writes it (T031 finalizer).';
COMMENT ON TABLE platform.quota_counters IS
    'Per-scope/dimension capacity counters (contracts/database.sql). 0016 seeds global/execution_slots used=0 capacity=2; capacity is a deployment knob, never a per-request value.';
COMMENT ON TABLE platform.quota_allocations IS
    'One slot allocation per holder (contracts/database.sql). Holder key is job:<job_id>. Taking a slot inserts or re-arms (released_at = NULL) the row; release sets released_at and decrements used exactly once, guarded by released_at IS NULL.';
COMMENT ON CONSTRAINT jobs_current_attempt_token_fkey ON platform.jobs IS
    'Deferred FK completed by T029 (0016): the leased job always names a real run_attempts row.';
COMMENT ON CONSTRAINT run_artifacts_attempt_token_fkey ON platform.run_artifacts IS
    'Deferred FK completed by T029 (0016): an attempt-produced artifact link names a real attempt. T029 writes no attempt links.';
