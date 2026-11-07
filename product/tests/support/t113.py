"""F13B/T113 real-stack helpers: the cache comparison and zero-hit matrices.

The ticket re-runs the T067 retrieval-cache claim at F13B level. Everything in
this module exists to make that evidence auditable rather than to substitute for
a product path:

* the fixed corpus/query inputs are pre-registered as module constants, so the
  measurement cannot silently choose a friendlier workload;
* the comparison summary computes P50/P95/P99 from the raw per-request samples and
  states the cost basis honestly (local hash embedding, no model provider);
* cache rows, literal row facts and sealed object bytes are read from PostgreSQL
  and the filesystem as independent truth;
* the deployment-switch and backend-outage actions are real: a second compiled
  ``cmd/api`` process for ``ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=off``, and a real
  ``ALTER TABLE ... RENAME`` for a cache backend that is unavailable;
* the evidence recorder writes ``reports/f13b/T113-cache-comparison.json`` only
  when ``F13B_WRITE_EVIDENCE=1`` and only a complete, all-passed run may replace
  the committed record (the T101/T106/T107/T111 convention).

No product code, migration or contract is mocked: the retrieval/answer endpoints,
the deletion transaction and the admin principal update are all driven over real
HTTP against the compiled binaries.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import platform
import shutil
import subprocess
from pathlib import Path

from support import t067
from support import stack as st

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = PRODUCT_ROOT / "reports" / "f13b" / "T113-cache-comparison.json"

CACHE_ENV = t067.CACHE_ENV
CACHE_TABLE = t067.CACHE_TABLE
# Mirrors knowledge.RetrievalCacheMaxEntryBytes / RetrievalCacheKeyVersion.
CACHE_MAX_ENTRY_BYTES = 256 * 1024
CACHE_KEY_VERSION = "knowledge-retrieval-cache/1"
EMBEDDING_REVISION = t067.EMBEDDING_REVISION
TTL_SECONDS = 900

# The documented answer-stub fault sentinel (knowledge.AnswerFaultWrongAnswerSentinel).
WRONG_ANSWER_SENTINEL = "T067-WRONG-ANSWER-NOT-IN-ANY-CHUNK"
# The stored cache item shape, pinned so an answer/document field cannot sneak in.
STORED_ITEM_FIELDS = {
    "chunk_id",
    "document_id",
    "document_revision",
    "chunk_no",
    "span",
    "citation",
    "body_artifact_id",
    "body_sha256",
    "text",
    "score",
}

# ---------------------------------------------------------------------------
# pre-registered experiment inputs
# ---------------------------------------------------------------------------
DOCUMENTS = 8
TOP_K_VALUES = (3, 8, 20)
QUERIES = (
    "华北地区十月的销售额是多少",
    "退款政策需要几天内申请",
    "华东地区订单数量",
    "退款需在订单完成后多少天内提交",
    "销售额以什么为准",
    "订单数量以什么记录为准",
    "客服审核退款",
    "第十期的销售额",
)
# Fixed order: 8 queries x 3 top_k values = 24 measured specs.
QUERY_SPECS = tuple((query, top_k) for query in QUERIES for top_k in TOP_K_VALUES)
WARMUP_QUERY = "T113 预热查询（不参与测量）"

DELETED_MARKER = "T113-DELETED-BODY-MUST-NEVER-BE-SERVED"
ALICE_MARKER = "T113-ALICE-PRIVATE-BODY-1234.50"
BOB_MARKER = "T113-BOB-PRIVATE-BODY-987.25"

DOC_B = (
    "# 退款制度\n\n"
    "退款需在订单完成后 30 天内提交申请，逾期不再受理。"
    + DELETED_MARKER
    + "\n"
) * 12

FILLER = (
    "补充条款：每期销售额以财务系统结算单为准，订单数量以出库记录为准，"
    "退款申请需经过客服审核并在原支付渠道退回。"
)

OVERSIZED_CHUNKS = 50
OVERSIZED_TOP_K = 50
_EMOJI_PARAGRAPH = "😀" * 1200  # 1200 runes = 4800 UTF-8 bytes, the chunk bound

ENVIRONMENT = {
    "profile": "dev-stub (ORCHARA_PROFILE=dev-stub; no vendor credentials or endpoint)",
    "database": "isolated PostgreSQL 16.4 at 127.0.0.1:55432; one throwaway database per test, "
    "migrated by the compiled cmd/admin chain",
    "api": "compiled cmd/api behind real HTTP with the real filesystem object store, HMAC-signed "
    "links and the internal mTLS listener",
    "maintenance": "compiled cmd/maintenance (document_ingest + artifact_finalize pools) as a real "
    "OS process",
    "embedding": "local-hash-v1 in-process hash embedding (no vendor provider)",
    "generation": "extractive-stub/1 labelled stub (no model provider)",
    "cache": "PostgreSQL platform.knowledge_retrieval_cache (ADR 0015; no Redis adapter in this build)",
    "resource_profile": "one Windows host, PostgreSQL 16.4 on localhost, fixed 8-document corpus and "
    "24-spec query sequence; cache-on and cache-off real API processes share the database/object "
    "directory and are sampled paired inside one measurement window",
    "toolchain": "Python 3.12.2, Go 1.27.3, Node v24.13.0 (page script execution only)",
}

BLOCKED = (
    {
        "item": "real vendor embedding provider",
        "reason": "no credentials/endpoint/adapter on this host (the same T055 boundary)",
        "unblock": "provide a vendor embedding adapter and credentials, then re-run the comparison",
        "effect": "the absolute latency/cost numbers are local-hash numbers, not vendor numbers",
    },
    {
        "item": "real model generation: answer quality/latency/cost",
        "reason": "no model provider exists; the answer path is the labelled extractive stub",
        "unblock": "provide a model provider; the answer cache question is answered structurally "
        "(the answer is regenerated every request and never stored)",
        "effect": "no real-model P50/P95/cost is claimed",
    },
)
NOT_RUN = (
    {
        "item": "SSE user path for the knowledge cache",
        "reason": "the knowledge retrieval/answer surface has no SSE endpoint; run-event SSE is a "
        "different surface and does not exercise the retrieval cache",
    },
    {
        "item": "browser DOM driving /knowledge",
        "reason": "no Playwright/Chromium on this host; the committed page script is executed under "
        "Node to prove it sends the same REST retrieval body",
    },
    {
        "item": "capacity-scale retrieval benchmark (multi-process, 100 users)",
        "reason": "T114 owns the exclusive capacity window; this ticket measures one fixed corpus "
        "against two real API processes",
    },
    {
        "item": "Redis cache backend",
        "reason": "this build has no Redis adapter; ADR 0015 stores the cache in PostgreSQL",
    },
)

COMMANDS = (
    "python -m pytest tests/f13b/test_t113_cache_comparison.py -q",
    "F13B_WRITE_EVIDENCE=1 python -m pytest tests/f13b/test_t113_cache_comparison.py -q -s",
    "python -m pytest tests/f10 -q",
    "python -m pytest tests/f13b -q",
    "go build ./... ; go vet ./... ; go test ./...",
    "gofmt -l internal cmd tools",
    "python tools/sync_spec.py --check",
    "git merge integration/t113-t114-t122",
)

VERSIONS = {
    "python": platform.python_version(),
    "go": "1.27.3",
    "node": "not_installed",
    "postgresql": "16.4",
    "embedding_model_revision": EMBEDDING_REVISION,
    "answer_generator": "extractive-stub/1",
    "cache_key_version": CACHE_KEY_VERSION,
    "cache_ttl_seconds": TTL_SECONDS,
    "cache_max_entry_bytes": CACHE_MAX_ENTRY_BYTES,
}


def detect_node_version() -> str | None:
    """Probe the Node runtime at measurement time (never a hard-coded version).

    The page script is only executed when Node exists, so the evidence must
    record the runtime that actually ran (or its absence) rather than a version
    copied from a developer machine (F13B/T113 review fix T113-1).
    """
    node = shutil.which("node")
    if node is None:
        return None
    try:
        completed = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=30
        )
    except OSError:  # pragma: no cover - environment failure
        return None
    version = (completed.stdout or "").strip()
    if completed.returncode != 0 or not version:
        return None
    return version


VERSIONS["node"] = detect_node_version() or "not_installed"


# ---------------------------------------------------------------------------
# deterministic inputs
# ---------------------------------------------------------------------------
def benchmark_document(index: int) -> bytes:
    """A deterministic txt document with several chunks (the T067-shaped fixture)."""
    sections = [
        f"# 销售资料第 {index} 期",
        "",
        f"华北地区十月第 {index} 期的销售额是 {1000 + index}.50 元，订单数为 {40 + index}。",
        "",
        f"华东地区十月第 {index} 期的销售额是 {900 + index}.25 元，订单数为 {30 + index}。",
        "",
        f"退款政策：订单完成后 {30 + index} 天内可申请退款，逾期不再受理。",
        "",
    ]
    for _ in range(12):
        sections.append(FILLER * 4)
        sections.append("")
    return "\n".join(sections).encode("utf-8")


def oversized_document() -> bytes:
    """The maximum public-API entry shape: 50 chunks at the 1200-rune chunk bound."""
    return ("\n\n".join([_EMOJI_PARAGRAPH] * OVERSIZED_CHUNKS) + "\n").encode("utf-8")


def sha256_hex(payload: str | bytes) -> str:
    import hashlib

    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def git_branch_and_commit() -> tuple[str | None, str | None]:
    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return branch or None, commit or None
    except Exception:  # noqa: BLE001 - evidence only
        return None, None


# ---------------------------------------------------------------------------
# measurement primitives
# ---------------------------------------------------------------------------
def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile (the T067 definition), 0.0 for no samples."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def stats(values: list[float]) -> dict:
    if not values:
        return {
            "runs": 0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "p99_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
            "mean_ms": 0.0,
        }
    return {
        "runs": len(values),
        "p50_ms": round(percentile(values, 0.50), 3),
        "p95_ms": round(percentile(values, 0.95), 3),
        "p99_ms": round(percentile(values, 0.99), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
        "mean_ms": round(sum(values) / len(values), 3),
    }


def timed(callable_):
    """Run one HTTP call and return ``(elapsed_ms, response)``."""
    import time

    started = time.perf_counter()
    response = callable_()
    return (time.perf_counter() - started) * 1000.0, response


def answer_digest(payload: dict) -> str:
    """A stable digest of the parts of an answer that must be identical.

    Every comparison count in ``comparison_summary`` is derived from these
    per-request digests, so a summary field can never be a hard-coded constant
    (F13B/T113 review fix T113-1).
    """
    canonical = json.dumps(
        comparable(payload), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_hex(canonical)


def measurement(
    *,
    seq: int,
    phase: str,
    mode: str,
    query: str,
    top_k: int,
    latency_ms: float,
    response,
) -> dict:
    """One per-request evidence row (latency, cache state, chunk count, answer)."""
    payload = response.json
    retrieval = payload.get("retrieval") or {}
    cache = retrieval.get("cache") or {}
    items = payload.get("items") or []
    index = retrieval.get("index") or {}
    return {
        "seq": seq,
        "phase": phase,
        "mode": mode,
        "query": query,
        "top_k": top_k,
        "latency_ms": round(latency_ms, 3),
        "status": response.status,
        "cache_enabled": cache.get("enabled"),
        "cache_hit": cache.get("hit"),
        "cache_stored": cache.get("stored"),
        "cache_discarded": cache.get("discarded"),
        "cache_discard_reason": cache.get("discard_reason"),
        "validated_chunks": cache.get("validated_chunks"),
        "key_prefix": cache.get("key_prefix"),
        "item_count": len(items),
        "chunk_ids_sha256": sha256_hex(",".join(item["chunk_id"] for item in items)),
        "answer_sha256": answer_digest(payload),
        "acl_epoch": retrieval.get("acl_epoch"),
        "embedding_model_revision": retrieval.get("embedding_model_revision"),
        "index_active": index.get("active_indexes"),
        "index_excluded": index.get("excluded_indexes"),
    }


def comparable(payload: dict) -> dict:
    """The parts of a retrieval answer that must be identical across cache modes."""
    retrieval = payload["retrieval"]
    return {
        "items": payload["items"],
        "next_cursor": payload.get("next_cursor"),
        "embedding_model_revision": retrieval["embedding_model_revision"],
        "top_k": retrieval["top_k"],
        "acl_epoch": retrieval["acl_epoch"],
        "index": retrieval["index"],
    }


def _measurement_index(measurements: list[dict]) -> dict[tuple, dict]:
    """Index one run's per-request rows by (phase, mode, query, top_k)."""
    index: dict[tuple, dict] = {}
    duplicates: list[tuple] = []
    for row in measurements:
        key = (row["phase"], row["mode"], row["query"], int(row["top_k"]))
        if key in index:
            duplicates.append(key)
        index[key] = row
    assert not duplicates, f"duplicate measurement rows for {duplicates[:5]}"
    return index


def comparison_summary(
    *,
    specs,
    total_chunks: int,
    measurements: list[dict],
    rows: list[dict],
) -> dict:
    """Aggregate one comparison run from its measured request rows only.

    Every correctness count and hit rate is derived from the recorded rows (the
    per-request answer digest, cache_hit and latency); nothing is a
    ``len(specs)``/``1.0`` constant, so a run whose answers diverge or whose
    cache misses cannot produce a passing summary (F13B/T113 review fix
    T113-1). ``rows`` are the independent cache-row facts read from PostgreSQL.
    """
    index = _measurement_index(measurements)

    def request(phase: str, mode: str, query: str, top_k: int) -> dict | None:
        return index.get((phase, mode, query, int(top_k)))

    def digests(phase: str, mode: str) -> list[str | None]:
        return [
            (request(phase, mode, query, top_k) or {}).get("answer_sha256")
            for query, top_k in specs
        ]

    def identical(left: tuple[str, str], right: tuple[str, str]) -> tuple[int, list[dict]]:
        mismatched: list[dict] = []
        for (query, top_k), left_digest, right_digest in zip(
            specs, digests(*left), digests(*right)
        ):
            if left_digest is None or right_digest is None or left_digest != right_digest:
                mismatched.append({"query": query, "top_k": top_k})
        return len(specs) - len(mismatched), mismatched

    cold_vs_off, cold_vs_off_bad = identical(
        ("cold", "cache_on"), ("cold", "cache_off")
    )
    hit_vs_off, hit_vs_off_bad = identical(
        ("repeat", "cache_on"), ("repeat", "cache_off")
    )
    hit_vs_cold, hit_vs_cold_bad = identical(
        ("repeat", "cache_on"), ("cold", "cache_on")
    )

    def mode_rows(phase: str, mode: str) -> list[dict]:
        return [
            row
            for row in measurements
            if row["phase"] == phase and row["mode"] == mode
        ]

    def latencies(phase: str, mode: str) -> list[float]:
        return [float(row["latency_ms"]) for row in mode_rows(phase, mode)]

    def hit_rate(phase: str, mode: str) -> float:
        rows_ = mode_rows(phase, mode)
        if not rows_:
            return 0.0
        return sum(1 for row in rows_ if row.get("cache_hit")) / len(rows_)

    cache_on_rows = [row for row in measurements if row["mode"] == "cache_on"]
    cache_off_rows = [row for row in measurements if row["mode"] == "cache_off"]
    cache_on_hits = sum(1 for row in cache_on_rows if row.get("cache_hit"))
    overall_hit_rate = cache_on_hits / len(cache_on_rows) if cache_on_rows else 0.0

    cold_on = latencies("cold", "cache_on")
    cold_off = latencies("cold", "cache_off")
    repeat_on = latencies("repeat", "cache_on")
    repeat_off = latencies("repeat", "cache_off")
    pairs = [
        (repeat_on_pos, repeat_off_pos)
        for repeat_on_pos, repeat_off_pos in zip(repeat_on, repeat_off)
        if repeat_on_pos > 0 and repeat_off_pos > 0
    ]
    ratios = sorted(on / off for on, off in pairs)
    by_top_k = {}
    top_k_values = sorted({int(value) for _query, value in specs})
    for top_k in top_k_values:
        on = [
            request("repeat", "cache_on", query, value)["latency_ms"]
            for query, value in specs
            if int(value) == top_k
        ]
        off = [
            request("repeat", "cache_off", query, value)["latency_ms"]
            for query, value in specs
            if int(value) == top_k
        ]
        by_top_k[str(top_k)] = {
            "specs": len([1 for _query, value in specs if int(value) == top_k]),
            "cache_on_repeat": stats([float(value) for value in on]),
            "cache_off_repeat": stats([float(value) for value in off]),
            "p50_ratio": (
                round(percentile(on, 0.50) / percentile(off, 0.50), 4) if off else None
            ),
        }
    return {
        "fixed": {
            "documents": DOCUMENTS,
            "chunks": total_chunks,
            "queries": len(specs),
            "top_k_values": list(TOP_K_VALUES),
            "measured_requests_per_mode": {
                "cache_on": len(cache_on_rows),
                "cache_off": len(cache_off_rows),
            },
            "order": "cold phase then repeat phase, paired cache-on/cache-off per spec",
        },
        "cache_on_cold": {**stats(cold_on), "hit_rate": hit_rate("cold", "cache_on")},
        "cache_off_cold": {**stats(cold_off), "hit_rate": hit_rate("cold", "cache_off")},
        "cache_on_repeat": {**stats(repeat_on), "hit_rate": hit_rate("repeat", "cache_on")},
        "cache_off_repeat": {**stats(repeat_off), "hit_rate": hit_rate("repeat", "cache_off")},
        "hit_rates": {
            "cache_on_cold": hit_rate("cold", "cache_on"),
            "cache_on_repeat": hit_rate("repeat", "cache_on"),
            "cache_on_overall": overall_hit_rate,
            "cache_off": (
                sum(1 for row in cache_off_rows if row.get("cache_hit"))
                / len(cache_off_rows)
                if cache_off_rows
                else 0.0
            ),
        },
        "paired_repeat": {
            "pairs": len(pairs),
            "median_on_over_off": round(percentile(ratios, 0.50), 4) if ratios else None,
            "p50_ratio": (
                round(percentile(repeat_on, 0.50) / percentile(repeat_off, 0.50), 4)
                if repeat_off
                else None
            ),
            "on_faster_count": sum(1 for on, off in pairs if on < off),
        },
        "by_top_k": by_top_k,
        "cost": {
            "estimated_external_cost_usd": 0.0,
            "cost_is_zero_because": (
                "the retrieval endpoint embeds with the in-process local-hash-v1 hash and calls "
                "no model or vendor provider; the answer endpoint (not part of this comparison) "
                "uses the labelled extractive stub. 0 is the accounted cost, not an omission."
            ),
            "real_model_cost": "blocked (no vendor credentials/endpoint/adapter on this host)",
            "authoritative_retrieval_executions": {
                "cache_on": len(cache_on_rows),
                "cache_off": len(cache_off_rows),
            },
            "reused_results": cache_on_hits,
            "stored_body_bytes": sum(int(row["body_bytes"]) for row in rows),
            "stored_entries": len(rows),
        },
        "correctness": {
            "specs_compared": len(specs),
            "answers_identical_cache_on_cold_vs_off": cold_vs_off,
            "answers_identical_cache_on_hit_vs_off": hit_vs_off,
            "answers_identical_hit_vs_cold": hit_vs_cold,
            "measured_requests": len(measurements),
            "measured_answer_digests": sum(
                1 for row in measurements if row.get("answer_sha256")
            ),
            "mismatched_specs": {
                "cache_on_cold_vs_off": cold_vs_off_bad,
                "cache_on_hit_vs_off": hit_vs_off_bad,
                "hit_vs_cold": hit_vs_cold_bad,
            },
            "note": (
                "every count/rate is derived from this run's measured rows (per-request "
                "answer_sha256 and cache_hit); nothing is a constant"
            ),
        },
        "cache_rows": len(rows),
    }


# ---------------------------------------------------------------------------
# independent truth: cache rows and sealed objects
# ---------------------------------------------------------------------------
def hit_counts(rows: list[dict]) -> dict[str, int]:
    return {row["cache_key"]: int(row["hit_count"]) for row in rows}


def row_fact(row: dict) -> dict:
    return {
        "key_prefix": row["cache_key"][:16],
        "principal_id": row["principal_id"],
        "knowledge_base_id": row["knowledge_base_id"],
        "top_k": int(row["top_k"]),
        "item_count": int(row["item_count"]),
        "body_bytes": int(row["body_bytes"]),
        "hit_count": int(row["hit_count"]),
        "source_document_count": int(row["source_document_count"]),
        "document_ids": sorted({item["document_id"] for item in row["items"]}),
        "chunk_ids": [item["chunk_id"] for item in row["items"]],
    }


def object_path(stack, object_key: str) -> Path:
    root = stack.object_dir.resolve()
    path = (root / object_key).resolve()
    assert root == path or root in path.parents, (root, path)
    return path


def remove_object(stack, object_key: str) -> bytes:
    """Remove one sealed object file and return its bytes (a real storage fault)."""
    path = object_path(stack, object_key)
    data = path.read_bytes()
    path.unlink()
    assert not path.exists()
    return data


def restore_object(stack, object_key: str, data: bytes) -> None:
    path = object_path(stack, object_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def hold_object_unreadable(stack, object_key: str):
    """Make one sealed object fail a *concurrent read* with an OS error.

    Unlike deleting the object (a deterministic ``ErrNotExist``), this holds the
    file with no sharing on Windows (or removes the read permission on POSIX),
    so the API's ``os.Open`` fails with a sharing/permission error that is not
    one of the store's deterministic sentinels. That is exactly the transient
    storage failure the T113-2 classification must treat as a miss-only.
    Returns a restore callable (F13B/T113 review fix T113-2).
    """
    path = object_path(stack, object_key)
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        GENERIC_READ = 0x80000000
        OPEN_EXISTING = 3
        handle = kernel32.CreateFileW(str(path), GENERIC_READ, 0, None, OPEN_EXISTING, 0, None)
        invalid = wintypes.HANDLE(-1).value
        if handle in (None, invalid):
            raise OSError(ctypes.get_last_error(), "the exclusive object hold failed")
        released = {"done": False}

        def restore() -> None:
            if not released["done"]:
                kernel32.CloseHandle(handle)
                released["done"] = True

        return restore
    mode = path.stat().st_mode
    path.chmod(0)

    def restore() -> None:
        path.chmod(mode)

    return restore


# ---------------------------------------------------------------------------
# real API / deployment actions
# ---------------------------------------------------------------------------
def disable_principal(admin, principal_id: str, *, disabled: bool = True):
    """The real admin revocation entry: PATCH /api/v1/admin/principals/{id}."""
    return admin.client.request(
        "PATCH",
        "/api/v1/admin/principals/" + principal_id,
        body={"disabled": disabled, "reason": "T113 access revocation evidence"},
        csrf=admin.csrf,
    )


def cache_table_available(dbname: str) -> bool:
    present = st.sql_scalar(
        dbname,
        "SELECT CASE WHEN to_regclass('platform.knowledge_retrieval_cache') IS NULL "
        "THEN 0 ELSE 1 END",
    )
    return present == "1"


def cache_table_rename(dbname: str) -> None:
    """Make the cache backend unavailable for real: rename the relation."""
    assert cache_table_available(dbname), "the cache table must exist before the outage"
    st.sql(
        dbname,
        f"ALTER TABLE {CACHE_TABLE} RENAME TO knowledge_retrieval_cache_t113_offline",
    )
    assert not cache_table_available(dbname)


def cache_table_restore(dbname: str) -> None:
    offline = st.sql_scalar(
        dbname,
        "SELECT CASE WHEN to_regclass("
        "'platform.knowledge_retrieval_cache_t113_offline') IS NULL THEN 0 ELSE 1 END",
    )
    if offline == "1":
        st.sql(
            dbname,
            "ALTER TABLE platform.knowledge_retrieval_cache_t113_offline "
            "RENAME TO knowledge_retrieval_cache",
        )
    assert cache_table_available(dbname)


# ---------------------------------------------------------------------------
# the committed page script (Web entry) executed as committed bytes under Node
# ---------------------------------------------------------------------------
NODE = shutil.which("node")


def web_retrieval_body(web_dir: Path, query: str, top_k: int, workdir: Path) -> dict | None:
    """Execute the committed knowledge.js and return buildRetrievalBody(query, top_k).

    ``None`` means Node is unavailable; the caller records that as not_run rather
    than claiming a browser/DOM result.
    """
    if NODE is None:
        return None
    harness = Path(workdir)
    harness.parent.mkdir(parents=True, exist_ok=True)
    harness.write_text(
        r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/knowledge.js', 'utf8'), context);
const built = context.buildRetrievalBody(__QUERY__, __TOP_K__);
process.stdout.write(JSON.stringify(built));
""".replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__QUERY__", json.dumps(query))
        .replace("__TOP_K__", json.dumps(str(top_k))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [NODE, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    built = json.loads(completed.stdout)
    assert built.get("ok") is True, built
    return built["body"]


# ---------------------------------------------------------------------------
# evidence recorder
# ---------------------------------------------------------------------------
EXPECTED_SECTIONS = ("comparison", "deletion", "revocation", "negative_cases", "boundaries")


class Evidence:
    """Accumulate the five executed sections; only a full pass may write JSON."""

    def __init__(self, *, branch: str | None = None, commit: str | None = None) -> None:
        self.branch = branch
        self.commit = commit
        self.sections: dict[str, dict] = {}
        self.not_run: list[dict] = []

    def record(self, section: str, payload: dict, *, verdict: str = "passed") -> dict:
        assert section in EXPECTED_SECTIONS, section
        record = {"id": section, "verdict": verdict, "recorded_at": utc_now()}
        record.update(payload)
        self.sections[section] = record
        return record

    def mark_not_run(self, item: str, *, reason: str) -> None:
        self.not_run.append({"item": item, "reason": reason, "recorded_at": utc_now()})

    def document(self) -> dict:
        missing = [section for section in EXPECTED_SECTIONS if section not in self.sections]
        failed = [
            section
            for section, record in self.sections.items()
            if record["verdict"] != "passed"
        ]
        if failed:
            status = "failed"
        elif missing:
            status = "partial"
        else:
            status = "passed"
        return {
            "ticket": "T113",
            "issue": 114,
            "title": (
                "cache switch comparison and zero stale/unauthorized retrieval-cache hits"
            ),
            "status": status,
            "branch": self.branch,
            "commit": self.commit,
            "generated_at": utc_now(),
            "environment": ENVIRONMENT,
            "versions": VERSIONS,
            "commands": list(COMMANDS),
            "expected_sections": list(EXPECTED_SECTIONS),
            "sections": [
                self.sections[section]
                for section in EXPECTED_SECTIONS
                if section in self.sections
            ],
            "missing_sections": missing,
            "failed_sections": failed,
            "failures": [],
            "blocked": list(BLOCKED),
            "not_run": list(NOT_RUN) + self.not_run,
        }

    def write_if_enabled(self) -> Path | None:
        if os.environ.get("F13B_WRITE_EVIDENCE") != "1":
            return None
        document = self.document()
        if document["status"] != "passed":
            return None
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return EVIDENCE_PATH
