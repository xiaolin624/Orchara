# ADR 0028 — Scoped diagnostic grants and the audited content door

- Status: Accepted (F11/T073, issue #74)
- Date: 2026-11-05
- Extends: ADR 0020 (the tombstone read predicate a grant must never bypass),
  ADR 0009 §1 (the personal-consent boundary that refuses service credentials),
  ADR 0004 §38 / ADR 0014 / ADR 0022 (the established pattern for documented
  non-contract response headers/fields on a frozen contract), ADR 0017 (an
  administrator is not automatically a content reader)
- Numbering: the orchestrator reserved 0028 for this ticket (0025–0027 belong to
  the concurrent F11 tickets). `db/migrations/0048_diagnostic_grants.sql` is the
  next free number on this branch after the T077/T071/T072 merges took 0045–0047
  (the merger renumbers by name, as BRIEF §5 requires; the reserved number is
  0049).

## Context

`contracts/database.sql` already names `diagnostic_grants(id, owner_id, grantee_id,
run_id, purposes text[], expires_at, revoked_at)` and the contract has exactly two
operations, `grantDiagnostics` (`POST /api/v1/diagnostic-grants`, 201 `Accepted`)
and `revokeDiagnostics` (`DELETE /api/v1/diagnostic-grants/{grant_id}`, 204). The
contract has:

- no object scope, although the ticket/UX require the user to choose the diagnostic
  objects (`诊断授权选运行、对象范围和到期时间`);
- no read surface through which a named administrator could read the granted body;
- no per-read audit vocabulary;
- no observability stack: `grep opentelemetry|otel|tracer|span|prometheus|metrics`
  over `internal/` and `cmd/` matches nothing (verified 2026-11-05). The repository
  has no tracer, no span, no metrics registry and no exporter.

`SECURITY.md:47` requires: a grant contains user/scope/expiry/purpose, every read is
audited, expiry is enforced, and the body must not be copied into long-lived logs.
`DATA-POLICY.md:7` requires a user's explicit authorisation and a trace for body
reads; `OPERATIONS.md:57` forbids high-cardinality user/run labels in metrics.
`API.md:12` forbids request content or credentials in error `details`.

The content lives in `platform.artifacts` (the run input, node outputs and final
outputs are all sealed artifacts linked by `platform.run_artifacts` /
`platform.run_artifact_uploads`). The existing owner reads
(`/api/v1/artifacts/{id}/content`, `/download`) are owner-only; the existing admin
reads (`/api/v1/background-jobs/{id}`, evaluation/review/attribution surfaces)
return typed metadata only. A grant therefore needs its own operator door and its
own audit record, not a relaxation of the owner-only paths.

## Decision

### 1. The grant table is the contract table plus one snapshot column

`0048_diagnostic_grants.sql` creates the contract table with:

| column | meaning |
|---|---|
| `id/owner_id/grantee_id/run_id/purposes/expires_at/revoked_at` | the contract shape, unchanged |
| `object_scope jsonb NOT NULL DEFAULT '[]'` | **non-contract** snapshot of `artifact:<uuid>` entries the owner selected (array-checked) |
| `created_at timestamptz NOT NULL DEFAULT now()` | non-contract; the expiry/revocation ordering anchor |

Guards are database facts: `cardinality(purposes) > 0`,
`jsonb_typeof(object_scope) = 'array'`, `expires_at > created_at`,
`revoked_at IS NULL OR revoked_at >= created_at`. Indexes:
`diagnostic_grants_live (grantee_id, run_id) WHERE revoked_at IS NULL` and
`diagnostic_grants_owner (owner_id, created_at DESC)`. The table is
diagnostic-only: no training/consent column, trigger or FK.

### 2. Grant creation: session + CSRF, owner grants, another administrator receives

- Only a human cookie session may create/revoke/list/read (403
  `personal_scope_forbidden` for an API key, the ADR 0009 §1 precedent). The
  contract's `apiKey` alternative is deliberately not honoured for personal
  consent.
- The caller must own the run (`runs.owner_id`, `FOR SHARE`); a foreign/unknown
  run is the same 404. A tombstoned run is not grantable.
- The grantee must be a different, enabled principal holding the `admin` role.
  A self-grant would let an administrator manufacture content access and is refused
  with a located 422.
- `purposes` is a bounded token vocabulary (`[a-z][a-z0-9_-]{0,63}`, ≤8,
  deduplicated). The contract only says `string[]`; closing the shape is what makes
  the purpose safe to copy into audit metadata and to compare on every read.
- `expires_at` is a required RFC3339 future instant.
- **Object scope is a non-contract request header**, `X-Diagnostic-Object-Scope:
  artifact:<uuid>[,artifact:<uuid>…]`. The JSON body stays exactly the contract's
  (`additionalProperties:false` is not violated). Every named object must be a live
  artifact linked to this run; an absent header derives the run's current objects
  **once** at grant time. The stored value is always an explicit sorted snapshot: an
  object uploaded later is never silently in scope. This is the documented
  deviation the ticket needs (BRIEF §1.3); the response `Accepted {id,state}` and
  the DELETE 204 remain contract-exact.
- Creation writes one grant row and one `audit_events(action='diagnostic.grant_created')`
  row with ids, purposes, expiry and `object_count` only.

### 3. The content door is a documented non-contract, audited stream

`GET /api/v1/diagnostic-grants/{grant_id}/content?object=artifact:<uuid>&purpose=<token>`
is the only path through which an administrator reads another user's run body. It
is admin+grantee only and streams the sealed object as an attachment (never a
signed URL: `API.md`/SECURITY require immediate revocation, and a minted link
outlives the grant). One transaction re-checks, in this order:

1. the grant row exists, the caller is the named grantee and still holds the
   `admin` role in `role_bindings` (any other caller gets the same 404 as a
   missing grant, no grant oracle; a named grantee whose role was removed gets
   403 `diagnostic_grantee_not_admin` — the live role, not the historical grant,
   is the authority);
2. `revoked_at IS NULL` → 403 `diagnostic_grant_revoked`;
3. `expires_at > now()` (database clock) → 403 `diagnostic_grant_expired`;
4. `runs.deleted_at IS NULL` → 403 `diagnostic_run_deleted`;
5. purpose ∈ `purposes` → 403 `diagnostic_purpose_denied`;
6. `artifact:<id>` ∈ `object_scope` → 403 `diagnostic_scope_denied`;
7. the artifact is still a member of the run and `ready`, not deleted/quarantined
   → 404 `diagnostic_content_unavailable` / 409 `diagnostic_content_not_ready`;
8. the sealed object opens; a missing/corrupt file is 404
   `diagnostic_content_unavailable`.

The `diagnostic.content_read` audit row is written **and committed before the first
byte** is served. Read fails closed if the audit write fails. A refusal is audited
with `outcome='refused'` and its own code; an open/storage failure with
`outcome='failed'` and `diagnostic_content_missing`/`diagnostic_storage_io`. The
unknown/foreign-grant 404 is not audited because there is no grant to attribute it
to. This satisfies "每次读取审计" and makes "refused" itself evidence.

`grant ∧ NOT deleted` is the read predicate: a grant can never resurrect a
tombstoned run or artifact, even if it was minted while they were alive.

### 4. One typed audit writer, whitelist-only metadata

`internal/audit.RecordContentRead` takes a positional `ContentRead` struct and
builds the metadata itself. The whitelist is exactly ten keys:

```
grant_id, purpose, run_id, resource_type, resource_id,
operation_id, attempt_token, request_id, outcome, error_code
```

`operation_id`/`attempt_token`/`request_id` are JSON null when absent, never
omitted. Every UUID field is validated, the purpose/type/error-code tokens are
bounded, and `outcome='served'` forbids an error code. There is no `map[string]any`
parameter, so no caller can smuggle a body, a query, a path or a credential into
the audit trail; a malformed record is refused before the INSERT. Correlation is
real: `attempt_token` comes from `run_artifacts`/`run_artifact_uploads`, and
`operation_id` is recorded only when exactly one `operation_attempts` row shares
that token (otherwise the attempt alone is reported — an arbitrary operation would
be a lie). `request_id` is the middleware id also returned in `X-Request-ID`.

### 5. Administrator default stays metadata-only

The pre-existing administrator reads are untouched. The new
`GET /api/v1/diagnostic-grants` (non-contract) returns metadata only —
`id/state/direction/run_id/owner_id/grantee_id/purposes/object_scope/expires_at/
created_at/revoked_at` — to the owner (direction `owner`) and the grantee
(`grantee`). It never opens an object. Body reads exist only at the §3 door, which
requires the live grant. A grant is not a training consent: creating one writes no
training/consent state and is a separate table/audit action from future training
exports.

### 6. Logs, errors and traces

- The request log stays one line: method, path **without query string**, status,
  duration, request id. The object/purpose query values therefore never reach it,
  and no header/body is read.
- The content stream's error log carries only `request_id` and the transport error;
  an error response is the contract envelope with a generic message and (for 422)
  the field name only, never the submitted value.
- There is no OTel/metrics SDK, so this ticket does not fake a tracer. The
  structural guarantees above (whitelist writer, no query-string logging, sentinel
  scans of the real API/maintenance logs and all audit rows) are the deliverable;
  the "OTel carries no content" AC is **partial** with that explicit reason, and
  "metrics avoid high-cardinality user/run labels" is satisfied by "there are no
  metrics labels at all". When a tracer/exporter is introduced, the same ten-key
  whitelist must be the only content-read attribute set; adding a body/query
  attribute requires a new ADR.

## Consequences

- T076's no-resurrection suite must use the same `grant ∧ NOT deleted` predicate
  and `audit.RecordContentRead`; it must not add a second operator read path.
- T071's purge inherits the rule that audit rows are evidence, not a backup: it
  deletes bytes/scopes, never `audit_events`. After purge the audit row still names
  ids/tokens only and cannot reconstruct the body (tested by the T073 redaction
  suite).
- A grant whose run/object was purged stays in the list as metadata; its content
  read refuses `diagnostic_content_unavailable`.
- The contract snapshot is untouched (`sync_spec --check` stays clean); the
  object-scope header, list read and content read are documented non-contract
  surfaces.
