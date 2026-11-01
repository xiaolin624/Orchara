# P05/T007 — one corpus, two languages, one verdict

The workflow DSL has two validators: Go validates at publish time, Python
re-validates defensively before compilation (`docs/spec/WORKFLOW-SPEC.md` §1).
They must not be allowed to guess differently about the same document, so this
probe holds one shared corpus and asserts that both implementations reach the
same verdict, the same ordered reason codes, the same locatable pointers and the
same canonical digests.

This is a **validator probe**, not the compiler: nothing here executes a graph.

```text
corpus.json          shared golden cases (40: 7 valid, 33 invalid)
rules.json           shared rule tables (types, config whitelists, patterns, limits, codes)
build_corpus.py      authors corpus.json from examples/ + named mutations; --check detects staleness
python/validate.py   Python reference implementation + CLI
go/                  stdlib-only Go implementation, own module, go test walks the corpus
```

Run it:

```powershell
python build_corpus.py --check
python python/validate.py --corpus corpus.json --check      # exit 0 == all pins reproduced
python python/validate.py --corpus corpus.json              # canonical wire envelope

# Go (the ticket's toolchain paths):
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
go -C go test ./...
go -C go run . -corpus ../corpus.json -check
go -C go run . -corpus ../corpus.json

python -m pytest product/tests/p05 -q                       # from the repo root
```

Both CLIs print the **same canonical bytes** for the corpus; the pytest suite
compares their stdout byte for byte.

## Corpus

Each case pins, and every value must be present (never `null` as a stand-in for
"absent"):

| field | meaning |
|---|---|
| `name` | unique case id |
| `source` | where the document came from (example file or the mutation applied) |
| `verdict` | `valid` or `invalid` |
| `input` | the complete workflow document |
| `input_sha256` | SHA-256 of the JCS canonical form of `input` — **every** case, positive or negative, pins a hash |
| `expected_hash` | valid only: SHA-256 of the canonical **normal form** (the data digest) |
| `expected_canonical` | optional anchor: the normal form as text, hand-written for one case so a reviewer can check the digest without running anything |
| `reason`, `reasons` | invalid only: the primary code and the full ordered list (precedence is part of the contract) |

Valid fixtures are the real contract examples (`examples/sales`,
`examples/parallel`, `examples/branch-approval`) plus two authored documents for
the cases the examples do not cover: a nesting case
(condition region containing a parallel fork/join,
`docs/spec/COMPILER-CONTRACT.md` §2/§4) and a three-node linear anchor. Negative
fixtures are single, named mutations of those documents. `build_corpus.py`
regenerates the file; `--check` fails when the checked-in copy is stale.

**Honesty note on the pins.** Unlike the JCS vectors (which are derived from RFC
8785), the *normal-form* digests have no third-party oracle: they are produced
by the Python reference from the documented normalisation rules below and then
independently reproduced by the Go implementation. A bug shared by both would
survive; a bug in either one cannot. The canonicalisation layer itself is
anchored by T003's RFC-derived vectors, which `go test` re-runs through this
probe's copy of the serialiser.

## The normal form (the data)

`NormalisedWorkflow` (Python) / `NormalizedWorkflow` (Go) is a **different
object** from `WorkflowArtifact`. The normal form is:

1. `nodes` ordered by node id;
2. every edge carries `port`, materialised as `"default"` when the document
   omits it, and `edges` ordered by (source, target, port);
3. set-valued config arrays ordered: `join.required_sources`,
   `join.optional_sources`, `agent.tool_versions`, `agent.subapp_versions`,
   `retrieve.knowledge_base_ids`.

Nothing else moves. Absent input bindings are never invented, and no missing
value is replaced by `null` — a document with a missing required value is
rejected with `missing_value` and produces **no** normal form at all. The digest
is `SHA-256(JCS(normal form))`, reusing `probes/jcs/python/jcs.py` in Python and
a port of the same RFC 8785 serialiser in Go (kept honest by re-running the
shared JCS vectors in `go test`).

## The artifact

`publish(data, provenance)` wraps the data digest into a descriptor:

```json
{"data_sha256": "...", "dsl_version": "1.0", "schema_id": "urn:agent-platform:workflow:1.0",
 "provenance": {"corpus_sha256": "...", "rules_sha256": "...", "input_sha256": "...",
                "data_sha256": "...", "probe": "p05_dsl", "probe_version": "1.0.0",
                "dsl_version": "1.0", "schema_id": "..."}}
```

Its digest is `SHA-256(JCS(descriptor))`, so it is always different from the data
digest and changes when provenance changes while the data digest does not. The
tests assert both directions.

## Stages and precedence

The pipeline mirrors the publish order in `WORKFLOW-SPEC.md` §1. **Only the
first stage that reports anything decides the verdict**, and the codes inside
that stage keep detection order:

| stage | question | codes |
|---|---|---|
| 1 | document shape, restricted JSON Schema subset, per-type config, policy | `bad_document`, `bad_id`, `unknown_type`, `bad_binding`, `bad_schema`, `bad_config`, `bad_edge`, `bad_policy` |
| 2 | identity and endpoints | `duplicate_id`, `unknown_endpoint`, `start_end_count` |
| 3 | DAG properties | `cycle`, `unreachable_node`, `cannot_reach_end`, `merge_without_join` |
| 4 | structured regions (in this order: pairing → ports → cross-region → join sources) | `invalid_region`, `bad_port`, `cross_region_edge`, `bad_join_sources` |
| 5 | references, pointers, dominance, missing values | `unknown_ref`, `bad_pointer`, `non_ancestor_ref`, `type_mismatch`, `missing_value` |

Within stage 1 the app-level schemas are checked first, then nodes in document
order (id → type → inputs → output_schema → config), then edges, then policy.
Stage 5 walks nodes in document order (bindings sorted by name), then prompt
templates, then the `end` output mapping, then condition cases. `rules.json`
declares every code with its stage; both implementations agree on the table, and
the corpus exercises **all 24 codes**.

Precedence is pinned by three probes, e.g.
`precedence-schema-over-duplicate-id`: the old documentation linter calls that
document `duplicate_id`, this contract calls it `unknown_type`.

Every problem carries a JSON-Pointer-ish `pointer` (`/nodes/3/inputs/summary`,
`/edges/4`, `""` for whole-graph problems) so both languages produce a
*locatable* verdict, not just a boolean.

## Where each rule comes from

`contracts/workflow.schema.json` is the authority for stage 1 (key sets, types,
patterns, enums, ranges, limits). It says nothing about the rest, which is
specified by `WORKFLOW-SPEC.md` §1–§4 and `COMPILER-CONTRACT.md` §1–§2:

- unique ids, one `start`/one `end`, reachability, DAG ordering, merges only at
  a `join` — WORKFLOW-SPEC §1, §4;
- a condition's exits must line up with `cases`/`default_port`, every condition
  needs a paired join (`join_id` ↔ `fork_id`), a plain fork must be declared by
  the join it feeds, `required_sources`/`optional_sources` must cover the
  region's real branch endpoints and stay disjoint — WORKFLOW-SPEC §4,
  COMPILER-CONTRACT §2;
- node outputs are objects, inputs are only `{literal}` or `{ref}`, pointers
  must resolve inside the producer's declared `output_schema`, referenced nodes
  must be control-dependency ancestors, a value must exist on every executable
  path, templates may only read already bound inputs, and a missing value is
  never an implicit `null` — WORKFLOW-SPEC §2, COMPILER-CONTRACT §1.

## Interpretations this probe had to choose (and why)

- **Dominance is decided by "ancestor but not dominator ⇒ `missing_value`."**
  An ancestor that is not on every path can be skipped; reading it would be an
  implicit null, which the spec forbids. It is not reported as
  `non_ancestor_ref` because it *is* an ancestor.
- **Region entry/exit dominance is enforced edge by edge**, not as a whole-region
  rejection. Any path that leaves a region without reaching its join shows up as
  `cross_region_edge` on the offending edge, which is more locatable than
  `invalid_region`. Pairing itself (`join_id`/`fork_id`/fork declaration) is
  checked structurally first.
- **A pointer is only rejected when the schema proves the field absent**
  (`additionalProperties: false`, a non-object type, or an array without
  `items`). Open objects cannot prove absence, so they are accepted.
- **A missing `value` on a comparison is `missing_value`** (not `bad_config`):
  the schema makes `value` optional, but `eq` without an operand would be an
  implicit null comparison. `exists` needs no operand.
- **Set-valued arrays are sorted** in the normal form; they are `uniqueItems`
  sets in the contract, so order carries no meaning.
- **Edges between two branches of the same region are not rejected.** The spec
  forbids crossing *region* boundaries, not joining sibling branches, and this
  probe does not invent a stronger rule.
- **An empty region** (fork straight to its join) is allowed; the contract does
  not forbid it.

## Not verified here (do not read as passing)

- Execution semantics: activation tokens, skipped branches, join firing counts,
  `missing_branch_value`, LangGraph compilation (that is T011's probe).
- Publish-time dependencies, authorization, test gates and content hashing
  (WORKFLOW-SPEC §1 stages after references).
- Runtime value types: a `ref`-bound input has no static type, so `gt`/`in` are
  only type-checked when the binding is a literal.
- `end` inputs that are not in `output_schema.properties` are not rejected, and
  `input_schema`/`output_schema` are only checked as restricted object schemas,
  not resolved into a full type system.
- No concurrency, storage or API path is involved; there is nothing to be
  `blocked` on in this ticket, and no credentials are used.

## Reference-only: the old documentation linter

`tools/validate_contracts.py` keeps a `graph_errors` helper described in its own
docstring as "a limited documentation linter, not the full publisher/compiler".
`product/tests/p05/test_dsl_parity.py` runs it over the corpus and records the
gap instead of assuming it: it cannot run on one malformed document, stays
silent on 18 of 33 negatives, uses a smaller and partly different vocabulary
(`condition_ports`, `invalid_join_pair`, `unknown_reference`, …) and disagrees
about precedence. Nothing in this corpus is derived from it.
