-- P04/T016: drain evidence for a run before it may be resumed.
--
-- A run may only be retried once the old process is gone, its sandbox is gone,
-- and its database writes have drained. This table is the write-side ledger the
-- runtime uses: rows exist while a write is in flight, and the count reaching
-- zero is the evidence the gate looks for.

CREATE SCHEMA IF NOT EXISTS app;

CREATE TABLE IF NOT EXISTS app.pending_writes (
    run_id     text NOT NULL,
    sequence   bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, sequence)
);

COMMENT ON TABLE app.pending_writes IS
    'In-flight write markers. A drained run has zero rows; the recovery gate refuses to resume otherwise.';
