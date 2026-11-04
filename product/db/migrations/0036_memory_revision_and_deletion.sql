-- F09/T058: revising or deleting a memory stops the old content being recalled.
--
-- Baseline: revision/update/delete of a confirmed memory needs one thing the
-- contract already fixes and 0033 did not create — `deletion_jobs`. Everything
-- else this ticket changes is a write to columns 0033 already carries:
--
--   * `memories.revision` is the strong ETag and the CAS token (it already starts
--     at 1 from 0033, so no backfill is needed);
--   * `memories.deleted_at` is the tombstone (contract column, unused until now);
--   * `platform.policy_epochs(scope='memory:<owner>:<application>')` is the cache/
--     recall epoch the confirm path already raises; T058 raises it in the same
--     transaction as the revision change.
--
-- Deliberate deviations from contracts/database.sql: none in this migration. The
-- table below is the contract's `deletion_jobs` verbatim (it lives in the
-- "business invariants" half of the snapshot, so the platform creates it here).
--
-- Deliberate boundaries and the F07 debt, stated so no later reader mistakes this
-- slice for the whole deletion model:
--   1. This is the *minimal* deletion job T058 needs to answer
--      `DELETE /api/v1/memories/{id}` with a stable `DeletionAccepted.id`:
--      one row per (memory, deletion revision), state `pending`, and the
--      `remaining_scopes` that still have to be physically purged. The job is
--      never advanced here.
--   2. The asynchronous `delete_content` handler is NOT registered by T058. The
--      full deletion model — job claim/lease, retry, completion, failure, the
--      maintenance handler and the physical copy purge — belongs to F07
--      (deletion governance), which owns the `deletion_jobs` handler table. The
--      row therefore stays `pending`; the API reports that honest state and the
--      page does not claim the bytes are gone.
--   3. Physical cleanup is genuinely not done here: the memory's body object and
--      artifact row stay on disk, and a superseded body artifact left by an
--      update is likewise not purged. What T058 does guarantee, synchronously and
--      in the authoritative tables, is that the old content is no longer
--      recallable: the memory row is tombstoned (`deleted_at`), its `revision`
--      moved, and the memory-scope cache epoch was raised before the response.
--      F07 (and T063 for embeddings) must purge: the object named in
--      `remaining_scopes`, the `memory_body` artifact row, and any embedding row
--      keyed to a revision that is no longer current.
--   4. `remaining_scopes` is a JSON array of objects, one per physical copy:
--        {"kind":"object","object_key":...,"sha256":...,"size_bytes":...},
--        {"kind":"artifact","artifact_id":...},
--        {"kind":"embedding","memory_id":...,"memory_revision":...},
--        {"kind":"memory_row","memory_id":...,"revision":...}.
--      The contract only requires `jsonb_typeof = 'array'`; the shape is
--      documented here and in ADR 0009 §11 so a cleanup handler has an exact
--      contract without a second registry.
--   5. `owner_id` references `platform.principals` exactly as the contract says.
--      Every writer in this slice passes the memory owner, which is always a
--      `platform.users` row (0033 makes personal memory human-only), so the
--      generic reference is satisfied without weakening that boundary.
--   6. No contract column meaning is changed, and no table is renamed.
--   7. Every statement is schema-qualified; `orchara_worker`/PUBLIC get no
--      `platform` privileges, exactly like 0007-0033.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.deletion_jobs (
    id                uuid PRIMARY KEY,
    owner_id          uuid NOT NULL REFERENCES platform.principals (id),
    resource_type     text NOT NULL,
    resource_id       uuid NOT NULL,
    deletion_revision bigint NOT NULL,
    state             text NOT NULL
                          CHECK (state IN ('pending', 'running', 'completed', 'failed')),
    remaining_scopes  jsonb NOT NULL CHECK (jsonb_typeof(remaining_scopes) = 'array'),
    created_at        timestamptz NOT NULL DEFAULT now(),
    completed_at      timestamptz,
    -- A resource revision is deleted at most once: the repeated DELETE of the
    -- same memory finds this row instead of creating a second job or bumping the
    -- revision a second time.
    CONSTRAINT deletion_jobs_resource_revision
        UNIQUE (resource_type, resource_id, deletion_revision)
);

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

COMMENT ON TABLE platform.deletion_jobs IS
    'Deletion job (contracts/database.sql): one row per deleted resource revision. F09/T058 creates it for memory deletion only, with state=pending and the remaining physical scopes; the asynchronous delete_content handler, lease/retry/completion and the physical purge are F07 deletion-governance work, not implemented here.';
COMMENT ON COLUMN platform.deletion_jobs.deletion_revision IS
    'F09/T058: for a memory this is the revision the row was tombstoned at (previous revision + 1). UNIQUE(resource_type, resource_id, deletion_revision) makes a repeated DELETE return the same job instead of bumping the revision again.';
COMMENT ON COLUMN platform.deletion_jobs.remaining_scopes IS
    'Physical copies still to purge, as a JSON array of {"kind":...} objects: object/artifact/embedding/memory_row. F09/T058 only records them; F07 consumes them.';
COMMENT ON COLUMN platform.deletion_jobs.state IS
    'pending|running|completed|failed. F09/T058 only writes pending: no cleanup worker is registered by this ticket, and the API/page report that state honestly.';
