"""C3.02 acceptance: citation open, answer re-check and cache hits use the one verified read.

C3.01 gave ordinary retrieval an owner-bound readable-chunk reader: the
authoritative ``platform.artifacts.sha256`` / ``size_bytes`` plus one bounded read
that counts the bytes and computes the digest in the same pass. This ticket
migrates the remaining three callers of chunk bodies that still bypassed it:

* ``GET /knowledge-citations/{id}`` (``Service.GetChunk``) must open through the
  same verified read; a body that cannot be verified is the existing safe 404.
* ``POST /knowledge-bases/{id}/answer`` must revalidate every citation through
  that path immediately before returning, and must never return an answer whose
  citation has become invalid; final answers are never cached.
* the retrieval-cache hit path must re-check every stored item through
  ``reader.CheckCached`` — same provenance, same authoritative metadata, same
  sealed bytes — while keeping the accepted fault policy: a deterministic
  missing/corrupt/unreadable body destroys the entry, a transient read/DB
  failure only misses this request and keeps it for a recovered service.

Everything here is the real stack: PostgreSQL 16.4 with the compiled
``cmd/admin``/``cmd/api``/``cmd/maintenance`` binaries, real HTTP, the real
filesystem object store, real sealed bytes and the real cache table. The
corruption is applied exactly where a storage fault or an inconsistent row would
apply it (sealed object bytes and authoritative artifact metadata), and the
assertions are the public retrieval/open/answer responses plus the real cache
rows. Cache on/off pairs are two real API processes over the same database and
object directory.
"""

from __future__ import annotations

import threading

from support import f00b, t055, t061, t064, t067, t113
from support import stack as st
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

QUERY = "华北地区十月的销售额是多少"
# contracts/openapi.json: the artifact size_bytes maximum (20 MiB).
MAX_OBJECT_BYTES = 20 << 20
CACHE_REASONS = {
    "expired",
    "components_changed",
    "item_unreadable",
    "item_mismatch",
    "cache_unavailable",
}

_KEEP = object()


# ---------------------------------------------------------------------------
# real-stack setup
# ---------------------------------------------------------------------------
def setup_kb(stack, contents=None, *, login: str = "c302.alice"):
    """One owner, one KB and N documents that became ready through the real jobs."""
    if contents is None:
        contents = [t055.SAMPLE_DOCUMENT]
    return t067.setup_kb_with_documents(
        stack, "c302", login=login, contents=list(contents)
    )


def login_on(server, login: str = "c302.alice"):
    return t064.login_on(server, login, f00b.USER_PASSWORD)


def item_ids(payload: dict) -> set[str]:
    return {item["chunk_id"] for item in payload["items"]}


def chunk_artifact(stack, chunk_id: str) -> dict:
    chunk = t064.chunk_row(stack.dbname, chunk_id)
    return t055.artifact_row(stack.dbname, chunk["body_artifact_id"])


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


def cache_rows(stack, owner, base_id: str) -> list[dict]:
    return t067.cache_rows(
        stack.dbname, principal_id=owner.principal_id, base_id=base_id
    )


def seeded_text(stack, artifact: dict) -> str:
    """The test's own truth read of one sealed body."""
    return sealed_path(stack, artifact).read_bytes().decode("utf-8")


def valid_sha(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def assert_safe_not_found(response, artifact: dict) -> None:
    """A citation body that cannot be verified is the existing indistinguishable 404."""
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


def assert_answer_excludes(payload: dict, chunk_id: str, forbidden_text: str) -> None:
    """The answer never carries the invalid citation or its bytes."""
    citations = t064.citations_of(payload)
    assert all(citation["chunk_id"] != chunk_id for citation in citations), citations
    assert chunk_id not in t064.retrieval_chunk_ids(payload), payload["retrieval"]
    assert forbidden_text not in payload["answer"]["text"], payload["answer"]
    assert all(forbidden_text not in citation["text"] for citation in citations)


def answer_signature(payload: dict) -> dict:
    """The parts of an answer a cache-on/off comparison must agree on exactly."""
    return {
        "outcome": payload["outcome"],
        "text": payload["answer"]["text"],
        "citations": [
            {
                key: citation[key]
                for key in (
                    "chunk_id",
                    "document_id",
                    "document_revision",
                    "chunk_no",
                    "span",
                    "text",
                    "citation",
                    "score",
                )
            }
            for citation in t064.citations_of(payload)
        ],
        "chunk_ids": t064.retrieval_chunk_ids(payload),
    }


def assert_verified_reads_refuse(
    stack,
    owner,
    base_id: str,
    *,
    chunk_id: str,
    forbidden_text: str,
    expected_reason: str,
    old_key: str | None = None,
) -> dict:
    """The cache hit and the answer both refuse a body that cannot be verified.

    ``old_key`` is the live entry warmed before the corruption: a deterministic
    refusal must delete it, so the miss path cannot re-serve the memoized text.
    """
    retry = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert retry.status == 200, retry.text
    assert chunk_id not in item_ids(retry.json), retry.json["items"]
    assert_cache_miss(retry.json, reason=expected_reason)
    if old_key is not None:
        assert t067.cache_row_by_key(stack.dbname, old_key) is None, (
            "a deterministic refusal must discard the cache entry"
        )
    assert cache_rows(stack, owner, base_id) == []

    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert_answer_excludes(answered.json, chunk_id, forbidden_text)
    for citation in t064.citations_of(answered.json):
        assert t055.get_citation(owner, citation["chunk_id"]).status == 200
    return answered


# ---------------------------------------------------------------------------
# a same-length replacement of the sealed bytes
# ---------------------------------------------------------------------------
def test_c302_same_length_replacement_blocks_open_answer_and_cache_hit(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    original = sealed_path(stack, artifact).read_bytes()
    tampered = b"Z" * len(original)
    assert len(tampered) == len(original) and tampered != original

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    cache = warm.json["retrieval"]["cache"]
    assert cache["stored"] is True, cache
    old_row = t067.cache_row_with_prefix(stack.dbname, cache["key_prefix"])
    assert target["id"] in item_ids(warm.json), warm.json["items"]

    replace_sealed_bytes(stack, artifact, tampered)
    try:
        # (1) Citation open: the citation path used to return the replaced bytes as
        # if they were the sealed body. It must be the safe 404 instead.
        opened = t055.get_citation(owner, target["id"])
        assert_safe_not_found(opened, artifact)

        # (2) Cache hit: the live entry must be re-checked against the sealed
        # bytes, miss and be discarded; (3) answer: the invalid body is never
        # cited or quoted.
        answered = assert_verified_reads_refuse(
            stack,
            owner,
            base_id,
            chunk_id=target["id"],
            forbidden_text="ZZZZ",
            expected_reason="item_mismatch",
            old_key=old_row["cache_key"],
        )
        on_retrieval = t055.retrieval_query(owner, base_id, QUERY, top_k=20)

        # (4) Cache on/off pair: another real API process with the cache disabled
        # must answer identically from the authoritative path.
        with t067.cache_disabled_server(
            api_server, stack, tmp_path / "cache-off-same-length"
        ) as server:
            off = login_on(server)
            off_retrieval = t055.retrieval_query(off, base_id, QUERY, top_k=20)
            assert off_retrieval.status == 200, off_retrieval.text
            assert off_retrieval.json["retrieval"]["cache"]["enabled"] is False
            assert off_retrieval.json["items"] == on_retrieval.json["items"]
            off_open = t055.get_citation(off, target["id"])
            assert_safe_not_found(off_open, artifact)
            off_answer = t064.answer(off, base_id, QUERY, top_k=20)
            assert off_answer.status == 200, off_answer.text
            assert answer_signature(off_answer.json) == answer_signature(answered.json)
    finally:
        restore_sealed_bytes(stack, artifact, original)

    # The verified body returns, and the complete result is memoized again.
    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.json["retrieval"]["cache"]["hit"] is True, hit.json["retrieval"]["cache"]
    assert t055.get_citation(owner, target["id"]).status == 200


# ---------------------------------------------------------------------------
# the authoritative size_bytes, not the host's size, decides
# ---------------------------------------------------------------------------
def test_c302_authoritative_size_mismatch_blocks_open_answer_and_cache_hit(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    original = sealed_path(stack, artifact).read_bytes()
    original_size = len(original)
    original_text = original.decode("utf-8")
    assert original_size > 1

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    old_key = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )["cache_key"]
    assert target["id"] in item_ids(warm.json), warm.json["items"]

    # The sealed bytes stay untouched, so only the authoritative size column can
    # expose this row. The old cache-hit check compared the stored text and
    # digest only and never consulted `artifacts.size_bytes`, so it would serve
    # the memoized entry; the same verified read the other paths use must refuse.
    set_integrity_metadata(stack.dbname, artifact["id"], size_bytes=original_size - 1)
    try:
        opened = t055.get_citation(owner, target["id"])
        assert_safe_not_found(opened, artifact)

        retry = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert retry.status == 200, retry.text
        assert target["id"] not in item_ids(retry.json), retry.json["items"]
        assert_cache_miss(retry.json, reason="item_mismatch")
        assert t067.cache_row_by_key(stack.dbname, old_key) is None, (
            "a deterministic size mismatch must discard the cache entry"
        )
        assert cache_rows(stack, owner, base_id) == []

        answered = t064.answer(owner, base_id, QUERY, top_k=20)
        assert answered.status == 200, answered.text
        assert_answer_excludes(answered.json, target["id"], original_text)
    finally:
        set_integrity_metadata(stack.dbname, artifact["id"], size_bytes=original_size)

    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True, healed.json["retrieval"]
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.json["retrieval"]["cache"]["hit"] is True, hit.json["retrieval"]["cache"]


# ---------------------------------------------------------------------------
# a truncated sealed body
# ---------------------------------------------------------------------------
def test_c302_truncated_body_blocks_open_answer_and_cache_hit(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    original = sealed_path(stack, artifact).read_bytes()
    assert len(original) > 1
    forbidden = original.decode("utf-8")

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    old_row = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )

    replace_sealed_bytes(stack, artifact, original[:-1])
    try:
        assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
        assert_verified_reads_refuse(
            stack,
            owner,
            base_id,
            chunk_id=target["id"],
            forbidden_text=forbidden,
            expected_reason="item_mismatch",
            old_key=old_row["cache_key"],
        )
    finally:
        restore_sealed_bytes(stack, artifact, original)

    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True


# ---------------------------------------------------------------------------
# missing or illegal integrity metadata is never "close enough"
# ---------------------------------------------------------------------------
def test_c302_missing_or_invalid_metadata_blocks_open_answer_and_cache_hit(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    original_sha = artifact["sha256"]
    original_size = int(artifact["size_bytes"])
    forbidden = seeded_text(stack, artifact)
    assert original_size > 0 and valid_sha(original_sha)

    # A digest that disagrees with the stored provenance is caught by the
    # provenance comparison before a byte is read (item_mismatch); a size that is
    # missing or outside the protocol range reaches the verifier as unusable
    # metadata (item_unreadable). Both are deterministic discards in ADR 0015 D6's
    # closed vocabulary.
    cases = (
        ({"sha256": None}, "sha256 NULL", "item_mismatch"),
        ({"sha256": "z" * 64}, "sha256 non-hex", "item_mismatch"),
        ({"size_bytes": None}, "size_bytes NULL", "item_unreadable"),
        ({"size_bytes": 0}, "size_bytes zero", "item_unreadable"),
        (
            {"size_bytes": MAX_OBJECT_BYTES + 1},
            "size_bytes over protocol cap",
            "item_unreadable",
        ),
    )
    for mutation, label, expected_reason in cases:
        warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
        old_row = t067.cache_row_with_prefix(
            stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
        )
        set_integrity_metadata(stack.dbname, artifact["id"], **mutation)
        try:
            assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
            assert_verified_reads_refuse(
                stack,
                owner,
                base_id,
                chunk_id=target["id"],
                forbidden_text=forbidden,
                expected_reason=expected_reason,
                old_key=old_row["cache_key"],
            )
        finally:
            set_integrity_metadata(
                stack.dbname,
                artifact["id"],
                sha256=original_sha,
                size_bytes=original_size,
            )

    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.json["retrieval"]["cache"]["hit"] is True


# ---------------------------------------------------------------------------
# a missing sealed object
# ---------------------------------------------------------------------------
def test_c302_missing_object_blocks_open_answer_and_cache_hit(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    forbidden = seeded_text(stack, artifact)

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    old_row = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )

    original = t113.remove_object(stack, artifact["object_key"])
    try:
        assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
        assert_verified_reads_refuse(
            stack,
            owner,
            base_id,
            chunk_id=target["id"],
            forbidden_text=forbidden,
            expected_reason="item_unreadable",
            old_key=old_row["cache_key"],
        )
    finally:
        t113.restore_object(stack, artifact["object_key"], original)

    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True


# ---------------------------------------------------------------------------
# a transient read failure keeps the entry; recovery hits it again
# ---------------------------------------------------------------------------
def test_c302_transient_read_failure_keeps_entry_then_hits_after_recovery(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    forbidden = seeded_text(stack, artifact)

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    key = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )["cache_key"]
    hits_before = int(t067.cache_row_by_key(stack.dbname, key)["hit_count"])

    release = t113.hold_object_unreadable(stack, artifact["object_key"])
    try:
        # A transient OS error is not ErrNotExist/ErrInvalidKey/ErrTooLarge: this
        # request misses with the existing `cache_unavailable` reason, but the
        # entry is a rebuildable optimization and must survive.
        transient = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert transient.status == 200, transient.text
        assert target["id"] not in item_ids(transient.json), transient.json["items"]
        assert_cache_miss(transient.json, reason="cache_unavailable")
        surviving = t067.cache_row_by_key(stack.dbname, key)
        assert surviving is not None, "a transient read failure must keep the cache row"
        assert int(surviving["hit_count"]) == hits_before, surviving

        # Opening the citation cannot verify the body either; it stays 404.
        assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
        # The answer is produced from the chunks that are still verifiable and
        # never quotes the unavailable body.
        answered = t064.answer(owner, base_id, QUERY, top_k=20)
        assert answered.status == 200, answered.text
        assert_answer_excludes(answered.json, target["id"], forbidden)
    finally:
        release()

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
# permission / tombstone / body-owner revocation: immediate block on every path
# ---------------------------------------------------------------------------
def test_c302_permission_tombstone_and_revocation_block_immediately(
    t055_stack,
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    forbidden = seeded_text(stack, artifact)

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    old_row = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )
    admin = f00b.admin_session(stack)
    bob = f00b.invite_and_activate(stack, admin, "c302.bob", password=f00b.USER_PASSWORD)

    # (1) A foreign principal cannot retrieve, open or answer, cache or not.
    assert t055.retrieval_query(bob, base_id, QUERY, top_k=20).status == 404
    assert t055.get_citation(bob, target["id"]).status == 404
    assert t064.answer(bob, base_id, QUERY, top_k=20).status == 404

    # (2) The body artifact becomes foreign while the cache key is unchanged. The
    # live hit must be discarded because the current readable-chunk predicate no
    # longer resolves it; no path may serve the body.
    st.sql(
        stack.dbname,
        "UPDATE platform.artifacts SET owner_id = "
        f"{st.quote_literal(bob.principal_id)}::uuid "
        f"WHERE id = {st.quote_literal(artifact['id'])}::uuid",
    )
    try:
        assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
        retry = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
        assert retry.status == 200, retry.text
        assert target["id"] not in item_ids(retry.json), retry.json["items"]
        retry_cache = retry.json["retrieval"]["cache"]
        assert retry_cache["hit"] is False, retry_cache
        assert retry_cache["discarded"] is True, retry_cache
        assert retry_cache["discard_reason"] == "item_unreadable", retry_cache
        # The foreign body is excluded by the SQL predicate (it is not a
        # "skipped candidate"), so the remaining complete result may be stored
        # again under the same key; the foreign body must not be in it.
        refreshed = t067.cache_row_by_key(stack.dbname, old_row["cache_key"])
        assert refreshed is not None, "the complete remaining result was re-stored"
        assert all(
            item["chunk_id"] != target["id"] for item in refreshed["items"]
        ), refreshed["items"]
        answered = t064.answer(owner, base_id, QUERY, top_k=20)
        assert answered.status == 200, answered.text
        assert_answer_excludes(answered.json, target["id"], forbidden)
        for citation in t064.citations_of(answered.json):
            assert t055.get_citation(owner, citation["chunk_id"]).status == 200
    finally:
        st.sql(
            stack.dbname,
            "UPDATE platform.artifacts SET owner_id = "
            f"{st.quote_literal(owner.principal_id)}::uuid "
            f"WHERE id = {st.quote_literal(artifact['id'])}::uuid",
        )

    # The cache is derived state: clear the entry stored while the body was
    # foreign, then prove the restored body is readable through all three paths.
    t067.delete_cache_rows(stack.dbname, principal_id=owner.principal_id)
    healed = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert target["id"] in item_ids(healed.json), healed.json["items"]
    assert healed.json["retrieval"]["cache"]["stored"] is True
    healed_hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert healed_hit.json["retrieval"]["cache"]["hit"] is True
    assert target["id"] in item_ids(healed_hit.json)

    # (3) A document tombstone immediately blocks all three paths. The cache key
    # changes with the source digest, so the old row cannot hit even though
    # nothing evicted it; a retry is an empty answer, never the old body.
    healed_key = t067.cache_row_with_prefix(
        stack.dbname, healed_hit.json["retrieval"]["cache"]["key_prefix"]
    )["cache_key"]
    t064.revoke_document(stack.dbname, documents[0])
    assert t055.document_row(stack.dbname, documents[0])["state"] == "deleted"
    assert t067.cache_row_by_key(stack.dbname, healed_key) is not None, (
        "the lost-eviction adversary: the old row is still live"
    )
    revoked = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert revoked.status == 200, revoked.text
    assert revoked.json["items"] == [], revoked.json["items"]
    assert revoked.json["retrieval"]["cache"]["hit"] is False
    assert revoked.json["retrieval"]["cache"]["key_prefix"] != healed_hit.json[
        "retrieval"
    ]["cache"]["key_prefix"]
    assert_safe_not_found(t055.get_citation(owner, target["id"]), artifact)
    revoked_answer = t064.answer(owner, base_id, QUERY, top_k=20)
    assert revoked_answer.status == 200, revoked_answer.text
    assert revoked_answer.json["outcome"] == t064.OUTCOME_NO_RESULTS
    assert revoked_answer.json["answer"]["text"] == ""
    assert revoked_answer.json["answer"]["citations"] == []


# ---------------------------------------------------------------------------
# answer revalidation: a citation invalidated while the answer is generated
# ---------------------------------------------------------------------------
def test_c302_answer_revalidates_each_citation_before_returning(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    assert chunk_artifact(stack, target["id"])

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert warm.json["retrieval"]["cache"]["stored"] is True

    with t064.faulted_answer_server(
        api_server, stack, tmp_path / "fault-slow", "slow=5s"
    ) as server:
        session = login_on(server)
        # The first answer is served from a retrieval *cache hit*; generation and
        # the per-citation re-check still run on every request, so a final answer
        # is never stored or replayed.
        first = t064.answer(session, base_id, QUERY, top_k=20)
        assert first.status == 200, first.text
        assert first.json["outcome"] == t064.OUTCOME_ANSWERED
        assert first.json["retrieval"]["cache"]["hit"] is True
        citations = t064.citations_of(first.json)
        assert citations
        victim_id = citations[0]["chunk_id"]
        victim_artifact = chunk_artifact(stack, victim_id)
        victim_size = int(victim_artifact["size_bytes"])

        result: dict[str, object] = {}

        def ask() -> None:
            result["response"] = t064.answer(session, base_id, QUERY, top_k=20)

        worker = threading.Thread(target=ask, daemon=True)
        worker.start()
        server.wait_for_log("knowledge answer stub: slow fault waiting", timeout=30.0)
        # The sealed bytes and their digest are untouched; only the authoritative
        # size changes. The old re-check compared text only and would have
        # returned the answer with a citation that can no longer be verified.
        set_integrity_metadata(
            stack.dbname, victim_artifact["id"], size_bytes=victim_size + 1
        )
        try:
            worker.join(timeout=30.0)
            assert not worker.is_alive(), "the answer request never finished"
        finally:
            set_integrity_metadata(
                stack.dbname, victim_artifact["id"], size_bytes=victim_size
            )

    response = result["response"]
    assert response.status == 409, response.text
    error = response.json["error"]
    assert error["code"] == t064.OUTCOME_CITATION_UNREADABLE, error
    assert error["details"]["outcome"] == t064.OUTCOME_CITATION_UNREADABLE
    assert error["details"]["chunk_id"] == victim_id
    assert "answer" not in response.json
    assert "citations" not in response.json

    # The retrieval cache still stores retrieval items only (the documented item
    # shape); no answer text was added to it by either request.
    for row in cache_rows(stack, owner, base_id):
        for item in row["items"]:
            assert set(item) == t113.STORED_ITEM_FIELDS, item

    # Once the metadata is authoritative again the citation opens and a fresh
    # answer is generated and revalidated.
    assert t055.get_citation(owner, victim_id).status == 200
    healed = t064.answer(owner, base_id, QUERY, top_k=20)
    assert healed.status == 200, healed.text
    assert healed.json["outcome"] == t064.OUTCOME_ANSWERED


# ---------------------------------------------------------------------------
# embedding rules: the cache cannot hide them, citation open does not need them
# ---------------------------------------------------------------------------
def test_c302_citation_open_does_not_require_the_current_embedding_model(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    expected_text = seeded_text(stack, artifact)

    warm = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    v1_row = t067.cache_row_with_prefix(
        stack.dbname, warm.json["retrieval"]["cache"]["key_prefix"]
    )

    # A second real deployment with the other compiled stub revision: retrieval
    # must match the active index's model (here it does not, so the index is
    # excluded and the result is empty), but an existing citation must still open.
    with t061.server_with_env(
        api_server, stack, tmp_path / "v2-certs", extra=t061.STUB_V2_ENV
    ) as (server_v2, _certs_v2):
        v2 = login_on(server_v2)
        switched = t055.retrieval_query(v2, base_id, QUERY, top_k=20)
        assert switched.status == 200, switched.text
        assert switched.json["items"] == [], switched.json["items"]
        switched_index = switched.json["retrieval"]["index"]
        assert switched_index["embedding_model_revision"] == t061.EMBEDDING_REVISION_V2
        assert int(switched_index["active_indexes"]) == 0
        assert int(switched_index["excluded_indexes"]) == 1
        assert switched.json["retrieval"]["cache"]["hit"] is False
        # The old deployment's entry is not addressable under the new model key.
        assert len(cache_rows(stack, owner, base_id)) == 1
        assert int(t067.cache_row_by_key(stack.dbname, v1_row["cache_key"])["hit_count"]) == 0

        opened = t055.get_citation(v2, target["id"])
        assert opened.status == 200, opened.text
        opened_bytes = opened.json["text"].encode("utf-8")
        assert opened.json["text"] == expected_text
        assert t055.sha256_hex(opened_bytes) == artifact["sha256"]
        assert len(opened_bytes) == int(artifact["size_bytes"])
        # An answer over the new vector space has nothing to ground it.
        assert (
            t064.answer(v2, base_id, QUERY, top_k=20).json["outcome"]
            == t064.OUTCOME_NO_RESULTS
        )

    # The cache can never hide a missing embedding provider: the real-provider
    # deployment fails before a cache lookup, even with a live valid entry.
    with t061.server_with_env(
        api_server, stack, tmp_path / "real-provider-certs", extra=t061.REAL_PROVIDER_ENV
    ) as (server_real, _certs_real):
        real = login_on(server_real)
        unavailable = t055.retrieval_query(real, base_id, QUERY, top_k=20)
        assert unavailable.status == 503, unavailable.text
        assert unavailable.json["error"]["code"] == "provider_not_configured"
        assert t064.answer(real, base_id, QUERY, top_k=20).status == 503
        # The citation path touches no embedder, so the existing citation opens.
        assert t055.get_citation(real, target["id"]).status == 200

    assert int(t067.cache_row_by_key(stack.dbname, v1_row["cache_key"])["hit_count"]) == 0


# ---------------------------------------------------------------------------
# the normal path, cache on vs cache off: same retrieval, open and answer
# ---------------------------------------------------------------------------
def test_c302_normal_retrieval_open_answer_and_cache_hit_agree_with_cache_off(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, _artifacts, documents = setup_kb(stack)
    target = t055.chunk_rows(stack.dbname, documents[0])[0]
    artifact = chunk_artifact(stack, target["id"])
    expected_text = seeded_text(stack, artifact)

    first = t067.warm_cache(owner, base_id, QUERY, top_k=20)
    assert first.json["retrieval"]["cache"]["stored"] is True
    opened = t055.get_citation(owner, target["id"])
    assert opened.status == 200, opened.text
    opened_bytes = opened.json["text"].encode("utf-8")
    assert opened.json["text"] == expected_text
    assert t055.sha256_hex(opened_bytes) == artifact["sha256"]
    assert len(opened_bytes) == int(artifact["size_bytes"])

    answered = t064.answer(owner, base_id, QUERY, top_k=20)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_ANSWERED

    # A repeated query is a real cache hit: the same chunk set, every item
    # re-checked against the sealed bytes before it is returned.
    hit = t055.retrieval_query(owner, base_id, QUERY, top_k=20)
    assert hit.status == 200, hit.text
    hit_cache = hit.json["retrieval"]["cache"]
    assert hit_cache["hit"] is True, hit_cache
    assert int(hit_cache["validated_chunks"]) == len(hit.json["items"])
    assert hit.json["items"] == first.json["items"]

    with t067.cache_disabled_server(
        api_server, stack, tmp_path / "cache-off-normal"
    ) as server:
        off = login_on(server)
        off_retrieval = t055.retrieval_query(off, base_id, QUERY, top_k=20)
        assert off_retrieval.status == 200, off_retrieval.text
        off_cache = off_retrieval.json["retrieval"]["cache"]
        assert off_cache["enabled"] is False
        assert off_cache["hit"] is False and off_cache["stored"] is False
        assert off_retrieval.json["items"] == first.json["items"]
        off_open = t055.get_citation(off, target["id"])
        assert off_open.status == 200, off_open.text
        assert off_open.json["text"] == opened.json["text"]
        off_answer = t064.answer(off, base_id, QUERY, top_k=20)
        assert off_answer.status == 200, off_answer.text
        assert answer_signature(off_answer.json) == answer_signature(answered.json)

    # The cache-off process never wrote to the cache.
    rows = cache_rows(stack, owner, base_id)
    assert len(rows) == 1, rows