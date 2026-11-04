# ADR 0008 — The evaluation runner's job-scoped run bridge

- Status: Accepted (F13A/T052, issue #53)
- Date: 2026-11-04
- Supersedes/extends: ADR 0004 §31 (T026 evaluation pipeline), ADR 0005 (non-contract
  bridge precedent: `tool-runs`), ADR 0006 (job-scoped tool-build binding, if present)

## Context

`contracts/openapi.json` fixes the evaluation surface at three operations:

| operation | path | shape |
|---|---|---|
| `evaluateDraft` | `POST /api/v1/applications/{application_id}/evaluations` | body `{draft_revision, dataset_artifact_id}`, `Idempotency-Key` required, 202 → `EvaluationJob` |
| `getEvaluation` | `GET /api/v1/evaluations/{evaluation_id}` | 200 → `EvaluationJob` (6 fields) |
| `getEvaluationReport` | `GET /api/v1/evaluation-reports/{report_id}` | 200 → `Report` (4 fields) |

The contract has no case shape, no progress fields and no way for a submission to
name a run. T026 built the surrounding pipeline (dataset artifact, sealed report,
completion guard) with a *synthetic* evaluator and no per-case execution. T052
must run each case through the real platform and decide it with an independent
checker.

Two seams are missing for that, and both are deliberate in this build:

1. **A maintenance identity may not create a user's run.** `cmd/maintenance`
   holds a maintenance mTLS certificate and job-scoped endpoints only; it has no
   database credential for business tables and no right to act as a principal.
   There is no contract operation "create a debug run of this frozen snapshot as
   its owner".
2. **The contract's `Report` cannot carry per-case detail**, so per-case results
   belong in the immutable sealed result artifact (already true since T026).

## Decision

Add a non-contract, **job-scoped, maintenance-capacity** bridge on the internal
mTLS listener, documented here and in the T052 report. It is the same precedent
as ADR 0004's `POST /internal/v1/background/jobs/{job_id}/evaluation` and ADR
0005's `tool-runs`:

```
POST /internal/v1/background/jobs/{job_id}/evaluation-runs
     {attempt_token, case_id, input}          -> 200 {run_id, created}
POST /internal/v1/background/jobs/{job_id}/evaluation-runs/{run_id}/facts
     {attempt_token}                          -> 200 independent truth
POST /internal/v1/background/jobs/{job_id}/evaluation-runs/{run_id}/interactions
     {attempt_token, interaction}             -> 200 {run_id, run_state}
```

Binding rules (all enforced server-side; the handler supplies only its lease
proof, a case id and the case's canonical input):

- **Authentication**: maintenance mTLS role + live attempt token, exactly like
  every other job-scoped route (`withJobData`). A worker or supervisor
  certificate is refused 403.
- **Ownership**: the job must own the evaluation (`background_job_id`,
  owner match). The evaluation must be `kind=application`.
- **ACL, re-proved on every call**: the application must still belong to the
  evaluation owner and must not be archived; the execution snapshot's owner must
  equal the evaluation owner. A withdrawn application or a foreign snapshot
  answers `409 evaluation_not_visible`; the case is then recorded as
  `blocked/collection_refused`, never collected from a cached authorization.
- **The run is the platform's**, not the handler's: the server seals the case
  input as a `run_input` artifact owned by the evaluation owner, inserts the
  `runs` row (`kind=debug`, the evaluation's frozen snapshot, the snapshot's
  manifest hash and policy), its `jobs(kind=start)` row, its `run_artifacts`
  link, its first `run.status` event and its `evaluation_case_runs` row — all in
  one transaction. It creates **no** new execution semantics: the run is claimed
  and executed by `cmd/supervisor exec` exactly like a user debug run.
- **Facts are independent truth**: the run state, the run's sealed output
  artifact (hash re-verified), the real `platform.operations` ledger counts and
  the citation source ids the output declares. The model trace is never read.
- **Interactions are real transitions**: `cancel` calls the same
  `execution.Service.Cancel(owner, run)` a user's `POST /runs/{id}/cancel` calls.
  This build performs exactly one interaction; anything else is an explicit
  refusal (`422 validation_failed`).

## Consequences

- The public contract is untouched; `python tools/sync_spec.py --check` stays
  clean.
- The completion guard remains the only writer of a report row. The bridge
  writes runs and case rows, never a report.
- `evaluation_case_runs` (migration 0033) is the durable link between a case and
  its platform run, which is also what T056 (per-case UI), T062 (ownership /
  cancellation integrity) and T065 (progress) need.
- **Contract debt to record**: `evaluateDraft` is authorized with
  `application:write` for API keys (the reads piggyback on `application:read`);
  the contract text says "scoped API keys require object ACL" without naming a
  scope. A dedicated evaluation scope is a later contract change.
- **Not expressible in this slice**: a case cannot name a caller artifact, so a
  sales/tool case (which needs a CSV `task_input`) cannot be *succeeded* through
  this bridge yet. That is #57's (T056) boundary, with the exact reason recorded
  in the T052 report; nothing fakes it.
