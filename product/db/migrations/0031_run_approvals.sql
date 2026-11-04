-- F06/T050: exact-parameter approvals and safe resume.
--
-- One row is the durable, server-built proposal for one *operation*: the real
-- target, the canonical params hash, the designated approver, the expiry and the
-- one decision. The human-readable proposal (origin/method/path/query/params/
-- destination/source versions) is sealed as an `approval_proposal` artifact
-- referenced by `proposal_artifact_id`; this table's public projection is the
-- contract's five audit fields, and the page reads the sealed proposal through
-- the existing artifact-content ACL (run owner == designated approver in F04A).
--
-- Columns are exactly contracts/database.sql's baseline (no extra timestamp
-- column): the list cursor therefore orders by (pending-first, expires_at, id),
-- and `decided_at` is the one clock fact for a decision.
--
-- Deliberate deviations, and their owners:
--   1. `node_key` is filled with the logical-key node prefix (`agent:<node_id>`
--      for the T043 agent loop; the whole logical key for a direct invocation).
--      The contract has no `operations.node_key` column and F06/T050 may not add
--      one; the capability input's logical key is the only stable node/call
--      identity the worker sends, and `UNIQUE(operation_id)` already makes the
--      approval 1:1 with the operation.
--   2. No `operations.approval_id` column is added. `approvals.operation_id` is
--      the single link, so the two rows cannot disagree about which approval
--      belongs to which parked operation.
--   3. The table is a contract table, unlike T035's `run_waitings`. It is created
--      here because the contract baseline is the aggregate schema and earlier
--      migrations never materialised it.
--
-- Boundaries:
--   * Approval never grants permission. The resumed attempt re-enters the same
--     capability proxy, which re-checks the live attempt, run state, manifest,
--     policy approval/revocation and budget before the `authorized -> dispatching`
--     CAS.
--   * Publication review is a different aggregate (`publication_reviews`); its
--     reviewers cannot decide a Run approval because this table is the only
--     lookup the decision path performs.
--   * Every statement is schema-qualified; the worker has no platform privilege.

CREATE TABLE IF NOT EXISTS platform.approvals (
    id                   uuid PRIMARY KEY,
    run_id               uuid NOT NULL REFERENCES platform.runs (id),
    operation_id         uuid UNIQUE REFERENCES platform.operations (id),
    node_key             text NOT NULL,
    proposal_artifact_id uuid NOT NULL REFERENCES platform.artifacts (id),
    params_sha256        text NOT NULL,
    approver_id          uuid NOT NULL REFERENCES platform.users (principal_id),
    state                text NOT NULL,
    expires_at           timestamptz NOT NULL,
    decided_at           timestamptz,
    decided_by           uuid REFERENCES platform.principals (id),
    decision_key         text,
    CONSTRAINT approvals_state_check
        CHECK (state IN ('pending', 'approved', 'rejected', 'expired', 'invalidated')),
    CONSTRAINT approvals_node_key_nonblank CHECK (length(btrim(node_key)) > 0),
    CONSTRAINT approvals_params_sha256_nonblank CHECK (length(btrim(params_sha256)) > 0),
    CONSTRAINT approvals_decision_pair CHECK ((state = 'pending') = (decided_at IS NULL)),
    UNIQUE (run_id, node_key, params_sha256)
);

CREATE INDEX IF NOT EXISTS approvals_pending
    ON platform.approvals (approver_id, expires_at)
    WHERE state = 'pending';

CREATE INDEX IF NOT EXISTS approvals_run
    ON platform.approvals (run_id);

-- Privileges: the same boundary as every other platform table (0025/0026/0028/
-- 0029/0030). The runtime worker never reads approvals; only the API path with
-- the approval scope reaches them.
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
