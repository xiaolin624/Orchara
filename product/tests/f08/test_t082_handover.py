"""F08/T082 (#83): the minimal handover preview and its confirmation gate.

A handover is not a hand-written summary: the server builds a bounded, metadata-only
preview from the named source Runs and the attachments the user selects, and the
submitted ``RequestInput.handoff`` must carry exactly that preview. The new Run's
sealed input closure is the new message, the explicitly attached artifacts and the
confirmed summary — never the old request's message bytes, the conversation
transcript or the old application's personal memory.

Real stack throughout: PostgreSQL 16, the compiled ``cmd/api``, real cookie+CSRF
HTTP, real sealed object bytes and real memory rows created through the contract
API. Every assertion reads the preview over HTTP or the authoritative rows/bytes.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from support import f00b, f08, t058, t077, t082
from support import stack as st

OLD_HISTORY = "OLD-INTERNAL-HISTORY-MARKER: the old application's sealed request text"
OLD_MEMORY = "OLD-PERSONAL-MEMORY-MARKER: the old application's confirmed memory"
NEW_MEMORY = "NEW-APP-MEMORY-MARKER: the new application's own memory"
EXPLICIT_ATTACHMENT = b"EXPLICIT-ATTACHMENT-MARKER: the one attachment the user selected\n"
NEW_MESSAGE = "NEW-REQUEST-MARKER: the message the user typed for the new application"


def _handoff_preview_fixture(stack):
    """One conversation, one source Run, one ready attachment and its preview."""
    owner = f08.account(stack)
    other = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    version_a = t077.publish_version(
        stack, owner.session, purpose="季度财务汇总", name="T082 交接来源应用")
    request_a = f08.request_of(f08.submit_request(
        owner.session, conversation_id, OLD_HISTORY,
        version_id=version_a.version_id, key=f00b.new_uuid(),
    ))
    assert request_a["state"] == "dispatched", request_a
    run_a = request_a["run_id"]
    attachment = f08.public_attachment(stack, owner.session, EXPLICIT_ATTACHMENT)
    preview_response = t082.preview(owner.session, conversation_id, [run_a], [attachment])
    preview = t082.preview_of(preview_response)
    return owner, other, conversation_id, version_a, request_a, run_a, attachment, preview


def _uuid_array(value: str) -> list[str]:
    body = value.strip()
    if body.startswith("{"):
        body = body[1:-1]
    if not body:
        return []
    return [item.strip().strip('"') for item in body.split(",")]


def test_the_handover_preview_is_a_bounded_server_summary(f08_stack) -> None:
    stack = f08_stack
    owner, other, conversation_id, version_a, request_a, run_a, attachment, preview = \
        _handoff_preview_fixture(stack)

    # The preview names exactly the facts the user chose.
    assert preview["conversation_id"] == conversation_id
    assert preview["source_run_ids"] == [run_a]
    assert preview["artifact_ids"] == [attachment]
    assert preview["from_version_id"] == version_a.version_id
    assert len(preview["source_runs"]) == 1
    source_run = preview["source_runs"][0]
    assert source_run["run_id"] == run_a
    assert source_run["application_id"] == version_a.application_id
    assert source_run["version_id"] == version_a.version_id
    assert source_run["version_no"] == 1
    assert source_run["application_name"] == "T082 交接来源应用"
    assert len(preview["artifacts"]) == 1
    artifact = preview["artifacts"][0]
    assert artifact["artifact_id"] == attachment
    assert artifact["purpose"] == "task_input"
    assert artifact["size_bytes"] == len(EXPLICIT_ATTACHMENT)
    assert artifact["sha256"] == hashlib.sha256(EXPLICIT_ATTACHMENT).hexdigest()

    # The summary is server-produced metadata: it names the handing-over facts and
    # is bounded. It must never contain the old request text or a memory body.
    summary = preview["summary"]
    assert "T082 交接来源应用" in summary, summary
    assert "v1" in summary, summary
    assert run_a[:8] in summary, summary
    assert "task_input" in summary, summary
    assert str(len(EXPLICIT_ATTACHMENT)) in summary, summary
    assert OLD_HISTORY not in summary, summary
    assert OLD_MEMORY not in summary, summary
    assert len(summary) <= t082.PREVIEW_LIMITS["max_summary_runes"], len(summary)
    # The preview is reproducible: the submit-time re-derivation can only match a
    # deterministic server object, never a per-call timestamp or random token.
    repeated = t082.preview_of(
        t082.preview(owner.session, conversation_id, [run_a], [attachment]))
    assert repeated["summary"] == summary
    assert repeated["summary_sha256"] == preview["summary_sha256"]

    # Over-broad selections are refused before any row is read: a handover is a
    # minimum, never "the whole conversation".
    too_many_runs = t082.preview(
        owner.session, conversation_id, [f00b.new_uuid() for _ in range(6)], [])
    st.assert_error(too_many_runs, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    assert too_many_runs.json["error"]["details"]["field"] == "handoff.source_run_ids"
    too_many_artifacts = t082.preview(
        owner.session, conversation_id, [run_a],
        [f00b.new_uuid() for _ in range(6)])
    st.assert_error(too_many_artifacts, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    assert too_many_artifacts.json["error"]["details"]["field"] == "handoff.artifact_ids"

    # Foreign / other-conversation source Runs are the same located refusal as T074.
    second_conversation = f08.conversation(owner.session)
    version_second = t077.publish_version(
        stack, owner.session, purpose="另一个来源", name="T082 第二个会话应用")
    foreign_conversation_run = f08.request_of(f08.submit_request(
        owner.session, second_conversation, "另一个会话里的 Run",
        version_id=version_second.version_id, key=f00b.new_uuid(),
    ))["run_id"]
    wrong_conversation = t082.preview(
        owner.session, conversation_id, [foreign_conversation_run], [])
    st.assert_error(wrong_conversation, 422, t082.ERROR_HANDOFF_SOURCE_UNKNOWN)

    other_conversation = f08.conversation(other.session)
    other_version = t077.publish_version(
        stack, other.session, purpose="别人的来源", name="T082 别人的来源应用")
    other_run = f08.request_of(f08.submit_request(
        other.session, other_conversation, "别人的 Run",
        version_id=other_version.version_id, key=f00b.new_uuid(),
    ))["run_id"]
    foreign_run = t082.preview(owner.session, conversation_id, [other_run], [])
    st.assert_error(foreign_run, 422, t082.ERROR_HANDOFF_SOURCE_UNKNOWN)

    # The conversation itself stays owner-only and tombstone-aware.
    other_view = t082.preview(other.session, conversation_id, [run_a], [])
    st.assert_error(other_view, 404, t082.ERROR_RESOURCE_NOT_FOUND)
    malformed = t082.preview(owner.session, "not-a-uuid", [run_a], [])
    st.assert_error(malformed, 404, t082.ERROR_RESOURCE_NOT_FOUND)
    tombstoned = f08.conversation(owner.session)
    deleted = f08.conversation_path(tombstoned)
    assert owner.session.client.request(
        "DELETE", deleted, csrf=owner.session.csrf).status == 202
    after_delete = t082.preview(owner.session, tombstoned, [run_a], [])
    st.assert_error(after_delete, 404, t082.ERROR_RESOURCE_NOT_FOUND)

    # Located body validation: required members, no unknown members, UUID shape.
    missing_artifacts = t082.preview(
        owner.session, conversation_id, [run_a], body={"source_run_ids": [run_a]})
    st.assert_error(missing_artifacts, 422, t082.ERROR_VALIDATION)
    unknown_member = t082.preview(
        owner.session, conversation_id, [run_a], [],
        body={"source_run_ids": [run_a], "artifact_ids": [], "extra": True})
    st.assert_error(unknown_member, 422, t082.ERROR_VALIDATION)
    no_sources = t082.preview(owner.session, conversation_id, [], [])
    st.assert_error(no_sources, 422, t082.ERROR_VALIDATION)
    bad_uuid = t082.preview(owner.session, conversation_id, ["nope"], [])
    st.assert_error(bad_uuid, 422, t082.ERROR_VALIDATION)

    # A foreign or non-ready attachment is refused with the T074 artifact vocabulary.
    other_attachment = f08.public_attachment(stack, other.session, b"not the caller's\n")
    foreign_artifact = t082.preview(
        owner.session, conversation_id, [run_a], [other_attachment])
    error = st.assert_error(foreign_artifact, 422, t082.ERROR_VALIDATION)
    codes = [entry["code"] for entry in error["details"]["errors"]]
    assert "artifact_not_visible" in codes, codes


def test_a_confirmed_server_preview_is_required_for_the_switch(f08_stack) -> None:
    stack = f08_stack
    owner, _other, conversation_id, version_a, request_a, run_a, attachment, preview = \
        _handoff_preview_fixture(stack)
    t058.create_memory_ok(owner.session, version_a.application_id, OLD_MEMORY)
    version_b = t077.publish_version(
        stack, owner.session, purpose="合同条款审阅", name="T082 目标应用")

    def submit(handoff, *, artifact_ids=None, key=None, message=NEW_MESSAGE):
        return f08.submit_request(
            owner.session, conversation_id, message,
            artifact_ids=list(artifact_ids if artifact_ids is not None else [attachment]),
            version_id=version_b.version_id,
            handoff=handoff,
            key=key or f00b.new_uuid(),
        )

    # The contract's confirmed:true is necessary but not sufficient.
    unconfirmed = submit({**t082.handoff_from(preview), "confirmed": False})
    st.assert_error(unconfirmed, 422, "handoff_not_confirmed")
    missing_confirmed = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": preview["artifact_ids"],
        "summary": preview["summary"],
    })
    st.assert_error(missing_confirmed, 422, t082.ERROR_VALIDATION)
    # Only the source request exists; none of the refusals created a second one.
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1

    # A hand-written transcript instead of the server preview is over-broad even
    # though it is "confirmed", and creates no request.
    transcript = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": preview["artifact_ids"],
        "summary": OLD_HISTORY,
        "confirmed": True,
    })
    error = st.assert_error(transcript, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    assert error["details"]["field"] == "handoff.summary", error
    tampered = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": preview["artifact_ids"],
        "summary": preview["summary"] + " " + OLD_HISTORY,
        "confirmed": True,
    })
    st.assert_error(tampered, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    memory_body = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": preview["artifact_ids"],
        "summary": OLD_MEMORY,
        "confirmed": True,
    })
    st.assert_error(memory_body, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1

    # An owned artifact that is neither produced by the source Run nor explicitly
    # attached to this request cannot be smuggled in through the handover.
    outside_scope = f08.public_attachment(stack, owner.session, b"outside the scope\n")
    over_broad_artifact = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": [outside_scope],
        "summary": preview["summary"],
        "confirmed": True,
    })
    error = st.assert_error(over_broad_artifact, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)
    assert outside_scope in error["details"]["artifact_ids"], error
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1

    # A well-formed but nonexistent id leaves through the located T074 artifact
    # gate as 422 `artifact_not_found`, never the 500 the pre-fix handover
    # metadata read produced. The handover scope gate now also runs before that
    # metadata read (defence in depth): only an allowed set is ever re-read.
    unknown_artifact = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": [f00b.new_uuid()],
        "summary": preview["summary"],
        "confirmed": True,
    })
    error = st.assert_error(unknown_artifact, 422, t082.ERROR_VALIDATION)
    codes = [entry["code"] for entry in error["details"]["errors"]]
    assert codes == ["artifact_not_found"], codes
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1

    # More attachment/run ids than the documented maximum is the same located 422.
    too_many = submit({
        "source_run_ids": preview["source_run_ids"],
        "artifact_ids": [f00b.new_uuid() for _ in range(6)],
        "summary": preview["summary"],
        "confirmed": True,
    })
    st.assert_error(too_many, 422, t082.ERROR_HANDOFF_SCOPE_EXCEEDED)

    # The confirmed server preview is accepted exactly once per idempotency key.
    key = f00b.new_uuid()
    handoff = t082.handoff_from(preview)
    accepted = submit(handoff, key=key)
    request = f08.request_of(accepted)
    # The source Run is still live, so the switch waits in the same queue.
    assert request["state"] == "queued", request
    # The same key with a different canonical body is the T074 conflict, not a
    # second handover.
    replayed = submit(handoff, key=key, message=NEW_MESSAGE + "（改写）")
    st.assert_error(replayed, 409, "idempotency_conflict")
    replay_same = submit(handoff, key=key)
    replay_body = f08.request_of(replay_same)
    assert replay_body["id"] == request["id"], replay_body
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 2

    # The durable handover record is exactly the preview the server produced.
    handoff_row = f08.handoff_row(stack.dbname, request["id"])
    assert handoff_row is not None
    assert handoff_row["confirmed_by"] == owner.principal_id
    assert _uuid_array(handoff_row["source_run_ids"]) == [run_a]
    assert _uuid_array(handoff_row["artifact_ids"]) == [attachment]
    assert handoff_row["from_version_id"] == version_a.version_id
    summary_artifact = f08.artifact_row(stack.dbname, handoff_row["summary_artifact_id"])
    assert summary_artifact["purpose"] == "handoff_summary"
    assert f08.artifact_bytes(stack, summary_artifact["id"]) == preview["summary"].encode()
    assert hashlib.sha256(preview["summary"].encode()).hexdigest() == preview["summary_sha256"]


def test_the_confirmed_handoff_artifact_reaches_the_new_run(f08_stack) -> None:
    """A source-Run artifact selected in the handover is a real input of the new Run.

    The artifact is not listed in the new request's own ``artifact_ids``: the only
    thing that carries it across is the confirmed handover record. The link on the
    source Run is a test-seeded ``output`` relation over a real ready artifact
    (the live Run in this stack has not produced a result yet), which is the same
    row shape the production finalizer writes.
    """
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    version_a = t077.publish_version(
        stack, owner.session, purpose="产出附件来源", name="T082 产出应用")
    version_b = t077.publish_version(
        stack, owner.session, purpose="接收交接的应用", name="T082 接收应用")
    request_a = f08.request_of(f08.submit_request(
        owner.session, conversation_id, "生成一个产出附件",
        version_id=version_a.version_id, key=f00b.new_uuid(),
    ))
    run_a = request_a["run_id"]
    produced = f08.public_attachment(
        stack, owner.session, b"PRODUCED-ARTIFACT-MARKER: a source Run output\n")
    st.sql(
        stack.dbname,
        "INSERT INTO platform.run_artifacts (run_id, artifact_id, attempt_token, relation) "
        f"VALUES ({st.quote_literal(run_a)}::uuid, {st.quote_literal(produced)}::uuid, "
        "NULL, 'output')",
    )
    preview = t082.preview_of(
        t082.preview(owner.session, conversation_id, [run_a], [produced]))
    assert "PRODUCED-ARTIFACT-MARKER" not in preview["summary"]

    request_b = f08.request_of(f08.submit_request(
        owner.session, conversation_id, NEW_MESSAGE,
        artifact_ids=[],  # carried only by the confirmed handover
        version_id=version_b.version_id,
        handoff=t082.handoff_from(preview),
        key=f00b.new_uuid(),
    ))
    assert request_b["state"] == "queued", request_b
    assert t082.run_cancel(owner.session, run_a).status == 202
    read_b = f08.get_request(owner.session, request_b["id"])
    assert read_b.status == 200, read_b.text
    assert read_b.json["state"] == "dispatched", read_b.json
    run_b = read_b.json["run_id"]

    request_b_row = f08.request_row(stack.dbname, request_b["id"])
    artifact_ids = st.sql(
        stack.dbname,
        "SELECT artifact_ids::text FROM platform.requests "
        f"WHERE id = {st.quote_literal(request_b['id'])}::uuid",
    )
    assert artifact_ids == "{}", artifact_ids
    handoff_row = f08.handoff_row(stack.dbname, request_b["id"])
    linked_ids = {link["artifact_id"] for link in f08.run_artifact_rows(stack.dbname, run_b)}
    assert linked_ids == {
        request_b_row["input_artifact_id"],
        produced,
        handoff_row["summary_artifact_id"],
    }, linked_ids
    payload = b"\n".join(blob for _, blob in f08.run_artifact_bytes(stack, run_b))
    assert b"PRODUCED-ARTIFACT-MARKER" in payload
    assert NEW_MESSAGE.encode() in payload
    assert OLD_HISTORY.encode() not in payload


def test_the_new_application_never_reads_the_old_history_or_memory(f08_stack) -> None:
    stack = f08_stack
    owner, _other, conversation_id, version_a, request_a, run_a, attachment, preview = \
        _handoff_preview_fixture(stack)
    version_b = t077.publish_version(
        stack, owner.session, purpose="合同条款审阅", name="T082 目标应用 B")
    t058.create_memory_ok(owner.session, version_a.application_id, OLD_MEMORY)
    t058.create_memory_ok(owner.session, version_b.application_id, NEW_MEMORY)
    assert t082.memory_texts_of_application(
        owner.session, stack, version_a.application_id) == [OLD_MEMORY]
    assert t082.memory_texts_of_application(
        owner.session, stack, version_b.application_id) == [NEW_MEMORY]

    request_b = f08.request_of(f08.submit_request(
        owner.session, conversation_id, NEW_MESSAGE,
        artifact_ids=[attachment],
        version_id=version_b.version_id,
        handoff=t082.handoff_from(preview),
        key=f00b.new_uuid(),
    ))
    assert request_b["state"] == "queued", request_b
    assert request_b["run_id"] is None

    # The source Run terminates; the queued switch advances on its read.
    assert t082.run_cancel(owner.session, run_a).status == 202
    read_b = f08.get_request(owner.session, request_b["id"])
    assert read_b.status == 200, read_b.text
    assert read_b.json["state"] == "dispatched", read_b.json
    assert read_b.header("X-Request-Application-Version-Id") == version_b.version_id
    run_b = read_b.json["run_id"]
    assert run_b is not None
    run_b_row = t082.run_row(stack.dbname, run_b)
    assert run_b_row["application_version_id"] == version_b.version_id
    assert run_b_row["owner_id"] == owner.principal_id

    # The sealed input closure is exactly the new message, the explicitly selected
    # attachment and the confirmed summary. The old request's sealed text is absent.
    request_b_row = f08.request_row(stack.dbname, request_b["id"])
    handoff_row = f08.handoff_row(stack.dbname, request_b["id"])
    links = f08.run_artifact_rows(stack.dbname, run_b)
    linked_ids = {link["artifact_id"] for link in links}
    assert linked_ids == {
        request_b_row["input_artifact_id"],
        attachment,
        handoff_row["summary_artifact_id"],
    }, links
    request_a_row = f08.request_row(stack.dbname, request_a["id"])
    assert request_a_row["input_artifact_id"] not in linked_ids

    # Every sealed byte of the new Run is read back; no old history/memory marker
    # survives, and the confirmed summary is present verbatim.
    payload = b"\n".join(blob for _, blob in f08.run_artifact_bytes(stack, run_b))
    assert NEW_MESSAGE.encode() in payload
    assert EXPLICIT_ATTACHMENT.strip() in payload
    assert preview["summary"].encode() in payload
    assert OLD_HISTORY.encode() not in payload
    assert OLD_MEMORY.encode() not in payload
    assert b"OLD-INTERNAL-HISTORY-MARKER" not in payload
    assert b"OLD-PERSONAL-MEMORY-MARKER" not in payload

    # The conversation's message projection still shows the old request (it is the
    # user's visible history), while the new Run's input never reads it.
    messages = f08.list_messages(owner.session, conversation_id)
    assert messages.status == 200, messages.text
    projected_request_ids = {item["request_id"] for item in messages.json["items"]}
    assert request_a["id"] in projected_request_ids
    assert request_b["id"] in projected_request_ids

    # Memory recall is scoped to the handling application: the new application can
    # only ever read its own memory, and the old memory's body is not a Run input.
    assert t082.memory_texts_of_application(
        owner.session, stack, version_b.application_id) == [NEW_MEMORY]
    assert t082.memory_texts_of_application(
        owner.session, stack, version_a.application_id) == [OLD_MEMORY]
    old_memory_rows = st.sql_rows(
        stack.dbname,
        "SELECT body_artifact_id::text AS body_artifact_id FROM platform.memories "
        f"WHERE application_id = {st.quote_literal(version_a.application_id)}::uuid "
        "AND deleted_at IS NULL",
    )
    assert old_memory_rows, "the old memory row disappeared unexpectedly"
    for row in old_memory_rows:
        assert row["body_artifact_id"] not in linked_ids, row
