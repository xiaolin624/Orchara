"""C3.03 acceptance: knowledge reads stay consistent across rebuild, delete and corruption.

C3.01 migrated ordinary retrieval and C3.02 migrated citation open, answer
re-check and the cache hit onto one owner-bound readable-chunk reader. This
ticket closes the C3 consistency surface *across real combinations*:

* the active index was built from source artifact A, the administrator replaces
  the source with B (the rebuild is queued or has already failed), and then A is
  deleted. A failed rebuild must keep the old active index readable (ADR 0018);
  deleting A must still stop the A-derived chunks immediately, through every read
  path, without a rule that makes an old active index unreadable by itself;
* a successful activation supersedes the old index: its citations stay closed and
  a cache entry warmed before the switch is never served;
* deletion/revocation blocks retrieval, citation open and the cache hit;
* a transient sealed-body read failure keeps the cache entry (recovery re-uses
  it) while deterministic corruption/truncation/size mismatch is a miss and a
  discard on all three paths;
* a citation that becomes invalid while the answer is being generated is never
  returned in a final answer;
* a paired cache-on/cache-off comparison of the same blocked state.

Everything runs on the real stack: PostgreSQL 16.4 migrated by the compiled
``cmd/admin`` chain, the compiled ``cmd/api`` behind real HTTP, the real
filesystem object store with real sealed bytes, the real ``cmd/maintenance``
``document_ingest``/``artifact_finalize`` pools, and the real deletion entry.
Corruption is applied where a storage fault or an inconsistent row would apply
it; the assertions are the public retrieval/citation/answer responses plus the
real cache/artifact/document/index rows. The API is never its own oracle: chunk
provenance, sealed bytes and timestamps are read from PostgreSQL and the object
directory.
"""

from __future__ import annotations

import json
import statistics
import threading

from support import f00b, t061, t064, t067, t069, t113
from support import stack as st
from support import t055
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

QUERY = "华北地区十月的销售额是多少"
# The replacement source: a different document so "B's content is live" is
# distinguishable from "A's content is still served".
REPLACEMENT_DOCUMENT = (
    "# 销售资料（十一月修订 B）\n"
    "\n"
    "华南地区十一月的销售额是 7777.00 元，订单数为 88。\n"
    "\n"
    "退款政策：订单完成后 30 天内可申请退款；十一月的修订版保持这条规则。\n"
)
REPLACEMENT_MARKER = "华南地区十一月的销售额是 7777.00"
# ADR 0015 D6's closed reason vocabulary: no new word may appear.
CACHE_REASONS = {
    "expired",
    "components_changed",
    "item_unreadable",
    "item_mismatch",
    "cache_unavailable",
}

IDENTITY_FIELDS = (
    "chunk_id",
    "document_id",
    "document_revision",
    "chunk_no",
    "span",
    "citation",
    "score",
)

_KEEP = object()


# ---------------------------------------------------------------------------
# setup and shared real-stack helpers
# ---------------------------------------------------------------------------
def setup_ready_document(stack, *, log_name: str, login: str = "c303.alice"):
    """One owner, one KB and one document that became ready through the real jobs."""
    return t061.setup_ready_document(stack, log_name=log_name, login=login)


def login_on(server, login: str = "c303.alice"):
    return t064.login_on(server, login, f00b.USER_PASSWORD)


def item_ids(payload: dict) -> set[str]:
    return {item["chunk_id"] for item in payload["items"]}


def cache_key_of(response, dbname: str) -> str:
    prefix = response.json["retrieval"]["cache"]["key_prefix"]
    return t067.cache_row_with_prefix(dbname, prefix)["cache_key"]


def chunk_body_artifact(stack, chunk_id: str) -> dict:
    chunk = t064.chunk_row(stack.dbname, chunk_id)
    return t055.artifact_row(stack.dbname, chunk["body_artifact_id"])


def sealed_path(stack, artifact: dict):
    return t113.object_path(stack, artifact["object_key"])


def sealed_text(stack, artifact: dict) -> str:
    return sealed_path(stack, artifact).read_bytes().decode("utf-8")


def replace_sealed_bytes(stack, artifact: dict, data: bytes) -> bytes:
    path = sealed_path(stack, artifact)
    original = path.read_bytes()
    path.write_bytes(data)
    assert path.read_bytes() == data
    return original


def restore_sealed_bytes(stack, artifact: dict, data: bytes) -> None:
    sealed_path(stack, artifact).write_bytes(data)


def set_integrity_metadata(
    dbname: str,
    artifact_id: str,
    *,
    sha256: str | None | object = _KEEP,
    size_bytes: int | None | object = _KEEP,
) -> None:
    """Edit the authoritative integrity metadata of one artifact row.

    ``_KEEP`` leaves a column alone; ``None`` writes SQL ``NULL``. This is the
    "missing/invalid integrity metadata" shape a bad ingest or a manual repair
    could leave behind, not a new product fault switch.
    """
    assignments = []
    if sha256 is not _KEEP:
        assignments.append(
            "sha256 = " + ("NULL" if sha256 is None else st.quote_literal(sha256))
        )
    if size_bytes is not _KEEP:
        assignments.append(
            "size_bytes = " + ("NULL" if size_bytes is None else str(int(size_bytes)))
        )
    assert assignments, "at least one integrity column must be edited"
    st.sql(
        dbname,
        f"UPDATE platform.artifacts SET {', '.join(assignments)} "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


def assert_item_is_the_sealed_body(stack, item: dict) -> None:
    """The response text is the authoritative sealed body (independent truth read)."""
    artifact = chunk_body_artifact(stack, item["chunk_id"])
    body = sealed_path(stack, artifact).read_bytes()
    assert item["text"] == body.decode("utf-8")
    assert t055.sha256_hex(body) == artifact["sha256"]
    assert len(body) == int(artifact["size_bytes"])


def assert_safe_not_found(response, artifact: dict) -> None:
    """An unreadable citation is the existing indistinguishable 404."""
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found", response.json
    assert "text" not in response.json, response.json
    assert "citation" not in response.json, response.json
    assert artifact["object_key"] not in response.text, response.text
    assert "sealed/" not in response.text, response.text


def assert_cache_miss(
    payload: dict, *, reason: str, stored: bool = False, discarded: bool = True
) -> None:
    cache = payload["retrieval"]["cache"]
    assert cache["enabled"] is True, cache
    assert cache["hit"] is False, cache
    assert cache["discarded"] is discarded, cache
    if reason:
        assert cache["discard_reason"] == reason, cache
        assert cache["discard_reason"] in CACHE_REASONS, cache
    assert cache["stored"] is stored, cache


def answer_signature(payload: dict) -> dict:
    """The parts of an answer a cache-on/off comparison must agree on exactly."""
    return {
        "outcome": payload["outcome"],
        "text": payload["answer"]["text"],
        "citations": [
            {key: citation[key] for key in IDENTITY_FIELDS}
            for citation in t064.citations_of(payload)
        ],
        "chunk_ids": t064.retrieval_chunk_ids(payload),
    }


def cache_rows(stack, owner, base_id: str) -> list[dict]:
    return t067.cache_rows(
        stack.dbname, principal_id=owner.principal_id, base_id=base_id
    )


# ---------------------------------------------------------------------------
# AC1/AC2: source A active -> replace with B (building) -> delete A
# ---------------------------------------------------------------------------
def test_c303_deleting_the_old_source_while_the_replacement_builds_blocks_all_read_paths(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, source_a, document_id = setup_ready_document(
        stack, log_name="c303-old-source-building"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None and v1["state"] == "ready", v1
    chunks_v1 = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks_v1, chunks_v1
    chunk_ids_v1 = [chunk["id"] for chunk in chunks_v1]
    # Every active chunk names A as the source it was derived from.
    assert {t055.citation_of(chunk)["source_artifact_id"] for chunk in chunks_v1} == {
        source_a
    }
    assert v1["activated_at"] is not None
    assert v1["document_revision"] == 1

    # Warm the revision-1 entry while A is the source. This row must never be
    # served again after the source is replaced (the activation changes the key).
    rev1 = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    rev1_row = t067.cache_row_with_prefix(
        stack.dbname, rev1.json["retrieval"]["cache"]["key_prefix"]
    )
    assert item_ids(rev1.json) <= set(chunk_ids_v1)

    # Replace the source with B through the real reindex entry and keep the
    # rebuild queued: the activation pointer still names v1. That is exactly the
    # documented ADR 0018 state in which the old active index stays readable.
    source_b = t055.prepare_source(stack, owner, REPLACEMENT_DOCUMENT)
    accepted = t061.reindex(owner, document_id, source_artifact_id=source_b)
    assert accepted.status == 202, accepted.text
    index_v2 = t061.index_by_version(stack.dbname, document_id, 2)
    assert index_v2["id"] == accepted.json["index_version_id"], index_v2
    assert index_v2["state"] == "building", index_v2
    row = t055.document_row(stack.dbname, document_id)
    assert row["source_artifact_id"] == source_b, row
    assert int(row["revision"]) == 2, row
    assert int(row["active_revision"]) == 1, row
    assert t061.active_index(stack.dbname, document_id)["id"] == v1["id"]
    assert row["deleted_at"] is None

    # With both sources live the old active index is readable and cacheable.
    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert item_ids(warm.json) == set(chunk_ids_v1), warm.json["items"]
    warm_key = cache_key_of(warm, stack.dbname)
    for item in warm.json["items"]:
        assert_item_is_the_sealed_body(stack, item)
    opened = t055.get_citation(owner, chunk_ids_v1[0])
    assert opened.status == 200, opened.text
    assert opened.json["text"] == sealed_text(
        stack, chunk_body_artifact(stack, chunk_ids_v1[0])
    )
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.json["retrieval"]["cache"]["hit"] is True, hit.json["retrieval"]["cache"]

    # Delete the old source A through the real deletion entry while the current
    # source B is untouched and the rebuild is still queued.
    revision_before_delete = int(t069.artifact_row(stack.dbname, source_a)["revision"])
    job = t069.deletion_of(
        t069.delete_resource(owner, t069.RESOURCE_ARTIFACT, source_a)
    )
    assert job["resource_id"] == source_a
    artifact_a = t069.artifact_row(stack.dbname, source_a)
    assert artifact_a["state"] == "deleted", artifact_a
    assert artifact_a["deleted_at"] is not None, artifact_a
    assert int(artifact_a["revision"]) == revision_before_delete + 1, artifact_a
    stored_job = t069.deletion_job(stack.dbname, job["id"])
    assert stored_job is not None and stored_job["state"] == "pending", stored_job
    # The document is not tombstoned: its current source is B. The refusal must
    # therefore come from the current-fact predicate, not from losing the row.
    live_document = t055.document_row(stack.dbname, document_id)
    assert live_document["deleted_at"] is None, live_document
    # The sealed bytes and their authoritative metadata still exist: this is a
    # read refusal, not a physical purge hiding the leak.
    for chunk in chunks_v1:
        body = t055.artifact_row(stack.dbname, chunk["body_artifact_id"])
        assert body["state"] == "ready" and body["deleted_at"] is None, body
        body_path = sealed_path(stack, body)
        assert body_path.exists()
        data = body_path.read_bytes()
        assert t055.sha256_hex(data) == body["sha256"]
        assert len(data) == int(body["size_bytes"])
    assert sealed_path(stack, artifact_a).exists()

    # (1) Retrieval refuses every A-derived chunk immediately. The deletion did
    # not move the document row, so the same key is derived; the hit re-check is
    # what must refuse and discard the live entry (the lost-eviction adversary).
    retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retrieval.status == 200, retrieval.text
    assert retrieval.json["items"] == [], retrieval.json["items"]
    assert_cache_miss(retrieval.json, reason="item_unreadable")
    assert (
        retrieval.json["retrieval"]["cache"]["key_prefix"]
        == warm.json["retrieval"]["cache"]["key_prefix"]
    ), retrieval.json["retrieval"]["cache"]
    assert t067.cache_row_by_key(stack.dbname, warm_key) is None, (
        "a deterministic source-deletion refusal must discard the cache entry"
    )
    # (2) Citation open refuses each chunk with the safe 404.
    for chunk_id in chunk_ids_v1:
        assert_safe_not_found(
            t055.get_citation(owner, chunk_id), chunk_body_artifact(stack, chunk_id)
        )
    # (3) Answer sees no results and returns no citation of the deleted source.
    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_NO_RESULTS, answered.json
    assert answered.json["answer"]["text"] == ""
    assert answered.json["answer"]["citations"] == []
    # The pre-replacement entry was never addressed, let alone served.
    surviving = t067.cache_row_by_key(stack.dbname, rev1_row["cache_key"])
    assert surviving is not None, "an unreachable old key may stay (eviction is lazy)"
    assert int(surviving["hit_count"]) == 0, surviving
    # No new row was stored for the blocked/empty result.
    assert [row["cache_key"] for row in cache_rows(stack, owner, base_id)] == [
        rev1_row["cache_key"]
    ]

    # (4) Paired cache-on/cache-off comparison: a second real API process with the
    # deployment cache disabled must agree exactly; the refusal is not a
    # cache-side effect (and the off process writes no rows).
    with t067.cache_disabled_server(api_server, stack, tmp_path / "cache-off-c303") as server:
        off = login_on(server)
        off_retrieval = t055.retrieval_query(off, base_id, QUERY, top_k=20)
        assert off_retrieval.status == 200, off_retrieval.text
        assert off_retrieval.json["retrieval"]["cache"]["enabled"] is False
        assert off_retrieval.json["items"] == retrieval.json["items"] == []
        off_open = t055.get_citation(off, chunk_ids_v1[0])
        assert_safe_not_found(off_open, chunk_body_artifact(stack, chunk_ids_v1[0]))
        off_answer = t064.answer(off, base_id, QUERY, top_k=20)
        assert off_answer.status == 200, off_answer.text
        assert answer_signature(off_answer.json) == answer_signature(answered.json)

    # (5) The queued rebuild is still allowed to finish: B is live, so v2
    # activates and the document is readable again — with B's content only.
    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-c303-building.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    v2 = t061.active_index(stack.dbname, document_id)
    assert v2 is not None and v2["id"] == index_v2["id"], v2
    assert v2["state"] == "ready" and v2["activated_at"] is not None, v2
    assert int(v2["document_revision"]) == 2, v2
    v1_after = t061.index_by_version(stack.dbname, document_id, 1)
    assert v1_after["state"] == "superseded", v1_after
    assert v1_after["superseded_at"] is not None, v1_after
    chunks_v2 = t061.chunk_rows_for_index(stack.dbname, v2["id"])
    assert {t055.citation_of(chunk)["source_artifact_id"] for chunk in chunks_v2} == {
        source_b
    }
    # The superseded index's citations stay closed; its cache entry stayed unused.
    for chunk_id in chunk_ids_v1:
        assert t055.get_citation(owner, chunk_id).status == 404
    assert int(t067.cache_row_by_key(stack.dbname, rev1_row["cache_key"])["hit_count"]) == 0
    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert healed.json["items"], healed.json
    assert item_ids(healed.json) <= {chunk["id"] for chunk in chunks_v2}
    assert any(REPLACEMENT_MARKER in item["text"] for item in healed.json["items"])
    assert not any("华北地区十月" in item["text"] for item in healed.json["items"])
    healed_key = cache_key_of(healed, stack.dbname)
    assert healed_key != rev1_row["cache_key"]
    opened_new = t055.get_citation(owner, healed.json["items"][0]["chunk_id"])
    assert opened_new.status == 200, opened_new.text
    answered_new = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered_new.status == 200, answered_new.text
    assert answered_new.json["outcome"] == t064.OUTCOME_ANSWERED, answered_new.json
    assert all(
        citation["chunk_id"] in item_ids(healed.json)
        for citation in t064.citations_of(answered_new.json)
    )
    then_hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert then_hit.json["retrieval"]["cache"]["hit"] is True, then_hit.json["retrieval"][
        "cache"
    ]
    assert then_hit.json["items"] == healed.json["items"]


# ---------------------------------------------------------------------------
# AC1/AC2: source A active -> replace with B (rebuild FAILED) -> delete A
# ---------------------------------------------------------------------------
def test_c303_deleting_the_old_source_after_a_failed_rebuild_blocks_all_read_paths(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, source_a, document_id = setup_ready_document(
        stack, log_name="c303-old-source-failed"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None and v1["state"] == "ready", v1
    chunks_v1 = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks_v1, chunks_v1
    chunk_ids_v1 = [chunk["id"] for chunk in chunks_v1]
    source_b = t055.prepare_source(stack, owner, REPLACEMENT_DOCUMENT)

    # The real-provider deployment fails closed in this build (no adapter or
    # credential): the B rebuild really runs and really fails. The completion
    # guard leaves the old activation pointer alone (ADR 0018).
    with t061.server_with_env(
        api_server, stack, tmp_path / "c303-real-provider", extra=t061.REAL_PROVIDER_ENV
    ) as (server_real, certs_real):
        real = t064.login_on(server_real, "c303.alice", f00b.USER_PASSWORD)
        with t061.maintenance_pools(
            stack,
            certs_real,
            kinds=t061.KIND_DOCUMENT_INGEST + "," + t061.KIND_ARTIFACT_FINALIZE,
            log_path=tmp_path / "maintenance-c303-failed.log",
            extra_env=t061.REAL_PROVIDER_ENV,
        ):
            accepted = t061.reindex(real, document_id, source_artifact_id=source_b)
            assert accepted.status == 202, accepted.text
            failed = t055.wait_for_document_state(real, document_id, {"failed"})
            assert failed["state"] == "failed", failed

    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "failed", row
    assert row["error_code"] == "embedding_provider_not_configured", row
    assert row["source_artifact_id"] == source_b, row
    assert int(row["revision"]) == 2, row
    assert int(row["active_revision"]) == 1, row
    assert row["deleted_at"] is None, row
    v2 = t061.index_by_version(stack.dbname, document_id, 2)
    assert v2["state"] == "failed", v2
    assert v2["error_code"] == "embedding_provider_not_configured", v2
    assert v2["activated_at"] is None and v2["superseded_at"] is None, v2
    v1_after = t061.index_by_version(stack.dbname, document_id, 1)
    assert v1_after["state"] == "ready", v1_after
    assert v1_after["superseded_at"] is None, v1_after

    # AC2: a failed rebuild keeps the old active index readable through all three
    # read paths and the answer, with A still live as a historical source.
    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert item_ids(warm.json) == set(chunk_ids_v1), warm.json["items"]
    warm_key = cache_key_of(warm, stack.dbname)
    for item in warm.json["items"]:
        assert_item_is_the_sealed_body(stack, item)
    opened_old = t055.get_citation(owner, chunk_ids_v1[0])
    assert opened_old.status == 200, opened_old.text
    answered_old = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered_old.status == 200, answered_old.text
    assert answered_old.json["outcome"] == t064.OUTCOME_ANSWERED, answered_old.json
    assert all(
        citation["chunk_id"] in set(chunk_ids_v1)
        for citation in t064.citations_of(answered_old.json)
    )
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.json["retrieval"]["cache"]["hit"] is True, hit.json["retrieval"]["cache"]

    # Delete the old source A while B is the live current source and the document
    # sits in the failed state. The document row does not move, so the same cache
    # key is derived: only the hit re-check can refuse it.
    job = t069.deletion_of(
        t069.delete_resource(owner, t069.RESOURCE_ARTIFACT, source_a)
    )
    assert job["resource_id"] == source_a
    artifact_a = t069.artifact_row(stack.dbname, source_a)
    assert artifact_a["state"] == "deleted", artifact_a
    assert artifact_a["deleted_at"] is not None, artifact_a
    assert t055.document_row(stack.dbname, document_id)["deleted_at"] is None
    for chunk in chunks_v1:
        body = t055.artifact_row(stack.dbname, chunk["body_artifact_id"])
        assert body["state"] == "ready" and body["deleted_at"] is None, body

    retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retrieval.status == 200, retrieval.text
    assert retrieval.json["items"] == [], retrieval.json["items"]
    assert_cache_miss(retrieval.json, reason="item_unreadable")
    assert (
        retrieval.json["retrieval"]["cache"]["key_prefix"]
        == warm.json["retrieval"]["cache"]["key_prefix"]
    ), retrieval.json["retrieval"]["cache"]
    assert t067.cache_row_by_key(stack.dbname, warm_key) is None
    for chunk_id in chunk_ids_v1:
        assert_safe_not_found(
            t055.get_citation(owner, chunk_id), chunk_body_artifact(stack, chunk_id)
        )
    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_NO_RESULTS, answered.json
    assert cache_rows(stack, owner, base_id) == []

    # Recovery: the same live source B can be rebuilt under the normal deployment;
    # the failed v2 stays as history and the superseded v1 stays closed.
    retried = t061.reindex(owner, document_id)
    assert retried.status == 202, retried.text
    assert int(retried.json["index_version"]) == 3, retried.json
    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-c303-failed-retry.log"
    ):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    v3 = t061.active_index(stack.dbname, document_id)
    assert v3 is not None and v3["id"] == retried.json["index_version_id"], v3
    assert v3["state"] == "ready" and v3["activated_at"] is not None, v3
    assert int(v3["document_revision"]) == 2, v3
    assert t061.index_by_version(stack.dbname, document_id, 1)["state"] == "superseded"
    assert t061.index_by_version(stack.dbname, document_id, 2)["state"] == "failed"
    for chunk_id in chunk_ids_v1:
        assert t055.get_citation(owner, chunk_id).status == 404
    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert healed.json["items"], healed.json
    assert any(REPLACEMENT_MARKER in item["text"] for item in healed.json["items"])
    assert not any("华北地区十月" in item["text"] for item in healed.json["items"])
    assert t055.get_citation(owner, healed.json["items"][0]["chunk_id"]).status == 200
    assert t064.answer(owner, base_id, QUERY, top_k=20).json["outcome"] == (
        t064.OUTCOME_ANSWERED
    )
    assert t055.retrieval_query(owner, base_id, QUERY, top_k=20).json["retrieval"][
        "cache"
    ]["hit"] is True


# ---------------------------------------------------------------------------
# AC2: a real document deletion blocks retrieval, citation open, answer and cache
# ---------------------------------------------------------------------------
def test_c303_document_delete_blocks_retrieval_citation_answer_and_cache(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _source, document_id = setup_ready_document(
        stack, log_name="c303-document-delete"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    chunks = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks
    chunk_ids = [chunk["id"] for chunk in chunks]
    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    old_key = cache_key_of(warm, stack.dbname)
    old_prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    assert item_ids(warm.json) == set(chunk_ids)
    revision_before = int(t055.document_row(stack.dbname, document_id)["revision"])

    job = t069.deletion_of(
        t069.delete_resource(owner, t069.RESOURCE_DOCUMENT, document_id)
    )
    assert job["resource_id"] == document_id
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "deleted" and row["deleted_at"] is not None, row
    assert int(row["revision"]) == revision_before + 1, row
    # The bytes are untouched (T069 tombstone only); the refusal is authorization.
    for chunk in chunks:
        body = t055.artifact_row(stack.dbname, chunk["body_artifact_id"])
        assert body["state"] == "ready" and body["deleted_at"] is None, body

    retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retrieval.status == 200, retrieval.text
    assert retrieval.json["items"] == [], retrieval.json["items"]
    cache = retrieval.json["retrieval"]["cache"]
    assert cache["hit"] is False and cache["stored"] is False, cache
    assert cache["key_prefix"] != old_prefix, cache
    # The lost-eviction adversary: the old entry is still live, yet unreachable.
    surviving = t067.cache_row_by_key(stack.dbname, old_key)
    assert surviving is not None and int(surviving["hit_count"]) == 0, surviving
    for chunk_id in chunk_ids:
        assert_safe_not_found(
            t055.get_citation(owner, chunk_id), chunk_body_artifact(stack, chunk_id)
        )
    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_NO_RESULTS, answered.json
    assert answered.json["answer"]["citations"] == []
    # No new row was stored for the empty post-delete result.
    assert [row["cache_key"] for row in cache_rows(stack, owner, base_id)] == [old_key]


# ---------------------------------------------------------------------------
# AC3: deterministic corruption is a miss + discard on all three paths
# ---------------------------------------------------------------------------
def test_c303_deterministic_corruption_is_a_miss_and_discard_on_all_three_paths(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _source, document_id = setup_ready_document(
        stack, log_name="c303-corruption"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    chunks = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert len(chunks) >= 2, chunks
    target = chunks[0]
    other_ids = {chunk["id"] for chunk in chunks[1:]}
    artifact = chunk_body_artifact(stack, target["id"])
    original = sealed_path(stack, artifact).read_bytes()
    original_sha = artifact["sha256"]
    original_size = int(artifact["size_bytes"])
    forbidden = original.decode("utf-8")
    assert len(original) > 1 and original_size > 1
    assert t055.sha256_hex(original) == original_sha

    modes = (
        "same-length replacement",
        "truncation",
        "authoritative size mismatch",
        "missing sha256 metadata",
        "missing sealed object",
    )
    for index, label in enumerate(modes):
        # Force a real store for every mode (the previous mode's heal may be live).
        t067.delete_cache_rows(stack.dbname, principal_id=owner.principal_id)
        warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
        old_key = cache_key_of(warm, stack.dbname)
        assert target["id"] in item_ids(warm.json), (label, warm.json["items"])

        removed: bytes | None = None
        if label == "same-length replacement":
            expected_reason = "item_mismatch"
            replace_sealed_bytes(stack, artifact, b"Z" * len(original))
        elif label == "truncation":
            expected_reason = "item_mismatch"
            replace_sealed_bytes(stack, artifact, original[:-1])
        elif label == "authoritative size mismatch":
            expected_reason = "item_mismatch"
            set_integrity_metadata(
                stack.dbname, artifact["id"], size_bytes=original_size - 1
            )
        elif label == "missing sha256 metadata":
            expected_reason = "item_mismatch"
            set_integrity_metadata(stack.dbname, artifact["id"], sha256=None)
        else:
            expected_reason = "item_unreadable"
            removed = t113.remove_object(stack, artifact["object_key"])
        try:
            retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
            assert retrieval.status == 200, (label, retrieval.text)
            assert target["id"] not in item_ids(retrieval.json), (label, retrieval.json)
            # The other chunk bodies remain verifiable, so this is a partial result.
            assert item_ids(retrieval.json) == other_ids, (label, retrieval.json["items"])
            assert_cache_miss(retrieval.json, reason=expected_reason)
            assert t067.cache_row_by_key(stack.dbname, old_key) is None, (
                label,
                "a deterministic refusal must discard the entry",
            )
            for item in retrieval.json["items"]:
                assert_item_is_the_sealed_body(stack, item)
            assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
            answered = t064.answer(owner, base_id, QUERY, top_k=20)
            assert answered.status == 200, (label, answered.text)
            assert answered.json["outcome"] == t064.OUTCOME_ANSWERED, (label, answered.json)
            assert all(
                citation["chunk_id"] != target["id"]
                for citation in t064.citations_of(answered.json)
            ), label
            assert forbidden not in answered.json["answer"]["text"], label
            assert all(
                forbidden not in citation["text"]
                for citation in t064.citations_of(answered.json)
            ), label
        finally:
            if label in {"same-length replacement", "truncation"}:
                restore_sealed_bytes(stack, artifact, original)
            elif label in {"authoritative size mismatch", "missing sha256 metadata"}:
                set_integrity_metadata(
                    stack.dbname,
                    artifact["id"],
                    sha256=original_sha,
                    size_bytes=original_size,
                )
            else:
                assert removed is not None
                t113.restore_object(stack, artifact["object_key"], removed)
        # Heal: the verified body is served and memoized again, then hits.
        healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
        assert target["id"] in item_ids(healed.json), (label, healed.json["items"])
        assert healed.json["retrieval"]["cache"]["stored"] is True
        assert_item_is_the_sealed_body(stack, next(
            item for item in healed.json["items"] if item["chunk_id"] == target["id"]
        ))
        assert t055.get_citation(owner, target["id"]).status == 200, label
        hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert hit.json["retrieval"]["cache"]["hit"] is True, (label, hit.json)


# ---------------------------------------------------------------------------
# AC3: a transient sealed-body read failure keeps the entry; recovery re-uses it
# ---------------------------------------------------------------------------
def test_c303_transient_read_failure_keeps_the_cache_entry_then_recovers(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _source, document_id = setup_ready_document(
        stack, log_name="c303-transient"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    chunks = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert len(chunks) >= 2, chunks
    target = chunks[0]
    other_ids = {chunk["id"] for chunk in chunks[1:]}
    artifact = chunk_body_artifact(stack, target["id"])
    forbidden = sealed_text(stack, artifact)

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    key = cache_key_of(warm, stack.dbname)
    assert target["id"] in item_ids(warm.json)
    hits_before = int(t067.cache_row_by_key(stack.dbname, key)["hit_count"])

    release = t113.hold_object_unreadable(stack, artifact["object_key"])
    try:
        # A transient OS error is not ErrNotExist/ErrInvalidKey/ErrTooLarge: this
        # request misses with the existing `cache_unavailable` reason, but the
        # entry is a rebuildable optimization and must survive.
        transient = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert transient.status == 200, transient.text
        assert target["id"] not in item_ids(transient.json), transient.json["items"]
        assert item_ids(transient.json) == other_ids, transient.json["items"]
        assert_cache_miss(transient.json, reason="cache_unavailable")
        surviving = t067.cache_row_by_key(stack.dbname, key)
        assert surviving is not None, "a transient read failure must keep the cache row"
        assert int(surviving["hit_count"]) == hits_before, surviving
        assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
        answered = t064.answer(owner, base_id, QUERY, top_k=20)
        assert answered.status == 200, answered.text
        assert all(
            citation["chunk_id"] != target["id"]
            for citation in t064.citations_of(answered.json)
        )
        assert forbidden not in answered.json["answer"]["text"]
    finally:
        release()

    # Recovery restores cache use with the entry that was kept (no re-store).
    recovered = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert recovered.status == 200, recovered.text
    recovered_cache = recovered.json["retrieval"]["cache"]
    assert recovered_cache["hit"] is True, recovered_cache
    assert int(recovered_cache["validated_chunks"]) == len(recovered.json["items"])
    assert target["id"] in item_ids(recovered.json), recovered.json["items"]
    after = t067.cache_row_by_key(stack.dbname, key)
    assert int(after["hit_count"]) == hits_before + 1, after
    assert t055.get_citation(owner, target["id"]).status == 200
    assert t064.answer(owner, base_id, QUERY, top_k=20).json["outcome"] == (
        t064.OUTCOME_ANSWERED
    )


# ---------------------------------------------------------------------------
# AC4: a citation invalidated while the answer is generating is never returned
# ---------------------------------------------------------------------------
def test_c303_a_source_deleted_during_answer_generation_is_never_in_the_final_answer(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, source_a, document_id = setup_ready_document(
        stack, log_name="c303-answer-window"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    chunks = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks
    chunk_ids = [chunk["id"] for chunk in chunks]

    # Replace the source with B and keep the rebuild queued, then warm the cache
    # under that state: retrieval reads the old active index (built from A).
    source_b = t055.prepare_source(stack, owner, REPLACEMENT_DOCUMENT)
    accepted = t061.reindex(owner, document_id, source_artifact_id=source_b)
    assert accepted.status == 202, accepted.text
    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert item_ids(warm.json) == set(chunk_ids), warm.json["items"]
    victim = warm.json["items"][0]["chunk_id"]
    # The retrieval cache stores retrieval items only; the answer is never cached.
    for row in cache_rows(stack, owner, base_id):
        for item in row["items"]:
            assert set(item) == t113.STORED_ITEM_FIELDS, item

    with t064.faulted_answer_server(
        api_server, stack, tmp_path / "c303-fault-slow", "slow=5s"
    ) as server:
        session = login_on(server)
        result: dict[str, object] = {}

        def ask() -> None:
            result["response"] = t064.answer(session, base_id, QUERY, top_k=20)

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        # The documented dev-stub fault: retrieval has completed (a cache hit) and
        # the stub holds the draft for five seconds before citation validation.
        server.wait_for_log("knowledge answer stub: slow fault waiting", timeout=30.0)
        job = t069.deletion_of(
            t069.delete_resource(owner, t069.RESOURCE_ARTIFACT, source_a)
        )
        assert job["resource_id"] == source_a
        worker.join(timeout=30.0)
        assert not worker.is_alive(), "the answer request never finished"

    response = result["response"]
    assert response.status == 409, response.text
    error = response.json["error"]
    assert error["code"] == t064.OUTCOME_CITATION_UNREADABLE, error
    assert error["details"]["outcome"] == t064.OUTCOME_CITATION_UNREADABLE
    assert error["details"]["chunk_id"] == victim, error
    assert "answer" not in response.json
    assert "citations" not in response.json

    # Afterwards the deleted source's chunks stay closed on every path.
    assert t055.get_citation(owner, victim).status == 404
    retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retrieval.json["items"] == [], retrieval.json["items"]
    assert t064.answer(owner, base_id, QUERY, top_k=20).json["outcome"] == (
        t064.OUTCOME_NO_RESULTS
    )
    # The retrieval cache stores retrieval items only; no answer text was ever
    # added to it by the generated (and then refused) answer.
    for row in cache_rows(stack, owner, base_id):
        for item in row["items"]:
            assert set(item) == t113.STORED_ITEM_FIELDS, item


# ---------------------------------------------------------------------------
# measured verification cost: the metadata-only source-liveness join
# ---------------------------------------------------------------------------
def _candidate_count_sql(*, base_id: str, owner_id: str, source_join: bool) -> str:
    """The reader's candidate aggregation, with or without this ticket's join.

    This is a *cost probe*, not a second implementation: it mirrors the shared
    readable-chunk predicate so the extra join can be measured over the real
    chunk/document/artifact rows. The sealed-body work (one open, one bounded read
    and one SHA-256 per candidate) is unchanged and measured by C3.01.
    """
    join = ""
    extra = ""
    if source_join:
        join = (
            " JOIN platform.artifacts AS src"
            " ON src.id = CASE"
            "   WHEN kc.citation->>'source_artifact_id'"
            "        ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'"
            "   THEN (kc.citation->>'source_artifact_id')::uuid"
            " END"
        )
        extra = (
            " AND src.owner_id = kb.owner_id AND src.deleted_at IS NULL"
            " AND src.state <> 'deleted'"
        )
    return (
        "SELECT count(*) FROM platform.knowledge_chunks AS kc"
        " JOIN platform.knowledge_documents AS kd ON kd.id = kc.document_id"
        " JOIN platform.knowledge_index_versions AS iv ON iv.id = kd.active_index_id"
        " JOIN platform.knowledge_bases AS kb ON kb.id = kd.knowledge_base_id"
        " JOIN platform.artifacts AS a ON a.id = kc.body_artifact_id"
        + join
        + " WHERE kb.owner_id = " + st.quote_literal(owner_id) + "::uuid"
        " AND kb.deleted_at IS NULL AND kd.deleted_at IS NULL"
        " AND iv.id = kc.index_version_id AND iv.state = 'ready'"
        " AND iv.document_id = kd.id"
        " AND kc.document_revision = iv.document_revision"
        " AND a.owner_id = kb.owner_id"
        " AND a.deleted_at IS NULL AND a.state = 'ready'"
        " AND kb.id = " + st.quote_literal(base_id) + "::uuid"
        + extra
    )


def _explain_analyze(dbname: str, query: str) -> dict:
    plan = json.loads(st.sql(dbname, "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query))
    return plan[0]


def test_c303_cost_of_the_source_liveness_join_on_real_rows(t055_stack) -> None:
    """Measure the added metadata join; no millisecond assertion, only a ceiling.

    The C3.01/C3.02 sealed-body verification cost is unchanged by this ticket: the
    same one open/one bounded read/one digest per candidate. The only new work is a
    metadata join that resolves the citation's source artifact through its primary
    key. This probe runs the reader's candidate aggregation with and without that
    join on the real seeded rows and prints the measured server-side times.
    """
    stack = t055_stack
    owner, base_id, _source, document_id = setup_ready_document(
        stack, log_name="c303-cost"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    chunks = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks
    expected = len(chunks)
    runs = 7
    measurements = {"with_join": [], "without_join": []}
    for source_join, key in ((True, "with_join"), (False, "without_join")):
        query = _candidate_count_sql(
            base_id=base_id, owner_id=owner.principal_id, source_join=source_join
        )
        assert int(st.sql(stack.dbname, query)) == expected, (key, expected)
        for _ in range(runs):
            plan = _explain_analyze(stack.dbname, query)
            measurements[key].append(
                {
                    "execution_ms": float(plan["Execution Time"]),
                    "shared_hit_blocks": int(plan["Plan"].get("Shared Hit Blocks", 0)),
                    "plan_node": plan["Plan"]["Node Type"],
                }
            )
    with_join = [row["execution_ms"] for row in measurements["with_join"]]
    without_join = [row["execution_ms"] for row in measurements["without_join"]]
    record = {
        "candidates": expected,
        "runs": runs,
        "with_join_ms_median": round(statistics.median(with_join), 4),
        "without_join_ms_median": round(statistics.median(without_join), 4),
        "delta_ms_median": round(
            statistics.median(with_join) - statistics.median(without_join), 4
        ),
        "with_join_plan_node": measurements["with_join"][0]["plan_node"],
        "without_join_plan_node": measurements["without_join"][0]["plan_node"],
        "guard": "both medians < 30000 ms (coarse functional ceiling; no ms assertion)",
    }
    print("C303-COST " + json.dumps(record, ensure_ascii=False))
    # Coarse functional ceiling only: absolute latency on this host is not a
    # performance promise, so the record is the evidence and the assertion only
    # catches a pathological regression (e.g. an accidental per-row object read).
    assert statistics.median(with_join) < 30_000
    assert statistics.median(without_join) < 30_000


# ---------------------------------------------------------------------------
# AC1/AC3: deleting one document's old source leaves the other document readable
# ---------------------------------------------------------------------------
SECOND_DOCUMENT = (
    "# 差旅制度\n\n"
    "差旅报销需在返回后 15 天内提交，marker-c303-second-document。\n\n"
    "住宿标准按城市等级执行，marker-c303-second-document。\n"
)


def test_c303_deleting_one_sources_chunks_leaves_the_other_document_readable(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, artifacts, documents = t067.setup_kb_with_documents(
        stack,
        "c303-partial",
        login="c303.partial",
        contents=[t055.SAMPLE_DOCUMENT, SECOND_DOCUMENT],
    )
    source_a1, source_a2 = artifacts
    document_one, document_two = documents
    index_one = t061.active_index(stack.dbname, document_one)
    index_two = t061.active_index(stack.dbname, document_two)
    chunks_one = t061.chunk_rows_for_index(stack.dbname, index_one["id"])
    chunks_two = t061.chunk_rows_for_index(stack.dbname, index_two["id"])
    assert chunks_one and chunks_two
    chunk_ids_one = {chunk["id"] for chunk in chunks_one}
    chunk_ids_two = {chunk["id"] for chunk in chunks_two}
    assert {t055.citation_of(chunk)["source_artifact_id"] for chunk in chunks_one} == {
        source_a1
    }
    assert {t055.citation_of(chunk)["source_artifact_id"] for chunk in chunks_two} == {
        source_a2
    }

    # Replace only document one's source with B and keep the rebuild queued.
    source_b = t055.prepare_source(stack, owner, REPLACEMENT_DOCUMENT)
    accepted = t061.reindex(owner, document_one, source_artifact_id=source_b)
    assert accepted.status == 202, accepted.text
    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    warm_key = cache_key_of(warm, stack.dbname)
    warm_ids = item_ids(warm.json)
    assert warm_ids & chunk_ids_one and warm_ids & chunk_ids_two, warm_ids
    for item in warm.json["items"]:
        assert_item_is_the_sealed_body(stack, item)

    # Delete document one's old source only; document two's source stays live.
    t069.deletion_of(t069.delete_resource(owner, t069.RESOURCE_ARTIFACT, source_a1))
    assert t069.artifact_row(stack.dbname, source_a1)["state"] == "deleted"
    assert t069.artifact_row(stack.dbname, source_a2)["deleted_at"] is None
    assert t055.document_row(stack.dbname, document_one)["deleted_at"] is None
    assert t055.document_row(stack.dbname, document_two)["deleted_at"] is None

    retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retrieval.status == 200, retrieval.text
    assert item_ids(retrieval.json) == chunk_ids_two, retrieval.json["items"]
    cache = retrieval.json["retrieval"]["cache"]
    assert cache["hit"] is False and cache["discarded"] is True, cache
    assert cache["discard_reason"] == "item_unreadable", cache
    # The remaining complete result is re-stored under the same key: the SQL
    # predicate removed the deleted source's chunks, they are not "skipped".
    assert cache["stored"] is True, cache
    assert cache["key_prefix"] == warm.json["retrieval"]["cache"]["key_prefix"]
    fresh = t067.cache_row_by_key(stack.dbname, warm_key)
    assert fresh is not None, "the complete remaining result was re-stored"
    assert {item["chunk_id"] for item in fresh["items"]} == chunk_ids_two
    assert all(item["document_id"] == document_two for item in fresh["items"])
    assert not ({item["chunk_id"] for item in fresh["items"]} & chunk_ids_one)
    # Document one's citations are closed; document two's still open.
    for chunk_id in chunk_ids_one:
        assert_safe_not_found(
            t055.get_citation(owner, chunk_id), chunk_body_artifact(stack, chunk_id)
        )
    for chunk_id in chunk_ids_two:
        assert t055.get_citation(owner, chunk_id).status == 200
    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_ANSWERED, answered.json
    assert {citation["chunk_id"] for citation in t064.citations_of(answered.json)} <= (
        chunk_ids_two
    )
    assert all(citation["chunk_id"] not in chunk_ids_one for citation in t064.citations_of(answered.json))

    # The queued B rebuild is still legal and activates document one again; both
    # documents are then readable with their live sources.
    with t055.ingest_pools(
        stack, stack.server.log_path.parent / "maintenance-c303-partial.log"
    ):
        view = t055.wait_for_document_state(owner, document_one, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_one))
    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    healed_ids = item_ids(healed.json)
    assert healed_ids & chunk_ids_two
    assert healed_ids & {chunk["id"] for chunk in t061.chunk_rows_for_index(
        stack.dbname, t061.active_index(stack.dbname, document_one)["id"]
    )}
    assert not (healed_ids & chunk_ids_one)
    assert any(REPLACEMENT_MARKER in item["text"] for item in healed.json["items"])
