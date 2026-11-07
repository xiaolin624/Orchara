"""F13B/T109 (GitHub #110): data/permission fault injection for FAULT-09/11/12/14.

Each test drives the **real** F13B stack (a throwaway PostgreSQL 16.4 database
migrated by the compiled ``cmd/admin``, the compiled ``cmd/api`` behind real HTTP
with the real filesystem object store and mTLS listener, the real compiled
``cmd/maintenance`` process and the real ``cmd/supervisor`` claim protocol).
Nothing is mocked:

* FAULT-09 deletes a knowledge document through the real deletion transaction while
  the PostgreSQL retrieval cache is warm, then proves no read path (retrieval,
  citation, answer, detail, diagnostics) can return the deleted body, that a
  poisoned live cache row is discarded by the mandatory hit-time re-check, and that
  reindexing/replaying the ingest job or re-ingesting the deleted source artifact
  cannot resurrect it. Independent truth: the sealed source object bytes + the
  citation span recomputed in the test, ``platform.knowledge_documents/chunks``,
  and the real ``platform.knowledge_retrieval_cache`` rows (hit counts).
* FAULT-11 injects the dev stub's registered fault model ids (429 / 5xx) and a
  mid-stream half response, and reads the real ``platform.operation_attempts``
  ledger: bounded retry, one compatible-fallback success, a stable final error
  code, and no concatenation of half answers. The dev stub is a labelled stub; no
  vendor credentials/endpoint exist on this host.
* FAULT-12 loses every wakeup (the API's in-process notifier has no consumer),
  restarts the real maintenance process, and proves PostgreSQL still claims the
  committed job; then it replays a real SSE stream from PostgreSQL after a
  disconnect. This build has **no Redis adapter**, so the Redis-restart half is
  recorded ``blocked`` and never claimed as a Redis pass.
* FAULT-14 revokes an application read grant over the real admin grant route while
  a real parent Run is live: the next child-capability call (``spawnChild``) is
  refused with a stable code even though the frozen snapshot still pins the child
  version, the next reads are 404, and no new child Run/job is created.

Evidence is written to ``product/reports/f13b/T109-faults.json`` only when
``F13B_WRITE_EVIDENCE=1`` (see ``support/t109.py``); the Redis restart is recorded
in the same document as ``blocked``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from support import f00b, t024, t027, t029, t032, t041, t044, t045, t055, t061, t064, t067
from support import t069, t078, t081, t109
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    f06_stack,
    fresh_database,
    go_binaries,
    migrated_database,
    t055_stack,
)

QUERY = "华北地区十月的销售额是多少"
# A body that must never be readable after its document is deleted. The marker is
# unique so a leak is unambiguous, and the text is long enough to produce chunks.
DELETED_BODY_MARKER = "T109-DELETED-BODY-MUST-NEVER-BE-SERVED"
DOC_B = (
    "# 退款制度\n\n"
    + "退款需在订单完成后 30 天内提交申请，逾期不再受理。"
    + DELETED_BODY_MARKER
    + "\n"
) * 12


@pytest.fixture(scope="session")
def fault_evidence() -> t109.Faults:
    branch, commit = t109.git_branch_and_commit()
    recorder = t109.Faults(branch=branch, commit=commit)
    yield recorder
    recorder.write_if_enabled()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# FAULT-09: delete a knowledge document while the retrieval cache is warm
# ---------------------------------------------------------------------------
def test_fault_09_a_deleted_document_is_never_served_from_the_real_cache(
    t055_stack, tmp_path: Path, fault_evidence: t109.Faults
) -> None:
    stack = t055_stack
    dbname = stack.dbname
    log_path = tmp_path / "maintenance-t109-delete.log"
    alice, base_id, artifact_ids, documents = t067.setup_kb_with_documents(
        stack, "t109-delete", contents=[t055.SAMPLE_DOCUMENT, DOC_B]
    )
    deleted_document = documents[1]
    kept_document = documents[0]
    deleted_source = t055.artifact_row(dbname, artifact_ids[1])
    source_bytes = (stack.object_dir / deleted_source["object_key"]).read_bytes()
    source_sha = _sha256(source_bytes)
    source_text = source_bytes.decode("utf-8")
    assert source_sha == deleted_source["sha256"], (source_sha, deleted_source)
    document_before = t055.document_row(dbname, deleted_document)

    # 1. Warm the real PostgreSQL cache: the query really returns the document's
    # body and stores one cache entry whose items include it.
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=10)
    warm_items = warm.json["items"]
    assert any(item["document_id"] == deleted_document for item in warm_items), warm.text
    deleted_item = next(
        item for item in warm_items if item["document_id"] == deleted_document
    )
    warm_cache = warm.json["retrieval"]["cache"]
    old_row = t067.cache_row_with_prefix(dbname, warm_cache["key_prefix"])
    assert any(item["document_id"] == deleted_document for item in old_row["items"]), old_row
    old_hit_count = int(old_row["hit_count"])
    assert old_hit_count == 0, old_row

    # 2. The citation opens with the real body, and the test recomputes the span
    # from the sealed source bytes (independent truth, not the product's own row).
    cited = t055.get_citation(alice, deleted_item["chunk_id"])
    assert cited.status == 200, cited.text
    assert cited.json["text"] == deleted_item["text"]
    span = deleted_item["span"]
    start, end = int(span["start_char"]), int(span["end_char"])
    recomputed = "".join(list(source_text)[start:end])
    assert recomputed == deleted_item["text"], (recomputed[:80], deleted_item["text"][:80])
    citation = cited.json["citation"]
    assert citation["source_sha256"] == source_sha, citation
    assert citation["document_id"] == deleted_document, citation
    chunks_before = t055.chunk_rows(dbname, deleted_document)
    assert chunks_before, "the ingestion produced no chunks"

    # 3. The real deletion transaction: tombstone in PostgreSQL, a real deletion
    # job, and the source object/file is still physically present (F11 semantics).
    deletion_job = t069.deletion_of(
        t069.delete_resource(alice, t069.RESOURCE_DOCUMENT, deleted_document)
    )
    document_after = t055.document_row(dbname, deleted_document)
    assert document_after["deleted_at"] is not None, document_after
    assert document_after["state"] == "deleted", document_after
    assert int(document_after["revision"]) == int(document_before["revision"]) + 1
    stored_job = t069.deletion_job(dbname, deletion_job["id"])
    assert stored_job is not None and stored_job["state"] == "pending", stored_job
    assert "retrieval_cache" in t069.remaining_scopes(stored_job)
    # The source file and the chunk rows still exist: the tombstone, not physical
    # cleanup, is what must stop every read.
    still_there = t055.artifact_row(dbname, artifact_ids[1])
    assert still_there["deleted_at"] is None and still_there["state"] == "ready"
    assert t055.chunk_rows(dbname, deleted_document), "chunk rows vanished before cleanup"

    # 4. Every read path refuses immediately and carries no text.
    refused_citation = t055.get_citation(alice, deleted_item["chunk_id"])
    assert refused_citation.status == 404, refused_citation.text
    assert refused_citation.json["error"]["code"] == t069.ERROR_RESOURCE_NOT_FOUND
    assert "text" not in refused_citation.json and "citation" not in refused_citation.json
    assert DELETED_BODY_MARKER not in refused_citation.text
    assert t055.get_document(alice, deleted_document).status == 404
    assert t055.document_diagnostics(alice, deleted_document).status == 404

    after = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert after.status == 200, after.text
    after_cache = after.json["retrieval"]["cache"]
    assert after_cache["hit"] is False, after_cache
    assert after_cache["key_prefix"] != warm_cache["key_prefix"], after_cache
    assert all(item["document_id"] != deleted_document for item in after.json["items"])
    assert DELETED_BODY_MARKER not in after.text
    # The stale row is still live (the eviction was lost) and must never hit.
    still_live = t067.cache_row_by_key(dbname, old_row["cache_key"])
    assert still_live is not None, "the old cache row was not preserved for the adversary"
    assert int(still_live["hit_count"]) == old_hit_count, still_live

    # 5. Hardest adversary: inject the deleted item into the entry stored under the
    # *current* key. Only the mandatory per-chunk re-check can stop it.
    keep = [t067.stored_item(dbname, item) for item in after.json["items"]]
    stale_item = next(
        item for item in old_row["items"] if item["document_id"] == deleted_document
    )
    assert DELETED_BODY_MARKER in stale_item["text"], stale_item
    current_row = t067.cache_row_with_prefix(dbname, after_cache["key_prefix"])
    current_key = current_row["cache_key"]
    poisoned_items = [stale_item] + keep
    t067.replace_entry_items(dbname, current_key, poisoned_items)
    assert DELETED_BODY_MARKER in json.dumps(poisoned_items, ensure_ascii=False)
    poisoned = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    poisoned_cache = poisoned.json["retrieval"]["cache"]
    assert poisoned_cache["hit"] is False, poisoned_cache
    assert poisoned_cache["discarded"] is True, poisoned_cache
    assert poisoned_cache["discard_reason"] == "item_unreadable", poisoned_cache
    assert DELETED_BODY_MARKER not in poisoned.text
    assert all(
        item["document_id"] != deleted_document for item in poisoned.json["items"]
    )
    fresh_row = t067.cache_row_by_key(dbname, current_key)
    assert fresh_row is not None
    assert all(
        item["document_id"] != deleted_document for item in fresh_row["items"]
    ), fresh_row

    # 6. Recovery/replay must not resurrect the deleted input.
    reindexed = t061.reindex(alice, deleted_document)
    assert reindexed.status == 404, reindexed.text
    assert reindexed.json["error"]["code"] == t069.ERROR_RESOURCE_NOT_FOUND
    replay = f00b.enqueue_ok(
        stack,
        kind=t109.KIND_DOCUMENT_INGEST,
        target_id=deleted_document,
        owner_id=alice.principal_id,
        dedup_key="t109-replay:" + deleted_document,
        input_artifact_id=artifact_ids[1],
    )
    completed, reports = f00b.maintenance_once(
        stack, "--kinds", t109.KIND_DOCUMENT_INGEST,
        env=f00b.maintenance_env(stack, heartbeat_interval="50ms"),
    )
    assert completed.returncode == 0, completed.stderr
    jobs = t055.document_jobs(dbname, deleted_document)
    replay_job = next(job for job in jobs if job["id"] == replay["id"])
    assert replay_job["state"] == "failed", (replay_job, reports)
    assert replay_job["failure_class"] == "business", replay_job
    unchanged = t055.document_row(dbname, deleted_document)
    assert unchanged["deleted_at"] is not None and unchanged["state"] == "deleted"
    after_replay = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert all(
        item["document_id"] != deleted_document for item in after_replay.json["items"]
    )
    assert DELETED_BODY_MARKER not in after_replay.text

    # Deleting the source file and trying to re-ingest it is refused: a deleted
    # file cannot become a new document (no recovery from the tombstone).
    deleted_source_file = t069.deletion_of(
        t069.delete_resource(alice, t069.RESOURCE_ARTIFACT, artifact_ids[1])
    )
    assert deleted_source_file["resource_id"] == artifact_ids[1]
    reingested = t055.ingest_document(alice, base_id, artifact_ids[1])
    assert reingested.status == 404, reingested.text
    assert reingested.json["error"]["code"] == t069.ERROR_RESOURCE_NOT_FOUND
    documents_for_source = t055.documents_for_artifact(dbname, artifact_ids[1])
    assert [row["id"] for row in documents_for_source] == [deleted_document]

    # 7. Delete the remaining document: the query is now empty and (by the cache's
    # own rule) an empty result is not memoized. The answer path says no results.
    empty_job = t069.deletion_of(
        t069.delete_resource(alice, t069.RESOURCE_DOCUMENT, kept_document)
    )
    assert empty_job["resource_id"] == kept_document
    rows_before_empty = len(
        t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
    )
    empty = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert empty.status == 200, empty.text
    assert empty.json["items"] == [], empty.text
    empty_cache = empty.json["retrieval"]["cache"]
    assert empty_cache["hit"] is False and empty_cache["stored"] is False, empty_cache
    assert (
        len(t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id))
        == rows_before_empty
    ), "an empty result must not be memoized"
    answer = t064.answer(alice, base_id, QUERY)
    assert answer.status == 200, answer.text
    assert answer.json["outcome"] == t064.OUTCOME_NO_RESULTS, answer.json

    # 8. Final stale-hit measurement over the real rows: no cache row that still
    # carries the deleted chunk ever hit, and no live row carries its body.
    final_rows = t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
    stale_rows = [
        row
        for row in final_rows
        if any(
            item["chunk_id"] == deleted_item["chunk_id"] for item in row["items"]
        )
    ]
    assert stale_rows, "the lost-eviction row is deliberately still present"
    assert all(int(row["hit_count"]) == 0 for row in stale_rows), stale_rows
    # A later repeat query may legitimately hit the surviving document's entry; the
    # invariant is that every *hit* row is free of the deleted document and body.
    hit_rows = [row for row in final_rows if int(row["hit_count"]) > 0]
    assert all(
        all(item["document_id"] != deleted_document for item in row["items"])
        for row in hit_rows
    ), hit_rows
    assert all(
        DELETED_BODY_MARKER not in json.dumps(row["items"], ensure_ascii=False)
        for row in hit_rows
    ), hit_rows

    fault_evidence.record(
        "FAULT-09",
        title="delete a knowledge document while the PostgreSQL retrieval cache is warm",
        injection_point=(
            "real `DELETE /api/v1/knowledge-documents/{id}` (the F11 tombstone transaction and "
            "its deletion job) executed after `POST /knowledge-bases/{id}/retrieval-queries` "
            "warmed `platform.knowledge_retrieval_cache`; then a stale item was injected into "
            "the live row under the current cache key (the lost-eviction adversary)"
        ),
        inputs={
            "query": QUERY,
            "knowledge_base_id": base_id,
            "deleted_document_id": deleted_document,
            "kept_document_id": kept_document,
            "source_artifact_id": artifact_ids[1],
            "deleted_chunk_id": deleted_item["chunk_id"],
            "source_object_sha256": source_sha,
            "cache_key_prefix_before": warm_cache["key_prefix"],
            "cache_key_prefix_after": after_cache["key_prefix"],
            "seed": "tests.f10 DOC fixtures (SAMPLE_DOCUMENT + Markdown refund policy); no RNG",
        },
        independent_truth={
            "sealed_source_object_bytes_sha256": source_sha,
            "citation_span_recomputed_from_sealed_source": True,
            "platform.knowledge_documents": document_after,
            "platform.knowledge_chunks_still_present": len(chunks_before),
            "platform.knowledge_retrieval_cache_rows": [
                {
                    "cache_key": row["cache_key"],
                    "hit_count": int(row["hit_count"]),
                    "documents": sorted({item["document_id"] for item in row["items"]}),
                }
                for row in final_rows
            ],
            "stale_rows_referencing_deleted_chunk": [
                {
                    "cache_key": row["cache_key"],
                    "hit_count": int(row["hit_count"]),
                }
                for row in stale_rows
            ],
            "deletion_job": stored_job,
        },
        expected={
            "retrieval_after_delete": "200 with no deleted-document item and no deleted body",
            "cache_after_delete": "hit=false; the stale row stays live but never hits",
            "citation": "404 resource_not_found with no text/citation",
            "reindex_and_replay": "refused/failed without restoring the document",
            "reingest_deleted_source": "404 resource_not_found",
        },
        observed={
            "warm_items": len(warm_items),
            "citation_before_delete": cited.status,
            "citation_after_delete": {
                "status": refused_citation.status,
                "code": refused_citation.json["error"]["code"],
                "has_text": "text" in refused_citation.json,
            },
            "retrieval_after_delete": {
                "status": after.status,
                "items": len(after.json["items"]),
                "deleted_document_items": sum(
                    1
                    for item in after.json["items"]
                    if item["document_id"] == deleted_document
                ),
                "hit": after_cache["hit"],
            },
            "poisoned_entry": {
                "hit": poisoned_cache["hit"],
                "discarded": poisoned_cache["discarded"],
                "discard_reason": poisoned_cache["discard_reason"],
                "deleted_body_present": DELETED_BODY_MARKER in poisoned.text,
            },
            "reindex_after_delete": {
                "status": reindexed.status,
                "code": reindexed.json["error"]["code"],
            },
            "replay_job": replay_job,
            "reingest_deleted_source": {
                "status": reingested.status,
                "code": reingested.json["error"]["code"],
            },
            "empty_retrieval": {
                "items": empty.json["items"],
                "hit": empty_cache["hit"],
                "stored": empty_cache["stored"],
                "rows_after_empty_store": len(
                    t067.cache_rows(dbname, principal_id=alice.principal_id, base_id=base_id)
                ),
            },
            "answer_outcome": answer.json["outcome"],
            "stale_hits": sum(int(row["hit_count"]) for row in stale_rows),
            "cache_hit_rows": [
                {
                    "cache_key": row["cache_key"],
                    "hit_count": int(row["hit_count"]),
                    "documents": sorted({item["document_id"] for item in row["items"]}),
                }
                for row in hit_rows
            ],
            "stale_body_on_hit_rows": all(
                DELETED_BODY_MARKER not in json.dumps(row["items"], ensure_ascii=False)
                for row in hit_rows
            ),
            "deleted_body_marker_in_responses": DELETED_BODY_MARKER
            in (after.text + poisoned.text + empty.text),
        },
        commands=[
            "python -m pytest tests/f13b/test_t109_data_faults.py "
            "-k fault_09 -q",
        ],
        not_run=[
            "physical cleanup scopes (T071/T072) are not driven here; the ticket is "
            "about immediate logical unreadability",
        ],
    )


# ---------------------------------------------------------------------------
# FAULT-11: provider 429 / 5xx / half response over the real capability path
# ---------------------------------------------------------------------------
def _attempt_facts(rows: list[dict]) -> list[dict]:
    return [
        {
            "attempt_no": int(row["attempt_no"]),
            "state": row["state"],
            "error_code": row["error_code"],
            "tokens_in": row["tokens_in"],
            "tokens_out": row["tokens_out"],
            "charged_amount": row["charged_amount"],
            "result_artifact_id": row["result_artifact_id"],
        }
        for row in rows
    ]


def test_fault_11_bounded_retry_fallback_and_no_spliced_half_response(
    f06_stack, fault_evidence: t109.Faults
) -> None:
    stack = f06_stack
    dbname = stack.dbname
    # A deployment knob, not a product rule: this test creates three Runs on one
    # throwaway database and must not fight the default two execution slots.
    t081.set_execution_capacity(dbname, 20)
    owner = t041.owner(stack)
    fallback_profile = t045.seed_profile(
        dbname, owner.creator.principal_id, model_id="stub-model"
    )
    worker = t041.worker_client(stack)

    # -- phase A: the primary is rate limited (429) on every physical attempt ----
    t045.set_profile(
        dbname,
        t041.GOLDEN_MODEL_ID,
        model_id="stub-rate-limited-primary",
        config=t045.compatible_config([fallback_profile]),
    )
    owner, run_rate, lease_rate = t041.claimed_run(stack, run_owner=owner)
    operation_rate = t041.new_uuid()
    rate = t041.invoke(
        worker, lease_rate, operation_id=operation_rate, params=t041.model_params()
    )
    assert rate.status == 200, rate.text
    rate_data = rate.json["data"]
    assert rate_data["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_profile,
        "reason": "rate_limited",
    }, rate_data
    rate_attempts = t041.attempt_rows(dbname, operation_rate)
    assert [int(row["attempt_no"]) for row in rate_attempts] == [1, 2, 3, 4]
    assert [row["state"] for row in rate_attempts] == [
        "failed",
        "failed",
        "failed",
        "succeeded",
    ]
    for failed in rate_attempts[:3]:
        assert failed["error_code"] == "provider_transient", failed
        assert failed["charged_amount"] is None, failed
        assert failed["result_artifact_id"] is None, failed
    assert rate_attempts[3]["tokens_in"] > 0 and rate_attempts[3]["tokens_out"] > 0
    assert rate_attempts[3]["result_artifact_id"] == rate.json["receipt_artifact_id"]
    rate_operation = t045.operation_fallback(dbname, run_rate["id"], operation_rate)
    assert rate_operation["state"] == "succeeded", rate_operation
    assert rate_operation["fallback_profile_version_id"] == fallback_profile, rate_operation
    assert rate_operation["fallback_reason"] == "rate_limited", rate_operation
    rate_ledger = t041.ledger_items(owner.creator, run_rate["id"])
    assert len(rate_ledger) == 1 and rate_ledger[0]["attempts"] == 4, rate_ledger
    assert rate_ledger[0]["fallback_profile_version_id"] == fallback_profile
    rate_receipt = t041.receipt_document(stack, rate.json["receipt_artifact_id"])
    assert rate_receipt["fallback"]["reason"] == "rate_limited", rate_receipt["fallback"]
    assert rate_receipt["text"] == rate_data["text"]

    # -- phase B: the primary streams half an answer and fails, three times -----
    t045.set_profile(
        dbname,
        t041.GOLDEN_MODEL_ID,
        model_id="stub-model",
        config=t045.compatible_config([fallback_profile]),
    )
    owner, run_half, lease_half = t041.claimed_run(stack, run_owner=owner)
    operation_half = t041.new_uuid()
    half = t044.stream(
        worker,
        lease_half,
        operation_id=operation_half,
        params=t041.model_params(
            stub_stream_chunks=4,
            stub_stream_fail_after=2,
            stub_stream_fail_attempts=3,
        ),
    )
    assert half.status == 200, half.text
    frames = t044.parse_sse(half.text)
    generations = t044.generation_ids(frames)
    assert len(generations) == 4, generations
    failed_generations = generations[:3]
    final_generation = generations[3]
    assert [frame["type"] for frame in frames] == [
        "delta",
        "delta",
        "error",
        "delta",
        "delta",
        "error",
        "delta",
        "delta",
        "error",
        "delta",
        "delta",
        "delta",
        "delta",
        "final",
    ], frames
    final_frame = t044.finals(frames)[0]
    final_text = final_frame["data"]["text"]
    final_generation_text = t044.delta_text(frames, final_generation)
    assert final_generation_text == final_text
    assert final_frame["data"]["fallback"] == {
        "primary_profile_version_id": t041.GOLDEN_MODEL_ID,
        "profile_version_id": fallback_profile,
        "reason": "availability",
    }, final_frame["data"]["fallback"]
    generation_facts = []
    for generation in failed_generations:
        partial = t044.delta_text(frames, generation)
        assert partial, f"the failed generation {generation} emitted no evidence"
        assert len(partial) < len(final_text), (partial, final_text)
        generation_facts.append(
            {"generation_id": generation, "delta_chars": len(partial), "is_prefix": final_text.startswith(partial)}
        )
    # No concatenation: the final answer is exactly the last generation's deltas,
    # never the failed half answer plus the retry/fallback answer.
    all_delta_text = "".join(
        t044.delta_text(frames, generation) for generation in generations
    )
    failed_delta_text = "".join(
        t044.delta_text(frames, generation) for generation in failed_generations
    )
    assert all_delta_text == failed_delta_text + final_text, (
        "the stream must contain the three failed halves and then the final answer"
    )
    assert final_text != all_delta_text, "the half answer was spliced into the result"
    half_attempts = t041.attempt_rows(dbname, operation_half)
    assert [int(row["attempt_no"]) for row in half_attempts] == [1, 2, 3, 4]
    assert [row["state"] for row in half_attempts] == [
        "failed",
        "failed",
        "failed",
        "succeeded",
    ]
    assert [row["error_code"] for row in half_attempts[:3]] == [
        "provider_transient",
        "provider_transient",
        "provider_transient",
    ]
    assert half_attempts[3]["result_artifact_id"] == final_frame["receipt_artifact_id"]
    half_operation = t045.operation_fallback(dbname, run_half["id"], operation_half)
    assert half_operation["fallback_profile_version_id"] == fallback_profile
    assert half_operation["fallback_reason"] == "availability"
    assert t044.receipt_artifact_count(dbname, run_half["id"]) == 1
    half_receipt = t041.receipt_document(stack, final_frame["receipt_artifact_id"])
    assert half_receipt["text"] == final_text, "the sealed receipt is not the final answer"

    # -- phase C: the 5xx primary with no compatible fallback ------------------
    t045.set_profile(
        dbname,
        t041.GOLDEN_MODEL_ID,
        model_id="stub-unavailable-primary",
        config=t045.compatible_config([]),
    )
    owner, run_down, lease_down = t041.claimed_run(stack, run_owner=owner)
    operation_down = t041.new_uuid()
    down = t041.invoke(
        worker, lease_down, operation_id=operation_down, params=t041.model_params()
    )
    assert down.status == 503, down.text
    assert down.json["error"]["code"] == "provider_transient", down.text
    down_attempts = t041.attempt_rows(dbname, operation_down)
    assert len(down_attempts) == 3, down_attempts
    assert [row["state"] for row in down_attempts] == ["failed"] * 3
    assert {row["error_code"] for row in down_attempts} == {"provider_transient"}
    assert {row["charged_amount"] for row in down_attempts} == {None}
    down_operation = t045.operation_fallback(dbname, run_down["id"], operation_down)
    assert down_operation["state"] == "failed", down_operation
    assert down_operation["last_error_code"] == "provider_transient", down_operation
    assert down_operation["fallback_profile_version_id"] is None, down_operation
    assert down_operation["fallback_reason"] is None, down_operation
    down_ledger = t041.ledger_items(owner.creator, run_down["id"])
    assert len(down_ledger) == 1 and down_ledger[0]["attempts"] == 3, down_ledger
    assert "fallback_profile_version_id" not in down_ledger[0], down_ledger

    fault_evidence.record(
        "FAULT-11",
        title="provider 429/5xx/half response: bounded retry, one compatible fallback, no splice",
        injection_point=(
            "the documented dev-stub provider registered under the golden profile version: "
            "`model_id` `stub-rate-limited-primary` (429), `stub-unavailable-primary` (5xx) and "
            "the streaming params `stub_stream_fail_after`/`stub_stream_fail_attempts` (a half "
            "answer then a typed transient). The calls are real mTLS capability invocations on "
            "real Runs with real sealed execution manifests; the failure is the stub's, labelled."
        ),
        inputs={
            "golden_profile_version_id": t041.GOLDEN_MODEL_ID,
            "fallback_profile_version_id": fallback_profile,
            "phase_a_model_id": "stub-rate-limited-primary",
            "phase_b_model_id": "stub-model + stub_stream_fail_after=2/fail_attempts=3",
            "phase_c_model_id": "stub-unavailable-primary",
            "seed": "deterministic dev stub (sha256 of canonical params); no RNG",
        },
        independent_truth={
            "platform.operation_attempts.A": _attempt_facts(rate_attempts),
            "platform.operation_attempts.B": _attempt_facts(half_attempts),
            "platform.operation_attempts.C": _attempt_facts(down_attempts),
            "platform.operations.A": rate_operation,
            "platform.operations.B": half_operation,
            "platform.operations.C": down_operation,
            "receipt_artifacts": {
                "A": rate.json["receipt_artifact_id"],
                "B": final_frame["receipt_artifact_id"],
            },
            "receipt_reason_A": rate_receipt["fallback"]["reason"],
            "receipt_reason_B": half_receipt["fallback"]["reason"],
            "receipt_text_B_sha256": _sha256(half_receipt["text"].encode("utf-8")),
            "final_text_sha256": _sha256(final_text.encode("utf-8")),
            "stream_generations": generation_facts,
        },
        expected={
            "bounded_retry": "exactly three physical primary attempts before the fallback",
            "fallback": "one compatible-fallback success per operation; one receipt",
            "half_response": "failed generations emit evidence; the final answer is only the "
            "successful generation, never a concatenation",
            "terminal_failure": "503 provider_transient after exactly three attempts, no fallback",
        },
        observed={
            "phase_a": {
                "response_status": rate.status,
                "fallback": rate_data["fallback"],
                "attempt_states": [row["state"] for row in rate_attempts],
                "attempt_count": len(rate_attempts),
                "charged_amounts": [row["charged_amount"] for row in rate_attempts],
                "ledger_attempts": rate_ledger[0]["attempts"],
            },
            "phase_b": {
                "response_status": half.status,
                "frame_types": [frame["type"] for frame in frames],
                "generations": len(generations),
                "failed_generation_deltas": [fact["delta_chars"] for fact in generation_facts],
                "final_text_chars": len(final_text),
                "final_equals_all_generations": final_text == all_delta_text,
                "final_equals_last_generation": final_generation_text == final_text,
                "attempt_states": [row["state"] for row in half_attempts],
                "fallback": final_frame["data"]["fallback"],
                "receipts": t044.receipt_artifact_count(dbname, run_half["id"]),
            },
            "phase_c": {
                "status": down.status,
                "error_code": down.json["error"]["code"],
                "attempts": len(down_attempts),
                "attempt_error_codes": [row["error_code"] for row in down_attempts],
                "operation": {
                    "state": down_operation["state"],
                    "last_error_code": down_operation["last_error_code"],
                    "fallback_profile_version_id": down_operation["fallback_profile_version_id"],
                },
            },
        },
        commands=[
            "python -m pytest tests/f13b/test_t109_data_faults.py -k fault_11 -q",
        ],
        not_run=[
            "a real vendor endpoint's own 429/5xx body, Retry-After header and request log "
            "(no vendor credential/endpoint exists on this host; the dev stub is labelled)",
            "a real vendor's non-deterministic truncation (the stub truncation is exact and "
            "reproducible)",
        ],
    )


# ---------------------------------------------------------------------------
# FAULT-12: lost wakeup + maintenance restart + SSE replay from PostgreSQL
# ---------------------------------------------------------------------------
def _wait_artifact_ready(stack, artifact_id: str, *, timeout: float = 60.0) -> dict:
    last: dict = {}
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        last = f00b.artifact_row(stack.dbname, artifact_id)
        if last["state"] == "ready":
            return last
        __import__("time").sleep(0.02)
    raise AssertionError(f"artifact {artifact_id} never became ready: {last}")


def test_fault_12_lost_wakeup_and_maintenance_restart_do_not_lose_work(
    f06_stack, tmp_path: Path, fault_evidence: t109.Faults
) -> None:
    stack = f06_stack
    dbname = stack.dbname
    rounds: list[dict] = []

    # -- round 1: the job is committed while no maintenance process exists ------
    job = t024.prepare_model_validate_job(stack)
    content = b'{"t109":"lost-wakeup-round-1"}\n'
    completed = t109.complete_artifact(stack, job.lease, content)
    assert completed.status == 202, completed.text
    artifact_id = completed.json["id"]
    finalize_job = t109.job_by_target(
        dbname, kind=t109.KIND_ARTIFACT_FINALIZE, target_id=artifact_id
    )
    # The API enqueued it and dropped the wakeup: no maintenance process is
    # running, so the durable row is the only truth and it is still claimable.
    assert finalize_job["state"] == "ready", finalize_job
    assert finalize_job["lease_token"] is None, finalize_job
    logs = stack.server.logs()
    assert "wakeup: no notification transport is configured" in logs, logs[-2000:]
    assert "the wakeup was dropped" in logs, logs[-2000:]
    # A short observation window: nothing claims it without a poller.
    __import__("time").sleep(0.5)
    assert f00b.artifact_row(dbname, artifact_id)["state"] == "scanning"
    assert t109.background_jobs(
        dbname, kind=t109.KIND_ARTIFACT_FINALIZE, target_id=artifact_id
    )[0]["state"] == "ready"

    # Start the real maintenance process only now: PostgreSQL polling claims the
    # committed job despite the lost wakeup.
    process, handle = t109.start_maintenance(
        stack,
        kinds=t109.KIND_ARTIFACT_FINALIZE,
        log_path=tmp_path / "t109-finalize-round1.log",
        worker_id="t109-finalize-round1",
    )
    try:
        ready = _wait_artifact_ready(stack, artifact_id)
        finished = t109.wait_job(dbname, finalize_job["id"], {"succeeded"})
    finally:
        exit_fact = t109.stop_process(process, handle)
    assert ready["sha256"] == t024.sha256_hex(content)
    assert int(finished["attempt_count"]) == 1, finished
    rounds.append(
        {
            "round": 1,
            "maintenance_before_enqueue": False,
            "job_state_before_maintenance": finalize_job["state"],
            "notifier_drop_log": True,
            "job_state_after": finished["state"],
            "attempt_count": int(finished["attempt_count"]),
            "artifact_state": ready["state"],
            "process": exit_fact,
        }
    )

    # -- round 2: a real maintenance restart with the wakeup lost in between ----
    job2 = t024.prepare_model_validate_job(stack)
    content2 = b'{"t109":"lost-wakeup-round-2"}\n'
    completed2 = t109.complete_artifact(stack, job2.lease, content2)
    assert completed2.status == 202, completed2.text
    artifact2 = completed2.json["id"]
    finalize2 = t109.job_by_target(
        dbname, kind=t109.KIND_ARTIFACT_FINALIZE, target_id=artifact2
    )
    assert finalize2["state"] == "ready", finalize2
    # The previous process is gone (round 1 exited); this is a restart after the
    # committed enqueue, not a startup scan.
    __import__("time").sleep(0.3)
    assert f00b.artifact_row(dbname, artifact2)["state"] == "scanning"
    process2, handle2 = t109.start_maintenance(
        stack,
        kinds=t109.KIND_ARTIFACT_FINALIZE,
        log_path=tmp_path / "t109-finalize-round2.log",
        worker_id="t109-finalize-round2",
    )
    try:
        ready2 = _wait_artifact_ready(stack, artifact2)
        finished2 = t109.wait_job(dbname, finalize2["id"], {"succeeded"})
    finally:
        exit_fact2 = t109.stop_process(process2, handle2)
    assert ready2["sha256"] == t024.sha256_hex(content2)
    assert int(finished2["attempt_count"]) == 1, finished2
    rounds.append(
        {
            "round": 2,
            "maintenance_before_enqueue": False,
            "job_state_before_maintenance": finalize2["state"],
            "notifier_drop_log": True,
            "job_state_after": finished2["state"],
            "attempt_count": int(finished2["attempt_count"]),
            "artifact_state": ready2["state"],
            "process": exit_fact2,
        }
    )

    # -- SSE: disconnect, submit without any wakeup, reconnect with Last-Event-ID -
    owner = t041.owner(stack)
    owner, run, lease = t041.claimed_run(stack, run_owner=owner)
    run_id = run["id"]
    durable_before = t032.run_events(dbname, run_id)
    assert [int(row["seq"]) for row in durable_before] == [1, 2], durable_before
    first_stream = t032.open_sse(stack, owner.creator, run_id, last_event_id=1)
    try:
        first_frames = t032.read_frames(first_stream, max_frames=1)
    finally:
        first_stream.close()
    assert [frame["id"] for frame in first_frames] == ["2"], first_frames
    # The worker's event is committed to PostgreSQL only; no notifier/consumer is
    # involved in this build (there is no Redis adapter), so the SSE stream must
    # find it by polling.
    submitted = t032.submit_events(
        t041.worker_client(stack),
        run_id,
        lease["attempt_token"],
        [t032.node_event(local_sequence=1, node_id="t109-node", state="running")],
    )
    assert submitted.status == 200, submitted.text
    t032.wait_for_events(dbname, run_id, 3)
    replay = t032.open_sse(stack, owner.creator, run_id, last_event_id=1)
    try:
        replay_frames = t032.read_frames(replay, max_frames=2)
    finally:
        replay.close()
    assert [frame["id"] for frame in replay_frames] == ["2", "3"], replay_frames
    replay_envelopes = [t032.frame_envelope(frame) for frame in replay_frames]
    assert replay_envelopes[1]["type"] == "node.status", replay_envelopes
    assert replay_envelopes[1]["payload"]["node_id"] == "t109-node"
    durable_after = t032.run_events(dbname, run_id)
    assert [int(row["seq"]) for row in durable_after] == [1, 2, 3], durable_after
    assert [row["type"] for row in durable_after] == [
        "run.status",
        "run.status",
        "node.status",
    ]

    blocked_redis = {
        "reason": "this build ships no Redis adapter or server: internal/background/notifier.go "
        "only has NopNotifier and a same-process InProcessNotifier (the API installs it with no "
        "consumer), and T022/T024 record the Redis adapter as not_run",
        "what_was_proved_instead": "the same lost-wakeup property over PostgreSQL polling plus "
        "SSE replay from platform.run_events",
        "unblock_condition": "a Redis client/instance and a subscribe path in a future build",
    }
    fault_evidence.record(
        "FAULT-12",
        title="lost wakeup and maintenance restart: jobs/facts survive; SSE replays from PostgreSQL",
        injection_point=(
            "the product's own notifier: cmd/api installs background.InProcessNotifier with no "
            "consumer, so every wakeup is dropped (logged once). The artifact_finalize job is "
            "committed by the real job-scoped upload/complete route while no maintenance process "
            "exists; the compiled cmd/maintenance process is started only afterwards (round 2 is a "
            "restart). SSE: a real GET /runs/{id}/events is closed and reopened with Last-Event-ID "
            "after a worker mTLS event was committed"
        ),
        inputs={
            "finalize_job_1": finalize_job["id"],
            "finalize_artifact_1": artifact_id,
            "finalize_job_2": finalize2["id"],
            "finalize_artifact_2": artifact2,
            "run_id": run_id,
            "last_event_id": 1,
            "content_sha256": {
                "round1": t024.sha256_hex(content),
                "round2": t024.sha256_hex(content2),
            },
            "seed": "no RNG: fixed artifact bytes; job dedup keys are derived from the fixture artifact",
        },
        independent_truth={
            "platform.background_jobs": [finalize_job, finalize2],
            "platform.artifacts": {
                "round1": {
                    "artifact_id": artifact_id,
                    "sha256": ready["sha256"],
                    "state": ready["state"],
                },
                "round2": {
                    "artifact_id": artifact2,
                    "sha256": ready2["sha256"],
                    "state": ready2["state"],
                },
            },
            "platform.run_events": [
                {
                    "seq": int(row["seq"]),
                    "type": row["type"],
                    "payload": row["payload"],
                }
                for row in durable_after
            ],
            "notifier_drop_log_lines": [
                line
                for line in logs.splitlines()
                if "wakeup" in line and "dropped" in line
            ][-1:],
            "redis_adapter_present": False,
        },
        expected={
            "lost_wakeup": "the committed job is still claimed and succeeds from PostgreSQL polling",
            "maintenance_restart": "a restarted real process claims the job committed while it was down",
            "sse": "the disconnected stream replays seq > Last-Event-ID from PostgreSQL",
        },
        observed={
            "rounds": rounds,
            "job_state_1": finished,
            "job_state_2": finished2,
            "sse": {
                "before_disconnect": [int(row["seq"]) for row in durable_before],
                "first_frames": [frame["id"] for frame in first_frames],
                "replayed_frames": [frame["id"] for frame in replay_frames],
                "reapplied_type": replay_envelopes[1]["type"],
                "durable_after": [int(row["seq"]) for row in durable_after],
            },
        },
        verdict="partial",
        commands=[
            "python -m pytest tests/f13b/test_t109_data_faults.py -k fault_12 -q",
        ],
        blocked={
            "part": "Redis restart / Redis queue loss",
            **blocked_redis,
        },
        not_run=[
            "a real Redis restart: no Redis server/adapter exists in this build",
            "an API process restart while an SSE stream is open (the fixture owns the API process); "
            "the stream is reconnected instead",
        ],
    )


# ---------------------------------------------------------------------------
# FAULT-14: revoke an application grant while a run is live
# ---------------------------------------------------------------------------
def test_fault_14_grant_revoked_mid_run_refuses_the_next_capability_call(
    f06_stack, fault_evidence: t109.Faults
) -> None:
    stack = f06_stack
    dbname = stack.dbname
    admin = f00b.admin_session(stack)

    # Two real human creators: B publishes the child application, A is the run
    # owner who only reaches it through a grant.
    provider = f00b.invite_and_activate(
        stack, admin, "t109.provider", roles=("creator",)
    )
    reader = f00b.invite_and_activate(stack, admin, "t109.reader", roles=("creator",))
    child_version = t078.create_child_version(stack, provider, version_no=1)
    child_application = child_version["application_id"]
    granted = t109.create_grant(
        admin, principal_id=reader.principal_id, resource_id=child_application
    )
    assert granted.status == 201, granted.text
    grant_id = granted.json["id"]
    epoch_after_grant = t109.policy_epoch(dbname)

    # A creates and claims the parent Run while the grant is still live. The
    # parent definition pins the child version into the sealed manifest.
    parent_definition = t078.parent_definition([child_version["id"]])
    parent_application = t027.application_with_definition(
        reader, parent_definition, name="t109 parent"
    )
    submitted = t027.submit_debug(
        reader,
        parent_application,
        draft_revision=t027.draft_revision(reader, parent_application),
        input_value={"source": "t109-parent"},
    )
    assert submitted.status == 202, submitted.text
    parent_run = submitted.json
    lease = t029.execution_client(stack, "supervisor").claim_job(
        worker_id="t109-supervisor", execution_unit_id="t109-unit"
    )
    assert lease.status == 200, lease.text
    lease = lease.json
    assert lease["run_id"] == parent_run["id"], (lease, parent_run)
    run_row = t029.run_state_row(dbname, parent_run["id"])
    assert run_row["state"] == "running", run_row

    snapshot_id = run_row["execution_snapshot_id"]
    manifest_artifact = st.sql_scalar(
        dbname,
        "SELECT manifest_artifact_id::text FROM platform.execution_snapshots "
        f"WHERE id = {st.quote_literal(snapshot_id)}::uuid",
    )
    manifest_artifact_row = t029.artifact_row(dbname, manifest_artifact)
    frozen_manifest = json.loads(
        (stack.object_dir / manifest_artifact_row["object_key"]).read_bytes()
    )
    assert child_version["id"] in frozen_manifest["subapp_versions"], frozen_manifest

    # Before the revoke the child capability really works through the same route.
    first_spawn = t078.spawn(
        stack,
        parent_run["id"],
        attempt_token=lease["attempt_token"],
        logical_key="call-1",
        application_version_id=child_version["id"],
        input={"source": "t109-before-revoke"},
    )
    assert first_spawn.status == 202, first_spawn.text
    first_child = first_spawn.json
    assert first_child["parent_run_id"] == parent_run["id"]
    assert first_child["application_version_id"] == child_version["id"]
    children_before = t078.child_count(dbname, parent_run["id"])
    assert children_before == 1, children_before
    runs_before = t109.run_count(dbname)
    jobs_before = t109.count(dbname, "platform.jobs")
    operations_before = t109.count(dbname, "platform.operations")

    # The live revoke: real admin session + CSRF, exactly the contract route.
    revoked = t109.revoke_grant(admin, grant_id)
    assert revoked.status == 204, revoked.text
    assert revoked.raw == b"", "204 must not carry a body"
    assert t109.grant_rows(dbname, principal_id=reader.principal_id, resource_id=child_application) == []
    assert revoked.header("Cache-Control") == "no-store"
    epoch_after_revoke = t109.policy_epoch(dbname)

    # The next reads are refused with the located not-found code.
    application_read = reader.client.get(f"/api/v1/applications/{child_application}")
    assert application_read.status == 404, application_read.text
    assert application_read.json["error"]["code"] == "resource_not_found"
    runnable = reader.client.get(f"/api/v1/versions/{child_version['id']}/runnable")
    assert runnable.status == 404, runnable.text
    assert runnable.json["error"]["code"] == "resource_not_found"

    # The next capability call (spawnChild) is refused even though the Run's
    # frozen snapshot still names the child version: the snapshot is not a grant.
    second_spawn = t078.spawn(
        stack,
        parent_run["id"],
        attempt_token=lease["attempt_token"],
        logical_key="call-2",
        application_version_id=child_version["id"],
        input={"source": "t109-after-revoke"},
    )
    assert second_spawn.status == 403, second_spawn.text
    assert second_spawn.json["error"]["code"] == "child_not_visible", second_spawn.text
    assert "call-2" not in json.dumps(second_spawn.json), second_spawn.json
    assert t078.run_children_row(dbname, parent_run["id"], "call-1") is not None
    assert t078.run_children_row(dbname, parent_run["id"], "call-2") is None
    assert t078.child_count(dbname, parent_run["id"]) == children_before
    assert t109.run_count(dbname) == runs_before, "the refused call created a Run"
    assert t109.count(dbname, "platform.jobs") == jobs_before, "the refused call created a job"
    assert (
        t109.count(dbname, "platform.operations") == operations_before
    ), "the refused call created a capability operation"

    # The frozen manifest still pins the child version (the snapshot survives),
    # but the grant row is authoritative and gone: already-done writes stay, no
    # new external write appears.
    manifest_after = json.loads(
        (stack.object_dir / manifest_artifact_row["object_key"]).read_bytes()
    )
    assert child_version["id"] in manifest_after["subapp_versions"]
    assert t109.run_count(dbname) >= 2, "the parent and the first child Run remain"
    assert t078.run_row(dbname, first_child["id"])["state"] in {
        "queued",
        "running",
    }, t078.run_row(dbname, first_child["id"])

    fault_evidence.record(
        "FAULT-14",
        title="revoke an application read grant while a Run is live: the next capability call is refused",
        injection_point=(
            "real `DELETE /api/v1/admin/grants/{id}` (admin session + CSRF, one policy-epoch "
            "bump) executed after a real parent Run was claimed (`cmd/supervisor` mTLS claim) "
            "and had already spawned one child through `POST /internal/v1/runs/{id}/children`. "
            "The second spawnChild re-resolves the child version under the run owner's *current* "
            "ACL (`catalog.ResolveRunnableVersionTx`) instead of trusting the frozen manifest"
        ),
        inputs={
            "grant_id": grant_id,
            "grantee_principal_id": reader.principal_id,
            "child_application_id": child_application,
            "child_version_id": child_version["id"],
            "parent_application_id": parent_application,
            "parent_run_id": parent_run["id"],
            "frozen_snapshot_id": snapshot_id,
            "frozen_manifest_artifact_id": manifest_artifact,
            "first_child_run_id": first_child["id"],
            "seed": "publisher-shape fixture for the child version (T075 helper); the publication "
            "review transaction is the documented boundary",
        },
        independent_truth={
            "platform.resource_grants_after": t109.grant_rows(
                dbname, principal_id=reader.principal_id, resource_id=child_application
            ),
            "platform.policy_epochs": {
                "after_create_grant": epoch_after_grant,
                "after_revoke": epoch_after_revoke,
                "note": "the real grant/revoke transactions bump the acl epoch; the next "
                "decision re-reads resource_grants instead of a cached allow",
            },
            "platform.runs": t109.run_count(dbname),
            "platform.run_children": [
                {
                    "logical_key": row["parent_invocation_key"],
                    "child_run_id": row["child_run_id"],
                    "parent_run_id": row["parent_run_id"],
                }
                for row in t078.run_children_rows(dbname, parent_run["id"])
            ],
            "platform.jobs": t109.count(dbname, "platform.jobs"),
            "platform.operations": t109.count(dbname, "platform.operations"),
            "frozen_manifest_subapp_versions": frozen_manifest["subapp_versions"],
            "frozen_manifest_sha256_matches_snapshot": manifest_after["subapp_versions"]
            == frozen_manifest["subapp_versions"],
        },
        expected={
            "next_reads": "404 resource_not_found for the child application and runnable version",
            "next_capability": "spawnChild refused with 403 child_not_visible",
            "snapshot": "still pins the child version but grants no permanent right",
            "writes": "the first child Run remains; the refused call creates no Run/job/operation",
        },
        observed={
            "reads": {
                "application": application_read.status,
                "application_code": application_read.json["error"]["code"],
                "runnable": runnable.status,
                "runnable_code": runnable.json["error"]["code"],
            },
            "first_spawn": {
                "status": first_spawn.status,
                "child_run_id": first_child["id"],
                "run_children": children_before,
            },
            "revoke": {
                "status": revoked.status,
                "grants_after": [],
                "epoch_bumped": True,
            },
            "second_spawn": {
                "status": second_spawn.status,
                "code": second_spawn.json["error"]["code"],
                "run_children_after": len(
                    t078.run_children_rows(dbname, parent_run["id"])
                ),
                "child_runs_after": t078.child_count(dbname, parent_run["id"]),
                "runs_after": t109.run_count(dbname),
                "jobs_after": t109.count(dbname, "platform.jobs"),
                "operations_after": t109.count(dbname, "platform.operations"),
            },
            "parent_run_state": t029.run_state_row(dbname, parent_run["id"])["state"],
        },
        commands=[
            "python -m pytest tests/f13b/test_t109_data_faults.py -k fault_14 -q",
        ],
        not_run=[
            "document-level grants: the F01 grant vocabulary is application-only in this build; "
            "document access revocation is exercised as the real document delete in FAULT-09",
            "a real publication review for the child version (the T075 publisher-shape fixture is "
            "used and labelled, as in the existing F07 child-run suites)",
        ],
    )
