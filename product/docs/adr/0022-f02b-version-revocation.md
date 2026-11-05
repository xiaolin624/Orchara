# ADR 0022 — Revoking a published version and refusing a stale catalog index

- Status: Accepted (F02B/T070, issue #71)
- Date: 2026-11-05
- Extends: ADR 0005 (the documented non-contract bridge pattern), ADR 0006 (the
  execution/cancel boundaries), ADR 0011 (execution snapshots and runs);
  consumes T068's `application_versions` revocation columns and `platform.outbox`.

## Context

`revokeVersion` (`POST /api/v1/versions/{version_id}/revoke`) is a contract
operation, but the contract gives it almost no surface: body
`{reason, stop_existing}`, success **200 `Accepted`** (`{id, state}`), **no
Idempotency-Key**, and the `Version` schema (`id, application_id, version_no,
manifest_sha256, manifest`) has **no state or availability field**. Yet the ticket
requires four distinct behaviours:

1. a revoked version must stop being usable: it cannot start a new Run, it is not
   visible in the catalog, and a *stale catalog index* that remembers an old id
   must not bypass that;
2. revoking the active version must **not** silently replace it with the latest or
   previous version;
3. historical Runs keep their fixed reference and the sealed bytes never change;
4. `stop_existing:true` must stop in-flight work "by the revocation reason",
   through the existing cancel machinery, without the revoke transaction writing a
   terminal Run state.

The rows already exist: T068's `0042` added
`application_versions.revoked_at/revoked_by/revoke_reason` and the
`application_versions_revoke_shape` CHECK (the three columns move together), and
created `platform.outbox`. No schema change is needed for T070.

## Decision

### 1. Revocation is a state change, never a rewrite

One transaction: lock the version and its application row, then

```sql
UPDATE platform.application_versions
   SET revoked_at = now(), revoked_by = $admin, revoke_reason = $reason
 WHERE id = $1 AND revoked_at IS NULL;
```

`manifest`, `manifest_sha256`, `manifest_artifact_id`, `version_no`, `review_id`
and `created_at` are never written again; there is no delete path
(`applications.active_version_id` is `ON DELETE RESTRICT`, `0010`). The state CAS
is the idempotency the contract implies by omitting an Idempotency-Key: a second
call answers the same 200 `{id, "state":"revoked"}` and writes **no** second
audit or outbox row; the first reason wins.

### 2. The active pointer becomes NULL — it is never repointed

`Application.active_version_id` is the only availability signal the contract
exposes. When the revoked version is the active one, the same transaction sets it
to `NULL`. Repointing at another version would be exactly the "silently swap in
the newest version" the ticket forbids, and would make a version the administrator
did not choose runnable. The previous pointer is recorded in the outbox payload
(`previous_active_version_id`) and in the `version.revoked` audit metadata, so the
transition is reconstructable.

### 3. A revoked version is hidden from the `Version` read model

`GET /versions/{id}` answers `404 resource_not_found` and
`GET /applications/{id}/versions` omits the row. This is the only
contract-legal representation: adding `state`/`revoked` to `Version` would be a
contract change. Historical Runs never need `getVersion`; they carry their own
`application_version_id`/`manifest_sha256`/`execution_snapshot_id` and remain
readable through the Run endpoints.

### 4. The authoritative guard is a live DB read, exposed as a documented non-contract route

`catalog.ResolveRunnableVersion(ctx, callerID, versionID)` (implemented in
`internal/catalog/publication.go`, used by T070 and by T074 when it creates a
Request/Run) reads the live rows and returns the immutable `Version` only when the
caller may read the application **and** `revoked_at IS NULL` **and** the
application is not archived. It returns distinct sentinels:

| sentinel | meaning | HTTP (non-contract route) |
|---|---|---|
| `catalog.ErrNotVisible` | unknown id or no read access | `404 resource_not_found` |
| `catalog.ErrVersionArchived` | the owning application is archived | `409 version_archived` |
| `catalog.ErrVersionRevoked` | `revoked_at` is set | `409 version_revoked` |

The thin HTTP surface `GET /api/v1/versions/{version_id}/runnable` + `/assets/versions.js`
renders the catalog/run-entry unavailability; a catalog index can only supply a
candidate id, never bypass this read. The route is additive, undocumented in the
contract, and recorded here plus in the T070 report (ADR 0005's precedent).

### 5. `stop_existing` = a cancel intent with a stable reason, never a terminal write

For every non-terminal Run of the version the revoke transaction records
`runs.cancel_requested_at` (first value wins, `COALESCE`) and raises
`runs.error_code` to the stable `version_revoked` code (only when no failure
reason is already stored — the same rule `markDeadlineIntentTx` uses for the
deadline). It then appends exactly one `run.status` event through the shared
same-transaction primitive `execution.AppendRunStatusTx`.

The contract's `run.status` payload is fixed to `{state, error_code}`
(`contracts/event.schema.json`, `additionalProperties:false`), so the
version-revoked reason cannot be an extra payload field: it travels in
`error_code` and in the durable audit/outbox metadata. The cancelled finalizer
clears `error_code` when the worker's exit fact commits `cancelled`
(`execution/service.go`), so the transient control reason never outlives the
cancel.

The revoke transaction deliberately does **not** write a terminal Run state; the
worker/exit path and the one T036 finalizer own that transition. The consequence,
stated honestly: a Run in `queued` with a `ready` job is not prevented from being
claimed by `Claim` (the claim query does not consult `cancel_requested_at`), so a
queued published Run can still start between revocation and its exit. No
published-run creator exists in this build (T074 owns it), and T074's start path
must call `ResolveRunnableVersion` first, which closes the new-Run hole. Extending
the claim predicate to honour a cancel intent belongs to a later execution ticket,
not to the revocation transaction.

### 6. One durable outbox row, one writer

The revoke transaction inserts `platform.outbox (kind='catalog_index',
aggregate_id=version_id, payload {op:"revoke", application_id, version_id,
version_no, manifest_sha256, previous_active_version_id, active_version_id:null,
reason, stop_existing})` through the single shared writer `insertOutboxTx` (T068's
approval uses the same function). There is no dispatcher or consumer in this
build: `delivered_at` stays NULL and `attempts` 0; T077/T082 own delivery and
`application_catalog_entries`. No report claims the projection was updated through
the outbox — the authoritative catalog reads are the DB read model.

### 7. The page

`GET /apps/{application_id}/versions` (+ `/assets/versions.js`) is the
administrator's version/revocation page: it lists the visible versions, shows the
catalog availability text (`active_version_id`), and submits the exact
`{reason, stop_existing}` body with the session CSRF token. The catalog page
(`/apps`) uses the same helper, so an application whose active pointer was nulled
reads "不可用：没有生效版本（未发布或已撤销）". The helpers are pure and executed
by Node in `tests/t070/test_t070_pages.py`; no browser DOM is driven (same
boundary as every page ticket).

## Consequences

- The public contract and `docs/spec/**` are untouched; `sync_spec.py --check`
  stays clean. The deviations are: the revoked-version omission (contract has no
  state field), the active pointer being NULL (a product decision), and the
  non-contract runnable route.
- The revocation columns and the outbox table are T068's `0042`; **T070 adds no
  migration**, so the merger has no numbering work for this ticket.
- T074 must call `catalog.ResolveRunnableVersion` before creating a Request/Run
  and copy the returned manifest identity into the published execution snapshot;
  it must not use a catalog projection for that decision.
- T077 must consume the outbox rows (`op=revoke`) and re-check
  `application_versions.revoked_at` + ACL on every search result before execution.
- A revoked version keeps receiving no new Runs, but already-running Runs finish
  through the normal cancel path (or keep running when `stop_existing=false`),
  retaining their original snapshot/manifest references.
- The 5 s/clock determinism rule is irrelevant here: revocation itself is a single
  committed state change and the tests poll/read authoritative rows, never a
  sleep.

## Not in this ADR / not run

- No deletion of version rows, artifacts or snapshots (F11).
- No catalog index consumer (T077), no conversation/request queue (T074).
- No browser DOM automation (none exists in this repo).
