"""F08/T079 (#80): candidate suggestion, missing input, ambiguity and the one Run.

Every test drives the real surfaces: the compiled API over session+CSRF HTTP, the
real published versions produced by the compiler/publication row shape, the real
`route_request` maintenance handler behind mTLS, the governed
`platform_model_calls` ledger and the authoritative PostgreSQL rows. The routing
control model is a real registered candidate (dev stub, `stub: true`); the server,
not the stub, validates the candidate set, the required inputs and the ACL.
"""

from __future__ import annotations

from support import f08, t079


def test_a_single_authorized_candidate_dispatches_exactly_one_run(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    published = t079.publish(stack, owner.session, purpose="quarterly sales report",
                             required_inputs=[])
    conversation = f08.conversation(owner.session)

    submitted = t079.submit(owner.session, conversation,
                            "please build a quarterly sales report")
    request = f08.request_of(submitted)
    assert request["state"] == "routing", request
    assert request["run_id"] is None, request
    assert t079.candidate_rows(stack.dbname, request["id"]) == []

    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "dispatched", row
    assert row["error_code"] is None, row
    assert row["run_id"], row

    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1, runs
    assert runs[0]["request_id"] == request["id"], runs[0]
    assert runs[0]["application_version_id"] == published.version_id, runs[0]
    assert runs[0]["parent_run_id"] is None, runs[0]

    # The server persisted exactly the authorized candidate set before the model
    # call, and the model's `select` is only accepted because it names that member.
    candidates = t079.candidate_rows(stack.dbname, request["id"])
    assert [candidate["application_version_id"] for candidate in candidates] == [
        published.version_id
    ], candidates
    assert [candidate["rank"] for candidate in candidates] == [1], candidates
    decisions = t079.decision_rows(stack.dbname, request["id"])
    assert len(decisions) == 1, decisions
    assert decisions[0]["decision"] == "select", decisions[0]
    assert decisions[0]["selected_version_id"] == published.version_id, decisions[0]
    assert decisions[0]["model_call_id"], decisions[0]

    # The control call is a governed ledger row owned by the route job, not by a
    # Run: the XOR owner is the background job and request_id stays NULL.
    calls = t079.control_calls(stack.dbname, request["id"])
    assert len(calls) == 1, calls
    assert calls[0]["state"] == "succeeded", calls[0]
    assert calls[0]["request_id"] is None, calls[0]
    assert calls[0]["result_artifact_id"], calls[0]

    # The contract read shows the handling version through headers (Request is
    # additionalProperties:false) and no interaction.
    read = f08.get_request(owner.session, request["id"])
    assert read.status == 200, read.text
    assert read.json["state"] == "dispatched", read.json
    assert read.json["interaction"] is None, read.json
    assert read.header("X-Request-Application-Version-Id") == published.version_id
    assert read.header("X-Request-Application-Version-No") == "1"


def test_missing_input_creates_no_run_until_the_reply_supplies_it(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    published = t079.publish(
        stack, owner.session, purpose="quarterly sales report",
        required_inputs=["sales csv", "region mapping"],
    )
    conversation = f08.conversation(owner.session)

    submitted = t079.submit(owner.session, conversation,
                            "please build a quarterly sales report")
    request = f08.request_of(submitted)
    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "awaiting_input", row
    assert row["error_code"] is None, row
    assert row["run_id"] is None, row
    assert f08.run_rows(stack.dbname, conversation) == []
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])
    assert interaction and interaction["kind"] == "input", interaction
    proposal = t079.proposal_bytes(stack, request["id"])
    assert proposal["kind"] == "input", proposal
    assert proposal["missing_fields"] == ["sales csv", "region mapping"], proposal
    assert proposal["candidate"]["application_version_id"] == published.version_id, proposal
    assert "score" not in proposal and "probability" not in proposal, proposal

    # The contract read exposes the same question/candidates through the open
    # Request.interaction object.
    read = f08.get_request(owner.session, request["id"])
    assert read.status == 200, read.text
    view = read.json["interaction"]
    assert view and view["kind"] == "input", view
    assert view["correlation_id"], view
    assert view["proposal"]["missing_fields"] == ["sales csv", "region mapping"], view

    first = f08.public_attachment(stack, owner.session, b"date,amount\n2026-01-01,10\n")
    second = f08.public_attachment(stack, owner.session, b"region,weight\nEU,1\n")
    answered = t079.reply(owner.session, request["id"], {
        "interaction_id": interaction["id"],
        "artifact_ids": [first, second],
    })
    assert answered.status == 202, answered.text
    assert answered.json["state"] == "routing", answered.json

    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout
    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "dispatched", row
    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1, runs
    assert runs[0]["application_version_id"] == published.version_id, runs[0]
    linked = {item["artifact_id"] for item in f08.run_artifact_rows(stack.dbname, runs[0]["id"])}
    assert first in linked and second in linked, linked
    assert len(t079.control_calls(stack.dbname, request["id"])) == 2


def test_multiple_candidates_are_all_visible_and_only_an_explicit_choice_dispatches(
    f08_stack,
) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    alpha = t079.publish(stack, owner.session, purpose="sales report alpha",
                         required_inputs=[], name="t079 alpha")
    beta = t079.publish(stack, owner.session, purpose="sales report beta",
                        required_inputs=[], name="t079 beta")
    conversation = f08.conversation(owner.session)

    submitted = t079.submit(owner.session, conversation, "please prepare a sales report")
    request = f08.request_of(submitted)
    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "awaiting_selection", row
    assert f08.run_rows(stack.dbname, conversation) == []
    candidates = t079.candidate_rows(stack.dbname, request["id"])
    assert {candidate["application_version_id"] for candidate in candidates} == {
        alpha.version_id, beta.version_id,
    }, candidates
    assert [candidate["rank"] for candidate in candidates] == [1, 2], candidates

    proposal = t079.proposal_bytes(stack, request["id"])
    assert proposal["kind"] == "selection", proposal
    assert len(proposal["candidates"]) == 2, proposal
    assert {item["application_version_id"] for item in proposal["candidates"]} == {
        alpha.version_id, beta.version_id,
    }, proposal
    for item in proposal["candidates"]:
        assert item["purpose"], item
        assert item["required_inputs"] is not None, item
        assert "score" not in item and "probability" not in item, item

    # The dev stub suggested the first candidate; the server still offered both and
    # dispatched nothing. The user's explicit choice is the other candidate, which
    # differs from the suggestion on purpose.
    chosen = proposal["candidates"][-1]["application_version_id"]
    assert chosen != proposal["candidates"][0]["application_version_id"]
    interaction = t079.pending_interaction_row(stack.dbname, request["id"])
    answered = t079.reply(owner.session, request["id"], {
        "interaction_id": interaction["id"],
        "candidate_version_id": chosen,
    })
    assert answered.status == 202, answered.text
    assert answered.json["state"] == "routing", answered.json

    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout
    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "dispatched", row
    runs = f08.run_rows(stack.dbname, conversation)
    assert len(runs) == 1, runs
    assert runs[0]["application_version_id"] == chosen, runs[0]
    # The explicit selection resumes without a second model call.
    assert len(t079.control_calls(stack.dbname, request["id"])) == 1
