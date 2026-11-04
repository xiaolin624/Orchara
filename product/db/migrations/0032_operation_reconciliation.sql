-- F06/T051: evidence reconciliation for an unknown external write.
--
-- The contract `operations` row already carries `reconciled_by` and
-- `reconciliation_evidence_id` (created by 0025). This migration adds only the
-- facts the reconcile endpoint must make idempotent and auditable:
--
--   * `reconciliation_conclusion` — the human conclusion
--     (confirmed_success|confirmed_not_sent|confirmed_failure). It is stored so a
--     replay of the same Idempotency-Key returns the recorded result and a
--     different conclusion after the terminal write is a 409, without inventing a
--     second table or trusting request text.
--   * `reconciliation_key` — the contract's Idempotency-Key value once it has
--     committed. Same key + same conclusion + same evidence is the idempotent
--     replay; any different tuple is a conflict.
--   * `reconciliation_limitation` — an explicit safe limitation fact recorded
--     before dispatch when the connector has no provider idempotency/query
--     capability (the only connector shape F06/T048 compiles today). The owner
--     API/UI discloses it; it never contains a provider body, URL or credential.
--
-- Deliberate deviation: none of the three columns is in
-- contracts/database.sql. They are additive, nullable audit columns of the
-- contract `operations` table, exactly like 0025's created_at/updated_at/
-- last_error_code deviation. No contract column changes meaning. No new table.
--
-- Boundaries are unchanged: the trusted API process is the only writer,
-- `platform` stays revoked from PUBLIC/orchara_worker, and no trigger exists.

ALTER TABLE platform.operations
    ADD COLUMN IF NOT EXISTS reconciliation_conclusion text,
    ADD COLUMN IF NOT EXISTS reconciliation_key text,
    ADD COLUMN IF NOT EXISTS reconciliation_limitation text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'operations_reconciliation_conclusion_closed'
    ) THEN
        ALTER TABLE platform.operations
            ADD CONSTRAINT operations_reconciliation_conclusion_closed
            CHECK (reconciliation_conclusion IS NULL
                   OR reconciliation_conclusion IN ('confirmed_success',
                                                    'confirmed_not_sent',
                                                    'confirmed_failure'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'operations_reconciliation_key_bounded'
    ) THEN
        ALTER TABLE platform.operations
            ADD CONSTRAINT operations_reconciliation_key_bounded
            CHECK (reconciliation_key IS NULL OR length(reconciliation_key) <= 200);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'operations_reconciliation_limitation_closed'
    ) THEN
        ALTER TABLE platform.operations
            ADD CONSTRAINT operations_reconciliation_limitation_closed
            CHECK (reconciliation_limitation IS NULL
                   OR reconciliation_limitation = 'no_idempotency_or_query_support');
    END IF;
END
$$;

COMMENT ON COLUMN platform.operations.reconciliation_conclusion IS
    'F06/T051 human conclusion that settled an unknown external write. Set together with reconciled_by/reconciliation_evidence_id; never written for a dispatch result.';
COMMENT ON COLUMN platform.operations.reconciliation_key IS
    'The contract Idempotency-Key of the reconcile call that committed. Same key+conclusion+evidence replays the recorded answer; a different tuple after the terminal write is 409.';
COMMENT ON COLUMN platform.operations.reconciliation_limitation IS
    'Safe limitation fact recorded before a write dispatch: the connector declares no provider idempotency key and no query/receipt lookup, so an unknown outcome cannot be resolved by the provider. Disclosed by the owner API/UI; never automatic resend.';
