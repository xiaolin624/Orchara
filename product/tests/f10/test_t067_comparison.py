"""T067 comparison experiment: retrieval with the cache off vs on.

Same real stack, same dataset, same queries, two real API processes (one with
``ORCHARA_KNOWLEDGE_RETRIEVAL_CACHE=off``). The experiment records:

* hit rate,
* per-query latency (p50/p95 over 8 measured runs per query, i.e. 24 runs per
  mode),
* correctness (identical chunk ids, texts, scores and stored citations),
* the cost of the mandatory validation re-check (hit latency at top_k=5 vs
  top_k=20; the per-extra-chunk delta).

Latency is **not** asserted against a boundary: this host runs several suites
concurrently (BRIEF §6.1), so the numbers are recorded in the report and the
keep/drop decision is made there. The only timing assertion is a gross-regression
guard (the hit path may not be several times slower than an authoritative miss);
it is not the keep/drop verdict.

The two modes are sampled **paired** (one cache-on hit, then the same query
against the cache-off deployment, per run, inside one measurement window). An
earlier version measured the whole on phase and then the whole off phase
minutes apart; on the loaded host a host-wide slow period landing in one phase
inverted the p50 comparison (13.9 ms vs 10.9 ms) even though the hit path was
3.4 ms and the miss path 9.9 ms when both were sampled together. Pairing keeps
both samples under the same load and reports the paired ratio, which is the
meaningful relative view on a noisy host.
"""

from __future__ import annotations

import json
import math
import time

from support import f00b, t055, t064, t067
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

DOCUMENTS = 8
QUERIES = (
    "华北地区十月的销售额是多少",
    "退款政策需要几天内申请",
    "华东地区订单数量",
)
TOP_K = 5
VALIDATION_TOP_K = 20
MEASURED_RUNS = 8
FILLER = (
    "补充条款：每期销售额以财务系统结算单为准，订单数量以出库记录为准，"
    "退款申请需经过客服审核并在原支付渠道退回。"
)


def benchmark_document(index: int) -> str:
    """A real txt document with enough text to produce several chunks."""
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
    return "\n".join(sections)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    rank = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[rank]


def timed(callable_) -> tuple[float, object]:
    started = time.perf_counter()
    response = callable_()
    return (time.perf_counter() - started) * 1000.0, response


def assert_identical(actual: dict, expected: dict, context: str) -> None:
    assert actual["items"] == expected["items"], context
    assert actual["retrieval"]["acl_epoch"] == expected["retrieval"]["acl_epoch"], context
    assert (
        actual["retrieval"]["embedding_model_revision"]
        == expected["retrieval"]["embedding_model_revision"]
    ), context


def test_cache_off_vs_on_same_queries_with_real_measurements(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    contents = [benchmark_document(index).encode("utf-8") for index in range(DOCUMENTS)]
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "comparison", contents=contents
    )
    assert len(t067.cache_rows(stack.dbname, base_id=base_id)) == 0

    # ---- cache ON: cold requests store, then measured hits -----------------
    on_cold: dict[str, dict] = {}
    cold_ms: list[float] = []
    for query in QUERIES:
        elapsed, response = timed(
            lambda query=query: t055.retrieval_query(alice, base_id, query, top_k=TOP_K)
        )
        assert response.status == 200, response.text
        cache = response.json["retrieval"]["cache"]
        assert cache["enabled"] is True and cache["hit"] is False, cache
        assert cache["stored"] is True, cache
        on_cold[query] = response.json
        cold_ms.append(elapsed)
    assert len(t067.cache_rows(stack.dbname, base_id=base_id)) == len(QUERIES)

    # ---- measured window: paired cache-on hit / cache-off miss ------------
    on_hit_ms: list[float] = []
    on_hits: dict[str, dict] = {}
    off_ms: list[float] = []
    off_payloads: dict[str, dict] = {}
    hit_count = 0
    measured = 0
    with t067.cache_disabled_server(api_server, stack, tmp_path / "comparison-off") as server:
        disabled = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        # Warm the second deployment first: pool/prepared-statement setup must not
        # land inside the measured window.
        for query in QUERIES:
            warmup = t055.retrieval_query(disabled, base_id, query, top_k=TOP_K)
            assert warmup.json["retrieval"]["cache"]["enabled"] is False
        for _ in range(MEASURED_RUNS):
            for query in QUERIES:
                elapsed_on, response = timed(
                    lambda query=query: t055.retrieval_query(alice, base_id, query, top_k=TOP_K)
                )
                cache = response.json["retrieval"]["cache"]
                assert response.status == 200, response.text
                assert cache["enabled"] is True, cache
                measured += 1
                if cache["hit"]:
                    hit_count += 1
                    assert int(cache["validated_chunks"]) == len(response.json["items"]), cache
                on_hit_ms.append(elapsed_on)
                on_hits[query] = response.json

                elapsed_off, response = timed(
                    lambda query=query: t055.retrieval_query(disabled, base_id, query, top_k=TOP_K)
                )
                cache = response.json["retrieval"]["cache"]
                assert cache["enabled"] is False and cache["hit"] is False, cache
                off_ms.append(elapsed_off)
                off_payloads[query] = response.json
    hit_rate = hit_count / measured
    assert hit_rate == 1.0, hit_rate

    # ---- mandatory validation cost: same query at two top_k values --------
    validation_ms: list[float] = []
    _, validation_cold = timed(
        lambda: t055.retrieval_query(alice, base_id, QUERIES[0], top_k=VALIDATION_TOP_K)
    )
    assert validation_cold.json["retrieval"]["cache"]["stored"] is True
    for _ in range(MEASURED_RUNS):
        elapsed, validation_hit = timed(
            lambda: t055.retrieval_query(alice, base_id, QUERIES[0], top_k=VALIDATION_TOP_K)
        )
        cache = validation_hit.json["retrieval"]["cache"]
        assert cache["hit"] is True, cache
        assert int(cache["validated_chunks"]) == len(validation_hit.json["items"]), cache
        validation_ms.append(elapsed)
    extra_items = len(validation_hit.json["items"]) - TOP_K
    per_item_ms = (
        (percentile(validation_ms, 0.5) - percentile(on_hit_ms, 0.5)) / extra_items
        if extra_items > 0
        else 0.0
    )

    # ---- correctness is unchanged across every mode ----------------------
    for query in QUERIES:
        assert_identical(on_cold[query], off_payloads[query], f"cold vs off: {query}")
        assert_identical(on_hits[query], off_payloads[query], f"hit vs off: {query}")
        assert_identical(on_cold[query], on_hits[query], f"cold vs hit: {query}")

    rows = t067.cache_rows(stack.dbname, base_id=base_id)
    assert len(rows) == len(QUERIES) + 1, [row["canonical_query"] for row in rows]
    total_chunks = sum(
        len(t055.chunk_rows(stack.dbname, document_id)) for document_id in documents
    )
    summary = {
        "documents": DOCUMENTS,
        "chunks": total_chunks,
        "queries": list(QUERIES),
        "measured_runs_per_query": MEASURED_RUNS,
        "cache_on": {
            "cold_ms": [round(value, 3) for value in cold_ms],
            "hit_rate": hit_rate,
            "p50_ms": round(percentile(on_hit_ms, 0.50), 3),
            "p95_ms": round(percentile(on_hit_ms, 0.95), 3),
            "runs": len(on_hit_ms),
        },
        "cache_off": {
            "p50_ms": round(percentile(off_ms, 0.50), 3),
            "p95_ms": round(percentile(off_ms, 0.95), 3),
            "runs": len(off_ms),
        },
        # The paired view (hit/miss sampled next to each other) is the robust one
        # on a loaded host: a ratio below 1 means the hit is still faster than the
        # authoritative miss measured in the same window.
        "paired": {
            "pairs": len(on_hit_ms),
            "median_on_over_off": round(
                percentile(
                    [
                        on / off
                        for on, off in zip(on_hit_ms, off_ms)
                        if off > 0
                    ],
                    0.50,
                ),
                3,
            ),
        },
        "validation": {
            "top_k": VALIDATION_TOP_K,
            "items": len(validation_hit.json["items"]),
            "p50_ms": round(percentile(validation_ms, 0.50), 3),
            "per_extra_item_ms": round(per_item_ms, 3),
        },
        "cache_rows": [
            {
                "query": row["canonical_query"],
                "top_k": int(row["top_k"]),
                "item_count": int(row["item_count"]),
                "body_bytes": int(row["body_bytes"]),
                "hit_count": int(row["hit_count"]),
            }
            for row in rows
        ],
    }
    print("T067-COMPARISON " + json.dumps(summary, ensure_ascii=False))
    # Gross-regression guard only; the quantitative keep/drop decision is the
    # recorded p50/p95 above (ADR 0015 D5). BRIEF §6.1 forbids gating on a
    # sub-second wall-clock window on this host, which runs several suites at
    # once: sampling both modes in one window still gave hit 13.9-20.5 ms vs miss
    # 10.9-12.9 ms under load, while the same tree measured 3.4 ms vs 9.9 ms when
    # the host was quiet. Host noise dominates the ~6 ms the cache buys on this
    # 24-chunk corpus, so a strict ordering assertion is not a valid gate; a cache
    # several times slower than an authoritative miss still fails.
    assert summary["cache_on"]["p50_ms"] <= summary["cache_off"]["p50_ms"] * 3.0, summary
