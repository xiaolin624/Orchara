-- F01/T015: identity core in the `platform` schema.
--
-- Scope: principals, local users, one-time activation tokens, role bindings,
-- sessions and audit events. Constraints and indexes below are deliberately
-- part of the migration rather than application checks: a second writer (a
-- future service, a repair script) must not be able to create a row the model
-- says is impossible.
--
-- Deliberate boundaries:
--   * `checkpoint` is out of band. langgraph-checkpoint-postgres owns that
--     schema; platform migrations never create or alter it.
--   * No vector columns: pgvector is not available in this cluster, and the
--     embedding migrations belong to the tickets that fix a dimension.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker` gets no USAGE on this schema and no table privileges,
--     so the runtime role cannot read, modify or create identity rows.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.principals (
    id                  uuid PRIMARY KEY,
    kind                text NOT NULL CHECK (kind IN ('user', 'service')),
    display_name        text NOT NULL CHECK (length(btrim(display_name)) > 0),
    disabled_at         timestamptz,
    responsible_user_id uuid,
    created_at          timestamptz NOT NULL DEFAULT now(),
    -- A service principal always has a named human owner; a human principal
    -- never has one. This is what stops an ownerless service account from
    -- starting high-impact work (T020/T021 build on it).
    CONSTRAINT principals_responsible_matches_kind CHECK (
        (kind = 'service' AND responsible_user_id IS NOT NULL)
        OR (kind = 'user' AND responsible_user_id IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS platform.users (
    principal_id         uuid PRIMARY KEY REFERENCES platform.principals (id),
    login                text NOT NULL UNIQUE CHECK (length(btrim(login)) > 0),
    password_hash        text NOT NULL,
    must_change_password boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS platform.activation_tokens (
    id           uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES platform.users (principal_id),
    token_hash   text NOT NULL UNIQUE,
    expires_at   timestamptz NOT NULL,
    consumed_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS activation_tokens_principal
    ON platform.activation_tokens (principal_id);

-- Added after `users` exists, as the design specifies: the responsible user is
-- itself a local user.
ALTER TABLE platform.principals
    ADD CONSTRAINT principals_responsible_user_fk
    FOREIGN KEY (responsible_user_id) REFERENCES platform.users (principal_id);

CREATE TABLE IF NOT EXISTS platform.role_bindings (
    principal_id uuid NOT NULL REFERENCES platform.principals (id),
    role         text NOT NULL CHECK (role IN ('admin', 'creator', 'user')),
    PRIMARY KEY (principal_id, role)
);

CREATE TABLE IF NOT EXISTS platform.sessions (
    token_hash   text PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES platform.principals (id),
    csrf_hash    text NOT NULL,
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz
);

CREATE INDEX IF NOT EXISTS sessions_principal ON platform.sessions (principal_id);
CREATE INDEX IF NOT EXISTS sessions_expiry ON platform.sessions (expires_at);

CREATE TABLE IF NOT EXISTS platform.audit_events (
    id            uuid PRIMARY KEY,
    actor_id      uuid REFERENCES platform.principals (id),
    action        text NOT NULL CHECK (length(btrim(action)) > 0),
    resource_type text NOT NULL,
    resource_id   uuid,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_events_resource
    ON platform.audit_events (resource_type, resource_id);
CREATE INDEX IF NOT EXISTS audit_events_created
    ON platform.audit_events (created_at);

-- Privileges. The identity tables are not PUBLIC-readable, and the runtime
-- role must not be able to touch them at all. `REVOKE` (not "forget to grant")
-- is the point: a default privilege granted elsewhere is undone here.
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

COMMENT ON TABLE platform.principals IS
    'One row per human user or service account. Rows are never deleted so audit events keep resolving.';
COMMENT ON COLUMN platform.users.password_hash IS
    'argon2id PHC string, or the literal !pending-activation before an invitation is activated.';
COMMENT ON TABLE platform.activation_tokens IS
    'One-time invitation tokens. Only the SHA-256 of the token is stored; consumed_at is set by a single conditional UPDATE.';
COMMENT ON TABLE platform.sessions IS
    'Session token and CSRF token hashes only; the raw values live in the client cookie and response body.';
