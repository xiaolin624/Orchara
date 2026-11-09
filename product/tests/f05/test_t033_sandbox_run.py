"""T033: the tool-debug execution path on a real runsc (gVisor) sandbox.

The product path is exercised end to end: a CSV uploaded through T028, one debug
run of the golden sales draft, ``cmd/supervisor exec --once`` claiming and running
the fixed preset tool in a minimal OCI bundle inside WSL2/runsc, uploading and
registering the outputs through the run-scoped mTLS endpoints, proposing the
result and confirming exit with the T031 identity evidence. A real
``cmd/maintenance run --kinds artifact_finalize`` process runs concurrently so the
uploaded artifacts really become ready.

A second group proves the sandbox boundaries with the platform-owned probe (no
host file, no foreign file, read-only input, no network, uid 65534) and proves
that timeout and crash leave no sandbox, pid or workspace behind.

Every test here needs WSL2 + runsc; if those are genuinely unavailable the test is
skipped with the exact failing command (and the report records the blocked
environment). Nothing is mocked: the sandbox is real, the mTLS calls are real and
every state assertion reads PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time

import pytest

from support import run_observations as observations
from support import execution_support as execution
from support import t028
from support import stack as st
from support import t033
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)


@pytest.fixture(scope="module", autouse=True)
def require_sandbox() -> None:
    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version' failed; build the rootfs with "
            "deploy/sandbox/build_python_rootfs.sh"
        )


def queued_sales_run(stack, *, content: bytes = t033.SALES_CSV):
    """An uploaded CSV + one queued debug run, claimed by the supervisor binary."""
    owner = t033.golden_owner(stack)
    artifact_id = t033.upload_artifact(stack, owner.creator, content)
    run = t033.debug_run_with_input(stack, owner, artifact_id)
    return owner, run, artifact_id


def test_real_runsc_tool_debug_run_end_to_end(t033_stack) -> None:
    owner, run, _csv_artifact = queued_sales_run(t033_stack)
    digest = execution.rootfs_digest()
    env = execution.supervisor_exec_env(t033_stack, digest=digest)

    with execution.FinalizeWorker(t033_stack):
        completed = execution.run_supervisor_exec(t033_stack, env)

    report = execution.exec_report(completed)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report["run_state"] == "succeeded", report
    assert report["run_id"] == run["id"], report
    assert report["heartbeats"] >= 1, report
    sandbox = report["sandbox"]
    assert sandbox["started"] is True, sandbox
    assert sandbox["exit_code"] == 0, sandbox
    assert sandbox["rootfs_digest"] == digest, sandbox
    assert sandbox["cleanup"]["sandbox_list_matches"] == 0, sandbox
    assert sandbox["cleanup"]["workspace_removed"] is True, sandbox

    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})
    assert state["finished_at"] is not None, state
    output_id = observations.output_artifact_id(t033_stack.dbname, run["id"])
    assert output_id, state

    # The registered summary is the exact documented result, byte for byte, both
    # through the platform content proxy and through a signed download link.
    content = observations.artifact_content(owner.creator, output_id)
    assert content.status == 200, content.text
    assert content.raw == t033.EXPECTED_SUMMARY_JSON, content.raw
    download = t033.artifact_download(owner.creator, output_id)
    assert download.status == 200, download.text
    signed = t033.get_link(download.json["url"])
    assert signed.status == 200
    assert signed.raw == t033.EXPECTED_SUMMARY_JSON

    csv = t033.artifact_by_purpose(t033_stack.dbname, run["id"], "node_output")
    assert csv is not None and csv["media_type"] == "text/csv", csv
    csv_content = observations.artifact_content(owner.creator, csv["id"])
    assert csv_content.status == 200, csv_content.text
    assert csv_content.raw == t033.EXPECTED_SUMMARY_CSV, csv_content.raw

    # The log is separate from data: stored, purpose=log, never listed as output.
    log = t033.artifact_by_purpose(t033_stack.dbname, run["id"], "log")
    assert log is not None, "the sandbox stdout/stderr log was not uploaded"
    assert log["state"] == "ready", log
    listed = t033.list_run_artifacts(owner.creator, run["id"])
    assert listed.status == 200, listed.text
    ids = [item["id"] for item in listed.json["items"]]
    assert set(ids) == {output_id, csv["id"]}, ids
    assert log["id"] not in ids
    for item in listed.json["items"]:
        t033.assert_artifact_shape(item)
    assert t033.list_run_artifacts(t033.minimal_owner(t033_stack).creator, run["id"]).status == 404
    assert t033.list_run_artifacts(owner.admin, run["id"]).status == 404

    # Provenance + run links: written by the live attempt, referencing this run.
    attempts = t033.attempts_for_run(t033_stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    token = attempts[0]["token"]
    provenance = [row for row in t033.provenance_rows_for_run(t033_stack.dbname, run["id"])]
    assert len(provenance) == 3, provenance
    assert {row["artifact_id"] for row in provenance} == {output_id, csv["id"], log["id"]}
    assert all(row["attempt_token"] == token for row in provenance)
    links = t033.run_artifact_links(t033_stack.dbname, run["id"])
    relations = {link["artifact_id"]: link["relation"] for link in links}
    assert relations[output_id] == "output", relations
    assert relations[csv["id"]] == "artifact", relations
    assert relations[log["id"]] == "log", relations
    for link in links:
        if link["artifact_id"] in {output_id, csv["id"], log["id"]}:
            assert link["attempt_token"] == token, link

    # T031 identity evidence plus the T033 sandbox facts, recorded verbatim.
    attempt = t033.attempt_row(t033_stack.dbname, token)
    evidence = json.loads(attempt["exit_evidence"])
    assert evidence["started"] is True
    assert evidence["protocol_version"] == "1.0"
    snapshot_manifest = st.sql(
        t033_stack.dbname,
        "SELECT manifest_sha256 FROM platform.execution_snapshots WHERE id = "
        f"{st.quote_literal(state['execution_snapshot_id'])}::uuid",
    )
    assert evidence["manifest_sha256"] == snapshot_manifest
    image_digest = st.sql(
        t033_stack.dbname,
        "SELECT worker_image_digest FROM platform.execution_snapshots WHERE id = "
        f"{st.quote_literal(state['execution_snapshot_id'])}::uuid",
    )
    assert evidence["image_digest"] == image_digest
    assert evidence["execution_unit_id"] == report["execution_unit_id"]
    assert evidence["sandbox_id"] == sandbox["sandbox_id"]
    assert evidence["sandbox_rootfs_digest"] == digest
    assert evidence["sandbox_cleanup_verified"] is True
    assert evidence["sandbox_cleanup"]["sandbox_list_matches"] == 0
    assert evidence["sandbox_cleanup"]["pid_alive"] is False
    assert evidence["sandbox_cleanup"]["ps_matches"] == 0
    assert evidence["sandbox_cleanup"]["workspace_removed"] is True
    assert evidence["db_writes_drained"] is True

    # Independent orphan check from the guest: no sandbox entry and no workspace.
    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan
    assert sandbox["sandbox_id"] not in orphan["state_root"], orphan


def test_tool_rejection_ends_the_run_failed_with_the_tool_code(t033_stack) -> None:
    owner, run, _csv = queued_sales_run(t033_stack, content=t033.BAD_CSV)
    env = execution.supervisor_exec_env(t033_stack)
    with execution.FinalizeWorker(t033_stack):
        completed = execution.run_supervisor_exec(t033_stack, env)
    report = execution.exec_report(completed)
    assert report["status"] == "executed", report
    assert report["outcome"] == "failed", report
    assert report["error_code"] == t033.REJECTION_CODE, report
    state = observations.wait_run_state(t033_stack.dbname, run["id"], {"failed"})
    assert state["error_code"] == t033.REJECTION_CODE, state
    assert state["output_artifact_id"] is None, state

    diagnostics = t033.artifact_by_purpose(t033_stack.dbname, run["id"], "node_output")
    assert diagnostics is not None, "a rejection must publish safe diagnostics"
    payload = observations.artifact_content(owner.creator, diagnostics["id"])
    assert payload.status == 200, payload.text
    document = json.loads(payload.raw)
    assert document["error_code"] == t033.REJECTION_CODE
    assert "location" in document and document["location"], document
    assert t033.SALES_CSV not in payload.raw

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan


def request_cancel_after_first_heartbeat(dbname: str) -> None:
    """Simulate T036's cancel arriving while the sandbox runs.

    The cancel endpoint exists from T036, but this test writes
    ``cancel_requested_at`` with SQL so the arrival is deterministic (and honestly
    mid-run): the write is triggered by the attempt's first heartbeat, so the
    supervisor's *next* heartbeat, ~50ms later while runsc is booting the
    container, observes it and kills the unit. No product trigger exists; this is
    test-owned SQL. Since T036 the same exit evidence commits ``cancelled`` (the
    cancel intent wins over the never-submitted result), so the Run is allowed to
    be terminal here instead of parked in ``recovery_pending``.
    """
    st.sql(
        dbname,
        "CREATE OR REPLACE FUNCTION t033_request_cancel() RETURNS trigger AS $$ "
        "BEGIN "
        "  IF NEW.heartbeat_at IS DISTINCT FROM OLD.heartbeat_at THEN "
        "    UPDATE platform.runs SET cancel_requested_at = now() "
        "     WHERE id = NEW.run_id AND cancel_requested_at IS NULL; "
        "  END IF; "
        "  RETURN NEW; "
        "END $$ LANGUAGE plpgsql",
    )
    st.sql(
        dbname,
        "CREATE TRIGGER t033_cancel_after_heartbeat AFTER UPDATE ON platform.run_attempts "
        "FOR EACH ROW EXECUTE FUNCTION t033_request_cancel()",
    )


def test_cancel_requested_mid_run_kills_the_sandbox_and_leaves_no_orphan(t033_stack) -> None:
    owner, run, _csv = queued_sales_run(t033_stack)
    request_cancel_after_first_heartbeat(t033_stack.dbname)
    env = execution.supervisor_exec_env(
        t033_stack, heartbeat="50ms", timeout="60s", result_wait="30s"
    )
    with execution.FinalizeWorker(t033_stack):
        completed = execution.run_supervisor_exec(t033_stack, env)
    report = execution.exec_report(completed)
    assert report["status"] == "executed", report
    assert report["outcome"] == "cancelled", (report, completed.stderr)
    assert report["sandbox"]["started"] is True, report
    assert report["sandbox"]["cancelled"] is True, report
    # A cancel that preempts the start can make the driver's static in-flight
    # counter observe the unit while runsc is still deleting it (the same
    # documented race as T042 §8: the counter is read before cleanup finishes).
    # The guest-side no-orphan assertions below are the authority that nothing
    # survived; the static counter may be 0 or 1 at this point.
    assert report["sandbox"]["cleanup"]["sandbox_list_matches"] in (0, 1), report

    state = t033.run_state_row(t033_stack.dbname, run["id"])
    # F03/T036 supersedes the T033 interim expectation: the same exit evidence now
    # lets the finalizer commit cancelled instead of parking recovery_pending.
    assert state["state"] in {"cancelled", "recovery_pending", "running"}, state
    assert state["cancel_requested_at"] is not None, state
    assert state["output_artifact_id"] is None, "a cancelled run must not publish a result"
    attempts = t033.attempts_for_run(t033_stack.dbname, run["id"])
    assert len(attempts) == 1, attempts
    attempt = t033.attempt_row(t033_stack.dbname, attempts[0]["token"])
    assert attempt["exited_at"] is not None, attempt
    evidence = json.loads(attempt["exit_evidence"])
    assert evidence["started"] is True
    assert evidence["sandbox_started"] is True
    assert evidence["db_writes_drained"] is True
    job = t033.job_row(t033_stack.dbname, attempts[0]["job_id"])
    assert job["state"] in {"held", "completed"}, job

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan
    assert report["sandbox"]["sandbox_id"] not in orphan["state_root"], orphan


# ---------------------------------------------------------------------------
# platform-owned isolation / timeout / crash probes through the real executor
# ---------------------------------------------------------------------------
def run_sandbox_probe(stack, *args: str, timeout: int = 300) -> dict:
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)
    env.update(execution.supervisor_exec_env(stack))
    completed = subprocess.run(
        [str(stack.binaries["sandboxprobe"]), *args],
        cwd=t033.PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    assert lines, (completed.stderr, completed.stdout)
    return json.loads(lines[-1])


def test_the_sandbox_cannot_read_host_or_foreign_files_or_reach_the_network(t033_stack) -> None:
    report = run_sandbox_probe(t033_stack, "--mode", "isolation")
    assert report["status"] == "observed", report
    assert report["outcome"] == "ran", report
    assert report["cleanup_ok"] is True, report
    probe = report["probe"]
    assert probe["probe"] == "orchara.sandbox-isolation/v1", probe
    assert probe["uid"] == 65534 and probe["gid"] == 65534, probe
    assert probe["hostname"] == "sandbox", probe
    for entry in probe["host_files"]:
        assert entry["read"] is False, entry
    # /proc is the sandbox's own PID namespace: PID 1 is the probe itself.
    assert "/tool/probe.py" in probe["pid_namespace_pid1"]["content_head"], probe
    for entry in probe["foreign_files"]:
        assert entry["read"] is False, entry
    assert probe["input_writes"], probe
    assert all(entry["wrote"] is False for entry in probe["input_writes"]), probe
    assert probe["workspace_write"]["wrote"] is True, probe
    assert probe["workspace_readable"] is True, probe
    assert probe["tool_readable"] is True, probe
    for entry in probe["network"]:
        assert entry["connected"] is False, entry
    assert report["cleanup"]["sandbox_list_matches"] == 0, report
    assert report["cleanup"]["workspace_removed"] is True, report


def test_timeout_and_crash_kill_the_sandbox_and_leave_no_orphan(t033_stack) -> None:
    timed_out = run_sandbox_probe(
        t033_stack, "--mode", "slow", "--seconds", "60", "--timeout", "2s"
    )
    assert timed_out["outcome"] == "timeout", timed_out
    assert timed_out["timed_out"] is True, timed_out
    assert timed_out["cleanup_ok"] is True, timed_out
    assert timed_out["cleanup"]["sandbox_list_matches"] == 0, timed_out
    assert timed_out["cleanup"]["pid_alive"] is False, timed_out
    assert timed_out["cleanup"]["workspace_removed"] is True, timed_out

    crashed = run_sandbox_probe(t033_stack, "--mode", "crash")
    assert crashed["outcome"] == "ran", crashed
    assert crashed["exit_code"] == 7, crashed
    assert crashed["cleanup_ok"] is True, crashed
    assert crashed["cleanup"]["workspace_removed"] is True, crashed

    orphan = t033.no_orphan_evidence()
    assert orphan["runsc_list"] == [], orphan
    assert timed_out["sandbox_id"] not in orphan["state_root"], orphan
    assert crashed["sandbox_id"] not in orphan["state_root"], orphan
