"""T074 (#75): a new application never inherits the old internal history or memory.

The boundary is enforced by construction, not by wording: a published Run's input
closure is exactly the sealed message, the caller's explicit ``artifact_ids`` and
the confirmed handover summary. The conversation's message projection is a
user-visible read model and is never a Run input; personal memory is recalled per
(owner, application) scope, so application A's memory is not visible to a request
handled by application B. The test seeds real memories through the memory API, runs
both applications in one conversation and reads every sealed byte back.
"""

from __future__ import annotations

import json

from support import f00b, f08, t054, t058
from support import stack as st

OLD_HISTORY = "OLD-INTERNAL-HISTORY-MARKER: application A's sealed message"
OLD_MEMORY = "OLD-PERSONAL-MEMORY-MARKER: application A's personal memory"
NEW_MEMORY = "NEW-APP-MEMORY-MARKER: application B's own memory"
EXPLICIT_ATTACHMENT = b"EXPLICIT-ATTACHMENT-MARKER: the one attachment the user chose\n"
HANDOVER_SUMMARY = "HANDOVER-SUMMARY-MARKER: only this confirmed summary crosses the boundary"
NEW_MESSAGE = "NEW-REQUEST-MARKER: the message the user typed for application B"


def _uuid_list(value: str) -> list[str]:
    body = value.strip()
    if body.startswith("{"):
        body = body[1:-1]
    if not body:
        return []
    return [item.strip().strip('"') for item in body.split(",")]


def test_a_new_application_never_reads_the_old_history_or_personal_memory(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)

    version_a = f08.publish_version(stack, owner.session, name="t074 application A")
    version_b = f08.publish_version(stack, owner.session, name="t074 application B")
    # Real personal memories, each scoped to its own application.
    memory_a = t058.create_memory_ok(owner.session, version_a.application_id, OLD_MEMORY)
    memory_b = t058.create_memory_ok(owner.session, version_b.application_id, NEW_MEMORY)
    scoped_a = t054.list_memories(owner.session, application_id=version_a.application_id)
    scoped_b = t054.list_memories(owner.session, application_id=version_b.application_id)
    assert [item["id"] for item in scoped_a.json["items"]] == [memory_a["id"]]
    assert [item["id"] for item in scoped_b.json["items"]] == [memory_b["id"]]

    request_a = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, OLD_HISTORY,
            version_id=version_a.version_id, key=f00b.new_uuid(),
        )
    )
    assert request_a["state"] == "dispatched"
    run_a = request_a["run_id"]

    attachment = f08.public_attachment(stack, owner.session, EXPLICIT_ATTACHMENT)
    handoff = {
        "source_run_ids": [run_a],
        "artifact_ids": [attachment],
        "summary": HANDOVER_SUMMARY,
        "confirmed": True,
    }
    request_b = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, NEW_MESSAGE,
            artifact_ids=[attachment],
            version_id=version_b.version_id,
            handoff=handoff,
            key=f00b.new_uuid(),
        )
    )
    # The old Run is still live, so the second request waits for the slot.
    assert request_b["state"] == "queued", request_b
    assert request_b["run_id"] is None

    # Cancelling the old request releases the slot and dispatches the second one.
    f08.cancel_accepted(f08.cancel_request(owner.session, request_a["id"]))
    promoted = f08.request_row(stack.dbname, request_b["id"])
    assert promoted["state"] == "dispatched", promoted
    run_b = promoted["run_id"]
    assert run_b is not None
    run_b_row = next(
        item for item in f08.run_rows(stack.dbname, conversation_id) if item["id"] == run_b
    )
    assert run_b_row["application_version_id"] == version_b.version_id
    assert run_b_row["state"] == "queued"

    # The handover record is durable and names exactly what the user confirmed.
    handoff_row = f08.handoff_row(stack.dbname, request_b["id"])
    assert handoff_row is not None
    assert handoff_row["confirmed_by"] == owner.principal_id
    assert _uuid_list(handoff_row["source_run_ids"]) == [run_a]
    assert _uuid_list(handoff_row["artifact_ids"]) == [attachment]
    assert handoff_row["from_version_id"] == version_a.version_id
    summary_artifact = f08.artifact_row(stack.dbname, handoff_row["summary_artifact_id"])
    assert summary_artifact["purpose"] == "handoff_summary"
    assert f08.artifact_bytes(stack, summary_artifact["id"]) == HANDOVER_SUMMARY.encode()

    # The new Run's input closure is exactly the new message, the explicit
    # attachment and the confirmed summary. It does not include request A's input
    # (the old internal history) and it reads nothing from application A's memory.
    links = f08.run_artifact_rows(stack.dbname, run_b)
    linked_ids = {link["artifact_id"] for link in links}
    request_a_row = f08.request_row(stack.dbname, request_a["id"])
    assert request_a_row["input_artifact_id"] not in linked_ids
    assert linked_ids == {
        promoted["input_artifact_id"],
        attachment,
        handoff_row["summary_artifact_id"],
    }, links
    payload = b"\n".join(blob for _, blob in f08.run_artifact_bytes(stack, run_b))
    assert NEW_MESSAGE.encode() in payload
    assert HANDOVER_SUMMARY.encode() in payload
    assert EXPLICIT_ATTACHMENT.strip() in payload
    assert b"OLD-INTERNAL-HISTORY-MARKER" not in payload
    assert b"OLD-PERSONAL-MEMORY-MARKER" not in payload

    # Memory recall stays scoped to the handling application: the new application
    # sees its own memory and never application A's.
    scoped_b_after = t054.list_memories(owner.session, application_id=version_b.application_id)
    texts = [t058.text_of_memory(stack, t058.row_of_memory(stack.dbname, item["id"]))
             for item in scoped_b_after.json["items"]]
    assert NEW_MEMORY in texts
    assert OLD_MEMORY not in texts
    scoped_a_after = t054.list_memories(owner.session, application_id=version_a.application_id)
    texts_a = [t058.text_of_memory(stack, t058.row_of_memory(stack.dbname, item["id"]))
               for item in scoped_a_after.json["items"]]
    assert OLD_MEMORY in texts_a


def test_the_handover_needs_an_explicit_minimal_confirmation(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    other = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    version_a = f08.publish_version(stack, owner.session, name="t074 handover A")
    version_b = f08.publish_version(stack, owner.session, name="t074 handover B")
    attachment = f08.public_attachment(stack, owner.session, b"confirmed input\n")

    run = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "handover source",
            version_id=version_a.version_id, key=f00b.new_uuid(),
        )
    )["run_id"]
    assert run is not None

    def submit_with(payload: dict):
        return f08.submit_request(
            owner.session, conversation_id, "handoff task",
            version_id=version_b.version_id, handoff=payload, key=f00b.new_uuid(),
        )

    unconfirmed = submit_with({
        "source_run_ids": [run],
        "artifact_ids": [attachment],
        "summary": "summary",
        "confirmed": False,
    })
    st.assert_error(unconfirmed, 422, "handoff_not_confirmed")

    missing_summary = submit_with({
        "source_run_ids": [run],
        "artifact_ids": [attachment],
        "summary": "   ",
        "confirmed": True,
    })
    st.assert_error(missing_summary, 422, f08.ERROR_VALIDATION)

    no_sources = submit_with({
        "source_run_ids": [],
        "artifact_ids": [attachment],
        "summary": "summary",
        "confirmed": True,
    })
    st.assert_error(no_sources, 422, f08.ERROR_VALIDATION)

    # A Run of another principal cannot be handed over even with confirmed=true.
    other_conversation = f08.conversation(other.session)
    other_version = f08.publish_version(stack, other.session, name="t074 other handover")
    foreign_run = f08.request_of(
        f08.submit_request(
            other.session, other_conversation, "someone else's source",
            version_id=other_version.version_id, key=f00b.new_uuid(),
        )
    )["run_id"]
    foreign_source = submit_with({
        "source_run_ids": [foreign_run],
        "artifact_ids": [attachment],
        "summary": "summary",
        "confirmed": True,
    })
    st.assert_error(foreign_source, 422, "handoff_source_unknown")

    # A source Run in a different conversation of the same owner is refused too.
    second_conversation = f08.conversation(owner.session)
    foreign_conversation_run = f08.request_of(
        f08.submit_request(
            owner.session, second_conversation, "different conversation source",
            version_id=version_a.version_id, key=f00b.new_uuid(),
        )
    )["run_id"]
    wrong_conversation = submit_with({
        "source_run_ids": [foreign_conversation_run],
        "artifact_ids": [attachment],
        "summary": "summary",
        "confirmed": True,
    })
    st.assert_error(wrong_conversation, 422, "handoff_source_unknown")

    # A foreign attachment cannot be smuggled in through the handover either.
    foreign_attachment = f08.public_attachment(stack, other.session, b"not the caller's\n")
    foreign_artifact = submit_with({
        "source_run_ids": [run],
        "artifact_ids": [foreign_attachment],
        "summary": "summary",
        "confirmed": True,
    })
    error = st.assert_error(foreign_artifact, 422, f08.ERROR_VALIDATION)
    assert "artifact_not_visible" in [entry["code"] for entry in error["details"]["errors"]]
    # None of the refusals created a request or a handover record.
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1
    assert f08.handoff_row(stack.dbname, "00000000-0000-4000-8000-000000000000") is None
