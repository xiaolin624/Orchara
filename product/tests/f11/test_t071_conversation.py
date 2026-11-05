"""F11/T071: the conversation resource added by F08/T074 uses the same purge.

T074's conversation deletion records the sealed request/attachment/run-output
artifacts under the ``object_store`` scope and expects the F11 cleanup tickets to
consume it. Message bodies are those sealed artifacts; the request/interaction
rows are metadata whose read paths T074 already tombstoned, so a conversation has
no chunk/vector index or retrieval-cache table of its own: ``search_index`` and
``retrieval_cache`` are legitimately empty for it. The purge therefore clears the
three T071 scopes without inventing a second conversation index, and T072's four
remaining scopes stay outstanding.

The second half of the test proves the fail-safe for a resource type with no purge
adapter: a real deletion job whose type this build does not know must keep every
scope outstanding with a non-retryable code, never claim an empty cleanup.
"""

from __future__ import annotations

import uuid

from support import f00b, f08, t028, t069, t071
from support import stack as st


def _seed_unknown_deletion_job(stack, owner_id: str) -> str:
    job_id = str(uuid.uuid4())
    resource_id = str(uuid.uuid4())
    st.sql(
        stack.dbname,
        "INSERT INTO platform.deletion_jobs "
        "(id, owner_id, resource_type, resource_id, deletion_revision, state, "
        " remaining_scopes) VALUES ("
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"'unknown_resource', {st.quote_literal(resource_id)}::uuid, 1, 'pending', "
        f"{st.quote_literal('[%s]' % ','.join('\"%s\"' % s for s in t069.SCOPES))}::jsonb)",
    )
    return job_id


def test_conversation_purge_and_unknown_resource_fail_safe(t069_stack) -> None:
    stack = t069_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    request = f08.request_of(
        f08.submit_request(
            owner.session,
            conversation_id,
            "t071 conversation marker: sealed request bytes",
            version_id=published.version_id,
            key=f00b.new_uuid(),
        )
    )
    request_row = f08.request_row(stack.dbname, request["id"])
    input_artifact = request_row["input_artifact_id"]
    assert input_artifact, request_row
    artifact = t071.artifact_row(stack.dbname, input_artifact)
    object_key = artifact["object_key"]
    assert t071.object_exists(stack, object_key)

    job = t069.deletion_of(
        t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, conversation_id)
    )
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports

    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "pending", stored
    assert t071.remaining(stored) == t071.T072_SCOPES, stored
    assert t071.completed(stored) == sorted(t069.T071_SCOPES), stored
    assert not t071.object_exists(stack, object_key), "the request bytes were not purged"
    purged = t071.artifact_row(stack.dbname, input_artifact)
    assert purged["purged_at"] is not None, purged
    assert purged["state"] == "deleted", purged
    # The conversation's own read paths still refuse after the purge.
    st.assert_error(
        f08.get_conversation(owner.session, conversation_id),
        404,
        f08.ERROR_RESOURCE_NOT_FOUND,
    )
    st.assert_error(
        f08.list_messages(owner.session, conversation_id),
        404,
        f08.ERROR_RESOURCE_NOT_FOUND,
    )

    # A deletion job of a resource type this build has no purge adapter for keeps
    # every scope outstanding: an empty derived set is never "cleared".
    unknown_job = _seed_unknown_deletion_job(stack, owner.principal_id)
    f00b.enqueue_ok(
        stack,
        kind=t071.KIND_DELETE_CONTENT,
        target_id=unknown_job,
        owner_id=owner.principal_id,
        dedup_key=t071.dedup_key(unknown_job),
    )
    _completed, reports = t071.purge_once(stack)
    report = t071.purge_report(reports)
    assert report["result"] == "failed", report
    assert report["error_code"] == "purge_unsupported_target", report
    unknown = t071.deletion_job(stack.dbname, unknown_job)
    assert unknown["state"] == "failed", unknown
    assert unknown["error_code"] == "purge_unsupported_target", unknown
    assert t071.remaining(unknown) == list(t069.SCOPES), unknown
    assert t071.completed(unknown) == [], unknown
    background = f00b.job_row(stack.dbname, report["job_id"])
    assert background["state"] == "failed", background
    assert background["failure_class"] == "business", background


def test_a_shared_attachment_survives_another_conversations_delete_and_purge(
    t069_stack,
) -> None:
    """Post-review finding [P1]: the purge guard must honour request/run/handoff refs.

    The reviewer's reproduction shared one attachment between conversations A and B:
    after A was deleted and purged, B's conversation was readable but its attachment
    answered 404. The explicit reference relations (request input/attachments, run
    input/run_artifacts, handoff summary/attachments) must keep the object with the
    non-retryable business code ``object_shared_reference``.
    """
    stack = t069_stack
    owner = f08.account(stack)
    conversation_a = f08.conversation(owner.session)
    conversation_b = f08.conversation(owner.session)
    content = b"t071 post-review shared attachment: B still needs these bytes\n"
    attachment = f08.public_attachment(stack, owner.session, content)
    request_a = f08.request_of(
        f08.submit_request(
            owner.session, conversation_a, "A share the attachment",
            artifact_ids=[attachment], key=f00b.new_uuid(),
        )
    )
    request_b = f08.request_of(
        f08.submit_request(
            owner.session, conversation_b, "B still uses the attachment",
            artifact_ids=[attachment], key=f00b.new_uuid(),
        )
    )
    row = t071.artifact_row(stack.dbname, attachment)
    object_key = row["object_key"]
    assert t071.object_exists(stack, object_key)

    job = t069.deletion_of(
        t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, conversation_a)
    )
    # B's attachment is still readable right after A's tombstone: a live other
    # conversation keeps it.
    assert t028.read_content(owner.session, attachment).status == 200

    _completed, reports = t071.purge_once(stack)

    # B's attachment is untouched: the row is live, the bytes are on disk and every
    # read entry still serves it. B's request and conversation are readable. This
    # is the reviewer's observable: the purge must not delete another conversation's
    # attachment behind it.
    for entry in (t028.get_artifact, t028.download_artifact, t028.read_content):
        response = entry(owner.session, attachment)
        assert response.status == 200, (entry.__name__, response.status, response.text)
    assert t028.read_content(owner.session, attachment).raw == content
    survives = t071.artifact_row(stack.dbname, attachment)
    assert survives["deleted_at"] is None, survives
    assert survives["purged_at"] is None, survives
    assert t071.object_exists(stack, object_key)
    assert f08.get_request(owner.session, request_b["id"]).status == 200
    assert f08.list_messages(owner.session, conversation_b).status == 200

    report = t071.purge_report(reports)
    assert report["result"] == "failed", report
    assert report["error_code"] == t071.ERROR_OBJECT_SHARED, report
    stored = t071.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "failed", stored
    assert stored["error_code"] == t071.ERROR_OBJECT_SHARED, stored
    assert "object_store" in t071.remaining(stored), stored
    assert "object_store" not in t071.completed(stored), stored

    # A's own message was tombstoned at the same DELETE and its bytes are gone;
    # only the shared object blocked the job.
    message_a = f08.request_row(stack.dbname, request_a["id"])["input_artifact_id"]
    purged = t071.artifact_row(stack.dbname, message_a)
    assert purged["deleted_at"] is not None, purged
    assert purged["purged_at"] is not None, purged
    assert not t071.object_exists(stack, purged["object_key"])
