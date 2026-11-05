# ADR 0020 — Publication review, the frozen version transaction and the author-case gate

- Status: Accepted (F02B/T068, issue #69)
- Date: 2026-11-05
- Extends: ADR 0004 (F02A/F02B boundaries and the manifest artifact),
  ADR 0005 (runs bind published versions), ADR 0006 (share applications, not user
  data), ADR 0011 (the evaluation run bridge and report binding), ADR 0019 (the
  evaluation progress read)
- Precedes: T070 (#71) revocation, T074 (#75) request/run creation, T075 (#76)
  child runs, T077/T082 (catalog index/delivery)

## Context

`contracts/openapi.json` fixes four operations (`submitApplication`,
`reviewPublication`, `listPublicationReviews`, `getPublicationReview`) and the
tables `publication_reviews`, `application_versions` and `outbox` exist in the
contract, but at the F02A baseline nothing wrote a review or a version:
`catalog/versions.go` was a read model and `internal/catalog/publication*` did not
exist. F02B needs a real submit → review → immutable version flow where:

- the creator sees purpose, risk and the trustworthy report before submitting;
- the administrator can only approve the *frozen same-hash* content; a draft edit
  after submission cannot change what is being reviewed;
- a submission binds the same evaluated manifest/dataset, and graph, dependency,
  permission and report trustworthiness are re-checked;
- approval atomically creates the version and moves the active pointer/outbox;
- hard gates cannot be waived and both decisions are recorded;
- a rejection returns the creator to editing and can never be approved later.

The public contract and `docs/spec/**` are read-only for a ticket
(`sync_spec.py --check` must stay clean), so every additive column or endpoint is
documented here, in the migration header and in the ticket report.

## Decision

### 1. Frozen evidence columns and the contract outbox (migration 0043)

`0043_publication_reviews_and_outbox.sql` adds, idempotently:

- `publication_reviews.reason`, `.snapshot_id`, `.evaluation_id`,
  `.dataset_sha256`, `.manifest_artifact_id`, `.version_id` — the decision reason
  and the frozen bindings. The contract only has the free-form `snapshot jsonb`;
  these columns make the same bindings foreign-key-checkable instead of re-parsing
  JSON for every read. T070 reads them and never writes them.
- a partial `UNIQUE (application_id, manifest_sha256) WHERE state='submitted'`
  index and a submitter/queue index. The content hash is the live-submission
  identity, so two concurrent submissions of the same content cannot become two
  pending reviews. The submit path returns the existing review instead.
- `application_versions.revoked_by`/`.revoke_reason` and a CHECK that refuses an
  actor/reason without `revoked_at` (T070 writes all three; a legacy row that only
  sets `revoked_at`, as the F02A fixtures seed, stays schema-legal). Revocation
  (T070) is an UPDATE of those three fields on the immutable row; manifest, hash,
  artifact and `version_no` are never rewritten. `UNIQUE (review_id)` makes one
  version per approved review a database fact.
- `platform.outbox` with exactly the contract's shape. Approval writes one
  `kind='catalog_index'` row in the same transaction; there is **no dispatcher or
  consumer in this build** (T077/T082 own delivery and the catalog index), so
  `delivered_at` stays NULL and `attempts` stays 0. The report does not claim the
  index was updated; the authoritative catalog today is
  `applications.active_version_id` plus the `application_versions` read model.

### 2. Submission re-proves the evidence instead of trusting the report row

`catalog.SubmitPublication` (owner-only, creator role, `Idempotency-Key`):

1. compiles the *current* draft with T025's shared-execution rule
   (`ValidateExecution`, `PrivateDebug=false`) and requires its canonical hash to
   equal `expected_manifest_sha256`; a different hash is `409 manifest_mismatch`
   with the current hash in `details`;
2. re-reads the immutable `evaluation_reports` row, the evaluation binding
   (application, owner, `state='succeeded'`), the sealed snapshot and the sealed
   canonical manifest bytes (`compiler.VerifyManifestBytes`), and requires the
   report document to be a *real runner* application report for the same
   manifest/dataset/evaluation/snapshot. A synthetic evaluator, an empty
   (`not_run`) report, a wrong hash or a report owned by another application is
   refused with its own stable code;
3. re-applies T062's per-case run rule to every passed case (the case must be a
   real run of this evaluation, against the bound snapshot/manifest, finished);
4. checks the five author-case minimum (below);
5. re-locks the draft row in the transaction and compares
   `execution_revision` again, so a save that commits while the manifest is being
   compiled is `409 manifest_mismatch` rather than a mixed snapshot;
6. stores one immutable snapshot (`purpose_card`, `model_destinations`,
   `tool_capabilities`, `subapp_versions`, `connector_policies`, report id/hash,
   author-case coverage) and creates the review in the idempotency receipt's
   transaction. A duplicate content submission (same application + manifest hash)
   returns the existing review, whatever its state; the database partial index is
   the backstop.

The snapshot is the shared definition plus the report's own case identifiers and
categories. It contains no run input/output, no object key, no credential
reference and no other user's content.

### 3. The five author-case gate (interpretation of EVALUATION.md §6)

The normative sentence is `docs/spec/EVALUATION.md` §6: an application release
needs at least five author cases covering 正常 (normal), 坏输入 (bad input),
歧义/越界 (ambiguity/out-of-bounds), 工具失败 (tool failure) and 输出检查
(output check). The sealed report has no explicit "case category" member, so the
server enforces a documented minimum over the report's own per-case evidence
(`catalog.EvaluateAuthorCaseCoverage`, unit-tested):

- `normal`: a passed case whose collected run state is `succeeded`;
- `output_check`: a passed case carrying at least one passed `json_equals` or
  `decimal_equals` assertion;
- `bad_input` / `ambiguity_or_out_of_bounds` / `tool_failure`: a passed case
  carrying a passed `failure_reason` outcome (the T056 mechanism by which a
  bad-input case declares the business failure it expects), classified by the
  observed platform code: `tool_*`/`sandbox_*` → tool failure; `ambiguous_*`,
  `out_of_*`, `*_out_of_scope`, `limit_*`, `row_limit_*`, `over_limit`,
  `too_large`, `unsupported_input` → ambiguity/out-of-bounds; anything else →
  bad input.

The gate requires all five categories **and** at least 5 passed cases (so one
case satisfying several predicates cannot make it vacuous). It is enforced at
submission and re-evaluated at approval. This is a server-side policy over the
trusted runner's sealed evidence; the server cannot re-run an arbitrary author's
checker at approval time. The T068 report records this interpretation, and its
end-to-end test produces the five categories with a real runner report (two
normal cases, two schema-rejected inputs, an over-limit input and a real
`sandbox_unavailable` tool failure).

### 4. Approval is one transaction and always re-checks

`catalog.ReviewPublication` (admin role, `Idempotency-Key`, body
`{decision, manifest_sha256, reason}`):

- the body must name the review's frozen hash (`409 manifest_mismatch` with both
  hashes) and a bounded reason;
- approval re-reads the sealed report/bytes and the frozen manifest bytes, re-runs
  the author-case gate and re-runs the dependency readiness rules against the
  manifest's frozen closure (`catalog.RecheckFrozenDependencies`): a revoked tool
  model, sub-application version, connector policy or a changed pinned image
  digest/policy hash fails with `409 dependency_unavailable` and the located
  `dependency_*` evidence;
- the transaction locks the review row, proves it is still `submitted`, locks the
  application row, refuses an archived application, re-verifies the frozen
  manifest artifact row, allocates `version_no = MAX+1`, inserts the immutable
  version row + the frozen `version_dependencies` edges, moves
  `applications.active_version_id`, writes the outbox row and audit events, marks
  the review `approved` with reviewer/reason/version id, and stores the
  idempotent 200 body;
- rejection records `state='rejected'`, reviewer, time and reason, creates no
  version and leaves the draft editable. The state CAS refuses any later approval;
- two concurrent approvals serialise on the review row lock: one commits the
  version, the other answers `409 review_conflict`. There is no waiver parameter:
  every hard check always runs.

The version's `manifest_artifact_id` is the canonical manifest artifact sealed at
submission. Object storage is not transactional, so the "version JSON artifact"
cannot be *created* inside the SQL transaction; the honest design is to seal the
canonical bytes before the transaction, freeze the artifact id in the snapshot,
and have the approval transaction re-verify that row and hash before referencing
it. No bytes are rewritten, and a later publication never touches an old
version's JSON, hash or artifact (proved by hashing the sealed object before and
after a second publication).

### 5. Reads, ACL and the run-creation seam

- `GET /api/v1/reviews` is a keyset page over `(created_at, id)`; an administrator
  sees all, everyone else sees only reviews they submitted or reviews of an
  application they own. The predicate is in the same SQL WHERE as cursor/LIMIT.
- `GET /api/v1/reviews/{id}` returns the contract body with the stored snapshot;
  unknown and foreign reviews are one 404, so ids cannot be enumerated.
- `catalog.ResolveRunnableVersion(ctx, callerID, versionID)` is the read T070/T074
  build on: it returns an immutable version only when the caller may read the
  application, the version is not revoked and the application is not archived
  (distinct `ErrVersionRevoked`/`ErrVersionArchived`). T074 must call it before
  creating a Request/Run and copy the returned hash/artifact into the published
  snapshot; a stale catalog index can only supply a candidate id.

### 6. Non-contract creator preview and pages

There is no contract operation that returns the manifest projection before
submission, and the UX requires the creator to see purpose/risk/sub-versions/
report before submitting. T068 adds one documented non-contract read:

```
POST /api/v1/applications/{application_id}/publication-preview
     owner + creator role + CSRF -> the same shared-definition projection
     a submission would freeze (no review/version row)
```

It shares `buildPublicationContent` with the submission snapshot, so the preview
and the review cannot drift. `/apps/{id}/publish` and `/reviews[/{id}]` are plain
static pages driven by the official paths; their logic is executed by Node in
`tests/f02b/test_t068_pages.py` (no browser DOM is driven, the same boundary every
page ticket records).

## Consequences

- The public contract and `docs/spec/**` are untouched; `sync_spec.py --check`
  remains clean. The preview endpoint, the added columns and the outbox writer are
  documented non-contract extensions (this ADR, the migration header and the T068
  report).
- The completion guard remains the only writer of an `evaluation_reports` row;
  publication only reads that row and re-proves its evidence.
- T070 can implement revocation as one UPDATE on
  `application_versions(revoked_at, revoked_by, revoke_reason)` plus the same
  outbox/audit pattern; it must also stop new starts through
  `ResolveRunnableVersion` and may null the active pointer. The T068 schema and
  read seam are already ready for it.
- T074/T075/T077/T082 consume `application_versions.manifest_artifact_id`,
  `version_dependencies`, `outbox(kind='catalog_index')` and
  `ResolveRunnableVersion`; the exact interface is listed in the T068 report's
  "next ticket interface" section.
- The author-case gate is a policy over sealed evidence; if a later ticket adds an
  explicit case category to the evaluation-case contract, the classifier should be
  replaced rather than grown.
