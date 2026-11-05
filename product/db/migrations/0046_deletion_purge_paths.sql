-- F11/T071: the purge scan path (no new business table, one index).
--
-- T071 consumes the first three scopes of a T069 deletion job
-- (`object_store`, `search_index`, `retrieval_cache`) and needs no new table:
--   * `platform.deletion_jobs` already carries `remaining_scopes`,
--     `completed_scopes`, `attempt_count`, `error_code`, `started_at`,
--     `updated_at` and the `deletion_jobs_open` scan index (0042);
--   * `platform.artifacts.purged_at` exists since 0009 and has been dead code
--     until this ticket (T069 report §3.4): the purge is its first writer;
--   * `platform.background_jobs.kind` already allows `delete_content` (0009) and
--     the retry/claim/completion machinery is kind-agnostic;
--   * `platform.knowledge_chunks` / `platform.knowledge_index_versions`
--     (0034/0039) hold the index rows and the `double precision[]` vector;
--   * `platform.knowledge_retrieval_cache` (0037) holds the cached retrieval
--     entries whose `items` JSONB array contains the chunk body text.
--
-- The one thing missing is an indexable way to find the cache entries that
-- contain a purged document or chunk. The purge uses JSONB **array containment**
-- (`items @> '[{"document_id": "..."}]'`, the same shape the hit path stores), so
-- the index must be `jsonb_ops` (path_ops does not support containment) and
-- cannot be a per-key expression index. Without it every purge scans the whole
-- cache table. 0037 explicitly deferred this to the deletion governance ticket
-- (「F07 deletion governance may delete rows by resource later」); this is that
-- ticket.
--
-- Deliberate deviations from contracts/database.sql: none. The cache table is
-- already a documented non-contract ADR 0014 surface; this migration only adds an
-- index and a comment to it, and T071 writes `artifacts.purged_at`, a contract
-- column the implementation had not used yet.
--
-- Idempotent: `CREATE INDEX IF NOT EXISTS` is a no-op when re-applied, and the
-- migration ledger makes a repeated `admin migrate` a no-op as well.
--
-- Zero grants: every statement is schema-qualified; `orchara_worker`/PUBLIC keep
-- no `platform` privileges, exactly like 0007-0046.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE INDEX IF NOT EXISTS knowledge_retrieval_cache_items
    ON platform.knowledge_retrieval_cache USING gin (items jsonb_ops);

COMMENT ON INDEX platform.knowledge_retrieval_cache_items IS
    'F11/T071: GIN (jsonb_ops) over knowledge_retrieval_cache.items so a deletion purge can find every cached retrieval entry that contains a purged document/chunk (array containment items @> [{document_id|chunk_id}]). T067 explicitly left this index to the deletion-governance ticket; the hit-time re-check remains the correctness authority, this index is only the scan path.';

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
