"""F11/T069: artifact deletion and the execution side-conditions.

Two different run facts are proven here on the real F03 stack:

* an artifact that is a frozen execution manifest of a recoverable run is a
  configuration input, not disposable user content: 409 `resource_in_use`, and
  nothing is tombstoned;
* a task/output artifact linked to a run is user content: deleting it enters the
  existing T036 cancel path. A queued run is really cancelled (and its run
  artifact list stops naming the deleted artifact), while a parked
  `recovery_pending` run is left to the T038 recovery gate, which now refuses the
  tombstoned input with `input_deleted` instead of resuming it.
"""

from __future__ import annotations

import json

from support import t029, t031, t038, t041, t069
from support import stack as st


def test_a_recoverable_runs_manifest_artifact_is_resource_in_use(t069_stack) -> None:
    stack = t069_stack
    owner, run, _lease = t041.claimed_run(stack)
    manifest = t069.run_snapshot_manifest_artifact(stack.dbname, run["id"])
    before = t069.artifact_row(stack.dbname, manifest)
    assert before["state"] == "ready" and before["deleted_at"] is None

    refused = t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, manifest)
    error = st.assert_error(refused, 409, t069.ERROR_RESOURCE_IN_USE)
    assert error["details"]["references"] == ["recoverable_run"], error["details"]

    # The refusal changed nothing: no tombstone, no job, the run is still the
    # running owner of its frozen configuration.
    after = t069.artifact_row(stack.dbname, manifest)
    assert after["deleted_at"] is None and after["revision"] == before["revision"], after
    assert t069.deletion_jobs(stack.dbname, resource_id=manifest) == []
    assert t069.run_rows(stack.dbname, run["id"])["state"] == "running"


def test_deleting_a_run_linked_artifact_cancels_the_queued_run(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    run = t029.queued_run(stack, owner)
    produced = t031.seed_output_artifact(stack, owner.creator.principal_id)
    t031.link_output(stack.dbname, run["id"], produced)
    before = t069.run_artifact_list(owner.creator, run["id"])
    assert before.status == 200, before.text
    assert produced in {item["id"] for item in before.json["items"]}, before.json

    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, produced)
    )

    row = t069.run_rows(stack.dbname, run["id"])
    assert row["state"] == "cancelled", row

    statuses = [
        json.loads(event["payload"]).get("state")
        for event in t069.run_events(stack.dbname, run["id"])
        if event["type"] == "run.status"
    ]
    assert "cancelled" in statuses, statuses

    # The run artifact list is the owner's authoritative produced-artifact view:
    # the tombstoned artifact is gone immediately, before any physical purge.
    after = t069.run_artifact_list(owner.creator, run["id"])
    assert after.status == 200, after.text
    assert after.json["items"] == [], after.json

    stored = t069.deletion_job(stack.dbname, job["id"])
    details = t069.scope_details(stored)
    assert details["checkpoints"]["run_ids"] == [run["id"]], details["checkpoints"]

    cancels = t069.audit_events(stack.dbname, "deletion.run_cancel", resource_id=run["id"])
    assert len(cancels) == 1, cancels
    metadata = json.loads(cancels[0]["metadata"])
    assert metadata["result_state"] == "cancelled", metadata
    assert metadata["outcome"] == "terminal", metadata


def test_a_parked_runs_deleted_input_makes_recovery_refuse(t069_stack, tmp_path) -> None:
    stack = t069_stack
    owner, lease, _report = t038.parked_run(
        stack, execution_unit_id="unit-t069-parked"
    )
    run_id = lease["run_id"]
    input_artifact = t069.run_rows(stack.dbname, run_id)["input_artifact_id"]
    assert t069.run_rows(stack.dbname, run_id)["state"] == "recovery_pending"

    t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, input_artifact)
    )

    # The parked run is not silently cancelled (it cannot execute anyway); the
    # recovery gate is the authority that must refuse the deleted input.
    assert t069.run_rows(stack.dbname, run_id)["state"] == "recovery_pending"
    assert t069.audit_events(stack.dbname, "deletion.run_cancel", resource_id=run_id) == []

    completed, refusal = t038.recover(
        stack,
        run_id,
        evidence=t038.recovery_evidence("unit-t069-parked"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    t038.assert_refused(refusal, t038.CODE_INPUT_DELETED)
    assert t069.run_rows(stack.dbname, run_id)["state"] == "recovery_pending"


def test_a_failed_cancellation_fanout_is_recorded_instead_of_swallowed(
    t069_stack,
) -> None:
    """T069 AC4: a post-commit fan-out failure stays visible on the job.

    The failure is a real database error: a trigger refuses the "clean" job touch
    the fan-out writes after a successful cancellation. The tombstone has already
    committed, so the delete still answers 202 and the Run is really cancelled,
    but the job must carry ``run_cancel_incomplete`` rather than read as a clean
    delete.
    """
    stack = t069_stack
    owner = t069.owner(stack)
    run = t029.queued_run(stack, owner)
    produced = t031.seed_output_artifact(stack, owner.creator.principal_id)
    t031.link_output(stack.dbname, run["id"], produced)

    st.sql(stack.dbname, """
        CREATE OR REPLACE FUNCTION platform.t069_fail_clean_cancel() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'injected: clean cancellation fan-out touch';
        END
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER t069_fail_clean_cancel BEFORE UPDATE ON platform.deletion_jobs
            FOR EACH ROW WHEN (NEW.error_code IS NULL)
            EXECUTE FUNCTION platform.t069_fail_clean_cancel();
    """)
    try:
        job = t069.deletion_of(
            t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, produced)
        )
    finally:
        st.sql(stack.dbname, """
            DROP TRIGGER IF EXISTS t069_fail_clean_cancel ON platform.deletion_jobs;
            DROP FUNCTION IF EXISTS platform.t069_fail_clean_cancel();
        """)

    stored = t069.deletion_job(stack.dbname, job["id"])
    assert stored["error_code"] == "run_cancel_incomplete", stored
    # The deletion itself stands and the real T036 cancel ran; only recording it
    # failed, and that is what the job row now exposes.
    assert t069.artifact_row(stack.dbname, produced)["deleted_at"] is not None
    assert t069.run_rows(stack.dbname, run["id"])["state"] == "cancelled"
