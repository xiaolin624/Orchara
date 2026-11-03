-- F03/T034: accumulated active execution time across attempts, and the frozen
-- active-time deadline.
--
-- Baseline: contracts/database.sql already carries `runs.active_elapsed_ms`
-- (written by 0014). This migration adds the three columns the cross-attempt
-- accounting rule needs and nothing else:
--
--   * `runs.max_active_ms` — the frozen active-time limit in milliseconds, taken
--     from the execution snapshot's `definition.policy.max_active_seconds` at
--     run creation (x1000). NULL means "unknown / no deadline"; it is never
--     interpreted as zero. The column is frozen on the Run: a newer draft or a
--     changed policy never re-targets an existing Run.
--   * `run_attempts.active_ms` — the settled active interval of one attempt
--     (milliseconds), NOT NULL DEFAULT 0. Settlement writes it exactly once.
--   * `run_attempts.active_settled_at` — the idempotency marker for that
--     settlement. NULL means the attempt is still accruing; a non-NULL value
--     means its interval is already included in `runs.active_elapsed_ms`, so a
--     repeated settle/finalize must not add it again.
--
-- Backfill: existing Runs get `max_active_ms` from their frozen snapshot
-- projection when (and only when) it is a positive whole number of seconds in the
-- workflow schema's range 1..3600. A run whose snapshot has no such policy keeps
-- NULL and therefore has no deadline. Historical account time is not invented:
-- pre-migration attempts keep `active_ms = 0` / `active_settled_at = NULL` and
-- settle on their next transition; terminal runs finalized before this migration
-- keep `active_elapsed_ms = 0` because no per-attempt interval was recorded.
--
-- Deliberate boundaries:
--   * No deadline/policy table: the limit is a frozen Run value, not a new
--     policy evaluator. T036 refines cancel priority; T034's deadline path is the
--     pre-cancel path and reuses the T031 finalizer (no second finalizer).
--   * No trigger computing the columns: the trusted API process settles in the
--     same transaction as the transition out of executing. A database trigger
--     would be a second writer with a different clock/transaction boundary.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007–0019; new columns inherit the existing table privileges (none).
--   * Every statement is schema-qualified; nothing relies on search_path.

ALTER TABLE platform.runs
    ADD COLUMN IF NOT EXISTS max_active_ms bigint;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'runs_max_active_ms_positive'
           AND conrelid = 'platform.runs'::regclass
    ) THEN
        ALTER TABLE platform.runs
            ADD CONSTRAINT runs_max_active_ms_positive
            CHECK (max_active_ms IS NULL OR max_active_ms > 0);
    END IF;
END
$$;

ALTER TABLE platform.run_attempts
    ADD COLUMN IF NOT EXISTS active_ms bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS active_settled_at timestamptz;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'run_attempts_active_ms_nonnegative'
           AND conrelid = 'platform.run_attempts'::regclass
    ) THEN
        ALTER TABLE platform.run_attempts
            ADD CONSTRAINT run_attempts_active_ms_nonnegative
            CHECK (active_ms >= 0);
    END IF;
END
$$;

-- Backfill the frozen limit for Runs created before this migration. The
-- projection is the snapshot's JSONB `definition.policy.max_active_seconds`; the
-- workflow schema bounds it to an integer in [1, 3600], and anything else stays
-- NULL ("unknown"), never a guessed deadline.
UPDATE platform.runs AS r
   SET max_active_ms = (policy.seconds * 1000)::bigint
  FROM platform.execution_snapshots AS s
  CROSS JOIN LATERAL (
      SELECT (s.manifest #>> '{definition,policy,max_active_seconds}')::numeric AS seconds
       WHERE jsonb_typeof(s.manifest #> '{definition,policy,max_active_seconds}') = 'number'
  ) AS policy
 WHERE s.id = r.execution_snapshot_id
   AND r.max_active_ms IS NULL
   AND policy.seconds = trunc(policy.seconds)
   AND policy.seconds BETWEEN 1 AND 3600;

COMMENT ON COLUMN platform.runs.max_active_ms IS
    'Frozen active-execution limit in milliseconds (policy.max_active_seconds x 1000), set when the Run is created. NULL means unknown/no deadline and is never read as zero. Settlement adds per-attempt intervals to active_elapsed_ms; the Run limit never resets on a new attempt (T034).';
COMMENT ON COLUMN platform.run_attempts.active_ms IS
    'Settled active interval of this attempt in milliseconds (claim -> first transition out of executing). Written exactly once together with active_settled_at; the same delta is added to runs.active_elapsed_ms (T034).';
COMMENT ON COLUMN platform.run_attempts.active_settled_at IS
    'Idempotency marker for active_ms settlement. NULL = still accruing; non-NULL = already included in runs.active_elapsed_ms, so a repeated settle/finalize is a no-op (T034).';
COMMENT ON CONSTRAINT runs_max_active_ms_positive ON platform.runs IS
    'A known deadline is strictly positive; NULL (unknown/no deadline) is the only way to disable it.';
COMMENT ON CONSTRAINT run_attempts_active_ms_nonnegative ON platform.run_attempts IS
    'Settled active time cannot be negative (GREATEST(0, now-started_at)).';
