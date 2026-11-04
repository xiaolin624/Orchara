"""T067 acceptance: the four RAG failure classes stay individually localized.

``TESTING-AND-DATASETS.md`` §7 requires four injected failures to be attributed to
their own stage, not collapsed into one score: missing target source, wrong answer,
fabricated citation and unauthorized chunk. This file runs every class against the
real stack with the retrieval cache **on** (and compares two of them against a
real cache-disabled process), so a cached hit can never hide which stage failed.

The wrong-answer and fabricated-citation classes use the documented dev-stub
answer-generator faults (ADR 0012 D2; `wrong-answer` added by T067). The faults are
deployment configuration, only honoured in a stub profile, and can only make the
server refuse or mislabel text -- they cannot bypass citation validation.
"""

from __future__ import annotations

from support import f00b, t055, t064, t067
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

QUERY = "华北地区十月的销售额是多少"
WRONG_ANSWER_SENTINEL = "T067-WRONG-ANSWER-NOT-IN-ANY-CHUNK"
BOB_BODY = b"BOB-PRIVATE-BODY-T067-UNAUTHORIZED"


def test_a_missing_target_source_stays_a_retrieval_stage_empty_result(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "missing-source", contents=[t055.SAMPLE_DOCUMENT]
    )
    empty_base_id = t055.create_knowledge_base(alice, "t067 empty source").json["id"]

    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    assert warm.json["items"]
    answered = t064.answer(alice, base_id, QUERY, top_k=3)
    assert answered.json["outcome"] == t064.OUTCOME_ANSWERED
    assert answered.json["retrieval"]["cache"]["hit"] is True

    # The target source is gone: the retrieval stage answers with nothing, and the
    # old live cache row (eviction lost) does not hide that.
    t064.revoke_document(stack.dbname, documents[0])
    assert t067.cache_count(stack.dbname, principal_id=alice.principal_id) == 1
    gone = t064.answer(alice, base_id, QUERY, top_k=3)
    assert gone.status == 200, gone.text
    assert gone.json["outcome"] == t064.OUTCOME_NO_RESULTS
    assert gone.json["answer"]["citations"] == []
    assert gone.json["retrieval"]["chunk_ids"] == []
    assert gone.json["retrieval"]["cache"]["hit"] is False

    # A knowledge base that never had the source is the same stage.
    never = t064.answer(alice, empty_base_id, QUERY, top_k=3)
    assert never.json["outcome"] == t064.OUTCOME_NO_RESULTS
    assert never.json["retrieval"]["chunk_ids"] == []

    # The cache-disabled deployment attributes the class identically.
    with t067.cache_disabled_server(api_server, stack, tmp_path / "missing-source-off") as server:
        disabled = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        for base in (base_id, empty_base_id):
            response = t064.answer(disabled, base, QUERY, top_k=3)
            assert response.json["outcome"] == t064.OUTCOME_NO_RESULTS
            assert response.json["retrieval"]["cache"]["enabled"] is False


def test_a_wrong_answer_is_localized_to_generation_while_the_cache_is_on(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "wrong-answer", contents=[t055.SAMPLE_DOCUMENT]
    )
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    warm_ids = [item["chunk_id"] for item in warm.json["items"]]
    assert all(WRONG_ANSWER_SENTINEL not in item["text"] for item in warm.json["items"])

    with t067.server_with_env(
        api_server, stack, tmp_path / "wrong-answer-on", faults="wrong-answer"
    ) as server:
        faulted = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        wrong = t064.answer(faulted, base_id, QUERY, top_k=3)
    assert wrong.status == 200, wrong.text
    assert wrong.json["outcome"] == t064.OUTCOME_ANSWERED
    # The retrieval (and the cache entry it served) is the correct one; only the
    # generated prose is wrong. That is the generation stage, not retrieval or ACL.
    assert wrong.json["retrieval"]["cache"]["hit"] is True
    assert wrong.json["retrieval"]["chunk_ids"] == warm_ids
    assert WRONG_ANSWER_SENTINEL in wrong.json["answer"]["text"]
    citations = t064.citations_of(wrong.json)
    assert citations
    for citation in citations:
        assert WRONG_ANSWER_SENTINEL not in citation["text"]
        assert t055.get_citation(alice, citation["chunk_id"]).status == 200

    # Cache off: same stage attribution (answered + ungrounded text), so the cache
    # is not what produced or masked the wrong answer.
    with t067.server_with_env(
        api_server,
        stack,
        tmp_path / "wrong-answer-off",
        cache="off",
        faults="wrong-answer",
    ) as server:
        disabled = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        off = t064.answer(disabled, base_id, QUERY, top_k=3)
    assert off.json["outcome"] == t064.OUTCOME_ANSWERED
    assert off.json["retrieval"]["cache"]["enabled"] is False
    assert off.json["retrieval"]["chunk_ids"] == warm_ids
    assert WRONG_ANSWER_SENTINEL in off.json["answer"]["text"]


def test_a_fabricated_citation_is_localized_to_citation_validation_with_the_cache_on(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    alice, base_id, _artifacts, _documents = t067.setup_kb_with_documents(
        stack, "fabricated", contents=[t055.SAMPLE_DOCUMENT]
    )
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=3)
    prefix = warm.json["retrieval"]["cache"]["key_prefix"]
    assert int(t067.cache_row_with_prefix(stack.dbname, prefix)["hit_count"]) == 0

    with t067.server_with_env(
        api_server, stack, tmp_path / "fabricated-on", faults="foreign-chunk"
    ) as server:
        faulted = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        refused = t064.answer(faulted, base_id, QUERY, top_k=3)
    assert refused.status == 422, refused.text
    assert refused.json["error"]["code"] == t064.OUTCOME_CITATION_NOT_FOUND
    assert refused.json["error"]["details"]["chunk_id"] == t064.FAULT_FOREIGN_CHUNK_ID
    assert "answer" not in refused.json and "citations" not in refused.json
    # The refused request really used the live cache entry (the error envelope has
    # no retrieval metadata, so the row's hit counter is the evidence).
    assert int(t067.cache_row_with_prefix(stack.dbname, prefix)["hit_count"]) == 1

    with t067.server_with_env(
        api_server, stack, tmp_path / "fabricated-off", cache="off", faults="foreign-chunk"
    ) as server:
        disabled = t064.login_on(server, "t067.alice", f00b.USER_PASSWORD)
        off = t064.answer(disabled, base_id, QUERY, top_k=3)
    assert off.status == 422, off.text
    assert off.json["error"]["code"] == t064.OUTCOME_CITATION_NOT_FOUND


def test_an_unauthorized_chunk_is_localized_to_the_acl_stage_with_the_cache_on(
    t055_stack,
) -> None:
    stack = t055_stack
    admin = f00b.admin_session(stack)
    alice, base_id, _artifacts, documents = t067.setup_kb_with_documents(
        stack, "unauthorized", contents=[t055.SAMPLE_DOCUMENT]
    )
    document_id = documents[0]
    bob = f00b.invite_and_activate(stack, admin, "t067.bob.acl", password=f00b.USER_PASSWORD)

    # The cache is warm and the entry is live before the unauthorized row exists.
    warm = t067.warm_cache(alice, base_id, QUERY, top_k=10)
    prefix = warm.json["retrieval"]["cache"]["key_prefix"]

    # A real ready artifact owned by Bob, and a real chunk row in Alice's ready
    # document pointing at it: only the SQL owner predicate stands in the way.
    bob_artifact_id = t055.prepare_source(stack, bob, BOB_BODY)
    bob_artifact = t055.artifact_row(stack.dbname, bob_artifact_id)
    assert bob_artifact["owner_id"] == bob.principal_id
    template = t055.chunk_rows(stack.dbname, document_id, 1)[0]
    citation = t055.citation_of(template)
    citation["chunk_no"] = 999
    forged_id = f00b.new_uuid()
    t064.insert_forged_chunk(
        stack.dbname,
        chunk_id=forged_id,
        document_id=document_id,
        document_revision=1,
        chunk_no=999,
        body_artifact_id=bob_artifact_id,
        citation=citation,
        template_chunk_id=template["id"],
    )
    assert t064.chunk_row(stack.dbname, forged_id)["body_artifact_id"] == bob_artifact_id

    # Inject the forged chunk into the live entry under its current key: the key
    # is unchanged (chunk rows are not part of the digest), so only the per-chunk
    # ACL re-check can keep another principal's body out.
    forged_item = {
        "chunk_id": forged_id,
        "document_id": document_id,
        "document_revision": 1,
        "chunk_no": 999,
        "span": citation["span"],
        "citation": citation,
        "body_artifact_id": bob_artifact_id,
        "body_sha256": bob_artifact["sha256"],
        "text": BOB_BODY.decode("utf-8"),
        "score": 0.0,
    }
    keep = [t067.stored_item(stack.dbname, item) for item in warm.json["items"]]
    key, _poisoned = t067.poison_live_entry(
        stack.dbname, key_prefix=prefix, stale_item=forged_item, keep_items=keep
    )

    retrieved = t055.retrieval_query(alice, base_id, QUERY, top_k=10)
    assert retrieved.status == 200, retrieved.text
    cache = retrieved.json["retrieval"]["cache"]
    assert cache["hit"] is False and cache["discarded"] is True, cache
    assert cache["discard_reason"] == "item_unreadable", cache
    assert all(item["chunk_id"] != forged_id for item in retrieved.json["items"])
    assert BOB_BODY.decode("utf-8") not in retrieved.text
    # The freshly stored entry no longer carries the forged row.
    fresh = t067.cache_row_by_key(stack.dbname, key)
    assert fresh is not None
    assert all(item["chunk_id"] != forged_id for item in fresh["items"])

    for caller in (alice, bob):
        refused = t055.get_citation(caller, forged_id)
        assert refused.status == 404, refused.text
        assert "text" not in refused.json

    answered = t064.answer(alice, base_id, QUERY, top_k=10)
    assert answered.status == 200, answered.text
    assert answered.json["outcome"] == t064.OUTCOME_ANSWERED
    assert all(
        citation["chunk_id"] != forged_id for citation in t064.citations_of(answered.json)
    )
    for citation in t064.citations_of(answered.json):
        assert t055.get_citation(alice, citation["chunk_id"]).status == 200
