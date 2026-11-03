-- F06/T045: the compatible-fallback fact on a capability operation.
--
-- IMPLEMENTATION-DETAILS 搂2/搂10 and the T045 brief: a model call whose primary
-- profile keeps failing with an *availability* fault may be served by the first
-- compatible profile in `ModelConfig.fallback_profile_version_ids`. The ledger
-- must then say so: a switched call may never look like a seamless primary
-- success. Two additive columns on `platform.operations` carry that fact.
--
-- Deliberate deviations from contracts/database.sql, and their owners:
--   1. `fallback_profile_version_id` and `fallback_reason` are not contract
--      columns. They are the additive F06/T045 projection of a server-owned
--      dispatch fact (the worker never supplies them). `capability_version`
--      keeps its contracted meaning: the version the worker asked for, i.e.
--      the primary. The fallback is identified separately, never by rewriting
--      the requested capability.
--   2. `fallback_reason` is closed to availability|rate_limited|
--      adapter_unavailable. Those are the only faults that may switch profiles;
--      permission, authorisation, syntax, schema-validation, tool-argument and
--      policy failures stay terminal and never write a fallback fact.
--   3. The pair check makes "a fallback profile without a reason" (or the
--      reverse) unrepresentable, so a reader cannot show a profile with no
--      explanation.
--   4. The physical attempts (including the fallback's single attempt) stay in
--      `platform.operation_attempts`; their tokens/charged_amount are the cost
--      authority. No second cost column is added here.
--
-- Boundaries:
--   * One writer: the trusted API process (internal/capabilities), exactly like
--     0025.
--   * Additive only: no existing column changes type or meaning, no row rewrite.
--   * `orchara_worker`/PUBLIC get no `platform` privileges, exactly like
--     0007鈥?026.

ALTER TABLE platform.operations
    ADD COLUMN IF NOT EXISTS fallback_profile_version_id uuid
        REFERENCES platform.model_profile_versions (id),
    ADD COLUMN IF NOT EXISTS fallback_reason text;

ALTER TABLE platform.operations
    ADD CONSTRAINT operations_fallback_reason_check
        CHECK (fallback_reason IS NULL
               OR fallback_reason IN ('availability', 'rate_limited', 'adapter_unavailable'));

ALTER TABLE platform.operations
    ADD CONSTRAINT operations_fallback_pair_check
        CHECK ((fallback_profile_version_id IS NULL) = (fallback_reason IS NULL));

COMMENT ON COLUMN platform.operations.fallback_profile_version_id IS
    'T045: the model profile version that actually served this operation after '
    'a compatible fallback; NULL means the primary capability_version answered.';

COMMENT ON COLUMN platform.operations.fallback_reason IS
    'T045: closed reason for the switch (availability, rate_limited, '
    'adapter_unavailable). NULL together with fallback_profile_version_id.';
