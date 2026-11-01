-- F00 skeleton: worker registration, run state and recovery hand-off.
--
-- The supervisor exists so that a dead worker's runs are moved to
-- `recovery_pending` instead of being silently re-executed by a second
-- executor. These tables are the minimum that decision needs; the full
-- execution model arrives with the worker/execution tickets.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.workers (
    worker_id    text PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    stopped_at   timestamptz
);

CREATE TABLE IF NOT EXISTS app.runs (
    run_id       text PRIMARY KEY,
    state        text NOT NULL
                 CHECK (state IN ('running', 'recovery_pending', 'succeeded', 'failed')),
    worker_id    text REFERENCES app.workers (worker_id),
    heartbeat_at timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS runs_by_worker_state ON app.runs (worker_id, state);

COMMENT ON COLUMN app.runs.state IS
    'recovery_pending means "do not re-run until the old process and its writes are proven drained", not "retry".';
