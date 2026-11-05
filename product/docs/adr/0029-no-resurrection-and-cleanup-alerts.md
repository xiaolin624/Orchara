# ADR 0029 — No-resurrection regression, cleanup alerts and the component deletion gate

- Status: Accepted (F11/T076, issue #77)
- Date: 2026-11-05
- Extends: ADR 0020 (the tombstone is the read authority), ADR 0027 (copy cleanup +
  hash-chained ledger), ADR 0028 §6 (no tracer/metrics SDK in this build), ADR 0009
  (the T066/Q45 Mem0 adoption gate)
- Numbering: the orchestrator reserved 0029 for this ticket. No migration is needed:
  every fact this ticket reads already exists (`platform.deletion_jobs`,
  `platform.background_jobs`, `platform.deletion_ledger`,
  `platform.diagnostic_grants`); the next free migration number on this branch is
  0050 and this ticket does not consume it.

## Context

F11 delivered a logical delete that commits before the 202 (T069), a physical purge
(T071), copy classes + a hash-chained, independently retainable deletion ledger
(T072), and scoped diagnostic grants (T073). T066 committed `do_not_adopt` for Mem0,
with the F11 gate as the reason. What no ticket delivered is the *adversary's* view:
replay late ingestion/finalize, restore an old checkpoint/backup copy, or recall a
component's history **after** a deletion, and prove nothing readable comes back —
plus an operator signal when a cleanup never finishes.

Three concrete gaps were found by reading the authoritative predicates:

1. `execution.Recover` re-checked only `runs.input_artifact_id`. The request path
   (`internal/requests/submit.go`) links the sealed message **and each attachment**
   as `run_artifacts.relation='input'`, and the supervisor discovers a Run's inputs
   through that relation. A deleted attachment therefore passed recovery.
2. A `delete_content` attempt that expired before it could read its scope set was
   reported as `transient_internal`, and the completion guard then rewrote the
   attempt code to `transient_storage_io` because the bytes were still present. The
   timeout was invisible to an operator.
3. The T066 gate keyed on one post-delete enumeration
   (`cleanup.residual_body_copies`). A component that keeps an opaque store with no
   deletion path passed when that one enumeration happened to be empty — the exact
   "cannot satisfy deletion" case Q45/F11.05 forbids adopting.

## Decision

### 1. "Input" means every input artifact, not only the primary one

`recovery_store.go` now derives the full input set: `runs.input_artifact_id` first,
then every `run_artifacts` row with `relation='input'` (sorted, deduplicated), and
`checkInputArtifact` + the locked `recheckRecoveryArtifactRows` verify **each** one
(ready, not deleted, hash/size present, sealed bytes match). Any failure is the
existing `input_deleted` refusal naming the artifact id; a hash mismatch stays
`input_hash_mismatch`. The refusal is written before any recovery evidence, so a
second replay refuses identically and writes nothing.

### 2. The cleanup alert surface is a read-only admin command, not a metrics stack

`internal/deletion/alerts.go` + `orchara-admin deletion alerts [--deadline 24h]
[--limit N]`:

- reads `platform.deletion_jobs` (overdue past the OPERATIONS.md §36 24h target,
  and `failed`) and `platform.background_jobs` (`kind=delete_content`: `failed`,
  `delete_timeout`, or unclaimed past the deadline);
- prints one JSON report: `{status, deadline, checked_at, jobs_examined,
  deletion_jobs, cleanup_jobs, alerts[], boundary}`; every alert carries ids, codes,
  counts, ages and remaining scopes only — never a body, object key or hash;
- exit codes: `0` no alert, `3` at least one alert (`deletion.AlertsExitCode`),
  `1` the pass could not run, `2` usage. `cmd/admin` gained an `exitCodeError` so a
  decided signal is distinguishable from a failure;
- writes nothing: it never advances a job, never deletes bytes and never touches a
  tombstone.

**There is no OTel/metrics exporter** in this repository (ADR 0028 §6, re-verified:
no `opentelemetry|otel|tracer|metrics` SDK). The "metrics-like table/command" shape
the ticket allows is what was built; an OTel-specific acceptance line stays
`partial` in the report rather than claiming a tracer that does not exist.

### 3. A bounded attempt keeps its timeout classification

`handlers/delete_content.go` maps "the deadline expired before the scope set could be
read" to `delete_timeout`, and `guardDeleteContentCompletion` preserves that code
when the only remaining evidence is "objects still present". The deletion job's own
`error_code` still records the evidence-derived failure, so the timeout is reported
on the attempt and the outstanding scope stays visible on the deletion row. Both
codes are in the fixed `delete_content` retry table; a retry with the normal
deadline finishes.

### 4. The Q45 gate gains a component-deletion half (extending, not replacing it)

`mem0exp/decision.py` adds `deletion_proof(cleanup)`:
`residual_body_copies` (observed leak) **or** `unverifiable_copies` (stores the
component cannot prove purged) **or** `deletion_sufficient: false` fails the gate.
`decide()` reports it as `gates.f11_component_deletion` and as
`governance.deletion_sufficient`; any evidence makes the verdict `do_not_adopt`,
independent of `gates.f11`. `mem0exp/records.py` refuses an `adopt` record that
omits the component gate or lists unpurged/unverifiable copies, and refuses the
self-contradictory record that claims the gate passed while enumerating copies. The
committed T066 evidence (`do_not_adopt`, six residual copies) validates unchanged.

### 5. The third-party / backup / already-downloaded boundary is stated, not hidden

The platform can name and purge what it wrote: sealed objects, chunk/vector/index
rows, retrieval cache entries, checkpoints it created, host sandbox staging, the
deletion ledger and the audit metadata. It cannot erase:

- a third-party component's own store (the T072 copy pass records it as
  `external`/not erasable and, in the T066 evidence, as residual body copies);
- backup media (the ledger is the restore-time control, OPERATIONS.md §46; a backup
  is erased by its own retention, not by a platform job);
- a copy the user already downloaded.

The alert report repeats that boundary in its `boundary` field. A restored database
row is indistinguishable from a live one (proved in `test_t076_restore.py`), which is
exactly why the documented restore procedure replays the ledger **before** reads
open — the platform cannot detect a restore, so it must not be asked to.

## Consequences

- Later tickets that add a resource type with more than one input artifact get
  recovery coverage for free; they must keep writing `run_artifacts.relation='input'`
  rather than a bespoke column.
- Any new cleanup kind that wants to report a timeout must (a) put its code in the
  fixed retry table and (b) preserve it in its completion guard when the evidence
  outcome would otherwise overwrite it, otherwise the alert surface cannot see it.
- The alert surface is the operator entry point for the "deletion backlog" line in
  OPERATIONS.md §55; it is not a scheduler and never retries by itself.
- Adoption of any memory/RAG component must record a deletion proof, not only a
  negative enumeration; a component without a deletion path can never satisfy it.
- No new table, no new contract field, no `docs/spec` change; `sync_spec --check`
  stays clean.
