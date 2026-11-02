-- F03/T032: the durable per-Run event timeline.
--
-- Baseline: contracts/database.sql defines `run_events` (`run_id` FK, `seq>0`,
-- globally unique `event_id`, `type`, `revision`, `payload` jsonb, `created_at`,
-- PK `(run_id, seq)`). contracts/event.schema.json is the authoritative envelope
-- and per-type payload allowlist; examples/event.json is the canonical example.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. The event `type` vocabulary is closed at the contract's five values
--      (`run.status`, `node.status`, `approval.required`, `artifact.ready`,
--      `usage.updated`). The baseline only says `text NOT NULL`; a writer that
--      invents a type would make the SSE client's frame contract unknowable.
--      A later type extends this CHECK in its own migration.
--   2. `payload` must be a JSON object and `revision` must be positive. The
--      event schema requires both; the constraints keep a scalar/zero revision
--      from becoming an unreadable durable fact.
--   3. `run_events_replay (run_id, seq)` names the replay access path explicitly.
--      The primary key already orders `(run_id, seq)`, but the named index is
--      kept as the documented replay contract and survives any future PK change.
--   4. `run_events_created_at (created_at)` supports the retention policy (the
--      API's documented window; F07 owns actual purge/retention deletion). This
--      migration does not delete any event: a visible timeline never silently
--      loses history.
--
-- Deliberate boundaries:
--   * No request/conversation, node_attempts, operations, approvals or usage
--     tables. Node events are `node.status` rows written by the worker endpoint;
--     their node identity is the payload's `node_id`/`attempt_no` until T033+
--     introduce node attempt tables.
--   * No event row is ever written outside a business transaction. Controller
--     transitions append `run.status` in the same transaction as the transition;
--     the event helper in internal/execution/events.go is the only writer.
--   * No body/content in an event payload. The per-type allowlists are enforced
--     in Go before the row is written (`run.status` = state + optional
--     error_code; `node.status` = node_id/state/attempt_no exactly).
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007–0017. The worker submits events over mTLS; it never reads/writes the
--     table directly.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.run_events (
    run_id     uuid NOT NULL REFERENCES platform.runs (id),
    seq        bigint NOT NULL CHECK (seq > 0),
    event_id   uuid NOT NULL UNIQUE,
    type       text NOT NULL,
    revision   bigint NOT NULL CHECK (revision > 0),
    payload    jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq),
    -- Deviation 1: the contract's closed event vocabulary.
    CONSTRAINT run_events_type_check CHECK (type IN (
        'run.status', 'node.status', 'approval.required', 'artifact.ready', 'usage.updated'
    )),
    -- Deviation 2: the envelope's object payload.
    CONSTRAINT run_events_payload_object_check CHECK (jsonb_typeof(payload) = 'object')
);

-- Deviation 3: the explicit replay path (also covered by the primary key).
CREATE INDEX IF NOT EXISTS run_events_replay
    ON platform.run_events (run_id, seq);

-- Deviation 4: the retention window's age scan (T032 documents the window; the
-- deleting owner is F07).
CREATE INDEX IF NOT EXISTS run_events_created_at
    ON platform.run_events (created_at);

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

COMMENT ON TABLE platform.run_events IS
    'Durable per-Run event timeline (contracts/database.sql + contracts/event.schema.json). seq is run-local, allocated under the runs row lock from runs.next_event_seq in the same transaction as the business transition; event_id is globally idempotent. The SSE replay is ordered by (run_id, seq).';
COMMENT ON COLUMN platform.run_events.seq IS
    'Run-local monotonic display order. Assigned by the server (never the worker) while holding the Run row lock; a duplicate event_id does not consume a seq.';
COMMENT ON COLUMN platform.run_events.event_id IS
    'Globally unique idempotency identity. Same id + same type/payload is a no-op; same id + different content is a 409 event_conflict.';
COMMENT ON COLUMN platform.run_events.payload IS
    'Metadata only, per contracts/event.schema.json. run.status = {state, error_code?}; node.status = {node_id, state, attempt_no}. Run content/body is never stored here.';
COMMENT ON COLUMN platform.run_events.created_at IS
    'Database commit-time proxy for the retention window. T032 reads it to answer 410 event_cursor_expired; F07 owns retention deletion.';
