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

from support import f00b, f08, t069, t071
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
