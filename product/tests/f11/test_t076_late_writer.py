"""F11/T076 (#77): a late background writer can never resurrect deleted input.

Two real races are replayed, each with the real deletion endpoint this time (T069)
instead of a hand-written tombstone, and each replayed twice:

1. **Late ``artifact_finalize``** -- the artifact is uploaded, finalized, then
   really deleted through ``DELETE /api/v1/artifacts/{id}``. A replayed finalize
   job (the shape a backed-up/duplicated queue entry carries: the pre-delete
   revision and hash) is enqueued through the real ``orchara-admin
   background-jobs enqueue`` and drained by the real maintenance controller. It
   must fail with the named code ``source_deleted``, write no sealed bytes, and
   leave the artifact unreadable. The delete itself refuses while a finalize
   writer still owns the artifact (409 ``resource_in_use``), so a late writer can
   neither be started first nor succeed afterwards.
2. **Late ``document_ingest``** -- the T061 parked-handler technique: a real
   work-only maintenance process is mid-flight uploading chunk bodies (the
   finalize pool is withheld), the document is really deleted, and only then is
   the finalize pool released so the old handler finishes after the delete. The
   activation transaction must refuse the late work (``source_deleted``), never
   activate the index version, and the T071 purge must then clear every staged
   chunk body (the copy set is derived from the authoritative rows at purge time).
"""

from __future__ import annotations

import time

from support import f00b, t024, t028, t055, t061, t069, t071, t076
from support import stack as st


def _wait_for_index_job(
    stack, document_id: str, index_version_id: str, states: set[str], *, timeout: float = 120.0
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = t061.job_for_index(stack.dbname, document_id, index_version_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"the ingest job for index {index_version_id} never reached {states}: {last}")


def test_a_replayed_finalize_job_cannot_resurrect_a_deleted_artifact(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t076 late finalize marker: these bytes must never come back\n"
    reserved = t028.create_upload(
        owner.creator, media_type=t028.TEXT, size_bytes=len(content), purpose="task_input"
    )
    assert reserved.status == 201, reserved.text
    artifact_id = reserved.json["artifact_id"]
    assert t024.put_link(reserved.json["upload_url"], content).status == 204
    completed = t028.complete_upload(owner.creator, artifact_id, t028.sha256_hex(content))
    assert completed.status == 202, completed.text
    scanning = t069.artifact_row(stack.dbname, artifact_id)
    assert scanning["state"] == "scanning", scanning
    assert t028.artifact_jobs(stack.dbname, artifact_id)[0]["state"] == "ready"

    # Gate 1: while a finalize writer is live the delete refuses to race it.
    refused = t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    error = st.assert_error(refused, 409, t069.ERROR_RESOURCE_IN_USE)
    assert error["details"]["references"] == ["background_job"], error["details"]
    assert t069.artifact_row(stack.dbname, artifact_id)["deleted_at"] is None

    # The real finalize activates the artifact; then the owner really deletes it.
    t028.finalize_once(stack)
    ready = t028.wait_for_artifact_state(owner.creator, artifact_id, {"ready"})
    assert ready["state"] == "ready", ready
    before_delete = t069.artifact_row(stack.dbname, artifact_id)
    object_key = before_delete["object_key"]
    assert t076.read_object(stack, object_key) == content
    deletion = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )
    deleted = t069.artifact_row(stack.dbname, artifact_id)
    assert deleted["deleted_at"] is not None and deleted["state"] == "deleted", deleted
    assert int(deleted["revision"]) > int(before_delete["revision"])

    sealed_before = t076.sealed_keys(stack)
    for attempt in ("replay-1", "replay-2"):
        late = t076.enqueue_late_finalize(
            stack, artifact=before_delete, owner_id=owner.creator.principal_id, suffix=attempt
        )
        assert late["kind"] == t076.KIND_ARTIFACT_FINALIZE, late
        _completed, reports = f00b.maintenance_once(
            stack, "--kinds", t076.KIND_ARTIFACT_FINALIZE
        )
        assert f00b.summary_of(reports)["failed"] == 1, reports
        job = t076.job_failure(stack.dbname, late["id"])
        assert job["state"] == "failed", job
        assert job["error_code"] == t076.CODE_SOURCE_DELETED, job
        assert job["failure_class"] == "business", job
        assert job["result_artifact_id"] is None, job
        # The replay wrote nothing: no new sealed object, the active key is still
        # the (now tombstoned) pre-delete key, and the row still refuses.
        assert t076.sealed_keys(stack) == sealed_before, (
            t076.sealed_keys(stack) - sealed_before,
            sealed_before - t076.sealed_keys(stack),
        )
        still = t069.artifact_row(stack.dbname, artifact_id)
        assert still["state"] == "deleted" and still["deleted_at"] is not None, still
        assert still["revision"] == deleted["revision"], still
        for suffix in ("", "/download", "/content"):
            refused_read = owner.creator.client.get(
                t069.resource_path(t069.RESOURCE_ARTIFACT, artifact_id) + suffix
            )
            st.assert_error(refused_read, 404, t069.ERROR_RESOURCE_NOT_FOUND)
        # The delete is still one job: a late writer cannot create a second
        # deletion authority or a second purge unit of work.
        assert len(t076.deletion_jobs_for(stack.dbname, artifact_id)) == 1
        assert len(
            t076.background_jobs(
                stack.dbname, kind=t076.KIND_DELETE_CONTENT, target_id=deletion["id"]
            )
        ) == 1

    # The physical copy is still there because only the tombstone committed; the
    # real purge then removes it and the read still refuses afterwards.
    assert t071.object_exists(stack, object_key)
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    assert not t071.object_exists(stack, object_key)
    assert t069.artifact_row(stack.dbname, artifact_id)["purged_at"] is not None
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )


def test_a_parked_ingest_handler_cannot_activate_a_really_deleted_document(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content_v1 = (
        "T076 晚到摄取基线：这是第一版正文。marker-t076-ingest\n"
        "退款政策：订单完成后三十天内可申请。marker-t076-ingest\n"
    )
    base_id, document_id, _source_v1 = t071.ready_document(
        stack, owner.creator, content_v1, log_path=tmp_path / "t076-ingest-v1.log"
    )
    assert t061.active_index(stack.dbname, document_id) is not None
    content_v2 = (
        "T076 晚到摄取第二版：GAMMA-LATE-MARKER 只属于晚到任务。\n"
        "这一版在文档被删除之后才完成，永远不能激活。\n"
    )
    source_v2 = t055.prepare_source(stack, owner.creator, content_v2)

    with t061.maintenance_pools(
        stack,
        stack.certificates,
        kinds=t076.KIND_DOCUMENT_INGEST,
        log_path=tmp_path / "t076-late-work.log",
        work_slots=1,
        system_io_slots=1,
    ):
        accepted = t061.reindex(owner.creator, document_id, source_artifact_id=source_v2)
        assert accepted.status == 202, accepted.text
        index_v2 = accepted.json["index_version_id"]
        t061.wait_for_parked_handler(stack.dbname, owner.creator.principal_id, timeout=120.0)

        # The real user path: the owner deletes the document mid-flight.
        deletion = t069.deletion_of(
            t069.delete_resource(owner.creator, t069.RESOURCE_DOCUMENT, document_id)
        )
        tombstoned = t055.document_row(stack.dbname, document_id)
        assert tombstoned["state"] == "deleted", tombstoned
        assert tombstoned["deleted_at"] is not None, tombstoned

        with t061.maintenance_pools(
            stack,
            stack.certificates,
            kinds=t076.KIND_ARTIFACT_FINALIZE,
            log_path=tmp_path / "t076-late-finalize.log",
            work_slots=1,
            system_io_slots=2,
        ):
            late_job = _wait_for_index_job(stack, document_id, index_v2, {"failed"})
    assert late_job["error_code"] == t076.CODE_SOURCE_DELETED, late_job
    assert late_job["failure_class"] == "business", late_job

    # The late handler really staged work, but the activation transaction refused
    # it: the index version never activated and the document stayed deleted.
    late_chunks = t061.chunk_rows_for_index(stack.dbname, index_v2)
    assert late_chunks, "the parked handler never staged its chunk set"
    late_index = t061.index_by_version(stack.dbname, document_id, 2)
    assert late_index["state"] == "failed", late_index
    assert late_index["error_code"] == t076.CODE_SOURCE_DELETED, late_index
    assert late_index["activated_at"] is None, late_index
    assert st.sql_scalar(
        stack.dbname,
        "SELECT count(*) FROM platform.knowledge_documents "
        f"WHERE id = {st.quote_literal(document_id)}::uuid AND state = 'deleted'",
    ) == "1"

    # Nothing is readable: detail, retrieval, citation open and the cache.
    st.assert_error(
        t055.get_document(owner.creator, document_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t055.list_documents(owner.creator, base_id).json["items"] == []
    retrieved = t055.retrieval_query(owner.creator, base_id, "晚到摄取", top_k=10)
    assert retrieved.status == 200, retrieved.text
    assert retrieved.json["items"] == [], retrieved.json
    assert "GAMMA-LATE-MARKER" not in retrieved.text
    for chunk in late_chunks:
        st.assert_error(
            t055.get_citation(owner.creator, chunk["id"]), 404, t069.ERROR_RESOURCE_NOT_FOUND
        )

    # The T071 purge derives the copy set from the authoritative rows at purge
    # time, so the bodies the late handler uploaded *after* the delete are cleared
    # too. Then the deletion job is complete and cannot report a readable window.
    keys = t071.document_object_keys(stack, document_id)
    assert keys, keys
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    assert t071.chunk_rows(stack.dbname, document_id) == []
    for key in keys:
        assert not t071.object_exists(stack, key), key
    for artifact_id in t071.document_body_artifact_ids(document_id, stack.dbname):
        assert t069.artifact_row(stack.dbname, artifact_id)["purged_at"] is not None

    # Replay twice: a late submit/reindex is refused by the tombstone before any
    # new job or object exists, and the completed deletion stays complete.
    objects_after_purge = t076.store_keys(stack)
    jobs_after_purge = t076.background_jobs(
        stack.dbname, kind=t076.KIND_DOCUMENT_INGEST, target_id=document_id
    )
    for _attempt in (1, 2):
        refused = t061.reindex(owner.creator, document_id)
        assert refused.status == 404, refused.text
        assert refused.json["error"]["code"] == t069.ERROR_RESOURCE_NOT_FOUND
        _completed, reports = t071.purge_once(stack)
        assert t071.purge_summary(reports)["claimed"] == 0, reports
        assert t076.store_keys(stack) == objects_after_purge, "the replay wrote new bytes"
        assert (
            t076.background_jobs(
                stack.dbname, kind=t076.KIND_DOCUMENT_INGEST, target_id=document_id
            )
            == jobs_after_purge
        )
        st.assert_error(
            t055.get_document(owner.creator, document_id),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )
    stored = t071.deletion_job(stack.dbname, deletion["id"])
    assert stored["state"] in {"pending", "completed"}, stored
