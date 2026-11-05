"""F11/T069: memory and knowledge-document deletion.

The same shared foundation covers both resources. What changes is the tombstone
column/state and the read endpoints that must refuse immediately: the memory
detail/list and runtime recall (checked here through the contract read), and the
document detail/list, retrieval, citation and grounded answer (the cache is
warmed before the delete so the test exercises the hit re-check, not just the
cache-key miss).
"""

from __future__ import annotations

from support import t055, t058, t064, t069
from support import stack as st


def test_delete_memory_answers_202_and_stops_the_contract_reads(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    memory = t069.create_memory(
        stack, owner.creator, owner.application_id, "t069 memory marker: paper invoices"
    )
    before = t069.memory_row(stack.dbname, memory["id"])
    assert before["revision"] == 1 and before["deleted_at"] is None
    epoch_before = t058.epoch_of(stack.dbname, owner.creator.principal_id, owner.application_id)

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_MEMORY, memory["id"])
    )
    assert job["resource_id"] == memory["id"]

    after = t069.memory_row(stack.dbname, memory["id"])
    assert after["deleted_at"] is not None, after
    assert after["revision"] == 2, after
    epoch_after = t058.epoch_of(
        stack.dbname, owner.creator.principal_id, owner.application_id
    )
    assert epoch_after > epoch_before, (epoch_before, epoch_after)

    st.assert_error(t058.detail(owner.creator, memory["id"]), 404, t069.ERROR_RESOURCE_NOT_FOUND)
    assert t058.list_memories(owner.creator).json["items"] == []

    stored = t069.deletion_job(stack.dbname, job["id"])
    assert stored is not None
    assert stored["resource_type"] == t069.RESOURCE_MEMORY
    assert stored["deletion_revision"] == 2
    assert t069.remaining_scopes(stored) == list(t069.SCOPES)
    details = t069.scope_details(stored)
    object_items = details["object_store"]["items"]
    assert any(
        item.get("artifact_id") == before["body_artifact_id"] for item in object_items
    ), object_items

    fetched = t069.get_deletion(owner.creator, job["id"])
    assert fetched.status == 200, fetched.text
    assert fetched.json["remaining_scopes"] == list(t069.SCOPES)

    again = t069.delete_resource(owner.creator, t069.RESOURCE_MEMORY, memory["id"])
    assert again.status == 202 and again.json == job, (again.json, job)
    assert len(
        t069.deletion_jobs(
            stack.dbname, resource_type=t069.RESOURCE_MEMORY, resource_id=memory["id"]
        )
    ) == 1
    # The rotation is exactly one revision: the repeat does not bump it again.
    assert t069.memory_row(stack.dbname, memory["id"])["revision"] == 2


def test_delete_document_stops_retrieval_citations_answers_and_the_cache(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    alice = t069.owner(stack)
    content = (
        "退款政策：退款需在订单完成后 30 天内提交申请。marker-t069-document\n"
        "第二次出现：退款申请需要订单号。marker-t069-document\n"
    )
    base_id, document_id = t069.ready_document(
        stack, alice.creator, content, log_path=tmp_path / "maintenance-t069.log"
    )
    row_before = t069.document_row(stack.dbname, document_id)
    assert row_before["state"] == "ready" and row_before["deleted_at"] is None

    # Warm the retrieval cache with the live document: the pre-delete query must
    # really return the content and store an entry.
    query = "退款申请"
    warm = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert warm.status == 200, warm.text
    assert warm.json["items"], warm.json
    warm_cache = warm.json["retrieval"]["cache"]
    assert warm_cache["stored"] is True, warm_cache
    chunk_id = warm.json["items"][0]["chunk_id"]

    accepted = t069.deletion_of(
        t069.delete_resource(alice.creator, t069.RESOURCE_DOCUMENT, document_id)
    )
    assert accepted["resource_id"] == document_id

    row_after = t069.document_row(stack.dbname, document_id)
    assert row_after["deleted_at"] is not None, row_after
    assert row_after["state"] == "deleted", row_after
    assert row_after["revision"] == row_before["revision"] + 1, (row_before, row_after)

    st.assert_error(
        t055.get_document(alice.creator, document_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t055.list_documents(alice.creator, base_id).json["items"] == []
    st.assert_error(
        t055.get_citation(alice.creator, chunk_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )

    after = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert after.status == 200, after.text
    assert after.json["items"] == [], after.json
    assert after.json["retrieval"]["cache"]["hit"] is False, after.json["retrieval"]["cache"]

    answer = t064.answer(alice.creator, base_id, query)
    assert answer.status == 200, answer.text
    assert answer.json["outcome"] == t064.OUTCOME_NO_RESULTS, answer.json

    stored = t069.deletion_job(stack.dbname, accepted["id"])
    assert stored["resource_type"] == t069.RESOURCE_DOCUMENT
    assert t069.remaining_scopes(stored) == list(t069.SCOPES)
    details = t069.scope_details(stored)
    assert row_before["source_artifact_id"] in {
        item.get("artifact_id") for item in details["object_store"]["items"]
    }
    assert details["retrieval_cache"]["document_id"] == document_id

    again = t069.delete_resource(
        alice.creator, t069.RESOURCE_DOCUMENT, document_id
    )
    assert again.status == 202 and again.json == accepted, (again.json, accepted)
    assert len(
        t069.deletion_jobs(
            stack.dbname,
            resource_type=t069.RESOURCE_DOCUMENT,
            resource_id=document_id,
        )
    ) == 1
    assert t069.document_row(stack.dbname, document_id)["revision"] == row_after["revision"]


def test_conversation_delete_is_wired_but_has_no_authority_until_t074(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)

    for conversation_id in ("not-a-uuid", "11111111-1111-4111-8111-111111111111"):
        st.assert_error(
            t069.delete_resource(
                owner.creator, t069.RESOURCE_CONVERSATION, conversation_id
            ),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )
    # Nothing was recorded for a resource type that has no table yet.
    assert t069.deletion_jobs(stack.dbname) == []


def test_memory_delete_refuses_a_service_credential_before_any_store_write(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    memory = t069.create_memory(
        stack, owner.creator, owner.application_id, "t069 service refusal marker"
    )
    service = t058.service_client(stack)
    st.assert_error(
        t069.delete_resource(
            service, t069.RESOURCE_MEMORY, memory["id"], client=service
        ),
        403,
        t069.ERROR_PERSONAL_SCOPE,
    )
    row = t069.memory_row(stack.dbname, memory["id"])
    assert row["revision"] == 1 and row["deleted_at"] is None
    assert t069.deletion_jobs(stack.dbname, resource_id=memory["id"]) == []
