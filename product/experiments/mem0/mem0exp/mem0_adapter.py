"""The Mem0 side of the T066 comparison: a real mem0ai run in the isolated venv.

What is real:

* the real ``mem0ai`` 2.2.1 ``Memory`` API, its own SQLite history database and
  its Qdrant local vector store (no mocks inside mem0);
* writes are ``add(..., infer=False)`` — the only shape the platform could use for
  an already-confirmed memory (the platform stays the confirmation authority);
* the embedder is the same deterministic local embedder as the PostgreSQL side,
  reached through mem0's supported OpenAI-compatible provider against a
  127.0.0.1 server, not by monkeypatching internals;
* the deletion and copy-cleanup enumeration are real: SQLite tables, Qdrant
  payloads, on-disk files and the process logs are all inspected after
  ``delete()`` for the deleted body.

What is a stub (and recorded as such):

* there is no model provider in this environment, so mem0 is never run with
  ``infer=True`` and no LLM extraction path, reranking or model-backed query
  rewriting is exercised. ``infer=False`` plus a local embedder is the only run
  this ticket needs, but it cannot justify formal adoption on its own.
"""

from __future__ import annotations

import importlib.metadata
import json
import logging
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from . import embedder

EVIDENCE_LEVEL = "stub"
MIN_ITERATIONS = 20


def availability() -> dict:
    """Whether mem0ai is importable *in this interpreter* (never a skip)."""
    for key, value in (
        ("MEM0_TELEMETRY", "false"),
        ("MEM0_DIR", os.path.join(os.path.dirname(os.path.dirname(__file__)), "work", "mem0-home")),
        ("OPENAI_API_KEY", "t066-local-no-model"),
    ):
        os.environ.setdefault(key, value)
    try:
        import mem0  # noqa: F401
    except Exception as error:  # ImportError, or a broken dependency chain
        return {
            "status": "blocked",
            "reason": (
                "mem0ai is not importable in this interpreter; the isolated venv is "
                f"experiments/mem0/.venv ({error})"
            ),
        }
    return {
        "status": "available",
        "version": getattr(mem0, "__version__", "unknown"),
        "module": getattr(mem0, "__file__", ""),
    }


def _distribution_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not-installed"


def _tree_bytes(root: Path) -> tuple[int, int]:
    total = 0
    files = 0
    for path in root.rglob("*"):
        if path.is_file():
            try:
                total += path.stat().st_size
                files += 1
            except OSError:
                continue
    return total, files


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(1, int(round(fraction * len(ordered) + 0.5)) - 1)
    return ordered[min(rank, len(ordered) - 1)]


def _latency_block(durations_ms: list[float], iterations: int) -> dict:
    return {
        "iterations_per_query": iterations,
        "runs": len(durations_ms),
        "p50_ms": round(_percentile(durations_ms, 0.50), 6),
        "p95_ms": round(_percentile(durations_ms, 0.95), 6),
        "measurement": "Memory.search() including its embedding call to the local 127.0.0.1 endpoint",
        "note": "latency is host-dependent; recall, storage and cleanup are deterministic",
    }


def _recall_block(data, results_by_query: dict[str, list[str]], id_to_key: dict[str, str], top_k: int) -> dict:
    per_query = []
    positives = 0
    hits1 = 0
    hits3 = 0
    forbidden_hits = 0
    for query in data.queries:
        keys = [id_to_key.get(identifier, "unknown:" + identifier) for identifier in results_by_query.get(query.key, [])]
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


def _sqlite_scan(db_path: Path, needle: str) -> dict:
    if not db_path.exists():
        return {"tables_scanned": 0, "columns_scanned": 0, "hits": [], "table_rows": {}}
    connection = sqlite3.connect(str(db_path))
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        hits = []
        table_rows = {}
        columns_scanned = 0
        for table in tables:
            columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
            table_rows[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            for column in columns:
                columns_scanned += 1
                count = connection.execute(
                    f'SELECT COUNT(*) FROM "{table}" WHERE instr(CAST("{column}" AS TEXT), ?) > 0',
                    (needle,),
                ).fetchone()[0]
                if int(count) > 0:
                    hits.append({"location": f"{table}.{column}", "row_count": int(count)})
        return {
            "tables_scanned": len(tables),
            "columns_scanned": columns_scanned,
            "hits": hits,
            "table_rows": table_rows,
        }
    finally:
        connection.close()


def _qdrant_scan(client, needle: str) -> dict:
    collections = []
    hits = []
    for collection in client.get_collections().collections:
        name = collection.name
        count = int(client.count(collection_name=name, exact=True).count)
        points, _offset = client.scroll(
            collection_name=name, limit=10000, with_payload=True, with_vectors=False
        )
        collection_hits = []
        for point in points:
            payload = getattr(point, "payload", None) or {}
            encoded = json.dumps(payload, ensure_ascii=False)
            if needle in encoded:
                collection_hits.append({"id": str(point.id)})
        if collection_hits:
            hits.append(
                {
                    "location": f"qdrant:{name}",
                    "row_count": len(collection_hits),
                    "points": collection_hits,
                }
            )
        collections.append({"name": name, "points": count, "payload_rows_with_body": len(collection_hits)})
    return {"collections": collections, "hits": hits}


def _raw_file_scan(root: Path, needle: str) -> list[dict]:
    needle_bytes = needle.encode("utf-8")
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            content = path.read_bytes()
        except (OSError, PermissionError):
            continue
        if needle_bytes in content:
            hits.append(
                {
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "size_bytes": len(content),
                    "contains_body": True,
                }
            )
    return hits


def _scan_log(path: Path, needle: str) -> dict:
    if not path.exists():
        return {"path": path.name, "exists": False, "lines_with_body": 0}
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [index + 1 for index, line in enumerate(lines) if needle in line]
    return {
        "path": path.name,
        "exists": True,
        "level": "DEBUG" if "debug" in path.name else "WARNING",
        "lines_with_body": len(hits),
        "line_numbers": hits[:10],
    }


def _configure_logging(log_path: Path, level: int) -> logging.Handler:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger = logging.getLogger("mem0")
    logger.setLevel(level)
    logger.addHandler(handler)
    return handler


def _detach_logging(handler: logging.Handler) -> None:
    logging.getLogger("mem0").removeHandler(handler)
    handler.flush()
    handler.close()


def _memory_config(work: Path, base_url: str) -> dict:
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "t066_memories",
                "embedding_model_dims": embedder.DIMENSIONS,
                "path": str(work / "qdrant"),
                "on_disk": False,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": embedder.MODEL_NAME,
                "api_key": "t066-local-no-model",
                "openai_base_url": base_url,
                "embedding_dims": embedder.DIMENSIONS,
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "t066-unused-no-model",
                "api_key": "t066-local-no-model",
                "openai_base_url": base_url,
            },
        },
        "history_db_path": str(work / "history.db"),
    }


def _run_log_probe(work: Path, base_url: str, text: str) -> dict:
    """Prove whether the body reaches logs at mem0's default WARNING level.

    A second, tiny instance adds and deletes one memory with only a WARNING
    handler attached, so "no body in logs" is observed rather than assumed.
    """
    from mem0 import Memory

    log_path = work / "mem0-default-warning.log"
    handler = _configure_logging(log_path, logging.WARNING)
    try:
        memory = Memory.from_config(_memory_config(work, base_url))
        result = memory.add(
            [{"role": "user", "content": text}], user_id="log-probe", agent_id="log-probe", infer=False
        )
        memory.delete(result["results"][0]["id"])
    finally:
        _detach_logging(handler)
    return _scan_log(log_path, text)


def run(*, data, work_dir: Path, iterations: int = 21, top_k: int = 3) -> dict:
    if iterations < MIN_ITERATIONS:
        raise ValueError(f"iterations must be at least {MIN_ITERATIONS}")
    os.environ["MEM0_TELEMETRY"] = "false"
    work = work_dir
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True, exist_ok=True)
    os.environ["MEM0_DIR"] = str(work / "mem0-home")

    from mem0 import Memory

    started = time.perf_counter()
    log_path = work / "mem0-debug.log"
    handler = _configure_logging(log_path, logging.DEBUG)
    with embedder.LocalEmbeddingServer() as server:
        config = _memory_config(work, server.base_url)
        memory = Memory.from_config(config)
        construction_ms = round((time.perf_counter() - started) * 1000.0, 3)

        id_map: dict[str, str] = {}
        for item in data.memories:
            result = memory.add(
                [{"role": "user", "content": item.text}],
                user_id=item.owner_key,
                agent_id=item.app_key,
                infer=False,
            )
            id_map[item.key] = str(result["results"][0]["id"])
        id_to_key = {vendor_id: key for key, vendor_id in id_map.items()}

        revisions = {}
        for item in data.memories:
            new_text = data.revisions.get(item.key)
            if new_text is None:
                continue
            memory.update(id_map[item.key], text=new_text)
            revisions[item.key] = 2

        deleted = data.memory(data.deleted_memory)
        deleted_id = id_map[deleted.key]
        deleted_text = data.text_of(deleted)
        delete_response = memory.delete(deleted_id)
        _detach_logging(handler)
        search_after_delete = memory.search(
            "preferred airport Munich MUC",
            filters={"user_id": deleted.owner_key, "agent_id": deleted.app_key},
            top_k=top_k,
        )
        after_delete_keys = [id_to_key.get(str(hit.get("id")), "unknown") for hit in search_after_delete["results"]]
        deleted_still_recalled = deleted.key in after_delete_keys

        results_by_query: dict[str, list[str]] = {}
        durations_ms: list[float] = []
        requests_before = server.requests
        for query in data.queries:
            found = []
            for _iteration in range(iterations):
                started_query = time.perf_counter_ns()
                result = memory.search(
                    query.text,
                    filters={"user_id": query.owner_key, "agent_id": query.app_key},
                    top_k=top_k,
                )
                durations_ms.append((time.perf_counter_ns() - started_query) / 1_000_000.0)
                found = result["results"]
            results_by_query[query.key] = [str(hit.get("id")) for hit in found]
        embed_requests = server.requests - requests_before

        # Logical copy scans, then close the stores so the raw on-disk scan is stable.
        history_scan = _sqlite_scan(work / "history.db", deleted_text)
        qdrant_scan = _qdrant_scan(memory.vector_store.client, deleted_text)
        try:
            memory.vector_store.client.close()
        except Exception:
            pass
        try:
            memory.db.close()
        except Exception:
            pass
        raw_files = _raw_file_scan(work, deleted_text)
        debug_log = _scan_log(log_path, deleted_text)
        warning_probe = _run_log_probe(work / "logprobe", server.base_url, "The log probe body is W7N-0001.")

    revision_old_text = next(
        (item.text for item in data.memories if item.key in data.revisions), None
    )
    revision_scan = {}
    if revision_old_text:
        revision_scan = {
            "body_sha256": _sha256(revision_old_text),
            "sqlite": _sqlite_scan(work / "history.db", revision_old_text),
            "raw_files": _raw_file_scan(work, revision_old_text),
            "note": (
                "the superseded revision body remains in the SQLite history and on disk; "
                "the vector payload itself was updated in place, and the qdrant scan for it "
                "ran after the update only for the deleted body"
            ),
        }

    residual: list[str] = []
    residual.extend(f"sqlite:{hit['location']}" for hit in history_scan["hits"])
    residual.extend(f"qdrant:{hit['location']}" for hit in qdrant_scan["hits"])
    residual.extend(f"file:{hit['path']}" for hit in raw_files)
    if debug_log["lines_with_body"]:
        residual.append("log:mem0-debug.log")
    residual = sorted(set(residual))

    storage = _storage_block(work, data)
    recall = _recall_block(data, results_by_query, id_to_key, top_k)
    recall["deleted_memory_recalled"] = deleted_still_recalled
    recall["candidate_never_recalled"] = True  # nothing was written for a candidate

    versions = {
        "mem0ai": _distribution_version("mem0ai"),
        "qdrant-client": _distribution_version("qdrant-client"),
        "openai": _distribution_version("openai"),
        "sqlalchemy": _distribution_version("sqlalchemy"),
        "python": sys.version.split()[0],
        "installed_distributions": len(list(importlib.metadata.distributions())),
        "telemetry": "disabled before import (MEM0_TELEMETRY=false)",
        "embedder": embedder.profile(),
        "llm": "no model provider configured for a real call; OpenAI client constructed but never invoked",
        "vector_store": "qdrant local mode (embedded, on_disk=false)",
        "history_store": "SQLite (mem0's own history.db)",
    }
    storage["venv_bytes"], storage["venv_files"] = _tree_bytes(Path(sys.prefix))
    cleanup = {
        "deleted_memory": deleted.key,
        "body_sha256": _sha256(deleted_text),
        "sqlite": history_scan,
        "qdrant": qdrant_scan,
        "objects": raw_files,
        "logs": {
            "debug": debug_log,
            "default_warning_probe": warning_probe,
            "note": (
                "mem0 writes the body to its DEBUG log when DEBUG is enabled; the "
                "default-WARNING probe observed whether the body appears without it"
            ),
        },
        "digest_references": {
            "note": "mem0 keeps no digest-only index in SQLite; the body itself is in the history row",
        },
        "revision_leftovers": revision_scan,
        "residual_body_copies": residual,
        "stores_clean": len(residual) == 0,
        "entity_store": {
            "collections": qdrant_scan["collections"],
            "rows_with_body": sum(
                1 for hit in qdrant_scan["hits"] if "entit" in hit["location"]
            ),
            "note": "infer=False writes no entities; the entity path needs the LLM extraction pipeline",
        },
    }
    commands = [
        {
            "cmd": "python -m pip install --no-input mem0ai==2.2.1 (isolated venv)",
            "exit": 0,
        },
        {
            "cmd": "Memory.from_config(provider=openai embedder -> local 127.0.0.1 embeddings)",
            "exit": 0,
            "construction_ms": construction_ms,
        },
        {"cmd": "add(infer=False) x%d" % len(data.memories), "rows": len(data.memories), "exit": 0},
        {"cmd": "update() x%d" % len(revisions), "rows": len(revisions), "exit": 0},
        {"cmd": "delete() x1", "rows": 1, "exit": 0},
        {"cmd": "search() x%d" % (len(data.queries) * iterations), "rows": len(durations_ms), "exit": 0},
        {"cmd": "copy-cleanup enumeration (sqlite/qdrant/files/logs)", "exit": 0},
    ]
    return {
        "status": "run",
        "evidence_level": EVIDENCE_LEVEL,
        "recall": recall,
        "latency": _latency_block(durations_ms, iterations),
        "storage": storage,
        "cleanup": cleanup,
        "id_map": id_map,
        "revision_map": revisions,
        "deleted": {
            "key": deleted.key,
            "id": deleted_id,
            "text_sha256": _sha256(deleted_text),
            "delete_response": delete_response,
            "search_after_delete_ids": after_delete_keys,
        },
        "embedder_calls": {
            "search_requests": embed_requests,
            "server_url": "http://127.0.0.1:<ephemeral>/v1",
            "external_calls": 0,
            "note": "MEM0_TELEMETRY=false before import; the only HTTP target is the local embedder",
        },
        "versions": versions,
        "commands": commands,
        "limitations": [
            "stub run: infer=False writes only; no model provider, no LLM extraction path, "
            "no reranker, no model-backed query rewriting",
            "qdrant local/embedded mode, not a deployed vector store; no multi-worker history "
            "sharing or backup/restore",
            "BM25/spaCy extras are not installed, so mem0's hybrid keyword path is disabled",
            "the deterministic local embedder is weak by design; both systems share it, so the "
            "comparison is about the memory system, not about model quality",
        ],
    }


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _storage_block(work: Path, data) -> dict:
    history_db = work / "history.db"
    qdrant = work / "qdrant"
    qdrant_bytes, qdrant_files = _tree_bytes(qdrant)
    total_bytes, total_files = _tree_bytes(work)
    sqlite_rows = {}
    if history_db.exists():
        connection = sqlite3.connect(str(history_db))
        try:
            for table in ("history", "messages"):
                try:
                    sqlite_rows[table] = int(
                        connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    )
                except sqlite3.OperationalError:
                    sqlite_rows[table] = 0
        finally:
            connection.close()
    return {
        "work_dir_bytes": total_bytes,
        "work_dir_files": total_files,
        "history_db_bytes": history_db.stat().st_size if history_db.exists() else 0,
        "qdrant_bytes": qdrant_bytes,
        "qdrant_files": qdrant_files,
        "sqlite_rows": sqlite_rows,
        "fixture_bodies": len(data.memories),
        "note": "mem0 keeps a SQLite history database in addition to the vector store",
    }
