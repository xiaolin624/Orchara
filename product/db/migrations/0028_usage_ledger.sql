-- F06/T046: the shared decimal usage ledger (budget reservation and settlement).
--
-- IMPLEMENTATION-DETAILS §6/§7, EXECUTION §6 and the T046 brief: one run's
-- budget is consumed by every physical model attempt of that run and of its
-- child runs. `runs.budget_limit` is the limit; `platform.usage_ledger` is the
-- single authoritative record of what was reserved, released and charged.
-- `operation_attempts.reserved_amount/charged_amount` stay the per-attempt
-- projection, never the only record.
--
-- Contract source: `contracts/database.sql` defines `usage_ledger` verbatim
-- (id, run_id, budget_root_run_id, operation_id, entry_key UNIQUE, kind,
-- amount, currency, tokens_in/out, price_version, created_at). This migration
-- creates that table without changing its columns or meaning.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `platform.operation_attempts.settlement_state` is an additive column, not
--      a contract column. T041 created the physical-attempt ledger; T046 needs
--      to say, per attempt, whether its reservation is still held, was released
--      on failure, was settled with actual usage, or is a conservative
--      reservation retained because the provider reported no usage
--      (`usage_missing`, reconciled later). Values: none|reserved|settled|
--      released|usage_missing. `none` is the tool attempt, which never reserves.
--   2. The contract gives `tokens_in/out` no CHECK; this migration adds
--      non-negative CHECKs and `amount >= 0` is already contracted. A ledger
--      entry can never carry a negative amount: a release is a positive amount
--      with kind='release'. No float is ever written (`numeric(20,8)`).
--   3. The contract does not index `usage_ledger`. T046 adds the two reads it
--      actually performs: one run's entries newest-first and one operation's
--      entries.
--   4. `budget_root_run_id` is the topmost ancestor of `run_id` reached through
--      `runs.parent_run_id`; for a debug/F04A run it is the run itself. The
--      server resolves it; a worker never supplies it.
--
-- Ledger arithmetic (the only reading used by internal/budget):
--   outstanding = sum(reserve) - sum(release)
--   charged     = sum(charge)
--   consumed    = charged + outstanding
-- A successful physical attempt inserts its `charge` and one paired full
-- `release` of the reservation, so the reservation stops being outstanding
-- while the actual amount stays charged. A failed/cancelled attempt inserts one
-- `release` of the full reservation. A missing-usage success inserts neither,
-- so the conservative reservation remains outstanding until a later
-- reconciliation settles it.
--
-- Boundaries:
--   * One writer: the trusted API process (internal/budget, called by
--     internal/capabilities). No trigger and no second writer.
--   * Every table lives in `platform`; a worker never reads it directly.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007–0027.

CREATE TABLE IF NOT EXISTS platform.usage_ledger (
    id                uuid PRIMARY KEY,
    run_id            uuid NOT NULL REFERENCES platform.runs (id),
    budget_root_run_id uuid NOT NULL REFERENCES platform.runs (id),
    operation_id      uuid NOT NULL REFERENCES platform.operations (id),
    entry_key         text NOT NULL,
    kind              text NOT NULL CHECK (kind IN ('reserve', 'release', 'charge')),
    amount            numeric(20,8) NOT NULL CHECK (amount >= 0),
    currency          text NOT NULL,
    tokens_in         bigint CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out        bigint CHECK (tokens_out IS NULL OR tokens_out >= 0),
    price_version     text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT usage_ledger_entry_key UNIQUE (entry_key),
    CONSTRAINT usage_ledger_currency_len CHECK (length(currency) = 3),
    CONSTRAINT usage_ledger_price_version_nonblank CHECK (length(btrim(price_version)) > 0)
);

CREATE INDEX IF NOT EXISTS usage_ledger_run_created
    ON platform.usage_ledger (run_id, created_at DESC);
CREATE INDEX IF NOT EXISTS usage_ledger_operation
    ON platform.usage_ledger (operation_id);
CREATE INDEX IF NOT EXISTS usage_ledger_budget_root
    ON platform.usage_ledger (budget_root_run_id);

-- Deviation 1: per-attempt settlement state. The default is `none` so a tool
-- attempt (which never reserves money) is not mislabelled as holding a budget.
ALTER TABLE platform.operation_attempts
    ADD COLUMN IF NOT EXISTS settlement_state text NOT NULL DEFAULT 'none';

-- Guarded constraint: ADD CONSTRAINT has no IF NOT EXISTS, so it is added only
-- when absent. The DO block keeps the whole migration replayable.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
          WHERE conname = 'operation_attempts_settlement_state_check'
            AND conrelid = 'platform.operation_attempts'::regclass
    ) THEN
        ALTER TABLE platform.operation_attempts
            ADD CONSTRAINT operation_attempts_settlement_state_check
            CHECK (settlement_state IN ('none', 'reserved', 'settled', 'released', 'usage_missing'));
    END IF;
END
$$;

-- Same privilege boundary as 0007–0027: a worker never gets a direct path and
-- nothing is inherited from PUBLIC.
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

COMMENT ON TABLE platform.usage_ledger IS
    'F06/T046 shared decimal budget ledger (contract table): every reserve, release and charge of a run tree. The sum, not the per-attempt projection, is the budget authority; amounts are numeric(20,8), never float.';
COMMENT ON COLUMN platform.usage_ledger.entry_key IS
    'Stable idempotency key. internal/budget derives "<attempt key>:reserve|release|charge" so a replay or a retried reconciliation cannot double-reserve or double-charge.';
COMMENT ON COLUMN platform.usage_ledger.budget_root_run_id IS
    'Topmost ancestor of run_id through runs.parent_run_id; one debug/F04A run is its own root. Reservations of every child attempt are serialised on this row.';
COMMENT ON COLUMN platform.usage_ledger.amount IS
    'Non-negative decimal amount. charge is the actual provider usage at the recorded price_version; release is a positive amount that cancels a reservation. Negative money is unrepresentable.';
COMMENT ON COLUMN platform.operation_attempts.settlement_state IS
    'T046: none (tool attempt, no reservation) | reserved (budget held) | settled (actual usage charged, reservation released) | released (failed/cancelled attempt, reservation returned) | usage_missing (provider reported no usage; conservative reservation retained for later reconciliation).';
