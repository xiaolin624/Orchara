-- F08/T077 (#78): the authorized purpose catalog is a projection, never the
-- authority, and its rebuild ledger is separate from the knowledge index.
--
-- Baseline: `application_versions` (0010, extended by 0043 with revocation) is
-- the authoritative published-version row; `applications` carries the ACL owner
-- and the archive tombstone. `platform.outbox` (0043) holds the durable
-- publish/revoke signal T068/T070 write in the same transaction as the state
-- change. This migration creates only the projection and its one-row rebuild
-- ledger.
--
-- Deliberate deviations from `contracts/database.sql`, with their owners:
--   1. `application_catalog_entries` has the contract's primary key,
--      `search_text`, `source_manifest_sha256` and `updated_at`. The contract's
--      `embedding_model_revision` / `embedding vector` columns are deliberately
--      NOT created: pgvector is not installed on this cluster (T055 recorded the
--      same fact for `knowledge_chunks`), and T077 implements the documented
--      full-text path only. The ANN/embedding decision stays with the retrieval
--      tickets (T064/T067); the catalog must not grow a second, untested vector
--      space.
--   2. `application_id` is additive. The contract row can reach the application
--      through `application_versions`, but a projection needs the owning
--      application id for the SQL ACL predicate before the page is cut. It is
--      always copied from the authoritative version row in the same transaction
--      as the entry, and the join re-verifies it (a stale/tampered copy is
--      dropped by the authoritative re-check).
--   3. `ts` is a generated tsvector over `search_text` so the GIN index can
--      answer the English/simple-word part of the query while `ILIKE` covers
--      substrings (the Chinese text T056/T061 handle elsewhere needs no segmenter
--      for an exact-substring match). No claim is made about Chinese token
--      quality; the full-text configuration is an implementation detail of the
--      projection.
--   4. `platform.catalog_index_state` is not in `contracts/database.sql`. It is a
--      single-row ledger (`id = 'catalog'`) recording the projection epoch, the
--      last completed rebuild, the in-progress rebuild and the last error. It is
--      how an interrupted rebuild is observable instead of silently
--      half-serving: search refuses to serve the projection unless the ledger
--      says `ready` and the live authorized-version count equals the live valid
--      entry count.
--
-- Deliberate boundaries:
--   * Nothing here reads or writes `knowledge_chunks` / `knowledge_index_versions`
--     (0034/0039). The two indexes have different authorities, ACL predicates,
--     rebuild paths and failure modes; T077's rebuild test counts the knowledge
--     rows before and after to prove the separation.
--   * Every statement is schema-qualified; `orchara_worker`/PUBLIC keep zero
--     privileges on `platform`, exactly like 0007-0044.

CREATE SCHEMA IF NOT EXISTS platform;

-- The projection over published, unrevoked application versions. One row per
-- version; `search_text` is derived from the immutable capability card, and
-- `source_manifest_sha256` names the version bytes the row was built from. A row
-- whose hash no longer matches its version is stale and is never served.
CREATE TABLE IF NOT EXISTS platform.application_catalog_entries (
    application_version_id uuid PRIMARY KEY
        REFERENCES platform.application_versions (id),
    application_id         uuid NOT NULL REFERENCES platform.applications (id),
    search_text            text NOT NULL CHECK (length(search_text) > 0),
    source_manifest_sha256 text NOT NULL CHECK (length(source_manifest_sha256) = 64),
    ts                     tsvector GENERATED ALWAYS AS
        (to_tsvector('simple', search_text)) STORED,
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS catalog_entries_search
    ON platform.application_catalog_entries USING gin (ts);
CREATE INDEX IF NOT EXISTS catalog_entries_application
    ON platform.application_catalog_entries (application_id, updated_at DESC);
-- The readiness check compares entry hashes with the authoritative version; the
-- plain index makes that count cheap and deterministic.
CREATE INDEX IF NOT EXISTS catalog_entries_manifest
    ON platform.application_catalog_entries (source_manifest_sha256);

-- One-row rebuild ledger. `projection_epoch` is bumped when a full rebuild
-- starts, so an error record can never be attributed to a superseded attempt.
-- `expected_count`/`indexed_count` are the rebuild's own progress; the search
-- readiness check additionally compares the live eligible-version count with the
-- live valid-entry count, so a hand-edited counter cannot make a half-built
-- projection look complete.
CREATE TABLE IF NOT EXISTS platform.catalog_index_state (
    id                  text PRIMARY KEY CHECK (id = 'catalog'),
    state               text NOT NULL DEFAULT 'failed' CHECK (state IN (
                            'ready', 'rebuilding', 'failed'
                        )),
    projection_epoch    bigint NOT NULL DEFAULT 0 CHECK (projection_epoch >= 0),
    rebuild_started_at  timestamptz,
    rebuilt_at          timestamptz,
    last_error_code     text,
    last_error_at       timestamptz,
    expected_count      integer NOT NULL DEFAULT 0 CHECK (expected_count >= 0),
    indexed_count       integer NOT NULL DEFAULT 0 CHECK (indexed_count >= 0),
    updated_at          timestamptz NOT NULL DEFAULT now()
);

-- Privileges: identical boundary to 0006-0044.
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

COMMENT ON TABLE platform.application_catalog_entries IS
    'F08/T077 catalog search projection: one derived row per published, unrevoked application version, rebuilt from application_versions/capability_card and never the authority. ACL, revocation, archive and manifest-hash checks are re-applied to the authoritative tables on every read; the embedding columns are omitted because pgvector is unavailable and T077 implements full-text only.';
COMMENT ON COLUMN platform.application_catalog_entries.application_id IS
    'F08/T077 (non-contract): the owning application copied from the authoritative application_versions row. It exists so the SQL ACL predicate can run before the projection page is cut; the per-row authoritative re-check re-reads the real version/application pair and drops a stale copy.';
COMMENT ON COLUMN platform.application_catalog_entries.source_manifest_sha256 IS
    'The application_versions.manifest_sha256 value this entry was built from. A mismatch with the current version hash means the entry is stale and search must leave it out (the authorized full-text fallback still answers).';
COMMENT ON TABLE platform.catalog_index_state IS
    'F08/T077 one-row rebuild ledger (id=catalog). state=rebuilding while a full rebuild runs, failed with last_error_code/last_error_at when it aborted, ready only after every eligible version was indexed. Search serves the projection only when state=ready and the live eligible/valid-entry counts agree; otherwise it uses the authorized full-text fallback and says so.';
