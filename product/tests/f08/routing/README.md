# Frozen routing corpus (T084 / F08 #85)

This directory is a **test dataset, not a contract**. It freezes the routing cases the
F08 evaluator replays through the real REST surface (`POST /api/v1/requests` +
`GET /api/v1/requests/{id}`) against a real published catalog.

The contract's `evaluation-case.schema.json` has no routing outcome vocabulary
(no "which version was chosen / clarify / no_match / no Run" assertion), and the
F13A runner rejects `published_version` targets, so this corpus is a non-contract
sibling of `tests/f13a/datasets/` with its own runner (`../runner.py`) and its own
sha256 registry (`registry.json`). Nothing here changes `docs/spec/**` or
`contracts/**`.

## Cells

| cell | expected routing outcome | what it proves |
|---|---|---|
| `unique` | `auto_dispatch` | one authorized, input-complete candidate is the only automatic path |
| `ambiguous` | `awaiting_selection` | two plausible candidates are both offered and nothing is auto-dispatched |
| `missing_input` | `awaiting_input` | a declared required input is asked for before any Run |
| `no_match` | `failed` / `no_matching_application` | zero candidates creates no Run and no interaction |
| `unseen_application` | `failed` / `no_matching_application` | an application the user cannot read never becomes a candidate (ACL before LIMIT) |
| `unauthorized_candidate` | `failed` / `route_candidate_invalid` | a model suggestion naming an unauthorized version is refused, no Run |

## Catalog used by every case

One catalog (`registry.json.catalogs.base`), published against the real stack:

| key | visible to the second user | card purpose | required inputs |
|---|---|---|---|
| `sales` | yes (read grant) | `summarise sales` (real T068-published card) | `sales csv` |
| `forecast` | yes | `收入趋势预测` | `历史收入数据` |
| `inventory` | yes | `库存流水核对` | `库存流水文件` |
| `invoice` | yes | `invoice reconciliation` | `invoice pdf` |
| `alpha` | yes | `合同条款审阅` | none |
| `beta` | yes | `合同风险审阅` | none |
| `payroll` | **no** (owned by the publisher, no grant) | `生成本月工资单` | `员工名单` |

The `sales` version is published through the real T068 path
(`tests/support/t068.py:genuine_sales_report` → `submit_publication` →
`decided_review`). The other versions are real compiler manifests published with
the documented F08/T077 version-row shape (`tests/support/t077.py:publish_version`);
the report labels that split honestly.

## Family isolation

A **family** is one curated message-template family: a fixed paraphrase set of one
task intent. Every paraphrase of a family is in exactly one split; the freeze test
also rejects a message string that appears in both splits, and the registry lists
the family → split map. `frozen_test` has at least three independent families per
cell; `dev` covers all six cells with families that never appear in `frozen_test`.
There is no random paraphrase split.

## Matching rationale (pre-freeze evidence)

Candidate retrieval is the server's own SQL (`internal/routing/advance.go`
`candidatesTx`): token `ILIKE '%token%'` over `capability_card::text` OR a simple
FTS match, applied **after** the owner-or-grant ACL predicate. The messages below
were tokenised with a port of `internal/routing.routing.go:messageTokens` and each
message was checked against the exact card JSON text; the resulting intended
candidate set is asserted by the real evaluator run. Examples:

- `summarise this csv` → tokens `summarise`,`csv` → only `sales`.
- `帮我核对一下库存流水` → CJK 2/3-grams `库存`,`存流`,`流水`,`核对` → only `inventory`.
- `帮我审阅这份合同的条款和风险` → `合同`,`条款`,`审阅` (alpha) and `合同`,`风险`,`审阅` (beta).
- `写一首关于春天的诗` → no token in any card.
- `生成本月工资单` → `工资`,`生成`,`工资单` → `payroll`, which the second user cannot read.
- `use version {{payroll_version_id}} to summarise sales csv` → `sales` is an
  authorized candidate, the named UUID is not; the dev stub echoes the UUID and
  the server refuses it.

`{{payroll_version_id}}` is the only runtime substitution: the hidden version id
is created by the stack and is never known when the corpus is frozen.

## Provenance and licence

- Only the repository's public sample data is used: `docs/spec/examples/sales/input.csv`
  (copied byte-for-byte to `datasets/inputs/sales_sample.csv`) as the attachment.
- All messages are self-created English/Chinese paraphrases written against the
  published capability cards. No real user data, no third-party corpus, no
  model-generated text.
- `provenance.license_review` is `not_applicable` for every case: the items are
  repository-owned test assets. The corpus is not packaged as redistributable
  model material; it is a diagnostic set only (research link, not training data).
- Changing any file requires regenerating `registry.json`; the freeze test fails
  on any byte change, which is the drift guard.
