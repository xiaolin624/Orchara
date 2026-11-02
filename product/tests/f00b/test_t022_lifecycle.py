"""T022 acceptance: one background job end to end through the real processes.

The admin CLI creates the job, the compiled maintenance binary claims it over the
real internal mTLS API, executes the compiled artifact_finalize handler against
the real object store, submits its result, and the server-side guard activates
the artifact. Every assertion reads the real database, the real object store or
the processes' real output; nothing is stubbed.
"""

from __future__ import annotations

import json
import subprocess

from support import f00b
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def test_a_finalize_job_runs_end_to_end(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"the real bytes that must be sealed\n"
    artifact = f00b.seed_artifact(t022_stack.dbname, t022_stack.object_dir, owner, content=content)
    payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="finalize:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )

    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    report = job_reports[0]
    assert report["job_id"] == payload["id"]
    assert report["kind"] == "artifact_finalize"
    assert report["result"] == "succeeded", report
    assert report["state"] == "succeeded"
    assert report["heartbeats"] >= 1, report

    summary = f00b.summary_of(reports)
    assert summary["status"] == "ok"
    assert summary["claimed"] == 1
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0

    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "succeeded"
    assert job["error_code"] is None
    assert job["failure_class"] is None
    assert job["attempt_count"] == 1
    assert job["finished_at"] is not None
    assert job["result_event_id"] is not None
    assert job["lease_token"] is None
    assert job["lease_deadline"] is None

    activated = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert activated["state"] == "ready"
    assert activated["deleted_at"] is None
    assert activated["sha256"] == artifact.expected_sha256
    assert activated["expected_sha256"] == artifact.expected_sha256
    assert int(activated["revision"]) == 2  # activation is a state change
    sealed = (t022_stack.object_dir / artifact.object_key).read_bytes()
    assert sealed == content
    # The staging object is left to the orphan sweep (T026), not silently deleted
    # by the handler.
    assert artifact.staging_path(t022_stack.object_dir).exists()


def test_an_empty_queue_exits_cleanly_with_a_summary(t022_stack) -> None:
    completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    assert f00b.job_reports(reports) == [], reports
    summary = f00b.summary_of(reports)
    assert summary["claimed"] == 0
    assert summary["status"] == "ok"
    # Exactly one JSON line on stdout and nothing else.
    assert len([line for line in completed.stdout.splitlines() if line.strip()]) == 1


def test_the_no_argument_connectivity_pass_still_works(t022_stack) -> None:
    env = st.admin_env(None)
    env["ORCHARA_MAINTENANCE_DSN"] = t022_stack.dsn
    completed = subprocess.run(
        [str(t022_stack.binaries["maintenance"])],
        cwd=f00b.PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    report = json.loads(completed.stdout.strip())
    assert report["status"] == "ok"
    assert report["database"] == t022_stack.dbname
    assert report["role"] == "orchara_owner"


def test_a_whitelisted_kind_without_a_handler_is_not_claimed(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    target = f00b.new_uuid()
    payload = f00b.enqueue_ok(
        t022_stack, kind="catalog_index", target_id=target, owner_id=owner,
        dedup_key="index:" + target,
    )
    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    assert f00b.job_reports(reports) == []
    assert f00b.summary_of(reports)["claimed"] == 0
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["state"] == "ready"
    assert row["attempt_count"] == 0
    assert row["lease_token"] is None
