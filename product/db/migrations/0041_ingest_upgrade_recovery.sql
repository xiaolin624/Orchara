-- F10/T061 (#62) upgrade recovery: a deployment upgraded from before 0039 must
-- never leave an ingestion attempt stuck forever.
--
-- Background: 0039 added `background_jobs.expected_index_version_id` and
-- backfilled an index version for every document that was already `ready`. It
-- said nothing about the `document_ingest` jobs that were queued (`ready`) or
-- running (`leased`) at upgrade time: those rows keep a NULL expected index
-- version, because no index version existed when they were created. The T061
-- completion guard refuses such a job (`stale_revision`), but the pre-fix guard
-- did not touch the document, so it stayed in `scanning`/`indexing`; the
-- idempotent re-ingest of the same artifact returned the same stuck document and
-- a same-source rebuild was refused with `index_build_in_progress`. The document
-- could never be ingested again, which contradicts the ticket's "失败可重新摄取".
--
-- Decision (documented in ADR 0018 §6): *fail* these legacy jobs instead of
-- binding them to a freshly created index version. Binding is not deterministic:
-- several legacy jobs may exist for one document (retries), their expected
-- source revision may already be stale, and two of them bound to one building
-- index version could stage two different chunk sets into it. Failing the jobs
-- with one stable code and marking the document `failed` with that same code
-- makes the upgrade deterministic and leaves the document exactly where a failed
-- ingestion is expected to be: re-ingestable (a failed document gets a new source
-- revision and a new index version/job), with the failure visible on the
-- diagnostics surface instead of an eternal "scanning".
--
-- Scope and safety:
--   * only non-terminal `document_ingest` jobs that have no bound index version
--     are ended; a job that a later migration/API bound is left alone;
--   * only documents that are still *pre-activation* (`uploaded`/`scanning`/
--     `indexing`) at that moment are failed; a `ready` document is never touched,
--     so its read model and activation pointer are preserved;
--   * a document that already has another live (`ready`/`leased`) ingestion job
--     is skipped, so a rolling upgrade that enqueued a bound job before the
--     legacy row was claimed keeps that job's attempt intact;
--   * deleted documents are excluded (`deleted_at IS NULL`); `deleting`/`deleted`
--     states are not in the list, so a tombstone is never resurrected (and a
--     `failed` document is not rewritten);
--   * every statement is idempotent: after the first run there is no matching
--     non-terminal legacy job, and the updated documents are already `failed`, so
--     a re-run (a replay of the file, or a database where the repair already
--     happened) changes nothing and repeated `admin migrate` stays a no-op. The
--     live-job guard in step 2 also keeps a replay from touching a document that
--     was legitimately re-ingested after the repair;
--   * it never creates a job, an index version or a chunk and never moves an
--     activation pointer, so there is nothing to "un-delete" and no build to
--     finish.
--
-- Deliberate deviations from `contracts/database.sql`: none. This file writes
-- only rows in tables 0009/0034/0039 already define; no schema object is added,
-- so the `orchara_worker`/PUBLIC zero-grant boundary is unchanged and merely
-- re-asserted below.

CREATE SCHEMA IF NOT EXISTS platform;

-- 1. End the legacy attempts. `finished_at`/`failure_class` are required by
--    0009's terminal-row CHECK; the lease fields are cleared because an ended
--    attempt no longer owns a lease (the sweep must not recover it either).
UPDATE platform.background_jobs AS j
   SET state = 'failed',
       error_code = 'ingest_index_unbound',
       failure_class = 'business',
       lease_token = NULL,
       lease_deadline = NULL,
       worker_id = NULL,
       last_error_at = now(),
       finished_at = now()
 WHERE j.kind = 'document_ingest'
   AND j.expected_index_version_id IS NULL
   AND j.state IN ('ready', 'leased');

-- 2. Leave their documents in the state a failed ingestion is expected to be in,
--    with the same specific code. `knowledge_documents_failed_has_code` requires
--    the code; the activation pointer and the active revision are not touched.
--    A document that already has another live (`ready`/`leased`) ingestion job is
--    skipped: that job owns the document (a rolling upgrade can enqueue a bound
--    job before the legacy row is claimed), and failing the document under it
--    would turn its eventual success into a stale-revision refusal. The same
--    condition makes a replay of this file safe: a document re-ingested after the
--    repair is guarded by its new live job and is left alone.
UPDATE platform.knowledge_documents AS d
   SET state = 'failed',
       error_code = 'ingest_index_unbound',
       updated_at = now()
  FROM platform.background_jobs AS j
 WHERE j.kind = 'document_ingest'
   AND j.expected_index_version_id IS NULL
   AND j.target_id = d.id
   AND j.state = 'failed'
   AND j.error_code = 'ingest_index_unbound'
   AND d.deleted_at IS NULL
   AND d.state IN ('uploaded', 'scanning', 'indexing')
   AND NOT EXISTS (
       SELECT 1
         FROM platform.background_jobs AS live
        WHERE live.kind = 'document_ingest'
          AND live.target_id = d.id
          AND live.state IN ('ready', 'leased')
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
