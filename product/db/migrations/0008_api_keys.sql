-- F01/T020: scoped, revocable API keys for service accounts.
--
-- A key is a bearer credential: the client presents the raw secret once per
-- request and the platform stores only its SHA-256. Keys belong to service
-- principals; a human authenticates with a session cookie and never holds a key.
-- That rule is enforced in code (identity.CreateAPIKey refuses a human target)
-- because this table cannot express a cross-table "principal.kind = 'service'"
-- constraint without a trigger, and the contract's schema is a plain table plus
-- a foreign key.
--
-- Deliberate boundaries:
--   * `checkpoint` is out of band. langgraph-checkpoint-postgres owns that
--     schema; platform migrations never create or alter it.
--   * No vector columns: pgvector is not available in this cluster.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker` gets no USAGE on this schema and no table privileges, so
--     the runtime role cannot read a stored key hash or mint/revoke keys.
--   * Scopes are stored as a non-empty text[]; the vocabulary is fixed in code
--     (`identity.ScopeApplicationRead` in F01) rather than by a database enum, so
--     a later ticket can extend it with a migration-free registry change. A key
--     is always additionally constrained by the resource ACL: scope alone never
--     grants access to a resource.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.api_keys (
    id           uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES platform.principals (id),
    key_hash     text NOT NULL UNIQUE,
    scopes       text[] NOT NULL CHECK (cardinality(scopes) > 0),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz
);

-- The authentication lookup is by key_hash (the UNIQUE index); this index
-- serves "all keys of one principal", which T021's disable path needs.
CREATE INDEX IF NOT EXISTS api_keys_principal
    ON platform.api_keys (principal_id);

-- Privileges. The new table inherits the 0006 boundary: the runtime role cannot
-- read or modify anything in `platform`, and PUBLIC has nothing.
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

COMMENT ON TABLE platform.api_keys IS
    'Scoped bearer credentials. Only service principals may hold a key; that cross-table rule is enforced in identity.CreateAPIKey because this schema cannot express it. Only the SHA-256 hex of the secret is stored; revoked_at and expires_at are checked on every authentication.';
COMMENT ON COLUMN platform.api_keys.scopes IS
    'Non-empty scope vocabulary fixed in code (F01: application:read). A scope is necessary but never sufficient: the resource ACL is checked separately and defaults to deny.';
COMMENT ON COLUMN platform.api_keys.key_hash IS
    'Lowercase SHA-256 hex of the one-time secret. The plaintext is returned exactly once at creation and is never stored, logged or audited.';
