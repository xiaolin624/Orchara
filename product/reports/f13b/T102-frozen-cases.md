# T102 / F13B — Frozen seven-category internal cases with mechanical split isolation

- **Issue:** GitHub #103 (`[T102][F13B] 冻结七类内部案例并证明没有切分泄漏`)
- **Branch:** `t/t102-frozen-cases` (worktree `E:\Orchara-wt\t102`, based on `integration/f13b` @ `30642be`)
- **Status:** **passed for the ticket's deliverable (frozen corpus + static proof).**
  Case execution by a runner or a real model is **`not_run`** — this ticket freezes
  inputs, truth and collection rules; it does not execute them. "Schema-valid" is
  never reported as "executed".
- **Ownership boundary:** the corpus is internal and self-created. No external
  benchmark, no vendor model, no PostgreSQL, no sandbox and no product runtime was
  used by this ticket's tests.

## 1. Deliverables

| Deliverable | Path |
|---|---|
| Cases, registry and fixtures | `product/tests/f13b/corpus/**` |
| Deterministic corpus builder (source of truth) | `product/tests/f13b/build_corpus.py` |
| Loader / validator / leak detector / rejection reasons | `product/tools/f13b_corpus.py` |
| Offline corpus tests | `product/tests/f13b/test_t102_corpus.py` (+ `conftest.py`) |
| Product-parser consumption proof | `product/internal/evaluation/dataset_f13b_corpus_test.go` |
| This report | `product/reports/f13b/T102-frozen-cases.md` |
| Batch summary | `E:\Orchara\.scratch\f13b\T102-REPORT.md` |
| Raw gate logs | `E:\Orchara\.scratch\f13b\T102\` |

Not modified: `product/docs/spec/**`, `product/contracts/**`, migrations, and every
existing `product/tests/f13a/**` file.

## 2. Corpus shape and coverage

Seven case documents, one per contract category, each exactly
`{"cases":[...]}` — the shape the committed `internal/evaluation/dataset.go`
strictly parses (also proven by the Go test). Case fields are exactly the
`additionalProperties:false` members of
`contracts/evaluation-case.schema.json`; template/scenario/truth/licence/facts
live in `corpus/registry.json`, keyed by `case_id`.

**46 cases — 25 dev / 21 frozen_test — 20 families — 178 assertions.** Every case
has at least one assertion, a family, a split, a fixture, provenance and a
registry entry.

| Category | Dev | Frozen | Cases | Families | Assertions | Covered boundaries (TESTING §4) | Truth source |
|---|---:|---:|---:|---:|---:|---|---|
| sales | 7 | 3 | 10 | 5 | 32 | CSV/XLSX equivalence, missing column, duplicate order, negative amount, formula, over-limit; frozen: large two-decimal amounts, empty input, invalid encoding | `independent_decimal_reference` (`tests/f13a/reference/sales_reference.py`, stdlib + `Decimal`, no product tool import) |
| routing | 3 | 3 | 6 | 2 | 24 | dev: unique match, ambiguous match, missing input; frozen: no match, unseen application family, unauthorized candidate | `human_labeled_catalog_condition` (derived from the committed catalog snapshot) |
| rag | 3 | 3 | 6 | 2 | 27 | dev: answered + citation, missing target chunk, fabricated citation; frozen: wrong answer, revoked source, stale version | `committed_source_span_truth` (committed source snapshot: ids/page/version/status/text) |
| memory | 3 | 3 | 6 | 3 | 29 | dev: confirm, reject, contradictory preference; frozen: modify, delete, user/application isolation | `human_labeled_platform_state` |
| delegation | 3 | 3 | 6 | 2 | 22 | dev: same sub-app concurrency, duplicate delivery, independent threads; frozen: parent budget, full-slot wait, skipped-child join | `human_labeled_effect_ledger` (stub ledger counts, idempotency keys, quota ledger) |
| approval_effects | 3 | 3 | 6 | 4 | 20 | dev: duplicate approval, parameter change, wrong approver; frozen: write-then-lost-receipt, cancel, expired approval | `human_labeled_effect_ledger` (canonical parameter hash, actual write count, unknown reconciliation) |
| lifecycle | 3 | 3 | 6 | 2 | 24 | dev: old graph restore, terminal late event, expired lease with live worker; frozen: delete-then-recover, Redis loss, deleted-input reuse | `human_labeled_platform_state` (DB/artifact invariants) |

The seven sales rejection cases share one family/template and are all in **dev**;
the frozen sales cases are different tasks (boundary sum, empty file, invalid
encoding), so no rejection template was split. The other categories split by
**application/document family** (support vs operations catalog; product handbook
vs policy portal; personal store vs frozen store; etc.), not by rewriting one
template across the split.

## 3. Split isolation — measured, not asserted

Implemented by `f13b_corpus.split_leakage()` and frozen by
`test_family_and_normalized_template_never_cross_splits` and
`test_the_measured_max_cross_split_near_duplicate_is_below_the_threshold`.

Method:

1. **Family:** every `family_id` maps to exactly one split. Measured
   `cross-split families = 0` (20 families, each in one split).
2. **Normalized template:** lowercase, 64-hex/SHA-256 → `<sha>`, UUID → `<uuid>`,
   long hex → `<hash>`, numbers/amounts → `<num>`, punctuation removed. Measured
   `cross-split normalized templates = 0`.
3. **Near duplicates:** character 5-gram Jaccard over the case's task statement
   (`template + scenario + fixture_id + canonical input`), threshold **0.75**,
   pinned in `registry.thresholds`; the detector ignores assertion boilerplate
   (same checker on different tasks is not leakage; same task with different
   checker is). Every cross-split pair is measured.

Measured values (46 cases, 25 × 21 = 525 cross-split pairs):

| Measurement | Value |
|---|---|
| Cross-split families | **0 / 20** |
| Cross-split normalized templates | **0 / 20** |
| Maximum cross-split similarity | **0.4018** (`approval-dev-wrong-approver` ↔ `approval-frozen-cancel`) |
| Pairs at/above 0.75 across splits | **0** |
| Maximum within-split similarity (discrimination check) | **0.6606** (`memory-dev-confirm-preference` ↔ `memory-dev-reject-preference`) |
| Reported threshold / n-gram | **0.75 / 5** |

The maximum cross-split similarity is 0.4018, i.e. 0.3482 below the reported
threshold; the same-split maximum is higher, so the low cross number is not a
zero metric. Forged cases (same family, same template or same text across the
split) are rejected with `cross_split_family`, `cross_split_template` and
`near_duplicate_leak` respectively — tested, see §6.

## 4. Truth and licence traceability

Every registry case entry carries `truth.source`, `truth.derivation`, `license`,
`provenance`, `required_facts` (and, for routing, the derived catalog
`condition`). `registry.json` also pins:

- `schema_sha256 = 826d3bc3db25bc1fd8e9538b7e59d59b792da47951b1af7ae004402737675a20`
  (the committed `product/contracts/evaluation-case.schema.json`);
- every case document by SHA-256/size/case-count (tests read the bytes back);
- every fixture by path (committed) or deterministic generator (over-limit CSV,
  GB18030 CSV) with its SHA-256; each case's `input.task_input` must pin the same
  hash/media type;
- the routing catalog snapshots and the RAG source snapshots the truth is derived
  from.

Independent truth per category:

| Category | Independent truth (not product output) | Mechanical check in the suite |
|---|---|---|
| sales | `tests/f13a/reference/sales_reference.py` — stdlib CSV/XLSX reader + `Decimal`, imports nothing from the product | recomputes every successful case's total/regions/order count from the committed fixture bytes; every rejected case must raise the case's `expected_failure` code |
| routing | committed app catalog (apps/capabilities/grants per split) | derives unique/ambiguous/no-match/unseen/unauthorized/incomplete from the catalog and requester, then compares the label and the expected decision |
| rag | committed source snapshot (id/page/version/status/text) | every allowed citation is an active committed source; fake ids are absent; revoked/stale status and the wrong-answer fact are checked against the text |
| memory, lifecycle | human-labeled platform state/revision/ext-generation invariants | labels are frozen in the registry with derivations; effect counts must be 0 for isolated/control cases |
| delegation, approval_effects | effect ledger + idempotency/canonical-parameter/quota labels | duplicate delivery expects 1 dispatch for 2 deliveries; budget rejection expects 0; lost receipt expects 1 write + `needs_reconciliation`; cancel/expiry expect 0 |

Licence: all cases are `self_created`, `license_id =
LicenseRef-Orchara-SelfCreated`, `redistributable = true`, `review =
not_applicable`, matching `provenance.origin/license_review`; provenance
references the case document. No third-party text, user data, secret or external
benchmark item is included.

## 5. Empty / skipped / incomplete is never a pass

`f13b_corpus.reject_reasons(case)` returns stable codes before/independent of the
JSON schema; `is_decidable(case, registry_entry)` returns `False` when any
collection fact or assertion-derived fact is missing. The registry declares
`collection.required_facts = [collection.complete, eval.artifact_manifest,
run.id, run.input_sha256]` plus a category fact and every
`result_json:<pointer>`, `run.terminal_state`, `stub_ledger:<capability>`,
`citations.source_ids` the assertions need.

Case-level codes tested: `empty_assertions`, `empty_input`, `skipped_case`,
`missing_license_review`, `missing_provenance`, `unsupported_category`,
`invalid_split`, `missing_fixture_id` (schema refusals carry
`schema_violation` as well).

Corpus-level refusals tested: `cross_split_family`, `cross_split_template`,
`near_duplicate_leak`, `duplicate_case_id`, `registry_missing_case`,
`missing_license`, `collection_incomplete`, `facts_incompatible_with_assertions`,
`file_pin_mismatch`, `schema_pin_mismatch`.

**20 negative checks** (8 parametrized case-level + 10 forged-corpus/rule
refusals + 2 `is_decidable == False` assertions); every one asserts the stable
code, not just "an error".

## 6. AC mapping

| Issue AC | Evidence | Status |
|---|---|---|
| Same-template rewrites do not cross train/dev/test | 20 families, 0 cross-split families, 0 cross-split normalized templates, max cross-split similarity 0.4018 < 0.75; forged cross-split family/template/duplicate are rejected | **met (mechanical)** |
| Public truth and licence are traceable | registry pins schema + every file + every fixture; per-case truth source/derivation/licence/provenance/facts; sales truth recomputed by an independent Decimal reference; routing/RAG checked against committed snapshots | **met** |
| Empty/skipped/incomplete is not passed | `reject_reasons` + `is_decidable` + 20 negative tests; no case is counted as run | **met** |
| Demonstrate through the end-to-end entry; failure/authorization/retry boundaries have assertions | The cases *declare* failure/authorization/retry/unknown assertions, and the product's own `ParseDataset` consumes all seven documents (Go test). **Executing them through the real runner is `not_run`** — no executor or real model exists in this ticket/batch | **not_run (execution) / corpus frozen (declarations)** |
| Record version, input hash, commands, raw results, environment; stub/real separated; blocked/not_run explicit | fixture/hash/schema pins, command tails and environment below; stub/real separation and not_run list | **met for the static ticket; execution not_run** |

## 7. Gates (raw tails, from `E:\Orchara-wt\t102\product`)

```
> python -m pytest tests/f13b -q
....................................                                     [100%]
36 passed in 6.21s

> python -m pytest tests/tooling -q
.....................                                                    [100%]
21 passed in 4.84s

> python -m pytest tests/f13a/test_t056_fixtures.py -q     # offline F13A dataset subset
.......                                                                  [100%]
7 passed in 1.31s
```

Full `tests/f13a` was deliberately **not** run: it starts the real PostgreSQL /
mTLS / object-store / WSL2-runsc stack, and this ticket is offline by envelope.
The offline T056 dataset/fixture suite above is the relevant non-regression check.

```
> go build ./...        # no output, exit 0
> go vet ./...          # no output, exit 0
> go test ./...
ok  	orchara/platform/internal/evaluation	2.446s
... (all packages ok; tail: GO_EXIT build=0 vet=0 test=0)

> gofmt -l internal cmd tools      # empty
> python tools/sync_spec.py --check
no drift: spec copy and contract snapshot match the manifest
```

Raw logs: `E:\Orchara\.scratch\f13b\T102\{pytest-f13b,pytest-tooling,pytest-f13a-offline,go-build,go-vet,go-test,gofmt,sync-spec}.txt`.

## 8. Environment profile

- Windows host, worktree `E:\Orchara-wt\t102`, branch `t/t102-frozen-cases`,
  base `integration/f13b` @ `30642be`.
- Python 3.12.2; `jsonschema` 4.26.0; Go 1.27.3
  (`E:\orchara-toolchains\go\bin`), `GOCACHE`/`GOMODCACHE` under
  `E:\orchara-toolchains`.
- Offline: no PostgreSQL cluster used, no WSL2/runsc, no model, no network.
- Case targets are declarative `debug_snapshot` ids; no snapshot is published or
  resolved by this ticket.

## 9. `not_run` and residual risks

**`not_run`**

- Executing any case through `PlatformTaskRunner`/the real evaluation API.
- Real-model answers, routing decisions, RAG generation, judge scores.
- Populating the `required_facts` from a real artifact (the list is declared, not collected).
- Publishing the referenced `debug_snapshot` targets.
- Full `pytest tests/f13a` (real-stack suite), by scope/offline envelope.

**Residual risks / honest limits**

1. The corpus is declarative: a case passing schema and loading through
   `ParseDataset` says nothing about product behavior. The report never counts it
   as an executed pass.
2. Routing/memory/delegation/approval/lifecycle labels are human-labeled plus a
   mechanical invariant check; only sales and RAG have a fully independent
   recomputation from fixture/source bytes. The labels are traceable but are not
   statistical ground truth.
3. The near-duplicate metric is character 5-gram Jaccard over task-statement
   text; it does not prove semantic non-leakage, only that no rewrite is
   textually near-identical across the split. Families are split by
   application/document family, not randomly.
4. Sales dev has 7 cases vs 3 frozen because the six TESTING §4 sales boundaries
   plus the CSV/XLSX equivalence pair cannot be covered by six cases (the
   equivalence pair needs two documents), and keeping all rejection variants in
   one family/split avoids a template leak. Counts are coverage-driven, not
   random.
5. The registry summary and case documents are reproducible from
   `tests/f13b/build_corpus.py`; a hand edit without regenerating is a test
   failure. This is intentional for a frozen corpus but means edits require
   running the builder.
6. `sync_spec --check` covers `docs/spec` + contract snapshot drift only; this
   ticket did not change either, and the check is no-drift as recorded above.
