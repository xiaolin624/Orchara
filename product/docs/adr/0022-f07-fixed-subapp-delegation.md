# ADR 0022 — Fixed sub-application delegation at publication time

- Status: Accepted (F07/T075, issue #76)
- Date: 2026-11-05
- Number: `0022` because `0020` was already taken on the integration branch
  (T069's deletion foundation) and the merger renumbers collisions; this file is
  the next free product ADR number on `integration/t068-t082` at the time of
  writing.
- Extends: spec ADR 0011 (explicit versioned sub-application delegation), ADR
  0004 (F00B/F02A dependency vocabulary), product ADR 0004 (dependency error
  codes and the validate contract)
- Depends on: F02B/T068 (the version publisher that writes
  `platform.version_dependencies`)

## Context

`call_app` has existed in the DSL and in the manifest closure since F02A:
`contracts/workflow.schema.json` fixes its config to exactly
`{application_version: uuid}`, `internal/catalog/manifest.go` collects that
reference as a fixed sub-application, and the readiness/cycle rules already
reject a version that is missing, revoked, archived, invisible or part of a
cycle. Two publication-gate holes remained:

1. `call_app.inputs` was only structurally validated (`literal`/`ref`), and the
   node's `output_schema` was never compared with the child version's frozen
   `definition.input_schema`/`output_schema`. A creator could declare a mapping
   that sends a string where the child requires an integer, omit a required
   child input, or promise downstream a field the child never returns — the
   first of those would only fail at runtime, the others not at all before T078.
2. `policy.max_child_depth` / `policy.max_child_calls` were only range-checked
   by the schema validator. Nothing measured the real
   `platform.version_dependencies` closure, so a policy of depth 1 could still
   pin a child whose own published version closes over three generations.

The workbench also had no way to author the node: the F04A canvas palette is
deliberately `start/python/agent/end` and the editor had no `call_app` panel.

## Decision

### 1. The I/O mapping is checked against the child version's frozen definition

The check lives in `internal/catalog/delegation.go` and runs inside
`ValidateExecution` after readiness and cycle detection (so it gates
`POST /applications/{id}/validate`, the publication preview and the publication
submission through one seam). The child schema is read from the stored
`application_versions.manifest -> 'definition'`; a version row whose manifest has
no frozen definition is `dependency_not_ready` because the mapping cannot be
checked against anything.

The rules are deliberately static and minimal — the validator never guesses a
value the document does not determine:

| problem | code | pointer |
|---|---|---|
| a mapped input field the child does not declare | `io_mapping_unknown_field` | `/nodes/<i>/inputs/<field>` |
| a required child input field with no binding | `io_mapping_missing_field` | `/nodes/<i>/inputs/<field>` |
| a binding whose statically known type the child schema refuses | `io_mapping_type_mismatch` | `/nodes/<i>/inputs/<field>` |
| a declared node output the child never produces | `io_output_unknown_field` | `/nodes/<i>/output_schema/properties/<field>` |
| a declared node output type the child cannot produce | `io_output_type_mismatch` | `/nodes/<i>/output_schema/properties/<field>` |

Type compatibility follows the contract's reference rule: identical JSON types,
plus widening `integer -> number`. A binding whose type cannot be known
statically (a producer schema with no `type`) is never reported — the check
reports only what it can prove. `additionalProperties` is honoured on the child
input side only: a child that explicitly allows additional properties may
receive an undeclared mapped field, but a child never *guarantees* undeclared
outputs, so the parent may not declare one. The node's output schema may always
expose fewer fields than the child declares; it may never invent one.

Value-level constraints (`enum`, `minimum`, `format`, …) are not re-checked here:
they are enforced when the child input is materialised and validated against the
same frozen schema in T078.

### 2. Child limits are measured over the real closure

- `policy.max_child_calls` bounds the number of fixed child references the
  definition declares: every `call_app` node counts once, and each distinct
  version in one `agent.subapp_versions` allowed set counts once (the set is a
  declaration, not a call per duplicate entry). Counting declared references is
  a static upper bound on the runtime count T078/T080 will enforce.
- `policy.max_child_depth` bounds the depth of the closure read from
  `platform.version_dependencies`: the referenced child version is depth 1, its
  children depth 2, and so on. The closure rows, not the draft, are the source
  of truth, so a grandchild published under the child version is counted.

`child_calls_exceeded` is located at `/policy/max_child_calls` (the field that
declares the limit); `child_depth_exceeded` is located at the node/config field
that names the offending child version. A cyclic edge set cannot make the depth
walk hang: the cycle rule rejects it first and the walk is memoised.

### 3. The workbench authors the node in the editor, not in the F04A canvas

The F04A canvas bundle is a committed Vue Flow app whose contract is a single
linear four-node chain (`start/python/agent/end`); adding a fifth node type with
a published-version picker and a mapping table would change that feature's
shape, and the canvas app is not executable by the Node page tests (only its
`lib/*.js` modules are). T075 therefore adds a real `call_app` panel to the
editor page (`editor.html` + `editor.js`):

- the application/version pickers are built from the existing ACL-filtered
  catalog/version reads (`GET /applications`, `GET /applications/{id}/versions`);
  a version the creator cannot read is never listed and the detail read answers
  404, so the picker cannot offer a foreign version;
- the mapping rows are generated from the selected version's frozen definition;
- the node is inserted before `end` and the chain is rewired, then written into
  the same definition textarea the page has always saved (ETag/412/428
  semantics unchanged);
- "保存并校验" saves the draft and renders the server's located errors against a
  node strip, highlighting every node named by an error.

**The canvas bundle is not rebuilt** and its palette still lists four node
types; that limitation is recorded as `not_run` in the T075 report. The
revocation flag is not in the contract's version read, so the picker cannot
pre-filter revoked versions client-side; the server's `dependency_revoked` is
the authority and the panel shows it inline.

### 4. The publisher writes the edges, but a dependent version cannot be published yet

F02B/T068's approval transaction already writes one
`platform.version_dependencies` row per entry of the frozen
`manifest.subapp_versions` set (which contains both `call_app.application_version`
and `agent.subapp_versions`, proven by the Go test
`TestCollectDependencyRefsLocatesEveryConfigField`). Publication is the only
writer of `application_versions`/`version_dependencies` and no statement updates
an existing version row or edge, so a published parent's pinned reference is
immutable by construction.

At this baseline, however, **no API path can publish a version that contains a
fixed sub-application**: submission requires a passed five-category evaluation
report, and the only graph runtime (`internal/presetexec`) refuses every node
type outside `start/python/agent/end` (`plan.go:123-127`). T078 (#79) implements
child runs; only then can a `call_app` definition pass an application
evaluation. T075's real-stack tests therefore materialise the publisher's stored
shape from the **real validator's sealed manifest bytes** (real object store,
real canonical manifest, real `application_versions` row, real edges from the
manifest's own subapp set) and say so. The end-to-end publish-then-repin
assertion is recorded as `partial` with T078 as the owner; the pinning property
itself (a newer child version never replaces the frozen reference, and the old
version is still the resolved one) is asserted through validation, the sealed
manifest bytes and the version read.

## Consequences

- The validate contract's error array carries five new located codes; its `code`
  member is a free string, so no contract change is needed and
  `python tools/sync_spec.py --check` stays clean.
- The child's frozen definition is now read on every validation with a
  sub-application reference: one extra `jsonb` column on the existing
  sub-app readiness query, no second query.
- Readiness/cycle errors still take precedence: a revoked child is reported once,
  at its config pointer, and the mapping is not checked against a version the
  platform refuses to call.
- A published version row without a frozen definition (only reachable through a
  pre-T075 fixture) is refused as `dependency_not_ready` rather than silently
  treated as an empty schema.
- T078 must reuse `checkChildDelegation`'s rules as the spawn-time re-check for
  the same definition; it must not invent a second mapping compatibility table.
- The picker is ACL-filtered by the server, and revoked versions are surfaced by
  validate until T070's revocation endpoint and a contract version flag exist.
