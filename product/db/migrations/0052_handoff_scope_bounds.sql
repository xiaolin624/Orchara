-- F08/T082 (#83): bound the confirmed cross-application handover.
--
-- T074 created `platform.request_handoffs` with `source_run_ids uuid[]` and
-- `artifact_ids uuid[]` (both NOT in contracts/database.sql). The confirmed
-- handover is a *minimum*: the new application receives only the server-produced
-- summary and the selected artifacts, never the whole conversation or another
-- application's memory. The Go layer enforces that scope (internal/requests/
-- handoff.go, ADR 0030); this migration adds the database backstop so a row that
-- bypasses the service cannot record an over-broad confirmation either.
--
-- Deliberate deviation (recorded in ADR 0030): two CHECK constraints on the
-- non-contract `request_handoffs` table. No table, column or grant is widened.
-- Cardinality is used instead of a trigger so a replayed migrate is a no-op and
-- the T074 row shape is unchanged:
--   * at least one and at most five source Runs;
--   * at most five confirmed artifacts (zero is the summary-only handover).
--
-- Zero grants: the table was created and revoked by 0044; repeating the revocation
-- keeps the `orchara_worker`/PUBLIC boundary explicit for this change.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'request_handoffs_source_run_ids_bound'
           AND conrelid = 'platform.request_handoffs'::regclass
    ) THEN
        ALTER TABLE platform.request_handoffs
            ADD CONSTRAINT request_handoffs_source_run_ids_bound
            CHECK (cardinality(source_run_ids) BETWEEN 1 AND 5);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'request_handoffs_artifact_ids_bound'
           AND conrelid = 'platform.request_handoffs'::regclass
    ) THEN
        ALTER TABLE platform.request_handoffs
            ADD CONSTRAINT request_handoffs_artifact_ids_bound
            CHECK (cardinality(artifact_ids) BETWEEN 0 AND 5);
    END IF;
END
$$;

REVOKE ALL ON TABLE platform.request_handoffs FROM PUBLIC;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'orchara_worker') THEN
        EXECUTE 'REVOKE ALL ON TABLE platform.request_handoffs FROM orchara_worker';
    END IF;
END
$$;

COMMENT ON CONSTRAINT request_handoffs_source_run_ids_bound ON platform.request_handoffs IS
    'F08/T082 (non-contract): a confirmed handover names between 1 and 5 source Runs, so it can never mean "the whole conversation".';
COMMENT ON CONSTRAINT request_handoffs_artifact_ids_bound ON platform.request_handoffs IS
    'F08/T082 (non-contract): at most 5 explicitly confirmed artifacts cross an application boundary with one handover.';
