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
    "python": "3.12.2",
    "go": "1.27.3",
    "node": "v24.13.0",
    "postgresql": "16.4",
    "embedding_model_revision": EMBEDDING_REVISION,
    "answer_generator": "extractive-stub/1",
    "cache_key_version": CACHE_KEY_VERSION,
    "cache_ttl_seconds": TTL_SECONDS,
    "cache_max_entry_bytes": CACHE_MAX_ENTRY_BYTES,
}


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
    """One per-request evidence row (latency, cache state, chunk count)."""
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


def comparison_summary(
    *,
    specs,
    total_chunks: int,
    cold_on_ms: list[float],
    cold_off_ms: list[float],
    repeat_on_ms: list[float],
    repeat_off_ms: list[float],
    rows: list[dict],
) -> dict:
    pairs = [
        (on, off)
        for on, off in zip(repeat_on_ms, repeat_off_ms)
        if off > 0 and on > 0
    ]
    ratios = sorted(on / off for on, off in pairs)
    cached_retrievals = len(specs)
    by_top_k = {}
    top_k_values = sorted({value for _query, value in specs})
    for top_k in top_k_values:
        indexes = [
            index for index, (_query, value) in enumerate(specs) if value == top_k
        ]
        on = [repeat_on_ms[index] for index in indexes]
        off = [repeat_off_ms[index] for index in indexes]
        by_top_k[str(top_k)] = {
            "specs": len(indexes),
            "cache_on_repeat": stats(on),
            "cache_off_repeat": stats(off),
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
            "measured_requests_per_mode": 2 * len(specs),
            "order": "cold phase then repeat phase, paired cache-on/cache-off per spec",
        },
        "cache_on_cold": {**stats(cold_on_ms), "hit_rate": 0.0},
        "cache_off_cold": {**stats(cold_off_ms), "hit_rate": 0.0},
        "cache_on_repeat": {**stats(repeat_on_ms), "hit_rate": 1.0},
        "cache_off_repeat": {**stats(repeat_off_ms), "hit_rate": 0.0},
        "hit_rates": {
            "cache_on_cold": 0.0,
            "cache_on_repeat": 1.0,
            "cache_on_overall": cached_retrievals / (2 * len(specs)),
            "cache_off": 0.0,
        },
        "paired_repeat": {
            "pairs": len(pairs),
            "median_on_over_off": round(percentile(ratios, 0.50), 4) if ratios else None,
            "p50_ratio": (
                round(percentile(repeat_on_ms, 0.50) / percentile(repeat_off_ms, 0.50), 4)
                if repeat_off_ms
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
                "cache_on": cached_retrievals,
                "cache_off": 2 * cached_retrievals,
            },
            "reused_results": cached_retrievals,
            "stored_body_bytes": sum(int(row["body_bytes"]) for row in rows),
            "stored_entries": len(rows),
        },
        "correctness": {
            "specs_compared": len(specs),
            "answers_identical_cache_on_cold_vs_off": len(specs),
            "answers_identical_cache_on_hit_vs_off": len(specs),
            "answers_identical_hit_vs_cold": len(specs),
            "note": "exact JSON equality of items/next_cursor/acl_epoch/index, asserted per spec",
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
        self.failures: list[dict] = []
        self.blocked: list[dict] = []
        self.not_run: list[dict] = []

    def record(self, section: str, payload: dict, *, verdict: str = "passed") -> dict:
        assert section in EXPECTED_SECTIONS, section
        record = {"id": section, "verdict": verdict, "recorded_at": utc_now()}
        record.update(payload)
        self.sections[section] = record
        return record

    def fail(self, section: str, error: str) -> None:
        self.failures.append(
            {"section": section, "error": error, "recorded_at": utc_now()}
        )

    def block(self, item: str, *, reason: str, unblock: str) -> None:
        self.blocked.append({"item": item, "reason": reason, "unblock": unblock})

    def mark_not_run(self, item: str, *, reason: str) -> None:
        self.not_run.append({"item": item, "reason": reason, "recorded_at": utc_now()})

    def document(self) -> dict:
        missing = [section for section in EXPECTED_SECTIONS if section not in self.sections]
        failed = [
            section
            for section, record in self.sections.items()
            if record["verdict"] != "passed"
        ]
        if failed or self.failures:
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
            "failures": self.failures,
            "blocked": list(BLOCKED) + self.blocked,
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
