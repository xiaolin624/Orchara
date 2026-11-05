"""F08/T079 (#80): refusals, the control budget, duplicate replies and expiry.

All assertions read the authoritative PostgreSQL rows (request/interaction/run/
candidate/decision/ledger) or the compiled API's contract responses. Expiry moves a
real deadline into the past by a 5 s margin instead of sleeping; no test races a
clock boundary.
"""

from __future__ import annotations

import json

from support import f08, t079
from support import stack as st


def _prepare(stack, owner, *, purpose="quarterly sales report", required_inputs=None):
    t079.seed_route_control_model(stack, owner.principal_id)
    return t079.publish(stack, owner.session, purpose=purpose,
                        required_inputs=required_inputs if required_inputs is not None else [])


def test_a_model_suggestion_outside_the_authorized_candidate_set_is_refused(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    real = t079.publish(stack, owner.session, purpose="quarterly sales report",
                        required_inputs=[])
    fake = t079.publish_offtopic(stack, owner.session, name="t079 fake candidate")
    t079.seed_route_control_model(stack, owner.principal_id)
    conversation = f08.conversation(owner.session)

    # The fake version is really published and readable; it is not a candidate
    # because its card does not match the request. The dev-stub router echoes the
    # UUID written in the user text, which is exactly a suggestion outside the set.
    version_read = owner.session.client.get("/api/v1/versions/" + fake.version_id)
    assert version_read.status == 200, version_read.text

    message = "please build a quarterly sales report for version " + fake.version_id
    submitted = t079.submit(owner.session, conversation, message)
    request = f08.request_of(submitted)
    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "route_candidate_invalid", row
    assert row["run_id"] is None, row
    assert f08.run_rows(stack.dbname, conversation) == []
    # No interaction was fabricated from the refused suggestion.
    assert t079.interaction_rows(stack.dbname, request["id"]) == []
    candidates = t079.candidate_rows(stack.dbname, request["id"])
    assert [candidate["application_version_id"] for candidate in candidates] == [real.version_id]
    decisions = t079.decision_rows(stack.dbname, request["id"])
    assert decisions and decisions[0]["decision"] == "select", decisions
    # The refused suggestion is kept in the decision raw payload for audit, but it
    # is not in the candidate snapshot and it created no interaction/Run.
    assert fake.version_id in decisions[0]["raw_decision"], decisions[0]
    assert decisions[0]["model_call_id"], decisions[0]
    calls = t079.control_calls(stack.dbname, request["id"])
    assert len(calls) == 1 and calls[0]["state"] == "succeeded", calls


def test_a_forged_reply_candidate_is_refused_and_the_interaction_stays_answerable(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner)
    outside = t079.publish_offtopic(stack, owner.session, name="t079 outside candidate")
    second = t079.publish(stack, owner.session, purpose="quarterly sales report beta",
                          required_inputs=[], name="t079 second candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "please build a quarterly sales report")
    request = f08.request_of(submitted)
    t079.route_once(stack)
    assert t079.request_state(stack.dbname, request["id"])["state"] == "awaiting_selection"
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])

    forged = t079.reply(owner.session, request["id"], {
        "interaction_id": interaction["id"],
        "candidate_version_id": outside.version_id,
    })
    st.assert_error(forged, 422, "candidate_not_in_snapshot")
    assert t079.request_state(stack.dbname, request["id"])["state"] == "awaiting_selection"
    assert t079.pending_interaction_row(stack.dbname, request["id"])["id"] == interaction["id"]
    assert f08.run_rows(stack.dbname, conversation) == []

    # A version that was not in the snapshot is refused even though it is published;
    # the real pending interaction is still answerable.
    valid = t079.reply(owner.session, request["id"], {
        "interaction_id": interaction["id"],
        "candidate_version_id": second.version_id,
    })
    assert valid.status == 202, valid.text
    t079.route_once(stack)
    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1 and runs[0]["application_version_id"] == second.version_id, runs


def test_the_third_control_call_is_the_last_and_the_budget_fails_typed(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner, required_inputs=["sales csv", "region mapping", "currency table"])
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "please build a quarterly sales report")
    request = f08.request_of(submitted)
    t079.route_once(stack)

    artifacts = []
    for index in range(3):
        artifacts.append(f08.public_attachment(
            stack, owner.session, ("input-%d\n" % index).encode()))
        interaction = t079.pending_interaction_row(stack.dbname, request["id"])
        assert interaction and interaction["kind"] == "input", interaction
        answered = t079.reply(owner.session, request["id"], {
            "interaction_id": interaction["id"],
            "artifact_ids": [artifacts[-1]],
        })
        assert answered.status == 202, answered.text
        run = t079.route_once(stack)
        assert run.completed.returncode == 0, run.stdout

    calls = t079.control_calls(stack.dbname, request["id"])
    assert len(calls) == t079.ROUTE_CONTROL_BUDGET, calls
    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "route_budget_exhausted", row
    assert row["run_id"] is None, row
    assert f08.run_rows(stack.dbname, conversation) == []
    assert row["control_calls"] == t079.ROUTE_CONTROL_BUDGET, row
    # A retried handler sees the failed request and writes no fourth call.
    rerun = t079.route_once(stack)
    assert rerun.completed.returncode == 0, rerun.stdout
    assert len(t079.control_calls(stack.dbname, request["id"])) == t079.ROUTE_CONTROL_BUDGET
    assert f08.run_rows(stack.dbname, conversation) == []


def test_two_replies_never_create_two_runs_and_the_second_is_closed(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner)
    other = t079.publish(stack, owner.session, purpose="quarterly sales report beta",
                         required_inputs=[], name="t079 duplicate candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "please build a quarterly sales report")
    request = f08.request_of(submitted)
    t079.route_once(stack)
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])
    body = {"interaction_id": interaction["id"], "candidate_version_id": other.version_id}
    key = "t079-duplicate-reply-key"

    first = t079.reply(owner.session, request["id"], body, key=key)
    assert first.status == 202, first.text
    replayed = t079.reply(owner.session, request["id"], body, key=key)
    assert replayed.status == 202, replayed.text
    assert replayed.json["id"] == first.json["id"], (first.json, replayed.json)

    different_key = t079.reply(owner.session, request["id"], body)
    st.assert_error(different_key, 409, "interaction_closed")

    t079.route_once(stack)
    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1, runs
    assert runs[0]["application_version_id"] == other.version_id, runs
    assert t079.request_state(stack.dbname, request["id"])["state"] == "dispatched"


def test_an_expired_reply_is_409_and_releases_the_conversation_slot(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner)
    second = t079.publish(stack, owner.session, purpose="quarterly sales report beta",
                          required_inputs=[], name="t079 queued candidate")
    conversation = f08.conversation(owner.session)
    first_request = f08.request_of(
        t079.submit(owner.session, conversation, "please build a quarterly sales report"))
    queued_request = f08.request_of(
        t079.submit(owner.session, conversation, "please build another sales report"))
    assert queued_request["state"] == "queued", queued_request
    t079.route_once(stack)
    interaction = t079.pending_interaction_row(stack.dbname, first_request["id"])
    assert interaction and interaction["expires_at"]

    t079.expire_interaction(stack, first_request["id"], seconds=5)
    key = "t079-expired-reply-key"
    body = {"interaction_id": interaction["id"], "candidate_version_id": second.version_id}
    expired = t079.reply(owner.session, first_request["id"], body, key=key)
    st.assert_error(expired, 409, "interaction_closed")

    first_row = t079.request_state(stack.dbname, first_request["id"])
    assert first_row["state"] == "failed", first_row
    assert first_row["error_code"] == "interaction_expired", first_row
    closed = t079.interaction_rows(stack.dbname, first_request["id"])[0]
    assert closed["state"] == "expired", closed
    assert closed["answered_at"] is None, closed
    assert f08.run_rows(stack.dbname, conversation) == []

    # Expiry released the slot: the next queued request was promoted to routing
    # (and only that one; the failed request can never be revived).
    queued_row = t079.request_state(stack.dbname, queued_request["id"])
    assert queued_row["state"] == "routing", queued_row
    receipt_rows = st.sql_rows(
        stack.dbname,
        "SELECT key FROM platform.idempotency_keys WHERE path LIKE "
        f"'%/requests/{first_request['id']}/reply'",
    )
    assert receipt_rows == [], receipt_rows
    # The same key answers the same 409; no receipt and no resurrection.
    again = t079.reply(owner.session, first_request["id"], body, key=key)
    st.assert_error(again, 409, "interaction_closed")
    assert t079.request_state(stack.dbname, first_request["id"])["state"] == "failed"


def test_candidates_and_proposals_carry_ordinal_ranks_not_probabilities(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner)
    t079.publish(stack, owner.session, purpose="quarterly sales report beta",
                 required_inputs=[], name="t079 probabilities candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "please build a quarterly sales report")
    request = f08.request_of(submitted)
    t079.route_once(stack)

    proposal = t079.proposal_bytes(stack, request["id"])
    document = json.dumps(proposal).lower()
    for forbidden in ("score", "probability", "confidence", "calibrated"):
        assert forbidden not in document, (forbidden, document)
    assert [candidate["rank"] for candidate in proposal["candidates"]] == [1, 2]
    decision = t079.decision_rows(stack.dbname, request["id"])[0]
    assert "score" not in (decision["raw_decision"] or "").lower()
    read = f08.get_request(owner.session, request["id"])
    assert read.status == 200, read.text
    body = json.dumps(read.json).lower()
    for forbidden in ("score", "probability", "confidence", "calibrated"):
        assert forbidden not in body, (forbidden, body)



def test_budget_exhaustion_degrades_to_selection_without_a_fourth_call(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    _prepare(stack, owner)
    t079.publish(stack, owner.session, purpose="quarterly sales report beta",
                 required_inputs=[], name="t079 degrade candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "please build a quarterly sales report")
    request = f08.request_of(submitted)
    t079.route_once(stack)

    # Three message-only replies each resume routing and spend one control call;
    # every round still offers both candidates.
    for _ in range(t079.ROUTE_CONTROL_BUDGET):
        interaction = t079.pending_interaction_row(stack.dbname, request["id"])
        assert interaction and interaction["kind"] == "selection", interaction
        answered = t079.reply(owner.session, request["id"], {
            "interaction_id": interaction["id"],
            "message": "请先帮我选一个",
        })
        assert answered.status == 202, answered.text
        run = t079.route_once(stack)
        assert run.completed.returncode == 0, run.stdout

    calls = t079.control_calls(stack.dbname, request["id"])
    assert len(calls) == t079.ROUTE_CONTROL_BUDGET, calls
    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "awaiting_selection", row
    assert row["error_code"] is None, row
    assert f08.run_rows(stack.dbname, conversation) == []
    # The degraded interaction is a fresh surface with both candidates visible.
    degraded = t079.pending_interaction_row(stack.dbname, request["id"])
    assert degraded and degraded["kind"] == "selection", degraded
    proposal = t079.proposal_bytes(stack, request["id"])
    assert len(proposal["candidates"]) == 2, proposal

    chosen = proposal["candidates"][-1]["application_version_id"]
    answered = t079.reply(owner.session, request["id"], {
        "interaction_id": degraded["id"],
        "candidate_version_id": chosen,
    })
    assert answered.status == 202, answered.text
    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout
    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1 and runs[0]["application_version_id"] == chosen, runs
    # The explicit choice resumes without a fourth model call.
    assert len(t079.control_calls(stack.dbname, request["id"])) == t079.ROUTE_CONTROL_BUDGET


def test_no_authorized_candidate_fails_without_a_model_call(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    t079.publish_offtopic(stack, owner.session, name="t079 no-match candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "compose a wedding speech")
    request = f08.request_of(submitted)
    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "no_matching_application", row
    assert f08.run_rows(stack.dbname, conversation) == []
    assert t079.control_calls(stack.dbname, request["id"]) == []
