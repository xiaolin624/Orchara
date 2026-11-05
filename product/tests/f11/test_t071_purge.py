"""F11/T071 acceptance: idempotent per-scope purge of objects, index rows and cache.

Real stack throughout: PostgreSQL 16, the compiled API/maintenance binaries, real
sealed bytes in a filesystem object store, real HTTP. The delete answers 202 with
a deletion job whose T071 scopes (``object_store``, ``search_index``,
``retrieval_cache``) are cleared by the real ``maintenance run --once --kinds
delete_content`` process; the four T072 scopes stay outstanding.

Every assertion reads the authoritative row or the real file, never a flag. The
purge job is enqueued in T069's delete transaction (same deletion job id), so the
``delete_content`` job must be visible before any maintenance process runs.
"""

from __future__ import annotations

import uuid

from support import f00b, t055, t058, t067, t069, t071
from support import stack as st


def test_artifact_purge_clears_object_store_and_is_idempotent(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t071 artifact purge marker: the only copy of these bytes\n"
    artifact_id = t071.ready_artifact(stack, owner.creator, content)
    before = t071.artifact_row(stack.dbname, artifact_id)
    assert before["state"] == "ready" and before["purged_at"] is None, before
    object_key = before["object_key"]
    assert t071.object_exists(stack, object_key)
    assert t071.read_object(stack, object_key) == content

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )

    # The purge unit of work is created in the delete transaction: it exists
    # before any maintenance process has run, and it names the deletion job.
    purge = t071.delete_content_job(stack.dbname, job["id"])
    assert purge["kind"] == t071.KIND_DELETE_CONTENT, purge
    assert purge["target_id"] == job["id"], purge
    assert purge["owner_id"] == owner.creator.principal_id, purge
    assert purge["dedup_key"] == t071.dedup_key(job["id"]), purge
    assert purge["state"] == "ready", purge
    view = t071.get_background_job(owner.creator, purge["id"])
    assert view.status == 200, view.text
    assert view.json == {
        "id": purge["id"],
        "kind": "delete_content",
        "state": "ready",
        "error_code": None,
    }, view.json

    # A repeated DELETE cannot create a second unit of work.
    again = t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    assert again.status == 202 and again.json == job, (again.json, job)
    assert len(t071.delete_content_jobs(stack.dbname, deletion_id=job["id"])) == 1

    completed, reports = t071.purge_once(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    report = t071.purge_report(reports)
    assert report["result"] == "succeeded", report
    assert report["kind"] == t071.KIND_DELETE_CONTENT, report
    assert t071.purge_summary(reports)["succeeded"] == 1, reports

    after = t071.artifact_row(stack.dbname, artifact_id)
    assert after["purged_at"] is not None, after
    assert after["state"] == "deleted", after
    assert after["deleted_at"] is not None, after
    assert not t071.object_exists(stack, object_key), "the sealed bytes were not purged"

    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "pending", stored  # the four T072 scopes are still owed
    assert t071.remaining(stored) == t071.T072_SCOPES, stored
    assert t071.completed(stored) == sorted(t069.T071_SCOPES), stored
    assert int(stored["attempt_count"]) == 1, stored
    assert stored["error_code"] is None, stored
    assert stored["started_at"] is not None, stored
    fetched = t069.get_deletion(owner.creator, job["id"])
    assert fetched.status == 200, fetched.text
    assert fetched.json == {
        "id": job["id"],
        "state": "pending",
        "remaining_scopes": t071.T072_SCOPES,
    }, fetched.json
    background = f00b.job_row(stack.dbname, purge["id"])
    assert background["state"] == "succeeded", background
    assert background["finished_at"] is not None, background

    # A second drain finds no ready purge job and changes nothing.
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_summary(reports)["claimed"] == 0, reports
    snapshot = t071.purge_scope_counts(stack.dbname)

    # A second, explicitly enqueued purge job for the same deletion (the
    # operator replay path) is a real handler+guard run that is a no-op.
    replay = f00b.enqueue_ok(
        stack,
        kind=t071.KIND_DELETE_CONTENT,
        target_id=job["id"],
        owner_id=owner.creator.principal_id,
        dedup_key=t071.dedup_key(job["id"]) + ":replay",
    )
    assert replay["id"] != purge["id"], replay
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    replayed = t071.deletion_job(stack.dbname, job["id"])
    assert t071.remaining(replayed) == t071.T072_SCOPES, replayed
    assert t071.completed(replayed) == sorted(t069.T071_SCOPES), replayed
    assert int(replayed["attempt_count"]) == 2, replayed
    assert t071.artifact_row(stack.dbname, artifact_id)["purged_at"] == after["purged_at"]
    replayed_counts = t071.purge_scope_counts(stack.dbname)
    for key in ("chunks", "index_versions", "cache_rows", "purged_artifacts", "deletion_jobs"):
        assert replayed_counts[key] == snapshot[key], (key, snapshot, replayed_counts)


def test_memory_purge_clears_the_body_and_every_superseded_revision(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    first_text = "t071 memory marker revision one: paper invoices"
    memory = t071.create_memory(stack, owner.creator, owner.application_id, first_text)
    first_artifact = t071.memory_row(stack.dbname, memory["id"])["body_artifact_id"]
    first_row = t071.artifact_row(stack.dbname, first_artifact)
    assert first_row["state"] == "ready" and first_row["purged_at"] is None, first_row
    first_key = first_row["object_key"]

    # A real revision update leaves the superseded body artifact row behind
    # (T058 documents this as F07's physical purge debt).
    update = t058.update(
        owner.creator,
        memory["id"],
        text="t071 memory marker revision two: the replacement body",
        if_match=t058.expected_etag(1),
    )
    assert update.status == 200, update.text
    second = update.json
    assert second["revision"] == 2, second
    second_artifact = t071.memory_row(stack.dbname, memory["id"])["body_artifact_id"]
    assert second_artifact != first_artifact
    second_row = t071.artifact_row(stack.dbname, second_artifact)
    second_key = second_row["object_key"]
    assert second_key != first_key
    assert t071.object_exists(stack, first_key) and t071.object_exists(stack, second_key)

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_MEMORY, memory["id"])
    )
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports

    # Both the current body named by the job and the superseded revision it can
    # no longer reach are physically gone and their rows are purged.
    for artifact_id, key in ((first_artifact, first_key), (second_artifact, second_key)):
        row = t071.artifact_row(stack.dbname, artifact_id)
        assert row["purged_at"] is not None, row
        assert row["state"] == "deleted" and row["deleted_at"] is not None, row
        assert not t071.object_exists(stack, key), key

    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "pending", stored
    assert t071.remaining(stored) == t071.T072_SCOPES, stored
    # memory_embeddings does not exist yet: the index scope is honestly cleared as
    # empty rather than failed (T069's scope descriptor says so).
    assert t071.completed(stored) == sorted(t069.T071_SCOPES), stored
    assert stored["error_code"] is None, stored

    # The tombstone row is the no-resurrection authority and still refuses reads
    # after the purge; the row itself is never deleted.
    row = t071.memory_row(stack.dbname, memory["id"])
    assert row is not None and row["deleted_at"] is not None, row
    st.assert_error(
        t058.detail(owner.creator, memory["id"]), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t058.list_memories(owner.creator).json["items"] == []


def test_document_purge_removes_chunks_vectors_versions_and_cache_rows(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    alice = t069.owner(stack)
    content = (
        "退款政策：退款需在订单完成后 30 天内提交申请。marker-t071-document\n"
        "第二次出现：退款申请需要订单号。marker-t071-document\n"
    )
    base_id, document_id, source_artifact = t071.ready_document(
        stack, alice.creator, content, log_path=tmp_path / "maintenance-t071.log"
    )
    chunks = t071.chunk_rows(stack.dbname, document_id)
    assert chunks, "the ingested document must have real chunks"
    for chunk in chunks:
        # The vector lives on the chunk row (no pgvector on this host): a real
        # embedding array of the fixed dimension is what "vector" means here.
        assert int(chunk["embedding_length"]) == 384, chunk
    versions = t071.index_versions(stack.dbname, document_id)
    assert len(versions) == 1, versions
    body_artifacts = sorted({chunk["body_artifact_id"] for chunk in chunks})
    keys = t071.document_object_keys(stack, document_id)
    assert len(keys) == len(body_artifacts) + 1, keys

    query = "退款申请"
    warmed = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert warmed.status == 200, warmed.text
    assert warmed.json["items"], warmed.json
    assert warmed.json["retrieval"]["cache"]["stored"] is True, warmed.json
    cached = t071.cache_rows(stack.dbname, base_id=base_id)
    assert cached, "the warm request must have stored a real cache row"
    assert any(
        item.get("document_id") == document_id
        for row in cached
        for item in row["items"]
    ), cached
    deleted_chunk = warmed.json["items"][0]["chunk_id"]

    job = t069.deletion_of(
        t069.delete_resource(alice.creator, t069.RESOURCE_DOCUMENT, document_id)
    )
    # The synchronous delete only tombstones: the cache row is the purge's job.
    assert t071.cache_rows(stack.dbname, base_id=base_id), "T069 must not be the cache purge"

    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports

    assert t071.chunk_rows(stack.dbname, document_id) == [], "chunk/vector rows survived"
    assert t071.index_versions(stack.dbname, document_id) == [], "index versions survived"
    assert t071.cache_rows(stack.dbname, base_id=base_id) == [], "cache rows survived"
    for key in keys:
        assert not t071.object_exists(stack, key), key
    for artifact_id in [source_artifact, *body_artifacts]:
        row = t071.artifact_row(stack.dbname, artifact_id)
        assert row["purged_at"] is not None, row
        assert row["state"] == "deleted", row
    document = t071.document_row(stack.dbname, document_id)
    assert document["state"] == "deleted", document
    assert document["deleted_at"] is not None, document
    # The activation pointer could not survive the index it pointed at.
    assert document["active_index_id"] is None, document

    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "pending", stored
    assert t071.remaining(stored) == t071.T072_SCOPES, stored
    assert t071.completed(stored) == sorted(t069.T071_SCOPES), stored

    # No resurrection: every real read still refuses, and the cache cannot serve.
    st.assert_error(
        t055.get_document(alice.creator, document_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t055.list_documents(alice.creator, base_id).json["items"] == []
    st.assert_error(
        t055.get_citation(alice.creator, deleted_chunk), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    after = t055.retrieval_query(alice.creator, base_id, query, top_k=5)
    assert after.status == 200, after.text
    assert after.json["items"] == [], after.json
    assert after.json["retrieval"]["cache"]["hit"] is False, after.json
    assert t067.cache_rows(stack.dbname, principal_id=alice.creator.principal_id) == []

    # An unknown deletion id remains the located 404 for the same owner.
    st.assert_error(
        t069.get_deletion(alice.creator, str(uuid.uuid4())),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
