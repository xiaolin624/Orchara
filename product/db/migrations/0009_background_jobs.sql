-- F00B/T022: durable background jobs and the artifacts they finalize.
--
-- Baseline: contracts/database.sql defines `artifacts` and `background_jobs`
-- (and `outbox`, which this ticket does not use: Redis would only wake, and the
-- wakeup bus is a no-op in this batch). Every column that is not in the contract
-- baseline is listed here with its reason; a later ticket that needs another
-- column adds it in its own migration instead of extending 0009.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `artifacts.revision bigint NOT NULL DEFAULT 1` — the contract's
--      `background_jobs.expected_revision` has no source column otherwise. The
--      revision is the artifact's source version: it is bumped by this batch on
--      every state change (scanning -> ready/quarantined/deleted). A finalize
--      job carries the revision it was created against, so a result produced
--      after a tombstone or a concurrent state change can never activate.
--      Contract owner of the follow-up: T024 creates artifacts (completeUpload)
--      and must insert revision = 1; later tickets must bump it in the same
--      transaction as the state change.
--   2. `background_jobs.finished_at`, `failure_class`, `last_error_at` — the
--      completion bookkeeping needed by the T022 controller. `failure_class`
--      separates a business failure (never retried) from an infrastructure
--      failure (retried at most three times, then failed); `finished_at` is the
--      terminal timestamp; `last_error_at` records when the last accepted
--      failure was applied. database.sql has none of these.
--   3. Index shape. The contract's `background_jobs_ready (available_at, id)
--      WHERE state = 'ready'` is kept verbatim because it is exactly the claim
--      scan (SKIP LOCKED, oldest available first). Two additional indexes serve
--      T022 behaviour the contract index cannot: lease-expiry recovery
--      (partial on state='leased') and owner diagnostics.
--   4. `background_jobs.kind` keeps the contract's eight fixed kinds and its
--      CHECK. The Go whitelist in internal/background must stay in sync with
--      this list; an unknown kind is refused by both the database and the
--      controller. The maintenance registry compiles handlers for a subset
--      (T022: artifact_finalize only); a job of another whitelisted kind stays
--      `ready` until the ticket that registers its handler lands.
--
-- Deliberate boundaries:
--   * `checkpoint` is out of band. langgraph-checkpoint-postgres owns that
--     schema; platform migrations never create or alter it.
--   * No vector columns: pgvector is not available in this cluster.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `platform.applications` is untouched here; its T019 read model already
--     exists and F02A owns any change to it.
--   * `orchara_worker` gets no USAGE on this schema and no table privileges, so
--     the runtime role can neither read the job queue nor forge a result. The
--     internal mTLS API is the only door a worker/maintenance process has; it
--     does not use the PostgreSQL role.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.artifacts (
    id                  uuid PRIMARY KEY,
    owner_id            uuid NOT NULL REFERENCES platform.principals (id),
    object_key          text NOT NULL UNIQUE,
    staging_object_key  text UNIQUE,
    expected_sha256     text,
    purpose             text NOT NULL,
    sha256              text,
    media_type          text NOT NULL,
    size_bytes          bigint CHECK (size_bytes >= 0),
    state               text NOT NULL CHECK (state IN ('uploading', 'scanning', 'ready', 'quarantined', 'deleted')),
    -- Deviation 1: source version for background_jobs.expected_revision.
    revision            bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at          timestamptz NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    purged_at           timestamptz
);

CREATE TABLE IF NOT EXISTS platform.background_jobs (
    id                 uuid PRIMARY KEY,
    kind               text NOT NULL CHECK (kind IN (
                           'artifact_finalize', 'route_request', 'tool_build', 'document_ingest',
                           'application_evaluate', 'model_validate', 'delete_content', 'catalog_index'
                       )),
    target_id          uuid NOT NULL,
    owner_id           uuid NOT NULL REFERENCES platform.principals (id),
    expected_revision  bigint,
    expected_sha256    text,
    dedup_key          text NOT NULL CHECK (length(btrim(dedup_key)) > 0),
    input_artifact_id  uuid REFERENCES platform.artifacts (id),
    result_artifact_id uuid REFERENCES platform.artifacts (id),
    state              text NOT NULL CHECK (state IN ('ready', 'leased', 'succeeded', 'failed', 'cancelled')),
    available_at       timestamptz NOT NULL DEFAULT now(),
    lease_token        uuid,
    lease_deadline     timestamptz,
    worker_id          text,
    attempt_count      integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    result_event_id    uuid UNIQUE,
    -- Deviation 2: the SHA-256 of the request that produced result_event_id, so
    -- the same event id with different content is a 409 and the same request is
    -- an idempotent replay even when the guard overrode the reported state.
    result_event_digest text CHECK (result_event_digest IS NULL OR length(result_event_digest) = 64),
    error_code         text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    -- Deviation 2: completion bookkeeping database.sql does not carry.
    finished_at        timestamptz,
    failure_class      text CHECK (failure_class IN ('business', 'infrastructure')),
    last_error_at      timestamptz,
    CONSTRAINT background_jobs_kind_dedup UNIQUE (kind, dedup_key),
    -- A terminal job always carries its completion time and a non-terminal one
    -- never does; a row that disagrees is a bug in the controller, not data.
    CONSTRAINT background_jobs_finished_matches_state CHECK (
        (state IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
        OR (state IN ('ready', 'leased') AND finished_at IS NULL)
    )
);

-- The claim scan: oldest ready job first, SKIP LOCKED. Kept from the contract.
CREATE INDEX IF NOT EXISTS background_jobs_ready
    ON platform.background_jobs (available_at, id) WHERE state = 'ready';

-- Lease-expiry recovery scans only leased rows; the partial index keeps it from
-- walking the whole history as the queue grows.
CREATE INDEX IF NOT EXISTS background_jobs_lease_expiry
    ON platform.background_jobs (lease_deadline) WHERE state = 'leased';

-- Owner-or-admin diagnostics read one owner's jobs; the primary key serves the
-- single-job lookup.
CREATE INDEX IF NOT EXISTS background_jobs_owner
    ON platform.background_jobs (owner_id, created_at DESC);

-- Privileges. New tables inherit the 0006-0008 boundary: the runtime role cannot
-- read, modify or create anything in `platform`, and PUBLIC has nothing.
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

COMMENT ON TABLE platform.artifacts IS
    'Objects whose bytes live in the object store. staging_object_key is written once by an uploader through a signed staging key; object_key is the private sealed key that only the server writes/reads. A finalize job may only turn state=scanning into ready; no other path does.';
COMMENT ON COLUMN platform.artifacts.revision IS
    'Source version (deviation from database.sql, added by T022). Bumped on every state change, so background_jobs.expected_revision can detect a result produced against a stale artifact.';
COMMENT ON COLUMN platform.artifacts.expected_sha256 IS
    'Hash the uploader declared. It is compared with the bytes actually read, never trusted as the stored sha256.';
COMMENT ON TABLE platform.background_jobs IS
    'Fixed-kind background work. kind is a closed whitelist enforced here and in internal/background; no request string ever names a handler. Completion is guarded by state=leased + lease_token + an unexpired lease, and a result event id is terminal and idempotent.';
COMMENT ON COLUMN platform.background_jobs.failure_class IS
    'business = never retried (validation/tombstone/hash mismatch); infrastructure = retried with exponential backoff up to three attempts, then failed. NULL means no failure has been classified yet.';
COMMENT ON COLUMN platform.background_jobs.finished_at IS
    'Terminal timestamp (deviation from database.sql, added by T022). Non-terminal rows must have NULL and terminal rows must have it, enforced by a CHECK.';
COMMENT ON COLUMN platform.background_jobs.result_event_id IS
    'The result event that moved the job to a terminal state. Its UNIQUE constraint is what makes a replay idempotent and a second, different terminal event a conflict.';
COMMENT ON COLUMN platform.background_jobs.expected_revision IS
    'Source version the job was created against (artifacts.revision for artifact_finalize). The completion guard refuses to activate a result whose source moved on.';
