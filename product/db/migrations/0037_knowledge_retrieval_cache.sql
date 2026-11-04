-- F10/T067: a qualified read-only retrieval cache in PostgreSQL.
--
-- Why a table and not Redis: this host has no Redis instance, and the cache is
-- **derived, rebuildable state**, not an authority. Every authoritative fact
-- (principal, ACL epoch, knowledge-base/document revisions, embedding model
-- revision, chunk rows, sealed chunk bytes) stays in its own table; the cache
-- only memoizes one qualified retrieval result. PostgreSQL is therefore the
-- authority *and* the store, and losing the table loses performance, never
-- correctness.
--
-- How a stale row is prevented from being served (the ticket's hard requirement):
--   1. `cache_key` is a SHA-256 over the *current* component values (principal,
--      ACL epoch / knowledge-base revision, a digest of the current readable
--      document revisions, embedding model revision, canonical query, top_k).
--      Identity, permission or source-version changes recompute a different key,
--      so an old key is never looked up again even if its row still exists: an
--      asynchronous eviction is an optimization, never a permission guarantee.
--   2. The components are stored next to the key for debuggability and are
--      compared again on every hit (`principal_id`, `acl_epoch`, `kb_revision`,
--      `source_revision_digest`, `source_document_count`,
--      `embedding_model_revision`, `canonical_query`, `top_k`).
--   3. `items` stores the returned chunks including their body text; a hit
--      re-checks **every** returned chunk's document in SQL (not deleted,
--      `active_revision = revision`, body artifact ready/not deleted/same owner)
--      and the body artifact's content hash. Any disagreement discards the whole
--      entry and the request proceeds as a miss.
--   4. `expires_at` is a TTL, not an authorization: a hit inside the TTL still
--      runs (1)-(3), so expiring an entry is garbage collection only.
--
-- Deliberate deviations from `contracts/database.sql`, and their owners:
--   1. The contract has no retrieval-cache table (and no retrieval operation at
--      all; the retrieval surface is ADR 0010's documented non-contract surface).
--      This additive table is recorded in ADR 0014 and never changes a contract
--      table. A future contract that adds retrieval replaces this shape.
--   2. `canonical_query` stores the owner's query text so a hit can be debugged
--      from the row. It is owner-scoped internal state, is never returned by any
--      endpoint beyond the key prefix, and is deleted by TTL or by the lazy
--      invalidation path. Queries older than the TTL disappear with the row.
--   3. No background eviction job is added. Deletion/permission changes are
--      authoritative in their tables first; a stale row is unreachable (key) and
--      re-checked (items), then lazily deleted on the next miss for that
--      principal or by TTL. F07 deletion governance may delete rows by resource
--      later; it is not required for correctness.
--
-- Every statement is schema-qualified; `orchara_worker`/PUBLIC keep zero
-- `platform` privileges (the cache is written only by the trusted API process).

CREATE TABLE IF NOT EXISTS platform.knowledge_retrieval_cache (
    -- hex SHA-256 of the length-prefixed key components (see internal/knowledge)
    cache_key               text PRIMARY KEY
                                CHECK (cache_key ~ '^[0-9a-f]{64}$'),
    principal_id            uuid NOT NULL REFERENCES platform.principals (id),
    knowledge_base_id       uuid NOT NULL REFERENCES platform.knowledge_bases (id),
    -- Effective authorization revision. In this slice it is the knowledge-base
    -- revision (the documented stand-in of ADR 0010/0014); a future sharing/ACL
    -- change bumps it and therefore invalidates every old key immediately.
    acl_epoch               bigint NOT NULL CHECK (acl_epoch >= 1),
    kb_revision             bigint NOT NULL CHECK (kb_revision >= 1),
    -- SHA-256 over the current (document id, revision, state, active_revision)
    -- rows of this knowledge base: adding, deleting or re-activating a source
    -- changes the digest and therefore the key.
    source_revision_digest  text NOT NULL CHECK (source_revision_digest ~ '^[0-9a-f]{64}$'),
    source_document_count   integer NOT NULL CHECK (source_document_count >= 0),
    embedding_model_revision text NOT NULL
                                CHECK (length(btrim(embedding_model_revision)) > 0),
    -- The exact trimmed query string that was embedded and ranked (no further
    -- normalization: collapsing whitespace here would change what a hit means).
    canonical_query         text NOT NULL,
    query_sha256            text NOT NULL CHECK (query_sha256 ~ '^[0-9a-f]{64}$'),
    top_k                   integer NOT NULL CHECK (top_k BETWEEN 1 AND 50),
    item_count              integer NOT NULL CHECK (item_count >= 1),
    -- Sum of the stored body/citation bytes; a bounded value keeps one row from
    -- becoming an unbounded copy of the knowledge base.
    body_bytes              bigint NOT NULL CHECK (body_bytes >= 0),
    -- [{chunk_id, document_id, document_revision, chunk_no, span, citation,
    --   body_artifact_id, body_sha256, text, score}, ...]
    items                   jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'),
    created_at              timestamptz NOT NULL DEFAULT now(),
    expires_at              timestamptz NOT NULL,
    last_hit_at             timestamptz,
    hit_count               bigint NOT NULL DEFAULT 0 CHECK (hit_count >= 0),
    CONSTRAINT knowledge_retrieval_cache_expiry_check CHECK (expires_at > created_at)
);

-- Owner-scoped lookup: a hit is always SELECTced by (cache_key, principal_id,
-- knowledge_base_id), so a row can never be served to a different principal even
-- if a digest ever collided.
CREATE INDEX IF NOT EXISTS knowledge_retrieval_cache_scope
    ON platform.knowledge_retrieval_cache (principal_id, knowledge_base_id, expires_at);

-- Opportunistic TTL cleanup scans by expiry.
CREATE INDEX IF NOT EXISTS knowledge_retrieval_cache_expiry
    ON platform.knowledge_retrieval_cache (expires_at);

COMMENT ON TABLE platform.knowledge_retrieval_cache IS
    'T067 PostgreSQL retrieval cache (no Redis on this host). Derived, rebuildable state memoizing one qualified retrieval result. Hit-time re-check of every returned chunk''s document/revision/body-artifact hash is mandatory; expires_at is a TTL, never a permission guarantee.';
COMMENT ON COLUMN platform.knowledge_retrieval_cache.cache_key IS
    'SHA-256 over principal, acl_epoch, KB revision, current readable document revision digest, embedding model revision, canonical query and top_k. Identity/permission/source changes recompute the key, so a lost eviction cannot be hit.';
COMMENT ON COLUMN platform.knowledge_retrieval_cache.source_revision_digest IS
    'SHA-256 over the current non-deleted knowledge_documents rows (id, revision, state, active_revision) of the knowledge base.';
COMMENT ON COLUMN platform.knowledge_retrieval_cache.items IS
    'Returned chunks including body text. A hit re-checks every item''s document tombstone/current revision/body-artifact owner+hash; any disagreement discards the entry and the request proceeds as a miss.';
COMMENT ON COLUMN platform.knowledge_retrieval_cache.expires_at IS
    'TTL only. A hit inside the TTL still re-derives the key and re-checks every item, so expiry is garbage collection, not authorization.';

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
