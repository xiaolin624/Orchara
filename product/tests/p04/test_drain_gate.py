"""P04/T016: resume only when process, sandbox and writes have all drained.

The unit tests pin the decision table; the integration tests prove the same
decision against a real process in the guest and real rows in PostgreSQL.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "p04_drain"
sys.path.insert(0, str(PROBE_ROOT))
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))

import drain_gate as gate  # noqa: E402


# --------------------------------------------------------------------------
# decision table
# --------------------------------------------------------------------------
def _evidence(observed: int | None, recorded: int = 100, sandbox: bool = False, writes: int = 0, idle: int = 0):
    return (
        gate.ProcessEvidence(pid=42, recorded_start_ticks=recorded, observed_start_ticks=observed),
        gate.SandboxEvidence(sandbox_id="sbx", present=sandbox),
        gate.DbEvidence(run_id="R1", pending_writes=writes, idle_in_transaction=idle),
    )


def test_all_clear_allows_the_next_attempt() -> None:
    decision = gate.decide(*_evidence(observed=None))
    assert decision.decision == "resume"
    assert decision.reasons == []


def test_running_process_blocks_recovery() -> None:
    decision = gate.decide(*_evidence(observed=100))
    assert decision.decision == "keep_pending"
    assert any("still running" in reason for reason in decision.reasons)


def test_exited_process_with_writes_in_flight_still_waits() -> None:
    """The ticket's core case: process gone, but the database has not drained."""
    decision = gate.decide(*_evidence(observed=None, writes=1))
    assert decision.decision == "keep_pending"
    assert any("in flight" in reason for reason in decision.reasons)


def test_exited_process_with_idle_transaction_still_waits() -> None:
    decision = gate.decide(*_evidence(observed=None, idle=2))
    assert decision.decision == "keep_pending"
    assert any("idle in transaction" in reason for reason in decision.reasons)


def test_live_sandbox_blocks_recovery() -> None:
    decision = gate.decide(*_evidence(observed=None, sandbox=True))
    assert decision.decision == "keep_pending"
    assert any("sandbox" in reason for reason in decision.reasons)


def test_pid_reuse_is_not_treated_as_a_live_process() -> None:
    """A reused PID must not block recovery forever, and must not be killed."""
    process, sandbox, db = _evidence(observed=999)
    assert process.verdict() == "pid_reused"

    decision = gate.decide(process, sandbox, db)
    assert decision.decision == "resume", decision.reasons
    assert decision.evidence["process"]["verdict"] == "pid_reused"


def test_every_blocker_is_reported_not_just_the_first() -> None:
    decision = gate.decide(*_evidence(observed=100, sandbox=True, writes=3, idle=1))
    assert decision.decision == "keep_pending"
    assert len(decision.reasons) == 4, decision.reasons


def test_unknown_pid_is_treated_as_exited() -> None:
    assert gate.read_start_ticks(999_999_999) is None


# --------------------------------------------------------------------------
# integration: real process, real database
# --------------------------------------------------------------------------
def _guest_ready() -> bool:
    if shutil.which("wsl") is None:
        return False
    try:
        completed = gate._run_wsl("echo ok", timeout=60)
    except Exception:  # noqa: BLE001
        return False
    return completed.returncode == 0


def _db_ready() -> bool:
    try:
        gate.collect_db("probe-check")
    except Exception:  # noqa: BLE001
        return False
    return True


integration = pytest.mark.skipif(
    not (_guest_ready() and _db_ready()), reason="guest or database unavailable"
)


def _psql(sql: str) -> None:
    import os

    env = dict(os.environ)
    env["PGPASSWORD"] = gate.pg_dev.load_secret()
    subprocess.run(
        [
            gate.pg_dev._tool("psql"),
            "-h", "127.0.0.1",
            "-p", str(gate.pg_dev.DEFAULT_PORT),
            "-U", gate.pg_dev.DEFAULT_USER,
            "-d", gate.pg_dev.DEFAULT_DB,
            "-v", "ON_ERROR_STOP=1",
            "-c", sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )


def _start_process() -> dict:
    completed = subprocess.run(
        [sys.executable, str(PROBE_ROOT / "drain_gate.py"), "start-process", "--seconds", "90"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def _kill(pid: int) -> None:
    gate._run_wsl(f"kill -9 {pid} 2>/dev/null || true", timeout=60)


@integration
def test_real_process_writes_and_sandbox_gate_the_resume() -> None:
    run_id = "R-integration"
    _psql(f"DELETE FROM app.pending_writes WHERE run_id = '{run_id}'")

    info = _start_process()
    pid, ticks = info["pid"], info["start_ticks"]
    try:
        assert gate.check(pid, ticks, "no-such-sandbox", run_id).decision == "keep_pending"

        _psql(f"INSERT INTO app.pending_writes (run_id, sequence) VALUES ('{run_id}', 1)")
        _kill(pid)
        time.sleep(0.5)

        decision = gate.check(pid, ticks, "no-such-sandbox", run_id)
        assert decision.decision == "keep_pending"
        assert any("in flight" in reason for reason in decision.reasons)

        _psql(f"DELETE FROM app.pending_writes WHERE run_id = '{run_id}'")
        assert gate.check(pid, ticks, "no-such-sandbox", run_id).decision == "resume"
    finally:
        _psql(f"DELETE FROM app.pending_writes WHERE run_id = '{run_id}'")
        _kill(pid)
