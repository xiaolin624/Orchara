# T114 — 20 top-level stand-in executions at the 100 user / 1000 application profile

- **Ticket:** GitHub #115 (T114, epic F13B). Branch `t/t114-capacity-20-top`.
- **Status:** **passed** (measurement), with one recorded product defect outside this ticket's
  scope (cancel race, §4; fixed by the follow-up `t/cancel-exit-fix`, see §4 “Status update”).
  The committed evidence comes from one full run of
  `F13B_CAPACITY_FULL=1 F13B_WRITE_EVIDENCE=1 python -m pytest
  tests/f13b/test_t114_capacity.py::test_capacity_full_three_trials -q` (`1 passed in 2922.75s`).
  The default gate (`python -m pytest tests/f13b/test_t114_capacity.py -q`) runs the same harness
  as one 60-second trial (`1 passed in 169.61s`) without writing evidence.
- **B2 review fixes (2026-11-08):** S1 (task-loss gate), S4 (executing-cancel terminal/slot gate)
  and S5 (evidence provenance: the committed JSON predates the AC5 probe rewrite and the 49-minute
  measurement was not re-run) are applied in this report and in `tests/support/t114.py`; see §2.1,
  §3.6, §3.8, §3.10. The committed gross numbers are historical (`5ffaff7`) and are not presented
  as post-fix measurements.
- **Evidence:** [`T114-capacity.json`](T114-capacity.json) (three trials, raw metrics), this
  report, harness [`test_t114_capacity.py`](../../tests/f13b/test_t114_capacity.py) +
  [`tests/support/t114.py`](../../tests/support/t114.py); raw logs under
  `E:\Orchara\.scratch\f13b\T114\`.
- **Headline:** the fixed profile (100 users, 1000 applications) was seeded through the public REST
  endpoints on each of three fresh trial databases, and 20 top-level Runs really executed at the
  same time against `platform.quota_counters('global','execution_slots').capacity = 20` while the
  queue stayed full (queue p50 ≈ 60 Runs). The independent child pool kept executing and
  completing child Runs during the saturation. The 500 ms service time is a **capacity-stub
  deployment knob**, not vendor latency.

## 1. What is stub and what is real

| item | what ran | boundary |
|---|---|---|
| profile (100 users / 1000 apps) | real `POST /api/v1/admin/users`, `POST /api/v1/auth/activate`, `POST /api/v1/applications`, `PUT /api/v1/applications/{id}/draft` over HTTP | **real executed** |
| top-level execution | one real `cmd/supervisor exec --once` OS process per attempt; claim/lease/heartbeat/result/exit over the real internal mTLS listener | **real executed** |
| graph | real Go `internal/presetexec` (ADR 0034): `start -> analyse(model) -> end` | **real executed** |
| model call | the real F06/T041 capability proxy + `internal/modelproxy` deterministic stub provider (output labelled `stub`) | **stub provider** |
| service time | `ORCHARA_CAPACITY_STUB_SERVICE_DELAY=500ms`, honoured only by the `capacity-stub` profile and only by the stub provider; rejected at startup by every other profile | **stand-in time, not vendor latency** |
| artifact finalize | real `cmd/maintenance run --kinds artifact_finalize` (4 system-io slots) | **real executed** |
| database | real PostgreSQL 16.4 isolated cluster, real `cmd/admin migrate` + `bootstrap` | **real** |
| cancel | real `POST /api/v1/runs/{id}/cancel`, real T036 arbitration/finalizer | **real executed** |
| lease recovery | real `cmd/admin runs sweep-leases --grace 1s` | **real executed** |
| real vendor model | no credential/endpoint on this host | **blocked** |
| real capacity machine / fleet | this 12-logical-CPU development machine | **not_run** |

## 2. Fixed profile, versions and the capacity knob

| item | value |
|---|---|
| git branch / commit | `t/t114-capacity-20-top` @ `5ffaff76d49a896a8239c651b947366068d5a37c` |
| python / go | 3.12.2 / go1.27.3 windows/amd64 |
| postgres | PostgreSQL 16.4 at 127.0.0.1:55432 (isolated cluster) |
| machine | Windows-11-10.0.26200-SP0; 12 logical CPUs; 15.84 GiB RAM |
| users requested / principals | 100 requested / 100 invited (`platform.principals` = 101 including the bootstrap admin) |
| applications requested / count | 1000 / 1000 |
| user ids sha256 | `bd4a8b4a3cc42046bc19912676807c1753622356a714ab2527c904b443b5ca9f` |
| application ids sha256 | `e7a6405d178fd688f0cc4569f4b424d35c240bac01028cec7719063403ebb6e8` |
| seeding path | `POST /api/v1/admin/users` + `POST /api/v1/auth/activate` + `POST /api/v1/applications` + `PUT /api/v1/applications/{id}/draft` |
| seed seconds (trial 1) | 100 users 34.84 s; 3 workload apps 0.56 s; the other 997 apps 2.11 s |
| capacity app / draft revision | `61f360df-d04d-4489-9c49-dbeafb1a87dd` r2 (one of the 1000) |
| child version | `a20cf5d5-ce4c-48c1-91ee-f9bccf440638` |
| `execution_slots` before → after | 2 → **20** |
| `child_slots` before → after | 8 → **8** (migration 0051 already seeds 8; the `UPDATE` was a no-op — no child-slot raise happened) |
| knob statement | `UPDATE platform.quota_counters SET capacity = 20 WHERE scope = 'global' AND dimension = 'execution_slots'; UPDATE platform.quota_counters SET capacity = 8 WHERE scope = 'global' AND dimension = 'child_slots'` (the second statement is the same no-op: the row is already 8) |
| service time | 500 ms stand-in (`ORCHARA_CAPACITY_STUB_SERVICE_DELAY`; capacity-stub profile only, stub provider only) |
| capacity graph / definition sha256 / run input sha256 | `start -> analyse(model, 500ms stub) -> end` / `3facf37e422f82a043bcc5110e5093e97aec6b7d189bff0ec5f980faa0c2b373` / `c486302476b3f37892fbbe25a1f316393942cd2db0bd63f2c0272b32313de071` |
| frozen manifest sha256 | `508f8e1bf7466faa4f78f35243d498d1fac45f7b852c27c2fc0461df5331dd1a` (one distinct hash per trial, as expected for a fresh snapshot per Run) |

### Experiment design

1. **Profile, per trial.** A fresh throwaway database is migrated by the real `cmd/admin` chain and
   bootstrapped. 100 creator users are invited+activated through the public admin API; 1000
   applications are created through the public application/draft API by those users — 997 trivial
   `start -> end` applications plus three workload applications (the capacity model graph, the
   `call_app` parent graph, the sealed child version). The DB row counts (101 principals, 1000
   applications) are the independent truth; the sorted user/application id hashes are in the
   evidence.
2. **Capacity knob.** `platform.quota_counters` is the established operator surface: this run sets
   `('global','execution_slots').capacity` from its migrated value **2** to **20**; the independent
   child pool is already **8** from migration 0051, so its `UPDATE` is a no-op (no child-slot raise
   happened). Before/after values and the exact `UPDATE` are in the evidence. No migration, spec or
   contract changed.
3. **Saturation.** 3 submitter threads keep a queue of `debugDraft` submissions full (target 60) on
   the one fixed capacity application revision; 24 executor threads each run a real one-shot
   `cmd/supervisor exec --once` process; a real finalize maintenance pool activates the artifacts.
   The same harness and the same scale are used in every trial.
4. **"Executing, not queued".** Every Run is submitted `queued` with no attempt. A Run only becomes
   `running` when the claim transaction allocates one of the 20 execution slots. The sampler reads
   the authoritative rows roughly every 1.5 s: `top_running`, `top_queued`, `execution_slots.used`,
   live attempts, and `queued_with_live_attempt` (a queued Run holding a live attempt would falsify
   the separation; it is 0 in every sample of every trial). Queue time (submit → first claim) and
   execution time (first claim → terminal) are computed separately.
5. **Independent second proof.** The attempt intervals of every Run created in the window are
   swept for the maximum number of live attempts at one instant, and the authoritative slot
   counter is reported next to it. The sweep reaches **21** interval-overlaps in every trial while
   `execution_slots.used` never exceeds **20** (§4 records the difference instead of hiding it).
6. **Child pool.** During saturation a `call_app` parent Run is submitted every two minutes: it is
   claimed in the top-level pool, spawns a child Run, parks in `waiting_children` and releases its
   slot; the child is claimed from the independent `child_slots` pool while the 20-slot pool is
   saturated, and the F07 terminal hook resumes the parent. Child counts, child queue/execution
   time, parent wait time and the child-attempt overlap with a 20-slot-saturated moment are
   recorded.
7. **Cancel probe.** At second 855 of each 900-second window a batch of live Runs is cancelled
   through the public API; the API response time, the persisted `cancel_requested_at` intent and the
   actual `finished_at` stop are recorded per Run, and a late worker result is attempted after the
   terminal state. A deterministic claim → cancel → exit → late-result probe runs after the window
   on the real internal protocol.
8. **Boundary probes (after the load).** A lease-expiry probe claims a Run, forces its lease into
   the past and runs the real `cmd/admin runs sweep-leases`; a non-owner read/cancel probe asserts
   the owner-only 404; the deterministic cancel arbitration asserts the T036 semantics.
9. **Short vs full.** The committed default test runs the whole harness as one **60-second** trial
   (`test_capacity_harness_short_profile`, no evidence write). In this run it was
   `1 passed in 169.61s`. The committed evidence is a single **full** run of
   `test_capacity_full_three_trials` with `F13B_CAPACITY_FULL=1 F13B_WRITE_EVIDENCE=1`: three
   independent 15-minute trials, each on a fresh database, `1 passed in 2922.75s`.

### 2.1 Evidence provenance (B2 review fix S5)

The committed `T114-capacity.json` was measured at `git_commit 5ffaff7` (2026-11-08, 49-minute
full run). Since then the **harness** changed while the product capacity path did not:

| fact | value |
|---|---|
| evidence JSON commit | `5ffaff76d49a896a8239c651b947366068d5a37c` (`T114-capacity.json` top-level `git_commit`) |
| AC5 lease-expiry probe rewritten | `c27f7e3` (`tests/support/t114.py` lease-expiry probe: real claim + forced past lease + real `cmd/admin runs sweep-leases` + recorded sweep report) |
| post-rewrite short-profile rerun on the integration tree | `21ee9a0`（`merge(b2): T114 AC5 lease-expiry probe 加固`）→ `python -m pytest tests/f13b/test_t114_capacity.py -q` **1 passed in 169.97s** |
| full 49-minute measurement after the rewrite | **not re-run** |

Why the 49-minute measurement was not re-run and what that limits:

- the probe rewrite (`c27f7e3`) changed **only** the after-window AC5 probe path; it did not change
  the capacity knob, the submission/claim/execution path or any measured quantity, and the committed
  evidence bytes for the measured window are unchanged by it;
- a full re-run costs ~49 minutes of exclusive real-stack time plus a fresh 100-user/1000-application
  seed; the B2 review window did not have that budget;
- therefore **the capacity numbers in §3 are the `5ffaff7` numbers** and are not claimed as a
  post-rewrite or post-cancel-fix measurement. The AC5 probe evidence in §3.10 is the rewritten
  probe's **short-profile** evidence on `21ee9a0` (and the B2 re-run below), not a re-measure of the
  full three-trial JSON.
- B2 review fix S5 is therefore a **documentation/provenance fix**: it records the commit split,
  the post-rewrite green rerun and the missing full re-run instead of presenting the old probe as
  current evidence. No new full-run numbers are fabricated.

## 3. Results

Every value below is a measured row from `T114-capacity.json`, not a projection. Queue time and
execution time are separate; a queued Run is never reported as executing.

### 3.1 Per-trial summary

| trial | window s | top submitted | top executed | succeeded | failed | cancelled | throughput | peak live attempts (sweep) | peak slots used (authoritative) | steady utilisation |
|---|---|---|---|---|---|---|---|---|---|---|
| trial-1 | 900 | 9388 | 9384 | 9382 | 0 | 4 | 10.43/s | 21 | 20 | 0.948 |
| trial-2 | 900 | 8622 | 8618 | 8614 | 0 | 4 | 9.58/s | 21 | 20 | 0.951 |
| trial-3 | 900 | 9048 | 9044 | 9038 | 0 | 4 | 10.05/s | 21 | 20 | 0.949 |

`top executed` = the Run has at least one real `platform.run_attempts` row; `succeeded` = the
product committed `succeeded`. The 4 cancelled Runs per trial are the queued Runs cancelled by the
cancel probe (they never held an attempt). No Run failed. 9 384 / 8 618 / 9 044 top-level stand-in
tasks therefore really executed, one real capability call each.

### 3.2 Queue vs execution (top-level; queueing is not execution)

| trial | queue p50 s | queue p95 s | queue p99 s | queue max s | exec p50 s | exec p95 s | exec p99 s | exec max s |
|---|---|---|---|---|---|---|---|---|
| trial-1 | 5.933 | 7.882 | 8.901 | 11.442 | 1.784 | 2.127 | 2.364 | 10.155 |
| trial-2 | 6.109 | 9.009 | 11.817 | 14.822 | 1.871 | 2.526 | 3.960 | 9.793 |
| trial-3 | 5.877 | 8.229 | 11.218 | 13.667 | 1.820 | 2.241 | 2.610 | 9.727 |

Queue time is submit → first claim (the Run spends it `queued` with no attempt). Execution time is
first claim → terminal Run state (claim through the 500 ms stand-in call, artifact finalize,
result/exit and the finalizer). The queue target keeps 50–90 Runs waiting at all times, so the
measured capacity is capacity-bound, not submission-bound.

### 3.3 Active histogram and the 20-slot proof

The steady-state window is seconds 10 → 855 of each trial: it excludes the warm-up and the cancel
tail (a cancelled Run that the product leaves `cancelling` holds its slot; see §4).

| trial | steady running p50/p95/max | fraction sampled at 20 | fraction sampled ≥19 | mean utilisation | queue p50 | queue p95 | queued with live attempt (max) | samples |
|---|---|---|---|---|---|---|---|---|
| trial-1 | 19 / 20 / 20 | 0.465 | 0.726 | 0.948 | 61 | 79 | 0 | 602 |
| trial-2 | 19 / 20 / 20 | 0.485 | 0.748 | 0.951 | 60 | 73 | 0 | 593 |
| trial-3 | 19 / 20 / 20 | 0.458 | 0.734 | 0.949 | 60 | 76 | 0 | 598 |

The `execution_slots.used` counter is the authoritative "executing" count: it reaches **20** and
never exceeds it (p95 = 20, max = 20 in all three trials). The 1-second state histogram reaches
20 sampled `running` Runs in every trial. The sampled count is 19 at p50 because the one-shot
executor shape leaves a short backfill gap between an attempt exit and the next claim (24 real
`exec` processes race for 20 slots); this is measured capacity, not an assumption.

### 3.4 API and stand-in call latency

| trial | submit p50 s | submit p95 s | submit p99 s | GET run p50 s | GET run p95 s | SSE ttfb s | stub call p50 s | stub call p95 s | stub call p99 s | calls |
|---|---|---|---|---|---|---|---|---|---|---|
| trial-1 | 0.134 | 0.272 | 0.356 | 0.099 | 0.188 | 0.055 | 0.516 | 0.566 | 0.609 | 9377 |
| trial-2 | 0.155 | 0.332 | 0.489 | 0.101 | 0.279 | 0.052 | 0.517 | 0.575 | 0.628 | 8611 |
| trial-3 | 0.142 | 0.291 | 0.384 | 0.114 | 0.265 | 0.115 | 0.517 | 0.571 | 0.612 | 9037 |

The stub call p50 is **0.516–0.517 s**: the configured 500 ms stand-in time plus the capability
proxy's own overhead. This is **stand-in service time, not vendor latency**.

### 3.5 Resources

| trial | API RSS p95 | API CPU p50 | API CPU p95 | finalizer RSS p50 | supervisor procs p50 | supervisor RSS p95 | PG conns p95 | PG active p95 | DB size max | disk free min |
|---|---|---|---|---|---|---|---|---|---|---|
| trial-1 | 79.0 MiB | 99.8 % | 112.7 % | 32.4 MiB | 24 | 381.8 MiB | 29 | 6 | 175.8 MiB | 25.26 GiB |
| trial-2 | 79.2 MiB | 95.5 % | 105.8 % | 46.7 MiB | 24 | 608.8 MiB | 30 | 6 | 161.5 MiB | 25.12 GiB |
| trial-3 | 34.9 MiB | 96.9 % | 108.8 % | 21.7 MiB | 24 | 373.1 MiB | 30 | 6 | 169.3 MiB | 24.98 GiB |

API RSS is the `cmd/api` process, CPU is that process on one core, supervisor RSS is the sum of the
24 one-shot `exec` processes alive at the sample, PG connections/active queries are
`pg_stat_activity` for the trial database. A ~100 % API CPU means the single API process is the
busiest component; it is not saturated across the 12 cores. The GPU, host network throughput and
multi-host resources are `not_run` (§5).

### 3.6 Errors and executor outcomes

| trial | run `error_code` | operation-attempt `error_code` | executor outcomes | non-zero exec processes | idle exec spawns |
|---|---|---|---|---|---|
| trial-1 | none | none | 9389 succeeded, 7 waiting_children, 2 error | 2 | 8725 |
| trial-2 | none | none | 8646 succeeded, 7 waiting_children, 4 error | 4 | 8487 |
| trial-3 | none | none | 9045 succeeded, 7 waiting_children, 6 error | 6 | 8856 |

The `waiting_children` outcomes are the 7 `call_app` parents parking for their child. The
`error` outcomes are exactly the cancelled Runs that hit the cancel race (§4): the supervisor
printed `status=failed` with `sandbox_failed` and the API refused its failed proposal with 409
`run_state_conflict`. No business Run failed (`runs.error_code` is empty); no operation attempt
failed.

**B2 review fix S1 (task loss is now gated).** The old harness recorded `submit_failures` and the
executor failure list but `assert_trial` never read them, so the report's "no submitted task is
lost" claim had no gate. The tightened `assert_trial` now requires:

- `result["api"]["submit_failures"] == []` (every submit was accepted by the real API);
- `result["executor"]["process_failures"] == []`;
- `result["executor"]["failures"] == []` (an executor failure is an unexplained product outcome,
  not something to filter away; if a future run has a legitimate classified failure, it must be
  added to the evidence and asserted against its real Run/terminal code instead of dropped).

The committed JSON above **predates** the fix: it contains the 2/4/6 cancel-race executor failures
(§4) and would fail the new assertion. Those rows are kept as the historical red evidence, not
rewritten. In the historical run every `submit_failures` list is empty and every executor failure
names a real `run_id` that reached a terminal state, so no submitted task was silently dropped —
but that is now enforced by the gate rather than asserted in prose.

### 3.7 Cost (stub ledger)

| trial | calls | tokens in | tokens out | charged amount | currency/note |
|---|---|---|---|---|---|
| trial-1 | 9377 | 553 243 | 65 639 | 0.00000000 | USD; the stub candidate carries no price version, so there is no charge |
| trial-2 | 8611 | 508 049 | 60 277 | 0.00000000 | same |
| trial-3 | 9037 | 533 183 | 63 259 | 0.00000000 | same |

The stub path records **real** token usage from the deterministic answer and a zero charge
(the stub candidate has no price version); this is not a vendor cost. Real model cost is `blocked`.

### 3.8 Cancel: API, intent and stop time, reported separately

Near the end of each saturated window the harness selected 6 live (`running`) and 4 `queued`
top-level Runs and issued the public cancel. Per Run it recorded the API response time/status, the
persisted `cancel_requested_at` and the `finished_at` the product actually committed. The outcome
splits cleanly:

| trial | executing selected | of those already terminal at the API call | intent recorded | intent + stop committed | intent + **stuck** (slot leaked) | queued selected | queued intent→stop |
|---|---|---|---|---|---|---|---|
| trial-1 | 6 | 4 | 2 | 0 | **2** | 4 | 4 × 0.000 s (immediate `cancelled`) |
| trial-2 | 6 | 2 | 4 | 0 | **4** | 4 | 4 × 0.000 s |
| trial-3 | 6 | 0 | 6 | 0 | **6** | 4 | 4 × 0.000 s |

- **Cancel API:** every selected Run answered **202** (accepted, not "stopped"); the client-measured
  API time is 10–210 ms per call.
- **Intent:** `cancel_requested_at` was persisted for every cancelled live Run and returned in
  `X-Run-Cancel-Requested-At`; a queued Run commits `cancelled` in the same transaction
  (intent == stop).
- **Stop:** the queued Runs stop immediately. The **executing** Runs deliberately cancelled in
  saturation did **not** stop: they remain `cancelling` with a live attempt and no `finished_at`
  (the defect in §4).
- **Deterministic live-attempt arbitration** (after the load, real protocol) shows the intended
  separation and the T036 terminal guarantee:

| trial | cancel API | state after cancel | exit confirmation | intent → stop | late worker result | state after late result |
|---|---|---|---|---|---|---|
| trial-1 | 202 | `cancelling` | 200 | **0.0267 s** | 409 `run_terminal` | `cancelled` |
| trial-2 | 202 | `cancelling` | 200 | **0.0386 s** | 409 `run_terminal` | `cancelled` |
| trial-3 | 202 | `cancelling` | 200 | **0.0294 s** | 409 `run_terminal` | `cancelled` |

**B2 review fix S4 (the AC4 gate now binds the executing-cancel case).** `stopped > 0` was
satisfiable by the queued cancels alone, and the late-result assertion accepted `cancelling`. The
tightened `assert_trial` now requires, for every Run whose cancel intent was persisted while it was
*executing*:

- terminal `state == "cancelled"` **and** a non-NULL `finished_at` within the bounded stop wait
  (the 30 s batch wait; `intent_to_stop_s` is recorded per row);
- no live attempt (`live_attempts == 0`, read independently from `platform.run_attempts`), i.e. the
  execution slot is released;
- `cancel["stuck"] == []` and `stuck_count == 0` — any stuck Run fails the gate, not just the
  report;
- queued cancels keep the immediate shape (`cancel_requested_at == finished_at`, no live attempt);
- a late result after `cancelled` must leave the Run `cancelled` (the old `cancelling` allowance is
  gone).

The committed `5ffaff7` evidence fails these assertions (2/4/6 stuck Runs, §4); it is kept as the
historical red evidence. The cancel-exit fix (`t/cancel-exit-fix`, in the integration tip) closes the
worker path; the B2 short-profile rerun on this branch records **0 stuck / 0 executor failures**
(see `product/reports/f13b/B2-REVIEW-FIXES.md`). The full three-trial JSON was not re-measured
(§2.1).

### 3.9 Independent child pool during saturation

| trial | children | children executed | parents completed | child queue p50 s | parent wait p50 s | peak concurrent child attempts | child attempts overlapping a 20-slot-saturated moment |
|---|---|---|---|---|---|---|---|
| trial-1 | 7 | 7 | 7 | 0.170 | 0.759 | 1 | 7 |
| trial-2 | 7 | 7 | 7 | 0.178 | 0.804 | 1 | 7 |
| trial-3 | 7 | 7 | 7 | 0.140 | 0.782 | 1 | 7 |

Each trial submitted 7 `call_app` parents (one every two minutes). Every parent spawned exactly one
child, every child was claimed from the independent `child_slots` pool while the 20 top-level slots
were saturated, and the F07 terminal hook resumed and completed every parent. Child attempts are
excluded from the top-level `running` counts.

### 3.10 Boundary probes

| trial | lease-expiry parked | sweep report | non-owner read | non-owner cancel | deterministic cancel | late result | final state |
|---|---|---|---|---|---|---|---|
| trial-1 | `recovery_pending` | 1 scanned, 1 parked (`--grace 1s`) | 404 | 404 | 202 → exit 200 | 409 `run_terminal` | `cancelled` |
| trial-2 | `recovery_pending` | 1 scanned, 1 parked | 404 | 404 | 202 → exit 200 | 409 `run_terminal` | `cancelled` |
| trial-3 | `recovery_pending` | 1 scanned, 1 parked | 404 | 404 | 202 → exit 200 | 409 `run_terminal` | `cancelled` |

The real T038 sweep parks a lease-expired Run in `recovery_pending` and (documented) keeps its held
job and its execution slot; that is why this probe runs after the measured window. A non-owner
cannot read or cancel another principal's Run (404). Capacity-full submission answers 202 and
queues; no submitted task is lost.

**B2 review fix S5 (probe provenance).** The lease-expiry probe was rewritten in `c27f7e3` (real
claim + forced expired lease + real `cmd/admin runs sweep-leases` + recorded sweep report); the
three-trial JSON above was produced **before** that rewrite at `5ffaff7`. The rewritten probe's
evidence is the post-rewrite **short-profile** run at `21ee9a0` (`1 passed in 169.97s`) and the B2
short-profile rerun on this branch, not a re-measure of the 49-minute JSON. See §2.1 for the full
provenance split and the reason the full measurement was not re-run.

## 4. Findings (recorded, not fixed by this ticket)

- **CANCEL-RACE-SLOT-LEAK (product defect, owner F03/T036 + F04B).** Cancelling an **executing**
  Run while its model capability call is in flight leaves the Run in `cancelling` forever: the
  post-call execution-bearing artifact/result call is refused with 409 `run_state_conflict`, the
  worker's failed proposal is refused for the same reason (`the failed proposal was refused: ...
  the run is not executing`), and the worker returns without confirming its exit. The attempt stays
  live, `finished_at` is never committed, and the execution slot is never released. Observed 2 / 4 /
  6 times (trials 1–3) out of the executing Runs that received an intent; the evidence holds the
  run ids, the persisted `cancel_requested_at`, the 202 API answers and the missing `finished_at`.
  The harness schedules this probe at second 855 and excludes its tail from the steady-state
  numbers; the capacity results stand on their own because they are measured before it. This is
  reported, not silently worked around: the deterministic claim → cancel → exit → late-result
  probe proves the intended T036 semantics still hold when the worker really confirms its exit.
  **Status update (B2, 2026-11-08): fixed.** The follow-up defect fix `t/cancel-exit-fix`
  (`reports/f13b/T114-cancel-exit-fix.md`, in the integration tip) makes every executor failure
  path confirm the attempt exit after a refused proposal; the real-stack regression
  `tests/f13b/test_t114_cancel_exit.py` is green and the tightened harness gate (§3.8) now fails on
  any recurrence. The 2/4/6 counts above remain the historical evidence of the defect.
- **PEAK-INTERVAL-OVERLAP (measurement note, not over-admission).** The attempt-interval sweep
  reaches **21** overlapping top-level attempt intervals in every trial while the authoritative
  `execution_slots.used` counter never exceeds **20** (max 20; the participants snapshot at the
  sweep's peak timestamp lists 20 Runs). The quota slot is only handed over after the exiting
  attempt's transaction commits, but the claim transaction stamps the new attempt's `started_at`
  when it inserts the attempt row; a concurrent exit can therefore commit between the insert and
  the slot hand-over, producing a sub-second interval overlap in the ledger without a second slot
  ever being allocated. The report keeps both numbers and treats the counter as authoritative for
  capacity.
- **Throughput is API/one-shot-executor bound, not stub bound.** At a 500 ms stand-in service time
  the system sustains **9.6–10.4 top-level Runs/s** with 20 slots, a 0.948–0.951 mean slot
  utilisation and a ~100 % single-core API. The gap from the theoretical 40/s (20 slots ÷ 500 ms)
  is per-Run overhead (submit/seal, claim, artifact upload + finalize, result/exit/finalizer) and
  the API's 4-connection database pool, not the model call.

## 5. Blocked / not_run

| item | state | why |
|---|---|---|
| real vendor model latency, quality, cost | **blocked** | no vendor credential/endpoint on this host; only the deterministic stub provider exists. The 500 ms is stand-in time. |
| real capacity machine / multi-host / load balancer | **not_run** | this is the 12-logical-CPU development machine with one API process and one PostgreSQL instance. |
| GPU and host network utilisation | **not_run** | no GPU; loopback network throughput is not measured by this harness. |
| a real-model candidate under `capacity-stub` | **blocked** | the profile refuses a real candidate by design (fail closed); no real model call can be claimed here. |

## 6. Commands and reproducibility

```
# default gate (short profile; no capacity window, no evidence write)
python -m pytest tests/f13b/test_t114_capacity.py -q
#   -> 1 passed in 169.61s

# the full measurement (exclusive capacity window; writes T114-capacity.json)
F13B_CAPACITY_FULL=1 F13B_WRITE_EVIDENCE=1 \
  python -m pytest tests/f13b/test_t114_capacity.py::test_capacity_full_three_trials -q
#   -> 1 passed in 2922.75s

# the capacity knob (established operator surface; no migration)
UPDATE platform.quota_counters SET capacity = 20
 WHERE scope = 'global' AND dimension = 'execution_slots';   -- migrated seed: 2 -> 20
UPDATE platform.quota_counters SET capacity = 8
 WHERE scope = 'global' AND dimension = 'child_slots';       -- migration 0051 seed is already 8: no-op

python tools/pg_dev.py status
```

The harness reads only authoritative rows (`platform.runs`, `platform.run_attempts`,
`platform.quota_counters`, `platform.operations`, `platform.operation_attempts`,
`platform.run_events`, `platform.background_jobs`, `platform.artifacts`) and the real API. Raw
logs (the short gate and all three full trials) are under `E:\Orchara\.scratch\f13b\T114\`.

## 7. Gates

| gate | result |
|---|---|
| `python -m pytest tests/f13b/test_t114_capacity.py -q` (short profile) | **1 passed in 169.61s** |
| `F13B_CAPACITY_FULL=1 F13B_WRITE_EVIDENCE=1 ... test_capacity_full_three_trials -q` | **1 passed in 2922.75s** |
| `python -m pytest tests/f13b -q` (exclusive, includes the T111 WSL/runsc suite) | **235 passed, 1 skipped in 705.22s** |
| `python -m pytest tests/f03 -q` (exclusive) | **3 failed, 200 passed in 2048.83s** — all three are pre-existing baseline failures, not caused by T114. They fail identically when run alone (`3 failed in 73.46s`): `test_t029_invariants::test_one_active_top_level_run_per_conversation` and `test_t035_migration::test_waiting_still_occupies_the_top_level_session_slot` seed `platform.runs` rows that the later F05/F07 request/conversation foreign keys now refuse, and `test_t032_pages::test_the_runs_page_is_served_with_timeline_markers` asserts a stale `runs.html` without `location.search`. T114's diff against `ae1af91` touches only `internal/config`, `internal/modelproxy`, `cmd/api/main.go` and the T114 test files, and these tests never set the capacity knob. Reported, not silently fixed, per ENV-B2 §5. |
| `go build ./...` / `go vet ./...` / `go test ./...` | exit 0 / 0 / 0 (all packages ok; 2 without test files) |
| `gofmt -l internal cmd tools` | empty |
| `python tools/sync_spec.py --check` | `no drift: spec copy and contract snapshot match the manifest` |

