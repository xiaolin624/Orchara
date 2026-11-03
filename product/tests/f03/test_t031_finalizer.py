"""T031 acceptance: the unified finalizer commits the terminal Run exactly once.

Result and exit can arrive in either order. The proposal call alone never
terminalises the Run; the exit call alone parks it in ``recovery_pending``; both
together produce exactly one terminal transition (one revision bump at the
terminal step, one completed job, one released allocation, one output link, no
duplicate rows). The waiting/needs_reconciliation proposals stay non-terminal;
T035/T036 own them from there.
"""

from __future__ import annotations

import json

from support import t025, t029, t031
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def test_result_then_exit_commits_the_terminal_run_exactly_once(t029_stack) -> None:
    owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-result-first"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")
    proposal = t031.result_body(
        lease,
        suggested_state="succeeded",
        output_artifact_id=artifact,
        checkpoint_id="ckpt-result-first",
    )
    assert worker.submit_result(lease["run_id"], body=proposal).status == 200

    proposed = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert proposed["state"] == "running" and proposed["phase"] == "finalizing", proposed

    response = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert response.status == 200, response.text

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "succeeded", run
    assert run["phase"] == "idle", run
    # The proposal bumped nothing; the terminal transition is exactly one bump.
    assert run["revision"] == proposed["revision"] + 1 == 3, (proposed, run)
    assert run["finished_at"] is not None, run
    assert run["output_artifact_id"] == artifact, run
    assert run["error_code"] is None, run

    job = t029.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed", job
    assert job["current_attempt_token"] is None, job

    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["exited_at"] is not None and attempt["revoked_at"] is not None, attempt
    assert json.loads(attempt["reported_result"])["suggested_state"] == "succeeded"

    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    allocations = t031.allocations_for_holder(t029_stack.dbname, "job:" + lease["job_id"])
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations
    # No duplicate rows: one run, one job, one attempt, one allocation, and the
    # canonical input + this output are the only run_artifacts rows.
    assert t031.count(t029_stack.dbname, "runs", f"id = '{lease['run_id']}'::uuid") == 1
    assert t031.count(t029_stack.dbname, "jobs", f"run_id = '{lease['run_id']}'::uuid") == 1
    assert t031.count(
        t029_stack.dbname, "run_attempts", f"run_id = '{lease['run_id']}'::uuid"
    ) == 1
    assert t031.run_artifact_count(t029_stack.dbname, lease["run_id"]) == 2

    # The owner sees the committed run and the output artifact id.
    view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert view.status == 200, view.text
    assert view.json["state"] == "succeeded", view.json
    assert view.json["output_artifact_id"] == artifact, view.json
    assert view.json["revision"] == 3, view.json

    # A repeated exit (same evidence) is idempotent; a late result is 409.
    repeated = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert repeated.status == 200, repeated.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"

    late = worker.submit_result(lease["run_id"], body=proposal)
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_terminal", late.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run

    # Different exit evidence on an exited attempt is a conflict, no change.
    conflicting = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-result-first",
        evidence=t029.exit_evidence(lease, started=True, exit_code=1),
    )
    assert conflicting.status == 409, conflicting.text
    assert conflicting.json["error"]["code"] == "attempt_evidence_conflict", conflicting.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run


def test_exit_then_result_commits_the_same_terminal_run_once(t029_stack) -> None:
    owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-exit-first"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")
    evidence = t029.exit_evidence(lease, started=True, exit_code=0)

    exit_response = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit-first", evidence=evidence
    )
    assert exit_response.status == 200, exit_response.text
    parked = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert parked["state"] == "recovery_pending", parked
    assert parked["phase"] == "idle" and parked["finished_at"] is None, parked
    assert parked["revision"] == 3, "claim 1->2 and the park 2->3"
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "held"
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    allocations = t031.allocations_for_holder(t029_stack.dbname, "job:" + lease["job_id"])
    assert len(allocations) == 1 and allocations[0]["released_at"] is not None, allocations

    proposal = t031.result_body(
        lease,
        suggested_state="succeeded",
        output_artifact_id=artifact,
        checkpoint_id="ckpt-exit-first",
    )
    response = worker.submit_result(lease["run_id"], body=proposal)
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.json

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "succeeded", run
    assert run["phase"] == "idle" and run["finished_at"] is not None, run
    assert run["revision"] == parked["revision"] + 1 == 4, (parked, run)
    assert run["output_artifact_id"] == artifact, run
    job = t029.job_row(t029_stack.dbname, lease["job_id"])
    assert job["state"] == "completed" and job["current_attempt_token"] is None, job
    # Releasing twice would have decremented the counter below zero/aborted; the
    # single allocation row stayed released.
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    assert len(t031.allocations_for_holder(t029_stack.dbname, "job:" + lease["job_id"])) == 1

    repeated_exit = supervisor.confirm_exit(
        lease["attempt_token"], execution_unit_id="unit-exit-first", evidence=evidence
    )
    assert repeated_exit.status == 200, repeated_exit.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run

    late = worker.submit_result(lease["run_id"], body=proposal)
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_terminal", late.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run


def test_repeated_result_replays_and_different_content_conflicts(t029_stack) -> None:
    _owner, lease, artifact = t031.claimed_with_output(t029_stack, execution_unit_id="unit-replay")
    worker = t031.execution_client(t029_stack, "worker")
    proposal = t031.result_body(
        lease, suggested_state="succeeded", output_artifact_id=artifact
    )

    first = worker.submit_result(lease["run_id"], body=proposal)
    assert first.status == 200, first.text
    run_before = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    attempt_before = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])

    replay = worker.submit_result(lease["run_id"], body=proposal)
    assert replay.status == 200, replay.text
    assert replay.json == first.json, (replay.json, first.json)
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run_before
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"]) == attempt_before

    # A new event_id (or any changed content) is a different proposal: 409.
    different = dict(proposal)
    different["event_id"] = t029.new_uuid()
    conflict = worker.submit_result(lease["run_id"], body=different)
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "event_conflict", conflict.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run_before
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"]) == attempt_before

    # The first proposal is the one the finalizer commits.
    terminal = t031.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-replay",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert terminal.status == 200, terminal.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "succeeded"


def test_failed_result_sets_the_error_code_and_no_output_artifact(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    lease = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-failed")
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")

    proposal = t031.result_body(
        lease, suggested_state="failed", error_code="tool_output_invalid"
    )
    assert worker.submit_result(lease["run_id"], body=proposal).status == 200
    response = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-failed",
        evidence=t029.exit_evidence(lease, started=True, exit_code=1),
    )
    assert response.status == 200, response.text

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "failed", run
    assert run["error_code"] == "tool_output_invalid", run
    assert run["output_artifact_id"] is None, "a failed run has no output artifact"
    assert run["phase"] == "idle" and run["finished_at"] is not None, run
    assert run["revision"] == 3, run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0

    view = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert view.status == 200 and view.json["state"] == "failed", view.json
    assert view.json["error_code"] == "tool_output_invalid"
    assert view.json["output_artifact_id"] is None


def test_needs_reconciliation_is_committed_but_stays_non_terminal(t029_stack) -> None:
    _owner, lease, _artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-reconcile"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")

    proposal = t031.result_body(
        lease, suggested_state="needs_reconciliation", checkpoint_id="ckpt-reconcile"
    )
    assert worker.submit_result(lease["run_id"], body=proposal).status == 200
    response = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-reconcile",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert response.status == 200, response.text

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "needs_reconciliation", run
    assert run["phase"] == "idle", run
    assert run["finished_at"] is None, "needs_reconciliation is not terminal"
    assert run["revision"] == 3, run
    assert run["output_artifact_id"] is None and run["error_code"] is None, run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0

    # A late proposal does not revive an execution; the interim code is
    # run_state_conflict (T036 owns reconciliation).
    late = worker.submit_result(
        lease["run_id"],
        body=t031.result_body(lease, suggested_state="failed", error_code="late"),
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "run_state_conflict", late.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run


def test_waiting_proposal_hands_the_run_to_t035_waiting(t029_stack) -> None:
    """T031's waiting proposal is stored; T035 owns the waiting state itself.

    T031 deliberately left the Run running/finalizing as an interim shape. T035
    supersedes that (the ticket's waiting transition): the accepted waiting
    proposal commits waiting_approval/phase waiting, a durable run_waitings row and
    a held job, and the supervisor's exit only records the exit fact. The resume
    belongs to T035 and is exercised in tests/f03/test_t035_*.py.
    """
    _owner, lease, _artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-waiting"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")

    proposal = t031.result_body(
        lease, suggested_state="waiting_approval", checkpoint_id="ckpt-waiting-1"
    )
    assert worker.submit_result(lease["run_id"], body=proposal).status == 200
    waiting_run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert waiting_run["state"] == "waiting_approval", waiting_run
    assert waiting_run["phase"] == "waiting", waiting_run
    assert waiting_run["finished_at"] is None, waiting_run
    assert waiting_run["revision"] == 3, "claim 2 -> waiting 3"

    response = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-waiting",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert response.status == 200, response.text
    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    # Exit alone is not a decision: the Run stays waiting and non-terminal.
    assert run["state"] == "waiting_approval", run
    assert run["phase"] == "waiting", run
    assert run["finished_at"] is None, run
    assert run["revision"] == 3, run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "held"
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["checkpoint_id"] == (
        "ckpt-waiting-1"
    )
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0

    replay = worker.submit_result(lease["run_id"], body=proposal)
    assert replay.status == 200, replay.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run


def test_a_succeeded_proposal_needs_an_owned_ready_linked_artifact(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    lease = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-artifact")
    worker = t031.execution_client(t029_stack, "worker")

    good = t031.seed_output_artifact(t029_stack, owner.creator.principal_id)
    unlinked = t031.seed_output_artifact(t029_stack, owner.creator.principal_id)
    uploading = t031.seed_output_artifact(t029_stack, owner.creator.principal_id, state="uploading")
    deleted = t031.seed_output_artifact(t029_stack, owner.creator.principal_id, deleted=True)
    foreign_owner = t025.seed_principal(t029_stack.dbname, "t031 foreign")
    foreign = t031.seed_output_artifact(t029_stack, foreign_owner)
    for artifact in (uploading, deleted, foreign):
        t031.link_output(t029_stack.dbname, lease["run_id"], artifact)

    cases = [
        (t029.new_uuid(), "artifact_not_found"),
        (unlinked, "artifact_not_linked"),
        (foreign, "artifact_not_owned"),
        (uploading, "artifact_not_ready"),
        (deleted, "artifact_not_ready"),
    ]
    for artifact_id, reason in cases:
        response = worker.submit_result(
            lease["run_id"],
            body=t031.result_body(
                lease, suggested_state="succeeded", output_artifact_id=artifact_id
            ),
        )
        assert response.status == 422, (reason, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (reason, error)
        assert error["details"] == {"field": "output_artifact_id", "code": reason}, (
            reason,
            error,
        )
        assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["phase"] == "executing"
        assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["reported_result"] is None
        assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"
        assert t029.quota_counter(t029_stack.dbname)["used"] == 1

    # Registering the good artifact makes the same call succeed.
    t031.link_output(t029_stack.dbname, lease["run_id"], good)
    accepted = worker.submit_result(
        lease["run_id"],
        body=t031.result_body(lease, suggested_state="succeeded", output_artifact_id=good),
    )
    assert accepted.status == 200, accepted.text
    assert accepted.json["state"] == "running" and accepted.json["phase"] == "finalizing"


def test_roles_worker_proposes_and_only_the_supervisor_confirms_exit(t029_stack) -> None:
    _owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-roles"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")
    maintenance = t031.execution_client(t029_stack, "maintenance")
    proposal = t031.result_body(
        lease, suggested_state="succeeded", output_artifact_id=artifact
    )

    denied = maintenance.submit_result(lease["run_id"], body=proposal)
    assert denied.status == 403, denied.text
    assert denied.json["error"]["code"] == "forbidden", denied.text
    assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["reported_result"] is None

    assert worker.submit_result(lease["run_id"], body=proposal).status == 200
    for client, label in ((worker, "worker"), (maintenance, "maintenance")):
        refused = client.confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-roles",
            evidence=t029.exit_evidence(lease, started=True),
        )
        assert refused.status == 403, (label, refused.text)
        assert refused.json["error"]["code"] == "forbidden", (label, refused.text)
    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "running" and run["phase"] == "finalizing", run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"

    # The supervisor may also re-send the identical proposal (idempotent) and is
    # the only role that can turn it into a terminal Run.
    assert supervisor.submit_result(lease["run_id"], body=proposal).status == 200
    confirmed = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-roles",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert confirmed.status == 200, confirmed.text
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "succeeded"


def test_two_runs_never_share_a_token_or_checkpoint(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    lease_a = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-a")
    lease_b = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-b")
    assert lease_a["run_id"] != lease_b["run_id"]
    assert lease_a["attempt_token"] != lease_b["attempt_token"]
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")

    # Run A rejects run B's token before storing anything.
    cross = worker.submit_result(
        lease_a["run_id"],
        attempt_token=lease_b["attempt_token"],
        suggested_state="needs_reconciliation",
    )
    assert cross.status == 409, cross.text
    assert cross.json["error"]["code"] == "stale_attempt", cross.text
    assert t029.attempt_row(t029_stack.dbname, lease_a["attempt_token"])["reported_result"] is None
    assert t029.attempt_row(t029_stack.dbname, lease_b["attempt_token"])["reported_result"] is None

    for lease, checkpoint in ((lease_a, "ckpt-run-a"), (lease_b, "ckpt-run-b")):
        accepted = worker.submit_result(
            lease["run_id"],
            body=t031.result_body(
                lease, suggested_state="needs_reconciliation", checkpoint_id=checkpoint
            ),
        )
        assert accepted.status == 200, accepted.text

    attempt_a = t029.attempt_row(t029_stack.dbname, lease_a["attempt_token"])
    attempt_b = t029.attempt_row(t029_stack.dbname, lease_b["attempt_token"])
    assert attempt_a["checkpoint_id"] == "ckpt-run-a", attempt_a
    assert attempt_b["checkpoint_id"] == "ckpt-run-b", attempt_b
    assert attempt_a["run_id"] != attempt_b["run_id"]

    # Finalizing B does not touch A.
    before_a = t029.run_state_row(t029_stack.dbname, lease_a["run_id"])
    assert supervisor.confirm_exit(
        lease_b["attempt_token"],
        execution_unit_id="unit-b",
        evidence=t029.exit_evidence(lease_b, started=True),
    ).status == 200
    assert t029.run_state_row(t029_stack.dbname, lease_b["run_id"])["state"] == (
        "needs_reconciliation"
    )
    assert t029.run_state_row(t029_stack.dbname, lease_a["run_id"]) == before_a
    assert t029.job_row(t029_stack.dbname, lease_a["job_id"])["state"] == "leased"


def test_a_live_child_blocks_the_parent_terminal_and_holds_the_job(t029_stack) -> None:
    owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-child"
    )
    worker = t031.execution_client(t029_stack, "worker")
    supervisor = t031.execution_client(t029_stack, "supervisor")
    assert worker.submit_result(
        lease["run_id"],
        body=t031.result_body(
            lease, suggested_state="succeeded", output_artifact_id=artifact
        ),
    ).status == 200

    child = t031.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="running",
    )
    response = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-child",
        evidence=t029.exit_evidence(lease, started=True),
    )
    assert response.status == 200, response.text

    run = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "running", "a live child blocks the parent terminal"
    assert run["phase"] == "finalizing", run
    assert run["finished_at"] is None, run
    assert run["revision"] == 2, run
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "held"
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0

    # The child is a real run row; finishing it does not retroactively revive a
    # finalizer call (T035/T036 own the re-drive), so the parent stays parked.
    t031.set_run_state(t029_stack.dbname, child, "succeeded")
    assert t029.run_state_row(t029_stack.dbname, lease["run_id"]) == run
