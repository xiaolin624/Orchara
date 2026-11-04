-- F10/T061: versioned knowledge indexes (independent document / chunk-set /
-- embedding-index revisions).
--
-- Baseline: `0034_knowledge.sql` gave the document a single activation pointer
-- (`active_revision`) and made the chunk identity
-- `(document_id, document_revision, chunk_no)`. That is enough for one index per
-- source revision, but not for the ticket: re-building the same source revision
-- under another embedding model must create a separate index version (the old
-- vectors may never be mixed with the new ones), a rebuild needs its own
-- progress/failure record, and a late job from an older source revision must be
-- refused at activation. This migration adds exactly that storage layer.
--
-- Deliberate deviations / extensions from `contracts/database.sql`
-- (`knowledge_index_versions` does not exist there) and their owners:
--   1. `platform.knowledge_index_versions` is one row per (document, source
--      revision, embedding model revision) *build attempt*. `version` is
--      per-document and monotonic; `document_revision` is the source revision the
--      index was built from; `embedding_model_revision` + `embedding_dimension`
--      name the vector space; `state` is the build lifecycle
--      (`building`/`ready`/`failed`/`superseded`) and `chunk_count` the honest
--      staged-chunk counter (no synthetic percentage). Only the
--      `document_ingest` completion guard writes `ready`/`failed`; this table is
--      the rebuild/progress read model the non-contract diagnostics surface
--      exposes (ADR 0017). The contract has no index/reindex operation, so no
--      contract file changes.
--   2. `knowledge_documents.active_index_id` + `active_embedding_model_revision`
--      are the activation pointer (the T055 `active_revision` stays as the source
--      revision of the active index, so old readers and the ready constraint keep
--      their meaning). Only the completion guard moves the pointer.
--   3. `knowledge_chunks.index_version_id` is additive and nullable for rows
--      written before this migration (the backfill below fills the live ones).
--      New writes always set it. The chunk identity changes from
--      `(document_id, document_revision, chunk_no)` to
--      `(index_version_id, chunk_no)`: two index versions of the *same* source
--      revision (different embedding models, or a retried build) must not
--      collide. The old unique constraint is dropped in this migration; this is
--      the only destructive statement, and it is required by the new identity.
--   4. `background_jobs.expected_index_version_id` binds one ingestion job to
--      exactly one index version, so the guard can verify the staged set and the
--      activation decision for the same build. Additive nullable column.
--
-- Backfill (upgrade path, no-op on a fresh database): every document that is
-- currently `ready` gets one index version `1` at its current revision/model, its
-- existing chunks are attached to it, and the document pointer is set. Failed or
-- deleted documents keep no index (they were not readable).
--
-- Every statement is schema-qualified; `orchara_worker`/PUBLIC keep zero
-- `platform` privileges.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.knowledge_index_versions (
    id                       uuid PRIMARY KEY,
    document_id              uuid NOT NULL REFERENCES platform.knowledge_documents (id),
    version                  bigint NOT NULL CHECK (version >= 1),
    document_revision        bigint NOT NULL CHECK (document_revision >= 1),
    embedding_model_revision text NOT NULL
                                 CHECK (length(btrim(embedding_model_revision)) > 0),
    embedding_dimension      integer NOT NULL CHECK (embedding_dimension > 0),
    state                    text NOT NULL CHECK (state IN (
                                 'building', 'ready', 'failed', 'superseded')),
    chunk_count              integer NOT NULL DEFAULT 0 CHECK (chunk_count >= 0),
    parser_version           text,
    chunker_version          text,
    error_code               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    activated_at             timestamptz,
    superseded_at            timestamptz,
    CONSTRAINT knowledge_index_versions_unique_version UNIQUE (document_id, version),
    CONSTRAINT knowledge_index_versions_failed_code CHECK (
        state <> 'failed' OR error_code IS NOT NULL
    ),
    CONSTRAINT knowledge_index_versions_ready_activated CHECK (
        state <> 'ready' OR activated_at IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS knowledge_index_versions_document
    ON platform.knowledge_index_versions (document_id, version DESC);

-- One in-flight build per document *per source revision*: a newer source
-- revision may start building while the old revision's job is still running
-- (that stale job is then refused by the activation guard), but asking for the
-- same source revision twice is refused by the database as well as the service.
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_index_versions_one_building
    ON platform.knowledge_index_versions (document_id, document_revision)
    WHERE state = 'building';

ALTER TABLE platform.knowledge_documents
    ADD COLUMN IF NOT EXISTS active_index_id uuid
        REFERENCES platform.knowledge_index_versions (id);
ALTER TABLE platform.knowledge_documents
    ADD COLUMN IF NOT EXISTS active_embedding_model_revision text;

ALTER TABLE platform.knowledge_chunks
    ADD COLUMN IF NOT EXISTS index_version_id uuid
        REFERENCES platform.knowledge_index_versions (id);

-- The chunk-set identity is the index version, not the source revision.
ALTER TABLE platform.knowledge_chunks
    DROP CONSTRAINT IF EXISTS knowledge_chunks_unique_no;
CREATE UNIQUE INDEX IF NOT EXISTS knowledge_chunks_index_version_no
    ON platform.knowledge_chunks (index_version_id, chunk_no)
    WHERE index_version_id IS NOT NULL;

ALTER TABLE platform.background_jobs
    ADD COLUMN IF NOT EXISTS expected_index_version_id uuid
        REFERENCES platform.knowledge_index_versions (id);
CREATE INDEX IF NOT EXISTS background_jobs_expected_index
    ON platform.background_jobs (expected_index_version_id)
    WHERE expected_index_version_id IS NOT NULL;

-- Upgrade backfill: one already-active version per currently readable document.
INSERT INTO platform.knowledge_index_versions
    (id, document_id, version, document_revision, embedding_model_revision,
     embedding_dimension, state, chunk_count, parser_version, chunker_version,
     activated_at, created_at, updated_at)
SELECT gen_random_uuid(), d.id, 1, d.revision,
       coalesce(
           d.embedding_model_revision,
           (SELECT min(kc.embedding_model_revision)
              FROM platform.knowledge_chunks AS kc
             WHERE kc.document_id = d.id AND kc.document_revision = d.revision),
           -- Before this migration the only ingestible embedder was the
           -- deterministic local stub, so that is the honest fallback for a
           -- pre-existing ready document whose column was never populated.
           'local-hash-v1'
       ),
       coalesce(
           (SELECT max(kc.embedding_dimension)
              FROM platform.knowledge_chunks AS kc
             WHERE kc.document_id = d.id AND kc.document_revision = d.revision),
           384
       ),
       'ready',
       (SELECT count(*)
          FROM platform.knowledge_chunks AS kc
         WHERE kc.document_id = d.id AND kc.document_revision = d.revision),
       d.parser_version, d.chunker_version,
       coalesce(d.updated_at, now()), d.created_at, coalesce(d.updated_at, now())
  FROM platform.knowledge_documents AS d
 WHERE d.state = 'ready' AND d.active_revision IS NOT NULL AND d.deleted_at IS NULL
   AND NOT EXISTS (
       SELECT 1 FROM platform.knowledge_index_versions AS iv WHERE iv.document_id = d.id
   );

UPDATE platform.knowledge_chunks AS kc
   SET index_version_id = iv.id
  FROM platform.knowledge_index_versions AS iv
 WHERE kc.index_version_id IS NULL
   AND iv.document_id = kc.document_id
   AND iv.document_revision = kc.document_revision;

UPDATE platform.knowledge_documents AS d
   SET active_index_id = iv.id,
       active_embedding_model_revision = iv.embedding_model_revision
  FROM platform.knowledge_index_versions AS iv
 WHERE d.active_index_id IS NULL
   AND d.state = 'ready'
   AND iv.document_id = d.id
   AND iv.document_revision = d.revision;

-- Added after the backfill: an upgraded database already has ready documents,
-- and they only satisfy this constraint once their activation pointer is set.
ALTER TABLE platform.knowledge_documents
    ADD CONSTRAINT knowledge_documents_ready_has_active_index CHECK (
        state <> 'ready' OR active_index_id IS NOT NULL
    );

COMMENT ON TABLE platform.knowledge_index_versions IS
    'T061 versioned knowledge index builds: one row per (document, document_revision, embedding_model_revision) attempt. `state` is building/ready/failed/superseded; `chunk_count` is the staged-chunk counter (no synthetic percentage). Only the document_ingest completion guard may move a row to ready/failed, and only a guarded activation may move the document pointer.';
COMMENT ON COLUMN platform.knowledge_index_versions.document_revision IS
    'The source document revision this index was built from. An activation requires it to equal the live document revision.';
COMMENT ON COLUMN platform.knowledge_documents.active_index_id IS
    'T061 activation pointer: the one index version retrieval may read. The completion guard is its only writer; a stale job never moves it.';
COMMENT ON COLUMN platform.knowledge_documents.active_embedding_model_revision IS
    'The embedding model revision of the active index version; retrieval filters on it so old and new vectors are never mixed.';
COMMENT ON COLUMN platform.knowledge_chunks.index_version_id IS
    'The index version that owns this chunk set. Chunk identity is (index_version_id, chunk_no) so two builds of the same source revision cannot collide.';
COMMENT ON COLUMN platform.background_jobs.expected_index_version_id IS
    'T061: the index version one document_ingest job was created for. The completion guard re-reads this row and refuses activation when the document revision or tombstone changed.';

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
