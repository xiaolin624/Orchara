-- F04A/T042: per-node result provenance for a linear start -> python -> end run.
--
-- A python node produces three separate, owner-visible result classes and this
-- table is the only place that says which node produced which artifact as which
-- class:
--
--   data  the validated output.json.data document
--   file  one admitted artifact the tool wrote inside /workspace/artifacts
--   log   the capped stdout/stderr capture (never run data)
--
-- The artifact row stays the content authority and `platform.run_artifact_uploads`
-- still proves the run/attempt/purpose that reserved the bytes.
-- `platform.run_artifacts` stays the run link. This table is provenance only: it
-- adds no bytes, no second content store and no owner column (the Run owns the
-- artifacts). A row may only be written after the server's own upload/complete/
-- register path accepted the artifact, which is what keeps raw tool output from
-- becoming an authorized artifact by itself.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. The table is not in the contract baseline. It is the F04A/T042 per-node
--      result mapping the ticket requires (issue #43). The contract tables
--      artifacts/run_artifacts/run_artifact_uploads are unchanged.
--   2. `artifact_id` has no ON DELETE clause: F07 owns unlink/purge and must
--      delete the provenance row together with the artifact (the composite PK
--      makes a reused id impossible without first deleting the row).
--   3. `kind` is closed to data|file|log. The UI and the owner read depend on
--      "logs are not data", so a free-text kind would be a semantic hole; a later
--      class extends this CHECK in its own migration.
--   4. `label` is bounded and printable. For a file it is the tool's relative
--      path; for data/log it is a fixed label. It is a display string, never a
--      server path or object key.
--   5. No `attempt_token` column: the live-attempt proof already lives in
--      run_artifact_uploads, and duplicating it here would create a second
--      provenance authority that can disagree.
--
-- Boundaries:
--   * One writer: the trusted API process, through the mTLS
--     POST /internal/v1/runs/{run_id}/node-results endpoint (non-contract).
--   * Every statement is schema-qualified; nothing relies on search_path.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007-0025: the worker goes through the mTLS endpoints.

CREATE SCHEMA IF NOT EXISTS platform;

CREATE TABLE IF NOT EXISTS platform.run_node_outputs (
    run_id      uuid NOT NULL REFERENCES platform.runs (id) ON DELETE CASCADE,
    node_id     text NOT NULL,
    kind        text NOT NULL,
    artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    label       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, node_id, kind, artifact_id),
    CONSTRAINT run_node_outputs_kind_check CHECK (kind IN ('data', 'file', 'log')),
    CONSTRAINT run_node_outputs_node_id_check
        CHECK (node_id ~ '^[A-Za-z][A-Za-z0-9_-]{0,63}$'),
    CONSTRAINT run_node_outputs_label_check
        CHECK (length(label) > 0 AND length(label) <= 512)
);

-- The owner read loads one Run's node results; the composite PK already serves
-- exact lookups, this index keeps the page query off a full table scan.
CREATE INDEX IF NOT EXISTS run_node_outputs_run
    ON platform.run_node_outputs (run_id, created_at, node_id);

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

COMMENT ON TABLE platform.run_node_outputs IS
    'F04A/T042 per-node result provenance (non-contract): which node produced which ready artifact as data, file or log. Written only after the server-side upload/complete/register path accepted the artifact; the artifact row remains the content authority.';
COMMENT ON COLUMN platform.run_node_outputs.node_id IS
    'The sealed definition node id (start|python|end). Bounded to the DSL node-id pattern so the display/read query cannot be poisoned by free text.';
COMMENT ON COLUMN platform.run_node_outputs.kind IS
    'data = validated output.json.data; file = one admitted tool artifact; log = capped stdout/stderr. Logs are never parsed as data and never appear in listRunArtifacts.';
COMMENT ON COLUMN platform.run_node_outputs.label IS
    'Display label only: the tool relative path for a file, a fixed label for data/log. Never an object key, host path or signed URL.';
