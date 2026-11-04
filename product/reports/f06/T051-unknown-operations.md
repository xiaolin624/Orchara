# T051 / issue #52 — unknown external writes and evidence reconciliation

- **Task:** T051, GitHub issue #52, parent F06 ("写后丢响应时暂停核对且真实写次数不增加").
- **Branch / worktree:** `t/t051-unknown` in `E:\Orchara-wt\t051`; base `0caac63` (T050 merged).
- **Status:** `passed` for the implemented scope; explicit `not_run` items in §8.
- **Date:** 2026-11-04.
- **Environment:** Windows, real PostgreSQL 16.4 at `127.0.0.1:55432` (throwaway databases
  migrated with the compiled `cmd/admin` chain through `0032`), compiled `cmd/api` behind HTTP +
  the real internal mTLS listener, real filesystem object store with HMAC links, real T050
  approval flow, a real loopback HTTP connector endpoint that can close the connection without a
  response. Go `E:\orchara-toolchains\go\bin\go.exe` with `GOCACHE`/`GOMODCACHE` under
  `E:\orchara-toolchains`.

## 1. What is fixed

A connector **write** dispatch persists its intent (`authorized → dispatching` CAS) *before* the
HTTP request. Only the CAS winner may make the physical request. When the request was sent and
the receipt is not trustworthy (timeout, connection loss, 5xx after send, or a 2xx whose body
fails validation), the operation becomes `unknown` — never `failed`/`succeeded` — and the Run's
existing T036 `run_unknown_operations` row is recorded in the same transaction. The Run parks in
`needs_reconciliation` at attempt exit and leaves it only through the one T031 finalizer, driven
by `reconcileOperation`. No path ever resends the write.

`POST /api/v1/operations/{operation_id}/reconcile` (owner or the operation's approver,
`Idempotency-Key`):
- verifies a ready, undeleted evidence artifact owned by the owner or linked to the Run, whose
  JSON body binds to *this* operation (`operation_id`, or `logical_key` + `params_sha256`);
- `confirmed_success` → operation `succeeded`; the original receipt is kept when present, else the
  evidence artifact becomes the receipt;
- `confirmed_not_sent` / `confirmed_failure` → operation `failed` with a stable safe code;
  **no same-operation retry is authorised**, because the F06/T048 connector policy declares no
  provider idempotency key and no query/receipt lookup. That limitation is persisted on the
  operation before dispatch and disclosed by the owner API/UI;
- resolves exactly the one T036 unknown row and re-invokes the finalizer in the same transaction;
- is idempotent on the `(conclusion, evidence, Idempotency-Key)` tuple (same tuple → recorded
  answer; different tuple after the terminal write → `409 reconciliation_conflict`);
- never creates a second operation id for the same logical call.

## 2. Exact interfaces

### 2.1 Go — operation/unknown dispatch (`internal/capabilities`)

```go
// Service fields (internal/capabilities/service.go)
Unknowns UnknownOperationBridge
Reconciler ReconciliationBridge

type UnknownOperationBridge interface {
    RecordUnknownOperationTx(ctx context.Context, tx *sql.Tx,
        runID, attemptToken, operationID, reason string) (*execution.UnknownOperation, error)
}
type ReconciliationBridge interface {
    ReconcileUnknownOperationTx(ctx context.Context, tx *sql.Tx,
        input execution.ReconcileResolution) (string, error)
}

// internal/capabilities/connectors.go
const ReconciliationLimitationNoIdempotency = "no_idempotency_or_query_support"
func connectorUnknownReason(spec *capabilitySpec, mapped *Error) (string, bool)
func (s *Service) markConnectorUnknown(ctx context.Context, run *runContext,
    attemptToken, operationID string, mapped *Error, reason string) (*Result, error)

// internal/capabilities/reconciliation.go
const (
    CodeOperationNotReconcilable  = "operation_not_reconcilable"
    CodeReconciliationConflict    = "reconciliation_conflict"
    CodeEvidenceInvalid           = "evidence_artifact_invalid"
    CodeReconciliationUnavailable = "reconciliation_unavailable"
)
const (
    ReconcileConfirmedSuccess = "confirmed_success"
    ReconcileConfirmedNotSent = "confirmed_not_sent"
    ReconcileConfirmedFailure = "confirmed_failure"
)
type ReconcileOperationRequest struct { Conclusion, EvidenceArtifactID string }
type ReconcileResult struct {
    OperationID, State, Conclusion, EvidenceArtifactID string
    ReceiptArtifactID, ProviderIdempotencyKey, Limitation *string
    RunState string
    AlreadyReconciled bool
    WriteCount int
}
func (s *Service) ReconcileOperation(ctx context.Context, callerID, operationID string,
    request ReconcileOperationRequest, idempotencyKey string) (*ReconcileResult, *Error)
```

Dispatch result for a lost receipt is the contract's existing `CapabilityResult`:
`{"operation_id": ..., "state": "unknown", "data": {"error_code": "operation_unknown",
"requires_reconciliation": true, "limitation": "no_idempotency_or_query_support",
"dispatch_error_code": "connector_transport_error"}}`. A duplicate invoke of the same
operation/logical key returns the recorded `unknown` state (200) and performs no request; a
same-key/different-params invoke is `409 operation_conflict`; a connector duplicate seen while
`dispatching` is `409 operation_in_progress`.

### 2.2 Go — run-side bridge (`internal/execution`)

```go
// internal/execution/cancel.go (refactor: the record body is now callable inside a tx)
func (s *Service) RecordUnknownOperationTx(ctx context.Context, tx *sql.Tx,
    runID, attemptToken, operationID, reason string) (*UnknownOperation, error)

// internal/execution/reconcile.go
const (
    ReconciliationConfirmedSuccess = "confirmed_success"
    ReconciliationConfirmedNotSent = "confirmed_not_sent"
    ReconciliationConfirmedFailure = "confirmed_failure"
    ReconciliationNotSentCode      = "operation_not_sent"
    ReconciliationFailedCode       = "operation_reconciled_failure"
)
type ReconcileResolution struct {
    RunID, OperationID, CallerID, Conclusion, FailureCode string
}
func (s *Service) ReconcileUnknownOperationTx(ctx context.Context, tx *sql.Tx,
    input ReconcileResolution) (string, error)
```

`ReconcileUnknownOperationTx` locks the Run first (the package's fixed lock order), re-checks
owner-or-approver authority, locks/ resolves the one `run_unknown_operations` row, then calls the
*existing* `finalizeRunTx`:
- a recorded cancel intent keeps priority → `cancelled`;
- `confirmed_success` passes the worker's stored result proposal through unchanged (a stored
  succeeded proposal becomes `succeeded`; a stored failed proposal stays failed — T051 never
  fabricates a success proposal);
- `confirmed_not_sent`/`confirmed_failure` pass a synthetic `failed` proposal with the stable safe
  code, so the Run fails through the same finalizer;
- a live child or a remaining unknown keeps the Run parked (never a direct terminal write).

### 2.3 HTTP

`POST /api/v1/operations/{operation_id}/reconcile` — contract `reconcileOperation`
(`contracts/openapi.json` already carries the path/body `Reconciliation`).
- Required `Idempotency-Key`; missing → `422 validation_failed` with `details.field = "Idempotency-Key"`.
- Cookie sessions need CSRF; scoped API keys need `application:write`.
- `200` body is the contract `Accepted {id, state}` (operation id and settled state).
- Errors: missing operation `404 operation_not_found`; not owner/approver `403 forbidden`; wrong
  conclusion/evidence → `422 validation_failed`/`evidence_artifact_invalid`; unsettled non-unknown
  or already-reconciled different tuple → `409 operation_not_reconcilable` /
  `reconciliation_conflict`; terminal Run `409 run_terminal`.
- Owner ledger `GET /api/v1/runs/{run_id}/capabilities` now projects
  `requires_reconciliation`, `reconciled_by`, `reconciliation_evidence_id`,
  `reconciliation_conclusion`, `reconciliation_limitation`, `provider_idempotency_key`
  (all omitted when empty, so the pre-T051 model/tool projection is byte-for-byte unchanged).
  `attempts` remains the real physical-write count.

### 2.4 UI (`apps/web/src/runs.html`, `assets/runs.js`)

Pure helpers executed under Node: `reconcileOperationPath`, `reconciliationLimitationText`,
`reconciliationConclusionText`, `describeReconciliation`, `describeReconcileRequest`,
`describeReconcileOutcome`, `describeUnknownWrites`; DOM: `renderReconcileRow` +
`submitReconciliation`. The ledger panel now shows `结果未知（待核对）` instead of failure, the
explicit no-idempotency/query disclosure, and one reconcile row per unknown operation with
conclusion + evidence artifact inputs. After submit it reloads the ledger and shows
`真实写次数仍为 N（未增加）` (`describeReconcileOutcome.unchanged`), or an explicit abnormal
warning if the physical count increased.

## 3. Migration

`product/db/migrations/0032_operation_reconciliation.sql` (new, commented): additive nullable
audit columns on the contract `platform.operations` table —
`reconciliation_conclusion` (closed enum check), `reconciliation_key` (bounded 200),
`reconciliation_limitation` (closed check, the recorded disclosure fact). No contract column
changes meaning, no new table, no trigger, same `platform`/PUBLIC/worker privileges. `0001–0031`
were not touched; `contracts/**` and `docs/spec/**` were not touched (`sync_spec --check` no drift).

## 4. Real write-count evidence (the acceptance criterion)

Each test is a real stack: one real loopback HTTP server counts the requests it actually received
(`LostReceiptServer.records`), and PostgreSQL counts the physical `platform.operation_attempts`
rows. Observed in `tests/f06/test_t051_unknown.py`:

| scenario | endpoint requests received | operation_attempts rows | operation state |
|---|---|---|---|
| lost receipt (server closes connection after the real write) | 1 | 1 (`dispatching`) | `unknown` |
| duplicate invoke of the same operation/logical key | 1 | 1 | `unknown` (replayed) |
| same key, different params | 1 | 1 | `unknown`; response 409 `operation_conflict` |
| reconcile `confirmed_success` + replay | 1 | 1 | `succeeded` |
| reconcile `confirmed_not_sent` | 1 | 1 | `failed` (`operation_not_sent`) |
| reconcile `confirmed_failure` | 1 | 1 | `failed` (`connector_transport_error`) |
| 5xx after send | 1 | 1 | `unknown` (never failed) |

The endpoint's own log and the DB attempt ledger agree: **the real write count stays 1** across
every recovery path, and reconciliation adds zero dispatches. Raw assertions:
`connector_request_count(write_server) == 1`, `physical_write_count(dbname, operation_id) == 1`,
`len(operation_rows) == 1` (no second operation id).

## 5. Tests

- `tests/f06/test_t051_unknown.py` — 9 real-stack tests:
  1. lost receipt → `unknown`, run `needs_reconciliation` after exit, duplicate invoke no resend,
     params conflict 409;
  2. 5xx after send → `unknown` not failed;
  3. `confirmed_success` with evidence → operation `succeeded`, receipt = evidence, Run succeeds
     through the finalizer, one write, idempotent replay, conflicting conclusion 409;
  4. `confirmed_not_sent` → `failed`, no second operation/write, Run fails, fresh-key settle 409;
  5. `confirmed_failure` → Run failed, one write;
  6. non-owner 403, nothing settled;
  7. evidence refusals: missing artifact, wrong operation binding, tombstoned artifact, foreign
     owner → all `422 evidence_artifact_invalid`, still `unknown`;
  8. missing `Idempotency-Key` → 422, bad conclusion → 422;
  9. no-idempotency/query disclosure in the operation row, the dispatch answer and the owner
     ledger, with no automatic resend.
- `tests/f06/test_t051_pages.py` — 3 tests (page markers, helper presence, Node execution of the
  unknown/reconcile projections and the "write count did not increase" outcome).
- Support: `tests/support/t051.py` (`LostReceiptServer`, approve→resume setup, evidence seeding,
  reconcile client, DB/ledger read models).

## 6. Gates (raw tails, run serially)

```text
$ go vet ./...
(no output; exit 0)

$ go test ./...
ok  	orchara/platform/internal/capabilities	2.996s
...
ok  	orchara/platform/internal/workflow	2.472s
(exit 0; 0 FAIL)

$ gofmt -l internal cmd tools
(empty)

$ python -m pytest tests/f06 -q
........................................................................ [ 87%]
....................                                                     [100%]
164 passed in 582.73s (0:09:42)

$ python -m pytest tests/f03/test_t036_unknown.py tests/f03/test_t036_cancel.py -q
.....................                                                    [100%]
21 passed in 98.73s (0:01:38)

$ python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.49s

$ python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
(exit 0)
```

## 7. Stub / real split

- **Real:** PostgreSQL (migrations 0001–0032), compiled API, internal mTLS worker/supervisor
  certificates, object store bytes + HMAC links, T050 approval decision, connector egress guard and
  real HTTP request, real lost-receipt server (it receives the request and closes the connection),
  real attempt/unknown/operation rows, Node execution of the page helpers.
- **Stub / test harness:** the connector endpoint is a local loopback HTTP server standing in for
  the deployment-defined vendor endpoint (the connector guard is real; the remote side is a test
  server). The worker node execution is not exercised: the test drives the documented internal
  capability protocol directly.
- **No fake lost-receipt success:** the dropped response is a real TCP connection close, not a mock
  of the Go client.

## 8. not_run / boundaries (honest)

- A real vendor connector with a provider idempotency key or a query-by-receipt API: no such
  connector shape exists in F06/T048, so `confirmed_not_sent` never performs the "one safe
  same-operation retry" branch; it fails and discloses the limitation instead. The retry branch is
  deliberately not invented.
- A distinct approver (≠ owner) reconciling: the authority check supports the recorded approver
  (`platform.approvals.approver_id`), but T050 always makes the Run owner the approver, so the
  distinct-approver path is not demonstrated end-to-end.
- `needs_reconciliation → queued` requeue/resume of the Run after `confirmed_success`: T038's
  `recover` command is the only requeue authority; T051 only settles through the finalizer
  (a stored succeeded proposal commits `succeeded`; otherwise the Run stays parked for the
  operator's existing recover/cancel path).
- A real browser DOM: the page helpers are Node-tested; no browser driver.
- Automatic retry/sweeper of any kind: intentionally absent.
- Full `pytest tests` / f00b/f01/f02a/f04a/f05 suites.
- No push, no issue close. No edits to `docs/spec/**`, `contracts/**` or migrations `0001–0031`.
