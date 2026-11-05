# ADR 0027 — Copy-class cleanup and the independently replayable deletion ledger

- Status: Accepted (F11/T072, issue #73)
- Date: 2026-11-05
- Extends: ADR 0020 (shared deletion foundation: job row, scope vocabulary,
  tombstone-first reads), ADR 0009 (memory deletion), ADR 0015/0018 (cache/index
  read predicates), ADR 0004/0006 (background-job/execution boundaries)

## Context

ADR 0020 fixed one deletion model: a `platform.deletion_jobs` row per deleted
revision with the closed scope vocabulary
`object_store | search_index | retrieval_cache | checkpoints | sandbox_temp |
third_party_history | backup_catalog`. T071 owns the first three physical purges;
T072 owns the last four and the *backup* half of deletion governance.

The normative requirements this ADR decides the shape for:

- `SECURITY.md` §49 —physical cleanup must cover object, chunk/vector, model cache,
  **checkpoint, temporary sandbox and traced body**; legitimate references are
  handled by explicit reference, never by deleting "unreferenced" data.
- `ROUTING-MEMORY-KNOWLEDGE.md` §47 —purging the vector store is not enough:
  **checkpoints and context summaries also copy body text** and must join the
  deletion/restore rules.
- `SECURITY.md` §51 / `DATA-POLICY.md` §13 —a backup, a vendor copy or an
  **already-downloaded** copy is not erasable by the platform on demand; the real
  boundary must be stated, not hidden.
- `SECURITY.md` §55 / `OPERATIONS.md` §15 —third-party memory/RAG components may
  keep history/docstore/cache/log copies and must be in the deletion ledger.
- `DATA-MODEL.md` §60-62 / `OPERATIONS.md` §46 —**after a backup restore, replay an
  independently retained deletion ledger before reads open**; audit metadata only,
  never body text.

The product has no context-summary store, and `DATA-MODEL.md` §22 says the
`checkpoint` schema is out of band and owned by `langgraph-checkpoint-postgres`:
the product must not create it. Treating "the schema is not installed" as "cleared"
would be a false claim, so the design must record an honest `absent`.

## Decision

### 1. `platform.deletion_ledger`: append-only, hash-chained, no foreign keys

`db/migrations/0045_deletion_copy_ledger.sql` (allocated 0048; renumbered by the
merger to the next free slot) creates:

```
sequence (identity, in the hash), deletion_job_id, resource_type, resource_id,
deletion_revision, entry_kind, scope, copy_class, scope_key, record jsonb,
record_sha256, prev_sha256, created_at
```

- `entry_kind` is closed: `tombstone | scope_cleared | scope_absent | scope_failed |
  scope_external | job_completed`.
- `copy_class` is closed: `checkpoint | summary | sandbox_temp | third_party_history |
  backup_catalog | backup_media | vendor_copy | downloaded_copy`.
- `record_sha256` = SHA-256 of the RFC 8785 canonical JSON of the whole entry
  including `sequence` and `prev_sha256`; `prev_sha256` chains to the previous
  entry (null at genesis). Sequence is allocated under a transaction-scoped
  advisory lock, so concurrent appends cannot fork the chain.
- A `BEFORE UPDATE OR DELETE` and a `BEFORE TRUNCATE` trigger raise
  `restrict_violation`: append-only is a database property, not a code convention.
- **No foreign key in either direction** to `deletion_jobs`/`artifacts`/`memories`/
  `knowledge_documents`/`conversations`. A restored target may not yet contain those
  rows; the ledger must survive on its own, and replay proves the row exists before
  it re-tombstones it.
- `record` carries ids, object keys, hashes, sizes, counts, safe codes and reasons
  only. Events/audit/ledger never carry body text (`DATA-MODEL.md` §46).

Every `deletion.Service.Delete` appends its `tombstone` entry in the same
transaction as the tombstone, the job and the audit event (repeated DELETE reuses
the job and appends nothing). The record carries the derived document ids the same
transaction tombstoned, so a restore cannot resurrect a document whose source was
deleted.

### 2. Export and replay are the restore control

`orchara-admin deletion export --file PATH` verifies the chain and writes JSONL:
a header (`format`, `exported_at`, `database`, `count`, `head_sha256`) then one
entry per line. The header's count/head detect a truncated or extended file even
when the remaining lines form a valid chain prefix.

`orchara-admin deletion replay --file PATH [--check-only]` verifies the chain
*before touching the target*, then re-applies each `tombstone` decision in its own
transaction and appends a `tombstone` entry (`record.source="replay"`, original
sequence) to the restored database's own ledger in the same commit. Replay only
updates a row whose `deleted_at IS NULL` and uses `GREATEST(revision+1,
deletion_revision)`, so a newer restored revision never moves backwards. Types:

| resource_type | replay effect |
|---|---|
| `artifact` | tombstone + `state='deleted'`; the recorded derived documents are tombstoned too |
| `memory` | tombstone + revision bump + `memory:<owner>:<app>` policy epoch bump |
| `knowledge_document` | tombstone + `state='deleted'` |
| `conversation` | tombstone + non-terminal requests cancelled + pending interactions invalidated |

A second replay reports `already_deleted` and appends nothing. A tampered file is
refused (exit 1) with no target mutation. Replay of a row missing from the
restored target is reported as `missing`, not silently treated as done.

### 3. `cmd/maintenance copies` is the copy inventory, not a second handler

The four scopes are cleared by one maintenance pass, not by registering a second
background handler or a new job kind:

```
orchara-maintenance copies
  --sandbox-temp-root PATH   (default ORCHARA_SANDBOX_WORKSPACE_ROOT or the OS temp dir)
  --sandbox-age 1h
  --third-party-components a,b
  --limit 100
```

Reasons for not extending T071's `delete_content` handler:

- the `checkpoint` schema is out of band and may not exist; its tables are not
  reachable through the object-store handler;
- sandbox staging directories are host filesystem state created by the supervisor,
  not object-store keys;
- third-party history and backup media are explicitly outside the database/object
  store, and T071's ticket boundary says "checkpoint/third-party copies are the next
  governance slice";
- T071 and T072 run in parallel. One handler registry entry per ticket, both
  mutating the same job row under a row lock, would be a merge conflict for no
  benefit. This ADR therefore records: **no `delete_content` registry change and no
  new job kind in T072**; `cmd/maintenance copies` is the T072 operator surface, and
  `sweep`/`run` stay T071/T026's.

### 4. The shared scope-clearing rule

For each scope whose copy classes are all terminal (`cleared`, `absent` or
`external`), one transaction appends the per-class ledger entries, removes the
scope from `remaining_scopes`, appends it to `completed_scopes` (stable sort),
increments `attempt_count`, and sets `state='completed'` + `completed_at` +
`job_completed` when—and only when—`remaining_scopes` is empty. A class that fails
leaves its scope in `remaining_scopes`, sets `state='failed'` and `error_code`, and
appends `scope_failed`; the tombstone still makes every read refuse. The rule is
implemented in `internal/deletion` so T071 can reuse it per scope instead of
duplicating the job update (T071's branch predates this helper; the merger should
point its completion code at it).

### 5. Honest outcomes per copy class

| scope | copy class | normal outcome | honest non-erasable / absent outcome |
|---|---|---|---|
| `checkpoints` | `checkpoint` | delete the named threads from `checkpoint.checkpoints/checkpoint_blobs/checkpoint_writes` (`absent` if the schema is not installed) | `absent`/`checkpoint_schema_absent`; never "cleared" |
| `checkpoints` | `summary` | — | `absent`/`no_summary_store`: the product has no summary store; the surface counts (`run_events`, `run_attempts.reported_result`, `run_node_outputs`) prove the claim is bounded, not prose |
| `sandbox_temp` | `sandbox_temp` | remove `orchara-sandbox-<attempt-unit>-*` directories for the deleted runs' attempts that are not live, after the age gate, verified on the filesystem | `absent`/`no_leftover_workspace`; a live attempt keeps the scope outstanding with `copy_sandbox_live_attempt` |
| `third_party_history` | `third_party_history` | — | `absent`/`no_third_party_component_deployed` by default (T066 committed `do_not_adopt`); a configured component is `external`/`component_history_outside_platform_control` |
| `third_party_history` | `vendor_copy` | — | `external`/`model_provider_may_retain_prompts` when the affected runs dispatched a model operation; `absent`/`no_external_model_path` otherwise |
| `backup_catalog` | `backup_catalog` | register the decision in the ledger | `cleared`/`deletion_registered_in_ledger` |
| `backup_catalog` | `downloaded_copy` | — | `external`/`signed_download_outside_platform_control` |
| `backup_catalog` | `backup_media` | — | `external`/`backup_media_retention` (30-day default from `OPERATIONS.md` §38) |

The `job_completed` record carries the `non_erasable` declaration list (vendor
copy, downloaded copy, backup media) so a deployer reading the ledger cannot read
"job completed" as "every copy was erased".

### 6. Sandbox workspace naming

The per-attempt host workspace is named `orchara-sandbox-<sandboxID>-<random>`;
`sandboxID` is derived from the attempt's execution unit id (`internal/sandbox`),
and `run_attempts.execution_unit_id` is the durable source reference. Without the
id in the name a crashed leftover could not be attributed to the deleted run, and
the cleanup would have to guess by age and prefix (which this ADR rejects). This is
the only change to the sandbox executor in T072; the per-attempt `defer RemoveAll`
self-cleanup is unchanged. `ORCHARA_SANDBOX_WORKSPACE_ROOT` pins the staging root
for deployments and must match `--sandbox-temp-root`.

## Consequences

- The ledger is a new non-contract table and a new admin command surface; the
  contract (`openapi.json`, `database.sql`) is unchanged and
  `python tools/sync_spec.py --check` stays clean.
- `platform.deletion_jobs` remains the live work queue; `GET /api/v1/deletions/{id}`
  still exposes only the contract fields. Failure visibility is the `failed` state
  plus the non-empty `remaining_scopes`; the error code is on the job row/ledger and
  in the maintenance report.
- Tail truncation is detectable only down to the exported header (`count`,
  `head_sha256`): an attacker who truncates both the table tail and every retained
  export cannot be detected by the chain alone. The header is the retained artifact;
  future work may add an external anchor.
- A restore must run `deletion replay` *before* reads open; the command does not and
  cannot enforce the ordering itself (`OPERATIONS.md` §46 is the operator runbook).
- Third-party component deployment is an operator flag, not database evidence: the
  product has no third-party memory component today (T066 `do_not_adopt`), so the
  default is `absent`, and a deployment that adopts one must pass the flag to record
  the honest `external` outcome.
- `checkpoint` rows are deleted by `thread_id` only when the schema is present; the
  product still never creates the schema (DATA-MODEL.md §22).
