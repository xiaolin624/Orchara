"""T074 (#75): the conversation slot, the ordered queue and cancellation.

The race test is the first thing written for this ticket (BRIEF §6.1): two really
concurrent ``submitRequest`` calls for one conversation must not both hold the
logical slot. There is no sleep and no sub-5-second timing assertion anywhere: the
winner/loser decision is read back from PostgreSQL rows after both HTTP calls
completed.
"""

from __future__ import annotations

import threading

from support import f00b, f08
from support import stack as st


def test_concurrent_submits_cannot_both_hold_the_single_conversation_slot(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)

    # Two independent real logins for the same principal, so neither HTTP request
    # can accidentally piggyback on the other's connection or CSRF state.
    second = f08.second_session(stack, owner)
    assert second.principal_id == owner.principal_id

    barrier = threading.Barrier(2)
    responses: dict[str, object] = {}

    def submit(name: str, session) -> None:
        barrier.wait(timeout=30)
        responses[name] = f08.submit_request(
            session,
            conversation_id,
            "race task " + name,
            version_id=published.version_id,
            key=f00b.new_uuid(),
        )

    threads = [
        threading.Thread(target=submit, args=("a", owner.session), daemon=True),
        threading.Thread(target=submit, args=("b", second), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not any(thread.is_alive() for thread in threads), "a submit never returned"

    assert set(responses) == {"a", "b"}, responses
    for name, response in responses.items():
        assert response.status == 202, (name, response.text)

    rows = f08.request_rows(stack.dbname, conversation_id)
    assert len(rows) == 2, rows
    assert sorted(row["state"] for row in rows) == ["dispatched", "queued"], rows

    # Exactly one live top-level Run and exactly one logical slot holder: the
    # lossy side is queued, has no run and owns no route_request job.
    live = f08.live_top_runs(stack.dbname, conversation_id)
    assert len(live) == 1, live
    holders = f08.slot_holder_rows(stack.dbname, conversation_id)
    assert len(holders) == 1, holders
    dispatched = next(row for row in rows if row["state"] == "dispatched")
    queued = next(row for row in rows if row["state"] == "queued")
    assert dispatched["run_id"] == live[0]["id"]
    assert queued["run_id"] is None
    assert f08.background_jobs(stack.dbname, target_id=queued["id"]) == []
    # An explicit version is not routed: the winner created no model-routing job.
    assert f08.background_jobs(
        stack.dbname, target_id=dispatched["id"], kind="route_request"
    ) == []

    # The handling version is visible on the read surface as documented headers.
    fetched = f08.get_request(owner.session, dispatched["id"])
    assert fetched.status == 200, fetched.text
    f08.assert_request_view(fetched.json)
    assert fetched.header("X-Request-Application-Version-Id") == published.version_id
    assert fetched.header("X-Request-Application-Version-No") == "1"
    assert fetched.header("X-Request-Conversation-Id") == conversation_id

    # Cancelling the dispatched request cancels its Run through the existing
    # execution cancel path, and the queued request takes the freed slot.
    cancelled = f08.cancel_request(owner.session, dispatched["id"])
    accepted = f08.cancel_accepted(cancelled)
    assert accepted["id"] == dispatched["id"]

    run = f08.run_rows(stack.dbname, conversation_id)[0]
    assert run["id"] == dispatched["run_id"]
    assert run["state"] == "cancelled", run
    promoted = f08.request_row(stack.dbname, queued["id"])
    assert promoted["state"] == "dispatched", promoted
    assert promoted["run_id"] is not None
    assert len(f08.live_top_runs(stack.dbname, conversation_id)) == 1
    assert len(f08.slot_holder_rows(stack.dbname, conversation_id)) == 1
    promoted_fetch = f08.get_request(owner.session, queued["id"])
    assert promoted_fetch.status == 200, promoted_fetch.text
    assert promoted_fetch.header("X-Request-Application-Version-Id") == published.version_id


def test_cancelling_the_slot_holder_advances_the_next_queued_request(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)

    # No explicit version: the request keeps the Request/queue/permission rules and
    # would be routed by F08/T079. This ticket's job is the queue itself.
    first = f08.request_of(f08.submit_request(owner.session, conversation_id, "first task"))
    assert first["state"] == "routing", first
    second_response = f08.submit_request(owner.session, conversation_id, "second task")
    second = f08.request_of(second_response)
    assert second["state"] == "queued", second
    assert second_response.header("X-Request-Queue-Position") == "2"
    third_response = f08.submit_request(owner.session, conversation_id, "third task")
    third = f08.request_of(third_response)
    assert third["state"] == "queued", third
    assert third_response.header("X-Request-Queue-Position") == "3"

    # The slot holder owns the one durable route_request intention; the queued
    # requests own no job at all.
    first_jobs = f08.background_jobs(stack.dbname, target_id=first["id"])
    assert [job["kind"] for job in first_jobs] == ["route_request"], first_jobs
    assert f08.background_jobs(stack.dbname, target_id=second["id"]) == []
    assert f08.background_jobs(stack.dbname, target_id=third["id"]) == []

    # Cancelling the slot holder (a pre-dispatch request) releases the slot in the
    # same transaction and admits exactly the next request, in order.
    cancelled = f08.cancel_accepted(f08.cancel_request(owner.session, first["id"]))
    assert cancelled == {"id": first["id"], "state": "cancelled"}
    assert f08.request_row(stack.dbname, first["id"])["state"] == "cancelled"
    assert f08.request_row(stack.dbname, second["id"])["state"] == "routing"
    assert f08.request_row(stack.dbname, third["id"])["state"] == "queued"
    second_jobs = f08.background_jobs(stack.dbname, target_id=second["id"])
    assert [job["kind"] for job in second_jobs] == ["route_request"], second_jobs
    assert f08.background_jobs(stack.dbname, target_id=third["id"]) == []
    holders = f08.slot_holder_rows(stack.dbname, conversation_id)
    assert [holder["id"] for holder in holders] == [second["id"]], holders

    # A repeated cancel of the already-cancelled request is idempotent: same 202,
    # no second job, and no further queue movement.
    again = f08.cancel_accepted(f08.cancel_request(owner.session, first["id"]))
    assert again == {"id": first["id"], "state": "cancelled"}
    assert f08.request_row(stack.dbname, third["id"])["state"] == "queued"
    assert len(f08.background_jobs(stack.dbname, target_id=second["id"])) == 1

    # Cancelling the active request admits the third one; nothing is promoted into
    # an empty queue.
    f08.cancel_accepted(f08.cancel_request(owner.session, second["id"]))
    assert f08.request_row(stack.dbname, third["id"])["state"] == "routing"
    assert len(f08.slot_holder_rows(stack.dbname, conversation_id)) == 1


def test_later_ordinary_requests_are_visible_in_the_conversation_timeline(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)

    first = f08.request_of(f08.submit_request(owner.session, conversation_id, "first visible"))
    second = f08.request_of(f08.submit_request(owner.session, conversation_id, "second visible"))

    fetched = f08.get_conversation(owner.session, conversation_id)
    assert fetched.status == 200, fetched.text
    body = fetched.json
    assert set(body) == f08.CONVERSATION_FIELDS, body
    assert body["id"] == conversation_id
    assert body["revision"] == 1

    listed = f08.list_conversations(owner.session)
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [conversation_id]
    assert listed.json["next_cursor"] is None

    messages = f08.list_messages(owner.session, conversation_id)
    assert messages.status == 200, messages.text
    items = messages.json["items"]
    assert items, messages.json
    for message in items:
        assert set(message) == f08.MESSAGE_FIELDS, message
    request_messages = [message for message in items if message["kind"] == "request"]
    assert {message["request_id"] for message in request_messages} == {first["id"], second["id"]}
    for message in request_messages:
        assert message["role"] == "user"
        artifact = f08.artifact_row(stack.dbname, message["body_artifact_id"])
        assert artifact["state"] == "ready"
        assert artifact["purpose"] == "request_input"
    # Newest first: the second request's message comes before the first one's.
    assert items[0]["request_id"] == second["id"]
    assert items[-1]["request_id"] == first["id"]

    # Cancelling the second (queued) request is a visible status message and does
    # not touch the active first request.
    f08.cancel_accepted(f08.cancel_request(owner.session, second["id"]))
    after = f08.list_messages(owner.session, conversation_id).json["items"]
    statuses = [message for message in after if message["kind"] == "status"]
    assert [message["request_id"] for message in statuses] == [second["id"]]
    assert statuses[0]["role"] == "system"
    assert statuses[0]["body_artifact_id"] is None
    assert f08.request_row(stack.dbname, first["id"])["state"] == "routing"
    assert len(f08.slot_holder_rows(stack.dbname, conversation_id)) == 1


def test_conversation_list_is_owner_scoped_and_paginates(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    other = f08.account(stack)
    first = f08.conversation(owner.session)
    second = f08.conversation(owner.session)
    foreign = f08.conversation(other.session)

    listed = f08.list_conversations(owner.session)
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [second, first]
    assert listed.json["next_cursor"] is None
    for item in listed.json["items"]:
        assert set(item) == f08.CONVERSATION_FIELDS, item

    page = f08.list_conversations(owner.session, limit=1)
    assert page.status == 200, page.text
    assert [item["id"] for item in page.json["items"]] == [second]
    cursor = page.json["next_cursor"]
    assert cursor
    rest = f08.list_conversations(owner.session, cursor=cursor)
    assert rest.status == 200, rest.text
    assert [item["id"] for item in rest.json["items"]] == [first]

    theirs = f08.list_conversations(other.session)
    assert [item["id"] for item in theirs.json["items"]] == [foreign]
    st.assert_error(
        f08.get_conversation(other.session, first), 404, f08.ERROR_RESOURCE_NOT_FOUND
    )
    assert f08.get_conversation(owner.session, first).status == 200


def test_a_run_cancelled_through_the_execution_path_advances_the_next_request(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    conversation_id = f08.conversation(owner.session)
    published = f08.publish_version(stack, owner.session)
    first = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "first fixed version",
            version_id=published.version_id, key=f00b.new_uuid(),
        )
    )
    second = f08.request_of(
        f08.submit_request(
            owner.session, conversation_id, "second fixed version",
            version_id=published.version_id, key=f00b.new_uuid(),
        )
    )
    assert first["state"] == "dispatched" and second["state"] == "queued"

    # The owner cancels the Run directly (the existing F03/T036 endpoint), not the
    # request: the dispatched request keeps its state and its Run becomes terminal.
    cancelled = owner.session.client.post(
        "/api/v1/runs/" + first["run_id"] + "/cancel", csrf=owner.session.csrf
    )
    assert cancelled.status == 202, cancelled.text
    run = next(
        item for item in f08.run_rows(stack.dbname, conversation_id)
        if item["id"] == first["run_id"]
    )
    assert run["state"] == "cancelled", run
    assert f08.request_row(stack.dbname, second["id"])["state"] == "queued"

    # The next read of the waiting request converges the queue: it is dispatched
    # from its fixed version without any unrelated write.
    fetched = f08.get_request(owner.session, second["id"])
    assert fetched.status == 200, fetched.text
    assert fetched.json["state"] == "dispatched", fetched.json
    assert fetched.header("X-Request-Application-Version-Id") == published.version_id
    assert f08.request_row(stack.dbname, second["id"])["state"] == "dispatched"
    assert len(f08.live_top_runs(stack.dbname, conversation_id)) == 1
