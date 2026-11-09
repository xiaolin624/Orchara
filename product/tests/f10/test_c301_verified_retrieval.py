"""C3.01 acceptance: ordinary retrieval serves only byte-verified chunk bodies.

The owner-bound readable-chunk module must not trust a sealed chunk body because a
chunk row points at it. It takes the authoritative ``platform.artifacts``
``sha256`` and ``size_bytes``, reads the sealed object once with a bounded read,
computes the digest and the byte count in that same read, and returns text only
when both agree. A chunk that fails that check is skipped, the answer is marked
partial (``retrieval.cache.stored == false``) and it is never memoized.

Everything here is the real stack: PostgreSQL 16.4 with the compiled
``cmd/admin``/``cmd/api``/``cmd/maintenance`` binaries, real HTTP, the real
filesystem object store and the real ``document_ingest`` handler. The corruption
is applied where a storage fault or an inconsistent row would apply it: the
sealed object bytes and the authoritative artifact metadata are edited directly,
and the assertion is the public retrieval answer plus the real cache table.
"""

from __future__ import annotations

import json
import time

from support import f00b, t055, t067, t113
from support import stack as st
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

QUERY = "华北地区十月的销售额是多少"
MAX_OBJECT_BYTES = 20 << 20  # contracts/openapi.json size_bytes maximum

_KEEP = object()


# ---------------------------------------------------------------------------
# real-stack setup and storage corruption helpers
# ---------------------------------------------------------------------------
def setup_kb(stack, contents, *, login: str = "c301.alice"):
    """One owner, one KB and N documents that became ready through the real jobs."""
    return t067.setup_kb_with_documents(stack, "c301", login=login, contents=list(contents))


def sealed_path(stack, artifact: dict):
    return t113.object_path(stack, artifact["object_key"])


def replace_sealed_bytes(stack, artifact: dict, data: bytes) -> bytes:
    """Overwrite one sealed chunk body in place; return the original bytes."""
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
        assignments.append("sha256 = " + ("NULL" if sha256 is None else st.quote_literal(sha256)))
    if size_bytes is not _KEEP:
        assignments.append("size_bytes = " + ("NULL" if size_bytes is None else str(int(size_bytes))))
    assert assignments, "at least one integrity column must be edited"
    st.sql(
        dbname,
        f"UPDATE platform.artifacts SET {', '.join(assignments)} "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


def candidate_chunks(stack, document_id: str) -> list[dict]:
    """The real chunk rows that the retrieval predicate can see for one document."""
    return t055.chunk_rows(stack.dbname, document_id)


def body_artifact_of(stack, chunk: dict) -> dict:
    return t055.artifact_row(stack.dbname, chunk["body_artifact_id"])


def cache_rows_for_owner(stack, owner, base_id: str) -> list[dict]:
    return t067.cache_rows(stack.dbname, principal_id=owner.principal_id, base_id=base_id)


def clear_cache(stack, owner) -> None:
    t067.delete_cache_rows(stack.dbname, principal_id=owner.principal_id)


def item_ids(payload: dict) -> set[str]:
    return {item["chunk_id"] for item in payload["items"]}


# ---------------------------------------------------------------------------
# a same-length replacement of the sealed bytes is not valid text
# ---------------------------------------------------------------------------
def test_c301_same_length_replacement_is_skipped_and_never_cached(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    chunks = candidate_chunks(stack, document_id)
    assert len(chunks) >= 3, chunks
    target = chunks[0]
    artifact = body_artifact_of(stack, target)
    original = sealed_path(stack, artifact).read_bytes()

    # A same-length replacement keeps every size on the host identical, so only the
    # SHA-256 of the bytes can expose it.
    tampered = b"Z" * len(original)
    assert len(tampered) == len(original) and tampered != original
    replace_sealed_bytes(stack, artifact, tampered)
    try:
        response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert response.status == 200, response.text
        payload = response.json
        assert target["id"] not in item_ids(payload), payload["items"]
        assert all("ZZZZ" not in item["text"] for item in payload["items"]), payload["items"]
        cache = payload["retrieval"]["cache"]
        assert cache["enabled"] is True, cache
        assert cache["hit"] is False, cache
        assert cache["stored"] is False, cache
        assert cache_rows_for_owner(stack, owner, base_id) == []

        # Repeating the request must not turn the corrupt body into a cache hit.
        repeat = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert repeat.status == 200, repeat.text
        assert target["id"] not in item_ids(repeat.json)
        assert repeat.json["retrieval"]["cache"]["stored"] is False
        assert cache_rows_for_owner(stack, owner, base_id) == []
    finally:
        restore_sealed_bytes(stack, artifact, original)

    # Once the sealed bytes are the authoritative bytes again, the chunk returns
    # and the (complete) result is memoized.
    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert healed.status == 200, healed.text
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# a truncated sealed body is not valid text
# ---------------------------------------------------------------------------
def test_c301_truncated_body_is_skipped_and_never_cached(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    target = candidate_chunks(stack, document_id)[0]
    artifact = body_artifact_of(stack, target)
    original = sealed_path(stack, artifact).read_bytes()
    assert len(original) > 1

    replace_sealed_bytes(stack, artifact, original[:-1])
    try:
        response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert response.status == 200, response.text
        assert target["id"] not in item_ids(response.json), response.json["items"]
        cache = response.json["retrieval"]["cache"]
        assert cache["hit"] is False and cache["stored"] is False, cache
        assert cache_rows_for_owner(stack, owner, base_id) == []
    finally:
        restore_sealed_bytes(stack, artifact, original)

    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# the authoritative size_bytes, not the host's size, decides
# ---------------------------------------------------------------------------
def test_c301_wrong_size_metadata_is_not_valid_text(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    target = candidate_chunks(stack, document_id)[0]
    artifact = body_artifact_of(stack, target)
    original_size = len(sealed_path(stack, artifact).read_bytes())

    # The bytes stay untouched, so only the recorded size can expose the row. A
    # size that is too large and a size that is too small both fail the one
    # bounded read; neither may fall back to the host's byte count.
    for wrong_size in (original_size + 1, original_size - 1):
        assert wrong_size >= 0
        set_integrity_metadata(stack.dbname, artifact["id"], size_bytes=wrong_size)
        try:
            response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
            assert response.status == 200, response.text
            assert target["id"] not in item_ids(response.json), (
                wrong_size,
                response.json["items"],
            )
            cache = response.json["retrieval"]["cache"]
            assert cache["hit"] is False and cache["stored"] is False, (wrong_size, cache)
            assert cache_rows_for_owner(stack, owner, base_id) == []
        finally:
            set_integrity_metadata(stack.dbname, artifact["id"], size_bytes=original_size)

    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# missing or illegal integrity metadata is never "close enough"
# ---------------------------------------------------------------------------
def test_c301_missing_or_invalid_integrity_metadata_is_not_valid_text(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    target = candidate_chunks(stack, document_id)[0]
    artifact = body_artifact_of(stack, target)
    original_sha = artifact["sha256"]
    original_size = int(artifact["size_bytes"])
    assert original_size > 0

    cases = (
        {"sha256": None, "label": "sha256 NULL"},
        {"sha256": "", "label": "sha256 empty"},
        {"sha256": "0" * 63, "label": "sha256 short"},
        {"sha256": "z" * 64, "label": "sha256 non-hex"},
        {"size_bytes": None, "label": "size_bytes NULL"},
        {"size_bytes": 0, "label": "size_bytes zero"},
        {"size_bytes": MAX_OBJECT_BYTES + 1, "label": "size_bytes over protocol cap"},
    )
    for case in cases:
        label = case["label"]
        set_integrity_metadata(
            stack.dbname,
            artifact["id"],
            **{key: value for key, value in case.items() if key != "label"},
        )
        try:
            response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
            assert response.status == 200, (label, response.text)
            assert target["id"] not in item_ids(response.json), (
                label,
                response.json["items"],
            )
            cache = response.json["retrieval"]["cache"]
            assert cache["hit"] is False and cache["stored"] is False, (label, cache)
            assert cache_rows_for_owner(stack, owner, base_id) == [], label
        finally:
            set_integrity_metadata(
                stack.dbname,
                artifact["id"],
                sha256=original_sha,
                size_bytes=original_size,
            )

    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# a missing sealed object is skipped, and the citation stays 404
# ---------------------------------------------------------------------------
def test_c301_missing_sealed_object_is_skipped_and_citation_stays_not_found(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    target = candidate_chunks(stack, document_id)[0]
    artifact = body_artifact_of(stack, target)
    original = t113.remove_object(stack, artifact["object_key"])
    try:
        response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert response.status == 200, response.text
        assert target["id"] not in item_ids(response.json), response.json["items"]
        cache = response.json["retrieval"]["cache"]
        assert cache["hit"] is False and cache["stored"] is False, cache
        assert cache_rows_for_owner(stack, owner, base_id) == []

        opened = t055.get_citation(owner, target["id"])
        assert opened.status == 404, opened.text
        assert opened.json["error"]["code"] == "resource_not_found"
        # The refusal never names the object key or a filesystem path.
        assert artifact["object_key"] not in opened.text
        assert "sealed/" not in opened.text
    finally:
        t113.restore_object(stack, artifact["object_key"], original)

    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# the owner and tombstone filters are unchanged by the reader migration
# ---------------------------------------------------------------------------
def test_c301_owner_and_tombstone_filters_still_block_retrieval(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    document_id = documents[0]
    saved = candidate_chunks(stack, document_id)
    warm = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert warm.json["retrieval"]["cache"]["stored"] is True

    admin = f00b.admin_session(stack)
    bob = f00b.invite_and_activate(stack, admin, "c301.bob", password=f00b.USER_PASSWORD)
    assert t055.retrieval_query(bob, base_id, QUERY).status == 404
    assert t055.get_citation(bob, saved[0]["id"]).status == 404

    st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_documents SET state = 'deleted', deleted_at = now() "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    revoked = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert revoked.status == 200, revoked.text
    assert revoked.json["items"] == [], revoked.json["items"]
    cache = revoked.json["retrieval"]["cache"]
    assert cache["hit"] is False and cache["stored"] is False, cache
    assert t055.get_citation(owner, saved[0]["id"]).status == 404
    assert t055.get_document(owner, document_id).status == 404


# ---------------------------------------------------------------------------
# a whole-SQL failure is an error, not a silently partial answer
# ---------------------------------------------------------------------------
def test_c301_candidate_sql_failure_is_an_error_not_a_partial_answer(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack, [t055.SAMPLE_DOCUMENT])
    assert documents

    st.sql(
        stack.dbname,
        "ALTER TABLE platform.knowledge_chunks "
        "RENAME TO knowledge_chunks_c301_offline",
    )
    try:
        response = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert response.status == 500, response.text
        assert response.json["error"]["code"] == "internal_error", response.json
        assert "knowledge_chunks" not in response.text, response.text
        assert "sealed/" not in response.text, response.text
        assert cache_rows_for_owner(stack, owner, base_id) == []
    finally:
        st.sql(
            stack.dbname,
            "ALTER TABLE platform.knowledge_chunks_c301_offline "
            "RENAME TO knowledge_chunks",
        )

    healed = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert healed.status == 200, healed.text
    assert healed.json["items"], healed.json
    assert healed.json["retrieval"]["cache"]["stored"] is True


# ---------------------------------------------------------------------------
# the verification cost, recorded honestly with a coarse functional guard
# ---------------------------------------------------------------------------
def test_c301_verification_cost_is_one_bounded_read_per_candidate(t055_stack) -> None:
    stack = t055_stack
    # Three real documents so the candidate set has more than one sealed body to
    # verify, while keeping the test's ingest time bounded.
    owner, base_id, _artifacts, documents = setup_kb(
        stack,
        [t055.SAMPLE_DOCUMENT, t113.DOC_B, t055.SAMPLE_DOCUMENT],
    )
    chunks = [chunk for document_id in documents for chunk in candidate_chunks(stack, document_id)]
    assert len(chunks) >= 6, chunks
    artifacts = {chunk["id"]: body_artifact_of(stack, chunk) for chunk in chunks}
    expected_bytes = sum(int(artifacts[chunk["id"]]["size_bytes"]) for chunk in chunks)

    clear_cache(stack, owner)
    started = time.perf_counter()
    response = t055.retrieval_query(owner, base_id, QUERY, top_k=50)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert response.status == 200, response.text
    assert response.json["retrieval"]["cache"]["stored"] is True

    # Every returned chunk is the whole authoritative body: its UTF-8 byte count
    # and SHA-256 equal the artifact row the reader had to verify. This is the
    # functional meaning of "one bounded read computed both values".
    returned_bytes = 0
    for item in response.json["items"]:
        artifact = artifacts[item["chunk_id"]]
        text = item["text"].encode("utf-8")
        assert len(text) == int(artifact["size_bytes"]), item["chunk_id"]
        assert t055.sha256_hex(text) == artifact["sha256"], item["chunk_id"]
        returned_bytes += len(text)
    assert item_ids(response.json) == {chunk["id"] for chunk in chunks}, (
        len(response.json["items"]),
        len(chunks),
    )
    assert returned_bytes == expected_bytes

    record = {
        "candidates": len(chunks),
        "candidate_body_bytes": expected_bytes,
        "returned_items": len(response.json["items"]),
        "returned_verified_bytes": returned_bytes,
        "elapsed_ms": round(elapsed_ms, 3),
        "guard": "elapsed_ms < 30000 (coarse functional ceiling; no millisecond assertion)",
    }
    print("C301-COST " + json.dumps(record, ensure_ascii=False))
    # Coarse functional guard, not a latency target: a pathological double read or
    # an unbounded read would be caught by the Go unit cost test; here the request
    # only has to finish inside a generous ceiling on this shared host.
    assert elapsed_ms < 30_000, record
