"""T074 (#75): fixed-version submission, idempotency and the published Run.

The fixed-version path is exercised through the real HTTP API, with a version whose
manifest was produced by the real compiler and whose review/version rows are the
publication transaction's shape. No router and no model call exists on this path:
the assertions read the authoritative rows back from PostgreSQL and the sealed
bytes back from the object store.
"""

from __future__ import annotations

import json

from support import f00b, f08
from support import stack as st


def test_fixed_version_submit_creates_the_published_run_without_any_router(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    attachment = f08.public_attachment(
        stack, owner.session, b"attachment bytes for the fixed-version request\n"
    )
    message = "summarise the attached report with the fixed published version"

    response = f08.submit_request(
        owner.session,
        conversation_id,
        message,
        artifact_ids=[attachment],
        version_id=published.version_id,
        key=f00b.new_uuid(),
    )
    request = f08.request_of(response)
    assert request["state"] == "dispatched", request
    assert request["run_id"] is not None, request
    assert request["interaction"] is None
    assert request["error_code"] is None
    assert response.header("X-Request-Application-Version-Id") == published.version_id
    assert response.header("X-Request-Application-Version-No") == "1"
    assert response.header("X-Request-Conversation-Id") == conversation_id

    row = f08.request_row(stack.dbname, request["id"])
    assert row["state"] == "dispatched"
    assert row["requested_version_id"] == published.version_id
    assert row["run_id"] == request["run_id"]
    assert row["conversation_id"] == conversation_id
    assert row["owner_id"] == owner.principal_id

    # The message text is the sealed input artifact's exact bytes; no second copy
    # of the request text exists on the request row.
    input_artifact = f08.artifact_row(stack.dbname, row["input_artifact_id"])
    assert input_artifact["purpose"] == "request_input"
    assert input_artifact["state"] == "ready"
    assert input_artifact["sha256"] == st.sha256_hex(message.encode())
    assert f08.artifact_bytes(stack, row["input_artifact_id"]) == message.encode()

    # The published Run: kind=published, the frozen version, the sealed message as
    # input, a queued start job and the real input, all in the request's conversation.
    run = next(
        item
        for item in f08.run_rows(stack.dbname, conversation_id)
        if item["id"] == request["run_id"]
    )
    assert run["kind"] == "published"
    assert run["state"] == "queued"
    assert run["parent_run_id"] is None
    assert run["application_version_id"] == published.version_id
    assert run["input_artifact_id"] == row["input_artifact_id"]
    assert run["manifest_sha256"] == published.manifest_sha256

    snapshot = f08.snapshot_row(stack.dbname, run["id"])
    assert snapshot["kind"] == "published"
    assert snapshot["application_id"] == published.application_id
    assert snapshot["application_version_id"] == published.version_id
    assert snapshot["draft_revision"] is None
    assert snapshot["manifest_sha256"] == published.manifest_sha256
    assert snapshot["manifest_artifact_id"] == published.manifest_artifact_id
    manifest = json.loads(snapshot["manifest"])
    assert manifest["definition"]["policy"]["budget_limit"] == "1.00"
    assert manifest["compiler_version"] == published.manifest["compiler_version"]

    # The Run's input closure is exactly the explicit inputs: the sealed message and
    # the caller's attachment. Nothing from the conversation's history is a Run
    # input.
    links = f08.run_artifact_rows(stack.dbname, run["id"])
    assert {link["artifact_id"] for link in links} == {row["input_artifact_id"], attachment}
    assert {link["relation"] for link in links} == {"input"}
    assert f08.artifact_bytes(stack, attachment).startswith(b"attachment bytes")

    # Exactly one start job for the Run, and no route_request job/model call for
    # this request: an explicit version bypasses model matching entirely.
    jobs = st.sql_rows(
        stack.dbname,
        "SELECT id::text AS id, kind, state FROM platform.jobs "
        f"WHERE run_id = {st.quote_literal(run['id'])}::uuid ORDER BY kind",
    )
    assert [(job["kind"], job["state"]) for job in jobs] == [("start", "ready")], jobs
    assert f08.background_jobs(stack.dbname, target_id=request["id"]) == []
    assert f08.model_call_rows(stack.dbname, request["id"]) == []

    # The existing owner-only Run read sees the published Run.
    run_read = owner.session.client.get("/api/v1/runs/" + run["id"])
    assert run_read.status == 200, run_read.text
    assert run_read.json["kind"] == "published"
    assert run_read.json["application_version_id"] == published.version_id
    assert run_read.header("X-Manifest-SHA256") == published.manifest_sha256

    # A second request in the same conversation queues behind the live Run even
    # though it also names a fixed version.
    queued = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "second fixed-version task",
            version_id=published.version_id, key=f00b.new_uuid(),
        )
    )
    assert queued["state"] == "queued", queued
    assert queued["run_id"] is None
    queued_fetch = f08.get_request(owner.session, queued["id"])
    assert queued_fetch.header("X-Request-Queue-Position") == "2"
    assert queued_fetch.header("X-Request-Application-Version-Id") is None


def test_idempotent_resubmission_replays_and_a_different_body_conflicts(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    key = f00b.new_uuid()
    message = "idempotent request message"

    first = f08.submit_request(
        owner.session, conversation_id, message,
        version_id=published.version_id, key=key,
    )
    first_body = f08.request_of(first)

    replay = f08.submit_request(
        owner.session, conversation_id, message,
        version_id=published.version_id, key=key,
    )
    assert replay.status == 202, replay.text
    assert replay.json == first_body, (replay.json, first_body)
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
    receipts = f08.idempotency_rows(stack.dbname)
    assert len(receipts) == 1, receipts
    assert receipts[0]["key"] == key
    assert receipts[0]["response_code"] == 202
    assert json.loads(receipts[0]["response_json"]) == first_body

    # Same key, different content is a conflict and creates nothing new.
    conflict = f08.submit_request(
        owner.session, conversation_id, "a different message",
        version_id=published.version_id, key=key,
    )
    st.assert_error(conflict, 409, f08.ERROR_IDEMPOTENCY_CONFLICT)
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1
    assert len(f08.run_rows(stack.dbname, conversation_id)) == 1
    assert len(f08.idempotency_rows(stack.dbname)) == 1

    # Same content with a new key is a new request (a distinct intention).
    another = f08.submit_request(
        owner.session, conversation_id, message,
        version_id=published.version_id, key=f00b.new_uuid(),
    )
    assert another.status == 202, another.text
    assert another.json["id"] != first_body["id"]
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 2

    # The key is required and one value only.
    missing = f08.submit_request(
        owner.session, conversation_id, message,
        version_id=published.version_id, omit_key=True,
    )
    st.assert_error(missing, 400, "idempotency_key_required")


def test_a_revoked_version_is_refused_before_any_request_or_run_exists(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    f08.revoke_version(stack.dbname, published.version_id, owner.principal_id)

    response = f08.submit_request(
        owner.session, conversation_id, "run the revoked version",
        version_id=published.version_id, key=f00b.new_uuid(),
    )
    st.assert_error(response, 409, f08.ERROR_VERSION_REVOKED)
    assert f08.request_rows(stack.dbname, conversation_id) == []
    assert f08.run_rows(stack.dbname, conversation_id) == []
    assert f08.idempotency_rows(stack.dbname) == []

    # An unknown version id and another principal's version are the same located
    # 404 (no enumeration), and still create nothing.
    st.assert_error(
        f08.submit_request(
            owner.session, conversation_id, "unknown version",
            version_id=f00b.new_uuid(), key=f00b.new_uuid(),
        ),
        404,
        f08.ERROR_RESOURCE_NOT_FOUND,
    )
    other = f08.account(stack)
    foreign = f08.publish_version(stack, other.session)
    st.assert_error(
        f08.submit_request(
            owner.session, conversation_id, "someone else's version",
            version_id=foreign.version_id, key=f00b.new_uuid(),
        ),
        404,
        f08.ERROR_RESOURCE_NOT_FOUND,
    )
    assert f08.request_rows(stack.dbname, conversation_id) == []


def test_submit_validates_the_contract_body_and_the_referenced_artifacts(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)

    missing_conversation = f08.submit_request(
        owner.session, f00b.new_uuid(), "hello", key=f00b.new_uuid()
    )
    st.assert_error(missing_conversation, 404, f08.ERROR_RESOURCE_NOT_FOUND)

    no_artifacts = f08.submit_request(
        owner.session, conversation_id, "hello",
        body={"conversation_id": conversation_id, "message": "hello"},
        key=f00b.new_uuid(),
    )
    st.assert_error(no_artifacts, 422, f08.ERROR_VALIDATION)
    assert no_artifacts.json["error"]["details"]["field"] == "artifact_ids"

    empty_message = f08.submit_request(
        owner.session, conversation_id, "",
        body={"conversation_id": conversation_id, "message": "", "artifact_ids": []},
        key=f00b.new_uuid(),
    )
    st.assert_error(empty_message, 422, f08.ERROR_VALIDATION)

    too_many = f08.submit_request(
        owner.session, conversation_id, "too many",
        body={
            "conversation_id": conversation_id,
            "message": "too many",
            "artifact_ids": [f00b.new_uuid() for _ in range(6)],
        },
        key=f00b.new_uuid(),
    )
    st.assert_error(too_many, 422, f08.ERROR_VALIDATION)

    other = f08.account(stack)
    foreign_attachment = f08.public_attachment(stack, other.session, b"not yours\n")
    foreign = f08.submit_request(
        owner.session, conversation_id, "foreign attachment",
        artifact_ids=[foreign_attachment], key=f00b.new_uuid(),
    )
    error = st.assert_error(foreign, 422, f08.ERROR_VALIDATION)
    codes = [entry["code"] for entry in error["details"]["errors"]]
    assert "artifact_not_visible" in codes, error

    unknown_attachment = f08.submit_request(
        owner.session, conversation_id, "missing attachment",
        artifact_ids=[f00b.new_uuid()], key=f00b.new_uuid(),
    )
    error = st.assert_error(unknown_attachment, 422, f08.ERROR_VALIDATION)
    assert error["details"]["errors"][0]["code"] == "artifact_not_found"

    # A valid submission afterwards proves the refusals wrote nothing partial.
    ok = f08.submit_request(
        owner.session, conversation_id, "finally valid",
        version_id=published.version_id, key=f00b.new_uuid(),
    )
    assert ok.status == 202, ok.text
    assert len(f08.request_rows(stack.dbname, conversation_id)) == 1
