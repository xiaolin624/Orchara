"""T022 acceptance: PostgreSQL polling alone advances work (no wakeup bus).

This batch has no Redis client and no outbox wakeup: the maintenance process
polls the claim endpoint, which scans PostgreSQL. These tests start the real
maintenance process *before* any work exists, enqueue work afterwards, and wait
for the database to move -- so a lost or absent wakeup cannot lose a job. Redis
integration itself is recorded as ``not_run`` in the report because no Redis
instance exists on this machine.
"""

from __future__ import annotations

import os
import subprocess
import time

from support import f00b
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def _no_wakeup_bus_configured(env: dict[str, str]) -> bool:
    keys = [key for key in env if "REDIS" in key.upper()]
    return keys == []


def _start_poller(stack) -> subprocess.Popen:
    env = f00b.maintenance_env(stack)
    assert _no_wakeup_bus_configured(env)
    assert _no_wakeup_bus_configured(dict(os.environ))
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            "--kinds",
            "artifact_finalize",
            "--poll-interval",
            "50ms",
            "--worker-id",
            "t022-poller",
        ],
        cwd=f00b.PRODUCT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    # Bounded startup check: the loop's first claim is an empty 204 we cannot
    # observe from outside, so the test only proves the process is still running
    # before it enqueues work.
    time.sleep(0.2)
    if process.poll() is not None:
        _out, err = process.communicate()
        raise AssertionError(f"maintenance exited during startup:\n{err}")
    return process


def _prepare_artifact(stack) -> f00b.SeededArtifact:
    owner = f00b.principal_id_for_login(stack.dbname)
    return f00b.seed_artifact(stack.dbname, stack.object_dir, owner)


def _enqueue(stack, artifact, suffix: str) -> dict:
    owner = f00b.principal_id_for_login(stack.dbname)
    return f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key=f"poll-{suffix}:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )


def test_work_created_after_the_poller_started_is_picked_up(t022_stack) -> None:
    process = _start_poller(t022_stack)
    try:
        first = _prepare_artifact(t022_stack)
        first_job = _enqueue(t022_stack, first, "one")
        f00b.wait_until(
            lambda: f00b.job_row(t022_stack.dbname, first_job["id"])["state"] == "succeeded",
            timeout=15.0,
            what="the first job to be claimed by PG polling",
        )
        first_row = f00b.artifact_row(t022_stack.dbname, first.id)
        assert first_row["state"] == "ready"

        # The poller keeps polling after an empty claim; a second job enqueued
        # while it is idle must still be picked up.
        second = _prepare_artifact(t022_stack)
        second_job = _enqueue(t022_stack, second, "two")
        f00b.wait_until(
            lambda: f00b.job_row(t022_stack.dbname, second_job["id"])["state"] == "succeeded",
            timeout=15.0,
            what="the second job to be claimed by PG polling",
        )
    finally:
        process.terminate()
        try:
            _stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            process.kill()
            _stdout, stderr = process.communicate(timeout=5)
    assert "run: worker=t022-poller" in stderr, stderr
    # The database is the authority: both jobs and artifacts reached their
    # terminal/ready state while the only transport was polling. (Windows
    # termination does not guarantee the final summary line, so stdout is not
    # asserted here.)
    assert f00b.job_row(t022_stack.dbname, second_job["id"])["state"] == "succeeded"
    assert f00b.artifact_row(t022_stack.dbname, second.id)["state"] == "ready"
