-- P04/T013: a fencing token per worker attempt.
--
-- A run may be retried only after the old attempt is known to be gone, and the
-- next attempt needs a credential the old one cannot keep using. The control
-- plane issues one token per attempt; superseding the previous token is how a
-- new attempt takes the lease over. A superseded token must never be accepted
-- again, even when the old process is still alive and still holds it.
--
-- Two properties are enforced here rather than in a caller, because a caller
-- can race:
--
-- * at most one *active* (not superseded) token per run, via a partial unique
--   index;
-- * supersession is a single conditional UPDATE ... WHERE superseded_at IS NULL
--   RETURNING, so two racing attempts cannot both win and both become active.
--
-- Note what this does *not* prove: a token expiring, being revoked, or losing a
-- race says nothing about whether the old process died. Recovery still has to
-- check process liveness (pid + /proc/<pid>/stat start ticks) before starting a
-- new attempt; see probes/p04_lease/lease_probe.py.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.lease_tokens (
    token                text PRIMARY KEY,
    worker_id            text NOT NULL,
    run_id               text NOT NULL,
    attempt              integer NOT NULL CHECK (attempt > 0),
    process_pid          integer CHECK (process_pid IS NULL OR process_pid > 0),
    process_start_ticks  bigint,
    issued_at            timestamptz NOT NULL DEFAULT clock_timestamp(),
    superseded_at        timestamptz,
    superseded_by        text,
    UNIQUE (run_id, attempt)
);

-- The database, not the supervisor, guarantees a run has one live credential.
CREATE UNIQUE INDEX IF NOT EXISTS lease_tokens_one_active_per_run
    ON app.lease_tokens (run_id)
    WHERE superseded_at IS NULL;

CREATE INDEX IF NOT EXISTS lease_tokens_by_run
    ON app.lease_tokens (run_id, issued_at);

COMMENT ON TABLE app.lease_tokens IS
    'Fencing tokens per worker attempt. NULL superseded_at means the token is still valid.';

COMMENT ON COLUMN app.lease_tokens.superseded_at IS
    'Set exactly once by app.supersede_token(); the old token is rejected from then on.';

COMMENT ON COLUMN app.lease_tokens.superseded_by IS
    'Token that took the lease over. Kept as plain text so the hand-off stays auditable.';

COMMENT ON COLUMN app.lease_tokens.process_start_ticks IS
    'Field 22 of /proc/<pid>/stat when the attempt started. Comparing it later distinguishes "still running" from "the pid was reused".';

-- Capability calls: 'ok' allows the call, 'token_superseded' and
-- 'token_unknown' reject it. Returning the reason (instead of a bare boolean)
-- keeps the rejection auditable.
CREATE OR REPLACE FUNCTION app.capability_status(p_token text)
RETURNS text
LANGUAGE sql
STABLE
AS $$
    SELECT CASE
        WHEN t.token IS NULL THEN 'token_unknown'
        WHEN t.superseded_at IS NOT NULL THEN 'token_superseded'
        ELSE 'ok'
    END
    FROM (VALUES (1)) AS probe(n)
    LEFT JOIN app.lease_tokens AS t ON t.token = p_token;
$$;

COMMENT ON FUNCTION app.capability_status(text) IS
    'ok | token_superseded | token_unknown. Enforcement helper for capability calls.';

-- First attempt for a run. If another active token already exists, the partial
-- unique index rejects the insert and the race is reported as won = false
-- instead of leaving two live credentials behind.
CREATE OR REPLACE FUNCTION app.issue_token(
    p_worker_id text,
    p_run_id text,
    p_process_pid integer DEFAULT NULL,
    p_process_start_ticks bigint DEFAULT NULL
)
RETURNS TABLE (won boolean, new_token text, run_id text, attempt integer)
LANGUAGE plpgsql
AS $$
DECLARE
    v_new_token text := gen_random_uuid()::text;
    v_attempt integer;
BEGIN
    SELECT coalesce(max(t.attempt), 0) + 1
      INTO v_attempt
      FROM app.lease_tokens AS t
     WHERE t.run_id = p_run_id;

    BEGIN
        INSERT INTO app.lease_tokens
            (token, worker_id, run_id, attempt, process_pid, process_start_ticks)
        VALUES
            (v_new_token, p_worker_id, p_run_id, v_attempt, p_process_pid, p_process_start_ticks);
    EXCEPTION WHEN unique_violation THEN
        -- Another attempt became active first. Nothing was written by this
        -- function: the failed INSERT is the only change in this block.
        RETURN QUERY SELECT false, NULL::text, p_run_id, NULL::integer;
        RETURN;
    END;

    RETURN QUERY SELECT true, v_new_token, p_run_id, v_attempt;
END;
$$;

COMMENT ON FUNCTION app.issue_token(text, text, integer, bigint) IS
    'Issue the first token for a run; won = false when another attempt already holds the lease.';

-- Atomic hand-off. The single UPDATE is the compare-and-swap: of two callers
-- presenting the same old token, exactly one matches a not-yet-superseded row.
-- The winner inserts the new token in the same transaction, so a loser cannot
-- leave a second active credential behind.
CREATE OR REPLACE FUNCTION app.supersede_token(
    p_old_token text,
    p_worker_id text,
    p_process_pid integer DEFAULT NULL,
    p_process_start_ticks bigint DEFAULT NULL
)
RETURNS TABLE (won boolean, new_token text, run_id text, attempt integer)
LANGUAGE plpgsql
AS $$
DECLARE
    v_new_token text := gen_random_uuid()::text;
    v_run_id text;
    v_next_attempt integer;
BEGIN
    UPDATE app.lease_tokens AS t
       SET superseded_at = clock_timestamp(),
           superseded_by = v_new_token
     WHERE t.token = p_old_token
       AND t.superseded_at IS NULL
    RETURNING t.run_id, t.attempt + 1
      INTO v_run_id, v_next_attempt;

    IF NOT FOUND THEN
        -- Already superseded, or never existed: this attempt lost the race and
        -- must not receive a token.
        RETURN QUERY SELECT false, NULL::text, NULL::text, NULL::integer;
        RETURN;
    END IF;

    INSERT INTO app.lease_tokens
        (token, worker_id, run_id, attempt, process_pid, process_start_ticks)
    VALUES
        (v_new_token, p_worker_id, v_run_id, v_next_attempt, p_process_pid, p_process_start_ticks);

    RETURN QUERY SELECT true, v_new_token, v_run_id, v_next_attempt;
END;
$$;

COMMENT ON FUNCTION app.supersede_token(text, text, integer, bigint) IS
    'Atomically supersede one token and issue the next attempt token; won = false means another attempt won.';
