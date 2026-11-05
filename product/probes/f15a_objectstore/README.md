# F15A/T053 — S3-compatible object-store compatibility probe

A deployer runs this probe against a **candidate** S3-compatible service before
selecting it. It produces per-assertion, machine-readable evidence for signing,
staging/sealed separation, key-path boundaries and delete behaviour. An
incompatible implementation is rejected by the probe (exit `1`), not accepted by
hand-waving.

This is a **probe product**, not a platform feature: no HTTP endpoint, no UI, no
database change. The platform still uses its local-filesystem store
(`internal/objectstore`); a green run here only says a candidate could back it.

## Run it

```powershell
# Non-secret target settings may be flags or environment; credentials are
# environment-only (a command line is visible to every local user).
$env:F15A_S3_ENDPOINT            = "https://s3.internal:9000"
$env:F15A_S3_REGION              = "us-east-1"
$env:F15A_S3_BUCKET              = "orchara-objects"
$env:F15A_PREFIX                 = "orchara-prod"
$env:F15A_S3_ACCESS_KEY          = "<runtime access key>"
$env:F15A_S3_SECRET_KEY          = "<runtime secret key>"
$env:F15A_S3_PROVISION_ACCESS_KEY = "<server/maintenance access key>"
$env:F15A_S3_PROVISION_SECRET_KEY = "<server/maintenance secret key>"

python probes/f15a_objectstore/object_store_probe.py `
    --out reports/f15a/candidate.json `
    --short-ttl 5 --timeout 10 --retries 2
```

Exit codes:

| code | meaning |
|---|---|
| `0` | every assertion passed (or was honestly `not_run`, e.g. no versioning) |
| `1` | a real compatibility violation was observed |
| `2` | configuration/environment failure — **including “no candidate endpoint”** |

`--out`/`--emit` writes JSON; stdout is the human summary. The JSON never
contains a credential value or an object body: sizes, SHA-256 digests and
probe-generated key names only.

## What the probe asserts

| id | rule | failure means |
|---|---|---|
| `anonymous_read_denied` | unsigned GET of a sealed key is 401/403 and never returns bytes | public bucket / leaked object |
| `anonymous_put_denied` | unsigned PUT is denied and creates nothing | open write |
| `anonymous_list_denied` | unsigned `list-type=2` is denied and returns no key list | bucket enumeration |
| `out_of_range_key_denied` | outside prefix, sibling prefix and absolute keys are denied; a `..` request cannot escape into the sealed namespace; a URL signed for key A cannot fetch key B | credential scoped too broadly or a path-normalising proxy |
| `sealed_put_denied` | the runtime credential cannot PUT into `sealed/` and no object appears | staging credential can overwrite sealed objects |
| `staging_overwrite_preserves_sealed` | overwriting a staging key with different bytes leaves the same-named sealed object byte-identical | shared namespace between staging and sealed |
| `short_lived_signature_expiry` | a presigned GET works before its TTL and is rejected after it | signatures do not expire |
| `proxy_download_boundary` | HEAD exposes exact Content-Length for a >20 MiB object, the proxy decision refuses to stream it, Range gives 206 + exact `Content-Range`, unsatisfiable Range gives 416; also records whether a leaked pre-expiry URL can be revoked | the platform's download boundary cannot be enforced |
| `delete_semantics` | runtime cannot delete sealed; provisioner delete yields 404, is idempotent, and spares a sibling key | delete resurrects or over-deletes |
| `versions_immutable` | if the candidate offers versioning, old sealed bytes stay addressable; otherwise `not_run` | versioning enabled but broken |

`--short-ttl` controls the expiry test (default 5 s, minimum 5); `--expiry-skew`
documents a candidate's clock grace and is added to the wait. The default is
`0` (strict) because a signing implementation that accepts expired URLs is a
security property, not a tolerance. The 5 s floor is deliberate: SigV4
`X-Amz-Date` has whole-second resolution, so a 1 s TTL leaves a 0–1 s window in
which a loaded host can lose the accept-before-expiry check. The probe computes
the URL's absolute `expires_at` and sleeps past it (plus the documented skew)
instead of racing a fixed second.

## Deployment preconditions the probe checks but cannot create

The probe needs **two credentials** because the platform has two roles:

* **runtime** (`F15A_S3_ACCESS_KEY`): staging read/write/delete, sealed read;
  never sealed write/delete, never another tenant prefix;
* **provisioning** (`F15A_S3_PROVISION_ACCESS_KEY`): the server/maintenance role
  that writes and deletes sealed objects — used to seed and verify boundaries.

On AWS/MinIO/SeaweedFS this is an IAM/bucket-policy configuration. If the
provisioning credential cannot write `sealed/`, the probe reports
`blocked`/exit 2 rather than blaming the candidate for the deployment's policy.
Path-style addressing is used (`/bucket/key`); virtual-hosted-only endpoints are
not supported and are rejected by connection failure.

## Discrimination proof (real, but not acceptance evidence)

`fixtures/compliant_server.py` and `fixtures/violating_server.py` are real HTTP
servers (stdlib only) implementing the S3 subset above, with independent SigV4
verification. They exist to prove the probe has teeth:

* compliant fixture → 9 assertions pass, versioning `not_run`, exit `0`;
* violating fixture, one broken rule at a time → that assertion fails, the others
  pass, exit `1`;
* violating fixture, all rules → 9 failures, exit `1`.

```powershell
python -m pytest tests/f15a -q          # 36 tests, real subprocesses
python probes/f15a_objectstore/run_evidence.py --out reports/f15a
```

**A fixture is not a candidate S3.** Passing them proves our probe and policy
logic, never the vendor's IAM/ACL/versioning behaviour. MinIO/moto-style stubs
must not be recorded as candidate acceptance either.

## Local-filesystem analogue

The product's real store is `internal/objectstore` (local filesystem + HMAC
links). `localfs_probe/main.go` runs the real package against a real directory and
records what *can* be proven here: key typing/traversal refusal, staging↔sealed
separation, the sealed write boundary, link TTL/op/max-bytes binding, idempotent
delete and the 20 MiB cap. It is labelled `not_s3_evidence: true` and cannot
prove anonymous access, IAM, versioning, backup or TLS.

Two determinism rules are visible in the report and asserted by `tests/f15a`:
the HMAC link TTL is 5 s with a 1 s expiry margin (no timer race), and the signing
key is hex-encoded because `NewSigner` applies `TrimSpace` — a raw random key could
trim below the 32-byte minimum and fail the probe for a probe-input reason. The
probe also asserts the trim rule itself (padded long key accepted, trimmed-short
key refused) so that regression is covered deterministically.

## Evidence

`reports/f15a/` holds the real runs: `candidate-absent` (blocked, exit 2),
fixture compliant/violating matrix, transient-retry run, local-FS analogue, and
`runs.json` with commands, exit codes and report digests. `environment-profile.json`
records the machine facts (no Docker/S3/Redis; `mc` is the Windows Message
Compiler, not the MinIO client; WSL has `runsc` but is not an accepted isolation
host and is irrelevant to this ticket).

## Limits

A passing candidate run is a **compatibility observation**, not a licence,
backup/restore, capacity or encryption review. The probe cannot see beyond the
two credentials and endpoint it was given, and `versions_immutable` stays
`not_run` unless the candidate exposes versioning. The acceptance rule is in
[ADR 0008](../../docs/adr/0008-f15a-object-store-compatibility.md): **no green
probe run against the candidate → not selected.**
