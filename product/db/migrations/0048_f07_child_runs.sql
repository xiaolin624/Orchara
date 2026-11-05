-- F07/T078 (#79): the delegation receipt for one child Run.
--
-- `runs.parent_run_id`, `runs.parent_invocation_key`, the
-- `UNIQUE(parent_run_id, parent_invocation_key)` arbiter and the contract CHECK
-- (`parent_run_id IS NULL OR (request_id IS NULL AND parent_invocation_key IS NOT
-- NULL)`) already exist in 0014 and are NOT re-added here. What 0014 does not
-- record, and what the T078 acceptance criteria need, is the *content identity* of
-- one delegation:
--
--   1. which child application version the parent pinned (`child_version_id`, a
--      real FK; the parent's frozen snapshot still owns the closure membership
--      proof, this column is the durable receipt);
--   2. the exact canonical SHA-256 of the mapped input the caller sent
--      (`input_sha256`) and the sealed artifact holding those bytes
--      (`input_artifact_id`). `runs` only points at the child's input artifact; it
--      cannot answer "same invocation key, different input" without re-reading the
--      bytes, and an idempotent replay must answer that before it returns the old
--      child;
--   3. which fields were explicitly mapped (`io_mapping`) so "content that was not
--      mapped never crossed" is a read of this row, not an inference from bytes;
--   4. the identity of the child's frozen I/O schemas (`input_schema_sha256`,
--      `output_schema_sha256`) so a result validation can name the schema it used
--      after the fact;
--   5. the delegation depth of this child, and whether the parent declared the
--      branch required (fail-fast) or optional. These are additive non-contract
--      columns; the contract's spawnChild body has no place for them and F07/T080/
--      T081 need them.
--
-- Not in contracts/database.sql: additive non-contract surface (ADR 0005 pattern,
-- documented in docs/adr/0025-f07-child-runs.md and the T078 report). T081 (#82)
-- owns the waiting/capacity side and reads this table; it does not need a second
-- delegation table.
--
-- Design decisions recorded here:
--   * No `state` column. The child's own `runs.state` is the authority; a
--     duplicated state machine in a receipt table is exactly the kind of second
--     truth T081 would have to keep consistent.
--   * `parent_attempt_token` is the attempt that created the delegation. It is an
--     audit fact only; an idempotent replay after a resume is created by a
--     different attempt and must not rewrite it.
--   * No child slot counters here: the independent child pool and the per-parent
--     cap are F07/T081, explicitly out of scope for T078.
--   * Deletion: the row is retained with its Run (the parent/child FKs), so the
--     existing F11 deletion scopes cover it without a second copy.
--
-- Every statement is schema-qualified; `orchara_worker`/`PUBLIC` get no grant
-- (the API is the only writer/reader, like 0014/0044).

CREATE TABLE IF NOT EXISTS platform.run_children (
    child_run_id          uuid PRIMARY KEY REFERENCES platform.runs (id),
    parent_run_id         uuid NOT NULL REFERENCES platform.runs (id),
    -- The attempt that asked for this delegation. Nullable: the row is inserted in
    -- the same transaction as the child Run, so a caller always has one, but a
    -- future repair path may not.
    parent_attempt_token  uuid REFERENCES platform.run_attempts (token),
    parent_invocation_key text NOT NULL,
    child_version_id      uuid NOT NULL REFERENCES platform.application_versions (id),
    input_sha256          text NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    input_artifact_id     uuid NOT NULL REFERENCES platform.artifacts (id),
    -- The explicitly mapped child input field names, plus the call_app node that
    -- declared the mapping: {"node_id": "...", "fields": ["..."]}. Never the
    -- values (the sealed artifact is the value authority).
    io_mapping            jsonb NOT NULL
                              CHECK (jsonb_typeof(io_mapping) = 'object'
                                     AND io_mapping ? 'fields'
                                     AND jsonb_typeof(io_mapping -> 'fields') = 'array'),
    input_schema_sha256   text NOT NULL CHECK (input_schema_sha256 ~ '^[0-9a-f]{64}$'),
    output_schema_sha256  text NOT NULL CHECK (output_schema_sha256 ~ '^[0-9a-f]{64}$'),
    required              boolean NOT NULL DEFAULT true,
    depth                 integer NOT NULL CHECK (depth BETWEEN 1 AND 8),
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- The database arbiter of "one child per parent invocation key". The spawn
    -- transaction locks the parent Run before reading/inserting, so a racing pair
    -- serialises here and the loser replays or conflicts; this constraint is the
    -- backstop that makes a second child impossible even if that lock is removed.
    CONSTRAINT run_children_invocation UNIQUE (parent_run_id, parent_invocation_key)
);

CREATE INDEX IF NOT EXISTS run_children_parent
    ON platform.run_children (parent_run_id, created_at);
CREATE INDEX IF NOT EXISTS run_children_version
    ON platform.run_children (child_version_id);

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

COMMENT ON TABLE platform.run_children IS
    'F07/T078: one delegation receipt per child Run. The parent Runs row owns the (parent_run_id, parent_invocation_key) idempotency key (0014); this table adds the frozen child version, the canonical mapped-input hash and sealed artifact, the explicitly mapped field names and the two frozen I/O schema hashes. The child Run is always created in the same transaction by the spawnChild endpoint.';
COMMENT ON COLUMN platform.run_children.io_mapping IS
    'F07/T078 (non-contract): {"node_id": "<call_app node>", "fields": ["<child input field>"]}. Only these fields crossed the application boundary; the values live in the sealed input artifact. Parent history/memory is never part of it.';
COMMENT ON COLUMN platform.run_children.input_schema_sha256 IS
    'SHA-256 of the RFC 8785 canonical bytes of the child version''s frozen definition.input_schema; the schema the mapped input was validated against.';
COMMENT ON COLUMN platform.run_children.output_schema_sha256 IS
    'SHA-256 of the RFC 8785 canonical bytes of the child version''s frozen definition.output_schema; the schema the child''s terminal result is validated against.';
COMMENT ON COLUMN platform.run_children.depth IS
    'Delegation depth of this child: 1 for a direct child of a top-level Run, 2 for a grandchild. Bounded by the parent''s frozen policy.max_child_depth at spawn time; F07/T080 owns the budget/count atomics.';
