"""F00/T008: the supervisor parks a dead worker's runs and never restarts them.

The supervisor is the third module T008's slice names ("API/maintenance/
supervisor 各自承担职责"). These tests drive the real binary against the isolated
development cluster, so the advisory lease and the SQL are both exercised.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import toolchain  # noqa: E402
import pg_dev  # noqa: E402

GO = toolchain.go_binary()
MIGRATION = PRODUCT_ROOT / "db" / "migrations" / "0001_worker_lease.sql"
LEASE_KEY = 0x0C4A2E5A  # must match internal/supervisor.LeaseKey


def _psql_execute(statements: str) -> None:
    """Run SQL against the development cluster through psql."""
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    completed = subprocess.run(
        [
            pg_dev._tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            pg_dev.DEFAULT_DB,
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            statements,
        ],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.fail(f"psql failed: {completed.stderr}")


@pytest.fixture(scope="module")
def supervisor_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not Path(GO).exists():
        pytest.skip("Go toolchain not available")
    if not pg_dev.is_running():
        pytest.skip("development cluster is not running")

    _psql_execute(MIGRATION.read_text(encoding="utf-8"))

    out = tmp_path_factory.mktemp("supervisor") / (
        "supervisor.exe" if sys.platform == "win32" else "supervisor"
    )
    completed = subprocess.run(
        [GO, "build", "-o", str(out), "./cmd/supervisor"],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if completed.returncode != 0:
        pytest.fail(f"go build failed:\n{completed.stdout}\n{completed.stderr}")
    return out


def _run_supervisor(binary: Path, *, stale_seconds: int = 1) -> dict:
    env = dict(os.environ)
    env["ORCHARA_SUPERVISOR_DSN"] = pg_dev.dsn()
    env["ORCHARA_STALE_AFTER_SECONDS"] = str(stale_seconds)
    completed = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.fail(f"supervisor failed: {completed.stdout} {completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _seed(dead: bool) -> tuple[str, str]:
    """Create a worker (stale or live) owning one running run."""
    suffix = uuid.uuid4().hex[:8]
    worker = f"w-{suffix}"
    run = f"r-{suffix}"
    # 0 seconds ago = live, 1 hour ago = stale.
    heartbeat = "now() - interval '1 hour'" if dead else "now()"
    _psql_execute(
        f"""
        INSERT INTO app.workers (worker_id, started_at, heartbeat_at)
        VALUES ('{worker}', {heartbeat}, {heartbeat});
        INSERT INTO app.runs (run_id, state, worker_id, heartbeat_at)
        VALUES ('{run}', 'running', '{worker}', {heartbeat});
        """
    )
    return worker, run


def _psql_scalar(sql: str) -> str:
    """Run a single-value query through psql (no Python driver needed)."""
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    completed = subprocess.run(
        [
            pg_dev._tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            pg_dev.DEFAULT_DB,
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0:
        pytest.fail(f"psql failed: {completed.stderr}")
    return completed.stdout.strip()


def _run_state(run_id: str) -> str:
    state = _psql_scalar(f"SELECT state FROM app.runs WHERE run_id = '{run_id}'")
    assert state, f"run {run_id} disappeared"
    return state


def test_dead_workers_runs_are_parked_not_restarted(supervisor_binary: Path) -> None:
    worker, run = _seed(dead=True)
    result = _run_supervisor(supervisor_binary)

    assert worker in result["stale_workers"], result
    assert result["runs_parked"] >= 1
    assert _run_state(run) == "recovery_pending", (
        "a dead worker's run must be parked, not left running and not re-executed"
    )
    # The result exposes no restart verb or flag at all: parking is the only verb.
    assert "restarted" not in result, result


def test_live_workers_runs_are_left_alone(supervisor_binary: Path) -> None:
    worker, run = _seed(dead=False)
    _run_supervisor(supervisor_binary)

    assert _run_state(run) == "running", "a live worker's run must not be touched"
    assert worker not in _run_supervisor(supervisor_binary)["stale_workers"]


def test_a_second_supervisor_skips_while_the_lease_is_held(
    supervisor_binary: Path,
) -> None:
    """The advisory lock is what stops two supervisors acting at once.

    The lock is session-scoped, so it has to be held by a live session while the
    supervisor runs — a one-shot query would release it immediately.
    """
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    holder = subprocess.Popen(
        [
            pg_dev._tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            pg_dev.DEFAULT_DB,
            "-tAc",
            f"SELECT pg_try_advisory_lock({LEASE_KEY}); SELECT pg_sleep(8);",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
    )
    try:
        # Wait until the lock is actually held before racing it.
        import time

        deadline = time.time() + 10
        while time.time() < deadline:
            if _psql_scalar(
                f"SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = {LEASE_KEY}"
            ) == "1":
                break
            time.sleep(0.2)
        else:
            pytest.fail("could not hold the supervisor lease")

        result = _run_supervisor(supervisor_binary)
        assert result["skipped"] is True, result
        assert "lease" in result["skip_reason"]
    finally:
        holder.terminate()
        try:
            holder.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            holder.kill()

    # The server may take a moment to notice the session ended, so poll rather
    # than assuming the lock is gone the instant the client exits.
    import time

    deadline = time.time() + 20
    while time.time() < deadline:
        if _run_supervisor(supervisor_binary)["skipped"] is False:
            return
        time.sleep(0.5)
    pytest.fail("the lease was never released after the holding session ended")


def test_cleanly_stopped_workers_are_not_treated_as_dead(supervisor_binary: Path) -> None:
    suffix = uuid.uuid4().hex[:8]
    worker = f"w-clean-{suffix}"
    _psql_execute(
        f"""
        INSERT INTO app.workers (worker_id, started_at, heartbeat_at, stopped_at)
        VALUES ('{worker}', now() - interval '1 hour', now() - interval '1 hour', now());
        """
    )
    assert worker not in _run_supervisor(supervisor_binary)["stale_workers"]
