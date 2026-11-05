"""F11/T071: a real purge failure keeps content unreadable, stays queryable, retries.

The failure is a real filesystem refusal, not a mock of the object store: the test
holds a Windows file handle open on the named sealed object, so the compiled
maintenance process's ``os.Remove`` receives a genuine sharing violation while the
bytes remain on disk. The first attempt fails and is retried by the background
controller; after the handle is released the next attempt (same job) finishes.

The second test proves the purge never deletes a physically shared object: a
source artifact of a deleted document is re-ingested into a second knowledge base
*before* the purge runs, and the bytes must survive because a live document still
reads them.
"""

from __future__ import annotations

from support import f00b, t028, t055, t069, t071
from support import stack as st


def test_a_real_store_failure_keeps_the_content_unreadable_and_a_retry_finishes(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t071 failure marker: the bytes stay on disk while the purge fails\n"
    artifact_id = t071.ready_artifact(stack, owner.creator, content)
    before = t071.artifact_row(stack.dbname, artifact_id)
    object_key = before["object_key"]
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )
    purge = t071.delete_content_job(stack.dbname, job["id"])

    handle = t071.hold_open(stack, object_key)
    try:
        # Attempt 1: the real object store refuses the delete. The explicit flag
        # is the deployment knob a real maintenance process receives.
        completed, reports = t071.purge_once(stack, "--delete-content-timeout", "2m")
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        report = t071.purge_report(reports)
        assert report["result"] == "retried", report
        assert report["error_code"] == t071.CODE_TRANSIENT_STORAGE, report
        assert t071.purge_summary(reports)["retried"] == 1, reports

        background = f00b.job_row(stack.dbname, purge["id"])
        assert background["state"] == "ready", background  # retry scheduled
        assert background["error_code"] == t071.CODE_TRANSIENT_STORAGE, background
        assert int(background["attempt_count"]) == 1, background
        # The failure is observable on the contract background-job surface, not
        # only in PostgreSQL.
        viewed = t071.get_background_job(owner.creator, purge["id"])
        assert viewed.status == 200, viewed.text
        assert viewed.json["state"] == "ready", viewed.json
        assert viewed.json["error_code"] == t071.CODE_TRANSIENT_STORAGE, viewed.json

        # The deletion job records the honest, observable failure; the scopes it
        # could still clear were cleared in the same evidence transaction.
        stored = t071.deletion_job(stack.dbname, job["id"])
        assert stored["state"] == "failed", stored
        assert stored["error_code"] == t071.CODE_TRANSIENT_STORAGE, stored
        assert "object_store" in t071.remaining(stored), stored
        assert set(t071.completed(stored)) == {"search_index", "retrieval_cache"}, stored
        fetched = t069.get_deletion(owner.creator, job["id"])
        assert fetched.status == 200, fetched.text
        assert fetched.json["state"] == "failed", fetched.json
        assert fetched.json["remaining_scopes"] == sorted(
            ["object_store", *t071.T072_SCOPES]
        ), fetched.json

        # The bytes are still on disk (that is what failed) but every real read
        # path refuses: a failed purge is never a readability window.
        assert t071.object_exists(stack, object_key)
        assert handle.read() == content
        st.assert_error(
            t028.get_artifact(owner.creator, artifact_id),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )
        for suffix in ("/download", "/content"):
            refused = owner.creator.client.get(
                t069.resource_path(t069.RESOURCE_ARTIFACT, artifact_id) + suffix
            )
            st.assert_error(refused, 404, t069.ERROR_RESOURCE_NOT_FOUND)
    finally:
        handle.close()

    # Attempt 2 after the real obstruction is gone: the same background job and
    # the same deletion job finish the purge, still with no readable window.
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
    t071.wait_retry_available(stack.dbname, purge["id"])
    reports = t071.retry_until_claimed(stack, stack.dbname)
    assert t071.purge_report(reports)["result"] == "succeeded", reports

    after = t071.artifact_row(stack.dbname, artifact_id)
    assert after["purged_at"] is not None, after
    assert not t071.object_exists(stack, object_key), object_key

    finished = t071.deletion_job(stack.dbname, job["id"])
    assert finished["state"] == "pending", finished
    assert t071.remaining(finished) == t071.T072_SCOPES, finished
    assert t071.completed(finished) == sorted(t069.T071_SCOPES), finished
    assert finished["error_code"] is None, finished
    assert int(finished["attempt_count"]) == 2, finished
    background = f00b.job_row(stack.dbname, purge["id"])
    assert background["state"] == "succeeded", background
    assert int(background["attempt_count"]) == 2, background

    # No resurrection: the resource still refuses after the successful purge.
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )

    # The purge attempt bound is a real maintenance flag with the documented
    # validation: a non-positive value is a startup refusal (exit 2, nothing runs).
    refused = f00b.run_maintenance(
        stack,
        "--once",
        "--kinds",
        t071.KIND_DELETE_CONTENT,
        "--delete-content-timeout",
        "-1s",
    )
    assert refused.returncode == 2, (refused.stdout, refused.stderr)
    assert "must be greater than zero" in refused.stderr, refused.stderr


def test_a_legitimately_shared_source_artifact_is_never_deleted(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    alice = t069.owner(stack)
    content = (
        "shared source marker-t071: the bytes are re-ingested before the purge\n"
        "second line of the shared source\n"
    )
    base_a, document_a, source_artifact = t071.ready_document(
        stack, alice.creator, content, log_path=tmp_path / "maintenance-t071-a.log"
    )
    source_key = t071.artifact_row(stack.dbname, source_artifact)["object_key"]
    document_a_keys = t071.document_object_keys(stack, document_a)
    document_a_bodies = t071.document_body_artifact_ids(document_a, stack.dbname)
    assert source_key in document_a_keys
    assert document_a_bodies, document_a_bodies

    job = t069.deletion_of(
        t069.delete_resource(alice.creator, t069.RESOURCE_DOCUMENT, document_a)
    )

    # A new live document in a second knowledge base is created from the same
    # source artifact while the deletion job is still pending: the artifact is a
    # legitimate reference that the purge must not delete behind.
    base_b = t071.second_knowledge_base(alice.creator, "t071 second knowledge base")
    document_b = t071.ingest_ready_document(
        stack,
        alice.creator,
        base_b,
        source_artifact,
        log_path=tmp_path / "maintenance-t071-b.log",
    )
    retrieval = t055.retrieval_query(alice.creator, base_b, "shared source marker-t071", top_k=5)
    assert retrieval.status == 200 and retrieval.json["items"], retrieval.json

    _completed, reports = t071.purge_once(stack)
    report = t071.purge_report(reports)
    assert report["result"] == "failed", report
    assert report["error_code"] == t071.ERROR_OBJECT_SHARED, report

    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "failed", stored
    assert stored["error_code"] == t071.ERROR_OBJECT_SHARED, stored
    assert "object_store" in t071.remaining(stored), stored
    assert set(t071.completed(stored)) == {"search_index", "retrieval_cache"}, stored
    # The shared-reference refusal is visible on both job surfaces: the deletion
    # row above and the contract background job below.
    purge = t071.delete_content_job(stack.dbname, job["id"])
    background = f00b.job_row(stack.dbname, purge["id"])
    assert background["state"] == "failed", background
    assert background["error_code"] == t071.ERROR_OBJECT_SHARED, background
    assert background["failure_class"] == "business", background
    viewed = t071.get_background_job(alice.creator, purge["id"])
    assert viewed.status == 200, viewed.text
    assert viewed.json["error_code"] == t071.ERROR_OBJECT_SHARED, viewed.json

    # The source bytes survive (the live document still reads them); the deleted
    # document's own chunk-body objects are not shared and are gone.
    assert t071.object_exists(stack, source_key), "a shared source artifact was deleted"
    for body_artifact in document_a_bodies:
        row = t071.artifact_row(stack.dbname, body_artifact)
        assert row["purged_at"] is not None, row
        assert not t071.object_exists(stack, row["object_key"]), row["object_key"]
    # The shared source artifact row stays live for document B.
    source_row = t071.artifact_row(stack.dbname, source_artifact)
    assert source_row["purged_at"] is None and source_row["deleted_at"] is None, source_row

    # The new document is still fully readable: the purge did not break it.
    assert t055.get_document(alice.creator, document_b).status == 200
    still_there = t055.retrieval_query(
        alice.creator, base_b, "shared source marker-t071", top_k=5
    )
    assert still_there.status == 200 and still_there.json["items"], still_there.json
    # And the deleted document still refuses through the same endpoints.
    st.assert_error(
        t055.get_document(alice.creator, document_a), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    assert t055.list_documents(alice.creator, base_a).json["items"] == []
