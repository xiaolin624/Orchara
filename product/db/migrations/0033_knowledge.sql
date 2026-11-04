-- F10/T055: knowledge bases, ingested documents and traceable chunks.
--
-- Baseline: `contracts/database.sql` defines `knowledge_bases`,
-- `knowledge_documents` and `knowledge_chunks` (lines ~317-334) but the
-- implementation has never created them, and the frozen contract assumes
-- pgvector (`embedding vector`). This migration creates the three tables for the
-- T055 txt/md ingestion slice.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. **No pgvector.** The contract declares `embedding vector` for
--      `knowledge_chunks` (and `memory_embeddings`). pgvector is not installed on
--      the development cluster and cannot be installed there, so this ticket
--      stores the embedding as `double precision[]` plus the explicit
--      `embedding_dimension` (fixed per model revision). No `CREATE EXTENSION
--      vector` is issued. The owning follow-up is **T064 (#64, F10 retrieval
--      scale/ACL)** and **T067 (#68, F10 index maintenance)**; they own any
--      pgvector/ANN decision and a possible representation change. Retrieval in
--      T055 therefore filters ACL/current revision in SQL and ranks the
--      surviving candidate set in the trusted API process (ADR 0008).
--   2. `knowledge_documents.active_revision` is additive and nullable. The
--      contract's `revision` is the document's current revision; the activation
--      pointer is the seam T061 (#62) needs to rebuild an index and atomically
--      switch which chunk revision is readable. T055 sets it to `revision` only
--      in the completion guard that marks a document `ready`; retrieval requires
--      `state='ready' AND active_revision = revision AND
--      chunk.document_revision = revision`.
--   3. `knowledge_documents.parser_version` / `chunker_version` /
--      `embedding_model_revision` / `error_code` / `updated_at` are additive
--      columns. `parser_version` exists in the contract; the other four record
--      the deterministic versions and the safe failure code the ticket requires
--      ("never ready on empty/failed"). `error_code` is written only by the
--      completion guard, never by the handler.
--   4. `knowledge_chunks.embedding_dimension` and `created_at` are additive.
--      The dimension makes the fixed-dimension rule (ROUTING-MEMORY-KNOWLEDGE
--      §3) inspectable instead of implied, and lets the guard refuse a mixed
--      model revision. `citation` stays the single traceability document: it
--      carries document id/revision, chunk_no, span (character offsets + line
--      range), source artifact id + sha256 and parser/chunker/embedding
--      revisions, so a chunk points at the exact bytes it came from.
--   5. `knowledge_chunks.embedding` stays nullable: a chunk whose embedding
--      could not be produced is a failed ingestion, and the completion guard
--      refuses to activate a revision with any missing embedding. It is never
--      silently substituted with a zero vector.
--
-- Deliberate boundaries:
--   * No CSV/XLSX parser (T059 #60), no PDF parser (T060 #61), no version
--     replacement/reindex flow (T061 #62), no vector index maintenance (T067
--     #68). The tables already carry the revision/activation/dimension seams
--     those tickets need.
--   * Same guarded kind style as 0017: `document_ingest` was already in the
--     0009 kind CHECK and in `internal/background/job.go`, so this ticket does
--     not touch the CHECK. The handler registry, the retry table and the
--     completion guard are extended in Go instead.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC keep zero `platform` privileges. The completion
--     guard (trusted API process) is the only writer of `state`,
--     `active_revision` and `error_code`; the maintenance handler writes staged
--     chunk rows and is verified by that guard in the same transaction that
--     activates the revision.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.knowledge_bases (
    id         uuid PRIMARY KEY,
    name       text NOT NULL CHECK (length(btrim(name)) > 0 AND length(name) <= 200),
    owner_id   uuid NOT NULL REFERENCES platform.principals (id),
    revision   bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS knowledge_bases_owner
    ON platform.knowledge_bases (owner_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.knowledge_documents (
    id                       uuid PRIMARY KEY,
    knowledge_base_id        uuid NOT NULL REFERENCES platform.knowledge_bases (id),
    source_artifact_id       uuid NOT NULL REFERENCES platform.artifacts (id),
    revision                 bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    state                    text NOT NULL CHECK (state IN (
                                 'uploaded', 'scanning', 'indexing', 'ready', 'failed',
                                 'deleting', 'deleted')),
    active_revision          bigint CHECK (active_revision IS NULL OR active_revision >= 1),
    parser_version           text,
    chunker_version          text,
    embedding_model_revision text,
    error_code               text,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    deleted_at               timestamptz,
    CONSTRAINT knowledge_documents_activation_is_current CHECK (
        active_revision IS NULL OR active_revision <= revision
    ),
    CONSTRAINT knowledge_documents_ready_has_active_revision CHECK (
        state <> 'ready'
        OR (active_revision IS NOT NULL AND active_revision = revision)
    ),
    CONSTRAINT knowledge_documents_failed_has_code CHECK (
        state <> 'failed' OR error_code IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS documents_scope
    ON platform.knowledge_documents (knowledge_base_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS platform.knowledge_chunks (
    id                       uuid PRIMARY KEY,
    document_id              uuid NOT NULL REFERENCES platform.knowledge_documents (id),
    document_revision        bigint NOT NULL CHECK (document_revision >= 1),
    chunk_no                 integer NOT NULL CHECK (chunk_no >= 0),
    body_artifact_id         uuid NOT NULL REFERENCES platform.artifacts (id),
    citation                 jsonb NOT NULL,
    embedding_model_revision text NOT NULL CHECK (length(btrim(embedding_model_revision)) > 0),
    embedding                double precision[],
    embedding_dimension      integer CHECK (embedding_dimension IS NULL OR embedding_dimension > 0),
    created_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT knowledge_chunks_dimension_matches CHECK (
        (embedding IS NULL AND embedding_dimension IS NULL)
        OR (embedding IS NOT NULL AND embedding_dimension = cardinality(embedding))
    ),
    -- The local embedder's fixed 384-dimension contract. Stored here as well as
    -- in internal/embedding.Dimensions so a mixed-dimension write is refused by
    -- the database, not only by the writer. A real provider with another
    -- dimension is a new embedding_model_revision and a new migration (T061/T064).
    CONSTRAINT knowledge_chunks_embedding_dimension_local CHECK (
        embedding IS NULL
        OR (array_length(embedding, 1) = 384 AND embedding_dimension = 384)
    ),
    CONSTRAINT knowledge_chunks_unique_no UNIQUE (document_id, document_revision, chunk_no)
);

CREATE INDEX IF NOT EXISTS knowledge_chunks_revision
    ON platform.knowledge_chunks (document_id, document_revision, chunk_no);

COMMENT ON TABLE platform.knowledge_bases IS
    'T055 owner-scoped knowledge bases. Owner-only ACL: a non-owner (including an administrator) reads 404, never 403.';
COMMENT ON TABLE platform.knowledge_documents IS
    'T055 ingested source documents. `revision` is the current revision; `active_revision` is the T061 seam that decides which chunk revision retrieval may read. Only the document_ingest completion guard writes state/active_revision/error_code.';
COMMENT ON TABLE platform.knowledge_chunks IS
    'T055 traceable chunks. `citation` carries document id/revision, chunk_no, character span, line range, source artifact id + sha256 and parser/chunker/embedding revisions. `embedding` is double precision[] because pgvector is unavailable here (T064/T067 own the ANN decision); dimension is fixed per embedding_model_revision.';
COMMENT ON COLUMN platform.knowledge_documents.active_revision IS
    'T061 activation pointer: the chunk revision retrieval is allowed to read. T055 sets it equal to revision only when the completion guard marks the document ready.';
COMMENT ON COLUMN platform.knowledge_documents.error_code IS
    'Safe failure code written by the completion guard when a document becomes failed. Empty content, unsupported/corrupt input or a handler failure must end here, never in ready.';
COMMENT ON COLUMN platform.knowledge_chunks.embedding_dimension IS
    'cardinality(embedding). Fixed per embedding_model_revision; the activation guard refuses mixed or missing dimensions.';

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
