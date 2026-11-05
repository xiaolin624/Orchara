"""T084 (#85): queue, expiry and failure states are visible through the real REST surface.

These are the state readings a user meets besides the routing decision itself:
the ordered queue position, the expiry of a pending clarification, cancellation of
the slot-holding Run, and the stable ``error_code`` on a no-match request. Every
value is read from the real API response or the authoritative PostgreSQL rows; no
state is invented for the page test (``test_t084_pages`` executes the real page
bytes against these same shapes).
"""

from __future__ import annotations

from support import f00b, f02a, f08, t033, t069, t077, t079, t082


def _catalog(stack):
    """One publisher, one second user with a real read grant, one sales app."""
    publisher = f08.account(stack)
    second = f08.account(stack)
    t079.seed_route_control_model(stack, publisher.principal_id)
    admin = f00b.admin_session(stack)
    # The T068 card shape: the unique cell's English messages must match it.
    version = t077.publish_version(
        stack,
        publisher.session,
        name="T084 states sales",
        purpose="summarise sales",
        includes=["aggregation"],
        excludes=["external writes"],
        required_inputs=["sales csv"],
        expected_outputs=["report"],
        examples=["monthly report", "weekly digest"],
    )
    f02a.grant_read(admin, second.principal_id, version.application_id)
    attachment = t069.upload_ready_artifact(
        stack, second.session, t033.SALES_CSV, purpose="task_input", media_type="text/csv"
    )
    return publisher, second, version, attachment


def test_the_queue_position_is_visible_and_the_slot_advances_in_order(f08_stack) -> None:
    stack = f08_stack
    _publisher, second, published, attachment = _catalog(stack)
    conversation = f08.conversation(second.session)

    first = f08.request_of(
        f08.submit_request(second.session, conversation, "summarise this csv",
                           artifact_ids=[attachment])
    )
    assert first["state"] == "routing", first
    route = t079.route_once(stack)
    assert route.completed.returncode == 0, route.stdout
    first_row = t079.request_state(stack.dbname, first["id"])
    assert first_row["state"] == "dispatched", first_row
    run_first = first_row["run_id"]

    second_response = f08.submit_request(
        second.session, conversation, "summarise this csv", artifact_ids=[attachment]
    )
    second_request = f08.request_of(second_response)
    assert second_request["state"] == "queued", second_request
    # UX: the rank is the server's ordered queue position, never a guess.
    assert second_response.header("X-Request-Queue-Position") == "2"
    assert t079.request_state(stack.dbname, second_request["id"])["run_id"] is None
    assert [item["id"] for item in f08.live_top_runs(stack.dbname, conversation)] == [run_first]

    # The queue read shows both rows in row order, the first holding the slot.
    queue_read = f08.get_request(second.session, second_request["id"])
    assert queue_read.status == 200, queue_read.text
    assert queue_read.json["state"] == "queued", queue_read.json
    assert queue_read.header("X-Request-Queue-Position") == "2", queue_read.headers

    # Cancelling the slot holder cancels its Run through the real cancel path and
    # promotes the queued request; the order is the row order, not arrival luck.
    accepted = f08.cancel_accepted(f08.cancel_request(second.session, first["id"]))
    assert accepted["id"] == first["id"]
    assert accepted["state"] == "dispatched", accepted  # post-dispatch keeps its state
    cancelled_run = t082.run_row(stack.dbname, run_first)
    assert cancelled_run["state"] == "cancelled", cancelled_run
    promoted = t079.request_state(stack.dbname, second_request["id"])
    assert promoted["state"] == "routing", promoted
    assert promoted["run_id"] is None

    route = t079.route_once(stack)
    assert route.completed.returncode == 0, route.stdout
    promoted = t079.request_state(stack.dbname, second_request["id"])
    assert promoted["state"] == "dispatched", promoted
    run_second = promoted["run_id"]
    assert run_second and run_second != run_first
    assert t082.run_row(stack.dbname, run_second)["application_version_id"] == published.version_id
    # One live top-level Run at most, and it is the promoted one.
    assert [run["id"] for run in f08.live_top_runs(stack.dbname, conversation)] == [run_second]
    read = f08.get_request(second.session, second_request["id"])
    assert read.status == 200, read.text
    assert read.header("X-Request-Application-Version-Id") == published.version_id


def test_an_expired_clarification_is_failed_and_explained(f08_stack) -> None:
    stack = f08_stack
    _publisher, second, published, _attachment = _catalog(stack)
    conversation = f08.conversation(second.session)

    # The sales card requires one input; without an attachment the server asks.
    submitted = f08.submit_request(second.session, conversation, "summarise this csv")
    request = f08.request_of(submitted)
    route = t079.route_once(stack)
    assert route.completed.returncode == 0, route.stdout
    waiting = t079.request_state(stack.dbname, request["id"])
    assert waiting["state"] == "awaiting_input", waiting
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])
    assert interaction and interaction["kind"] == "input", interaction

    # The 15-minute interaction deadline moves into the past (test-owned SQL); the
    # next reply is the real lazy-expiry seam.
    t079.expire_interaction(stack, request["id"], seconds=5)
    expired = t079.reply(second.session, request["id"], {
        "interaction_id": interaction["id"],
        "message": "expired reply",
    })
    assert expired.status == 409, expired.text
    assert expired.json["error"]["code"] == "interaction_closed", expired.text

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "interaction_expired", row
    assert f08.run_rows(stack.dbname, conversation) == []
    closed = t079.interaction_rows(stack.dbname, request["id"])[0]
    assert closed["state"] == "expired", closed

    # The user-visible read carries the stable code; the page test proves the real
    # chat.js renders it as "过期 … 重新提交".
    read = f08.get_request(second.session, request["id"])
    assert read.status == 200, read.text
    assert read.json["state"] == "failed", read.json
    assert read.json["error_code"] == "interaction_expired", read.json
    assert read.json["interaction"] is None, read.json


def test_a_no_match_request_is_failed_explained_and_creates_nothing(f08_stack) -> None:
    stack = f08_stack
    _publisher, second, _published, _attachment = _catalog(stack)
    conversation = f08.conversation(second.session)

    submitted = f08.submit_request(second.session, conversation, "写一首关于春天的诗")
    request = f08.request_of(submitted)
    assert request["state"] == "routing", request
    route = t079.route_once(stack)
    assert route.completed.returncode == 0, route.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "no_matching_application", row
    assert t079.candidate_rows(stack.dbname, request["id"]) == []
    assert f08.run_rows(stack.dbname, conversation) == []
    assert t079.interaction_rows(stack.dbname, request["id"]) == []
    decisions = t079.decision_rows(stack.dbname, request["id"])
    assert [item["decision"] for item in decisions] == ["no_match"], decisions

    read = f08.get_request(second.session, request["id"])
    assert read.status == 200, read.text
    assert read.json["error_code"] == "no_matching_application", read.json
    # The application the user can use was not silently forced into this request.
    assert read.header("X-Request-Application-Version-Id") is None
