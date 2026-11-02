-- F02A/T027: execution snapshots, debug runs, their start job and the
-- idempotency record that makes a debug submission replayable.
--
-- Baseline: contracts/database.sql defines `execution_snapshots`, `runs`,
-- `jobs`, `run_artifacts` and `idempotency_keys`. This migration creates those
-- five tables, completes the one foreign key T026 deferred
-- (`evaluation_jobs_snapshot_fkey`) and nothing else. It is the first migration
-- that creates execution identity; nothing here is a published version.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `runs.request_id` and `runs.conversation_id` are plain uuid columns with
--      no foreign key. `platform.requests`/`platform.conversations` belong to
--      F05/F07 and do not exist in this batch. The contract's UNIQUE(request_id)
--      is kept (it is meaningful once the FK exists); the contract CHECK that a
--      published top-level run carries request+conversation is kept verbatim.
--      F05/F07 add `runs_request_id_fkey`/`runs_conversation_id_fkey` in their
--      own migration after creating those tables. Until then no code may treat
--      either column as a verified reference; a debug run has both NULL.
--   2. `jobs.current_attempt_token` and `run_artifacts.attempt_token` are plain
--      uuid columns with no foreign key. `platform.run_attempts` belongs to F03
--      (execution) and does not exist here. The contract adds the two FKs after
--      creating run_attempts; F03 must add
--      `jobs_current_attempt_token_fkey (current_attempt_token) ->
--      run_attempts(token)` and
--      `run_artifacts_attempt_token_fkey (attempt_token) -> run_attempts(token)`.
--      Until then a NULL token is the only value this batch writes.
--   3. `platform.idempotency_keys.response_json jsonb` is added by T027. The
--      contract's table stores only `response_code`/`response_artifact_id`, so
--      there is nowhere to replay the exact response body that `debugDraft` must
--      return on a repeated key (API.md §1: "保存规范请求哈希和响应"). A debug
--      replay therefore returns the stored 202 Run body without creating a new
--      row; F05/F06 inherit the column for their own create operations. A
--      committed row always carries both response_code and response_json.
--   4. Tightenings that the contract does not spell out but that every writer in
--      this batch satisfies: `execution_snapshots.manifest_sha256`,
--      `runs.manifest_sha256` and `idempotency_keys.request_sha256` are CHECKed
--      to 64 characters (the same rule 0013 applies to report hashes);
--      `execution_snapshots.manifest` is a jsonb object (the queryable
--      projection, never the canonical bytes); `execution_snapshots.draft_revision`
--      is positive when present; `idempotency_keys.key` is non-blank;
--      `run_artifacts.run_id`/`artifact_id` are NOT NULL because they are the
--      primary key (the contract's two inline columns are nullable on their own,
--      which the PK would reject anyway).
--
-- Deliberate boundaries:
--   * No worker/attempt/operation/approval/event/quota tables: F03 owns the
--     execution loop (`run_attempts`, `node_attempts`, `run_events`,
--     `operations`, `approvals`, `usage_ledger`, `quota_*`). This migration only
--     creates the queued `runs` row and its `ready` start `jobs` row.
--   * No `requests`/`conversations` rows or tables (F05/F07); debug runs do not
--     create a request or conversation, which is exactly the contract's
--     `kind='debug'` CHECK.
--   * No published snapshots/versions: `kind='published'` is representable (the
--     contract CHECK is kept) but F02B owns writing it.
--   * `platform.execution_snapshots` is reached through the API only; the
--     runtime role `orchara_worker` gets no schema USAGE and no table/sequence
--     privileges, exactly like 0007–0013.
--   * Every statement is schema-qualified; nothing relies on search_path.

CREATE SCHEMA IF NOT EXISTS platform;

-- One frozen execution identity. A debug snapshot belongs to one draft revision
-- of one application and references the sealed canonical manifest artifact;
-- `manifest` is only a queryable JSONB projection of those bytes.
CREATE TABLE IF NOT EXISTS platform.execution_snapshots (
    id                     uuid PRIMARY KEY,
    kind                   text NOT NULL CHECK (kind IN ('published', 'debug')),
    application_id         uuid NOT NULL REFERENCES platform.applications (id),
    application_version_id uuid REFERENCES platform.application_versions (id),
    draft_revision         bigint,
    owner_id               uuid NOT NULL REFERENCES platform.principals (id),
    manifest               jsonb NOT NULL,
    manifest_sha256        text NOT NULL CHECK (length(manifest_sha256) = 64),
    manifest_artifact_id   uuid NOT NULL REFERENCES platform.artifacts (id),
    compiler_version       text NOT NULL,
    worker_image_digest    text NOT NULL,
    state_schema_version   text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    -- Deviation 4: the projection is always an object and a debug revision is
    -- always positive.
    CHECK (jsonb_typeof(manifest) = 'object'),
    CHECK (draft_revision IS NULL OR draft_revision > 0),
    -- Contract CHECK: exactly one identity, chosen by kind.
    CHECK ((kind = 'published' AND application_version_id IS NOT NULL AND draft_revision IS NULL)
        OR (kind = 'debug' AND application_version_id IS NULL AND draft_revision IS NOT NULL))
);

-- F02B/F03 read the snapshots of one application (published/debug history).
CREATE INDEX IF NOT EXISTS execution_snapshots_application
    ON platform.execution_snapshots (application_id, created_at DESC);

-- One queued (then executing) run. A debug run has no request/conversation, no
-- application version and no parent; the contract's CHECKs say exactly that.
CREATE TABLE IF NOT EXISTS platform.runs (
    id                    uuid PRIMARY KEY,
    -- Deviation 1: no FK until F05/F07 create requests/conversations.
    request_id            uuid UNIQUE,
    conversation_id       uuid,
    parent_run_id         uuid REFERENCES platform.runs (id),
    parent_invocation_key text,
    owner_id              uuid NOT NULL REFERENCES platform.principals (id),
    kind                  text NOT NULL CHECK (kind IN ('published', 'debug')),
    execution_snapshot_id uuid NOT NULL REFERENCES platform.execution_snapshots (id),
    application_version_id uuid REFERENCES platform.application_versions (id),
    manifest_sha256       text NOT NULL CHECK (length(manifest_sha256) = 64),
    input_artifact_id     uuid NOT NULL REFERENCES platform.artifacts (id),
    output_artifact_id    uuid REFERENCES platform.artifacts (id),
    checkpoint_thread_id  uuid NOT NULL UNIQUE,
    state                 text NOT NULL CHECK (state IN (
                              'queued', 'running', 'waiting_approval', 'waiting_children',
                              'cancelling', 'recovery_pending', 'needs_reconciliation',
                              'succeeded', 'failed', 'cancelled'
                          )),
    revision              bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    next_event_seq        bigint NOT NULL DEFAULT 1 CHECK (next_event_seq > 0),
    budget_limit          numeric(20,8) NOT NULL CHECK (budget_limit >= 0),
    currency              text NOT NULL,
    phase                 text NOT NULL DEFAULT 'idle'
                              CHECK (phase IN ('idle', 'executing', 'waiting', 'finalizing')),
    active_elapsed_ms     bigint NOT NULL DEFAULT 0 CHECK (active_elapsed_ms >= 0),
    cancel_requested_at   timestamptz,
    error_code            text,
    started_at            timestamptz,
    finished_at           timestamptz,
    deleted_at            timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- Contract CHECKs, verbatim.
    CHECK (kind = 'debug' OR parent_run_id IS NOT NULL
        OR (request_id IS NOT NULL AND conversation_id IS NOT NULL)),
    CHECK ((kind = 'published' AND application_version_id IS NOT NULL)
        OR (kind = 'debug' AND application_version_id IS NULL
            AND parent_run_id IS NULL AND request_id IS NULL)),
    CHECK (parent_run_id IS NULL OR (request_id IS NULL AND parent_invocation_key IS NOT NULL)),
    UNIQUE (parent_run_id, parent_invocation_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_top_run_per_conversation
    ON platform.runs (conversation_id)
    WHERE parent_run_id IS NULL AND state NOT IN ('succeeded', 'failed', 'cancelled');
CREATE INDEX IF NOT EXISTS runs_owner_time ON platform.runs (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS runs_parent ON platform.runs (parent_run_id);

-- One start job per debug run. The claim/lease protocol is F03's; T027 only
-- writes `kind='start', state='ready'`. `current_attempt_token` has no FK until
-- F03 creates run_attempts (deviation 2).
CREATE TABLE IF NOT EXISTS platform.jobs (
    id                    uuid PRIMARY KEY,
    run_id                uuid NOT NULL REFERENCES platform.runs (id),
    kind                  text NOT NULL CHECK (kind IN ('start', 'resume')),
    state                 text NOT NULL CHECK (state IN ('ready', 'leased', 'held', 'completed', 'void')),
    available_at          timestamptz NOT NULL DEFAULT now(),
    current_attempt_token uuid,
    resume_artifact_id    uuid REFERENCES platform.artifacts (id),
    created_at            timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS one_live_job_per_run
    ON platform.jobs (run_id) WHERE state IN ('ready', 'leased', 'held');
CREATE INDEX IF NOT EXISTS jobs_ready
    ON platform.jobs (available_at, id) WHERE state = 'ready';

-- Run-scoped artifact links. T027 links the canonical test input and every
-- caller-supplied input; F03 adds output/attempt relations. `attempt_token` has
-- no FK until F03 creates run_attempts (deviation 2).
CREATE TABLE IF NOT EXISTS platform.run_artifacts (
    run_id        uuid NOT NULL REFERENCES platform.runs (id),
    artifact_id   uuid NOT NULL REFERENCES platform.artifacts (id),
    attempt_token uuid,
    relation      text NOT NULL,
    PRIMARY KEY (run_id, artifact_id)
);

-- Idempotency receipts. The primary key is the contract's
-- (principal, method, path, key); request_sha256 is the canonical request digest
-- and response_json the exact response body (deviation 3) so a replay returns
-- the same 202 Run without creating a second execution identity.
CREATE TABLE IF NOT EXISTS platform.idempotency_keys (
    principal_id         uuid NOT NULL REFERENCES platform.principals (id),
    method               text NOT NULL,
    path                 text NOT NULL,
    key                  text NOT NULL CHECK (length(btrim(key)) > 0),
    request_sha256       text NOT NULL CHECK (length(request_sha256) = 64),
    response_code        integer,
    response_artifact_id uuid REFERENCES platform.artifacts (id),
    -- Deviation 3: T027's replayable response body; F05/F06 inherit it.
    response_json        jsonb CHECK (response_json IS NULL OR jsonb_typeof(response_json) = 'object'),
    expires_at           timestamptz NOT NULL,
    PRIMARY KEY (principal_id, method, path, key)
);

-- F07 owns key cleanup; the index makes "which receipts expired" cheap.
CREATE INDEX IF NOT EXISTS idempotency_keys_expiry
    ON platform.idempotency_keys (expires_at);

-- Deferred FK from 0013: evaluation_jobs.snapshot_id becomes a real reference
-- now that execution_snapshots exists. ADD CONSTRAINT has no IF NOT EXISTS, so
-- the guard keeps the migration idempotent.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM pg_constraint
         WHERE conname = 'evaluation_jobs_snapshot_fkey'
           AND conrelid = 'platform.evaluation_jobs'::regclass
    ) THEN
        ALTER TABLE platform.evaluation_jobs
            ADD CONSTRAINT evaluation_jobs_snapshot_fkey
            FOREIGN KEY (snapshot_id) REFERENCES platform.execution_snapshots (id);
    END IF;
END
$$;

-- Privileges: identical boundary to 0007–0013. New tables inherit it because
-- `orchara_worker` has no USAGE on the schema either.
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

COMMENT ON TABLE platform.execution_snapshots IS
    'One frozen execution identity (contracts/database.sql). A debug snapshot binds one draft revision, its owner, the sealed canonical manifest artifact and the compiler/worker/state-schema identity. manifest is a queryable JSONB projection only; the hash covers the JCS bytes in manifest_artifact_id, never this projection. Written only by debugDraft (T027) and, later, publication review (F02B).';
COMMENT ON COLUMN platform.execution_snapshots.draft_revision IS
    'The application_drafts.revision the debug submission froze (the ETag/CAS counter, not execution_revision). NOT NULL exactly for kind=debug; a newer draft revision never changes an existing snapshot.';
COMMENT ON COLUMN platform.execution_snapshots.manifest_artifact_id IS
    'The ready, sealed artifact holding the canonical JCS manifest bytes. Reused from platform.manifest_artifacts (content-addressed by application_id/execution_revision/manifest_sha256) so debug and publication hash the same bytes.';
COMMENT ON TABLE platform.runs IS
    'One run of a frozen snapshot (contracts/database.sql). T027 writes the queued debug run only: kind=debug, state=queued, phase=idle, no request/conversation/application_version/parent. F03 owns claiming, events, results and terminal states.';
COMMENT ON COLUMN platform.runs.request_id IS
    'Deferred FK (T027 decision, ADR 0004): platform.requests belongs to F05/F07; 0014 creates the column and the contract UNIQUE without the foreign key. Debug runs leave it NULL. F05/F07 must add runs_request_id_fkey after creating requests.';
COMMENT ON COLUMN platform.runs.conversation_id IS
    'Deferred FK (T027 decision, ADR 0004): platform.conversations belongs to F05/F07. Debug runs leave it NULL; F05/F07 must add runs_conversation_id_fkey.';
COMMENT ON COLUMN platform.runs.checkpoint_thread_id IS
    'Stable LangGraph checkpoint thread identity for this run (UNIQUE). The checkpoint schema itself is out of band and owned by F03.';
COMMENT ON COLUMN platform.runs.manifest_sha256 IS
    'The frozen snapshot manifest hash (same value as the snapshot row). Exposed by the API as the documented X-Manifest-SHA256 header because the contract Run schema has no field for it.';
COMMENT ON TABLE platform.jobs IS
    'The executable job for a run. T027 writes exactly one kind=start/state=ready row per debug run; claiming, leases, resume and run_attempts belong to F03.';
COMMENT ON COLUMN platform.jobs.current_attempt_token IS
    'Deferred FK (T027 decision): platform.run_attempts belongs to F03; 0014 creates the column without the foreign key. F03 must add jobs_current_attempt_token_fkey when run_attempts exists, and no code may treat the token as a verified reference before that.';
COMMENT ON TABLE platform.run_artifacts IS
    'Run-scoped artifact links (contracts/database.sql). T027 links the canonical test input and every caller-supplied input with relation=input; F03 adds attempt/output relations.';
COMMENT ON COLUMN platform.run_artifacts.attempt_token IS
    'Deferred FK (T027 decision): run_attempts belongs to F03. NULL for the inputs T027 links; F03 must add run_artifacts_attempt_token_fkey.';
COMMENT ON TABLE platform.idempotency_keys IS
    'Idempotency receipts keyed by principal+method+path+key (contracts/database.sql). T027 stores the canonical request digest and the exact 202 Run body (response_json, a documented deviation) in the same transaction that creates the snapshot/run/job; a replay returns the stored body and creates no new rows. Expiry is informational for F07 cleanup and never permits a second execution.';
COMMENT ON COLUMN platform.idempotency_keys.response_json IS
    'Deviation from database.sql added by T027: the exact response body stored with response_code so a replay is byte-identical and needs no recompilation. F05/F06 inherit this column for their create operations.';
COMMENT ON CONSTRAINT evaluation_jobs_snapshot_fkey ON platform.evaluation_jobs IS
    'Deferred FK completed by T027 (0014): an application-kind evaluation now references a real execution snapshot. The binding guard still requires the snapshot owner to equal the evaluation owner.';
