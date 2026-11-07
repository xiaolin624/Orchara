"""F13B/T107 (GitHub #108): real lifecycle fault injection for FAULT-04/05/06/10.

Each test drives the **real** F13B stack (throwaway PostgreSQL 16.4 migrated by
the compiled ``cmd/admin``, the compiled ``cmd/api`` with a real object store and
mTLS listener, HMAC-signed sealed links) and, for FAULT-05, the compiled real
``cmd/supervisor exec --once`` process. Nothing is mocked: the "old worker" is a
real OS process with a real PostgreSQL session, the manifests are the real
compiler's sealed bytes, and every assertion reads PostgreSQL or the sealed
object bytes.

The four faults:

* FAULT-04 — a v1 Run exists, the application then publishes v2 through the real
  review transaction; the Run must still resume against the sealed v1 manifest,
  and deleting that object must be an explicit ``artifact_unavailable`` refusal,
  never a quiet fall back to v2.
* FAULT-05 — the real supervisor holds a live attempt/lease and a real database
  session; the process is suspended at the OS level (heartbeat stops, process and
  session stay alive), the lease is expired and the real ``sweep-leases`` parks
  the Run in ``recovery_pending``. No second attempt is issued, the old token's
  writes are refused, and only an operator recovery after the process is gone
  may requeue it.
* FAULT-06 — cancel a running Run, then deliver late result/exit/duplicate queue
  deliveries/events/artifacts/capability calls: the terminal state never moves,
  no new capability operation appears and the event seq stays contiguous.
* FAULT-10 — two opposite approval decisions race over real mTLS: exactly one is
  stored (the other is ``409 decision_conflict``), and replayed worker events
  consume no second seq and produce no duplicate projection; the terminal status
  sequence is monotonic.

Evidence is written to ``product/reports/f13b/T107-faults.json`` only when
``F13B_WRITE_EVIDENCE=1`` (see ``support/t107.py``).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from support import (
    t025,
    t027,
    t029,
    t031,
    t032,
    t033,
    t035,
    t036,
    t038,
    t041,
    t042,
    t068,
    t078,
    t107,
)
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


@pytest.fixture(scope="session")
def fault_evidence() -> t107.Faults:
    branch, commit = t107.git_branch_and_commit()
    recorder = t107.Faults(branch=branch, commit=commit)
    yield recorder
    recorder.write_if_enabled()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _object_bytes(stack, artifact_id: str) -> bytes:
    row = t038.artifact_row(stack.dbname, artifact_id)
    return (stack.object_dir / row["object_key"]).read_bytes()


# ---------------------------------------------------------------------------
# FAULT-05: a live old supervisor + an expired lease
# ---------------------------------------------------------------------------
def _wait_attempt(dbname: str, run_id: str, *, timeout: float = 120.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        attempts = t038.attempts_for_run(dbname, run_id)
        if attempts:
            return attempts[0]
        time.sleep(0.01)
    raise AssertionError(f"the real supervisor never claimed run {run_id}")


def _wait_sessions(dbname: str, application_name: str, *, timeout: float = 20.0) -> list[dict]:
    deadline = time.time() + timeout
    rows: list[dict] = []
    while time.time() < deadline:
        rows = t107.database_sessions(dbname, application_name)
        if rows:
            return rows
        time.sleep(0.02)
    raise AssertionError(f"no pg_stat_activity session for {application_name!r}: {rows}")


def _exec_report(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "the supervisor printed no JSON report"
    return json.loads(lines[-1])


def test_fault_05_a_live_old_supervisor_is_never_replaced_while_its_lease_expires(
    t103_stack, tmp_path: Path, fault_evidence: t107.Faults
) -> None:
    stack = t103_stack
    dbname = stack.dbname
    owner = t033.golden_owner(stack)
    application_id = t042.application_for(owner)
    csv_id = t042.ready_csv(stack, owner)
    queued = t042.submit_linear_run(
        owner,
        application_id,
        artifact_id=csv_id,
        input_value={"source": "t107-fault05"},
    )
    run_id = queued["id"]

    execution_unit = "t107-old-worker-" + t107.new_run_token()
    application_name = "t107-old-worker-db-" + t107.new_run_token()
    env = t033.supervisor_exec_env(
        stack, heartbeat="1s", timeout="30s", result_wait="15s"
    )
    # The connection label is observation only: the product's own DSN stays the
    # only credential, and pg_stat_activity can then be pointed at this process.
    env["ORCHARA_SUPERVISOR_DSN"] = t107.dsn_with_application_name(
        stack.dsn, application_name
    )
    process = subprocess.Popen(
        [
            str(stack.binaries["supervisor"]),
            "exec",
            "--once",
            "--worker-id",
            "t107-owner-supervisor",
            "--execution-unit-id",
            execution_unit,
        ],
        cwd=t033.PRODUCT_ROOT,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        # The real process claims a real attempt. Freeze it at the OS level right
        # after the claim commits: its heartbeat stops, but the process and its
        # PostgreSQL session stay alive (the exact "expired lease, old worker
        # still alive" fault). This is not a mocked liveness flag.
        attempt = _wait_attempt(dbname, run_id)
        token = attempt["token"]
        assert attempt["execution_unit_id"] == execution_unit, attempt
        suspended = t107.suspend_process(process.pid)
        identity_before = t107.process_identity(process.pid)
        assert identity_before["alive"] is True, identity_before
        sessions_before = _wait_sessions(dbname, application_name)
        run_before = t038.run_state_row(dbname, run_id)
        assert run_before["state"] == "running", run_before
        events_before = t038.run_event_count(dbname, run_id)

        # Real lease loss: the deadline moves into the past (clock/lease-store
        # fault) and the real sweep parks the Run. Sweep never requeues.
        t038.expire_lease(dbname, token, lease_seconds=120, heartbeat_seconds=120)
        completed, sweep = t038.sweep(stack, "--grace", "1s")
        assert sweep["parked"] == 1, sweep
        events_after_sweep = t038.run_event_count(dbname, run_id)
        assert events_after_sweep == events_before + 1, (
            events_before,
            events_after_sweep,
            "the sweep must project exactly one recovery_pending run.status event",
        )
        parked = t038.run_state_row(dbname, run_id)
        assert parked["state"] == t038.STATE_RECOVERY, parked
        recovery_reason = t038.recovery_columns(dbname, run_id)["recovery_reason"]
        assert recovery_reason == t038.REASON_LEASE_EXPIRED, recovery_reason
        attempt_parked = t038.attempt_row(dbname, token)
        job = t038.job_row(dbname, attempt["job_id"])
        assert job["state"] == "held" and job["current_attempt_token"] is None, job
        allocation = t038.allocation_for_job(dbname, attempt["job_id"])
        assert allocation is not None and allocation["released_at"] is None, allocation
        assert t038.attempt_is_live(dbname, token), t038.attempt_row(dbname, token)
        # No second execution even though the Run is parked: the only live job is
        # held, so the real claim endpoint has nothing to hand out.
        t038.claim_none(stack, execution_unit_id="t107-fault05-second")
        assert len(t038.attempts_for_run(dbname, run_id)) == 1
        assert len(t038.recovery_evidence_rows(dbname, run_id)) == 0
        # The old process is still there, with its real database backend visible.
        identity_parked = t107.process_identity(process.pid)
        assert identity_parked["alive"] is True, identity_parked
        assert identity_parked["creation_time_epoch"] == identity_before["creation_time_epoch"]
        sessions_parked = t107.database_sessions(dbname, application_name)
        assert sessions_parked, "the old supervisor's real DB session disappeared"

        # The fence in action: an independent heartbeat with the old token is
        # refused while the Run is parked; the old process's own report after the
        # resume is the same refusal from the real writer.
        late_beat = t038.execution_client(stack, "supervisor").heartbeat_run(
            run_id,
            attempt_token=token,
            execution_unit_id=execution_unit,
        )
        assert late_beat.status == 409, late_beat.text
        assert late_beat.json["error"]["code"] == "stale_attempt", late_beat.text

        # Let the old process run again. Its next heartbeat/write must be refused
        # (the lease is gone); the Run must stay parked with the same attempt.
        t107.resume_process(process.pid)
        stdout, stderr = process.communicate(timeout=180)
        report = _exec_report(stdout)
        assert report["status"] == "failed", (report, stderr[-2000:])
        fence_text = json.dumps(report).lower()
        assert (
            "stale_attempt" in fence_text
            or "lease was lost" in fence_text
            or "own a live lease" in fence_text
        ), report
        assert t038.run_state_row(dbname, run_id)["state"] == t038.STATE_RECOVERY
        assert len(t038.attempts_for_run(dbname, run_id)) == 1
        assert t038.attempt_is_live(dbname, token)
        assert t038.run_event_count(dbname, run_id) == events_after_sweep, (
            "the old process's refused writes must not append any run event"
        )

        # The old process is gone now; only the audited operator recovery may
        # requeue the Run, and only then may a *second* attempt be claimed.
        deadline = time.time() + 30
        while time.time() < deadline and t107.database_sessions(dbname, application_name):
            time.sleep(0.05)
        assert t107.database_sessions(dbname, application_name) == []
        recovered = t038.recover_ok(
            stack,
            run_id,
            evidence=t038.recovery_evidence(execution_unit),
            directory=tmp_path,
        )
        assert recovered["state"] == "queued", recovered
        statuses = {check["name"]: check["status"] for check in recovered["checks"]}
        assert statuses["old_execution"] == "passed", recovered
        assert len(t038.recovery_evidence_rows(dbname, run_id)) == 1
        new_lease = t038.claim_next(stack, execution_unit_id="t107-fault05-recovered")
        assert new_lease["run_id"] == run_id, new_lease
        assert new_lease["attempt_token"] != token, new_lease
        assert len(t038.attempts_for_run(dbname, run_id)) == 2
        assert t038.run_state_row(dbname, run_id)["state"] == "running"
    finally:
        teardown = t107.terminate_process(process)

    fault_evidence.record(
        "FAULT-05",
        title="a live old supervisor holds an expired lease; recovery_pending without a second run",
        injection_point=(
            "the compiled real `cmd/supervisor exec --once` claims attempt "
            f"{token[:8]}.. on run {run_id}; the process is suspended with "
            "NtSuspendProcess (heartbeat stops, process+DB session stay alive); then "
            "`UPDATE platform.run_attempts SET lease_deadline = now() - 120s` and the real "
            "`orchara-admin runs sweep-leases --grace 1s` parks the Run"
        ),
        inputs={
            "run_id": run_id,
            "execution_unit_id": execution_unit,
            "attempt_token_prefix": token[:8],
            "supervisor_pid": process.pid,
            "supervisor_application_name": application_name,
            "suspend": suspended,
            "commands": {
                "sweep": "orchara-admin runs sweep-leases --grace 1s",
                "recover": f"orchara-admin runs recover --run {run_id} --evidence <json>",
            },
        },
        independent_truth={
            "process": {
                "before": identity_before,
                "while_parked": identity_parked,
                "verdict": "still alive (STILL_ACTIVE) with the same creation time",
            },
            "pg_stat_activity_sessions": sessions_before,
            "sessions_after_park": sessions_parked,
            "sessions_after_exit": t107.database_sessions(dbname, application_name),
            "sweep_report": sweep,
            "platform.run_attempts": {
                "token_prefix": token[:8],
                "exited_at_while_parked": attempt_parked["exited_at"],
                "revoked_at_while_parked": attempt_parked["revoked_at"],
                "attempt_count_before_recovery": 1,
            },
            "supervisor_report": report,
            "supervisor_stderr_tail": stderr[-2000:],
            "independent_late_heartbeat": late_beat.text,
        },
        expected={
            "parked": "run state recovery_pending, reason lease_expired, job held, allocation not released",
            "no_second_execution": "claim returns 204; exactly one attempt before recovery",
            "old_process": "process alive with a real pg_stat_activity backend while parked",
            "old_process_write": "the resumed old process reports failure on the stale lease; run stays parked",
            "recovery": "only after the process is gone: recover -> queued, attempt finalized, then one new attempt",
        },
        observed={
            "parked_state": parked["state"],
            "recovery_reason": recovery_reason,
            "job_state": job["state"],
            "attempts_before_recovery": 1,
            "process_alive_while_parked": identity_parked["alive"],
            "db_sessions_while_parked": len(sessions_parked),
            "events_before_park": events_before,
            "events_after_park": events_after_sweep,
            "old_process_report_status": report["status"],
            "old_process_report_error_code": report["error_code"],
            "old_process_report_error": report["error"],
            "independent_late_heartbeat": {
                "status": late_beat.status,
                "code": late_beat.json["error"]["code"],
            },
            "recovered_state": recovered["state"],
            "attempts_after_successful_recovery": 2,
        },
        commands=[
            "python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q -k fault_05",
        ],
    )

# ---------------------------------------------------------------------------
# FAULT-04: publish v2, resume v1
# ---------------------------------------------------------------------------
def _save_policy(stack, owner, *, policy_seconds: int) -> None:
    """Edit the draft's max_active_seconds through the real draft endpoint."""
    draft = owner.creator.client.get(
        f"/api/v1/applications/{owner.application_id}/draft"
    )
    assert draft.status == 200, draft.text
    etag = draft.header("ETag")
    changed = json.loads(json.dumps(draft.json["definition"]))
    changed["policy"]["max_active_seconds"] = policy_seconds
    saved = t027.save_draft(
        owner.creator,
        owner.application_id,
        etag,
        definition=changed,
        layout=draft.json["layout"],
        capability_card=draft.json["capability_card"],
    )
    assert saved.status == 200, saved.text


def _publish_current_draft(stack, owner) -> dict:
    """Publish one *real* version of the owner's current draft and return its row."""
    version_id = t078.publish_version(stack, owner.creator, owner.application_id)
    versions = {
        row["id"]: row
        for row in t068.version_rows(stack.dbname, owner.application_id)
    }
    assert version_id in versions, (version_id, versions)
    return versions[version_id]


def test_fault_04_a_v1_run_keeps_its_pinned_graph_after_v2_is_published(
    t103_stack, tmp_path: Path, fault_evidence: t107.Faults
) -> None:
    stack = t103_stack
    dbname = stack.dbname
    owner = t038.golden_owner(stack)

    # The v1 Run is created *before* any v2 exists. Its execution snapshot was
    # compiled from the checked-in golden draft, so the pinned manifest must equal
    # the independent checked-in canonical bytes (`tests/snapshots/sales`).
    run = t038.queued_run(stack, owner)
    run_id = run["id"]
    run_row = t038.run_state_row(dbname, run_id)
    v1_snapshot = t027.snapshot_row(dbname, run_row["execution_snapshot_id"])
    golden_jcs, golden_sha = t025.golden_bytes("sales")
    assert v1_snapshot["manifest_sha256"] == golden_sha, v1_snapshot
    v1_artifact = v1_snapshot["manifest_artifact_id"]
    v1_bytes = _object_bytes(stack, v1_artifact)
    assert v1_bytes == golden_jcs, "the sealed v1 manifest is not the golden canonical bytes"
    assert _sha256(v1_bytes) == golden_sha, "the sealed v1 bytes do not hash to the pinned digest"

    # Publish v1 and then v2 through the real submission/review/approval
    # transaction. The application now has a newer active version whose manifest
    # digest differs from the v1 Run's frozen digest.
    v1_version = _publish_current_draft(stack, owner)
    assert v1_version["version_no"] == 1, v1_version
    assert v1_version["manifest_sha256"] == golden_sha, v1_version
    _save_policy(stack, owner, policy_seconds=902)
    v2_version = _publish_current_draft(stack, owner)
    assert v2_version["version_no"] == 2, v2_version
    versions = t068.version_rows(dbname, owner.application_id)
    assert [row["version_no"] for row in versions] == [1, 2], versions
    assert t068.active_version_id(dbname, owner.application_id) == v2_version["id"]
    assert v2_version["manifest_sha256"] != golden_sha, v2_version

    # The v2 manifest object really is different and intact: if a broken resume
    # fell back to "the newest artifact", this is the byte stream it would use.
    v2_bytes = _object_bytes(stack, v2_version["manifest_artifact_id"])
    assert _sha256(v2_bytes) == v2_version["manifest_sha256"]
    assert v2_bytes != golden_jcs

    # Claiming/starting the v1 Run after the v2 publish must load the v1 graph.
    first_lease = t038.claim_next(stack, execution_unit_id="t107-fault04-v1")
    assert first_lease["run_id"] == run_id, first_lease
    assert first_lease["execution_snapshot_id"] == v1_snapshot["id"], first_lease
    assert first_lease["manifest_sha256"] == golden_sha, first_lease
    assert first_lease["manifest"] == json.loads(golden_jcs), "the lease manifest is not v1"
    downloaded = t029.download(first_lease["manifest_url"])
    assert downloaded == golden_jcs, "the signed manifest URL served v2/other bytes"
    assert _sha256(downloaded) == golden_sha

    # A real lease loss parks the Run; the audited recovery re-verifies the same
    # pinned manifest before it may requeue the Run.
    t038.expire_lease(dbname, first_lease["attempt_token"])
    _completed, sweep = t038.sweep(stack, "--grace", "1s")
    assert sweep["parked"] == 1, sweep
    assert t038.run_state_row(dbname, run_id)["state"] == t038.STATE_RECOVERY

    recovered = t038.recover_ok(
        stack,
        run_id,
        evidence=t038.recovery_evidence("t107-fault04-v1"),
        directory=tmp_path,
    )
    assert recovered["state"] == "queued", recovered
    statuses = {check["name"]: check["status"] for check in recovered["checks"]}
    assert statuses["manifest_artifact"] == "passed", recovered
    assert statuses["old_execution"] == "passed", recovered
    evidence_after_recovery = len(t038.recovery_evidence_rows(dbname, run_id))
    assert evidence_after_recovery == 1

    second_lease = t038.claim_next(stack, execution_unit_id="t107-fault04-v1b")
    assert second_lease["run_id"] == run_id, second_lease
    assert second_lease["attempt_token"] != first_lease["attempt_token"], second_lease
    assert second_lease["manifest_sha256"] == golden_sha, second_lease
    assert t029.download(second_lease["manifest_url"]) == golden_jcs

    # Park once more and remove ONLY the v1 sealed object. The recovery path must
    # refuse with a stable code and never consult the intact v2 object.
    t038.expire_lease(dbname, second_lease["attempt_token"])
    _completed, sweep = t038.sweep(stack, "--grace", "1s")
    assert sweep["parked"] == 1, sweep
    t038.delete_sealed_object(stack, v1_artifact)
    assert not (stack.object_dir / t038.artifact_row(dbname, v1_artifact)["object_key"]).exists()
    assert _sha256(_object_bytes(stack, v2_version["manifest_artifact_id"])) == (
        v2_version["manifest_sha256"]
    )

    completed, refusal = t038.recover(
        stack,
        run_id,
        evidence=t038.recovery_evidence("t107-fault04-v1b"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    t038.assert_refused(refusal, t038.CODE_ARTIFACT_UNAVAILABLE)
    failed_checks = [check for check in refusal["checks"] if check["status"] == "failed"]
    assert failed_checks and failed_checks[-1]["name"] == "manifest_artifact", refusal
    assert t038.run_state_row(dbname, run_id)["state"] == t038.STATE_RECOVERY
    assert len(t038.recovery_evidence_rows(dbname, run_id)) == evidence_after_recovery, (
        "a refused recovery must not append a second evidence row"
    )
    # No v2 fallback in the stored facts either: the Run still pins v1.
    assert t038.run_state_row(dbname, run_id)["execution_snapshot_id"] == v1_snapshot["id"]
    assert t027.snapshot_row(dbname, v1_snapshot["id"])["manifest_sha256"] == golden_sha

    fault_evidence.record(
        "FAULT-04",
        title="publish v2 after a v1 Run/snapshot exists, then resume/recover v1",
        injection_point=(
            "real POST /applications/{id}/submit + /reviews/{id}/decision publishes v2 "
            "(new platform.application_versions row and sealed v2 manifest artifact); "
            "then the v1 sealed manifest object is deleted from the real object store "
            "before `orchara-admin runs recover`"
        ),
        inputs={
            "run_id": run_id,
            "v1_snapshot_id": v1_snapshot["id"],
            "v1_manifest_artifact_id": v1_artifact,
            "v1_manifest_sha256": golden_sha,
            "v1_version_id": v1_version["id"],
            "v2_version_id": v2_version["id"],
            "v2_manifest_sha256": v2_version["manifest_sha256"],
            "v2_manifest_artifact_id": v2_version["manifest_artifact_id"],
            "golden_manifest_bytes_sha256": _sha256(golden_jcs),
        },
        independent_truth={
            "checked_in_golden_manifest": "tests/snapshots/sales/manifest.jcs + manifest.sha256",
            "object_bytes_v1_sha256": _sha256(v1_bytes),
            "object_bytes_v2_sha256": _sha256(v2_bytes),
            "platform.runs.execution_snapshot_id": run_row["execution_snapshot_id"],
            "platform.runs.manifest_sha256": t027.run_row(dbname, run_id)[
                "manifest_sha256"
            ],
            "platform.execution_snapshots": {
                "v1": {
                    "manifest_sha256": v1_snapshot["manifest_sha256"],
                    "manifest_artifact_id": v1_artifact,
                },
                "v2": {
                    "manifest_sha256": v2_version["manifest_sha256"],
                    "manifest_artifact_id": v2_version["manifest_artifact_id"],
                },
            },
            "platform.application_versions": [
                {"version_no": row["version_no"], "manifest_sha256": row["manifest_sha256"]}
                for row in versions
            ],
            "recovery_refusal": refusal,
        },
        expected={
            "claim_1": "manifest_sha256 == v1 golden sha; manifest bytes == golden JCS; snapshot == v1",
            "recovery_1": "status recovered; manifest_artifact check passed; state queued",
            "claim_2": "the new attempt still loads the v1 manifest bytes, not the v2 artifact",
            "recovery_2": f"status refused; code {t038.CODE_ARTIFACT_UNAVAILABLE}; run stays "
            "recovery_pending; no evidence row; no v2 fallback",
        },
        observed={
            "claim_1": {
                "manifest_sha256": first_lease["manifest_sha256"],
                "manifest_url_sha256": _sha256(downloaded),
                "execution_snapshot_id": first_lease["execution_snapshot_id"],
            },
            "recovery_1": {
                "status": recovered["status"],
                "state": recovered["state"],
                "checks": statuses,
            },
            "claim_2": {
                "manifest_sha256": second_lease["manifest_sha256"],
                "attempt_token_differs": True,
            },
            "recovery_2": {
                "status": refusal["status"],
                "code": refusal["code"],
                "run_state": t038.run_state_row(dbname, run_id)["state"],
                "evidence_rows": len(t038.recovery_evidence_rows(dbname, run_id)),
                "v2_object_still_intact": True,
            },
        },
        commands=[
            "python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q -k fault_04",
        ],
    )


# ---------------------------------------------------------------------------
# FAULT-06: cancel, then every late delivery
# ---------------------------------------------------------------------------
def _event_seqs(dbname: str, run_id: str) -> list[int]:
    return [row["seq"] for row in t032.run_events(dbname, run_id)]


def _assert_contiguous(seqs: list[int], *, what: str) -> None:
    assert seqs == list(range(1, len(seqs) + 1)), (what, seqs)


def test_fault_06_a_cancelled_run_never_revives_on_late_deliveries(
    t103_stack, tmp_path: Path, fault_evidence: t107.Faults
) -> None:
    stack = t103_stack
    dbname = stack.dbname
    owner = t038.golden_owner(stack)
    lease = t038.claim_lease(stack, owner, execution_unit_id="t107-fault06")
    run_id = lease["run_id"]
    token = lease["attempt_token"]
    worker = t038.execution_client(stack, "worker")
    supervisor = t038.execution_client(stack, "supervisor")
    artifacts = t036.run_artifact_client(stack)
    run_artifacts_before = t031.run_artifact_count(dbname, run_id)

    # A real worker event recorded before cancel. It will be replayed verbatim
    # after the terminal cancel: the idempotent identity must not consume a seq.
    first_event = t032.node_event(local_sequence=1, node_id="node-a", state="running")
    accepted = t032.submit_events(worker, run_id, token, [first_event])
    assert accepted.status == 200, accepted.text
    assert _event_seqs(dbname, run_id) == [1, 2, 3]

    cancel = t036.cancel_run(owner.creator, run_id)
    assert cancel.status == 202, cancel.text
    assert cancel.json["state"] == t036.STATE_CANCELLING, cancel.text
    cancel_requested_at = t036.run_cancel_row(dbname, run_id)["cancel_requested_at"]
    assert cancel_requested_at is not None

    # While cancelling: every execution-bearing call is refused, nothing is
    # written. These are the "new capability / new work" guards.
    refused_result = worker.submit_result(
        run_id,
        body=t036.result_body(
            lease, suggested_state="failed", error_code="tool_crash"
        ),
    )
    assert refused_result.status == 409, refused_result.text
    assert refused_result.json["error"]["code"] == t036.RUN_STATE_CONFLICT
    refused_event = t032.submit_events(worker, run_id, token, [t032.node_event()])
    assert refused_event.status == 409, refused_event.text
    assert refused_event.json["error"]["code"] == t036.RUN_STATE_CONFLICT
    refused_upload = artifacts.create_upload(
        run_id,
        {
            "attempt_token": token,
            "media_type": "application/json",
            "size_bytes": 4,
            "purpose": "node_output",
        },
    )
    assert refused_upload.status == 409, refused_upload.text
    assert refused_upload.json["error"]["code"] == t036.RUN_STATE_CONFLICT
    refused_capability = t041.invoke(
        t041.worker_client(stack),
        lease,
        operation_id=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert refused_capability.status == 409, refused_capability.text
    assert refused_capability.json["error"]["code"] == "run_cancelled"
    assert t041.operation_rows(dbname, run_id) == []

    # The supervisor's exit is the only thing that may commit the terminal state.
    exited = supervisor.confirm_exit(
        token,
        execution_unit_id="t107-fault06",
        evidence=t036.exit_evidence(lease, started=True, exit_code=137),
    )
    assert exited.status == 200, exited.text
    terminal = t036.run_cancel_row(dbname, run_id)
    t036.assert_cancelled(terminal)
    terminal_events = t032.run_events(dbname, run_id)
    terminal_seqs = [row["seq"] for row in terminal_events]
    _assert_contiguous(terminal_seqs, what="after cancel")
    status_states = t038.run_status_states(dbname, run_id)
    assert status_states == ["queued", "running", "cancelling", "cancelled"], status_states
    next_seq = t032.next_event_seq(dbname, run_id)
    terminal_json = dict(terminal)

    late_deliveries: list[dict] = []

    def record(name: str, response, note: str) -> None:
        late_deliveries.append(
            {
                "delivery": name,
                "note": note,
                "at": t107.utc_now(),
                "status": response.status,
                "code": (
                    response.json.get("error", {}).get("code")
                    if isinstance(response.json, dict)
                    else None
                ),
                "body": response.text[:400],
            }
        )

    # 1. A late result proposal (new event id) after the terminal cancel.
    late_result_body = t036.result_body(
        lease, suggested_state="failed", error_code="tool_crash"
    )
    late_result = worker.submit_result(run_id, body=late_result_body)
    assert late_result.status == 409, late_result.text
    assert late_result.json["error"]["code"] == t036.RUN_TERMINAL
    record("late_result", late_result, "a fresh result after cancelled")

    # 2. The exact same result delivery repeated: still refused, nothing stored.
    duplicate_result = worker.submit_result(run_id, body=late_result_body)
    assert duplicate_result.status == 409, duplicate_result.text
    assert duplicate_result.json["error"]["code"] == t036.RUN_TERMINAL
    record("duplicate_late_result", duplicate_result, "the same result body replayed")

    # 3. The exact pre-cancel worker event, replayed after the terminal cancel.
    replay_event = t032.submit_events(worker, run_id, token, [first_event])
    assert replay_event.status == 409, replay_event.text
    assert replay_event.json["error"]["code"] == t036.RUN_TERMINAL
    record("replay_pre_cancel_event", replay_event, "same event_id/content replayed")

    # 4. A brand-new worker event after the terminal cancel.
    new_event = t032.submit_events(worker, run_id, token, [t032.node_event()])
    assert new_event.status == 409, new_event.text
    assert new_event.json["error"]["code"] == t036.RUN_TERMINAL
    record("late_new_event", new_event, "a new node.status after cancelled")

    # 5. A duplicate queue delivery / claim poll for the voided job.
    claim_again = t038.execution_client(stack, "supervisor").claim_job(
        worker_id="t107-fault06-queue", execution_unit_id="t107-fault06-queue"
    )
    assert claim_again.status == 204, claim_again.text
    record("duplicate_queue_delivery", claim_again, "claim poll after cancelled")

    # 6. A late exit with identical evidence is idempotent; different evidence is
    #    a conflict. Neither changes the Run.
    replay_exit = supervisor.confirm_exit(
        token,
        execution_unit_id="t107-fault06",
        evidence=t036.exit_evidence(lease, started=True, exit_code=137),
    )
    assert replay_exit.status == 200, replay_exit.text
    claim_evidence = t036.exit_evidence(lease, started=True, exit_code=7)
    different_exit = supervisor.confirm_exit(
        token,
        execution_unit_id="t107-fault06",
        evidence=claim_evidence,
    )
    assert different_exit.status == 409, different_exit.text
    assert different_exit.json["error"]["code"] == "attempt_evidence_conflict"
    record("replay_same_exit", replay_exit, "identical exit evidence")
    record("different_exit", different_exit, "different exit evidence")

    # 7. Artifact upload and capability call after the terminal cancel.
    late_upload = artifacts.create_upload(
        run_id,
        {
            "attempt_token": token,
            "media_type": "application/json",
            "size_bytes": 4,
            "purpose": "node_output",
        },
    )
    assert late_upload.status == 409, late_upload.text
    assert late_upload.json["error"]["code"] == "stale_attempt"
    record("late_artifact_upload", late_upload, "a new capability/artifact call")
    late_capability = t041.invoke(
        t041.worker_client(stack),
        lease,
        operation_id=t041.new_uuid(),
        params=t041.model_params(),
    )
    assert late_capability.status == 403, late_capability.text
    assert late_capability.json["error"]["code"] == "stale_attempt"
    record("late_capability_call", late_capability, "a new model capability call")
    assert t041.operation_rows(dbname, run_id) == []
    assert t031.run_artifact_count(dbname, run_id) == run_artifacts_before, (
        "a late upload must not register any new run artifact"
    )

    # 8. An unauthorised owner read is a 404 (no cross-owner disclosure).
    foreign = t036.minimal_owner(stack)
    foreign_read = t031.get_run(foreign.creator, run_id)
    assert foreign_read.status == 404, foreign_read.text
    record("foreign_owner_read", foreign_read, "another owner reads the cancelled run")

    # 9. An operator recovery of a terminal Run is a refusal, not a re-run.
    completed, refusal = t038.recover(
        stack,
        run_id,
        evidence=t038.recovery_evidence("t107-fault06"),
        directory=tmp_path,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    t038.assert_refused(refusal, t038.CODE_NOT_RECOVERY_PENDING)
    late_deliveries.append(
        {
            "delivery": "recover_cancelled_run",
            "note": "operator recover after terminal",
            "at": t107.utc_now(),
            "status": completed.returncode,
            "code": refusal["code"],
            "body": completed.stdout[:400],
        }
    )

    # The terminal facts did not move at all.
    final = t036.run_cancel_row(dbname, run_id)
    assert final == terminal_json, (final, terminal_json)
    assert t032.run_events(dbname, run_id) == terminal_events
    assert t032.next_event_seq(dbname, run_id) == next_seq
    assert t032.run_event_count(dbname, run_id) == len(terminal_seqs)
    assert _event_seqs(dbname, run_id) == terminal_seqs
    _assert_contiguous(_event_seqs(dbname, run_id), what="after late deliveries")
    assert t038.run_status_states(dbname, run_id) == [
        "queued",
        "running",
        "cancelling",
        "cancelled",
    ]
    assert len(t038.attempts_for_run(dbname, run_id)) == 1
    job = t038.job_row(dbname, lease["job_id"])
    assert job["state"] in {"completed", "void"} and job["current_attempt_token"] is None, job
    assert t038.quota_counter(dbname)["used"] == 0
    allocation = t038.allocation_for_job(dbname, lease["job_id"])
    assert allocation is not None and allocation["released_at"] is not None, allocation

    fault_evidence.record(
        "FAULT-06",
        title="cancel then late result/exit/duplicate queue delivery/events: no revival, no new capability",
        injection_point=(
            "real POST /runs/{id}/cancel while the claimed attempt is running; after the "
            "supervisor's real exit commits cancelled, replay the pre-cancel worker event, "
            "send fresh result/event/upload/capability calls, poll the claim endpoint again, "
            "and call `orchara-admin runs recover` on the terminal Run"
        ),
        inputs={
            "run_id": run_id,
            "attempt_token_prefix": token[:8],
            "cancel_requested_at": cancel_requested_at,
            "pre_cancel_event_id": first_event["event_id"],
            "pre_cancel_event_seq": 3,
            "terminal_revision": terminal["revision"],
            "late_deliveries": late_deliveries,
        },
        independent_truth={
            "platform.runs": terminal_json,
            "platform.run_events": [
                {"seq": row["seq"], "type": row["type"], "payload": row["payload"]}
                for row in terminal_events
            ],
            "status_states": status_states,
            "next_event_seq": next_seq,
            "run_attempts": t038.attempts_for_run(dbname, run_id),
            "jobs": t038.job_row(dbname, lease["job_id"]),
            "quota": t038.quota_counter(dbname),
            "recovery_refusal": refusal,
            "capability_operations": t041.operation_rows(dbname, run_id),
        },
        expected={
            "terminal": "cancelled stays cancelled; revision/row/events identical after every late delivery",
            "execution_calls": "result/event/upload/capability after cancel are refused; zero operation rows",
            "queue": "claim poll returns 204; no second attempt",
            "seq": f"contiguous 1..{next_seq - 1} with no duplicate event",
            "recovery": f"refused {t038.CODE_NOT_RECOVERY_PENDING}",
        },
        observed={
            "final_state": final["state"],
            "final_revision": final["revision"],
            "event_seqs": terminal_seqs,
            "status_states": t038.run_status_states(dbname, run_id),
            "attempts": len(t038.attempts_for_run(dbname, run_id)),
            "capability_operations": len(t041.operation_rows(dbname, run_id)),
            "run_artifacts_before": run_artifacts_before,
            "run_artifacts_after": t031.run_artifact_count(dbname, run_id),
            "late_delivery_statuses": [
                {"delivery": item["delivery"], "status": item["status"], "code": item["code"]}
                for item in late_deliveries
            ],
            "recovery_code": refusal["code"],
        },
        commands=[
            "python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q -k fault_06",
        ],
    )


# ---------------------------------------------------------------------------
# FAULT-10: duplicate events + two concurrent opposite approval decisions
# ---------------------------------------------------------------------------
def _assert_terminal_monotonic(states: list[str], *, what: str) -> None:
    terminal_seen: str | None = None
    for state in states:
        if terminal_seen is not None and state != terminal_seen:
            raise AssertionError(f"{what}: {terminal_seen} -> {state} moves backwards: {states}")
        if state in {"succeeded", "failed", "cancelled"}:
            terminal_seen = state


def test_fault_10_duplicate_events_and_concurrent_opposite_decisions(
    t103_stack, tmp_path: Path, fault_evidence: t107.Faults
) -> None:
    stack = t103_stack
    dbname = stack.dbname
    owner = t038.golden_owner(stack)
    worker = t038.execution_client(stack, "worker")
    supervisor = t038.execution_client(stack, "supervisor")

    # ---- duplicate worker events: one seq, one projection --------------------
    events_lease = t038.claim_lease(stack, owner, execution_unit_id="t107-fault10-events")
    events_run = events_lease["run_id"]
    events_token = events_lease["attempt_token"]
    event = t032.node_event(local_sequence=1, node_id="node-a", state="running")
    accepted = t032.submit_events(worker, events_run, events_token, [event])
    assert accepted.status == 200, accepted.text
    assert _event_seqs(dbname, events_run) == [1, 2, 3]
    seq_after_first = t032.next_event_seq(dbname, events_run)
    row_count = t032.run_event_count(dbname, events_run)

    duplicate = {**event, "local_sequence": 9}
    replay = t032.submit_events(worker, events_run, events_token, [duplicate])
    assert replay.status == 200, replay.text
    assert t032.run_event_count(dbname, events_run) == row_count
    assert t032.next_event_seq(dbname, events_run) == seq_after_first

    second = t032.node_event(local_sequence=2, node_id="node-b", state="succeeded")
    batch = t032.submit_events(worker, events_run, events_token, [event, second])
    assert batch.status == 200, batch.text
    assert _event_seqs(dbname, events_run) == [1, 2, 3, 4]
    seq_after_batch = t032.next_event_seq(dbname, events_run)
    replay_batch = t032.submit_events(worker, events_run, events_token, [event, second])
    assert replay_batch.status == 200, replay_batch.text
    assert t032.next_event_seq(dbname, events_run) == seq_after_batch
    assert t032.run_event_count(dbname, events_run) == 4

    # A real terminal outcome, then replay every event identity: terminal must
    # stay terminal, no row and no seq may be appended.
    proposed = worker.submit_result(
        events_run,
        body=t036.result_body(
            events_lease, suggested_state="failed", error_code="tool_crash"
        ),
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        events_token,
        execution_unit_id="t107-fault10-events",
        evidence=t036.exit_evidence(events_lease, started=True),
    )
    assert exited.status == 200, exited.text
    terminal_row = t038.run_state_row(dbname, events_run)
    assert terminal_row["state"] == "failed", terminal_row
    terminal_events = t032.run_events(dbname, events_run)
    terminal_seqs = [row["seq"] for row in terminal_events]
    _assert_contiguous(terminal_seqs, what="event run terminal")
    terminal_states = t038.run_status_states(dbname, events_run)
    _assert_terminal_monotonic(terminal_states, what="event run")
    terminal_next = t032.next_event_seq(dbname, events_run)

    late_replay = t032.submit_events(worker, events_run, events_token, [event])
    assert late_replay.status == 409, late_replay.text
    assert late_replay.json["error"]["code"] == "run_terminal"
    late_new = t032.submit_events(
        worker, events_run, events_token, [t032.node_event(node_id="node-c")]
    )
    assert late_new.status == 409, late_new.text
    assert late_new.json["error"]["code"] == "run_terminal"
    assert t032.run_events(dbname, events_run) == terminal_events
    assert t032.next_event_seq(dbname, events_run) == terminal_next
    assert t038.run_state_row(dbname, events_run) == terminal_row

    # ---- two concurrent, opposite approval decisions -------------------------
    wait_lease = t038.claim_lease(stack, owner, execution_unit_id="t107-fault10-wait")
    wait_run = wait_lease["run_id"]
    submitted = t035.submit_waiting(
        worker,
        wait_lease,
        kind="approval",
        checkpoint_id="ckpt-t107-fault10",
    )
    assert submitted.status == 200, submitted.text
    assert t038.run_state_row(dbname, wait_run)["state"] == t035.STATE_APPROVAL
    waiting_before = t035.waiting_row(dbname, wait_run)
    assert waiting_before["decision"] is None, waiting_before
    events_before_decisions = t032.run_event_count(dbname, wait_run)
    run_before_decisions = t038.run_state_row(dbname, wait_run)

    outcomes = ("approved", "rejected")
    references = ("t107-race-approve", "t107-race-reject")
    clients = [
        t035.execution_client(stack, "supervisor"),
        t035.execution_client(stack, "supervisor"),
    ]
    barrier = threading.Barrier(2)
    decisions: list[object] = [None, None]

    def decide(index: int) -> None:
        barrier.wait(timeout=30)
        decisions[index] = clients[index].resolve_waiting(
            wait_run,
            kind="approval",
            outcome=outcomes[index],
            reference=references[index],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(decide, (0, 1)))
    for response in decisions:
        assert response is not None, decisions
    statuses = sorted(response.status for response in decisions)
    assert statuses == [200, 409], [(r.status, r.text) for r in decisions]
    winner_index = next(index for index, r in enumerate(decisions) if r.status == 200)
    loser = next(r for r in decisions if r.status == 409)
    assert loser.json["error"]["code"] == t035.DECISION_CONFLICT, loser.text
    winner = decisions[winner_index]
    winner_outcome = outcomes[winner_index]
    winner_reference = references[winner_index]

    # One row, one decision, first (winner) authority. The loser wrote nothing.
    assert t035.waiting_count(dbname, where=f"run_id = '{wait_run}'::uuid") == 1
    waiting = t035.waiting_row(dbname, wait_run)
    decision = t035.waiting_decision(dbname, wait_run)
    assert decision == {
        "kind": "approval",
        "outcome": winner_outcome,
        "reference": winner_reference,
    }, decision
    decision_at = waiting["decision_at"]
    assert decision_at is not None
    assert waiting["resume_job_id"] is None, waiting
    assert t032.run_event_count(dbname, wait_run) == events_before_decisions
    assert t038.run_state_row(dbname, wait_run) == run_before_decisions
    assert t035.resume_jobs(dbname, wait_run) == []

    # Replay the winning decision (idempotent 200) and the losing one (409).
    replay_winner = supervisor.resolve_waiting(
        wait_run, kind="approval", outcome=winner_outcome, reference=winner_reference
    )
    assert replay_winner.status == 200, replay_winner.text
    replay_loser = supervisor.resolve_waiting(
        wait_run,
        kind="approval",
        outcome=outcomes[1 - winner_index],
        reference=references[1 - winner_index],
    )
    assert replay_loser.status == 409, replay_loser.text
    assert replay_loser.json["error"]["code"] == t035.DECISION_CONFLICT
    assert t035.waiting_decision(dbname, wait_run) == decision
    assert t035.waiting_row(dbname, wait_run)["decision_at"] == decision_at
    assert t035.waiting_count(dbname, where=f"run_id = '{wait_run}'::uuid") == 1

    # The exit hands the Run over exactly once; a rejected decision still produces
    # the one resume (the graph must learn the refusal), never a second one.
    confirmed = t035.confirm_waiting_exit(
        stack, wait_lease, execution_unit_id="t107-fault10-wait"
    )
    assert confirmed.status == 200, confirmed.text
    resume = t035.resume_job(dbname, wait_run)
    assert resume["state"] == "ready" and resume["resume_artifact_id"], resume
    assert len(t035.resume_jobs(dbname, wait_run)) == 1
    wait_row = t038.run_state_row(dbname, wait_run)
    assert wait_row["state"] == "queued", wait_row
    wait_states = t038.run_status_states(dbname, wait_run)
    _assert_terminal_monotonic(wait_states, what="waiting run")
    wait_seqs = _event_seqs(dbname, wait_run)
    _assert_contiguous(wait_seqs, what="waiting run")
    # The stored decision cannot be replaced after the resume either.
    late_opposite = supervisor.resolve_waiting(
        wait_run,
        kind="approval",
        outcome=outcomes[1 - winner_index],
        reference="t107-late-opposite",
    )
    assert late_opposite.status == 409, late_opposite.text
    assert late_opposite.json["error"]["code"] == t035.DECISION_CONFLICT
    assert t035.waiting_decision(dbname, wait_run) == decision
    assert len(t035.resume_jobs(dbname, wait_run)) == 1

    # ---- negative boundaries ------------------------------------------------
    worker_decision = t035.execution_client(stack, "worker").resolve_waiting(
        wait_run, kind="approval", outcome="approved", reference="worker-not-allowed"
    )
    assert worker_decision.status == 403, worker_decision.text
    assert worker_decision.json["error"]["code"] == "forbidden"
    unknown = supervisor.resolve_waiting(
        t035.new_uuid(), kind="approval", outcome="approved", reference="unknown-run"
    )
    assert unknown.status == 404, unknown.text
    assert unknown.json["error"]["code"] == "run_not_found"
    foreign = t036.minimal_owner(stack)
    foreign_read = t031.get_run(foreign.creator, wait_run)
    assert foreign_read.status == 404, foreign_read.text

    fault_evidence.record(
        "FAULT-10",
        title="duplicate events and two concurrent opposite approval decisions",
        injection_point=(
            "two real supervisor mTLS clients race `POST /internal/v1/runs/{id}/"
            "waiting-resolution` with approved/rejected over separate connections "
            "(threading.Barrier); duplicate worker events replay the same event_id "
            "through the real `POST /internal/v1/runs/{id}/events` endpoint, including "
            "after the real terminal finalizer committed failed"
        ),
        inputs={
            "event_run_id": events_run,
            "event_id": event["event_id"],
            "duplicate_local_sequence": 9,
            "decision_run_id": wait_run,
            "race_outcomes": list(outcomes),
            "race_references": list(references),
            "decision_statuses": [
                {"index": index, "status": r.status, "body": r.text[:400]}
                for index, r in enumerate(decisions)
            ],
        },
        independent_truth={
            "platform.run_waitings": waiting,
            "stored_decision": decision,
            "waiting_rows_for_run": 1,
            "platform.run_events_waiting_run": [
                {"seq": row["seq"], "type": row["type"], "payload": row["payload"]}
                for row in t032.run_events(dbname, wait_run)
            ],
            "platform.run_events_terminal_run": [
                {"seq": row["seq"], "type": row["type"], "payload": row["payload"]}
                for row in terminal_events
            ],
            "resume_job": resume,
            "late_duplicate_event_response": late_replay.text,
            "late_new_event_response": late_new.text,
        },
        expected={
            "concurrent_decisions": "exactly one 200, the other 409 decision_conflict; one stored decision row",
            "duplicate_events": "same event_id consumes no seq, produces no second row; batch replay is a no-op",
            "terminal": "late events on the failed Run are 409 run_terminal; seq stays contiguous and the row unchanged",
            "resume": "the exit creates exactly one resume job; the stored decision is never replaced",
            "negative": "worker certificate cannot decide (403), unknown run 404, foreign owner read 404",
        },
        observed={
            "decision_statuses": statuses,
            "stored_outcome": decision["outcome"],
            "stored_reference": decision["reference"],
            "decision_rows": 1,
            "resume_jobs": 1,
            "waiting_run_states": wait_states,
            "waiting_run_seqs": wait_seqs,
            "event_run_terminal_states": terminal_states,
            "event_run_terminal_seqs": terminal_seqs,
            "late_event_codes": [
                late_replay.json["error"]["code"],
                late_new.json["error"]["code"],
            ],
            "negative_codes": {
                "worker_decision": worker_decision.json["error"]["code"],
                "unknown_run": unknown.json["error"]["code"],
                "foreign_read": foreign_read.status,
            },
        },
        commands=[
            "python -m pytest tests/f13b/test_t107_lifecycle_faults.py -q -k fault_10",
        ],
    )


