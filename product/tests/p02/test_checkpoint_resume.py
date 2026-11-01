"""P02/T005 acceptance: an interrupted run continues in a new process.

Every step below runs the probe as a *separate OS process*, because "resume in a
new process" is the property under test. The runtime steps must execute as the
restricted `orchara_worker` role, not as the schema owner — otherwise the
privilege guards would be testing an identity the run never uses.

Requires the isolated development cluster (`python tools/pg_dev.py init`) and the
locked probe environment. No credential is written in this file: the password
comes from `ORCHARA_PG_PASSWORD` (or the git-ignored `product/.env.local`).
"""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402

PROBE = PRODUCT_ROOT / "probes" / "p02_checkpoint" / "checkpoint_probe.py"
VENV_PYTHON = PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
)
RUNTIME_ROLE = "orchara_worker"


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
    if not pg_dev.is_running():
        pytest.skip("development cluster is not running (run tools/pg_dev.py init)")
    _run("init")
    return _run("setup-checkpointer")


@pytest.fixture()
def thread_id() -> str:
    return f"t-{uuid.uuid4().hex[:10]}"


def test_run_stops_at_the_approval_boundary(probe_env, thread_id: str) -> None:
    result = _run("run", "--thread", thread_id, "--owner", "alice")
    assert result["interrupted"] is True
    assert result["pending_nodes"] == ["approve"], "the run must stop before the approval node"
    assert result["stage"] == "prepared"


def test_runtime_executes_as_the_restricted_role(probe_env, thread_id: str) -> None:
    """The identity the guards protect must be the identity that actually runs."""
    result = _run("run", "--thread", thread_id, "--owner", "alice")
    assert result["runtime_role"] == RUNTIME_ROLE, (
        f"the run executed as {result['runtime_role']}, so the DDL/privilege guards "
        "would be testing an identity the runtime never uses"
    )
    assert result["search_path"].startswith("checkpoint")


def test_state_is_readable_from_another_process(probe_env, thread_id: str) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    state = _run("read", "--thread", thread_id, "--owner", "alice")

    assert state["values"]["stage"] == "prepared"
    assert state["values"]["amount"] == "120.50"
    assert state["values"]["operation_key"] == f"op-{thread_id}"
    assert state["pending_nodes"] == ["approve"]
    assert state["runtime_role"] == RUNTIME_ROLE


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
    assert resumed["runtime_role"] == RUNTIME_ROLE, "the approval write must run as the runtime role"


def test_replaying_the_logical_operation_does_not_create_a_second_row(
    probe_env, thread_id: str
) -> None:
    _run("run", "--thread", thread_id, "--owner", "alice")
    _run("resume", "--thread", thread_id, "--owner", "alice", "--decision", "approve")

    replayed = _run(
        "resume", "--thread", thread_id, "--owner", "alice", "--decision", "approve",
        expect_success=False,
    )
    assert replayed["error"] == "nothing_to_resume"

    replayed_write = _run("replay", "--thread", thread_id, "--owner", "alice", "--times", "3")
    assert replayed_write["attempts"] == 3
    assert replayed_write["runtime_role"] == RUNTIME_ROLE
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
    assert "threads" in results["cross_module_write"]["detail"], (
        "the cross-module guard must target another module's table, not the runtime's own outbox"
    )


def test_runtime_refuses_a_search_path_that_cannot_see_the_checkpointer(probe_env) -> None:
    """The guard is in the runtime path, not a side calculation in the probe."""
    healthy = _run("runtime-check")
    assert healthy["resolved_table"] is not None
    assert healthy["runtime_role"] == RUNTIME_ROLE

    broken = _run("runtime-check", "--schema", "public", expect_success=False)
    assert broken["error"] == "wrong_search_path"
    assert broken["runtime_role"] == RUNTIME_ROLE
