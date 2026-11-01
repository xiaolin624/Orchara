-- F01/T019: resource authorization (principal/resource/action grants), the
-- policy epoch that invalidates in-process decisions, and the applications read
-- model the ACL is demonstrated on.
--
-- The model is deliberately deny-by-default: a resource is visible only when the
-- caller owns it or when an unexpired `resource_grants` row names it. There is no
-- role column here and no "admin sees all" path; roles gate the management
-- endpoints, not the resource itself (SECURITY.md §1: managing the ACL is not
-- the same as reading the body).
--
-- Deliberate boundaries:
--   * `checkpoint` is out of band. langgraph-checkpoint-postgres owns that
--     schema; platform migrations never create or alter it.
--   * No vector columns: pgvector is not available in this cluster.
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * F02A owns `application_versions` and therefore the foreign key on
--     `applications.active_version_id`. F01 owns only this read model.
--   * `orchara_worker` gets no USAGE on this schema and no table privileges, so
--     the runtime role cannot read or forge grants or the policy epoch.

CREATE SCHEMA IF NOT EXISTS platform;

-- One row per policy scope. A write that changes the ACL bumps `acl.revision`
-- in the same transaction, which is what makes an in-process decision cache
-- unable to outlive a revoke. The row is seeded here so an authorization check
-- never has to guess a default (a missing row is treated as fail-closed anyway).
CREATE TABLE IF NOT EXISTS platform.policy_epochs (
    scope    text PRIMARY KEY,
    revision bigint NOT NULL CHECK (revision >= 0)
);

INSERT INTO platform.policy_epochs (scope, revision)
VALUES ('acl', 0)
ON CONFLICT (scope) DO NOTHING;

CREATE TABLE IF NOT EXISTS platform.resource_grants (
    id            uuid PRIMARY KEY,
    principal_id  uuid NOT NULL REFERENCES platform.principals (id),
    resource_type text NOT NULL CHECK (length(btrim(resource_type)) > 0),
    resource_id   uuid NOT NULL,
    action        text NOT NULL CHECK (length(btrim(action)) > 0),
    expires_at    timestamptz,
    -- One live grant per tuple: a second grant for the same tuple is a conflict,
    -- not a silent overwrite. The name is relied on by the API to report 409
    -- `grant_exists` without parsing a driver message.
    CONSTRAINT resource_grants_tuple
        UNIQUE (principal_id, resource_type, resource_id, action)
);

-- The decision lookup is always (resource_type, resource_id, action) for one
-- principal; this index also serves the list query's ACL EXISTS probe.
CREATE INDEX IF NOT EXISTS grants_resource
    ON platform.resource_grants (resource_type, resource_id, action);

-- F01 applications read model. Rows are created by F02A's createApplication;
-- T019 only needs a real resource with an owner and an optional active version
-- so the ACL and the filtered list can be exercised end to end.
CREATE TABLE IF NOT EXISTS platform.applications (
    id                uuid PRIMARY KEY,
    owner_id          uuid NOT NULL REFERENCES platform.principals (id),
    name              text NOT NULL CHECK (length(btrim(name)) > 0),
    active_version_id uuid,
    archived_at       timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS applications_owner
    ON platform.applications (owner_id);

-- Privileges. New tables inherit the same boundary as 0006: the runtime role
-- cannot read, modify or create anything in `platform`, and PUBLIC has nothing.
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

COMMENT ON TABLE platform.policy_epochs IS
    'Per-scope policy revision. Bumped in the same transaction as any ACL/role change so cached decisions for the old revision are unreachable.';
COMMENT ON TABLE platform.resource_grants IS
    'Default-deny ACL rows: (principal, resource_type, resource_id, action). expires_at NULL means no expiry; the authorizer re-validates a cached allow against the clock.';
COMMENT ON COLUMN platform.applications.active_version_id IS
    'Read model only in F01. The foreign key to platform.application_versions is added when that table lands in F02A; F02A also owns createApplication.';
