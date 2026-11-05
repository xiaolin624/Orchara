-- F11/T073: user-scoped diagnostic grants (contract table + the object snapshot).
--
-- contracts/database.sql defines
-- `diagnostic_grants(id, owner_id, grantee_id, run_id, purposes text[], expires_at,
-- revoked_at)` but gives it no object scope, no creation/expiry guard and no read
-- audit surface. This migration creates that contract table plus the ticket's
-- documented non-contract extension (ADR 0028):
--
--   * `object_scope jsonb` is the explicit, closed list of `artifact:<uuid>`
--     entries the run owner selected when granting. It is a snapshot: an object
--     added to the run later is never silently in scope. It is never returned by a
--     contract endpoint; only the documented non-contract list read exposes it to
--     the owner (and to the grantee administrator).
--   * `created_at` plus the `expires_at > created_at` check make "a grant must be
--     time-bounded from the moment it exists" a database fact, not a handler habit.
--   * `revoked_at >= created_at` keeps the terminal state ordered.
--   * `diagnostic_grants_live (grantee_id, run_id) WHERE revoked_at IS NULL` is
--     the only lookup the content door performs; `diagnostic_grants_owner` serves
--     the owner's list.
--
-- Deviations from contracts/database.sql (all additive; owning ticket T073):
--   * `object_scope` and `created_at` are not in the reference table. They are
--     required by the ticket's object scope and by the expiry/revocation guards;
--     no contract response exposes them.
--   * `purposes` has no closed vocabulary in the contract; the service validates
--     the bounded `[a-z][a-z0-9_-]{0,63}` token shape before insert (ADR 0028).
--
-- This table is diagnostic consent only: it is not a training/consent record, it
-- has no trigger or foreign key into any training state, and creating a grant
-- writes exactly one grant row plus one audit row.
--
-- Zero grants: every statement is schema-qualified and `orchara_worker`/PUBLIC
-- keep no `platform` privileges, exactly like 0007-0044.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.diagnostic_grants (
    id           uuid PRIMARY KEY,
    owner_id     uuid NOT NULL REFERENCES platform.users (principal_id),
    grantee_id   uuid NOT NULL REFERENCES platform.principals (id),
    run_id       uuid NOT NULL REFERENCES platform.runs (id),
    purposes     text[] NOT NULL CHECK (cardinality(purposes) > 0),
    object_scope jsonb NOT NULL DEFAULT '[]'::jsonb
                 CHECK (jsonb_typeof(object_scope) = 'array'),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT diagnostic_grants_expiry_after_creation
        CHECK (expires_at > created_at),
    CONSTRAINT diagnostic_grants_revoked_after_creation
        CHECK (revoked_at IS NULL OR revoked_at >= created_at)
);

CREATE INDEX IF NOT EXISTS diagnostic_grants_live
    ON platform.diagnostic_grants (grantee_id, run_id) WHERE revoked_at IS NULL;
CREATE INDEX IF NOT EXISTS diagnostic_grants_owner
    ON platform.diagnostic_grants (owner_id, created_at DESC);

-- Privileges: this file enforces the zero-grant boundary its header claims. The
-- block is the same one 0007-0047 repeat (0042:145-152 and siblings).
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

COMMENT ON TABLE platform.diagnostic_grants IS
    'F11/T073 user-scoped diagnostic grants: one run owner authorises one administrator to read one explicit object snapshot for bounded purposes until an expiry. The row is metadata only; the bytes stay in platform.artifacts and every content read appends audit_events(action=diagnostic.content_read) with a typed whitelist.';
COMMENT ON COLUMN platform.diagnostic_grants.object_scope IS
    'F11/T073 non-contract snapshot of the objects the owner selected: a JSON array of "artifact:<uuid>" entries. Empty is written as an explicit snapshot derived at grant time, never as "all objects now and later".';
COMMENT ON COLUMN platform.diagnostic_grants.purposes IS
    'F11/T073 bounded purpose tokens ([a-z][a-z0-9_-]{0,63}, max 8). A read must name one of them; the purpose is copied into the content-read audit row.';
COMMENT ON COLUMN platform.diagnostic_grants.expires_at IS
    'F11/T073 hard expiry: a content read after this instant is refused with diagnostic_grant_expired, independently of any administrator session.';
COMMENT ON COLUMN platform.diagnostic_grants.revoked_at IS
    'F11/T073 owner revocation: set once, idempotent; a content read after it is refused with diagnostic_grant_revoked. A grant never outlives its run tombstone.';
