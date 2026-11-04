"""The PostgreSQL side of the T066 comparison, on real isolated PostgreSQL 16.4.

Scope of what is real here:

* the throwaway database and ``cmd/admin migrate`` (the product's real schema);
* the memories themselves: ``Create``/``Update``/``Delete`` are executed by the
  real ``internal/memory`` service through the compiled ``experiments/mem0/pgdriver``,
  so sealed objects, artifacts, revisions, tombstones, deletion jobs and cache
  epochs are the product's own writes — not a Python imitation of them;
* the recall *metric* is experiment code, because the baseline branch has no
  runtime recall implementation (T063 owns it). It uses the same deterministic
  embedder as the Mem0 side and the T058 guards (owner+application, not deleted,
  current revision) in one SQL statement, and an experiment-owned
  ``t066.memory_vectors`` table stands in for the embedding index T063 does not
  create. This limitation is recorded in the result, never hidden;
* the copy-cleanup enumeration walks every text/json column of every table in the
  isolated database, the sealed object bytes on disk and the experiment log
  surface, for the deleted body and for the superseded revision body.

The product database is never used: without ``--dsn`` the runner creates
``orchara_t066_mem0_<hex>`` and drops it afterwards; a supplied DSN is refused
when its database is the development product database.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.parse
import uuid
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[3]
TOOLS_ROOT = PRODUCT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pg_dev  # noqa: E402

from . import embedder  # noqa: E402

MIN_ITERATIONS = 20
PURPOSE_MEMORY = "memory_body"


class PgBlocked(RuntimeError):
    """The PostgreSQL side cannot run; the runner records ``blocked``."""


# ---------------------------------------------------------------------------
# real psql client (the same seam product/tests/support/stack.py uses)
# ---------------------------------------------------------------------------
def _psql(dbname: str, query: str, *, timeout: int = 300) -> str:
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    completed = subprocess.run(
        [
            pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            dbname,
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "-",
        ],
        # SQL goes through stdin, not argv: the copy-cleanup scan is one generated
        # UNION over every text column and would exceed the Windows command line.
        input=query,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"psql failed ({completed.returncode}) for {dbname}:\n{completed.stdout}\n{completed.stderr}"
        )
    return completed.stdout.strip()


def _rows(dbname: str, query: str) -> list[dict]:
    payload = _psql(dbname, f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({query}) AS t")
    return json.loads(payload or "[]")


def _scalar(dbname: str, query: str) -> str:
    return _psql(dbname, query)


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _array_literal(vector: list[float]) -> str:
    return "ARRAY[" + ",".join(repr(float(value)) for value in vector) + "]::double precision[]"


# ---------------------------------------------------------------------------
# isolated database lifecycle
# ---------------------------------------------------------------------------
def create_isolated_database() -> tuple[str, str]:
    if not pg_dev.is_running():
        raise PgBlocked(
            "the isolated PostgreSQL cluster is not running (python tools/pg_dev.py init)"
        )
    name = "orchara_t066_mem0_" + secrets.token_hex(6)
    pg_dev.run_sql(f'CREATE DATABASE "{name}"', database="postgres")
    return pg_dev.dsn(name), name


def drop_database(name: str) -> None:
    if not pg_dev.is_running():
        return
    try:
        pg_dev.run_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', database="postgres")
    except SystemExit:  # teardown must not mask the real result
        pass


def database_name_from_dsn(dsn: str) -> str:
    parsed = urllib.parse.urlsplit(dsn)
    name = parsed.path.lstrip("/")
    if not name:
        raise PgBlocked("the supplied DSN has no database name")
    return name


def check_isolated_dsn(dsn: str) -> str:
    """Refuse the development product database; everything else is throwaway."""
    name = database_name_from_dsn(dsn)
    if name == pg_dev.DEFAULT_DB:
        raise PgBlocked(
            f"refusing to run the experiment against the development product database '{name}'"
        )
    return name


# ---------------------------------------------------------------------------
# real product binaries
# ---------------------------------------------------------------------------
def _clean_env(dsn: str, object_dir: str | None = None) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("ORCHARA_")}
    env["ORCHARA_PROFILE"] = "dev-stub"
    env["ORCHARA_DB_DSN"] = dsn
    if object_dir:
        env["ORCHARA_OBJECT_DIR"] = object_dir
    return env


def build_binary(package: str, destination: Path) -> dict:
    """Build one experiment/product binary and record the command."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(_go_binary()), "build", "-o", str(destination), package]
    completed = subprocess.run(
        command, cwd=str(PRODUCT_ROOT), env=_go_env(), capture_output=True, text=True, timeout=600
    )
    if completed.returncode != 0:
        raise RuntimeError(f"go build {package} failed:\n{completed.stdout}\n{completed.stderr}")
    return {
        "cmd": f"go build -o {destination} {package}",
        "exit": completed.returncode,
        "sha256": _file_sha256(destination),
    }


def _go_binary() -> str:
    toolchain = _toolchain()
    return toolchain.go_binary()


def _go_env() -> dict[str, str]:
    return _toolchain().go_env()


def _toolchain():
    if str(TOOLS_ROOT) not in sys.path:
        sys.path.insert(0, str(TOOLS_ROOT))
    import toolchain  # noqa: F401  (lazy: only needed when the runner builds binaries)

    return toolchain


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def migrate(admin: Path, dsn: str) -> dict:
    completed = subprocess.run(
        [str(admin), "migrate"],
        cwd=str(PRODUCT_ROOT),
        env=_clean_env(dsn),
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"admin migrate failed:\n{completed.stdout}\n{completed.stderr}")
    return {
        "cmd": "cmd/admin migrate (ORCHARA_DB_DSN=<isolated throwaway>)",
        "exit": completed.returncode,
        "output_tail": _redact((completed.stdout + completed.stderr).strip()[-2000:]),
        "sha256": _file_sha256(admin),
    }


def _redact(text: str) -> str:
    secret = pg_dev.load_secret()
    return text.replace(secret, "<redacted>") if secret else text


def _run_driver(
    driver: Path, mode: str, payloads: list[dict], dsn: str, object_dir: str
) -> list[dict]:
    stdin_text = "\n".join(json.dumps(payload, ensure_ascii=False) for payload in payloads) + "\n"
    completed = subprocess.run(
        [str(driver), mode],
        cwd=str(PRODUCT_ROOT),
        env=_clean_env(dsn, object_dir),
        input=stdin_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"pgdriver {mode} failed:\n{completed.stdout}\n{completed.stderr}"
        )
    results = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        decoded = json.loads(line)
        if not decoded.get("ok"):
            raise RuntimeError(f"pgdriver {mode} refused: {decoded.get('error')}")
        results.append(decoded)
    return results


def _run_bench(
    driver: Path, payload: dict, dsn: str, object_dir: str
) -> list[dict]:
    completed = subprocess.run(
        [str(driver), "bench"],
        cwd=str(PRODUCT_ROOT),
        env=_clean_env(dsn, object_dir),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"pgdriver bench failed:\n{completed.stdout}\n{completed.stderr}")
    decoded = json.loads(completed.stdout.strip())
    if not decoded.get("ok"):
        raise RuntimeError(f"pgdriver bench refused: {decoded.get('error')}")
    return decoded["runs"]


# ---------------------------------------------------------------------------
# fixture seeding
# ---------------------------------------------------------------------------
def _seed_identity(dbname: str, data) -> tuple[dict[str, str], dict[str, str]]:
    user_ids: dict[str, str] = {}
    app_ids: dict[str, str] = {}
    statements = ["BEGIN;"]
    for user in data.users:
        principal_id = str(uuid.uuid4())
        user_ids[user.key] = principal_id
        statements.append(
            "INSERT INTO platform.principals (id, kind, display_name) VALUES "
            f"({_quote(principal_id)}::uuid, 'user', {_quote(user.display_name)});"
        )
        statements.append(
            "INSERT INTO platform.users (principal_id, login, password_hash, must_change_password) "
            f"VALUES ({_quote(principal_id)}::uuid, {_quote(user.login)}, "
            "'t066-isolated-fixture-no-credential', false);"
        )
    for application in data.applications:
        application_id = str(uuid.uuid4())
        app_ids[application.key] = application_id
        statements.append(
            "INSERT INTO platform.applications (id, owner_id, name) VALUES "
            f"({_quote(application_id)}::uuid, {_quote(user_ids[application.owner_key])}::uuid, "
            f"{_quote(application.name)});"
        )
    statements.append("COMMIT;")
    _psql(dbname, "\n".join(statements))
    return user_ids, app_ids


def _seed_pending_candidate(dbname: str, data, user_ids, app_ids, object_dir: Path) -> dict:
    """A real pending candidate row + sealed body, seeded read-only by the fixture builder.

    The product's only way to create a candidate is a live worker run in a running
    run (T054 proves that path); re-running that whole run stack is out of scope
    for a comparison experiment. The fixture copies the same row/artifact/object
    shape so the recall boundary "a candidate is never recallable" can be tested
    from the recall side. Recorded as fixture-seeded, not as product evidence.
    """
    candidate = data.candidate
    body = candidate.text.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    key = "sealed/" + secrets.token_hex(32)
    path = object_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    artifact_id = str(uuid.uuid4())
    candidate_id = str(uuid.uuid4())
    _psql(
        dbname,
        "BEGIN;"
        "INSERT INTO platform.artifacts (id, owner_id, object_key, purpose, sha256, media_type, "
        "size_bytes, state, revision) VALUES "
        f"({_quote(artifact_id)}::uuid, {_quote(user_ids[candidate.owner_key])}::uuid, {_quote(key)}, "
        f"'memory_candidate', {_quote(digest)}, 'text/plain; charset=utf-8', {len(body)}, 'ready', 1);"
        "INSERT INTO platform.memory_candidates (id, owner_id, application_id, body_artifact_id, "
        "source_run_id, logical_key, state, expires_at) VALUES "
        f"({_quote(candidate_id)}::uuid, {_quote(user_ids[candidate.owner_key])}::uuid, "
        f"{_quote(app_ids[candidate.app_key])}::uuid, {_quote(artifact_id)}::uuid, NULL, "
        f"{_quote('t066-' + candidate.key)}, 'pending', now() + interval '7 days');"
        "COMMIT;",
    )
    return {
        "key": candidate.key,
        "id": candidate_id,
        "artifact_id": artifact_id,
        "object_key": key,
        "state": "pending",
        "seeded_by": "experiment fixture builder (not the T054 worker propose path)",
    }


# ---------------------------------------------------------------------------
# measurements
# ---------------------------------------------------------------------------
def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, int(round(fraction * len(ordered) + 0.5)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def _latency_block(durations_ms: list[float], embed_ms: list[float], runs: int) -> dict:
    return {
        "iterations_per_query": runs,
        "runs": len(durations_ms),
        "p50_ms": round(_percentile(durations_ms, 0.50), 6),
        "p95_ms": round(_percentile(durations_ms, 0.95), 6),
        "embed_p50_ms": round(_percentile(embed_ms, 0.50), 6),
        "embed_p95_ms": round(_percentile(embed_ms, 0.95), 6),
        "query_p50_ms": round(_percentile([total - embed for total, embed in zip(durations_ms, embed_ms)], 0.50), 6),
        "query_p95_ms": round(_percentile([total - embed for total, embed in zip(durations_ms, embed_ms)], 0.95), 6),
        "measurement": "end-to-end: Python query embedding + one real SQL recall round trip",
        "note": "latency is host-dependent; recall, storage and cleanup are deterministic",
    }


def _scan_columns_for(dbname: str, needle: str) -> dict:
    """Find every text/json column of every base table that still holds ``needle``."""
    columns = _rows(
        dbname,
        "SELECT c.table_schema, c.table_name, c.column_name, c.data_type "
        "FROM information_schema.columns AS c "
        "JOIN information_schema.tables AS t "
        "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
        "WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema') "
        "  AND t.table_type = 'BASE TABLE' "
        "  AND c.data_type IN ('text', 'character varying', 'character', 'jsonb', 'json') "
        "ORDER BY c.table_schema, c.table_name, c.ordinal_position",
    )
    branches = []
    for column in columns:
        relation = (
            f'"{column["table_schema"]}"."{column["table_name"]}"'
        )
        branches.append(
            f"SELECT {_quote(column['table_schema'] + '.' + column['table_name'] + '.' + column['column_name'])} "
            f"AS location, count(*) AS row_count FROM {relation} "
            f"WHERE strpos(coalesce(\"{column['column_name']}\"::text, ''), {_quote(needle)}) > 0"
        )
    hits: list[dict] = []
    if branches:
        rows = _rows(dbname, " UNION ALL ".join(branches))
        hits = [row for row in rows if int(row["row_count"]) > 0]
    return {
        "tables_scanned": len({(c["table_schema"], c["table_name"]) for c in columns}),
        "columns_scanned": len(columns),
        "hits": hits,
        "needle_sha256": hashlib.sha256(needle.encode("utf-8")).hexdigest(),
    }


def _scan_object_files(object_dir: Path, needle: str) -> list[dict]:
    needle_bytes = needle.encode("utf-8")
    hits: list[dict] = []
    for path in sorted(object_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except OSError:
            continue
        if needle_bytes in content:
            hits.append(
                {
                    "path": str(path.relative_to(object_dir)).replace("\\", "/"),
                    "size_bytes": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "contains_body": True,
                }
            )
    return hits


def _storage_block(dbname: str, object_dir: Path, data, baseline_database_bytes: int) -> dict:
    tables = (
        "platform.memories",
        "platform.memory_candidates",
        "platform.artifacts",
        "platform.deletion_jobs",
        "platform.policy_epochs",
        "t066.memory_vectors",
    )
    table_sizes = {}
    for table in tables:
        table_sizes[table] = int(
            _scalar(dbname, f"SELECT coalesce(pg_total_relation_size(to_regclass({_quote(table)})), 0)")
        )
    object_files = [
        path for path in object_dir.rglob("*") if path.is_file()
    ]
    object_bytes = sum(path.stat().st_size for path in object_files)
    return {
        "database_bytes": int(_scalar(dbname, "SELECT pg_database_size(current_database())")),
        "database_bytes_after_migrate": baseline_database_bytes,
        "database_bytes_delta": int(
            _scalar(dbname, "SELECT pg_database_size(current_database())")
        )
        - baseline_database_bytes,
        "table_bytes": table_sizes,
        "row_counts": {
            "memories_total": int(_scalar(dbname, "SELECT count(*) FROM platform.memories")),
            "memories_active": int(
                _scalar(dbname, "SELECT count(*) FROM platform.memories WHERE deleted_at IS NULL")
            ),
            "memory_candidates": int(_scalar(dbname, "SELECT count(*) FROM platform.memory_candidates")),
            "artifacts_memory_body": int(
                _scalar(dbname, "SELECT count(*) FROM platform.artifacts WHERE purpose = 'memory_body'")
            ),
            "deletion_jobs": int(_scalar(dbname, "SELECT count(*) FROM platform.deletion_jobs")),
            "memory_vectors": int(_scalar(dbname, "SELECT count(*) FROM t066.memory_vectors")),
        },
        "memory_body_bytes": int(
            _scalar(
                dbname,
                "SELECT coalesce(sum(size_bytes), 0) FROM platform.artifacts WHERE purpose = 'memory_body'",
            )
        ),
        "object_files": len(object_files),
        "object_bytes": object_bytes,
        "fixture_bodies": len(data.memories),
        "note": "t066.memory_vectors is the experiment-owned recall index stand-in, not a platform table",
    }


def _recall_from_ids(
    data, results_by_query: dict[str, list[str]], id_to_key: dict[str, str], top_k: int
) -> dict:
    per_query = []
    positives = 0
    hits1 = 0
    hits3 = 0
    forbidden_hits = 0
    for query in data.queries:
        ids = results_by_query.get(query.key, [])
        keys = [id_to_key.get(identifier, "unknown:" + identifier) for identifier in ids]
        entry = {"query": query.key, "kind": query.kind, "ids": keys[:top_k]}
        if query.kind == "positive":
            positives += 1
            at1 = bool(keys) and keys[0] == query.expected_memory
            atk = query.expected_memory in keys[:top_k]
            hits1 += int(at1)
            hits3 += int(atk)
            entry["hit_at_1"] = at1
            entry["hit_at_k"] = atk
        else:
            forbidden = set(query.forbidden_memories) & set(keys[:top_k])
            forbidden_hits += len(forbidden)
            entry["forbidden_hits"] = sorted(forbidden)
        per_query.append(entry)
    return {
        "hit_at_1": round(hits1 / positives, 6) if positives else 0.0,
        "hit_at_3": round(hits3 / positives, 6) if positives else 0.0,
        "positive_queries": positives,
        "negative_queries": sum(1 for query in data.queries if query.kind == "negative"),
        "forbidden_hits": forbidden_hits,
        "top_k": top_k,
        "per_query": per_query,
    }


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------
def run(
    *,
    data,
    dsn: str,
    dbname: str,
    admin: Path,
    driver: Path,
    object_dir: Path,
    iterations: int = 21,
    top_k: int = 3,
) -> dict:
    if iterations < MIN_ITERATIONS:
        raise ValueError(f"iterations must be at least {MIN_ITERATIONS}")
    object_dir.mkdir(parents=True, exist_ok=True)
    admin_record = migrate(admin, dsn)
    baseline_database_bytes = int(_scalar(dbname, "SELECT pg_database_size(current_database())"))

    user_ids, app_ids = _seed_identity(dbname, data)
    candidate_record = _seed_pending_candidate(dbname, data, user_ids, app_ids, object_dir)

    # Real writes through internal/memory: confirmed memories, one revision, one delete.
    create_payloads = [
        {
            "owner_id": user_ids[memory.owner_key],
            "application_id": app_ids[memory.app_key],
            "text": memory.text,
        }
        for memory in data.memories
    ]
    create_results = _run_driver(driver, "create", create_payloads, dsn, str(object_dir))
    if len(create_results) != len(data.memories):
        raise RuntimeError("pgdriver create returned a different number of rows")
    id_map = {
        memory.key: str(create_results[index]["id"]) for index, memory in enumerate(data.memories)
    }
    id_to_key = {identifier: key for key, identifier in id_map.items()}

    revisions = {}
    update_results = _run_driver(
        driver,
        "update",
        [
            {
                "owner_id": user_ids[memory.owner_key],
                "memory_id": id_map[memory.key],
                "text": new_text,
                "if_match": '"1"',
            }
            for memory in data.memories
            if (new_text := data.revisions.get(memory.key)) is not None
        ],
        dsn,
        str(object_dir),
    )
    for result in update_results:
        revisions[id_to_key[str(result["id"])]] = int(result["revision"])
    delete_results = _run_driver(
        driver,
        "delete",
        [
            {
                "owner_id": user_ids[memory.owner_key],
                "memory_id": id_map[memory.key],
            }
            for memory in data.memories
            if memory.key == data.deleted_memory
        ],
        dsn,
        str(object_dir),
    )
    deleted = data.memory(data.deleted_memory)
    deleted_id = id_map[deleted.key]
    deleted_text = data.text_of(deleted)

    # Read the active rows back through SQL: the body bytes come from disk, verified
    # against the recorded sha256, and the embedding is computed from those bytes.
    active_rows = _rows(
        dbname,
        "SELECT m.id::text AS id, m.owner_id::text AS owner_id, m.application_id::text AS application_id, "
        "m.revision, a.object_key, a.sha256, a.size_bytes "
        "FROM platform.memories AS m JOIN platform.artifacts AS a ON a.id = m.body_artifact_id "
        "WHERE m.deleted_at IS NULL ORDER BY m.id",
    )
    _psql(
        dbname,
        "CREATE SCHEMA IF NOT EXISTS t066;"
        "CREATE TABLE IF NOT EXISTS t066.memory_vectors ("
        "memory_id uuid PRIMARY KEY REFERENCES platform.memories (id) ON DELETE CASCADE,"
        "revision bigint NOT NULL, embedding double precision[] NOT NULL);",
    )
    vector_rows = []
    for row in active_rows:
        key = id_to_key.get(str(row["id"]))
        if key is None:
            continue
        path = object_dir / row["object_key"]
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != str(row["sha256"]).strip():
            raise RuntimeError(f"sealed body digest mismatch for {key}")
        vector_rows.append(
            (
                str(row["id"]),
                int(row["revision"]),
                embedder.embed(body.decode("utf-8")),
            )
        )
    if vector_rows:
        _psql(
            dbname,
            "BEGIN; DELETE FROM t066.memory_vectors; "
            + " ".join(
                "INSERT INTO t066.memory_vectors (memory_id, revision, embedding) VALUES "
                f"({_quote(memory_id)}::uuid, {revision}, {_array_literal(vector)});"
                for memory_id, revision, vector in vector_rows
            )
            + " COMMIT;",
        )

    # One real recall round trip per query per iteration, timed by the Go driver.
    results_by_query: dict[str, list[str]] = {}
    durations_ms: list[float] = []
    embed_ms: list[float] = []
    runs_recorded = 0
    for scope_owner, scope_app in sorted({(q.owner_key, q.app_key) for q in data.queries}):
        scope_queries = [
            query for query in data.queries if query.owner_key == scope_owner and query.app_key == scope_app
        ]
        vectors = [embedder.embed(query.text) for query in scope_queries]
        measured = _run_bench(
            driver,
            {
                "owner_id": user_ids[scope_owner],
                "application_id": app_ids[scope_app],
                "query_vectors": vectors,
                "iterations": iterations,
                "top_k": top_k,
            },
            dsn,
            str(object_dir),
        )
        by_index = {run["query_index"]: run for run in measured}
        for index, query in enumerate(scope_queries):
            measured_run = by_index[index]
            results_by_query[query.key] = [str(identifier) for identifier in measured_run["ids"]]
            query_durations = [ns / 1_000_000.0 for ns in measured_run["durations_ns"]]
            local_embed = []
            started = time.perf_counter()
            for _ in range(len(query_durations)):
                embedder.embed(query.text)
            per_embed = (time.perf_counter() - started) * 1000.0 / len(query_durations)
            local_embed = [per_embed] * len(query_durations)
            durations_ms.extend(
                total + embedded for total, embedded in zip(query_durations, local_embed)
            )
            embed_ms.extend(local_embed)
            runs_recorded += len(query_durations)

    recall = _recall_from_ids(data, results_by_query, id_to_key, top_k)
    recall["deleted_memory_recalled"] = any(
        deleted_id in [str(item) for item in results]
        for results in results_by_query.values()
    )
    recall["candidate_never_recalled"] = candidate_record["id"] not in {
        str(item) for results in results_by_query.values() for item in results
    }

    # Platform-as-is recall (T063 list semantics) for context: all current memories.
    platform_scopes = []
    for application in data.applications:
        scope_rows = _rows(
            dbname,
            "SELECT m.id::text AS id, a.size_bytes, a.object_key, a.sha256 "
            "FROM platform.memories m JOIN platform.artifacts a ON a.id = m.body_artifact_id "
            "WHERE m.owner_id = "
            f"{_quote(user_ids[application.owner_key])}::uuid AND m.application_id = "
            f"{_quote(app_ids[application.key])}::uuid AND m.deleted_at IS NULL "
            "ORDER BY m.confirmed_at DESC, m.id DESC",
        )
        texts = []
        for row in scope_rows:
            path = object_dir / row["object_key"]
            body = path.read_bytes()
            if hashlib.sha256(body).hexdigest() != str(row["sha256"]).strip():
                raise RuntimeError("platform recall body digest mismatch")
            texts.append(body.decode("utf-8"))
        platform_scopes.append(
            {
                "owner_key": application.owner_key,
                "app_key": application.key,
                "active_memories": len(scope_rows),
                "body_bytes": sum(len(text.encode("utf-8")) for text in texts),
                "approx_context_tokens": sum(max(1, len(text) // 4) for text in texts),
            }
        )
    platform_recall = {
        "kind": "list_all_current_memories_newest_first",
        "owner": "T063 runtime recall semantics (not present in this branch's baseline)",
        "note": (
            "the platform injects all current memories within the context budget and "
            "applies no query ranking, so for this small fixture every expected memory is "
            "in the injected set by construction; the measured query-level numbers below "
            "are the experiment's cheapest credible semantic PG baseline"
        ),
        "scopes": platform_scopes,
    }

    storage = _storage_block(dbname, object_dir, data, baseline_database_bytes)
    cleanup = _cleanup_block(
        dbname, object_dir, data, user_ids, app_ids, deleted, deleted_text, deleted_id
    )
    cleanup["candidate"] = candidate_record

    versions = {
        "postgres": _scalar(dbname, "SHOW server_version").strip(),
        "admin_binary_sha256": admin_record["sha256"],
        "pgdriver_binary_sha256": _file_sha256(driver),
        "python": sys.version.split()[0],
    }
    commands = [
        admin_record,
        {
            "cmd": "pgdriver create (real internal/memory.Service.Create)",
            "rows": len(create_results),
            "exit": 0,
        },
        {
            "cmd": "pgdriver update (real internal/memory.Service.Update, If-Match)",
            "rows": len(update_results),
            "exit": 0,
        },
        {
            "cmd": "pgdriver delete (real internal/memory.Service.Delete)",
            "rows": len(delete_results),
            "exit": 0,
        },
        {
            "cmd": "pgdriver bench (real SQL recall, persistent connection)",
            "runs": runs_recorded,
            "exit": 0,
        },
    ]
    return {
        "status": "run",
        "evidence_level": "real",
        "recall": recall,
        "latency": _latency_block(durations_ms, embed_ms, iterations),
        "storage": storage,
        "cleanup": cleanup,
        "platform_recall": platform_recall,
        "id_map": id_map,
        "revision_map": revisions,
        "deleted": {"key": deleted.key, "id": deleted_id, "text_sha256": hashlib.sha256(
            deleted_text.encode("utf-8")).hexdigest(), "delete_response": delete_results[0]},
        "versions": versions,
        "commands": commands,
        "limitations": [
            "query-level PG recall is implemented by the experiment (T063 runtime recall is not "
            "in this branch's baseline); both sides share the same deterministic local embedder",
            "t066.memory_vectors is experiment-owned and is not a platform migration",
            "the pending candidate body was seeded by the fixture builder, not by the T054 worker path",
        ],
    }


def _cleanup_block(
    dbname: str,
    object_dir: Path,
    data,
    user_ids: dict[str, str],
    app_ids: dict[str, str],
    deleted,
    deleted_text: str,
    deleted_id: str,
) -> dict:
    """Enumerate every place the deleted body is still held after the real delete."""
    logical = _scan_columns_for(dbname, deleted_text)
    object_files = _scan_object_files(object_dir, deleted_text)
    tombstone = _rows(
        dbname,
        "SELECT m.id::text AS id, m.revision, m.deleted_at::text AS deleted_at, "
        "m.confirmed_at::text AS confirmed_at FROM platform.memories m "
        f"WHERE m.id = {_quote(deleted_id)}::uuid",
    )
    job = _rows(
        dbname,
        "SELECT id::text AS id, state, deletion_revision, remaining_scopes::text AS remaining_scopes "
        "FROM platform.deletion_jobs WHERE resource_type = 'memory' "
        f"AND resource_id = {_quote(deleted_id)}::uuid "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
    )
    epoch_scope = (
        "memory:" + user_ids[deleted.owner_key].lower() + ":" + app_ids[deleted.app_key].lower()
    )
    epoch = _rows(
        dbname,
        "SELECT scope, revision FROM platform.policy_epochs "
        f"WHERE scope = {_quote(epoch_scope)}",
    )
    artifact = _rows(
        dbname,
        "SELECT a.id::text AS id, a.object_key, a.sha256, a.size_bytes, a.state "
        "FROM platform.artifacts a JOIN platform.memories m ON m.body_artifact_id = a.id "
        f"WHERE m.id = {_quote(deleted_id)}::uuid",
    )
    digest_references = _rows(
        dbname,
        "SELECT 'platform.artifacts' AS relation, count(*) AS row_count FROM platform.artifacts "
        f"WHERE sha256 = {_quote(hashlib.sha256(deleted_text.encode()).hexdigest())}",
    )
    residual: list[str] = []
    residual.extend(f"table:{hit['location']}" for hit in logical["hits"])
    residual.extend(f"object:{hit['path']}" for hit in object_files)
    log_block = {
        "status": "not_applicable",
        "reason": (
            "the experiment starts no product process, so no service log is produced; the "
            "durable log-like store (platform.audit_events) is covered by the table scan"
        ),
        "hits": [],
    }

    revision_leftovers = {}
    old_text = None
    for memory in data.memories:
        if memory.key in data.revisions:
            old_text = memory.text
    if old_text:
        revision_leftovers = {
            "body_sha256": hashlib.sha256(old_text.encode()).hexdigest(),
            "logical": _scan_columns_for(dbname, old_text),
            "object_files": _scan_object_files(object_dir, old_text),
            "note": "the superseded revision artifact is retained by design; F07 owns the purge",
        }

    return {
        "deleted_memory": deleted.key,
        "body_sha256": hashlib.sha256(deleted_text.encode("utf-8")).hexdigest(),
        "logical": logical,
        "object_files": object_files,
        "logs": log_block,
        "digest_references": digest_references,
        "tombstone": tombstone[0] if tombstone else {"missing": True},
        "deletion_job": {
            "id": job[0]["id"] if job else None,
            "state": job[0]["state"] if job else None,
            "deletion_revision": job[0]["deletion_revision"] if job else None,
            "remaining_scopes": _remaining_scope_kinds(job[0]["remaining_scopes"]) if job else [],
        },
        "cache_epoch": {
            "scope": epoch_scope,
            "revision": epoch[0]["revision"] if epoch else None,
        },
        "current_artifact": artifact[0] if artifact else None,
        "revision_leftovers": revision_leftovers,
        "residual_body_copies": residual,
        "stores_clean": len(residual) == 0,
        "cleanup_owner": "F07 deletion governance performs the physical purge; T058 already "
        "stopped recall synchronously",
    }


def _remaining_scope_kinds(encoded: str | None) -> list[str]:
    if not encoded:
        return []
    try:
        scopes = json.loads(encoded)
    except json.JSONDecodeError:
        return []
    return sorted({str(scope.get("kind")) for scope in scopes if isinstance(scope, dict)})
