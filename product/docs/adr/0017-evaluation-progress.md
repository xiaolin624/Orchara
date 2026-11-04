# ADR 0017 — The asynchronous evaluation progress read

- Status: Accepted (F13A/T065, issue #66)
- Date: 2026-11-04
- Extends: ADR 0004 (evaluation pipeline boundaries), ADR 0011 (the evaluation run
  bridge and `evaluation_case_runs`), ADR 0014 (the administrator attribution read
  and the `terminal`/`completed` vocabulary)

## Context

The contract's evaluation surface is fixed at three operations: `evaluateDraft`
(202 → six-field `EvaluationJob`), `getEvaluation` (the same six fields) and
`getEvaluationReport` (four fields: `id/status/manifest_sha256/
result_artifact_id`). None of them says how far an asynchronous evaluation has
got, and the report only exists once the run is over. Ticket #66 needs a creator
to be able to refresh progress and, when the evaluation stops, to tell two very
different outcomes apart:

- a **business** result: the sealed report says the evaluated application failed
  (`status=failed`, or `not_run` when no case could be decided) while the
  evaluation job and the evaluation row both succeeded;
- an **infrastructure** failure: the job failed, so no report exists at all and
  `report_id` stays empty.

Two existing facts were not enough for that:

1. `evaluation_jobs.progress_total/progress_done` (0035) were only recomputed
   from a case run's observed state. A case the runner decides *without* a run
   (an unsupported target, a refused collection) never moved the counter, and a
   case *verdict* (passed/failed/blocked/not_run) was not representable at all:
   a cancelled run with a failed verdict could only show as `cancelled`.
2. No persisted count of failed cases existed, so the read would have to parse
   the sealed report on every poll — or invent a number.

The hard constraints are the same as every F13A ticket: the public contract and
`docs/spec/**` must not change (`python tools/sync_spec.py --check` stays clean),
the completion guard remains the only writer of a report row, and a maintenance
handler still may not touch platform tables directly.

## Decision

### 1. Persist the runner's per-case decision (non-contract migration)

`0039_evaluation_progress.sql` (originally `0038`; renumbered in the integration
merge when T060/F10 took `0038_pdf_admission.sql`) adds:

- `platform.evaluation_case_runs.verdict text` — the runner's closed decision
  vocabulary `passed|failed|blocked|not_run`. `state` keeps its meaning (the
  observed run state, or `blocked` when no run was created), so a cancelled run
  with a failed verdict keeps both facts.
- `platform.evaluation_jobs.progress_failed integer NOT NULL DEFAULT 0` — how
  many cases the runner decided as failed.
- `progress_done` is redefined in one SQL expression used by every writer: a
  case is decided when `verdict IS NOT NULL` **or** its run reached a rest state
  (`succeeded/failed/cancelled/recovery_pending/needs_reconciliation`). The run
  creation hook, the facts hook and the runner's progress report therefore never
  lower the counter on a retry.

### 2. The runner reports each decision through a job-scoped bridge

```
POST /internal/v1/background/jobs/{job_id}/evaluation-progress
     {attempt_token, case_id, ordinal, verdict, reason_code}  -> {recorded: true}
```

- maintenance-capacity only (mTLS role + live attempt token, `withJobData`),
  exactly like the F13A/T052 run bridge;
- the server re-proves the evaluation owner's current access
  (`loadEvaluationScope`: application still owned by the owner and not archived,
  snapshot owner matches) and never trusts a run/owner/verdict authority from the
  handler's body beyond the decision it made;
- the write is an idempotent upsert on `(evaluation_id, case_id)` and recomputes
  the evaluation's counters; the dataset ordinal comes from the runner and is
  stored on the row;
- progress is a **live convenience, not the verdict authority**: a refused
  progress report never aborts the evaluation, because the sealed report remains
  the record of truth.

### 3. The completion guard writes the final counters from the sealed report

In the same transaction that records the accepted report, the guard writes
`progress_total/progress_done = GREATEST(current, report case count)` and
`progress_failed` = the report's failed-case count. A retried attempt whose live
reports were interrupted therefore still ends with the report's own truth.

### 4. The read

```
GET /api/v1/evaluations/{evaluation_id}/progress   -> 200 progress
```

Authorization is exactly the contract evaluation read's: owner or administrator;
everyone else gets `404 resource_not_found` (the same answer as a missing row, so
ids cannot be enumerated), and an API key needs the registered
`application:read` scope before the ACL runs (a write-only key is
`403 insufficient_scope`).

The projection is deliberately small and honest; every field is a persisted
platform fact:

| field | meaning |
|---|---|
| `evaluation_id` | the evaluation row's id |
| `state` | `evaluation_jobs.state` (`queued/running/succeeded/failed/cancelled`) |
| `report_status` | the immutable report's own status (`passed/failed/not_run`), `null` until it exists |
| `report_id` | `null` until the completion guard records a report |
| `total_cases` | the dataset's case count frozen at submission |
| `completed_cases` | cases with a decision (see above); includes `not_run`/`blocked` decisions |
| `failed_cases` | cases the runner decided as failed |
| `error_code` | `evaluation_jobs.error_code` (a platform code only; a business verdict has none) |
| `failure_class` | `business`, `infrastructure` or `null` (rules below) |
| `created_at`, `finished_at` | the evaluation row's real timestamps |

**No percentage is ever derived from `state`.** The counters are persisted case
decisions; a queued evaluation honestly reads `0/N`.

The failure class is the ticket's distinction, and it is a function of *the
report's existence*, not of the job's retry classification:

| `failure_class` | condition | meaning |
|---|---|---|
| `business` | a report exists and its status is `failed` or `not_run` | the platform completed the evaluation; the report is a verdict about the evaluated application. `not_run` is a report status, never a job state. |
| `infrastructure` | no report exists and the evaluation state is `failed` | the job failed, so the platform produced no verdict at all; `report_id` is empty |
| `null` | queued/running, cancelled, or a `passed` report | — |

This is deliberately **not** the background job's own `failure_class` (the
retryability axis stored on `background_jobs.failure_class`). A deterministic
platform refusal (`source_deleted`) and a transient outage
(`transient_storage_io`) both leave the evaluation without a report, and from the
creator's point of view both are "the task failed, there is no verdict". The
job's own class remains visible on its contract read. The report documents both
semantics rather than conflating them.

### 5. The page

`GET /evaluations/{evaluation_id}` (+ `/assets/evaluations.js`) is a real client
of the progress read and the contract report read:

- the evaluation id is in the URL path, so refresh is the same shareable view;
- while `queued/running` the page polls; a terminal state stops the timer;
- it states the business-vs-infrastructure distinction in plain language, renders
  `not_run` explicitly as a report status, and shows the empty `report_id` state;
- the report section renders only the four contract fields, re-reads on demand
  and compares snapshots: re-opening the same report shows identical content.

The application debug page gains a small entry card that turns a submitted
evaluation id into that page's URL (the creator is already there). The page logic
is pure and executed by Node in `tests/f13a/test_t065_progress.py`, the same page
boundary every page ticket records (no browser DOM is driven).

## Consequences

- The public contract is untouched; `python tools/sync_spec.py --check` stays
  clean. The progress read, the bridge and the two columns are documented
  non-contract extensions (this ADR, the T065 report and the migration header).
- The completion guard stays the only writer of a report row; the progress
  bridge writes case decisions and counters only, never a report.
- A retried attempt is idempotent per case and the terminal counters come from
  the sealed report, so a live progress read can lag but can never fabricate a
  verdict.
- `progress_done`'s "decided" expression lives once in
  `internal/execution/evaluation_runs.go` and is used by all writers; a later
  ticket that adds a progress writer must reuse it.
- The terminal counters are written by the guard even when the live bridge was
  interrupted; the read is therefore honest for every accepted report.
- T056 (the creator per-case page) can render per-case results from the sealed
  report as before; if it also reads `evaluation_case_runs`, `verdict` now
  carries the runner's decision independently of the observed run state.
- Migration numbering: the file was `0038` on the ticket branch and became
  `0039` in the integration merge when T060/F10 took `0038`; the migration
  protocol says the merger renumbers rather than reusing a version.
