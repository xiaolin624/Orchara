# T045 / issue #46 — compatible fallback after availability faults

Status: **`passed` within this ticket's scope** (stub provider only; no real vendor
adapter is compiled in this build). Real-stack evidence comes from real PostgreSQL
(migration chain 0001–0027), the compiled `cmd/api`, the real worker mTLS listener,
real sealed object bytes and the real owner HTTP/SSE surfaces. The model provider is
the documented dev stub (`provider=stub`, every reply labelled `stub=true`); a
profile's registered `model_id` selects a deterministic fault. The real-vendor path
is still honestly `adapter_unavailable`, never a fabricated success.

Worktree `E:\Orchara-wt\t045`, branch `t/t045-fallback`, base `53b563a` (T044 merge).
No push, no issue close; `docs/spec`, `contracts/` and migrations 0001–0026 were not
modified. One migration was added: `0027_run_operation_fallback.sql` (T044 left 0027
unused).

## 1. Delivered behaviour

### 1.1 Fallback closure and readiness (`internal/catalog`)

- The only fallback source is the **primary profile row's**
  `ModelConfig.fallback_profile_version_ids` (the contract field modelregistry already
  validates). It is never read from worker params.
- `collectDependencyRefs` remains the structural walk of the draft document; the
  expansion lives in `checkDependencies`, which has the loaded primary rows:
  `fallbackModelRefs` adds every declared fallback as another `dependencyModel` in the
  same closure. The located pointer stays the primary's document field
  (`/nodes/<i>/config/model_profile_version`) and the message names the fallback id and
  its source primary, because the list lives on the profile row, not in the draft.
- Each fallback passes the same `modelReadinessProblem`: it must exist, be
  `validation_state=passed` and not revoked. A pending/revoked/missing fallback is
  located as `dependency_not_ready`/`dependency_revoked`/`dependency_not_found` and
  no manifest is produced (`tests/f06/test_t045_fallback.py::test_unpassed_fallback_is_refused_at_compile`).
- `execution_manifest.model_profile_versions` therefore freezes primary + fallbacks.
  T041's manifest allowlist accepts them; a later DB config edit cannot add an
  unfrozen profile at dispatch time (`test_fallback_not_in_the_frozen_manifest_is_refused`).

### 1.2 Compatibility gate (`internal/capabilities/fallback.go`)

Pure functions, no database/provider/clock:

- same `kind` and `protocol`, and the pair is `generation`/`chat_compatible`;
- fallback `context_window` ≥ required tokens = estimated input (canonical message
  bytes / 4, the same conservative rule as the reservation) + requested
  `max_output_tokens`;
- fallback `max_output_tokens` ≥ requested value;
- a fallback that explicitly declares `tools: false` never serves a call that declares
  tools; one that declares `structured_output: false` never serves a call with a
  `response_schema`. The contract `ModelConfig` has no such fields, so absence means
  "allowed" and only an explicit `false` refuses — the fail-closed direction for the
  only information the platform actually stores.
- fallback must be in the run manifest, `passed`, unrevoked, have a dispatchable
  provider, and its conservative reservation must fit
  `runs.budget_limit` minus this run's known `reserved_amount`/`charged_amount`
  (T046 owns the full ledger; T045 only refuses a fallback that cannot be paid for).

### 1.3 Dispatch (`internal/capabilities/service.go`, `stream.go`)

- Primary retry is unchanged: at most 3 physical attempts, Go-only.
- After the final availability fault, the first compatible fallback in configured
  order gets exactly one physical attempt (`attempt_no=4`). Non-transient/permission/
  schema/tool-argument/policy failures terminate immediately: no retry, no fallback
  (`test_permission_error_is_neither_retried_nor_fallen_back`, one attempt row).
- If no fallback passes every gate, the primary's typed failure is returned unchanged
  and no fallback attempt row exists.
- The streaming path uses the same gates: after 3 failed primary generations the
  compatible fallback answers in a **new generation**; the primary's text/tool
  fragments are never concatenated with the fallback's, and the final frame/receipt
  come only from the fallback generation (`test_streaming_call_switches_in_a_new_generation`).

### 1.4 Ledger, receipt and owner UI

- Migration 0027 adds `platform.operations.fallback_profile_version_id` (FK to
  `model_profile_versions`) and `fallback_reason` with a closed check
  (`availability|rate_limited|adapter_unavailable`) plus a pair check (both NULL or
  both set). `capability_version` keeps its contracted meaning: the version the worker
  requested (the primary).
- On a switched success the operation row stores the fallback id + reason; the sealed
  receipt carries `fallback: {primary_profile_version_id, profile_version_id, reason}`.
- The owner ledger projection (`GET /api/v1/runs/{id}/capabilities`) adds
  `fallback_profile_version_id`, `fallback_reason` (omitted when unused),
  `tokens_in`, `tokens_out` and `charged_amount` (sum over every physical attempt).
  It still carries no params and no receipt body.
- `/runs` capability panel: the attempts cell shows attempts plus summed cost/tokens;
  the source cell shows "已切换到 <profile>，原因：<closed reason>" for a switched call.
  `describeCapabilityOperation` exposes `fallback`, `fallbackText`, `costText`,
  `attemptsDetailText`; `capabilityFallbackReasonText` maps the closed reason
  vocabulary. The page hint documents the new projection.

### 1.5 Stub fault seam (`internal/modelproxy`)

Deterministic, candidate-owned (not worker-supplied params), because the primary and
the fallback of one call must be able to answer differently:

| registered `model_id` prefix | stub answer |
|---|---|
| `stub-unavailable*` | bounded-transient availability fault |
| `stub-rate-limited*` | bounded-transient rate-limit fault (`rate_limited`) |
| `stub-adapter-down*` | `ErrAdapterUnavailable` (`adapter_unavailable`) |
| `stub-permission-denied*` | terminal provider refusal (`provider_error`) |
| `stub-schema-error*` | terminal schema refusal (never retried/fallen back) |

A normal model id (e.g. `stub-model`) is unaffected. The stream stub calls the same
path, so streaming faults are equally deterministic.

## 2. Tests

### 2.1 Go unit (new)

- `internal/capabilities/fallback_test.go` (5 tests): compatibility matrix
  (kind/protocol/context/output/tools/schema, generation-only primary), reason
  classification (transient/rate-limited/adapter/not-configured vs permission/schema/
  tool-argument), declared fallback list parsing, required-token estimate.
- `internal/catalog/fallback_test.go` (4 tests): closure expansion order + kept
  primary pointer/source, unknown primary/tool refs not expanded, config fallback id
  parsing (invalid entries kept for located error), fallback readiness rule reuse.

### 2.2 Real stack `tests/f06`

`test_t045_fallback.py` (9 tests, real PG + compiled binary + worker mTLS + object
bytes):
compatible fallback end-to-end (manifest freeze, 4 attempts, reason/profile, one
receipt, ledger cost/tokens, no params), rate-limited reason, incompatible protocol
refused, permission error single attempt, fallback not in frozen manifest, unpassed
fallback refused at compile, budget exhaustion, tools-incompatible fallback,
streaming fallback in a new generation.

`test_t045_pages.py` (3 tests): runs.js fallback helpers present; `/runs` hint;
pure helpers executed under Node (fallback label, closed reason text, summed
cost/tokens, non-switched call has no fallback/cost).

`tests/f06/test_t041_capabilities.py` was updated for the extended ledger key set
(the projection legitimately grew; non-fallback entries carry no fallback keys).

### 2.3 Gate results

See §5 for the raw tails. `tests/f02a` (compiler/dependency regressions) and
`tests/tooling` are green.

## 3. Interfaces for T046 (budget/settlement)

- **Operation columns**: `platform.operations.fallback_profile_version_id` (uuid,
  FK), `fallback_reason` (text, closed vocabulary, pair with the profile id).
  `capability_version` is the requested primary; the fallback never rewrites it.
- **Attempt ledger** remains the cost authority: every primary retry and the single
  fallback attempt are `platform.operation_attempts` rows with `reserved_amount`
  (pre-dispatch conservative reservation) and `charged_amount`/`tokens_in`/
  `tokens_out` (on success). T045 sums them; T046 should settle/release reservations.
- **Budget seam**: `(*capabilities.Service).budgetAllowsFallback(ctx, runID, reserved)`
  is the single place T045 reads budget. It currently checks
  `runs.budget_limit - Σ dispatching reserved - Σ charged >= reserved`. Replace its
  body with T046's ledger query; the caller only needs a bool (fail closed on error).
- **Fallback selection seam**: `selectFallback(ctx, run, spec, params, reason)`
  returns `{Profile, Reason}` or nil; the compatibility rule is the pure
  `checkFallbackCompatibility(primary, fallback, *ModelParams) string` ("" = allowed).
  T046 can reuse it for budget-aware ordering without changing the gate vocabulary.
- **Owner projection**: ledger JSON fields `fallback_profile_version_id`,
  `fallback_reason`, `tokens_in`, `tokens_out`, `charged_amount`; UI helpers
  `describeCapabilityOperation`, `capabilityFallbackReasonText`.
- **Receipt facts**: `fallback: {primary_profile_version_id, profile_version_id,
  reason}` inside the sealed capability receipt.

## 4. Stub / real split

- **Real**: PostgreSQL (0001–0027), compiled `cmd/api`, worker mTLS certificates, run
  attempt leases, sealed manifests/receipts in the object store, public owner HTTP and
  the compiled web assets served by the API.
- **Stub**: the model provider (dev stub; deterministic faults by registered
  `model_id`). No vendor adapter exists, so the "real provider" branches remain the
  documented `adapter_unavailable`/`provider_not_configured` failures. Those are
  covered at the unit level, not by a real vendor call.

## 5. Gates (raw tails)

Run from `product/` with `GOCACHE=E:\orchara-toolchains\gocache`,
`GOMODCACHE=E:\orchara-toolchains\gomodcache` and
`E:\orchara-toolchains\go\bin\go.exe`.

- `go vet ./...` — exit 0, no output.
- `go test ./...` — every package with tests passed (24 `ok`, 0 `FAIL`); tail:
  `ok orchara/platform/internal/capabilities 2.158s`, `ok orchara/platform/internal/catalog (cached)`,
  `TEST_EXIT:0`.
- `gofmt -l internal cmd tools` — empty output.
- `python -m pytest tests/f06 -q` — `96 passed in 479.53s (0:07:59)` (the whole F06
  suite, not only T045; T045 itself is 9 real-stack + 3 page tests).
- `python -m pytest tests/f02a -q` — `104 passed in 726.21s (0:12:06)`.
  Three stale assertions in `test_t023/t025/t027_migration.py` pinned the chain
  tail at 0024 and were already red at base (T041/T042 appended 0025/0026); they now
  assert contiguous numbering from 0001 plus each ticket's own migration name, so
  the suite tests the invariant instead of a rotted tail. This is the only
  out-of-ticket edit and it changes tests only.
- `python -m pytest tests/tooling -q` — `21 passed in 6.68s`.
- `python tools/sync_spec.py --check` — `no drift: spec copy and contract snapshot match the manifest`.

## 6. Honest not_run / boundaries

- **Real vendor adapters**: not compiled, therefore a real primary/fallback switch is
  `not_run`; only the stub seam is exercised end to end.
- **Browser DOM driving**: `not_run` (same boundary as every page ticket); the page
  projection is pure helpers executed under Node, plus the compiled assets served by
  the API.
- **Multi-process/streaming preview fan-out**: unchanged from T044 (`not_run` for
  multi-process).
- **Budget settlement/release**: owned by T046; T045 only checks the remaining
  `runs.budget_limit` before a fallback.
- **Full `pytest tests` (f00b/f03/f05)**: not run, per the ticket boundary (only the
  listed suites were run).
- Connector capabilities and cross-owner fallbacks remain out of scope.
