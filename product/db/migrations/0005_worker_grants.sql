-- P04/T008: grant the runtime role exactly what it needs, and nothing else.
--
-- DATA-MODEL.md §48 says the worker has no direct write access to business
-- tables. The worker still has to record its own liveness (`app.workers`) so the
-- supervisor can tell a dead worker from a busy one, and it has to write its own
-- outbox (`app.operations`) — those two are runtime bookkeeping, not business
-- data. This migration makes that boundary explicit instead of leaving it to
-- whatever the DSN happens to be.
--
-- Verification: `psql` as the worker role can INSERT/UPDATE app.workers and
-- app.operations, and is denied on every other app table and on CREATE.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orchara_worker') THEN
        CREATE ROLE orchara_worker LOGIN;
    END IF;
END
$$;

-- Runtime bookkeeping it owns.
GRANT USAGE ON SCHEMA app TO orchara_worker;
GRANT SELECT, INSERT, UPDATE ON app.workers TO orchara_worker;
GRANT SELECT, INSERT ON app.operations TO orchara_worker;

-- Checkpoint schema (DML only; migrations belong to the owner).
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = 'checkpoint') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA checkpoint TO orchara_worker';
        EXECUTE 'GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA checkpoint TO orchara_worker';
    END IF;
END
$$;

-- Explicitly not granted: identity/threads, artifacts, pending writes, and DDL.
REVOKE CREATE ON SCHEMA app FROM orchara_worker;
REVOKE CREATE ON SCHEMA public FROM orchara_worker;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'app' AND table_name = 'threads') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON app.threads FROM orchara_worker';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'app' AND table_name = 'artifacts') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON app.artifacts FROM orchara_worker';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'app' AND table_name = 'pending_writes') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON app.pending_writes FROM orchara_worker';
    END IF;
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = 'app' AND table_name = 'lease_tokens') THEN
        EXECUTE 'REVOKE INSERT, UPDATE, DELETE ON app.lease_tokens FROM orchara_worker';
    END IF;
END
$$;

COMMENT ON TABLE app.workers IS
    'Runtime liveness registry. The worker role may write here by explicit grant (0005); this is bookkeeping, not business data.';
