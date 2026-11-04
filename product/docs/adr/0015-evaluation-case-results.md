# ADR 0015 — Evaluation case results: fixture binding and the per-case read

- Status: Accepted (F13A/T056, issue #57)
- Date: 2026-11-04
- Extends: ADR 0011 (the T052 run bridge), ADR 0005 (non-contract bridge
  precedent: `tool-runs`), ADR 0004 §31 (T026 evaluation pipeline)

## Context

T052 built the real PlatformTaskRunner and the sealed per-case report, and
recorded one honest gap: a case could not bind a caller's CSV/XLSX as the run's
`task_input` artifact, so a sales/tool case could never reach `succeeded`. T052
could only prove a real terminal success with a predefined `cancel` interaction.

T056 closes that gap and adds the creator-facing per-case read. Three contract
limits shape the design:

1. `contracts/evaluation-case.schema.json` describes a case's `input` as an
   object with `additionalProperties: true`, but it has **no assertion kind for a
   run's error code**. A bad-input case therefore cannot express "the run must
   fail with `missing_column`" using contract assertions alone.
2. A committed dataset cannot embed a volatile artifact id: the fixture is
   uploaded per evaluation, and its id is generated at upload time.
3. The contract's `Report` has four fields and no per-case detail, and there is
   no contract operation to read the sealed report document.

## Decision

Three additive, documented non-contract extensions. The public contract and
`docs/spec/**` are untouched.

### 1. Content-addressed `task_input` binding (reserved case-input member)

Inside the contract-legal `input` object a case may carry:

```json
"input": {
  "source": "sales-happy-csv",
  "task_input": {"sha256": "<64 hex>", "media_type": "text/csv"},
  "expected_failure": {"error_code": "missing_column"}
}
```

`task_input` is validated at submit time (strict shape, sha256 hex, CSV/XLSX
media type only). The collector forwards the binding over the existing internal
bridge; the server (`execution.EvaluationRuns.Create`) resolves it **by content**
among the evaluation owner's own ready `task_input` artifacts:

- owner = the evaluation owner, `purpose='task_input'`, `state='ready'`, not
  deleted, `media_type` equal, `sha256` equal;
- the scan is deterministic: earliest `(created_at, id)` wins when the same bytes
  were uploaded more than once, and the chosen artifact id is recorded in the
  case facts (`task_input_artifact_id`/`task_input_sha256`);
- no match is a located `422 task_input_unavailable`, recorded as
  `blocked/task_input_unavailable`. The case never invents a run.

The server links the artifact to the case run as `run_artifacts(relation='input')`
in the same transaction that creates the run, exactly like a debug submission's
`artifact_ids`. The collector strips the reserved members (`interactions`,
`task_input`, `expected_failure`) from the sealed run input; platform control
data is never part of the application's input. The bytes still move only through
the run-scoped signed link, size- and sha256-verified by the supervisor.

### 2. `expected_failure` (reserved case-input member, evaluated by the checker)

Because the case contract cannot express a run error code, a bad-input case
declares the failure it expects in `input.expected_failure.error_code`. The same
`internal/evaluation` checker evaluates it against the run's *platform* state and
`error_code` (never stdout, never a log line) and appends one outcome of kind
`failure_reason` to the case's assertions array. A passed bad-input case
therefore requires: `run_state=failed`, the exact expected error code, and every
other assertion (for example `effect_count=0` on the real ledger).

The dataset remains valid against `contracts/evaluation-case.schema.json`, since
`input` allows extra members. The outcome is additive evidence in the sealed
report document (which is not a contract shape).

### 3. Per-case results read (non-contract HTTP)

```
GET /api/v1/evaluation-reports/{report_id}/cases   -> 200 per-case results
```

- owner-or-admin; every other principal gets **404** (same answer as a missing
  report, so ids cannot be enumerated);
- an API key needs `application:read`; a key without it is refused
  `403 insufficient_scope` before any ACL decision;
- the body is parsed from the **sealed report artifact's real bytes**, re-verified
  against the artifact row's SHA-256 before decoding; a tampered/unreadable
  report is an internal error, never rendered as case data;
- each case carries: case id, verdict, reason code/detail, the per-assertion
  outcomes (`kind`, `pointer`/`capability`, `expected`, `actual`, `passed`,
  `detail`), the collected facts summary, and the **artifact ids** a user may
  download;
- the response contains **no object key, staging key or signed URL**; the client
  exchanges an artifact id through the existing owner-only
  `GET /api/v1/artifacts/{id}/download`;
- repeated reads are byte-identical: the report document is immutable.

Attachments are resolved from the platform's own rows for the case's run:
`run_node_outputs` (the tool's data/file outputs, e.g. `summary.json` /
`summary.csv`), `runs.output_artifact_id`, and the registered failure evidence
(`diagnostics`). Logs are deliberately excluded (operator evidence, not run
data), matching the run-artifact read's rule.

### 4. Failure evidence is linked to the failed run

To make the "actual attachment" of a failed tool case a real platform artifact,
`internal/presetexec` now registers the safe log and diagnostics document it
already uploaded on the linear/legacy failure paths
(`run_artifacts(relation='log'/'artifact')`). The bytes and the admission rules
are unchanged; only the run link is added. This is the only change to the preset
tool path.

## Consequences

- The public contract is untouched: `python tools/sync_spec.py --check` stays
  clean, and no migration is needed (the existing `run_artifacts`,
  `run_node_outputs`, `evaluation_case_runs` and `artifacts` rows carry all of it).
- The completion guard is still the only writer of a report row. The new read
  never writes.
- Committed datasets stay runnable and self-describing: they name the fixture by
  content, never by an object key or a volatile id. The timeout/over-limit
  fixtures too large to commit are generated deterministically in the test tree;
  their SHA-256 is frozen in the dataset.
- **Contract debt to record** (unchanged from ADR 0011): `evaluateDraft` is
  authorized with `application:write`; the read piggybacks on
  `application:read`. A dedicated evaluation scope remains a later contract
  change.
- An expectation kind for a run's business error code is still absent from the
  case contract; `expected_failure` is the non-contract spelling. A future
  contract revision should either add an error-code assertion or adopt this
  member.
