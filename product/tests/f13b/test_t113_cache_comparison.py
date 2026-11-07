"""F13B/T113 acceptance: the retrieval-cache switch and zero stale/unauthorized hits.

This is the F13B-level re-run of the T067 cache claim, not a citation of T067's
numbers. Every assertion drives the real stack: a throwaway PostgreSQL 16.4
database migrated by the compiled ``cmd/admin``, the compiled ``cmd/api`` behind
real HTTP with the real filesystem object store and HMAC-signed links, the real
``document_ingest``/``artifact_finalize`` maintenance processes, and (for the
cache-off / faulted deployments) additional real API processes over the same
database and object directory. The API is never its own oracle: cache rows, chunk
and document rows, sealed object bytes and the cache table itself are read from
PostgreSQL/the filesystem and compared with the HTTP answers.

The five executed evidence sections (``support/t113.py`` aggregates them into
``reports/f13b/T113-cache-comparison.json`` when ``F13B_WRITE_EVIDENCE=1``):

* ``comparison``  AC1/AC2: one fixed 8-document corpus (24 chunks), one fixed
  24-spec query sequence (8 queries x top_k 3/8/20, fixed order), the same
  sequence against a cache-on and a cache-off real API process, paired sampling,
  per-request latency/hit/chunk-count, hit rates, P50/P95/P99, cold-vs-hot and
  the cost accounting (0 because the embedding is the local ``local-hash-v1``
  hash and no model provider is called; the real-model cost is ``blocked``).
* ``deletion``    AC3: the real T069 document-deletion transaction, executed while
  a live cache row exists, followed by the retrieval/citation/answer/detail/
  diagnostics read paths, a poisoned live row under the current key, reindex and
  ingest replay, and a concurrent query storm during the final delete.
* ``revocation``  AC3/AC4: cross-principal isolation (A's row never serves B, a
  poisoned B row is discarded, B cannot read A's known base at all), the admin
  credential revocation (``PATCH /admin/principals/{id}`` disabled), and the
  grounded-answer provenance (answers are regenerated on every request even when
  the retrieval cache hits; answer text is never stored, so it cannot cross a
  user or a point in time).
* ``negative_cases`` AC4: source-revision changes (add a document, re-upload the
  same bytes as a new document) never hit the old key; empty and partial results
  are not stored; a previously stored entry whose sealed body became unreadable
  is discarded instead of served; an over-bound entry is not stored.
* ``boundaries``  AC5: an unknown cache switch refuses startup, and a cache
  backend that is unavailable degrades to the authoritative path instead of
  failing or serving a hit, then recovers with a fresh process.

Boundaries recorded, not hidden: there is no real vendor embedding/model provider
on this host (all real-model latency/quality/cost numbers are ``blocked``); the
knowledge cache has no SSE surface, so SSE is not applicable; a browser DOM is
``not_run`` (the committed page script is executed under Node to prove the page
sends the same REST retrieval body the measurements use); and this build has no
per-knowledge-base ACL/grant vocabulary (``policy.ResourceTypes`` is
``{application}``), so a "grant revoke" entry over a knowledge base does not
exist -- the real content-revocation entry is the document tombstone (deletion
section) and the real credential-revocation entry is the admin disable above.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from support import f00b, t055, t061, t064, t067, t069, t113
from support import stack as st
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures


@pytest.fixture(scope="session")
def cache_evidence() -> t113.Evidence:
    """One recorder for the whole file; writes the JSON only on a full pass."""
    branch, commit = t113.git_branch_and_commit()
    recorder = t113.Evidence(branch=branch, commit=commit)
    yield recorder
    recorder.write_if_enabled()


# ---------------------------------------------------------------------------
# AC1/AC2: fixed corpus + fixed query sequence, cache off vs on
# ---------------------------------------------------------------------------
def test_t113_cache_off_vs_on_comparison_with_latency_and_cost(
    t055_stack, api_server, tmp_path: Path, cache_evidence: t113.Evidence
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    login = "t113.compare"
    contents = [t113.benchmark_document(index) for index in range(t113.DOCUMENTS)]
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "t113-comparison", login=login, contents=contents
    )
    assert t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id) == []

    corpus_sha256 = [t113.sha256_hex(content) for content in contents]
    total_chunks = sum(len(t055.chunk_rows(dbname, document_id)) for document_id in documents)
    # top_k=20 must be satisfiable by the fixed corpus; the exact chunk count is
    # recorded (the T067 generator produces 24) rather than assumed by the test.
    assert total_chunks >= max(t113.TOP_K_VALUES), total_chunks
    migration_chain = st.migration_chain(dbname)
    migration_tail = max(migration_chain)

    specs = t113.QUERY_SPECS
    assert len(specs) == 24, specs

    measurements: list[dict] = []
    cold_on: dict[int, dict] = {}
    cold_off: dict[int, dict] = {}
    repeat_on: dict[int, dict] = {}
    repeat_off: dict[int, dict] = {}
    cold_on_ms: list[float] = []
    cold_off_ms: list[float] = []
    repeat_on_ms: list[float] = []
    repeat_off_ms: list[float] = []

    with t067.cache_disabled_server(api_server, stack, tmp_path / "t113-cache-off") as off_server:
        off = t064.login_on(off_server, login, f00b.USER_PASSWORD)

        # Warm both real processes with a query outside the measured set so pool
        # and prepared-statement setup is not charged to the first measured call.
        on_warmup = t055.retrieval_query(alice, base_id, t113.WARMUP_QUERY, top_k=3)
        off_warmup = t055.retrieval_query(off, base_id, t113.WARMUP_QUERY, top_k=3)
        assert on_warmup.status == 200 and off_warmup.status == 200

        # Phase 1 (cold): the identical 24-spec sequence on both deployments,
        # paired one-by-one inside the same measurement window.
        for index, (query, top_k) in enumerate(specs):
            on_ms, on_response = t113.timed(
                lambda query=query, top_k=top_k: t055.retrieval_query(
                    alice, base_id, query, top_k=top_k
                )
            )
            off_ms, off_response = t113.timed(
                lambda query=query, top_k=top_k: t055.retrieval_query(
                    off, base_id, query, top_k=top_k
                )
            )
            assert on_response.status == 200, on_response.text
            assert off_response.status == 200, off_response.text
            on_payload = on_response.json
            off_payload = off_response.json
            on_cache = on_payload["retrieval"]["cache"]
            off_cache = off_payload["retrieval"]["cache"]
            assert on_cache["enabled"] is True, on_cache
            assert on_cache["hit"] is False and on_cache["stored"] is True, on_cache
            assert off_cache["enabled"] is False, off_cache
            assert off_cache["hit"] is False and off_cache["stored"] is False, off_cache
            assert len(on_payload["items"]) == min(top_k, total_chunks), on_payload["items"]
            assert t113.comparable(on_payload) == t113.comparable(off_payload), (
                f"cold answers differ for spec {index}: {query!r} top_k={top_k}"
            )
            cold_on[index] = on_payload
            cold_off[index] = off_payload
            cold_on_ms.append(on_ms)
            cold_off_ms.append(off_ms)
            measurements.append(
                t113.measurement(
                    seq=len(measurements) + 1,
                    phase="cold",
                    mode="cache_on",
                    query=query,
                    top_k=top_k,
                    latency_ms=on_ms,
                    response=on_response,
                )
            )
            measurements.append(
                t113.measurement(
                    seq=len(measurements) + 1,
                    phase="cold",
                    mode="cache_off",
                    query=query,
                    top_k=top_k,
                    latency_ms=off_ms,
                    response=off_response,
                )
            )

        # Phase 2 (repeat): the same sequence again. The cache-on deployment must
        # hit the entry stored by phase 1; the cache-off deployment misses every
        # time. The two samples are taken next to each other.
        for index, (query, top_k) in enumerate(specs):
            on_ms, on_response = t113.timed(
                lambda query=query, top_k=top_k: t055.retrieval_query(
                    alice, base_id, query, top_k=top_k
                )
            )
            off_ms, off_response = t113.timed(
                lambda query=query, top_k=top_k: t055.retrieval_query(
                    off, base_id, query, top_k=top_k
                )
            )
            assert on_response.status == 200, on_response.text
            assert off_response.status == 200, off_response.text
            on_payload = on_response.json
            off_payload = off_response.json
            on_cache = on_payload["retrieval"]["cache"]
            off_cache = off_payload["retrieval"]["cache"]
            assert on_cache["enabled"] is True and on_cache["hit"] is True, on_cache
            assert on_cache["stored"] is False, on_cache
            assert int(on_cache["validated_chunks"]) == len(on_payload["items"]), on_cache
            assert on_cache["key_prefix"] == cold_on[index]["retrieval"]["cache"]["key_prefix"], (
                on_cache,
                cold_on[index]["retrieval"]["cache"],
            )
            assert off_cache["enabled"] is False, off_cache
            assert off_cache["hit"] is False and off_cache["stored"] is False, off_cache
            assert t113.comparable(on_payload) == t113.comparable(off_payload), (
                f"hit answers differ from the authoritative answer for spec {index}"
            )
            assert t113.comparable(on_payload) == t113.comparable(cold_on[index]), (
                f"a hit answered differently from the cold miss for spec {index}"
            )
            repeat_on[index] = on_payload
            repeat_off[index] = off_payload
            repeat_on_ms.append(on_ms)
            repeat_off_ms.append(off_ms)
            measurements.append(
                t113.measurement(
                    seq=len(measurements) + 1,
                    phase="repeat",
                    mode="cache_on",
                    query=query,
                    top_k=top_k,
                    latency_ms=on_ms,
                    response=on_response,
                )
            )
            measurements.append(
                t113.measurement(
                    seq=len(measurements) + 1,
                    phase="repeat",
                    mode="cache_off",
                    query=query,
                    top_k=top_k,
                    latency_ms=off_ms,
                    response=off_response,
                )
            )

    # Independent truth: one live row per measured spec plus the warmup entry, no
    # rows written by the cache-off deployment, and one recorded hit per spec.
    rows = t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
    assert len(rows) == len(specs) + 1, [row["canonical_query"] for row in rows]
    assert sum(int(row["hit_count"]) for row in rows) == len(specs), [
        int(row["hit_count"]) for row in rows
    ]
    assert all(row["embedding_model_revision"] == t113.EMBEDDING_REVISION for row in rows)
    assert all(int(row["top_k"]) in t113.TOP_K_VALUES for row in rows)
    assert all(int(row["source_document_count"]) == len(documents) for row in rows)

    # The committed page script builds the same retrieval request body the
    # measurements send (the page is the formal Web entry for this REST surface).
    page = stack.server.http.get("/knowledge")
    assert page.status == 200, page.text
    script = stack.server.http.get("/assets/knowledge.js")
    assert script.status == 200, script.text
    page_body = t113.web_retrieval_body(
        st.WEB_DIR, specs[0][0], specs[0][1], tmp_path / "t113-page.js"
    )
    if page_body is None:
        cache_evidence.mark_not_run(
            "web_page_node_execution",
            reason="node is not on PATH; the committed page script was not executed",
        )
        web_path = {"executed": False, "reason": "node not on PATH"}
    else:
        assert page_body == {"query": specs[0][0], "top_k": specs[0][1]}, page_body
        web_path = {
            "executed": True,
            "page": "/knowledge",
            "script": "/assets/knowledge.js",
            "retrieval_body_matches_measured_request": True,
            "browser_dom": "not_run (no Playwright/Chromium on this host)",
        }

    hit_rate_repeat = sum(
        1 for payload in repeat_on.values() if payload["retrieval"]["cache"]["hit"]
    ) / len(specs)
    assert hit_rate_repeat == 1.0, hit_rate_repeat
    assert all(
        payload["retrieval"]["cache"]["hit"] is False for payload in repeat_off.values()
    )

    summary = t113.comparison_summary(
        specs=specs,
        total_chunks=total_chunks,
        cold_on_ms=cold_on_ms,
        cold_off_ms=cold_off_ms,
        repeat_on_ms=repeat_on_ms,
        repeat_off_ms=repeat_off_ms,
        rows=rows,
    )
    print("T113-COMPARISON " + json.dumps(summary, ensure_ascii=False))
    # Gross-regression guard only (the same precedent as T067/ADR 0015 D5): the
    # host runs several suites at once, so the quantitative keep/drop verdict is
    # the recorded P50/P95 table, not a sub-second ordering assertion.
    assert summary["cache_on_repeat"]["p50_ms"] <= summary["cache_off_repeat"]["p50_ms"] * 3.0, (
        summary
    )

    cache_evidence.record(
        "comparison",
        {
            "fixed_inputs": {
                "corpus": {
                    "documents": len(documents),
                    "chunks": total_chunks,
                    "document_sha256": corpus_sha256,
                    "generator": "support/t113.py::benchmark_document (T067-shaped fixed text)",
                },
                "queries": [{"query": query, "top_k": top_k} for query, top_k in specs],
                "query_count": len(specs),
                "order": "fixed: 8 queries x top_k [3, 8, 20], cold phase then repeat phase",
                "embedding_model_revision": t113.EMBEDDING_REVISION,
                "cache_key_version": t113.CACHE_KEY_VERSION,
                "cache_ttl_seconds": t113.TTL_SECONDS,
                "parser_version": t055.citation_of(t055.chunk_rows(dbname, documents[0])[0])[
                    "parser_version"
                ],
                "chunker_version": t055.citation_of(
                    t055.chunk_rows(dbname, documents[0])[0]
                )["chunker_version"],
                "cache_switch": f"{t113.CACHE_ENV}=on (default) vs =off (second real process)",
                "migration_chain": {
                    "entries": len(migration_chain),
                    "tail_version": migration_tail,
                    "tail_name": migration_chain[migration_tail],
                },
                "resource_profile": t113.ENVIRONMENT["resource_profile"],
            },
            "measurements": measurements,
            "cache_on_repeat": {
                **t113.stats(repeat_on_ms),
                "hit_rate": hit_rate_repeat,
                "validated_chunks_equals_items": True,
            },
            "cache_on_cold": {**t113.stats(cold_on_ms), "hit_rate": 0.0},
            "cache_off_repeat": {**t113.stats(repeat_off_ms), "hit_rate": 0.0},
            "cache_off_cold": {**t113.stats(cold_off_ms), "hit_rate": 0.0},
            "summary": summary,
            "web_path": web_path,
            "cache_rows": [
                {
                    "key_prefix": row["cache_key"][:16],
                    "top_k": int(row["top_k"]),
                    "item_count": int(row["item_count"]),
                    "body_bytes": int(row["body_bytes"]),
                    "hit_count": int(row["hit_count"]),
                }
                for row in rows
            ],
        },
    )


# ---------------------------------------------------------------------------
# AC3 deletion: zero stale hits through every real read path
# ---------------------------------------------------------------------------
def test_t113_deletion_leaves_zero_stale_cache_hits(
    t055_stack, tmp_path: Path, cache_evidence: t113.Evidence
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    login = "t113.delete"
    alice, base_id, artifact_ids, documents = t067.setup_kb_with_documents(
        stack,
        "t113-delete",
        login=login,
        contents=[t055.SAMPLE_DOCUMENT, t113.DOC_B],
    )
    deleted_document, kept_document = documents[1], documents[0]
    query, top_k = t113.QUERIES[1], 10

    warm = t067.warm_cache(alice, base_id, query, top_k=top_k)
    old_prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    old_row = t067.cache_row_with_prefix(dbname, old_prefix)
    deleted_item = next(
        item for item in warm.json["items"] if item["document_id"] == deleted_document
    )
    assert t113.DELETED_MARKER in deleted_item["text"]
    old_key = old_row["cache_key"]
    hits_before = t113.hit_counts(t067.cache_rows(dbname, base_id=base_id))
    first_hit = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert first_hit.json["retrieval"]["cache"]["hit"] is True, first_hit.json["retrieval"]
    hits_before_deletion = t113.hit_counts(t067.cache_rows(dbname, base_id=base_id))
    assert hits_before_deletion[old_key] == hits_before[old_key] + 1

    accepted = t069.deletion_of(
        t069.delete_resource(alice, t069.RESOURCE_DOCUMENT, deleted_document)
    )
    assert accepted["resource_id"] == deleted_document
    document_after = t055.document_row(dbname, deleted_document)
    assert document_after["deleted_at"] is not None and document_after["state"] == "deleted"
    job = t069.deletion_job(dbname, accepted["id"])
    assert job is not None and job["state"] == "pending", job
    assert "retrieval_cache" in t069.remaining_scopes(job), job

    after = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert after.status == 200, after.text
    after_cache = after.json["retrieval"]["cache"]
    assert after_cache["hit"] is False, after_cache
    assert after_cache["key_prefix"] != old_prefix, after_cache
    assert all(item["document_id"] != deleted_document for item in after.json["items"])
    assert t113.DELETED_MARKER not in after.text
    # The old row was not evicted (deletion leaves cache cleanup to a later scope)
    # and its hit count must not have moved after the tombstone committed.
    still_live = t067.cache_row_by_key(dbname, old_key)
    assert still_live is not None
    assert int(still_live["hit_count"]) == hits_before_deletion[old_key]
    new_row = t067.cache_row_with_prefix(dbname, after_cache["key_prefix"])
    assert new_row["cache_key"] != old_key
    assert all(
        item["document_id"] != deleted_document for item in new_row["items"]
    ), new_row["items"]

    # Every remaining read path refuses the deleted chunk without a body.
    refused_citation = t055.get_citation(alice, deleted_item["chunk_id"])
    st.assert_error(refused_citation, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    assert "text" not in refused_citation.json and "citation" not in refused_citation.json
    assert t113.DELETED_MARKER not in refused_citation.text
    st.assert_error(t055.get_document(alice, deleted_document), 404, t069.ERROR_RESOURCE_NOT_FOUND)
    st.assert_error(
        t055.document_diagnostics(alice, deleted_document), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    answer = t064.answer(alice, base_id, query, top_k=top_k)
    assert answer.status == 200, answer.text
    assert answer.json["outcome"] == t064.OUTCOME_ANSWERED, answer.json
    assert all(
        citation["document_id"] != deleted_document
        for citation in t064.citations_of(answer.json)
    ), answer.json
    assert t113.DELETED_MARKER not in answer.text

    # Hardest adversary: inject the deleted document's stored item into the live
    # entry stored under the *current* key; only the per-chunk hit re-check can
    # stop the body from being served.
    keep = [t067.stored_item(dbname, item) for item in after.json["items"]]
    stale_item = next(
        item for item in old_row["items"] if item["document_id"] == deleted_document
    )
    current_prefix = after_cache["key_prefix"]
    current_key, poisoned_items = t067.poison_live_entry(
        dbname,
        key_prefix=current_prefix,
        stale_item=stale_item,
        keep_items=keep,
    )
    assert any(item["text"] == t067.POISON_BODY for item in poisoned_items)
    poisoned = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    poisoned_cache = poisoned.json["retrieval"]["cache"]
    assert poisoned_cache["hit"] is False, poisoned_cache
    assert poisoned_cache["discarded"] is True, poisoned_cache
    assert poisoned_cache["discard_reason"] == "item_unreadable", poisoned_cache
    assert t113.DELETED_MARKER not in poisoned.text
    assert all(item["document_id"] != deleted_document for item in poisoned.json["items"])
    fresh_row = t067.cache_row_by_key(dbname, current_key)
    assert fresh_row is not None
    assert all(item["document_id"] != deleted_document for item in fresh_row["items"])

    # Recovery/replay cannot resurrect the deleted input.
    reindexed = t061.reindex(alice, deleted_document)
    assert reindexed.status == 404, reindexed.text
    assert reindexed.json["error"]["code"] == t069.ERROR_RESOURCE_NOT_FOUND
    replay = f00b.enqueue_ok(
        stack,
        kind=t055.KIND_DOCUMENT_INGEST,
        target_id=deleted_document,
        owner_id=alice.principal_id,
        dedup_key="t113-replay:" + deleted_document,
        input_artifact_id=artifact_ids[1],
    )
    completed, reports = f00b.maintenance_once(
        stack,
        "--kinds",
        t055.KIND_DOCUMENT_INGEST,
        env=f00b.maintenance_env(stack, heartbeat_interval="50ms"),
    )
    assert completed.returncode == 0, completed.stderr
    replay_job = next(
        job for job in t055.document_jobs(dbname, deleted_document) if job["id"] == replay["id"]
    )
    assert replay_job["state"] == "failed", (replay_job, reports)
    assert replay_job["failure_class"] == "business", replay_job
    still_deleted = t055.document_row(dbname, deleted_document)
    assert still_deleted["state"] == "deleted" and still_deleted["deleted_at"] is not None
    after_replay = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert all(
        item["document_id"] != deleted_document for item in after_replay.json["items"]
    )
    assert t113.DELETED_MARKER not in after_replay.text

    # A deleted source file cannot be re-ingested into a new document.
    t069.deletion_of(
        t069.delete_resource(alice, t069.RESOURCE_ARTIFACT, artifact_ids[1])
    )
    reingested = t055.ingest_document(alice, base_id, artifact_ids[1])
    st.assert_error(reingested, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    assert [row["id"] for row in t055.documents_for_artifact(dbname, artifact_ids[1])] == [
        deleted_document
    ]

    # Concurrency: keep querying while the last document is deleted. Any request
    # that *starts after* the DELETE answer returned must see an empty result and
    # no hit; a request that overlapped the commit may legitimately have read the
    # pre-commit state, so only start-time ordering is asserted.
    rows_before_final_delete = t067.cache_rows(dbname, base_id=base_id)
    hits_before_final_delete = t113.hit_counts(rows_before_final_delete)
    storm_session = f00b.session_for(stack, login, f00b.USER_PASSWORD)
    storm: list[dict] = []
    stop = threading.Event()

    def hammer() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            response = t055.retrieval_query(storm_session, base_id, query, top_k=top_k)
            finished = time.perf_counter()
            cache = (response.json.get("retrieval") or {}).get("cache") or {}
            storm.append(
                {
                    "started": started,
                    "finished": finished,
                    "status": response.status,
                    "hit": bool(cache.get("hit")),
                    "items": len(response.json.get("items") or []),
                    "marker": t113.DELETED_MARKER in response.text,
                }
            )
            time.sleep(0.01)

    thread = threading.Thread(target=hammer, name="t113-query-storm", daemon=True)
    thread.start()
    try:
        time.sleep(0.2)
        final_delete = t069.deletion_of(
            t069.delete_resource(alice, t069.RESOURCE_DOCUMENT, kept_document)
        )
        deletion_finished = time.perf_counter()
        deadline = time.time() + 8.0
        while time.time() < deadline:
            post = [row for row in storm if row["started"] >= deletion_finished]
            if len(post) >= 4:
                break
            time.sleep(0.02)
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive(), "the query storm thread did not stop"
    post_delete = [row for row in storm if row["started"] >= deletion_finished]
    assert len(post_delete) >= 4, len(post_delete)
    assert all(row["status"] == 200 for row in post_delete), post_delete
    assert all(row["items"] == 0 for row in post_delete), post_delete
    assert all(row["hit"] is False for row in post_delete), post_delete
    assert all(row["marker"] is False for row in post_delete), post_delete
    assert final_delete["resource_id"] == kept_document

    # All in-flight requests have now completed. From here a read must not touch
    # any row: snapshot the hit counts, run the post-delete read, and require the
    # snapshot to be unchanged. This is the deterministic zero-stale-hit check.
    hits_after_storm = t113.hit_counts(t067.cache_rows(dbname, base_id=base_id))
    empty = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert empty.status == 200, empty.text
    assert empty.json["items"] == []
    empty_cache = empty.json["retrieval"]["cache"]
    assert empty_cache["hit"] is False and empty_cache["stored"] is False, empty_cache
    # An empty result is never memoized: there is no row under the post-delete key.
    # A concurrent in-flight hit validation may legitimately *discard* the old row
    # (that is the mandatory hit re-check), so the count may shrink but never grow
    # and the empty key never gets a row.
    assert (
        t067.cache_rows(dbname, base_id=base_id, key_prefix=empty_cache["key_prefix"]) == []
    ), "an empty retrieval must not be memoized"
    assert len(t067.cache_rows(dbname, base_id=base_id)) <= len(rows_before_final_delete), (
        "the empty retrieval must not create a cache row"
    )
    hits_after_empty = t113.hit_counts(t067.cache_rows(dbname, base_id=base_id))
    assert hits_after_empty == hits_after_storm, (
        "a post-delete read must not hit any cache row",
        hits_after_storm,
        hits_after_empty,
    )
    empty_answer = t064.answer(alice, base_id, query, top_k=top_k)
    assert empty_answer.json["outcome"] == t064.OUTCOME_NO_RESULTS, empty_answer.json

    # Final independent measurement. hit_count is historical, so the invariant is
    # the *delta*: a row that still carries the tombstoned chunk must gain no hit
    # after the first tombstone.
    final_rows = t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
    stale_deleted_rows = [
        row
        for row in final_rows
        if any(item["document_id"] == deleted_document for item in row["items"])
    ]
    assert stale_deleted_rows, "the lost-eviction rows are deliberately still present"
    stale_hit_delta = {
        row["cache_key"]: int(row["hit_count"])
        - hits_before_deletion.get(row["cache_key"], 0)
        for row in stale_deleted_rows
    }
    assert all(delta <= 0 for delta in stale_hit_delta.values()), stale_hit_delta
    # The tombstoned row may keep its *historical* hit from before the delete, but
    # no hit may be newer than the tombstone: last_hit_at is the server's own
    # evidence and both timestamps come from the same database clock.
    stale_last_hits = [
        row["last_hit_at"] for row in stale_deleted_rows if int(row["hit_count"]) > 0
    ]
    assert all(
        value is not None and value <= document_after["deleted_at"]
        for value in stale_last_hits
    ), (stale_last_hits, document_after["deleted_at"])
    # A request that overlapped the final delete may legitimately count a hit from
    # its pre-commit read; the deterministic post-commit evidence is the snapshot
    # above (post-delete read changed no hit count) plus the storm's post-commit
    # answers (empty, hit=false).

    cache_evidence.record(
        "deletion",
        {
            "inputs": {
                "query": query,
                "top_k": top_k,
                "deleted_document_id": deleted_document,
                "kept_document_id": kept_document,
                "deleted_chunk_id": deleted_item["chunk_id"],
                "source_artifact_id": artifact_ids[1],
                "deletion_entry": "DELETE /api/v1/knowledge-documents/{id} (real T069 API)",
            },
            "observed": {
                "warm_key_prefix": old_prefix,
                "post_delete_key_prefix": after_cache["key_prefix"],
                "post_delete_cache": after_cache,
                "citation_status": refused_citation.status,
                "document_status": 404,
                "diagnostics_status": 404,
                "answer_outcome": answer.json["outcome"],
                "poisoned": {
                    "hit": poisoned_cache["hit"],
                    "discarded": poisoned_cache["discarded"],
                    "discard_reason": poisoned_cache["discard_reason"],
                },
                "reindex_status": reindexed.status,
                "replay_job_state": replay_job["state"],
                "replay_failure_class": replay_job["failure_class"],
                "reingest_status": reingested.status,
                "storm": {
                    "total": len(storm),
                    "post_delete": len(post_delete),
                    "post_delete_hits": sum(1 for row in post_delete if row["hit"]),
                    "post_delete_items_nonempty": sum(
                        1 for row in post_delete if row["items"]
                    ),
                    "post_delete_markers": sum(1 for row in post_delete if row["marker"]),
                },
            },
            "cache_rows": {
                "rows_after_warm": len(hits_before),
                "rows_before_final_delete": len(rows_before_final_delete),
                "rows_after_final_delete": len(final_rows),
                "rows_discarded_by_inflight_hit_validation": (
                    len(rows_before_final_delete) - len(final_rows)
                ),
                "empty_key_prefix_has_no_row": (
                    t067.cache_rows(
                        dbname, base_id=base_id, key_prefix=empty_cache["key_prefix"]
                    )
                    == []
                ),
                "after": [t113.row_fact(row) for row in final_rows],
                "stale_deleted_rows_post_delete_hit_delta": stale_hit_delta,
                "post_delete_read_hit_counts_unchanged": hits_after_empty == hits_after_storm,
                "stale_rows_last_hit_not_after_tombstone": all(
                    value is not None and value <= document_after["deleted_at"]
                    for value in stale_last_hits
                ),
                "stale_rows_last_hit_at": stale_last_hits,
                "document_deleted_at": document_after["deleted_at"],
                "rows_gaining_a_hit_from_an_inflight_pre_commit_request": [
                    t113.row_fact(row)
                    for row in final_rows
                    if int(row["hit_count"])
                    > hits_before_final_delete.get(row["cache_key"], 0)
                ],
            },
            "independent_truth": {
                "document_row_after": document_after,
                "deletion_job": {
                    "state": job["state"],
                    "remaining_scopes": t069.remaining_scopes(job),
                },
                "chunk_rows_still_present": len(t055.chunk_rows(dbname, deleted_document)),
                "stale_rows_referencing_deleted_chunk": [
                    {
                        "key_prefix": row["cache_key"][:16],
                        "hit_count": int(row["hit_count"]),
                    }
                    for row in stale_deleted_rows
                ],
            },
        },
    )


# ---------------------------------------------------------------------------
# AC3/AC4 revocation: unauthorized hits are zero; answers are never cached
# ---------------------------------------------------------------------------
def test_t113_revocation_and_cross_principal_hits_are_zero(
    t055_stack, api_server, tmp_path: Path, cache_evidence: t113.Evidence
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    admin = f00b.admin_session(stack)
    alice, alice_base, _alice_artifacts, alice_documents = t067.setup_kb_with_documents(
        stack,
        "t113-alice",
        login="t113.alice",
        contents=[("# 销售资料\n\n" + t113.ALICE_MARKER + "\n").encode("utf-8")],
    )
    bob, bob_base, _bob_artifacts, bob_documents = t067.setup_kb_with_documents(
        stack,
        "t113-bob",
        login="t113.bob",
        contents=[("# 销售资料\n\n" + t113.BOB_MARKER + "\n").encode("utf-8")],
    )
    query, top_k = t113.QUERIES[0], 5
    alice_warm = t067.warm_cache(alice, alice_base, query, top_k=top_k)
    bob_warm = t067.warm_cache(bob, bob_base, query, top_k=top_k)
    alice_prefix = alice_warm.json["retrieval"]["cache"]["key_prefix"]
    bob_prefix = bob_warm.json["retrieval"]["cache"]["key_prefix"]
    assert alice_prefix != bob_prefix
    alice_row = t067.cache_row_with_prefix(dbname, alice_prefix)
    bob_row = t067.cache_row_with_prefix(dbname, bob_prefix)
    assert alice_row["cache_key"] != bob_row["cache_key"]
    assert any(t113.ALICE_MARKER in item["text"] for item in alice_row["items"])
    assert all(t113.BOB_MARKER not in item["text"] for item in alice_row["items"])

    # Both owners repeat their own query: each hits only their own row.
    alice_hit = t055.retrieval_query(alice, alice_base, query, top_k=top_k)
    bob_hit = t055.retrieval_query(bob, bob_base, query, top_k=top_k)
    assert alice_hit.json["retrieval"]["cache"]["hit"] is True
    assert bob_hit.json["retrieval"]["cache"]["hit"] is True
    alice_hits = t113.hit_counts(t067.cache_rows(dbname, base_id=alice_base))
    bob_hits = t113.hit_counts(t067.cache_rows(dbname, base_id=bob_base))
    assert alice_hits[alice_row["cache_key"]] == 1
    assert bob_hits[bob_row["cache_key"]] == 1
    assert t113.ALICE_MARKER not in bob_hit.text
    assert t113.BOB_MARKER not in alice_hit.text

    # A different principal cannot read Alice's base or citation at all (the
    # contract's single 404, no body), even though Alice's live row exists.
    refused_base = t055.retrieval_query(bob, alice_base, query, top_k=top_k)
    st.assert_error(refused_base, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    assert "items" not in refused_base.json and "retrieval" not in refused_base.json
    assert t113.ALICE_MARKER not in refused_base.text
    alice_item = next(
        item for item in alice_warm.json["items"] if t113.ALICE_MARKER in item["text"]
    )
    refused_citation = t055.get_citation(bob, alice_item["chunk_id"])
    st.assert_error(refused_citation, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    assert "text" not in refused_citation.json
    assert t113.ALICE_MARKER not in refused_citation.text
    st.assert_error(
        t055.retrieval_query(alice, bob_base, query, top_k=top_k),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )

    # The hardest adversary: Alice's real stored item inside Bob's live row under
    # Bob's current key. Only the per-chunk owner/KB re-check can discard it.
    keep = [t067.stored_item(dbname, item) for item in bob_warm.json["items"]]
    stored_alice = t067.stored_item(dbname, alice_item)
    bob_key, poisoned_items = t067.poison_live_entry(
        dbname,
        key_prefix=bob_prefix,
        stale_item=stored_alice,
        keep_items=keep,
    )
    poisoned = t055.retrieval_query(bob, bob_base, query, top_k=top_k)
    poisoned_cache = poisoned.json["retrieval"]["cache"]
    assert poisoned_cache["hit"] is False, poisoned_cache
    assert poisoned_cache["discarded"] is True, poisoned_cache
    assert poisoned_cache["discard_reason"] == "item_unreadable", poisoned_cache
    assert t113.ALICE_MARKER not in poisoned.text
    assert all(t113.ALICE_MARKER not in item["text"] for item in poisoned.json["items"])
    assert any(t113.BOB_MARKER in item["text"] for item in poisoned.json["items"])
    fresh_bob = t067.cache_row_by_key(dbname, bob_key)
    assert fresh_bob is not None
    assert all(t113.ALICE_MARKER not in item["text"] for item in fresh_bob["items"])
    assert poisoned_items  # the injection really happened

    # The grounded answer is regenerated on every request; it is never stored, so
    # it cannot be replayed across users or points in time.
    alice_answer = t064.answer(alice, alice_base, query, top_k=top_k)
    assert alice_answer.status == 200, alice_answer.text
    assert alice_answer.json["outcome"] == t064.OUTCOME_ANSWERED
    assert alice_answer.json["retrieval"]["cache"]["hit"] is True, alice_answer.json["retrieval"]
    assert t113.ALICE_MARKER in alice_answer.text
    assert t113.BOB_MARKER not in alice_answer.text
    bob_answer = t064.answer(bob, bob_base, query, top_k=top_k)
    assert bob_answer.json["outcome"] == t064.OUTCOME_ANSWERED
    assert t113.BOB_MARKER in bob_answer.text
    assert t113.ALICE_MARKER not in bob_answer.text
    # A second real API with the documented dev-stub `wrong-answer` fault shares
    # the database and the warm retrieval row: the retrieval cache really hits,
    # yet the answer text is a fresh generation (the fault sentinel), proving the
    # answer itself is not cached.
    with t067.server_with_env(
        api_server, stack, tmp_path / "t113-answer-fault", faults="wrong-answer"
    ) as fault_server:
        faulted = t064.login_on(fault_server, "t113.alice", f00b.USER_PASSWORD)
        faulted_answer = t064.answer(faulted, alice_base, query, top_k=top_k)
    assert faulted_answer.status == 200, faulted_answer.text
    assert faulted_answer.json["outcome"] == t064.OUTCOME_ANSWERED
    assert faulted_answer.json["retrieval"]["cache"]["hit"] is True, faulted_answer.json["retrieval"]
    assert faulted_answer.text.count(t113.WRONG_ANSWER_SENTINEL) == 1
    assert faulted_answer.json["answer"]["citations"], faulted_answer.json
    cache_rows_json = json.dumps(
        t067.cache_rows(dbname, base_id=alice_base), ensure_ascii=False
    )
    assert t113.WRONG_ANSWER_SENTINEL not in cache_rows_json
    for row in t067.cache_rows(dbname, base_id=alice_base):
        for item in row["items"]:
            assert set(item) == t113.STORED_ITEM_FIELDS, item

    # Admin credential revocation: A is disabled; the live cache row must not be
    # served, and its hit count must not move. B (a different principal) keeps
    # working, so the revocation is scoped to A.
    alice_row_before_revoke = t067.cache_row_by_key(dbname, alice_row["cache_key"])
    alice_hits_before_revoke = int(alice_row_before_revoke["hit_count"])
    disabled = t113.disable_principal(admin, alice.principal_id)
    assert disabled.status == 200, disabled.text
    assert disabled.json == {"id": alice.principal_id, "state": "disabled"}, disabled.json
    revoked = t055.retrieval_query(alice, alice_base, query, top_k=top_k)
    st.assert_error(revoked, 401, "session_revoked")
    assert "items" not in revoked.json and "retrieval" not in revoked.json
    assert t113.ALICE_MARKER not in revoked.text
    st.assert_error(t064.answer(alice, alice_base, query, top_k=top_k), 401, "session_revoked")
    alice_row_after_revoke = t067.cache_row_by_key(dbname, alice_row["cache_key"])
    assert alice_row_after_revoke is not None
    assert int(alice_row_after_revoke["hit_count"]) == alice_hits_before_revoke, (
        alice_row_after_revoke
    )
    bob_after = t055.retrieval_query(bob, bob_base, query, top_k=top_k)
    assert bob_after.status == 200 and bob_after.json["retrieval"]["cache"]["hit"] is True

    cache_evidence.record(
        "revocation",
        {
            "entries": {
                "cross_principal": "POST /knowledge-bases/{id}/retrieval-queries as another real user",
                "credential": "PATCH /api/v1/admin/principals/{id} {disabled:true} (admin session)",
                "content": "document tombstone: DELETE /api/v1/knowledge-documents/{id} (deletion section)",
                "per_resource_grant": (
                    "not available in this build: policy.ResourceTypes is {application}; a "
                    "knowledge-base/document grant/revoke entry does not exist (recorded as "
                    "not_applicable with that code fact)"
                ),
            },
            "observed": {
                "alice_row_prefix": alice_prefix,
                "bob_row_prefix": bob_prefix,
                "alice_row_hit_count_before_answer": alice_hits[alice_row["cache_key"]],
                "bob_row_hit_count_before_poison": bob_hits[bob_row["cache_key"]],
                "cross_principal_base_status": refused_base.status,
                "cross_principal_citation_status": refused_citation.status,
                "poisoned_bob_row": {
                    "hit": poisoned_cache["hit"],
                    "discarded": poisoned_cache["discarded"],
                    "discard_reason": poisoned_cache["discard_reason"],
                    "alice_marker_returned": t113.ALICE_MARKER in poisoned.text,
                },
                "answers": {
                    "alice_hit": alice_answer.json["retrieval"]["cache"]["hit"],
                    "alice_contains_bob_marker": t113.BOB_MARKER in alice_answer.text,
                    "bob_contains_alice_marker": t113.ALICE_MARKER in bob_answer.text,
                    "faulted_answer_cache_hit": faulted_answer.json["retrieval"]["cache"]["hit"],
                    "faulted_answer_is_fresh_generation": (
                        t113.WRONG_ANSWER_SENTINEL in faulted_answer.text
                    ),
                    "answer_text_stored_in_cache": False,
                },
                "credential_revocation": {
                    "disable_status": disabled.status,
                    "post_revoke_retrieval_status": revoked.status,
                    "post_revoke_retrieval_error": revoked.json["error"]["code"],
                    "post_revoke_answer_status": 401,
                    "alice_row_hit_count_before": alice_hits_before_revoke,
                    "alice_row_hit_count_after": int(alice_row_after_revoke["hit_count"]),
                    "bob_still_hits": True,
                },
            },
            "independent_truth": {
                "alice_document": t055.document_row(dbname, alice_documents[0]),
                "bob_document": t055.document_row(dbname, bob_documents[0]),
                "alice_cache_row_initial": t113.row_fact(alice_row),
                "alice_cache_row_at_revocation": t113.row_fact(alice_row_before_revoke),
                "alice_cache_row_after_revocation": t113.row_fact(alice_row_after_revoke),
                "bob_cache_row_after_discard": t113.row_fact(fresh_bob),
            },
            "not_applicable": [
                "per-knowledge-base grant revoke: the F01 grant vocabulary is {application} only"
            ],
        },
    )


# ---------------------------------------------------------------------------
# AC4: unstable sources, empty/partial/oversized results and answer provenance
# ---------------------------------------------------------------------------
def test_t113_unstable_sources_and_partial_results_are_not_served(
    t055_stack, tmp_path: Path, cache_evidence: t113.Evidence
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    login = "t113.negative"
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "t113-negative", login=login, contents=[t055.SAMPLE_DOCUMENT]
    )
    query, top_k = t113.QUERIES[2], 8
    warm = t067.warm_cache(alice, base_id, query, top_k=top_k)
    old_prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    old_row = t067.cache_row_with_prefix(dbname, old_prefix)
    assert int(old_row["source_document_count"]) == 1
    hit = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert hit.json["retrieval"]["cache"]["hit"] is True
    hits_before_source_change = t113.hit_counts(
        t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
    )

    # (1) A new document changes the source revision digest: the old key misses.
    source_log = tmp_path / "maintenance-t113-negative.log"
    added_artifact = t055.prepare_source(stack, alice, t113.DOC_B)
    with t055.ingest_pools(stack, source_log):
        accepted = t055.ingest_document(alice, base_id, added_artifact)
        assert accepted.status == 202, accepted.text
        added_document = accepted.json["id"]
        view = t055.wait_for_document_state(alice, added_document, {"ready", "failed"})
        assert view["state"] == "ready", view
    after_add = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    after_add_cache = after_add.json["retrieval"]["cache"]
    assert after_add_cache["hit"] is False, after_add_cache
    assert after_add_cache["key_prefix"] != old_prefix, after_add_cache
    assert after_add_cache["stored"] is True, after_add_cache
    new_row = t067.cache_row_with_prefix(dbname, after_add_cache["key_prefix"])
    assert int(new_row["source_document_count"]) == 2
    assert int(t067.cache_row_by_key(dbname, old_row["cache_key"])["hit_count"]) == (
        hits_before_source_change[old_row["cache_key"]]
    )
    assert after_add.json["items"]

    # (2) Re-uploading the same bytes as a new document creates a new document id
    # and source digest: the previous key still does not hit.
    previous_prefix = after_add_cache["key_prefix"]
    previous_hits = t113.hit_counts(t067.cache_rows(dbname, base_id=base_id))
    reuploaded_artifact = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT)
    with t055.ingest_pools(stack, source_log):
        accepted = t055.ingest_document(alice, base_id, reuploaded_artifact)
        assert accepted.status == 202, accepted.text
        reuploaded_document = accepted.json["id"]
        view = t055.wait_for_document_state(alice, reuploaded_document, {"ready", "failed"})
        assert view["state"] == "ready", view
    after_reupload = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    after_reupload_cache = after_reupload.json["retrieval"]["cache"]
    assert after_reupload_cache["hit"] is False, after_reupload_cache
    assert after_reupload_cache["key_prefix"] != previous_prefix, after_reupload_cache
    newest_row = t067.cache_row_with_prefix(dbname, after_reupload_cache["key_prefix"])
    assert int(newest_row["source_document_count"]) == 3
    previous_row = t067.cache_row_by_key(dbname, new_row["cache_key"])
    assert int(previous_row["hit_count"]) == previous_hits[new_row["cache_key"]]
    assert t055.documents_for_artifact(dbname, reuploaded_artifact)[0]["id"] == reuploaded_document
    assert t055.documents_for_artifact(dbname, added_artifact)[0]["id"] == added_document

    # (3) An empty result is never memoized.
    empty_base = t055.create_knowledge_base(alice, "t113 empty base").json["id"]
    empty_first = t055.retrieval_query(alice, empty_base, query, top_k=top_k)
    empty_second = t055.retrieval_query(alice, empty_base, query, top_k=top_k)
    for response in (empty_first, empty_second):
        assert response.status == 200
        assert response.json["items"] == []
        cache = response.json["retrieval"]["cache"]
        assert cache["enabled"] is True and cache["hit"] is False, cache
        assert cache["stored"] is False, cache
    assert t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=empty_base) == []

    # (4) A partial result (one candidate's sealed body unreadable) is not stored.
    partial_base = t055.create_knowledge_base(alice, "t113 partial base").json["id"]
    partial_source = t055.prepare_source(stack, alice, t055.SAMPLE_DOCUMENT)
    with t055.ingest_pools(stack, source_log):
        accepted = t055.ingest_document(alice, partial_base, partial_source)
        assert accepted.status == 202, accepted.text
        partial_document = accepted.json["id"]
        view = t055.wait_for_document_state(alice, partial_document, {"ready", "failed"})
        assert view["state"] == "ready", view
    chunk_rows = t055.chunk_rows(dbname, partial_document)
    assert len(chunk_rows) >= 2, chunk_rows
    unreadable = chunk_rows[0]
    body_artifact = t055.artifact_row(dbname, unreadable["body_artifact_id"])
    body_bytes = t113.remove_object(stack, body_artifact["object_key"])
    try:
        partial = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
        partial_cache = partial.json["retrieval"]["cache"]
        assert partial.status == 200, partial.text
        assert partial_cache["hit"] is False, partial_cache
        assert partial_cache["stored"] is False, partial_cache
        assert unreadable["id"] not in {
            item["chunk_id"] for item in partial.json["items"]
        }, partial.json["items"]
        assert t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=partial_base) == []
        partial_repeat = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
        assert partial_repeat.json["retrieval"]["cache"]["hit"] is False
    finally:
        t113.restore_object(stack, body_artifact["object_key"], body_bytes)
    recovered = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
    recovered_cache = recovered.json["retrieval"]["cache"]
    assert recovered_cache["stored"] is True, recovered_cache
    recovered_hit = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
    assert recovered_hit.json["retrieval"]["cache"]["hit"] is True

    # (5) A previously stored entry whose sealed body becomes unreadable must be
    # discarded, not served: the hit has to answer exactly like the authoritative
    # path, which would skip that candidate.
    stored_row = t067.cache_row_with_prefix(dbname, recovered_cache["key_prefix"])
    stored_key = stored_row["cache_key"]
    body_artifact = t055.artifact_row(dbname, unreadable["body_artifact_id"])
    body_bytes = t113.remove_object(stack, body_artifact["object_key"])
    try:
        broken = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
        broken_cache = broken.json["retrieval"]["cache"]
        assert broken_cache["hit"] is False, broken_cache
        assert broken_cache["discarded"] is True, broken_cache
        assert broken_cache["discard_reason"] == "item_unreadable", broken_cache
        assert unreadable["id"] not in {item["chunk_id"] for item in broken.json["items"]}
        assert t067.cache_row_by_key(dbname, stored_key) is None, (
            "a hit whose sealed body is unreadable must discard the entry"
        )
        entry_deleted_after_discard = t067.cache_row_by_key(dbname, stored_key) is None
        refused = t055.get_citation(alice, unreadable["id"])
        st.assert_error(refused, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    finally:
        t113.restore_object(stack, body_artifact["object_key"], body_bytes)
    healed = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]
    healed_hit = t055.retrieval_query(alice, partial_base, query, top_k=top_k)
    assert healed_hit.json["retrieval"]["cache"]["hit"] is True

    # (6) An over-bound entry is refused. The fixed corpus is the maximum-size
    # shape reachable through the public API: 50 chunks at the 1200-rune chunk
    # bound with 4-byte runes, so the measured body is near the 256 KiB policy
    # bound. If this corpus cannot cross the bound, the case is recorded not_run
    # (the same policy is covered by the Go unit test) instead of being faked.
    oversized = t113.oversized_document()
    oversized_base = t055.create_knowledge_base(alice, "t113 oversized base").json["id"]
    oversized_source = t055.prepare_source(stack, alice, oversized)
    with t055.ingest_pools(stack, source_log):
        accepted = t055.ingest_document(alice, oversized_base, oversized_source)
        assert accepted.status == 202, accepted.text
        oversized_document = accepted.json["id"]
        view = t055.wait_for_document_state(alice, oversized_document, {"ready", "failed"})
        assert view["state"] == "ready", view
    oversized_rows = t055.chunk_rows(dbname, oversized_document)
    assert len(oversized_rows) == t113.OVERSIZED_CHUNKS, len(oversized_rows)
    oversized_response = t055.retrieval_query(
        alice, oversized_base, query, top_k=t113.OVERSIZED_TOP_K
    )
    assert oversized_response.status == 200, oversized_response.text
    oversized_items = oversized_response.json["items"]
    assert len(oversized_items) == t113.OVERSIZED_CHUNKS, len(oversized_items)
    citation_text = {row["id"]: row["citation"] for row in oversized_rows}
    measured_body_bytes = sum(
        len(item["text"].encode("utf-8")) + len(citation_text[item["chunk_id"]])
        for item in oversized_items
    )
    oversized_cache = oversized_response.json["retrieval"]["cache"]
    oversized_rows_in_db = t067.cache_rows(
        dbname, principal_id=alice.principal_id, base_id=oversized_base
    )
    if measured_body_bytes > t113.CACHE_MAX_ENTRY_BYTES:
        assert oversized_cache["stored"] is False, oversized_cache
        assert oversized_cache["hit"] is False, oversized_cache
        assert oversized_rows_in_db == []
        oversized_verdict = "proven"
    else:  # pragma: no cover - the corpus is sized to cross the bound
        oversized_verdict = "not_run"
        cache_evidence.mark_not_run(
            "oversized_entry_real_api",
            reason=(
                f"the public-API maximum body measured {measured_body_bytes} bytes, below the "
                f"policy bound {t113.CACHE_MAX_ENTRY_BYTES}; the Go unit test covers the refusal"
            ),
        )

    cache_evidence.record(
        "negative_cases",
        {
            "product_gap_found_and_fixed": (
                "the hit-time re-check compared the stored body sha256 with the artifact row only; it "
                "did not read the sealed object. A missing/replaced object was therefore served from "
                "the memoized text while the authoritative path skipped that candidate. The fix "
                "re-reads each sealed body on a hit and requires the exact stored text and sha256 "
                "(internal/knowledge/cache_store.go:cachedBodyMatches)."
            ),
            "observed": {
                "source_change": {
                    "warm_prefix": old_prefix,
                    "after_add_prefix": after_add_cache["key_prefix"],
                    "after_reupload_prefix": after_reupload_cache["key_prefix"],
                    "source_document_counts": [
                        int(old_row["source_document_count"]),
                        int(new_row["source_document_count"]),
                        int(newest_row["source_document_count"]),
                    ],
                    "old_keys_hit_after_change": False,
                },
                "empty_result": {
                    "first": empty_first.json["retrieval"]["cache"],
                    "second": empty_second.json["retrieval"]["cache"],
                    "cache_rows": 0,
                },
                "partial_result": {
                    "skipped_chunk_id": unreadable["id"],
                    "stored": partial_cache["stored"],
                    "hit": partial_cache["hit"],
                    "cache_rows": 0,
                    "recovered_stored": recovered_cache["stored"],
                    "recovered_hit": recovered_hit.json["retrieval"]["cache"]["hit"],
                },
                "unreadable_sealed_body_on_hit": {
                    "old_key_prefix": stored_key[:16],
                    "hit": broken_cache["hit"],
                    "discarded": broken_cache["discarded"],
                    "discard_reason": broken_cache["discard_reason"],
                    "entry_deleted": entry_deleted_after_discard,
                    "healed_stored": healed.json["retrieval"]["cache"]["stored"],
                    "healed_hit": healed_hit.json["retrieval"]["cache"]["hit"],
                },
                "oversized_entry": {
                    "corpus_chunks": len(oversized_rows),
                    "top_k": t113.OVERSIZED_TOP_K,
                    "measured_body_bytes": measured_body_bytes,
                    "policy_bound_bytes": t113.CACHE_MAX_ENTRY_BYTES,
                    "stored": oversized_cache["stored"],
                    "hit": oversized_cache["hit"],
                    "cache_rows": len(oversized_rows_in_db),
                    "verdict": oversized_verdict,
                },
            },
            "independent_truth": {
                "documents_for_reuploaded_artifact": t055.documents_for_artifact(
                    dbname, reuploaded_artifact
                ),
                "partial_document_chunks": len(chunk_rows),
                "partial_cache_rows_total": len(
                    t067.cache_rows(dbname, principal_id=alice.principal_id)
                ),
            },
        },
    )


# ---------------------------------------------------------------------------
# AC5 boundaries: unknown switch, unavailable backend, recovery
# ---------------------------------------------------------------------------
def test_t113_failure_and_recovery_boundaries(
    t055_stack, api_server, tmp_path: Path, cache_evidence: t113.Evidence
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    login = "t113.boundary"
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "t113-boundary", login=login, contents=[t055.SAMPLE_DOCUMENT]
    )
    query, top_k = t113.QUERIES[3], 5
    warm = t067.warm_cache(alice, base_id, query, top_k=top_k)
    warm_prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    first_hit = t055.retrieval_query(alice, base_id, query, top_k=top_k)
    assert first_hit.json["retrieval"]["cache"]["hit"] is True
    warm_key = t067.cache_row_with_prefix(dbname, warm_prefix)["cache_key"]
    hits_before_outage = int(t067.cache_row_by_key(dbname, warm_key)["hit_count"])

    # (1) The deployment switch is fail-closed: an unknown value refuses startup,
    # in a real process, before any request is served.
    env = st.admin_env(stack.dsn)
    env["ORCHARA_HTTP_ADDR"] = f"127.0.0.1:{st.free_port()}"
    env["ORCHARA_WEB_DIR"] = str(st.WEB_DIR)
    env[t113.CACHE_ENV] = "maybe"
    refused_start = subprocess.run(
        [str(stack.binaries["api"])],
        cwd=st.PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert refused_start.returncode != 0, refused_start.stdout
    refused_output = refused_start.stdout + refused_start.stderr
    assert t113.CACHE_ENV in refused_output, refused_output

    # (2) The cache backend is unavailable (the cache relation is temporarily
    # renamed). A real API process started in that state still answers from the
    # authoritative path: no hit, a reported degradation reason, no failed query.
    t113.cache_table_rename(dbname)
    assert t113.cache_table_available(dbname) is False
    try:
        with t067.server_with_env(
            api_server, stack, tmp_path / "t113-cache-outage"
        ) as outage_server:
            outage = t064.login_on(outage_server, login, f00b.USER_PASSWORD)
            degraded = t055.retrieval_query(outage, base_id, query, top_k=top_k)
            assert degraded.status == 200, degraded.text
            degraded_cache = degraded.json["retrieval"]["cache"]
            assert degraded_cache["enabled"] is True, degraded_cache
            assert degraded_cache["hit"] is False, degraded_cache
            assert degraded_cache["discarded"] is True, degraded_cache
            assert degraded_cache["discard_reason"] == "cache_unavailable", degraded_cache
            assert degraded_cache["stored"] is False, degraded_cache
            assert degraded.json["items"], degraded.json
            assert degraded.json["items"] == warm.json["items"]
            citation = t055.get_citation(outage, degraded.json["items"][0]["chunk_id"])
            assert citation.status == 200, citation.text
    finally:
        t113.cache_table_restore(dbname)
    assert t113.cache_table_available(dbname) is True

    # (3) Recovery: a fresh real process over the restored table can use the
    # surviving valid entry again (it was never served while the backend was
    # unavailable), and a spec without an entry is stored and then hit.
    with t067.server_with_env(
        api_server, stack, tmp_path / "t113-cache-recovery"
    ) as recovery_server:
        recovery = t064.login_on(recovery_server, login, f00b.USER_PASSWORD)
        recovered = t055.retrieval_query(recovery, base_id, query, top_k=top_k)
        assert recovered.status == 200, recovered.text
        recovered_cache = recovered.json["retrieval"]["cache"]
        assert recovered_cache["hit"] is True, recovered_cache
        assert int(recovered_cache["validated_chunks"]) == len(recovered.json["items"])
        recovered_row = t067.cache_row_by_key(dbname, warm_key)
        assert int(recovered_row["hit_count"]) == hits_before_outage + 1, recovered_row
        fresh_top_k = top_k + 1
        fresh = t055.retrieval_query(recovery, base_id, query, top_k=fresh_top_k)
        fresh_cache = fresh.json["retrieval"]["cache"]
        assert fresh_cache["hit"] is False and fresh_cache["stored"] is True, fresh_cache
        fresh_hit = t055.retrieval_query(recovery, base_id, query, top_k=fresh_top_k)
        assert fresh_hit.json["retrieval"]["cache"]["hit"] is True
    # The outage itself never served or corrupted the surviving row; the only hit
    # after the outage is the one recovery legitimately answered.
    recovered_row = t067.cache_row_by_key(dbname, warm_key)
    assert int(recovered_row["hit_count"]) == hits_before_outage + 1, recovered_row

    cache_evidence.record(
        "boundaries",
        {
            "observed": {
                "unknown_switch": {
                    "value": "maybe",
                    "exit_code": refused_start.returncode,
                    "names_the_switch": t113.CACHE_ENV in refused_output,
                },
                "cache_backend_unavailable": {
                    "table_renamed": True,
                    "response_status": degraded.status,
                    "cache": degraded.json["retrieval"]["cache"],
                    "items_from_authoritative_path": degraded.json["items"] == warm.json["items"],
                    "citation_status": citation.status,
                },
                "recovery": {
                    "restored": t113.cache_table_available(dbname),
                    "pre_outage_row_hit_after_recovery": recovered_cache["hit"],
                    "validated_chunks": int(recovered_cache["validated_chunks"]),
                    "old_row_hit_count_before_outage": hits_before_outage,
                    "old_row_hit_count_after_recovery": int(recovered_row["hit_count"]),
                    "new_spec_stored_on_miss": fresh_cache["stored"],
                    "new_spec_hit_after_store": fresh_hit.json["retrieval"]["cache"]["hit"],
                },
                "concurrent_delete": (
                    "covered by the deletion section: a query storm during the final "
                    "DELETE produced zero post-commit hits and zero post-commit items"
                ),
            },
        },
    )
