"""F08/T079: the route_request job's recovery boundaries.

Two failure paths are proven here without a timer: a job whose routing round cannot
read the sealed input fails terminally through the background completion guard, and
the guard fails the still-routing request and releases the conversation slot; a
business refusal committed by the server leaves the request non-routing, so the
guard writes nothing. The handler itself is the compiled maintenance process behind
the real mTLS protocol.
"""

from __future__ import annotations

from support import f08, t079


def test_a_terminal_route_failure_fails_the_request_and_frees_the_slot(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    t079.publish(stack, owner.session, purpose="quarterly sales report", required_inputs=[])
    conversation = f08.conversation(owner.session)
    first = f08.request_of(
        t079.submit(owner.session, conversation, "please build a quarterly sales report"))
    second = f08.request_of(
        t079.submit(owner.session, conversation, "please build another sales report"))
    assert first["state"] == "routing", first
    assert second["state"] == "queued", second
    jobs_before = t079.route_jobs(stack.dbname, first["id"])
    assert len(jobs_before) == 1, jobs_before

    # Break the sealed input bytes of the first request only: the router's message
    # read fails and the handler reports a deterministic refusal. The completion
    # guard must not leave the request (the slot holder) in routing.
    row = f08.request_row(stack.dbname, first["id"])
    artifact = f08.artifact_row(stack.dbname, row["input_artifact_id"])
    (stack.object_dir / artifact["object_key"]).unlink()

    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout

    failed = t079.request_state(stack.dbname, first["id"])
    assert failed["state"] == "failed", failed
    assert failed["error_code"] == "route_failed", failed
    assert failed["run_id"] is None, failed
    assert f08.run_rows(stack.dbname, conversation) == [] or all(
        item["request_id"] == second["id"] for item in f08.run_rows(stack.dbname, conversation)
    )
    job = t079.route_jobs(stack.dbname, first["id"])[0]
    assert job["state"] == "failed", job
    assert "request.routing_failed" in f08.audit_actions(stack.dbname, first["id"])

    # The slot was released: the queued request is no longer queued. The same
    # --once drain may already have routed it (its own input is intact).
    promoted = t079.request_state(stack.dbname, second["id"])
    assert promoted["state"] in ("routing", "dispatched"), promoted


def test_a_refusal_committed_by_the_router_is_not_rewritten_by_the_guard(f08_stack) -> None:
    stack = f08_stack
    owner = f08.account(stack)
    t079.seed_route_control_model(stack, owner.principal_id)
    t079.publish_offtopic(stack, owner.session, name="t079 refusal candidate")
    conversation = f08.conversation(owner.session)
    submitted = t079.submit(owner.session, conversation, "compose a wedding speech")
    request = f08.request_of(submitted)

    run = t079.route_once(stack)
    assert run.completed.returncode == 0, run.stdout
    row = t079.request_state(stack.dbname, request["id"])
    assert row["state"] == "failed", row
    assert row["error_code"] == "no_matching_application", row
    job = t079.route_jobs(stack.dbname, request["id"])[0]
    # The routing round committed the typed refusal and the handler succeeded; the
    # guard never replaces it with route_failed.
    assert job["state"] == "succeeded", job
