# ADR 0034 — Go graph runtime, the LangGraph proof, and the non-contract node data plane

- Status: Accepted (F04B batch T083–T091, issues #84–#92)
- Date: 2026-11-06
- Extends: ADR 0005 (non-contract bridges are documented, not silently added)
- Related: `reports/f04b/T083-condition-branching.md`, `T085-parallel-optional.md`,
  `T086-model-node.md`, `T087-approval-node.md`, `T088-retrieve-node.md`,
  `T089-callapp-node.md`, `T091-ten-node-workflow.md`

## Context

`COMPILER-CONTRACT.md:3` says "LangGraph负责图执行/持久化，平台编译器负责受限DSL到框架图的
正确映射"; `ARCHITECTURE.md:32` lists "worker/compiler | 固定 DSL+编译器/依赖版本 → LangGraph |
Python；执行内部节点与 checkpoint"; `DESIGN-SESSION.md` Q36/Q37 (`:120`/`:124`) choose a Go
backend/control plane and leave the Python LangGraph/DeepAgents execution layer as a separate
decision. The implementation that actually shipped does not match that sentence:

- Since F04A (T042/T043/T049) the **production** run executor is
  `internal/presetexec` in Go, claimed and executed by `cmd/supervisor exec` over the worker
  mTLS protocol, with `runs`/`run_attempts`/`run_events`/`run_node_outputs` as the platform
  authority (`EXECUTION.md:9,16`). The Python `worker/platform_worker` compiler validates and
  builds LangGraph `StateGraph`s, but no Python graph has ever executed a production Run.
- F04B added the structured-region graph path (`graphplan.go`/`graphrun.go`): condition/join
  regions, a concurrent ready-queue scheduler, optional-branch convergence, and the
  model/approval/retrieve/agent/call_app node types on top of the same Go runtime
  (T083/T085–T089, T091 ten-node acceptance).
- The Python side is still exercised as an **independent proof**:
  `worker/platform_worker/compiler/regions.py` + `tests/f04b/test_t083_langgraph_regions.py`
  run real `langgraph 1.1.6` and assert activation sets, skipped sets and join call counts
  with a recording stub adapter. That proof is required by the spec's golden corpus
  (`COMPILER-CONTRACT.md:42`, "重放后 join 只触发一次" and the two-wait join line) but it is
  not the production runtime.

Leaving this as a silent drift would make every later report claim a runtime that does not
exist. This ADR records the deviation, its reason and the boundary; the spec text itself is
**not** rewritten in this batch (no `docs/spec/**` change, `sync_spec --check` stays clean).

## Decision

### 1. Go `internal/presetexec` is the authoritative F04B graph runtime

The production executor for structured regions and all ten node types is the Go scheduler:

- `graphplan.go` re-proves the sealed DAG (single start/end, DAG, reachability, merge rule,
  fork/join pairing, dominance/post-dominance regions, required/optional source coverage) from
  the manifest bytes; it never trusts the publish validator alone.
- `graphrun.go` is a single-owner scheduler: all ready nodes get a goroutine, results return
  through one channel, a failure cancels in-flight branches, drains them, emits `skipped` once
  per attempt and commits one terminal proposal; join runs once per region activation.
- Node types execute through the same platform wrapper (input/output schema, node result rows,
  status events, deadline/error classification) as the linear F04A/F05 path.

Rationale: the production control plane, lease/fencing model and worker protocol are Go
(`internal/execution`, `cmd/supervisor`, `internal/api/internal.go`). Introducing a second
Python executor would duplicate the attempt/lease/resume authority across two runtimes and
would require a new worker image/checkpointer contract that this batch does not have. The
Python LangGraph proof remains the independent semantic evidence the spec asks for; it is
reported separately as a proof, never as production execution.

Consequence: a feature is only "executed in production" when the Go scheduler runs it. Any
report that says LangGraph executes a Run is wrong and must be corrected. A future migration
to a Python runtime is a new ADR with its own attempt/lease/resume migration plan.

### 2. Non-contract node output convention (node data plane)

Node results are **not** OpenAPI contract fields. They are stored as node data artifacts and
read by the run view through the existing owner-only chain
`GET /runs/{id}/node-results` (non-contract) → `GET /artifacts/{id}/content` (contract), or
reused by the executor through the recovery seam. The frozen F04B shapes are:

| node | node data output (`kind=data` artifact) |
|---|---|
| condition | `{port, matched_case}` — the selected port and case index (or `null`) |
| join | `{results:{<source_node_id>:{status, value?, error_code?}}}`; `status` ∈ succeeded/failed/skipped; a converged optional failure has `status=failed` + `error_code`, no `value` |
| retrieve | `{items:[{chunk_id,document_id,document_revision,document_state,chunk_no,span,excerpt,citation}], citations:[{chunk_id,document_id,document_revision,span,excerpt}], count}`; `excerpt` is the first 500 characters of the chunk; **no full chunk body and no vector score**; `citation`/`span` are the stored citation JSON forwarded verbatim |
| model | text mode `{text}`; json mode the structured object validated by the node's `output_schema` |
| approval | `{approval_id, decision, decided_at, params_sha256, proposal}` — server decision facts plus the resolved proposal the human saw |

Retrieve's narrowing follows `ROUTING-MEMORY-KNOWLEDGE.md:23` (R3: citations preserve
`doc_id`/version/chunk id/span/**short excerpt**) and N-R10 (do not expose chunk bodies,
embeddings or vector scores as public fields); the full body stays readable only through the
owner-only `GET /api/v1/knowledge-citations/{chunk_id}` route. The internal worker retrieval
route still returns text/score so the server can rank and the worker can merge bases; those
fields never become node data. All node data is validated against the node's sealed
`output_schema` before it is registered, and the recovery seam re-validates a reused object
against the same schema.

### 3. Non-contract internal routes (worker mTLS)

Two worker-only routes are added outside `contracts/**` (documented in the T087/T088
reports):

- `POST /internal/v1/runs/{run_id}/retrieval` — body
  `{attempt_token, knowledge_base_ids, query, top_k}`; the server re-proves the live attempt
  token, derives the owner from `runs.owner_id` (the body never carries one), runs the F10
  knowledge service's ACL-filtered query per base, merges by score with a stable chunk-id
  tie-break, and returns `{items, requested, authorized}`. A base the owner cannot see is
  silently excluded and counted.
- `POST /internal/v1/runs/{run_id}/node-approvals` — body carries the node key and the
  proposal material; the server rebuilds the canonical proposal from the sealed manifest and
  run facts, computes `params_sha256`, and creates or replays the approval row
  (`UNIQUE(run_id,node_key,params_sha256)`). Approval decisions are executed through the
  existing public approval API; the worker route never returns a decision token.

Neither route is reachable from the public listener (`RequireInternalMTLS`, worker role); the
supervisor certificate is refused. They are not added to `contracts/openapi.json`.

### 4. Known limits recorded with this deviation

- **F08 chat input (T084)**: a natural-language chat message is routed and submitted, but the
  sales workflow's `start` node requires a JSON object, so execution fails with
  `node_input_invalid`. T084's journey uses a JSON task document; the integration gap is
  documented, not hidden.
- **Optional source publishing (T085)**: the canvas/publish layer accepts
  `optional_sources` pointing at agent/call_app/model nodes, but the runtime only converges
  pure read/compute failures and fails closed for writes/approval/unknown outcomes. A stricter
  publish-time rejection is deferred (see below) because it needs a contract-validator change
  and a corpus/hash rebuild; until then the canvas warning plus fail-closed runtime behavior
  are the guard.
- **Retrieve output (T088)**: narrowed in this batch to `excerpt`/no score (see §2); this ADR
  is the record that the earlier full-body output was a bug.
- **Approval expiry (T087)**: there is **no automatic expiry sweeper**. Expiry is observed on
  the decision path (`decide` returns 409 `approval_expired` and the run fails with
  `approval_expired`); a run parked in `waiting_approval` does not terminate merely because
  time passes. T087 is therefore reported as `partial`.
- **T083/T085 replay join-once**: the replay evidence is a Go fake-seam (`NodeOutputSource`)
  test plus later real-resume coverage in T087/T091; the direct "same run, second exec,
  retrieve/model join once" smoke is `not_run` in T083/T085 and is claimed only as indirect
  coverage by T091.

## References

- `COMPILER-CONTRACT.md:3` (the LangGraph sentence this ADR deviates from), `:23` (join output
  envelope), `:31–37` (NodeAdapter semantics; retrieve/approval bullets), `:42` (golden corpus,
  including the two-wait join and replay join-once rows).
- `WORKFLOW-SPEC.md:23` (ten node types), `:40` (structured regions).
- `ROUTING-MEMORY-KNOWLEDGE.md:23` (R3 provenance/short excerpt), `:31` (cache keys).
- `EXECUTION.md:9,16` (supervised Go execution, platform authority), `:34` (interrupt/resume),
  `:74` (child runs).
- `DATA-MODEL.md:22` (checkpoints are framework-owned), `DESIGN-SESSION.md:120,124` (Q36/Q37:
  Go backend, Python LangGraph layer as a separate decision).
- `.scratch/t083-t091/notes/D-f04b-semantics.md`: N-R10 (no chunk bodies/embeddings/vector
  scores as public fields), R3 (short excerpt), A1–A13 (approval node), R1–R11 (retrieve node),
  P1–P4 (call_app child protocol).

## Consequences

- Every F04B report must say "Go runtime" when describing production execution and mark the
  Python LangGraph rows as proof/seam, not production.
- Adding a graph node type still touches several switch points (plan type set, dispatch,
  config re-verification, canvas palette/panel/export, run view). That is accepted for this
  batch; a node-type registry refactor is deferred and recorded in
  `reports/review/BATCH-REVIEW-FIXES.md`.
- The node data plane can evolve without an OpenAPI change, but any change to these shapes
  must update the owning report and the run-view/canvas consumers in the same commit.
- No `docs/spec/**` or `contracts/**` change; `python tools/sync_spec.py --check` remains
  clean. A future spec edit that keeps the LangGraph sentence must link to this ADR (or a
  successor) so the deviation is never silent again.
