"""T061 acceptance: a rebuild creates an independent index version and activates it.

The real path: ingestDocument -> ready (index v1) -> POST
/knowledge-documents/{id}/reindex with a new sealed source artifact -> the real
maintenance handler builds index v2 for source revision 2 -> the completion guard
verifies the staged set and moves the activation pointer. The old index version
stays in the database as history but is no longer readable, and retrieval returns
only chunks that belong to the active index.
"""

from __future__ import annotations

from support import f00b, t055, t061, t064
from support.fixtures import api_server, t055_stack  # noqa: F401 - pytest fixtures

REVISED_DOCUMENT = (
    "# 销售资料（十一月修订）\n"
    "\n"
    "华南地区十一月的销售额是 7777.00 元，订单数为 88。\n"
    "\n"
    "退款政策：订单完成后 30 天内可申请退款；十一月的修订版没有改变这条规则。\n"
)


def test_a_reindex_with_a_new_source_activates_a_new_index_version(t055_stack) -> None:
    stack = t055_stack
    owner, base_id, artifact_v1, document_id = t061.setup_ready_document(
        stack, log_name="reindex-source"
    )

    first = t061.active_index(stack.dbname, document_id)
    assert first is not None, t055.document_row(stack.dbname, document_id)
    assert int(first["version"]) == 1
    assert int(first["document_revision"]) == 1
    assert first["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1
    assert first["state"] == "ready"
    assert int(first["chunk_count"]) > 0
    chunks_v1 = t061.chunk_rows_for_index(stack.dbname, first["id"])
    assert len(chunks_v1) == int(first["chunk_count"])
    assert all(chunk["index_version_id"] == first["id"] for chunk in chunks_v1)
    old_chunk_ids = {chunk["id"] for chunk in chunks_v1}

    revised_artifact = t055.prepare_source(stack, owner, REVISED_DOCUMENT)
    accepted = t061.reindex(owner, document_id, source_artifact_id=revised_artifact)
    assert accepted.status == 202, accepted.text
    payload = accepted.json
    assert payload["document_id"] == document_id
    assert payload["state"] == "scanning"
    assert int(payload["revision"]) == 2
    assert int(payload["index_version"]) == 2
    assert payload["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t061-reindex.log"):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))

    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "ready"
    assert int(row["revision"]) == 2
    assert int(row["active_revision"]) == 2
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and active["id"] == payload["index_version_id"]
    assert int(active["version"]) == 2
    assert int(active["document_revision"]) == 2
    assert active["state"] == "ready"
    assert active["error_code"] is None

    indexes = t061.index_rows(stack.dbname, document_id)
    assert [int(item["version"]) for item in indexes] == [1, 2]
    superseded = indexes[0]
    assert superseded["id"] == first["id"]
    assert superseded["state"] == "superseded", superseded
    assert superseded["activated_at"] is not None
    assert superseded["superseded_at"] is not None

    chunks_v2 = t061.chunk_rows_for_index(stack.dbname, active["id"])
    assert chunks_v2
    assert len(chunks_v2) == int(active["chunk_count"])
    assert {chunk["id"] for chunk in chunks_v2}.isdisjoint(old_chunk_ids)
    for chunk in chunks_v2:
        assert int(chunk["document_revision"]) == 2
        assert int(chunk["embedding_length"]) == t061.DIMENSION
        assert int(chunk["embedding_dimension"]) == t061.DIMENSION
        citation = t055.citation_of(chunk)
        assert citation["source_artifact_id"] == revised_artifact
        assert int(citation["document_revision"]) == 2

    # The job is bound to exactly this index version and finished successfully.
    job = t061.job_for_index(stack.dbname, document_id, active["id"])
    assert job["state"] == "succeeded", job
    assert job["expected_index_version_id"] == active["id"]
    assert int(job["expected_revision"]) == 2

    # Retrieval reads only the active index and reports which index/model it used.
    retrieved = t055.retrieval_query(owner, base_id, "华南地区十一月的销售额", top_k=20)
    assert retrieved.status == 200, retrieved.text
    items = retrieved.json["items"]
    assert items, retrieved.text
    active_chunk_ids = {chunk["id"] for chunk in chunks_v2}
    assert {item["chunk_id"] for item in items} <= active_chunk_ids
    assert not ({item["chunk_id"] for item in items} & old_chunk_ids)
    assert any("华南地区" in item["text"] for item in items)
    index_meta = retrieved.json["retrieval"]["index"]
    assert index_meta["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1
    assert int(index_meta["active_indexes"]) == 1
    assert int(index_meta["excluded_indexes"]) == 0

    # The superseded index's chunks are no longer openable as citations.
    opened_old = t055.get_citation(owner, chunks_v1[0]["id"])
    assert opened_old.status == 404, opened_old.text
    opened_new = t055.get_citation(owner, items[0]["chunk_id"])
    assert opened_new.status == 200, opened_new.text
    assert opened_new.json["text"] == items[0]["text"]


def test_switching_the_embedding_model_keeps_two_vector_spaces_apart(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, artifact_id, document_id = t061.setup_ready_document(
        stack, log_name="model-switch"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None
    chunks_v1 = t061.chunk_rows_for_index(stack.dbname, v1["id"])
    assert chunks_v1
    v1_chunk_ids = {chunk["id"] for chunk in chunks_v1}

    with t061.server_with_env(
        api_server, stack, tmp_path / "v2-certs", extra=t061.STUB_V2_ENV
    ) as (server_v2, certs_v2):
        session_v2 = t064.login_on(server_v2, "t061.alice", f00b.USER_PASSWORD)
        with t061.maintenance_pools(
            stack,
            certs_v2,
            kinds=t061.KIND_DOCUMENT_INGEST + "," + t061.KIND_ARTIFACT_FINALIZE,
            log_path=tmp_path / "maintenance-v2.log",
            extra_env=t061.STUB_V2_ENV,
        ):
            # Re-submitting the same source document under the new deployment
            # model creates a new index version at the *same* source revision; the
            # old vectors stay in their own index version.
            again = t055.ingest_document(session_v2, base_id, artifact_id)
            assert again.status == 202, again.text
            assert again.json["id"] == document_id
            view = t055.wait_for_document_state(session_v2, document_id, {"ready", "failed"})
        assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))

        indexes = t061.index_rows(stack.dbname, document_id)
        assert [int(item["version"]) for item in indexes] == [1, 2], indexes
        assert [item["state"] for item in indexes] == ["superseded", "ready"], indexes
        assert {item["document_revision"] for item in indexes} == {1}
        assert indexes[0]["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1
        assert indexes[1]["embedding_model_revision"] == t061.EMBEDDING_REVISION_V2
        active = t061.active_index(stack.dbname, document_id)
        assert active is not None and active["id"] == indexes[1]["id"]
        assert active["state"] == "ready"

        chunks_v2 = t061.chunk_rows_for_index(stack.dbname, active["id"])
        assert chunks_v2
        assert {chunk["id"] for chunk in chunks_v2}.isdisjoint(v1_chunk_ids)
        assert {chunk["embedding_model_revision"] for chunk in chunks_v2} == {
            t061.EMBEDDING_REVISION_V2
        }
        assert {chunk["embedding_model_revision"] for chunk in chunks_v1} == {
            t061.EMBEDDING_REVISION_V1
        }
        # Two full chunk sets for the same source revision, in separate vector
        # spaces, are a legal state only because the identity is the index version.
        assert t061.unscoped_chunk_count(stack.dbname, document_id) == len(chunks_v1) + len(
            chunks_v2
        )

        switched = t055.retrieval_query(
            session_v2, base_id, "华北地区十月的销售额", top_k=20
        )
        assert switched.status == 200, switched.text
        items = switched.json["items"]
        assert items, switched.text
        assert {item["chunk_id"] for item in items} <= {chunk["id"] for chunk in chunks_v2}
        switched_index = switched.json["retrieval"]["index"]
        assert switched_index["embedding_model_revision"] == t061.EMBEDDING_REVISION_V2
        assert int(switched_index["active_indexes"]) == 1
        assert int(switched_index["excluded_indexes"]) == 0
        assert switched.json["retrieval"]["embedding_model_revision"] == (
            t061.EMBEDDING_REVISION_V2
        )

        # The old deployment (v1) sees the new active index as excluded and
        # returns no mixed vectors. It must never score v2 chunks with a v1 query
        # vector (or the reverse).
        old_deployment = t055.retrieval_query(owner, base_id, "华北地区十月的销售额", top_k=20)
        assert old_deployment.status == 200, old_deployment.text
        assert old_deployment.json["items"] == []
        old_index = old_deployment.json["retrieval"]["index"]
        assert old_index["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1
        assert int(old_index["active_indexes"]) == 0
        assert int(old_index["excluded_indexes"]) == 1

        # The diagnostics read exposes the index version, its model revision,
        # honest progress and which version is active.
        diagnostics = t055.document_diagnostics(session_v2, document_id)
        assert diagnostics.status == 200, diagnostics.text
        active_view = diagnostics.json["active_index"]
        assert int(active_view["version"]) == 2
        assert active_view["embedding_model_revision"] == t061.EMBEDDING_REVISION_V2
        assert active_view["state"] == "ready"
        assert active_view["stage"] == "complete"
        assert int(active_view["chunk_count"]) == len(chunks_v2)
        assert active_view["active"] is True
        history = {int(item["version"]): item for item in diagnostics.json["indexes"]}
        assert history[1]["state"] == "superseded"
        assert history[1]["embedding_model_revision"] == t061.EMBEDDING_REVISION_V1
        assert history[2]["background_job"]["state"] == "succeeded"

        # Citations opened from either deployment read the active index only.
        assert t055.get_citation(owner, chunks_v1[0]["id"]).status == 404
        opened = t055.get_citation(session_v2, items[0]["chunk_id"])
        assert opened.status == 200, opened.text
        assert opened.json["citation"]["embedding_model_revision"] == (
            t061.EMBEDDING_REVISION_V2
        )


def test_a_failed_rebuild_keeps_the_previous_index_readable_and_can_be_retried(
    t055_stack, api_server, tmp_path
) -> None:
    stack = t055_stack
    owner, base_id, _artifact_id, document_id = t061.setup_ready_document(
        stack, log_name="failed-rebuild"
    )
    v1 = t061.active_index(stack.dbname, document_id)
    assert v1 is not None and v1["state"] == "ready"
    v1_chunk_ids = {
        chunk["id"] for chunk in t061.chunk_rows_for_index(stack.dbname, v1["id"])
    }
    assert v1_chunk_ids

    with t061.server_with_env(
        api_server, stack, tmp_path / "real-provider-certs", extra=t061.REAL_PROVIDER_ENV
    ) as (server_real, certs_real):
        session_real = t064.login_on(server_real, "t061.alice", f00b.USER_PASSWORD)
        with t061.maintenance_pools(
            stack,
            certs_real,
            kinds=t061.KIND_DOCUMENT_INGEST + "," + t061.KIND_ARTIFACT_FINALIZE,
            log_path=tmp_path / "maintenance-real-provider.log",
            extra_env=t061.REAL_PROVIDER_ENV,
        ):
            accepted = t061.reindex(session_real, document_id)
            assert accepted.status == 202, accepted.text
            failed = t055.wait_for_document_state(session_real, document_id, {"failed"})
            assert failed["state"] == "failed", failed

    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "failed"
    assert row["error_code"] == "embedding_provider_not_configured"
    # The previous active index is untouched and still readable through the
    # original deployment (local-hash-v1).
    assert int(row["active_revision"]) == 1
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and active["id"] == v1["id"] and active["state"] == "ready"
    failed_index = t061.index_by_version(stack.dbname, document_id, 2)
    assert failed_index["state"] == "failed"
    assert failed_index["error_code"] == "embedding_provider_not_configured"
    assert failed_index["activated_at"] is None
    diagnostics = t055.document_diagnostics(owner, document_id)
    assert diagnostics.status == 200, diagnostics.text
    assert int(diagnostics.json["active_index"]["version"]) == 1
    failed_view = next(
        item for item in diagnostics.json["indexes"] if int(item["version"]) == 2
    )
    assert failed_view["state"] == "failed"
    assert failed_view["stage"] == "failed"
    assert failed_view["error_code"] == "embedding_provider_not_configured"
    assert failed_view["background_job"]["state"] == "failed"
    assert int(failed_view["background_job"]["attempt_count"]) == 1
    still_readable = t055.retrieval_query(owner, base_id, "华北地区十月的销售额", top_k=20)
    assert still_readable.status == 200, still_readable.text
    assert {item["chunk_id"] for item in still_readable.json["items"]} <= v1_chunk_ids

    # Re-submitting the failed rebuild is allowed and succeeds on the normal
    # deployment: a new index version, the old failure kept as history.
    retried = t061.reindex(owner, document_id)
    assert retried.status == 202, retried.text
    assert int(retried.json["index_version"]) == 3
    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t061-retry.log"):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and int(active["version"]) == 3
    assert t061.index_by_version(stack.dbname, document_id, 2)["state"] == "failed"
    retrieval = t055.retrieval_query(owner, base_id, "华北地区十月的销售额", top_k=20)
    assert retrieval.status == 200, retrieval.text
    assert retrieval.json["items"]


def test_reindex_is_owner_only_csrf_protected_and_refuses_a_duplicate_build(t055_stack) -> None:
    stack = t055_stack
    owner, _base_id, _artifact_id, document_id = t061.setup_ready_document(
        stack, log_name="reindex-authz"
    )
    admin = f00b.admin_session(stack)
    bob = f00b.invite_and_activate(stack, admin, "t061.bob", password=f00b.USER_PASSWORD)
    for caller in (bob, admin):
        refused = t061.reindex(caller, document_id)
        assert refused.status == 404, refused.text
        assert refused.json["error"]["code"] == "resource_not_found"
    no_csrf = t061.reindex(owner, document_id, csrf=False)
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid"

    accepted = t061.reindex(owner, document_id)
    assert accepted.status == 202, accepted.text
    duplicate = t061.reindex(owner, document_id)
    assert duplicate.status == 409, duplicate.text
    assert duplicate.json["error"]["code"] == "index_build_in_progress"
    # The request itself never activates anything; the pointer still names v1.
    row = t055.document_row(stack.dbname, document_id)
    assert row["state"] == "scanning"
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and int(active["version"]) == 1
    progress = t055.document_diagnostics(owner, document_id)
    assert progress.status == 200, progress.text
    building = next(item for item in progress.json["indexes"] if int(item["version"]) == 2)
    assert building["state"] == "building"
    assert building["active"] is False
    # Progress is the persisted state + job, never a invented percentage.
    assert building["stage"] in {"queued", "building"}
    assert "percent" not in progress.text
    assert int(progress.json["active_index"]["version"]) == 1

    with t055.ingest_pools(stack, stack.server.log_path.parent / "maintenance-t061-authz.log"):
        view = t055.wait_for_document_state(owner, document_id, {"ready", "failed"})
    assert view["state"] == "ready", (view, t055.document_row(stack.dbname, document_id))
    active = t061.active_index(stack.dbname, document_id)
    assert active is not None and int(active["version"]) == 2
