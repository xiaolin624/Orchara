# T066 — Mem0 vs PostgreSQL, decided by isolated evidence

This directory is a **throwaway evaluation harness**, not a product feature and not a
product dependency. It answers one question with recorded evidence: *should Orchara
replace or wrap its PostgreSQL personal-memory baseline with Mem0?*

The answer recorded in `evidence/t066-both.json` is **`do_not_adopt`** (keep
PostgreSQL). The details, numbers and the remaining F11 gate are in
`product/reports/f09/T066-mem0-comparison.md`.

Nothing under `product/internal` imports this code, `mem0ai` is not in any product
runtime lock, and the product database is never used: the PostgreSQL side runs in a
throwaway `orchara_t066_mem0_<hex>` database and the Mem0 side runs in a temporary
directory with `MEM0_TELEMETRY=false`.

## What runs

| side | what is real | what is not |
|---|---|---|
| PostgreSQL (`pg`) | real PostgreSQL 16.4, real `cmd/admin migrate`, real `internal/memory` `Create`/`Update`/`Delete` (via the compiled `experiments/mem0/pgdriver`), real sealed object bytes, real revision/tombstone/deletion-job/epoch rows, real copy enumeration over every text column + object file | the query-level recall **metric** is experiment code (T063 runtime recall is not in this branch's baseline); both sides share the same deterministic local embedder; `t066.memory_vectors` is an experiment-owned stand-in index |
| Mem0 (`mem0`) | real `mem0ai` 2.2.1, its real SQLite `history.db`, its real Qdrant local vector store, real `add(infer=False)` / `update` / `delete` / `search`, real copy enumeration over SQLite tables, Qdrant payloads, files and logs | **stub run**: no model provider exists here, so `infer=True` extraction, reranking and model query rewriting are not exercised; the embedder is a deterministic local hash model served over a 127.0.0.1 OpenAI-compatible endpoint |

The fixture (`mem0exp/fixture.py`, seed 66) is synthetic and isolated: 2 users,
3 applications, 9 confirmed memories, 1 revision, 1 deletion, 1 pending candidate,
8 positive queries and 4 negative (cross-user, cross-application, deleted,
pending-candidate). Its canonical SHA-256 is recorded in the result.

## Run it

```powershell
# 1. one-time isolated venv + locked dependencies (mem0ai must never enter the product lock)
python -m venv experiments/mem0/.venv
experiments/mem0/.venv/Scripts/python -m pip install --require-hashes -r experiments/mem0/requirements.lock.txt

# 2. the comparison (the venv interpreter is needed for the Mem0 side)
$env:MEM0_TELEMETRY = "false"
experiments/mem0/.venv/Scripts/python experiments/mem0/run_experiment.py both `
    --out experiments/mem0/evidence/t066-both.json `
    --iterations 21 --top-k 3

# 3. the harness tests (system Python is enough: they never import mem0)
python -m pytest tests/f09/test_t066_mem0_experiment.py -q
```

`pg` alone needs only the system interpreter (real PG + compiled binaries are
built on demand under `.target/`), and is what the test suite exercises:

```powershell
python experiments/mem0/run_experiment.py pg --out experiments/mem0/evidence/t066-pg.json
```

Exit codes: `0` every requested side ran (the decision may still be
`do_not_adopt`), `2` a side was `blocked` (e.g. no isolated venv, no PostgreSQL),
`1` the harness produced an invalid record (a bug).

## The result file

`experiments/mem0/evidence/t066-both.json` is the deliverable. It records:

* environment, versions, fixture manifest and SHA-256, harness/lock/embedder hashes;
* every command that produced it, with the input hashes;
* per system: status (`run`/`blocked`/`not_run`), evidence level (`real`/`stub`),
  `hit@1` / `hit@k`, per-query results, latency (`p50`/`p95` over ≥20 runs per query),
  storage (bytes/rows/dependencies) and the deletion **copy-cleanup enumeration**;
* the verdict plus the `F11` gate that formal adoption would still need.

`mem0exp/records.py::validate` rejects a record that claims adoption while a
system did not run, while a copy remains, while only stub evidence exists, or while
F11 has not passed; `mem0exp/decision.py` contains the rule and its unit tests.

## Copy cleanup: what is enumerated

After deleting one confirmed memory, both sides enumerate every place its body can
still be:

* **PostgreSQL**: every `text`/`varchar`/`json`/`jsonb` column of every base table in
  the isolated database (`strpos`, one query per column, reported per table/column),
  every file under the isolated object store, the tombstone/revision, the pending
  `deletion_jobs` row and its `remaining_scopes`, and `policy_epochs`. The durable
  log-like store is `platform.audit_events`; no service process is started, so no
  file log exists on this side (recorded as `not_applicable`, not as clean).
* **Mem0**: the SQLite `history` and `messages` tables row-by-row, every Qdrant
  collection's payloads, every file under the work directory (including the Qdrant
  storage SQLite file), and the process logs — both with DEBUG deliberately enabled
  (to enumerate that possible copy) and at the default WARNING level (a second tiny
  instance proves whether the body appears without DEBUG).

## Limits

See the report §6. In short: no model provider, no `infer=True`, no deployed
Qdrant/PostgreSQL vector index, no multi-worker history sharing, no backup/restore,
no browser UI. Formal adoption remains gated on **F11 deletion governance** even if
the numbers ever change.

## Cleanup

`.venv/`, `.target/` and `work/` are git-ignored and disposable. The committed
evidence does not contain credentials and does not contain memory bodies.
