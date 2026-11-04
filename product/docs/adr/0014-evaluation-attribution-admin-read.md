# ADR 0014 — The administrator evaluation-attribution read

- Status: Accepted (F13A/T062, issue #63)
- Date: 2026-11-04
- Extends: ADR 0004 (evaluation pipeline boundaries), ADR 0011 (the evaluation run
  bridge and its `evaluation_case_runs` link)

## Context

The report-integrity ticket (#63) needs an administrator to be able to check three
things about an evaluation report:

1. **the account** that produced it (owner principal and login);
2. **the cancellation outcome**: the state of every case's platform run, including
   a run that was cancelled or never finished;
3. **the attribution**: the frozen snapshot, the manifest and dataset hashes, the
   compiler version and the worker image digest the report is bound to.

The contract cannot carry any of that. `getEvaluation` is fixed at six fields
(`id/kind/state/background_job_id/report_id/error_code`) and `getReport` at four
(`id/status/manifest_sha256/result_artifact_id`); both are read by the owner *and*
the administrator, so widening them for administrators would either leak
attribution metadata to every owner or require a second schema on one route.

T056 owns the user-facing per-case read but is not part of this ticket's branch.
The completion guard (T052/T062) proves a report was *accepted*; it does not give a
human the evidence to audit one after the fact. Without that evidence an
administrator cannot distinguish "the guard accepted a report for the wrong
application" from "the guard was bypassed"; the ticket requires the attribution
surface as a deliverable, not as a debugging convenience.

The two hard constraints from the ticket and the repository:

- the surface must **never** expose a sealed object key, a report body, a case
  input, an artifact download URL or another user's content;
- adding it must not change `contracts/**` or `docs/spec/**`, so
  `python tools/sync_spec.py --check` stays clean.

## Decision

Add one **non-contract, administrator-only** read, documented here and in the T062
report:

```
GET /api/v1/admin/evaluations/{evaluation_id}/attribution  -> 200 attribution
```

Authorization:

- the caller must hold the `admin` role; the role check runs **before** any
  database lookup, so a non-admin receives `403 forbidden` for every id and cannot
  use the endpoint to probe for an evaluation's existence;
- service-account API keys have no roles, so a bearer credential is refused by
  the same role check before the scope check (the contract evaluation reads are
  the scope-limited path); the handler keeps the `application:read` scope check
  as defence in depth for a future credential shape, but the administrator
  surface is a human-session surface today;
- an unknown or malformed evaluation id is `404 resource_not_found`, exactly like
  a missing row.

The response is a read-only projection of platform rows (never of the sealed
report bytes) with a fixed, tested field set:

| group | fields |
|---|---|
| evaluation | `evaluation_id`, `kind`, `state`, `error_code`, `background_job_id`, `created_at`, `finished_at`, `cancel_requested_at` |
| account | `owner.principal_id`, `owner.login` |
| attribution | `application_id`, `snapshot_id`, `draft_revision`, `manifest_sha256`, `dataset_artifact_id`, `dataset_sha256`, `compiler_version`, `worker_image_digest`, `state_schema_version` |
| report | `report.id`, `report.status`, `report.manifest_sha256`, `report.dataset_sha256`, `report.result_artifact_id` |
| cases[] | `case_id`, `ordinal`, `case_state`, `reason_code`, `run_id`, `run_state`, `run_owner_id`, `run_snapshot_id`, `run_error_code`, `terminal`, `completed`, `updated_at` |

`terminal` means the run can no longer transition (`succeeded`, `failed`,
`cancelled`); `completed` means it finished executing (`succeeded` or `failed`) and
is therefore the only state that may back a passed case. Exposing the distinction
is deliberate: it is the T062 acceptance rule in the administrator's view.

What the payload deliberately does **not** contain: object keys (`object_key`,
`sealed/...`, staging keys), report bytes or checks, case input or dataset content,
artifact download URLs or content, session/credential material, or any field of
another evaluation. The `result_artifact_id` is the same id the contract's Report
already exposes; everything else is keyed by ids and hashes.

## Consequences

- The public contract is untouched; `python tools/sync_spec.py --check` stays
  clean. No migration is required: every field already exists on
  `evaluation_jobs`, `evaluation_case_runs`, `runs`, `execution_snapshots`,
  `evaluation_reports`, `artifacts` and `users`.
- The endpoint is covered end to end in `tests/f13a/test_t062_authz.py`: the
  administrator sees the account and the cancelled/unfinished case states; the
  owner, another creator and an unauthenticated client are refused (403/401); an
  unknown or malformed id is 404; the response text is asserted to contain no
  sealed key, no case input marker and no report body.
- The guard remains the only writer of a report row; this read never writes.
- If T056 later adds a contract or owner-facing per-case read, this administrator
  surface can stay as the audit view (it is a different audience and a different
  authorization rule) or be folded into it; either way the field set here is the
  minimum an administrator needs for #63 and is pinned by a test so a future
  change cannot silently widen it.
