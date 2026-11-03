"""T038 acceptance: recovery refuses while the old execution cannot be disproved.

Every refusal is a decision against the real database/object store and writes
nothing: a checkpoint backend still present in ``pg_stat_activity`` blocks
recovery until it commits and closes (``database_writes_in_flight``), a missing
sealed manifest is ``artifact_unavailable``, a deleted input is ``input_deleted``,
a tampered manifest is a hash refusal, and the operator evidence must be the
closed shape naming the same execution unit.
"""

from __future__ import annotations

import json

from support import t038
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _assert_still_parked(stack, lease: dict) -> None:
    dbname = stack.dbname
    assert t038.run_state_row(dbname, lease["run_id"])["state"] == "recovery_pending"
    assert t038.recovery_evidence_rows(dbname, lease["run_id"]) == []
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] == "held" and job["current_attempt_token"] is None, job
    assert t038.allocation_for_job(dbname, lease["job_id"])["released_at"] is None
    assert t038.quota_counter(dbname)["used"] == 1
    assert t038.attempt_is_live(dbname, lease["attempt_token"])


def test_an_inflight_checkpoint_transaction_blocks_recovery_until_it_commits(
    t029_stack, tmp_path
) -> None:
    owner = t038.golden_owner(t029_stack)
    lease = t038.claim_lease(t029_stack, owner, execution_unit_id="unit-db-inflight")
    dbname = t029_stack.dbname
    run_id = lease["run_id"]
    token = lease["attempt_token"]

    with t038.open_checkpoint_session(dbname, seconds=12) as session:
        connections = [
            {
                "pid": int(session["pid"]),
                "backend_start": session["backend_start"],
                "application_name": session["application_name"],
            }
        ]
        beat = t038.execution_client(t029_stack, "supervisor").heartbeat_run(
            run_id,
            attempt_token=token,
            execution_unit_id="unit-db-inflight",
            checkpoint_connections=connections,
        )
        assert beat.status == 200, beat.text
        recorded = json.loads(t038.attempt_row(dbname, token)["checkpoint_connections"])
        assert recorded == connections, recorded

        t038.expire_lease(dbname, token)
        _completed, sweep = t038.sweep(t029_stack, "--grace", "1s")
        assert sweep["parked"] == 1, sweep
        assert t038.run_state_row(dbname, run_id)["state"] == "recovery_pending"

        completed, refusal = t038.recover(
            t029_stack,
            run_id,
            evidence=t038.recovery_evidence("unit-db-inflight"),
            directory=tmp_path,
            extra=["--grace", "1s"],
        )
        assert completed.returncode == 1, completed.stdout + completed.stderr
        t038.assert_refused(refusal, t038.CODE_DATABASE_WRITES_IN_FLIGHT)
        assert str(session["pid"]) in refusal["message"], refusal
        failed = [check for check in refusal["checks"] if check["status"] == "failed"]
        assert failed and failed[-1]["name"] == "database_drained", refusal
        _assert_still_parked(t029_stack, lease)

    # The real transaction committed and its backend is gone: recovery may proceed.
    report = t038.recover_ok(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("unit-db-inflight"),
        directory=tmp_path,
        extra=["--grace", "1s"],
    )
    assert report["status"] == "recovered", report
    assert t038.run_state_row(dbname, run_id)["state"] == "queued"
    assert len(t038.recovery_evidence_rows(dbname, run_id)) == 1


def test_missing_manifest_object_refuses_artifact_unavailable(t029_stack, tmp_path) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-missing-manifest"
    )
    manifest = t038.manifest_artifact_id(t029_stack, lease["run_id"])
    t038.delete_sealed_object(t029_stack, manifest)

    completed, refusal = t038.recover(
        t029_stack,
        lease["run_id"],
        evidence=t038.recovery_evidence("unit-missing-manifest"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_ARTIFACT_UNAVAILABLE)
    _assert_still_parked(t029_stack, lease)


def test_deleted_input_refuses_input_deleted(t029_stack, tmp_path) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-deleted-input"
    )
    input_artifact = t038.input_artifact_id(t029_stack, lease["run_id"])
    t038.mark_artifact_deleted(t029_stack.dbname, input_artifact)

    completed, refusal = t038.recover(
        t029_stack,
        lease["run_id"],
        evidence=t038.recovery_evidence("unit-deleted-input"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_INPUT_DELETED)
    _assert_still_parked(t029_stack, lease)


def test_tampered_manifest_bytes_are_a_hash_refusal(t029_stack, tmp_path) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-tampered-manifest"
    )
    manifest = t038.manifest_artifact_id(t029_stack, lease["run_id"])
    t038.tamper_sealed_object(t029_stack, manifest, b'{"manifest":"tampered"}')

    completed, refusal = t038.recover(
        t029_stack,
        lease["run_id"],
        evidence=t038.recovery_evidence("unit-tampered-manifest"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_MANIFEST_HASH_MISMATCH)
    _assert_still_parked(t029_stack, lease)


def test_evidence_shape_and_unit_are_enforced_before_any_write(t029_stack, tmp_path) -> None:
    _owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-evidence-shape"
    )
    run_id = lease["run_id"]

    # Missing --evidence is a refusal, not a "trust me" default.
    completed, refusal = t038.recover(t029_stack, run_id, directory=tmp_path)
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_EVIDENCE_REQUIRED)

    # Free-form text alone is never proof.
    completed, refusal = t038.recover(
        t029_stack, run_id, evidence="the process is dead", directory=tmp_path
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_EVIDENCE_INVALID)

    # A document missing the sandbox proof is refused.
    incomplete = t038.recovery_evidence("unit-evidence-shape")
    del incomplete["sandbox"]
    completed, refusal = t038.recover(
        t029_stack, run_id, evidence=incomplete, directory=tmp_path
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_EVIDENCE_INVALID)

    # The evidence must name THIS attempt's execution unit.
    completed, refusal = t038.recover(
        t029_stack,
        run_id,
        evidence=t038.recovery_evidence("some-other-unit"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout
    t038.assert_refused(refusal, t038.CODE_EVIDENCE_UNIT_MISMATCH)
    assert "some-other-unit" in refusal["message"], refusal
    _assert_still_parked(t029_stack, lease)


def test_usage_errors_exit_two_without_json(t029_stack) -> None:
    completed = t038.run_runs_command(t029_stack, ["recover"])
    assert completed.returncode == 2, completed.stdout + completed.stderr
    completed = t038.run_runs_command(t029_stack, ["recover", "--run", "not-a-uuid"])
    assert completed.returncode == 2, completed.stdout + completed.stderr
    completed = t038.run_runs_command(
        t029_stack,
        [
            "recover",
            "--run",
            "00000000-0000-4000-8000-000000000000",
            "--evidence",
            "no-such-file.json",
        ],
    )
    assert completed.returncode == 2, completed.stdout + completed.stderr
