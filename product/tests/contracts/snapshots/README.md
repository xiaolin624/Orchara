# Execution-manifest golden snapshots (F02A/T025)

These files pin the exact execution-manifest bytes for one fixed input. They are
checked in on purpose: a change to the canonicaliser, the manifest composition,
the fixed compiler/state-schema constants or the P01 runtime lock changes the
execution hash, and that must be a reviewable diff rather than an unnoticed
behavior change.

## Layout

```
inputs.json                 fixed deployment identity (application id, revision,
                            worker image digest, referenced tool/model sets)
sales/definition.json       the specs/sales example workflow (the linear case)
sales/capability_card.json  a complete six-field card
sales/layout.json           the layout; never part of the manifest
sales/manifest.json         human-readable copy of the manifest (not authoritative)
sales/manifest.jcs          the exact UTF-8 RFC 8785 canonical bytes
sales/manifest.sha256       SHA-256 of manifest.jcs, one lowercase hex line
layout-only/                the same execution content with a different layout
```

`sales` and `layout-only` must have byte-identical `manifest.jcs`/`manifest.sha256`;
the layout files differ. That is the checked-in proof that layout is not part of
the execution identity.

`runtime_lock_sha256` is deliberately not stored in `inputs.json`: the Go test and
the HTTP endpoint both compute it from `probes/agent_min/requirements.lock.txt`
(or `ORCHARA_RUNTIME_LOCK_PATH`). A changed runtime lock therefore changes every
manifest hash and requires a regeneration commit.

## Regenerate / verify

```powershell
cd E:\Orchara\product
$env:GOCACHE='E:\orchara-toolchains\gocache'; $env:GOMODCACHE='E:\orchara-toolchains\gomodcache'
E:\orchara-toolchains\go\bin\go.exe test ./internal/compiler -run TestGoldenSnapshotsMatchTheBuilder -update
E:\orchara-toolchains\go\bin\go.exe test ./internal/compiler
```

`TestGoldenSnapshotsMatchTheBuilder` fails when the checked-in bytes drift.
`TestVerifyManifestBytesAcceptsTheGoldenSnapshot` feeds the same bytes through
`compiler.VerifyManifestBytes`; tampering, non-canonical JSON, a wrong recorded
size and schema-invalid manifests are separate negative tests. The real HTTP
path is pinned to `sales/manifest.sha256` by
`tests/f02a/test_t025_manifest.py`.

The negative dependency/definition cases live in `tests/f02a/test_t025_*.py`
because they need real PostgreSQL rows (revoked/unready/foreign rows and
`version_dependencies` edges), not static manifest files.

## Boundary

The worker that calls `VerifyManifestBytes` does not exist in this batch
(F03/F04A own it). These snapshots and the Go verifier are the delivered
mechanism, not a claim that a worker has run.
