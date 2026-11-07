# T104 / F13B — import adapted τ²-bench tasks and refuse empty checkers

**Branch / worktree:** `t/t104-tau2-import` / `E:\Orchara-wt\t104`, based on `integration/f13b` @
`5c7c506`; the final merge of `integration/f13b` @ `c132e8e` reported *Already up to date* (the
branch had already merged it). The commit that contains this report is the final tree commit.

**Status:** **passed** for the offline import, the empty-checker gate and the stub fixture
demonstration. The product evaluation runner, a real model, the upstream τ²-bench suite and the
recovery-boundary part of AC5 are **not_run** (details in §8). No τ²-bench result is claimed.

## 1. What was delivered

| Path | Purpose |
|---|---|
| `product/tests/f13b/external/tau2/source_pin.json` | real source pin: repo, commit, licence, per-file SHA-256, task ids/paths, acquisition commands, the reward=1 trap |
| `product/tests/f13b/external/tau2/LICENSE.tau2-mit.txt` | the upstream root MIT licence, byte-identical to the pinned file (SHA-256 `74741b87…`) |
| `product/tests/f13b/external/tau2/tasks.json` | three **adapted** evaluation cases, valid against `contracts/evaluation-case.schema.json` |
| `product/tests/f13b/external/tau2/fixtures/*.json` | three committed local booking fixtures (the adapted environments) |
| `product/tests/f13b/external/registry.json` | file/schema pins, families/templates, per-case source, licence, adaptation, environment, reference action trajectory and required facts |
| `product/tests/f13b/build_external.py` | deterministic builder for the generated external documents |
| `product/tests/f13b/test_t104_tau2_import.py` | 46 offline tests (pin, schema, gate, isolation, stub execution) |
| `product/tools/f13b_external.py` | loader, source pin validator, empty-checker gate, isolation check, offline fixture model + assertion mirror + CLI |
| `product/internal/evaluation/dataset_t104_external_test.go` | the adapted cases load through the product's strict `ParseDataset` |
| `product/tests/f13b/build_corpus.py` + `corpus/registry.json` | T102 registry now references the external registry (SHA-256 pin); the seven T102 case documents are unchanged |

No `docs/spec/**`, `contracts/**`, migration or product worker code was modified. Upstream tau2
source was cloned to `E:\Orchara\.scratch\f13b\T104\upstream` (not committed); only the source pin,
the MIT licence text and a small criteria/purpose extract are committed, as provenance evidence.

## 2. AC-by-AC

| AC (issue #105) | Verdict | Evidence |
|---|---|---|
| 1. Source commit/task/licence and adaptation are recorded | **passed** | §3 source table; `source_pin.json`; per-case `provenance.reference` = `sierra-research/tau2-bench@<commit>#task=<id>#data/tau2/domains/airline/tasks.json`; adaptation lives in the registry (never in the schema-restricted case) |
| 2. Task, environment and checker co-exist | **passed** | every case has `input.request` (task), a registered committed fixture + local HTTP endpoint surface (environment) and a non-empty checker; `validate_external` refuses a missing environment, fixture or checker (§4) |
| 3. Empty criteria / reward-basis tasks are refused | **passed** | `reject_reason` + `validate_tau2_source` + `validate_adapted_case`; 24 negative gate checks (§5) |
| 4. Adapted results never impersonate released benchmark results | **passed** | `usage_label`/`label` = `adapted_external`, `origin` = `adapted_external`, per-case adaptation notes; a test greps every committed import artifact for `official`/`leaderboard`/`rank` and requires none |
| 5. End-to-end demo + failure/authorisation/recovery boundaries + version/hash/command/result/profile; stub vs real split | **partial**: offline demo, failure/authorisation boundaries, hashes/commands/results/profile and the stub/real split are present; the **recovery boundary is not_run** (none of the three source tasks has recovery semantics) and no product runner executed the cases |

## 3. Source pin (AC1)

Upstream: `https://github.com/sierra-research/tau2-bench`, commit
`da0835f6c67baef58bdda18f2a3b037dfb3a1754` (committed 2026-10-29 22:37:05 -07:00; 2026-10-30 UTC),
read 2026-11-07 by `git init` + `git fetch --depth 1 origin <commit>` + detached checkout of
`FETCH_HEAD`; verified `HEAD == <commit>`. Network was available; no hash is invented.

| Upstream path | Role | SHA-256 | Bytes |
|---|---|---|---|
| `LICENSE` | licence (MIT) | `74741b87a33d4704c6f33e5c71742542446c984233f7a7e4905bf36f4da5a33b` | 1093 |
| `data/tau2/domains/airline/tasks.json` | tasks | `34dfcf0622792d0d0fa32aa491d21582e1b5d3ec1948e7743bae881885f878ea` | 159049 |
| `data/tau2/domains/airline/policy.md` | policy | `7db375ddcce5061cf9f48a5aabe1fc3e52aa2936d6a65b7c5b62e3c7ca54cfe4` | 7842 |
| `data/tau2/domains/airline/split_tasks.json` | split | `8249d7af4c654a48e5fa776993c3416c3b68c7213a26d00ec89e976c28363e97` | 1550 |
| `data/tau2/domains/airline/db.json` | environment DB (pinned, not committed) | `7184914bd3720d93f1160a09bb2724c3a5601d8ca39d02d371cbbfa62626f7e2` | 7247917 |
| `src/tau2/data_model/tasks.py` | `Task/EvaluationCriteria/Action/EnvAssertion` | `dd169761cf3449a504b528a452ad4d1ef6667ce18336eacd4a033630ad43c61e` | 23889 |
| `src/tau2/evaluator/evaluator.py` | evaluation entry + empty-criteria trap | `b7de29656b0c5f31c5b0b7ac9a7cdf58e90595f54aa03b7d326e3ebb1f8b1ecf` | 15431 |
| `pyproject.toml` | dependency declaration | `b9e5d3880fceb2ae2ccad2178d261c5ec6f7acbb4d13055c4ccb62bf26d6220c` | 2931 |

SPDX: **MIT**; the committed `LICENSE.tau2-mit.txt` is byte-identical (`74741b87…`). Only the root
repository licence was reviewed; per-domain/third-party data was not re-audited.

Per-task source hashes (canonical JSON of the upstream task object):
`task 11 c76cd2fd…`, `task 39 a455a770…`, `task 40 4928c624…`; the criteria-only digests are
`15a58278…`, `2f978bbc…`, `3d11b4f8…` respectively and are re-verified offline against the committed
criteria extract. The full-task digests are verifiable only against the pinned clone (residual risk
§8).

The empty-checker trap is reproduced at `src/tau2/evaluator/evaluator.py:134-139`:
`if task.evaluation_criteria is None: return RewardInfo(reward=1.0, ...)`. The import gate refuses
exactly this default success.

## 4. Imported subset (AC2)

All three adapted cases are `category=approval_effects`, `split=frozen_test`, and carry
`origin=adapted_external`, `license_review=reviewed`, `reference=<repo>@<commit>#task=<id>#<path>`.
The adapted actions preserve the upstream reference trajectory one-to-one (renamed tool names:
`get_user_details`→`get_customer`, `get_reservation_details`→`get_reservation`).

| Upstream task | Adapted case | Family | Checker | Adapted reference actions | Local environment |
|---|---|---|---|---:|---|
| 39 — refund eligibility refusal | `tau2-airline-39-refund-eligibility` | `tau2-airline-refund-eligibility` | 10 assertions: `run_state`, `effect_count(booking.cancel)=3`, 7× `json_equals(/bookings/<id>/status)`, `decimal_equals(/refund_total)=995.75` | 11 (1 customer + 7 reservation reads + 3 cancels) | `tau2-airline-task39-bookings` (7 bookings + refund policy) |
| 11 — downgrade without changing passenger count | `tau2-airline-11-flight-downgrade` | `tau2-airline-flight-change-refund` | 7 assertions: `run_state`, `effect_count(booking.update_flights)=1`, `effect_count(booking.update_passengers)=0`, cabin, flights, passenger list, `decimal_equals(/refund_total)=5244.00` | 1 (`update_reservation_flights`) | `tau2-airline-task11-booking` (fares 12880.00 → 7636.00, 2 passengers) |
| 40 — passenger name correction | `tau2-airline-40-passenger-correction` | `tau2-airline-passenger-correction` | 6 assertions: `run_state`, `effect_count(booking.update_passengers)=1`, `effect_count(booking.update_flights)=0`, full passenger list, cabin, status | 2 (reservation read + `update_reservation_passengers`) | `tau2-airline-task40-booking` (second passenger Mei Lee → Mei Garcia) |

23 assertions in total. Every case validates against the committed schema **and** loads through the
product's strict `ParseDataset` (`dataset_t104_external_test.go`). The environment is a committed
local JSON fixture plus a local HTTP endpoint surface (`/booking-desk/...`) declared in the
registry; τ²'s environment, user simulator and tools are not reused. Adaptations are recorded in
`registry.json` (`adaptation.preserved/changed/not_preserved`) and in the source pin.

T102 integration: `corpus/registry.json` now has an `external_imports` block pinning the external
registry's SHA-256 and commit and labelling it `adapted_external`; the seven T102 case documents
are byte-unchanged, and the T102 builder reproduces both. Imported families/templates do not
cross the T102 dev/frozen split; the measured near-duplicate gate reuses the T102 5-gram Jaccard
metric (threshold 0.75).

## 5. Empty-checker and empty-reward-basis refusals (AC3)

`product/tools/f13b_external.py` has two gates:

* `reject_reason(task)` — the upstream-shaped source gate with stable codes
  `missing_checker`, `invalid_checker`, `empty_criteria`, `no_actions`, `text_only_checker`,
  `empty_reward_basis`, `invalid_reward_basis`, `default_success`, `missing_user_scenario`,
  `missing_task_id`, `no_environment`, `invalid_task`.
* `validate_tau2_source(pin, tasks)` — re-checks the pin (commit, MIT SPDX, LICENSE SHA-256, task
  criteria SHA-256, acquisition evidence) and every task; `validate_external(external)` adds
  `validate_adapted_case` (schema, `adapted_external`, `reviewed`, reference shape, at least one
  observable assertion) and the T102 isolation rules.

Negative checks use **real τ²-shaped tasks** (the criteria extract is the real pinned task 39):
a task without `evaluation_criteria`, a criteria object with empty lists, an empty `reward_basis`,
a missing `reward_basis` (the Pydantic default trap), a text-only `nl_assertions` task, and a task
without an environment. Additional adapted-case negatives cover a `run_state`-only checker, an
unknown `text_similarity` assertion, adaptation data smuggled into the case, a missing environment
entry, an unreviewed licence, an unknown source task, a reference to another upstream task, an
empty checker, and forged cross-split family/template/near-duplicate cases.

The 24 negative gate checks are all green. Representative expectations (all asserted):

| Negative input | Refusal codes |
|---|---|
| `evaluation_criteria` absent/None | `missing_checker`, `empty_criteria`, `empty_reward_basis`, `default_success` |
| criteria `{actions:[], env_assertions:[], nl_assertions:[], communicate_info:[]}` | `empty_criteria`, `no_actions`, `default_success` |
| real actions but `reward_basis: []` | `empty_reward_basis` (of the actions, no `no_actions`) |
| real actions but no `reward_basis` key | `empty_reward_basis` (default-basis trap) |
| only `nl_assertions: ["Agent should refuse…"]` | `text_only_checker`, `no_actions`, `default_success` |
| adapted case with only `run_state: succeeded` | `default_success_checker` |
| `text_similarity` assertion | `schema_violation`, `unsupported_checker` |
| adapted case without a registry environment | `no_environment` |
| licence review `pending` | `license_not_reviewed` |
| source task id not in the pin | `unknown_source_task` |
| reference names task 11 while source is task 39 | `provenance_mismatch` |
| pin SPDX not MIT / wrong commit / wrong LICENSE SHA-256 | `source_unlicensed` / `source_commit_mismatch` / `license_hash_mismatch` |
| changed criteria against the pin | `source_criteria_mismatch` |
| forged cross-split family/template/near-duplicate | `cross_split_family` / `cross_split_template` / `near_duplicate_leak` |

## 6. Offline end-to-end demonstration (stub, AC5)

The tool provides a deterministic offline fixture model (`simulate_reference`) that applies the
adapted reference actions to the committed fixture, and a Python mirror of the five contract
assertion kinds (`evaluate_assertions`). This is explicitly a **stub**: it executes neither the
product runner nor τ²-bench, and it is not the Go checker.

Command (from `product/`):

```powershell
python tools/f13b_external.py --summary
```

Result (exit 0): `source=…@da0835f6…`, `license=MIT`, `usage_label=adapted_external`,
`cases=3`, `gate_issues=[]`; all stub assertions pass —
task 39: 10/10 assertions, effects `{booking.cancel: 3}`, `refund_total=995.75`;
task 11: 7/7, effects `{booking.update_flights: 1}`, `refund_total=5244.00`;
task 40: 6/6, effects `{booking.update_passengers: 1}`.

Failure boundaries (negative controls, asserted as failing): cancelling all seven reservations
fails `/bookings/UDMOP1/status`, `/bookings/XAZ3C0/status`, `/bookings/I6M8JQ/status`,
`/bookings/4XGCCM/status` and `booking.cancel` (7 recorded vs 3 expected); removing a passenger on
task 11 fails `/bookings/GV1N64/passengers` and `booking.update_passengers` (1 vs 0). The
authorisation boundary is the refund-policy gate itself (only policy-eligible cancellations may
change state and add to the refund total). The recovery boundary has no analogue in these three
τ² tasks and is recorded as `not_run`.

## 7. Gates (raw tails)

Environment: Python 3.12.2, pytest 9.0.3, jsonschema 4.26.0; Go 1.27.3
(`E:\orchara-toolchains\go\bin`); PostgreSQL 16.4 local cluster at `127.0.0.1:55432` for the T103
real-stack tests that also live in `tests/f13b`; `ORCHARA_PROFILE=dev-stub`; no real model
credentials. Commands were run from `E:\Orchara-wt\t104\product`; raw logs are in
`E:\Orchara\.scratch\f13b\T104\`.

```
pytest tests/f13b -q
  88 passed in 47.79s
  (48 T104 + 36 T102 offline + 4 T103 real-stack tests)

pytest tests/tooling -q
  21 passed in 4.72s

go build ./...
  exit 0 (no output)

go vet ./...
  exit 0 (no output)

go test ./...
  exit 0; 39 packages ok, 0 FAIL (last lines:
  ok orchara/platform/internal/workflow 0.937s
  ?  orchara/platform/probes/f15a_objectstore/localfs_probe [no test files])

gofmt -l internal cmd tools
  exit 0; empty output

python tools/sync_spec.py --check
  no drift: spec copy and contract snapshot match the manifest

python tools/f13b_external.py --summary
  exit 0; 3 cases, gate_issues=[], 23/23 stub assertions passed

python tests/f13b/build_external.py
  external import matches the builder

python tests/f13b/build_corpus.py
  corpus matches the builder

git merge integration/f13b
  Already up to date.
```

## 8. not_run / residual risks

* **not_run** — executing the adapted cases through the Orchara product evaluation runner: this
  build has no booking application, so the fixture-level stub is the honest demonstration. The
  Go `ParseDataset` acceptance is proven; a verdict is not.
* **not_run** — the upstream τ²-bench suite, its user simulator, its model calls and any released
  benchmark number. Nothing in this import is a τ² result; the adapted cases are not comparable to
  released scores.
* **not_run** — recovery-boundary assertions: tasks 11/39/40 contain no recovery semantics; no
  such assertion was invented.
* Full upstream task SHA-256 verification requires the pinned clone (not committed). Offline tests
  re-verify the committed criteria extract against the recorded `criteria_sha256` and the pin
  against the recorded file/commit/licence constants; they cannot re-read the upstream checkout.
* `db.json` (7.2 MB) is pinned by hash but not committed, so the upstream environment truth cannot
  be reconstructed from this repository alone.
* Only the root MIT licence was reviewed; per-domain or third-party task data was not re-audited.
* The offline assertion mirror is a Python design mirror, not the Go checker; equivalence was
  established by reading `internal/evaluation/assertions.go`, not by executing both.
* The refund policy is an adaptation for the isolated fixture, not τ²'s full policy engine.
* `pytest tests/f13b` now also runs T103's four real-stack tests, which need the local
  `product/.env.local` PostgreSQL password and the local cluster; the T104 suite itself is offline.
