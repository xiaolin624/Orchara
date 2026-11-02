"""T031 acceptance: a worker result is a proposal, never a success by itself.

The worker POSTs the contract's ``WorkerResult`` over the real internal mTLS
listener. Go stores it on ``run_attempts`` (``reported_result``/``reported_at``/
``checkpoint_id``), moves the Run to ``phase=finalizing`` *only*, and answers the
contract's Run. Nothing about that 200 means the Run succeeded: the supervisor's
exit evidence and the unified finalizer are still missing (F03 T031).
"""

from __future__ import annotations

import json

from support import t029, t031
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def claimed_with_output(t029_stack, *, execution_unit_id: str = "unit-result"):
    """One claimed run plus a ready, owned and linked output artifact."""
    return t031.claimed_with_output(t029_stack, execution_unit_id=execution_unit_id)


def test_a_result_proposal_is_stored_but_never_makes_the_run_terminal(t029_stack) -> None:
    owner, lease, artifact = claimed_with_output(t029_stack)
    event_id = t029.new_uuid()

    response = t031.execution_client(t029_stack, "worker").submit_result(
        lease["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        event_id=event_id,
        output_artifact_id=artifact,
        checkpoint_id="ckpt-t031-1",
    )
    assert response.status == 200, response.text
    run = response.json
    t031.assert_run_shape(run)
    # The contract Run is honest: the proposal does not transition the aggregate.
    assert run["state"] == "running", run
    assert run["phase"] == "finalizing", run
    assert run["output_artifact_id"] is None, run
    assert run["error_code"] is None, run
    assert run["revision"] == 2, "the claim bumped 1->2; a proposal adds no state transition"

    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["reported_at"] is not None, attempt
    assert attempt["checkpoint_id"] == "ckpt-t031-1", attempt
    assert json.loads(attempt["reported_result"]) == {
        "attempt_token": lease["attempt_token"],
        "event_id": event_id,
        "suggested_state": "succeeded",
        "output_artifact_id": artifact,
        "checkpoint_id": "ckpt-t031-1",
    }, attempt
    # Not an exit: the attempt is still the live credential of this run.
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None, attempt

    stored = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert stored["state"] == "running", stored
    assert stored["phase"] == "finalizing", stored
    assert stored["finished_at"] is None, stored
    assert stored["revision"] == 2, stored

    job = t029.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "leased", job
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1
    assert t029.allocation_for_job(t029_stack.dbname, lease["job_id"])["released_at"] is None

    # The owner's view agrees, and the exact contract fields are returned.
    view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert view.status == 200, view.text
    t031.assert_run_shape(view.json)
    assert view.json["state"] == "running" and view.json["phase"] == "finalizing", view.json


def test_the_result_body_is_the_exact_contract_shape(t029_stack) -> None:
    _owner, lease, artifact = claimed_with_output(t029_stack, execution_unit_id="unit-shape")
    worker = t031.execution_client(t029_stack, "worker")

    def located(body: dict, field: str) -> None:
        response = worker.submit_result(lease["run_id"], body=body)
        assert response.status == 422, (field, response.text)
        assert response.json["error"]["code"] == "validation_failed", (field, response.text)
        assert response.json["error"]["details"]["field"] == field, (field, response.text)

    base = {"attempt_token": lease["attempt_token"], "event_id": t029.new_uuid()}
    located({**base}, "suggested_state")
    located({**base, "suggested_state": "cheerful"}, "suggested_state")
    located({**base, "suggested_state": "succeeded"}, "output_artifact_id")
    located({**base, "suggested_state": "failed"}, "error_code")
    located({**base, "suggested_state": "waiting_approval"}, "checkpoint_id")
    located(
        {**base, "attempt_token": "not-a-uuid", "suggested_state": "needs_reconciliation"},
        "attempt_token",
    )
    located({**base, "event_id": "not-a-uuid", "suggested_state": "needs_reconciliation"}, "event_id")
    located(
        {**base, "suggested_state": "needs_reconciliation", "checkpoint_id": "  "},
        "checkpoint_id",
    )

    # The contract schema is additionalProperties:false and validates the enum
    # before any row is touched.
    unknown = worker.submit_result(
        lease["run_id"], body={**base, "suggested_state": "succeeded", "extra": True}
    )
    assert unknown.status == 400, unknown.text
    assert unknown.json["error"]["code"] == "invalid_json", unknown.text
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["reported_result"] is None
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"

    # A malformed/unknown run path is the contract's 404; a wrong token for an
    # existing run is stale_attempt, so the endpoint cannot enumerate runs.
    malformed = worker.submit_result("not-a-uuid", body={
        **base, "suggested_state": "needs_reconciliation",
    })
    assert malformed.status == 404, malformed.text
    assert malformed.json["error"]["code"] == "run_not_found", malformed.text
    unknown_run = worker.submit_result(t029.new_uuid(), body={
        **base, "suggested_state": "needs_reconciliation",
    })
    assert unknown_run.status == 404, unknown_run.text
    assert unknown_run.json["error"]["code"] == "run_not_found", unknown_run.text
    wrong_token = worker.submit_result(lease["run_id"], body={
        **base,
        "attempt_token": t029.new_uuid(),
        "suggested_state": "needs_reconciliation",
    })
    assert wrong_token.status == 409, wrong_token.text
    assert wrong_token.json["error"]["code"] == "stale_attempt", wrong_token.text
