-- F09/T054: personal long-term memory candidates and the memories they confirm.
--
-- Baseline: `contracts/database.sql` already fixes both tables this slice needs
-- (`memory_candidates`, `memories`) plus the deferred `memory_embeddings`. This
-- migration creates the two tables with the deliberate deviations listed below.
-- Each deviation names its owning ticket; nothing here changes a contract column
-- meaning.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `memory_candidates.logical_key` (NOT NULL, bounded) plus
--      UNIQUE (source_run_id, logical_key). The contract's
--      `proposeMemoryCandidate` body requires `logical_key`; a retried proposal
--      for the same run and logical key must return the one candidate instead of
--      writing a second row or a second sealed object. Owner: F09/T054.
--   2. `memory_candidates.created_at` / `decided_at` / `decided_by` /
--      `decision_key`. The owner-visible list is ordered by creation time, and a
--      decision records who decided when and the Idempotency-Key value that
--      committed it, so a replay can be answered without trusting request text.
--      `decided_by` references `platform.users` for the same human-only reason
--      as `owner_id`. Owner: F09/T054.
--   3. `memories.confirmed_by` (NOT NULL, references `platform.users`). T054
--      requires the confirmer to be recorded and the contract table has no such
--      column. It is always the memory owner today (only the owner can confirm);
--      the column keeps that fact auditable instead of implied. Owner: F09/T054;
--      T058 (#59) owns memory update/delete and must keep it consistent.
--   4. No `memories.source_run_id`. Provenance is the single chain
--      `memories.candidate_id -> memory_candidates.source_run_id`; a direct
--      `createMemory` has no run source (NULL candidate). A second source column
--      would be a second provenance authority that can disagree (the same
--      argument 0026 recorded). Owner: F09/T054.
--   5. `platform.memory_embeddings` is NOT created here. Runtime recall and its
--      index belong to F09/T063 (#64); this PostgreSQL 16 cluster has no vector
--      extension (0009 records the same boundary), so creating a table whose only
--      column the platform cannot write would be a dead schema. Owner: F09/T063.
--
-- Deliberate boundaries:
--   * A candidate is never recallable. Only `platform.memories` rows are a
--     recall source (T063); nothing in this ticket reads a candidate body for
--     context, and the list/decision endpoints are the only readers.
--   * One memory per candidate is structural: UNIQUE (candidate_id). A repeated
--     confirm finds the existing row instead of writing a second memory or a
--     second sealed artifact.
--   * `owner_id` / `confirmed_by` reference `platform.users`, not
--     `platform.principals`: a service principal has no `users` row, so the
--     database itself refuses to make a service account the owner or confirmer
--     of personal memory even if a future writer forgot the API refusal.
--   * `memories.revision` starts at 1. T058 (#59) owns the strong ETag +
--     revision update path and the delete cache-epoch bump; T054 only fixes the
--     starting value so those additions need no backfill.
--   * No deletion model: F07 owns purge. `memories.deleted_at` is carried
--     (contract) but unused here.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007-0032: the worker goes through the internal mTLS endpoints.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.memory_candidates (
    id                uuid PRIMARY KEY,
    owner_id          uuid NOT NULL REFERENCES platform.users (principal_id),
    application_id    uuid NOT NULL REFERENCES platform.applications (id),
    body_artifact_id  uuid NOT NULL REFERENCES platform.artifacts (id),
    source_run_id     uuid REFERENCES platform.runs (id),
    logical_key       text NOT NULL,
    state             text NOT NULL DEFAULT 'pending'
                          CHECK (state IN ('pending', 'confirmed', 'rejected', 'expired')),
    expires_at        timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    decided_at        timestamptz,
    decided_by        uuid REFERENCES platform.users (principal_id),
    decision_key      text,
    CONSTRAINT memory_candidates_logical_key_check
        CHECK (length(btrim(logical_key)) > 0 AND length(logical_key) <= 200),
    CONSTRAINT memory_candidates_decision_key_check
        CHECK (decision_key IS NULL OR (length(btrim(decision_key)) > 0
                                        AND length(decision_key) <= 200)),
    -- One candidate per (run, logical key): the retry of a proposal is the same
    -- intention, never a second candidate.
    CONSTRAINT memory_candidates_run_logical_key UNIQUE (source_run_id, logical_key)
);

-- The owner list is newest-first with a (created_at, id) keyset cursor.
CREATE INDEX IF NOT EXISTS memory_candidates_owner_recent
    ON platform.memory_candidates (owner_id, created_at DESC, id DESC);
-- The optional application scope filter.
CREATE INDEX IF NOT EXISTS memory_candidates_scope
    ON platform.memory_candidates (owner_id, application_id);

CREATE TABLE IF NOT EXISTS platform.memories (
    id                uuid PRIMARY KEY,
    owner_id          uuid NOT NULL REFERENCES platform.users (principal_id),
    application_id    uuid NOT NULL REFERENCES platform.applications (id),
    body_artifact_id  uuid NOT NULL REFERENCES platform.artifacts (id),
    candidate_id      uuid UNIQUE REFERENCES platform.memory_candidates (id),
    revision          bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    confirmed_at      timestamptz NOT NULL,
    confirmed_by      uuid NOT NULL REFERENCES platform.users (principal_id),
    deleted_at        timestamptz
);

-- The contract index for the active scope read.
CREATE INDEX IF NOT EXISTS memories_scope
    ON platform.memories (owner_id, application_id) WHERE deleted_at IS NULL;
-- The owner list is newest-first with a (confirmed_at, id) keyset cursor.
CREATE INDEX IF NOT EXISTS memories_owner_recent
    ON platform.memories (owner_id, confirmed_at DESC, id DESC) WHERE deleted_at IS NULL;

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

COMMENT ON TABLE platform.memory_candidates IS
    'F09/T054 memory candidate (contracts/database.sql): a proposed personal fact whose body is a sealed artifact. Never a recall source; only a confirmed candidate creates one platform.memories row.';
COMMENT ON COLUMN platform.memory_candidates.state IS
    'pending|confirmed|rejected|expired. Expiry is also enforced by the server against expires_at, so a pending row past its deadline is reported and decided as expired.';
COMMENT ON COLUMN platform.memory_candidates.logical_key IS
    'Stable worker-side intention key within one run; a retried propose with the same run and key returns the same candidate.';
COMMENT ON TABLE platform.memories IS
    'F09/T054 confirmed personal long-term memory (contracts/database.sql): one row per confirmed candidate (UNIQUE candidate_id), body in a sealed artifact, revision fixed at 1 until T058 adds the ETag/update path.';
COMMENT ON COLUMN platform.memories.confirmed_by IS
    'F09/T054 deviation: the human who confirmed the candidate (always the owner today). References platform.users so a service principal can never confirm personal memory.';
