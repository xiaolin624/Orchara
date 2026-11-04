"""T067 acceptance: a cache hit never returns deleted or revoked content.

Every test drives the real stack: a throwaway PostgreSQL database migrated by the
real ``cmd/admin`` chain, the compiled ``cmd/api`` binary behind real HTTP with
real sealed chunk bodies, the real ``document_ingest`` maintenance process, and —
for the comparison/disabled cases — a second real API process with its own
deployment switch. The API is never trusted as its own oracle: cache rows, chunk
rows, document tombstones and sealed hashes are read from PostgreSQL and the
object directory.

The "lost eviction" adversary is explicit: a stale row is kept live under the
current key (or injected into the entry the server just stored), and the request
must still never return its body.
"""

from __future__ import annotations

import subprocess

from support import f00b, t055, t064, t067
from support import stack as st
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

QUERY = "华北地区十月的销售额是多少"
DOC_B = "# 退款制度\n\n" + "退款需在订单完成后 30 天内提交申请，逾期不再受理。\n" * 12
ALICE_MARKER = "ALICE-PRIVATE-BODY-1234.50"
BOB_MARKER = "BOB-PRIVATE-BODY-987.25"


def test_a_repeated_query_reuses_one_qualified_result(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "reuse", contents=[t055.SAMPLE_DOCUMENT]
    )

    first = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    payload = first.json
    cache = payload["retrieval"]["cache"]
    assert cache["enabled"] is True, cache
    assert cache["hit"] is False, cache
    assert cache["stored"] is True, cache
    assert int(cache["validated_chunks"]) == 0, cache
    assert int(cache["ttl_seconds"]) == 900, cache
    assert len(cache["key_prefix"]) == 16, cache
    assert payload["retrieval"]["acl_epoch"] == 1

    # The row stores every key component, so a hit is debuggable and can be
    # re-derived from the row alone.
    rows = t067.cache_rows(stack.dbname, principal_id=alice.principal_id, base_id=base_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["cache_key"].startswith(cache["key_prefix"]), row
    assert row["principal_id"] == alice.principal_id
    assert int(row["acl_epoch"]) == 1
    assert int(row["kb_revision"]) == 1
    assert row["embedding_model_revision"] == t067.EMBEDDING_REVISION
    assert row["canonical_query"] == QUERY
    assert int(row["top_k"]) == 3
    assert len(row["source_revision_digest"]) == 64
    assert int(row["source_document_count"]) == 1
    assert int(row["item_count"]) == len(payload["items"])
    assert row["items"] == [
        t067.stored_item(stack.dbname, item) for item in payload["items"]
    ]
    assert int(row["hit_count"]) == 0
    assert row["last_hit_at"] is None

    # Canonical query = the trimmed string T055 embeds; the surrounding whitespace
    # of the repeated request must not create a second entry.
    second = t055.retrieval_query(alice, base_id, "  " + QUERY + "\n", top_k=3)
    assert second.status == 200, second.text
    hit = second.json["retrieval"]["cache"]
    assert hit["enabled"] is True and hit["hit"] is True, hit
    assert hit["stored"] is False, hit
    assert int(hit["validated_chunks"]) == len(payload["items"]), hit
    assert hit["key_prefix"] == cache["key_prefix"]
    assert second.json["items"] == payload["items"]
    assert second.json["retrieval"]["acl_epoch"] == 1

    hit_row = t067.cache_row_with_prefix(stack.dbname, cache["key_prefix"])
    assert int(hit_row["hit_count"]) == 1
    assert hit_row["items"] == row["items"]
    assert len(t067.cache_rows(stack.dbname, principal_id=alice.principal_id)) == 1


def test_a_deleted_documents_live_cache_row_is_never_returned(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack,
        "deleted",
        contents=[t055.SAMPLE_DOCUMENT, DOC_B],
    )
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=10)
    old_prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    old_row = t067.cache_row_with_prefix(stack.dbname, old_prefix)
    old_key = old_row["cache_key"]
    assert any(item["document_id"] == documents[1] for item in old_row["items"])
    stale_item = next(
        item for item in old_row["items"] if item["document_id"] == documents[1]
    )

    # Revocation is authoritative in PostgreSQL first (the F07-shaped tombstone),
    # while the old cache row is explicitly NOT evicted: the eviction was lost.
    t064.revoke_document(stack.dbname, documents[1])
    assert t055.document_row(stack.dbname, documents[1])["state"] == "deleted"
    assert t067.cache_row_by_key(stack.dbname, old_key) is not None

    after = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert after.status == 200, after.text
    after_cache = after.json["retrieval"]["cache"]
    assert after_cache["hit"] is False, after_cache
    assert after_cache["key_prefix"] != old_prefix, after_cache
    assert all(item["document_id"] != documents[1] for item in after.json["items"])
    assert t067.POISON_BODY not in after.text
    # The stale row is still live (nothing evicted it), yet it did not hit.
    assert t067.cache_row_by_key(stack.dbname, old_key) is not None
    new_row = t067.cache_row_with_prefix(stack.dbname, after_cache["key_prefix"])
    assert new_row["cache_key"] != old_key

    # Harder adversary: inject the deleted document's stored item into the live
    # entry under the *current* key, so the key check cannot save us and only the
    # mandatory per-chunk re-check can.
    keep = [t067.stored_item(stack.dbname, item) for item in after.json["items"]]
    current_key, poisoned_items = t067.poison_live_entry(
        stack.dbname,
        key_prefix=after_cache["key_prefix"],
        stale_item=stale_item,
        keep_items=keep,
    )
    assert any(item["text"] == t067.POISON_BODY for item in poisoned_items)
    assert t067.cache_row_by_key(stack.dbname, current_key)["items"] == poisoned_items

    poisoned = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert poisoned.status == 200, poisoned.text
    poisoned_cache = poisoned.json["retrieval"]["cache"]
    assert poisoned_cache["hit"] is False, poisoned_cache
    assert poisoned_cache["discarded"] is True, poisoned_cache
    assert poisoned_cache["discard_reason"] == "item_unreadable", poisoned_cache
    assert t067.POISON_BODY not in poisoned.text
    assert all(item["document_id"] != documents[1] for item in poisoned.json["items"])
    assert poisoned.json["items"] == after.json["items"]

    # The discarded entry was replaced by a fresh, valid entry with no revoked body.
    fresh = t067.cache_row_by_key(stack.dbname, current_key)
    assert fresh is not None and int(fresh["hit_count"]) == 0
    assert all(item["text"] != t067.POISON_BODY for item in fresh["items"])
    assert all(item["document_id"] != documents[1] for item in fresh["items"])


def test_a_stored_body_is_never_served_to_another_principal(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack,
        "alice",
        login="t067.alice",
        contents=[("# 销售资料\n\n" + ALICE_MARKER + "\n").encode("utf-8")],
    )
    bob, bob_base_id, _bob_artifacts, _bob_documents = t067.setup_kb_with_documents(
        stack,
        "bob",
        login="t067.bob",
        contents=[("# 销售资料\n\n" + BOB_MARKER + "\n").encode("utf-8")],
    )

    alice_warm = t067.warm_cache(alice, base_id, QUERY, top_k=5)
    bob_warm = t067.warm_cache(bob, bob_base_id, QUERY, top_k=5)
    alice_prefix = alice_warm.json["retrieval"]["cache"]["key_prefix"]
    bob_prefix = bob_warm.json["retrieval"]["cache"]["key_prefix"]
    assert alice_prefix != bob_prefix
    alice_row = t067.cache_row_with_prefix(stack.dbname, alice_prefix)
    bob_row = t067.cache_row_with_prefix(stack.dbname, bob_prefix)
    assert alice_row["cache_key"] != bob_row["cache_key"]
    assert any(ALICE_MARKER in item["text"] for item in alice_row["items"])
    assert all(BOB_MARKER not in item["text"] for item in alice_row["items"])

    # The second user's repeated query hits only their own entry.
    bob_again = t055.retrieval_query(bob, bob_base_id, QUERY, top_k=5)
    assert bob_again.json["retrieval"]["cache"]["hit"] is True
    assert bob_again.json["retrieval"]["cache"]["key_prefix"] == bob_prefix
    assert int(t067.cache_row_with_prefix(stack.dbname, alice_prefix)["hit_count"]) == 0

    # Another principal cannot even ask over Alice's knowledge base, cache or not.
    refused = t055.retrieval_query(bob, base_id, QUERY, top_k=5)
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found"
    assert "items" not in refused.json and "retrieval" not in refused.json

    # Hardest adversary: a live entry under Bob's current key that carries Alice's
    # chunk and body. The per-chunk owner/document re-check must discard it.
    alice_item = next(
        t067.stored_item(stack.dbname, item)
        for item in alice_warm.json["items"]
        if ALICE_MARKER in item["text"]
    )
    keep = [t067.stored_item(stack.dbname, item) for item in bob_warm.json["items"]]
    bob_key, _poisoned = t067.poison_live_entry(
        stack.dbname,
        key_prefix=bob_prefix,
        stale_item=alice_item,
        keep_items=keep,
    )
    poisoned = t055.retrieval_query(bob, bob_base_id, QUERY, top_k=5)
    assert poisoned.status == 200, poisoned.text
    poisoned_cache = poisoned.json["retrieval"]["cache"]
    assert poisoned_cache["hit"] is False and poisoned_cache["discarded"] is True
    assert ALICE_MARKER not in poisoned.text
    assert all(ALICE_MARKER not in item["text"] for item in poisoned.json["items"])
    assert any(BOB_MARKER in item["text"] for item in poisoned.json["items"])
    fresh = t067.cache_row_by_key(stack.dbname, bob_key)
    assert fresh is not None
    assert all(ALICE_MARKER not in item["text"] for item in fresh["items"])


def test_an_expired_entry_is_a_miss_and_is_replaced(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "expired", contents=[t055.SAMPLE_DOCUMENT]
    )
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    row = t067.cache_row_with_prefix(stack.dbname, prefix)
    # A TTL is garbage collection, not authorization: expire the live row without
    # deleting it and prove the request re-derives the same key but still misses.
    t067.expire_cache_row(stack.dbname, row["cache_key"])

    response = t055.retrieval_query(alice, base_id, QUERY, top_k=3)
    cache = response.json["retrieval"]["cache"]
    assert cache["hit"] is False and cache["discarded"] is True, cache
    assert cache["discard_reason"] == "expired", cache
    assert cache["stored"] is True, cache
    assert response.json["items"] == warm.json["items"]
    fresh = t067.cache_row_with_prefix(stack.dbname, prefix)
    assert int(fresh["hit_count"]) == 0
    assert fresh["items"] == [t067.stored_item(stack.dbname, item) for item in response.json["items"]]


def test_an_authorization_revision_change_invalidates_a_live_entry(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "acl", contents=[t055.SAMPLE_DOCUMENT]
    )
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    old_row = t067.cache_row_with_prefix(stack.dbname, prefix)

    # This slice has no sharing operation; the effective authorization revision is
    # the knowledge-base revision (documented stand-in). A later permission change
    # bumps it in the authoritative row first, exactly as this update does.
    st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_bases SET revision = revision + 1 "
        f"WHERE id = {st.quote_literal(base_id)}::uuid",
    )
    assert t067.cache_row_by_key(stack.dbname, old_row["cache_key"]) is not None

    response = t055.retrieval_query(alice, base_id, QUERY, top_k=3)
    cache = response.json["retrieval"]["cache"]
    assert cache["hit"] is False, cache
    assert cache["key_prefix"] != prefix, cache
    assert cache["discarded"] is False, cache
    assert response.json["retrieval"]["acl_epoch"] == 2
    assert response.json["items"] == warm.json["items"]
    # The old row stayed (eviction lost) and still did not hit.
    assert t067.cache_row_by_key(stack.dbname, old_row["cache_key"]) is not None
    new_row = t067.cache_row_with_prefix(stack.dbname, cache["key_prefix"])
    assert int(new_row["acl_epoch"]) == 2


def test_a_document_revision_change_does_not_hit_the_old_revision(t055_stack) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "revision", contents=[t055.SAMPLE_DOCUMENT]
    )
    document_id = documents[0]
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    old_row = t067.cache_row_with_prefix(stack.dbname, prefix)
    old_chunk_ids = {item["chunk_id"] for item in warm.json["items"]}
    assert old_chunk_ids
    old_revisions = {int(item["document_revision"]) for item in warm.json["items"]}
    assert old_revisions == {1}

    # Simulate the T061 activation switch: stage revision 2 chunks (real rows, real
    # sealed bodies) and move the current/active pointer in the authoritative row.
    st.sql(
        stack.dbname,
        "INSERT INTO platform.knowledge_chunks "
        "(id, document_id, document_revision, chunk_no, body_artifact_id, citation, "
        " embedding_model_revision, embedding, embedding_dimension) "
        "SELECT gen_random_uuid(), document_id, 2, chunk_no, body_artifact_id, "
        "       citation || jsonb_build_object('document_revision', 2), "
        "       embedding_model_revision, embedding, embedding_dimension "
        "FROM platform.knowledge_chunks "
        f"WHERE document_id = {st.quote_literal(document_id)}::uuid "
        "AND document_revision = 1",
    )
    st.sql(
        stack.dbname,
        "UPDATE platform.knowledge_documents "
        "SET revision = 2, active_revision = 2 "
        f"WHERE id = {st.quote_literal(document_id)}::uuid",
    )
    assert t067.cache_row_by_key(stack.dbname, old_row["cache_key"]) is not None

    response = t055.retrieval_query(alice, base_id, QUERY, top_k=3)
    cache = response.json["retrieval"]["cache"]
    assert cache["hit"] is False, cache
    assert cache["key_prefix"] != prefix, cache
    assert response.json["items"], response.text
    assert {int(item["document_revision"]) for item in response.json["items"]} == {2}
    assert not ({item["chunk_id"] for item in response.json["items"]} & old_chunk_ids)
    assert t067.cache_row_by_key(stack.dbname, old_row["cache_key"]) is not None


def test_the_deployment_switch_disables_reuse_and_still_answers(t055_stack, api_server, tmp_path) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "disabled", contents=[t055.SAMPLE_DOCUMENT]
    )

    with t067.cache_disabled_server(api_server, stack, tmp_path / "cache-off-certs") as server:
        disabled = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        first = t055.retrieval_query(disabled, base_id, QUERY, top_k=3)
        second = t055.retrieval_query(disabled, base_id, QUERY, top_k=3)
    for response in (first, second):
        assert response.status == 200, response.text
        cache = response.json["retrieval"]["cache"]
        assert cache["enabled"] is False, cache
        assert cache["hit"] is False and cache["stored"] is False, cache
        assert cache["discarded"] is False, cache
    assert first.json["items"] == second.json["items"]
    assert t067.cache_count(stack.dbname, principal_id=alice.principal_id) == 0

    enabled = t055.retrieval_query(alice, base_id, QUERY, top_k=3)
    assert enabled.json["retrieval"]["cache"]["enabled"] is True
    assert enabled.json["retrieval"]["cache"]["stored"] is True
    assert enabled.json["items"] == first.json["items"]


def test_an_unknown_cache_switch_refuses_startup(t055_stack) -> None:
    stack = t055_stack
    env = st.admin_env(stack.dsn)
    env["ORCHARA_HTTP_ADDR"] = f"127.0.0.1:{st.free_port()}"
    env["ORCHARA_WEB_DIR"] = str(st.WEB_DIR)
    env[t067.CACHE_ENV] = "maybe"
    completed = subprocess.run(
        [str(stack.binaries["api"])],
        cwd=st.PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode != 0, completed.stdout
    assert t067.CACHE_ENV in (completed.stdout + completed.stderr)
