"""T035 acceptance: exactly one resume, whichever fact arrives first.

The resume is one idempotent transaction: a stored decision AND the supervisor's
exit fact AND the fixed checkpoint, re-checked against permission/budget/deadline,
then one sealed canonical payload artifact, one ``jobs`` row of kind ``resume``,
the old job completed, the old token revoked and the execution slot released
exactly once. Decision-first, exit-first, duplicate calls and a conflicting
decision are all exercised against real PostgreSQL/HTTP/mTLS/object bytes.
"""

from __future__ import annotations

import hashlib
import json

from support import t025, t029, t031, t032, t035
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _claim_two(stack, owner) -> tuple[dict, dict]:
    """Two claimed runs before any waiting transition (claim order stays stable)."""
    first = t035.claim_lease(stack, owner, execution_unit_id="unit-first")
    second = t035.claim_lease(stack, owner, execution_unit_id="unit-second")
    return first, second


def test_decision_first_and_exit_first_each_create_exactly_one_resume(t029_stack) -> None:
    owner = t035.golden_owner(t029_stack)
    decision_first, exit_first = _claim_two(t029_stack, owner)
    worker = t035.execution_client(t029_stack, "worker")
    supervisor = t035.execution_client(t029_stack, "supervisor")

    for lease in (decision_first, exit_first):
        response = t035.submit_waiting(
            worker, lease, kind="approval", checkpoint_id="ckpt-" + lease["run_id"][:8]
        )
        assert response.status == 200, response.text

    # Decision first: the decision is stored, but no resume exists until the exit.
    approved = supervisor.resolve_waiting(
        decision_first["run_id"],
        kind="approval",
        outcome="approved",
        reference="decision-first-ref",
    )
    assert approved.status == 200, approved.text
    assert approved.json["resume_job_id"] is None, approved.text
    t035.assert_no_resume(t029_stack.dbname, decision_first["run_id"])

    exited = t035.confirm_waiting_exit(t029_stack, decision_first, execution_unit_id="unit-first")
    assert exited.status == 200, exited.text
    decision_first_waiting = t035.waiting_row(t029_stack.dbname, decision_first["run_id"])
    assert decision_first_waiting["resume_job_id"] is not None, decision_first_waiting
    first_job = t035.resume_job(t029_stack.dbname, decision_first["run_id"])
    assert first_job["id"] == decision_first_waiting["resume_job_id"], first_job
    assert first_job["state"] == "ready", first_job
    assert first_job["resume_artifact_id"] is not None, first_job

    # Exit first: the exit only records the fact; the decision creates the resume.
    early_exit = t035.confirm_waiting_exit(t029_stack, exit_first, execution_unit_id="unit-second")
    assert early_exit.status == 200, early_exit.text
    t035.assert_no_resume(t029_stack.dbname, exit_first["run_id"])

    rejected = supervisor.resolve_waiting(
        exit_first["run_id"],
        kind="approval",
        outcome="rejected",
        reference="exit-first-ref",
    )
    assert rejected.status == 200, rejected.text
    assert rejected.json["resume_job_id"] is not None, rejected.text
    second_job = t035.resume_job(t029_stack.dbname, exit_first["run_id"])
    assert second_job["id"] == rejected.json["resume_job_id"], (second_job, rejected.text)
    assert second_job["state"] == "ready", second_job

    for lease, job in ((decision_first, first_job), (exit_first, second_job)):
        run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
        assert run["state"] == "queued", run
        assert run["phase"] == "idle", run
        assert run["finished_at"] is None, run
        # claim 2 -> waiting 3 -> resume 4: exactly one bump per transition.
        assert run["revision"] == 4, run

        old_job = t035.job_row(t029_stack.dbname, lease["job_id"])
        assert old_job["state"] == "completed", old_job
        assert old_job["current_attempt_token"] is None, old_job
        old_attempt = t035.attempt_row(t029_stack.dbname, lease["attempt_token"])
        assert old_attempt["exited_at"] is not None and old_attempt["revoked_at"] is not None
        assert t035.quota_counter(t029_stack.dbname)["used"] == 0

        # One allocation row, released exactly once, still matching the old job.
        allocation = t035.allocation_for_job(t029_stack.dbname, lease["job_id"])
        assert allocation is not None and allocation["released_at"] is not None, allocation
        jobs = t035.jobs_for_run(t029_stack.dbname, lease["run_id"])
        assert [(entry["kind"], entry["state"]) for entry in jobs] == [
            ("start", "completed"),
            ("resume", "ready"),
        ], jobs
        assert len(t035.resume_jobs(t029_stack.dbname, lease["run_id"])) == 1

    # Duplicate resolution and duplicate exit after the resume are idempotent: the
    # same resume job id, no second job/artifact/event, no state change.
    resume_artifact = t035.resume_job(t029_stack.dbname, decision_first["run_id"])[
        "resume_artifact_id"
    ]
    events_before = t035.run_event_count(t029_stack.dbname, decision_first["run_id"])
    replay = supervisor.resolve_waiting(
        decision_first["run_id"],
        kind="approval",
        outcome="approved",
        reference="decision-first-ref",
    )
    assert replay.status == 200, replay.text
    assert replay.json["resume_job_id"] == first_job["id"], replay.text
    repeated_exit = t035.confirm_waiting_exit(
        t029_stack, decision_first, execution_unit_id="unit-first"
    )
    assert repeated_exit.status == 200, repeated_exit.text
    assert len(t035.resume_jobs(t029_stack.dbname, decision_first["run_id"])) == 1
    assert t035.run_event_count(t029_stack.dbname, decision_first["run_id"]) == events_before
    assert (
        t035.resume_job(t029_stack.dbname, decision_first["run_id"])["resume_artifact_id"]
        == resume_artifact
    )
    assert t035.run_state_row(t029_stack.dbname, decision_first["run_id"])["state"] == "queued"

    # A different decision after the resume is a conflict; nothing is rewritten.
    conflict = supervisor.resolve_waiting(
        decision_first["run_id"],
        kind="approval",
        outcome="expired",
        reference="decision-first-ref",
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == t035.DECISION_CONFLICT, conflict.text
    assert t035.waiting_decision(t029_stack.dbname, decision_first["run_id"]) == {
        "kind": "approval",
        "outcome": "approved",
        "reference": "decision-first-ref",
    }


def test_resume_claim_carries_the_canonical_sealed_payload(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-resume-claim", checkpoint_id="ckpt-resume"
    )
    supervisor = t035.execution_client(t029_stack, "supervisor")
    resolved = supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="resume-ref-1"
    )
    assert resolved.status == 200, resolved.text
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-resume-claim"
    ).status == 200

    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    job = t035.resume_job(t029_stack.dbname, lease["run_id"])
    artifact = t035.artifact_row(t029_stack.dbname, job["resume_artifact_id"])
    assert artifact["purpose"] == t035.RESUME_PURPOSE, artifact
    assert artifact["state"] == "ready", artifact
    assert artifact["owner_id"] == owner.creator.principal_id, artifact

    payload, object_key = t035.resume_payload_object(t029_stack, artifact["id"])
    assert artifact["object_key"] == object_key
    assert artifact["sha256"] == hashlib.sha256(payload).hexdigest(), artifact
    assert int(artifact["size_bytes"]) == len(payload), artifact
    expected = t035.expected_resume_payload(
        lease["run_id"],
        "approval",
        "ckpt-resume",
        {"kind": "approval", "outcome": "approved", "reference": "resume-ref-1"},
    )
    assert payload == expected, (payload, expected)

    # The claim is a real resume lease: job_kind, artifact id and a signed URL that
    # serves exactly the canonical payload bytes.
    claimed = supervisor.claim_job(execution_unit_id="unit-resume-claim")
    assert claimed.status == 200, claimed.text
    resumed = claimed.json
    assert resumed["job_id"] == job["id"], (resumed, job)
    assert resumed["job_kind"] == "resume", resumed
    assert resumed["run_id"] == lease["run_id"], resumed
    assert resumed["resume_artifact_id"] == artifact["id"], resumed
    assert isinstance(resumed["resume_url"], str) and resumed["resume_url"].startswith("http")
    assert t029.download(resumed["resume_url"]) == expected
    # A fresh attempt with a different token, not a reuse of the waiting attempt.
    assert resumed["attempt_token"] != lease["attempt_token"], resumed

    attempts = t035.attempts_for_run(t029_stack.dbname, lease["run_id"])
    assert len(attempts) == 2, attempts
    live = [
        attempt
        for attempt in attempts
        if attempt["exited_at"] is None and attempt["revoked_at"] is None
    ]
    assert len(live) == 1, live
    assert live[0]["token"] == resumed["attempt_token"], live
    assert live[0]["job_id"] == job["id"], live

    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "running" and run["phase"] == "executing", run
    assert t035.quota_counter(t029_stack.dbname)["used"] == 1
    # The waiting attempt's allocation is released once; the fresh resume attempt
    # takes its own holder allocation (one row per job, never mutated history).
    assert t035.allocation_for_job(t029_stack.dbname, lease["job_id"])["released_at"] is not None
    assert t035.allocation_for_job(t029_stack.dbname, job["id"])["released_at"] is None
    assert len(t035.quota_allocations(t029_stack.dbname)) == 2
    assert waiting["resume_job_id"] == job["id"], waiting


def test_waiting_does_not_accrue_active_time_and_resume_keeps_the_total(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack,
        execution_unit_id="unit-wait-clock",
        checkpoint_id="ckpt-clock",
        shift_seconds=2,
    )
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    first = t035.active_headers(detail)
    t035.assert_active_ms(first["active_elapsed_ms"], at_least=2000, below=3000, label="waiting")

    # Age the waiting row by a minute: waiting must not be counted as active time.
    t035.age_waiting(t029_stack.dbname, lease["run_id"], 60)
    aged = t035.active_headers(owner.creator.client.get(t029.run_path(lease["run_id"])))
    t035.assert_active_ms(
        aged["active_elapsed_ms"], at_least=2000, below=3000, label="aged waiting"
    )

    supervisor = t035.execution_client(t029_stack, "supervisor")
    assert supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="clock-ref"
    ).status == 200
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-wait-clock"
    ).status == 200
    assert supervisor.claim_job(execution_unit_id="unit-wait-clock-2").status == 200

    resumed_token = t035.attempts_for_run(t029_stack.dbname, lease["run_id"])[-1]["token"]
    t035.shift_attempt_started_at_seconds(t029_stack.dbname, resumed_token, 1)
    resumed = t035.active_headers(owner.creator.client.get(t029.run_path(lease["run_id"])))
    # The accumulated waiting-attempt interval is kept and the new attempt adds to it.
    t035.assert_active_ms(
        resumed["active_elapsed_ms"], at_least=2900, below=4000, label="resumed"
    )
    assert resumed["max_active_ms"] == t035.GOLDEN_MAX_ACTIVE_MS


def test_children_resolution_requires_this_runs_terminal_child(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack,
        kind="children",
        execution_unit_id="unit-children",
        checkpoint_id="ckpt-children",
    )
    supervisor = t035.execution_client(t029_stack, "supervisor")

    # A top-level Run is not this Run's child.
    foreign = t035.queued_run(t029_stack, owner)
    foreign_attempt = supervisor.resolve_waiting(
        lease["run_id"], kind="children", outcome="succeeded", child_run_id=foreign["id"]
    )
    assert foreign_attempt.status == 409, foreign_attempt.text
    assert foreign_attempt.json["error"]["code"] == t035.RUN_STATE_CONFLICT
    assert foreign_attempt.json["error"]["details"]["reason"] == "child_not_owned"
    assert t035.waiting_decision(t029_stack.dbname, lease["run_id"]) is None

    # A live child cannot resume the parent.
    child = t035.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="running",
    )
    live = supervisor.resolve_waiting(
        lease["run_id"], kind="children", outcome="succeeded", child_run_id=child
    )
    assert live.status == 409, live.text
    assert live.json["error"]["details"]["reason"] == "child_not_terminal"

    # Once the child is terminal, the child-first order stores the result and waits
    # for the exit; the exit then creates the one resume.
    t035.set_run_state(t029_stack.dbname, child, "succeeded")
    stored = supervisor.resolve_waiting(
        lease["run_id"], kind="children", outcome="succeeded", child_run_id=child
    )
    assert stored.status == 200, stored.text
    assert stored.json["resume_job_id"] is None, stored.text
    decision = t035.waiting_decision(t029_stack.dbname, lease["run_id"])
    assert decision == {"kind": "children", "outcome": "succeeded", "child_run_id": child}, decision
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])

    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-children"
    ).status == 200
    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert waiting["resume_job_id"] is not None, waiting
    jobs = t035.resume_jobs(t029_stack.dbname, lease["run_id"])
    assert len(jobs) == 1 and jobs[0]["state"] == "ready", jobs
    run = t035.run_state_row(t029_stack.dbname, lease["run_id"])
    assert run["state"] == "queued", run


def test_children_exit_first_resumes_once(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack,
        kind="children",
        execution_unit_id="unit-children-exit",
        checkpoint_id="ckpt-children-exit",
    )
    child = t035.seed_child_run(
        t029_stack,
        parent_run_id=lease["run_id"],
        owner_id=owner.creator.principal_id,
        state="failed",
    )
    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-children-exit"
    ).status == 200
    t035.assert_no_resume(t029_stack.dbname, lease["run_id"])

    resolved = t035.execution_client(t029_stack, "supervisor").resolve_waiting(
        lease["run_id"], kind="children", outcome="failed", child_run_id=child
    )
    assert resolved.status == 200, resolved.text
    assert resolved.json["resume_job_id"] is not None, resolved.text
    assert len(t035.resume_jobs(t029_stack.dbname, lease["run_id"])) == 1
    assert t035.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "queued"
