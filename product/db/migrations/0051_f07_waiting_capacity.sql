-- F07/T081 (#82): the independent child execution pool, the per-parent parallel
-- cap and the fair-claiming rotation.
--
-- Why this is additive non-contract surface: `contracts/database.sql` freezes the
-- 0016 quota machinery (`quota_counters` + `quota_allocations`) and 0014's
-- `runs.parent_run_id`; it has no second counter for delegated child Runs and no
-- per-parent fairness column. T078 deliberately did not add them (its ADR 0025 D1
-- says "no child slot counters here: the independent child pool and the per-parent
-- cap are F07/T081"). This migration adds exactly the two pieces T081's acceptance
-- criteria need, and no new table:
--
--   1. `global/child_slots` (capacity 8, OPERATIONS.md:26 "子任务8"). A child Run's
--      *attempt* takes one of these instead of a `global/execution_slots` slot, so
--      20 parents parked in `waiting_children` (which already released their Python
--      slot on exit) cannot starve delegated work. The counter is seeded here; a
--      fresh deployment must not silently run without it (the claim fails closed
--      with 503 execution_capacity_unavailable when the row is missing).
--
--      The per-parent counter (`parent:<parent_run_id>/child_slots`, capacity 3,
--      OPERATIONS.md:28 "每父并行3") is created lazily by the first claim of one of
--      that parent's children. A counter row is append-only in this design: the
--      `quota_counters` CHECK (`used <= capacity`) and the allocation rows keyed by
--      `(scope, dimension, holder_key)` are what make `used` exactly one, so the
--      capacity can never be lowered later. Seeding one row per parent here is not
--      possible (the parent set is open).
--
--   2. `runs.last_child_claim_at`: the rotation key. The candidate query orders
--      queued child Runs by `COALESCE(parent.last_child_claim_at, '-infinity')`
--      before `jobs.available_at`, so a parent that just got one child claimed goes
--      behind every parent that has not (or has not recently) advanced. The column
--      belongs to the *parent* Run (it records when that parent last consumed a
--      child-slot claim), never to the child.
--
-- Deliberate design decisions (recorded in ADR 0032 and the T081 report):
--   * Slots are taken at **claim**, not at spawn. The contract spawnChild body has
--     no capacity field and a spawn-time 429 would make the parent's node fail
--     before it can even wait; a "parallel" limit is a property of running
--     attempts. Spawn still enforces count/depth (T078) and the parent's frozen
--     closure.
--   * The per-parent scope names the **direct** parent Run (`parent:<id>`), not the
--     tree root: "每父" is the delegating Run. Nested delegation therefore gives
--     each Run its own cap while the global child pool still bounds the tree.
--   * Release reuses `quota_allocations` (`released_at`) exactly like the existing
--     execution slots. A child attempt has *two* allocation rows (global + parent);
--     the release primitive was generalised to release every row of one holder key,
--     so no second release system exists.
--   * No `run_children.state` column: the child's own `runs.state` stays the
--     authority (ADR 0025 D1) and the terminal hook is idempotent from it.
--
-- Every statement is schema-qualified; `orchara_worker`/`PUBLIC` get no grant (the
-- API is the only writer/reader, like 0014/0016/0049).

ALTER TABLE platform.runs
    ADD COLUMN IF NOT EXISTS last_child_claim_at timestamptz;

INSERT INTO platform.quota_counters (scope, dimension, used, capacity)
VALUES ('global', 'child_slots', 0, 8)
ON CONFLICT (scope, dimension) DO NOTHING;

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

COMMENT ON COLUMN platform.runs.last_child_claim_at IS
    'F07/T081 (non-contract): when this Run last had one of its delegated child Runs claimed. The claim candidate query rotates child Runs by this key so one parent cannot monopolise the child pool. It is only written for Runs that are themselves parents; a child Run never changes its own value here.';
