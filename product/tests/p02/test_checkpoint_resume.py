"""P02/T005 acceptance: an interrupted run continues in a new process.

Every step below runs the probe as a *separate OS process*, because "resume in a
new process" is the property under test. Requires the isolated development
cluster (``python tools/pg_dev.py init``) and the locked probe environment.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "p02_checkpoint" / "checkpoint_probe.py"
VENV_PYTHON = PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
)
DSN = os.environ.get(
    "ORCHARA_PG_DSN",
    "postgresql://orchara_owner:orchara_dev_only@127.0.0.1:55432/orchara_probe?sslmode=disable",
)


def _run(*args: str, expect_success: bool = True) -> dict:
    completed = subprocess.run(
        [str(VENV_PYTHON), str(PROBE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if expect_success and completed.returncode != 0:
        pytest.fail(f"probe {args} failed ({completed.returncode}):\n{completed.stderr}")
    if not completed.stdout.strip():
        pytest.fail(f"probe {args} produced no JSON; stderr={completed.stderr}")
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probe_env() -> dict:
    if not VENV_PYTHON.exists():
        pytest.skip("locked probe environment is missing")
    try:
        import psycopg  # noqa: F401
    except ImportError:
        pass
    try:
        _run("init")
        setup = _run("setup-checkpointer")
    except AssertionError as exc:  # cluster not reachable
        pytest.skip(f"development cluster unavailable: {exc}")
    return setup


@pytest.fixture()
def thread_id() -> str:
    return f"t-{uuid.uuid4().hex[:10]}"


def test_run_stops_at_the_approval_boundary(probe_env, thread_id: str) -> None:
    result = _run("run", "--thread", thread_id, "--owner", "alice")
    assert result["interrupted"] is True
    assert result["pending_nodes"] == ["approve"], "the run must stop before the approval node"
    assert result["stage"] == "prepared"


def test_state_is_readable_from_another_process(probe_env, thread_id: str) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    state = _run("read", "--thread", thread_id, "--owner", "alice")

    assert state["values"]["stage"] == "prepared"
    assert state["values"]["amount"] == "120.50"
    assert state["values"]["operation_key"] == f"op-{thread_id}"
    assert state["pending_nodes"] == ["approve"]


def test_resume_in_a_new_process_completes_and_keeps_the_decision(
    probe_env, thread_id: str
) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    resumed = _run("resume", "--thread", thread_id, "--owner", "alice", "--decision", "approve")

    assert resumed["resumed_from_pending"] == ["approve"]
    assert resumed["stage"] == "done"
    assert resumed["decision"] == "approve", "the decision made after the interrupt must persist"
    assert resumed["finished"] is True
    assert resumed["operation_rows"] == 1


def test_replaying_the_logical_operation_does_not_create_a_second_row(
    probe_env, thread_id: str
) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    _run("resume", "--thread", thread_id, "--owner", "alice", "--decision", "approve")

    # A second resume has nothing to resume: the run is already terminal.
    replayed = _run(
        "resume", "--thread", thread_id, "--owner", "alice", "--decision", "approve",
        expect_success=False,
    )
    assert replayed["error"] == "nothing_to_resume"

    # And the write itself is idempotent at the storage layer.
    replayed_write = _run("replay", "--thread", thread_id, "--owner", "alice", "--times", "3")
    assert replayed_write["attempts"] == 3
    assert replayed_write["operation_rows"] == 1, (
        f"replay created {replayed_write['operation_rows']} rows instead of 1"
    )


def test_another_owner_cannot_read_the_thread(probe_env, thread_id: str) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    refused = _run("read", "--thread", thread_id, "--owner", "bob", expect_success=False)
    assert refused["error"] == "thread_not_owned"


def test_runtime_role_cannot_run_ddl_or_write_other_modules_tables(probe_env) -> None:
    results = _run("guards")
    assert results["ddl"]["blocked"] is True, results["ddl"]
    assert results["cross_module_write"]["blocked"] is True, results["cross_module_write"]


def test_wrong_search_path_is_detected(probe_env) -> None:
    results = _run("guards")["search_path"]
    assert results["correct_search_path_resolves"] is True
    assert results["wrong_search_path_resolves"] is False, (
        "a wrong search_path must not resolve the checkpointer tables"
    )
    assert results["detected"] is True
