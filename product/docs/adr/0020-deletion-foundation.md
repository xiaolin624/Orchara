# ADR 0020 — The shared deletion foundation

- Status: Accepted (F11/T069, issue #70)
- Date: 2026-11-05
- Extends: ADR 0009 (F09 memory deletion slice; its `deletion_jobs` shape is
  superseded in part by §2 below), ADR 0004/0006 (background-job and execution
  boundaries), ADR 0015/0018 (cache/index read predicates this ticket reuses)

## Context

F11 has five tickets on one deletion model: T069 (this foundation), T071 (async
object/index/cache purge), T072 (checkpoint/temp/third-party/backup ledger), T073
(scoped diagnostics/audit) and T076 (no-resurrection regression). T058 had already
created `platform.deletion_jobs` for memory deletion with:
`id/owner_id/resource_type/resource_id/deletion_revision/state/remaining_scopes/
created_at/completed_at`, state `pending|running|completed|failed`, unique
`(resource_type, resource_id, deletion_revision)`, and `remaining_scopes` as an
array of copy *descriptors* (`{"kind":"object",...}`).

The contract's `DeletionJob.remaining_scopes` is `string[]` (openapi.json), and
later tickets need one fixed vocabulary they can consume without rewriting the
job model. The read paths already refuse a tombstoned resource in one place each
(artifacts `readyRow`, knowledge `readable_chunk.go`, memory recall, run reads
filtering `runs.deleted_at`), so this ticket must not add a second filter.

## Decision

### 1. One durable job row, extended additively

`db/migrations/0042_deletion_foundation.sql` adds to the contract table:

| column | meaning |
|---|---|
| `remaining_scopes jsonb` | **redefined** to the closed string vocabulary below; still `jsonb_typeof='array'` |
| `scope_details jsonb` | per-scope copy descriptors (object keys/artifact/document/run ids), internal only |
| `completed_scopes jsonb` | scopes cleared by T071/T072 |
| `attempt_count integer` | cleanup attempts (job-level retry counter; T071/T072 may add per-scope counters) |
| `error_code text` | last safe cleanup/cancellation error code |
| `started_at`, `updated_at` | job timestamps; `created_at/completed_at` stay the contract columns |

The state enum, `owner_id` (the requesting principal), `resource_type`,
`resource_id`, `deletion_revision` and the unique key are unchanged. The ticket
does **not** create `platform.conversations` (F08/T074 owns it), a backup ledger
(T072) or diagnostic grants (T073).

### 2. Fixed scope vocabulary (the later-ticket interface)

Every fresh T069 job starts with all seven scopes outstanding, because T069 cannot
prove any class of derived copy absent:

```
object_store | search_index | retrieval_cache | checkpoints |
sandbox_temp | third_party_history | backup_catalog
```

- T071 owns the first three (sealed object/artifact rows, knowledge chunks +
  vectors/index versions, retrieval/answer cache entries).
- T072 owns the last four (checkpoints/summaries, sandbox temp, third-party
  history, backup deletion ledger).
- T071/T072 remove a scope from `remaining_scopes` and append it to
  `completed_scopes` in the same transaction that records the per-scope evidence;
  `GET /api/v1/deletions/{id}` then reports the real remaining set.
- The vocabulary is code (`internal/deletion.AllScopes`) and is mirrored by the
  migration/column comments; adding a value needs a migration + report/ADR.

This supersedes ADR 0009 §11's descriptor-array shape: legacy `object|artifact`
entries map to `object_store`, `embedding` to `search_index`, and `memory_row` is
**not** a purge scope (the tombstone row is the no-resurrection authority and is
never deleted). Migration 0042 performs that translation once; new jobs write the
vocabulary directly. The T058/F09 tests and the mem0 baseline helper were updated
in the same change (documented in the T069 report).

### 3. Tombstone-first, then cancel, then (later) purge

`internal/deletion.Service.Delete` runs one transaction per request:

1. the resource package's `Target.Delete` locks/checks the owner row, refuses
   `resource_in_use` for configuration artifacts, writes the tombstone
   (`deleted_at` / `state='deleted'` / revision + 1; memory also bumps the
   `memory:<owner>:<app>` epoch) and returns the scope plan;
2. the foundation inserts (or reuses, by the resource tombstone/unique key) the
   job row and appends `audit_events(action='deletion.requested')` with ids,
   resource type, scopes and affected run ids only;
3. the transaction commits **before** the API answers 202.

After the commit the foundation calls the existing F03/T036 `Cancel` path for
every affected run that could still make progress (queued/running/waiting/
cancelling). A cancellation refusal never makes the resource readable again; the
job records `run_cancel_incomplete` and a repeated DELETE retries the fan-out.
Runs parked in `recovery_pending`/`needs_reconciliation` are deliberately left to
the T038 recovery gate, which re-reads the tombstoned input and refuses with
`input_deleted` (defence in depth; cancelling them would mask that proof).

The job stays `pending` in T069. No `delete_content` background handler is
registered: T071 adds the handler/claim/retry/completion on the same job row
(BACKGROUND-JOBS.md's `delete_content` kind already exists).

### 4. API surface and access

Four contract DELETE routes (`conversations` wired but 404 until T074, `artifacts`,
`memories`, `knowledge-documents`) answer 202 `DeletionAccepted`; a repeated
delete returns the same job id. `GET /api/v1/deletions/{id}` answers the contract
`DeletionJob` (`id/state/remaining_scopes`). Owner-only; cookie sessions need
CSRF; API keys use the existing `application:write` (delete) /
`application:read` (job read) scopes. A foreign/unknown resource or job is one
404; an artifact referenced by a published version, recoverable run, model
profile, tool version, evaluation report or live job is 409 `resource_in_use`
with `details.references` (reference classes only). Knowledge source artifacts
are user content: deleting one also tombstones the documents derived from it.

## Consequences

- Later tickets extend the same row/table and vocabulary; no parallel deletion
  model exists. `scope_details` gives T071/T072 exact keys/ids without scanning
  content.
- The 202 is honest: `pending` + non-empty scopes means physical copies still
  exist. "Failure keeps it unreadable" is a read-predicate property, not a purge
  property; no read path consults `deletion_jobs`.
- The contract snapshot is untouched (`sync_spec --check` stays clean); the added
  columns are a documented non-contract extension, like earlier migrations.
