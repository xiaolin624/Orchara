"""T074 (#75): conversation deletion through the F11/T069 foundation.

T069 created the contract's conversation DELETE route but had no conversation
authority, so every id was a located 404 and its conversation tests were deferred.
This ticket adds the authority (platform.conversations + the request aggregate) and
the ``deletion.Target``; these are the deferred tests, driven through the real
compiled API, the real tombstone transaction and the real execution cancel path.
"""

from __future__ import annotations

from support import f00b, f08, t028, t069
from support import stack as st


def test_conversation_delete_tombstones_cancels_and_returns_the_seven_scope_job(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)

    dispatched = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "delete me: live run",
            version_id=published.version_id, key=f00b.new_uuid(),
        )
    )
    queued = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "delete me: queued",
            version_id=published.version_id, key=f00b.new_uuid(),
        )
    )
    assert dispatched["state"] == "dispatched" and queued["state"] == "queued"
    run_id = dispatched["run_id"]
    before = f08.conversation_row(stack.dbname, conversation_id)
    assert before["deleted_at"] is None and before["revision"] == 1
    sealed = f08.request_row(stack.dbname, dispatched["id"])["input_artifact_id"]
    sealed_row = f08.artifact_row(stack.dbname, sealed)
    sealed_bytes = f08.artifact_bytes(stack, sealed)

    response = t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, conversation_id)
    job = t069.deletion_of(response)
    assert job["resource_id"] == conversation_id
    assert job["state"] == "pending"

    # The tombstone committed before the 202: every conversation/request read
    # refuses immediately, and the conversation revision moved exactly once.
    after = f08.conversation_row(stack.dbname, conversation_id)
    assert after["deleted_at"] is not None
    assert after["revision"] == before["revision"] + 1
    st.assert_error(f08.get_conversation(owner.session, conversation_id),
                    404, f08.ERROR_RESOURCE_NOT_FOUND)
    st.assert_error(f08.list_messages(owner.session, conversation_id),
                    404, f08.ERROR_RESOURCE_NOT_FOUND)
    st.assert_error(f08.get_request(owner.session, dispatched["id"]),
                    404, f08.ERROR_RESOURCE_NOT_FOUND)
    listing = f08.list_conversations(owner.session)
    assert listing.status == 200, listing.text
    assert all(item["id"] != conversation_id for item in listing.json["items"])
    new_submit = f08.submit_request(
        owner.session, conversation_id, "after the tombstone", key=f00b.new_uuid()
    )
    st.assert_error(new_submit, 404, f08.ERROR_RESOURCE_NOT_FOUND)

    # The queued request was cancelled in the tombstone transaction; the live Run
    # entered the existing execution cancel path after the commit. The dispatched
    # request keeps its state (STATE-MACHINES.md §17) and points at the cancelled Run.
    assert f08.request_row(stack.dbname, queued["id"])["state"] == "cancelled"
    dispatched_row = f08.request_row(stack.dbname, dispatched["id"])
    assert dispatched_row["state"] == "dispatched"
    assert dispatched_row["run_state"] == "cancelled", dispatched_row
    cancelled_runs = [run for run in f08.run_rows(stack.dbname, conversation_id)
                      if run["state"] == "cancelled"]
    assert [run["id"] for run in cancelled_runs] == [run_id]

    # Post-review fix: the sealed request body is tombstoned in the same
    # transaction as the conversation, so every artifact read entry refuses
    # immediately. T069 still does not purge: only T071 removes the real bytes.
    still_there = f08.artifact_row(stack.dbname, sealed)
    assert still_there["deleted_at"] is not None
    assert still_there["state"] == "deleted"
    assert f08.artifact_bytes(stack, sealed) == sealed_bytes

    # The 202 is the contract DeletionAccepted and the job query is the contract
    # DeletionJob with all seven vocabulary scopes still outstanding.
    fetched = t069.get_deletion(owner.session, job["id"])
    assert fetched.status == 200, fetched.text
    assert set(fetched.json) == t069.DELETION_JOB_FIELDS, fetched.json
    assert fetched.json["remaining_scopes"] == list(t069.SCOPES)
    stored = t069.deletion_job(stack.dbname, job["id"])
    assert stored is not None
    assert stored["resource_type"] == t069.RESOURCE_CONVERSATION
    assert stored["deletion_revision"] == after["revision"]
    assert stored["completed_at"] is None
    details = t069.scope_details(stored)
    object_items = details["object_store"]["items"]
    assert any(item["artifact_id"] == sealed for item in object_items), object_items
    assert any(item.get("object_key") == sealed_row["object_key"] for item in object_items)
    assert details["checkpoints"]["run_ids"] == [run_id]
    assert set(details["search_index"]["request_ids"]) == {dispatched["id"], queued["id"]}

    # A repeated DELETE returns the same job, does not bump the revision again and
    # leaves exactly one job row.
    again = t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, conversation_id)
    assert again.status == 202, again.text
    assert again.json == job, (again.json, job)
    assert len(t069.deletion_jobs(
        stack.dbname, resource_type=t069.RESOURCE_CONVERSATION,
        resource_id=conversation_id,
    )) == 1
    assert f08.conversation_row(stack.dbname, conversation_id)["revision"] == after["revision"]

    # The audit trail records the deletion and the execution cancellation. A
    # repeated DELETE is a real request, so the foundation audits each one (the
    # job itself stays single, asserted above).
    actions = [row["action"] for row in t069.audit_events(
        stack.dbname, "deletion.requested", resource_id=conversation_id)]
    assert actions and set(actions) == {"deletion.requested"}, actions
    run_cancel_actions = [row["action"] for row in t069.audit_events(
        stack.dbname, "deletion.run_cancel", resource_id=run_id)]
    assert run_cancel_actions == ["deletion.run_cancel"], run_cancel_actions


def test_conversation_delete_is_owner_only_and_unknown_is_the_same_404(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    other = f08.account(stack)
    conversation_id = f08.conversation(owner.session)

    st.assert_error(
        t069.delete_resource(other.session, t069.RESOURCE_CONVERSATION, conversation_id),
        404, t069.ERROR_RESOURCE_NOT_FOUND,
    )
    st.assert_error(
        t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, f00b.new_uuid()),
        404, t069.ERROR_RESOURCE_NOT_FOUND,
    )
    st.assert_error(
        t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, "not-a-uuid"),
        404, t069.ERROR_RESOURCE_NOT_FOUND,
    )
    assert t069.deletion_jobs(stack.dbname, resource_type=t069.RESOURCE_CONVERSATION) == []
    assert f08.conversation_row(stack.dbname, conversation_id)["deleted_at"] is None


def test_deleted_conversation_body_is_unreadable_through_every_artifact_entry(
    f08_stack,
) -> None:
    """Post-review finding [P1]: DELETE must close the body-read entries at once.

    The reviewer's reproduction read the request body with 200 + full content after
    the DELETE answered 202, because only the conversation/request rows carried the
    tombstone. SECURITY §5 requires logical deletion to block API reads
    immediately, so the sealed message and the attachment of the conversation must
    be tombstoned in the same transaction (their bytes stay until T071 purges).
    """
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    attachment = f08.public_attachment(
        stack, owner.session, b"t074 post-review deleted conversation attachment\n"
    )
    request = f08.request_of(
        f08.submit_request(
            owner.session,
            conversation_id,
            "post-review: delete my sealed body",
            artifact_ids=[attachment],
            version_id=published.version_id,
            key=f00b.new_uuid(),
        )
    )
    message_artifact = f08.request_row(stack.dbname, request["id"])["input_artifact_id"]
    message_bytes = f08.artifact_bytes(stack, message_artifact)

    # The three artifact read entries really serve the live conversation's body.
    for entry in (t028.get_artifact, t028.download_artifact, t028.read_content):
        assert entry(owner.session, message_artifact).status == 200, entry.__name__
    for entry in (t028.get_artifact, t028.download_artifact, t028.read_content):
        assert entry(owner.session, attachment).status == 200, entry.__name__

    t069.deletion_of(
        t069.delete_resource(owner.session, t069.RESOURCE_CONVERSATION, conversation_id)
    )

    # Every read entry refuses both the message body and the request attachment.
    for artifact_id in (message_artifact, attachment):
        for entry in (t028.get_artifact, t028.download_artifact, t028.read_content):
            st.assert_error(
                entry(owner.session, artifact_id),
                404,
                t069.ERROR_RESOURCE_NOT_FOUND,
            )
    for artifact_id in (message_artifact, attachment):
        row = f08.artifact_row(stack.dbname, artifact_id)
        assert row["deleted_at"] is not None, row
        assert row["state"] == "deleted", row
    # The tombstone is logical: only T071 removes the real bytes.
    assert f08.artifact_bytes(stack, message_artifact) == message_bytes
