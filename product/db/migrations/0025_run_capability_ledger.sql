-- F06/T041: the run-scoped controlled-capability ledger.
--
-- IMPLEMENTATION-DETAILS §2/§5 and the T041 brief: every worker-driven model or
-- tool call is one `platform.operations` row (the logical capability call) and
-- one `platform.operation_attempts` row per *physical* dispatch. The operation
-- row is the identity a retry/replay is compared against; the attempt rows are
-- how the platform proves "the real stub count matches the ledger" (issue #42
-- acceptance criterion). No row may exist only in the worker's memory.
--
-- Contract source: `contracts/database.sql` defines `operations` verbatim
-- (id, run_id, logical_key, capability_version, capability_kind, risk_class,
-- params_sha256, params_artifact_id, state, provider_idempotency_key,
-- receipt_artifact_id, dispatched_at, reconciled_by,
-- reconciliation_evidence_id, UNIQUE(run_id,logical_key)). This migration
-- creates that table and the per-attempt ledger F06 needs.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `operations.created_at`, `updated_at` and `last_error_code` are added:
--      the owner-facing ledger read needs a stable ordering and a safe error
--      code, and idempotent replay needs a change clock. They are additive
--      columns, not a semantic change to the contract columns.
--   2. `platform.operation_attempts` is not in the contract baseline. It is the
--      F06/T041 physical-attempt ledger (IMPLEMENTATION-DETAILS §5: every
--      physical call is a row, never a hidden retry). T046 adds budget
--      settlement on top of `reserved_amount`/`charged_amount`; T050 adds
--      connector/approval rows, which this ticket deliberately does not create.
--   3. `operation_attempts.attempt_token` binds the physical attempt to the
--      live run attempt that caused it (worker provenance and the result
--      endpoint's "belongs to this run/attempt" check). It is nullable so a
--      future maintenance/supervisor-owned dispatch can exist, but T041 always
--      writes it.
--   4. `connector` is a legal `capability_kind` in the contract, but no
--      connector capability is executable in this batch: the Go service refuses
--      it with 422 `capability_kind_unsupported`. The CHECK still accepts it so
--      T050 can insert connector rows without a second migration.
--   5. `provider_idempotency_key`, `dispatched_at`, `reconciled_by` and
--      `reconciliation_evidence_id` are contract columns this ticket creates but
--      does not write (no provider idempotency key exists until a real adapter
--      lands; reconciliation is T046). They are nullable exactly as contracted.
--
-- Boundaries:
--   * One writer: the trusted API process. There is no trigger and no second
--     writer.
--   * Every table lives in `platform`; a worker never reads it directly. The
--     worker path is the mTLS capability endpoint and the non-contract
--     capability-results endpoint only.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007–0024.

CREATE TABLE IF NOT EXISTS platform.operations (
    id                          uuid PRIMARY KEY,
    run_id                      uuid NOT NULL REFERENCES platform.runs (id),
    logical_key                 text NOT NULL,
    capability_version          text NOT NULL,
    capability_kind             text NOT NULL
                                    CHECK (capability_kind IN ('model', 'tool', 'connector')),
    risk_class                  text NOT NULL
                                    CHECK (risk_class IN ('pure', 'read', 'write')),
    params_sha256               text NOT NULL,
    params_artifact_id          uuid NOT NULL REFERENCES platform.artifacts (id),
    state                       text NOT NULL
                                    CHECK (state IN ('prepared', 'awaiting_approval', 'authorized',
                                                     'dispatching', 'succeeded', 'failed', 'unknown')),
    provider_idempotency_key    text,
    receipt_artifact_id         uuid REFERENCES platform.artifacts (id),
    dispatched_at               timestamptz,
    reconciled_by               uuid REFERENCES platform.principals (id),
    reconciliation_evidence_id  uuid REFERENCES platform.artifacts (id),
    -- Deviation 1: ordering/change clock and the safe error code the owner read
    -- projects. Never a provider body.
    created_at                  timestamptz NOT NULL DEFAULT now(),
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    last_error_code             text,
    CONSTRAINT operations_run_logical_key UNIQUE (run_id, logical_key),
    CONSTRAINT operations_params_sha256_hex CHECK (params_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT operations_logical_key_nonblank CHECK (length(btrim(logical_key)) > 0),
    CONSTRAINT operations_logical_key_bounded CHECK (length(logical_key) <= 200),
    CONSTRAINT operations_last_error_code_bounded
        CHECK (last_error_code IS NULL OR length(last_error_code) <= 128)
);

-- The owner read selects one Run's operations newest-first; the result endpoint
-- looks one row up by id.
CREATE INDEX IF NOT EXISTS operations_run_created
    ON platform.operations (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS operations_run_state
    ON platform.operations (run_id, state);

CREATE TABLE IF NOT EXISTS platform.operation_attempts (
    id                 uuid PRIMARY KEY,
    operation_id       uuid NOT NULL REFERENCES platform.operations (id) ON DELETE CASCADE,
    -- Deviation 2: the per-physical-call row. attempt_no starts at 1 and is
    -- dense per operation; UNIQUE is the backstop for concurrent dispatch.
    attempt_no         integer NOT NULL CHECK (attempt_no > 0),
    state              text NOT NULL
                            CHECK (state IN ('prepared', 'dispatching', 'succeeded', 'failed')),
    -- Deviation 3: the run attempt that caused this physical call (worker
    -- provenance; nullable only for a future non-worker dispatcher).
    attempt_token      uuid REFERENCES platform.run_attempts (token),
    result_artifact_id uuid REFERENCES platform.artifacts (id),
    error_code         text,
    tokens_in          bigint CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out         bigint CHECK (tokens_out IS NULL OR tokens_out >= 0),
    -- Pre-dispatch reservation: T046 settles it. Money is numeric, never float.
    reserved_amount    numeric(20,8) NOT NULL DEFAULT 0 CHECK (reserved_amount >= 0),
    charged_amount     numeric(20,8) CHECK (charged_amount IS NULL OR charged_amount >= 0),
    currency           text,
    price_version      text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    CONSTRAINT operation_attempts_operation_no UNIQUE (operation_id, attempt_no),
    CONSTRAINT operation_attempts_error_code_bounded
        CHECK (error_code IS NULL OR length(error_code) <= 128)
);

CREATE INDEX IF NOT EXISTS operation_attempts_operation
    ON platform.operation_attempts (operation_id, attempt_no);

-- Same privilege boundary as 0007–0024: the database never grants the worker a
-- direct path, and nothing is inherited from PUBLIC.
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

COMMENT ON TABLE platform.operations IS
    'F06/T041 run-scoped controlled-capability ledger (contract table): one row per logical model/tool capability call. The row id is the worker-supplied operation id; UNIQUE(run_id,logical_key) makes a same-key different-params call a 409 operation_conflict instead of a second billed call.';
COMMENT ON COLUMN platform.operations.params_sha256 IS
    'SHA-256 of the RFC 8785 JCS bytes of the capability params. The sealed params artifact holds exactly those bytes; this hash is what an idempotent replay and the tool grant compare.';
COMMENT ON COLUMN platform.operations.receipt_artifact_id IS
    'Sealed validated response (model) or reported tool result artifact. Set only after the capability reached succeeded; a failed/unknown operation never carries one.';
COMMENT ON COLUMN platform.operations.last_error_code IS
    'Safe closed code (for example provider_transient, capability_not_allowed) for the owner read. Never a provider payload, URL or credential.';
COMMENT ON COLUMN platform.operations.updated_at IS
    'Database clock of the last state change, maintained by the one writer (the API process), not a trigger.';
COMMENT ON TABLE platform.operation_attempts IS
    'F06/T041 per-physical-dispatch ledger (non-contract): one row per real provider/tool call, so the real call count is auditable and a hidden retry is impossible. T046 settles reserved_amount/charged_amount.';
COMMENT ON COLUMN platform.operation_attempts.reserved_amount IS
    'Conservative pre-dispatch reservation in numeric(20,8). T046/F13A own budget policy; T041 only records the reservation and the provider-reported charge.';
COMMENT ON COLUMN platform.operation_attempts.attempt_token IS
    'The live run attempt that caused this physical call. Binds the non-contract capability-results report to the same attempt that received the grant; nullable only for a future non-worker dispatcher.';
COMMENT ON CONSTRAINT operation_attempts_operation_no ON platform.operation_attempts IS
    'One row per physical attempt number; a retry inserts attempt_no+1 rather than mutating the previous row, so the real call count is never lost.';
