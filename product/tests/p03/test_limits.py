"""P03/T014: over-limit scripts are stopped and their resources reclaimed.

Every limit is applied by the runtime from the OCI spec (rlimits, tmpfs size,
pids limit) or by the launcher (host timeout, output cap) — never inside the
script under test, because a fixture that limits itself proves nothing.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "p03_sandbox"
sys.path.insert(0, str(PROBE_ROOT))

import sandbox_probe as probe  # noqa: E402


def _guest_ready() -> bool:
    if shutil.which("wsl") is None:
        return False
    try:
        completed = probe.run_wsl("runsc --version", timeout=120)
    except Exception:  # noqa: BLE001
        return False
    return completed.returncode == 0


guest = pytest.mark.skipif(not _guest_ready(), reason="no Linux guest with a gVisor runtime")


@pytest.fixture(scope="module")
def matrix() -> dict:
    if not _guest_ready():
        pytest.skip("no Linux guest with a gVisor runtime")
    return probe.limits_matrix()


def _case(matrix: dict, name: str) -> dict:
    for case in matrix["cases"]:
        if case["case"] == name:
            return case
    raise AssertionError(f"case {name} missing from {[c['case'] for c in matrix['cases']]}")


@guest
def test_timeout_is_enforced_and_the_sandbox_is_reclaimed(matrix: dict) -> None:
    case = _case(matrix, "timeout")
    assert case["exit_code"] == 124, "the host timeout must stop the sandbox"
    # Killing the client leaves the sandbox behind; the cleanup must reclaim it.
    assert case["leftover_sandboxes_before_cleanup"] >= 1
    assert case["leftover_sandboxes_after_cleanup"] == 0


@guest
def test_disk_usage_cannot_exceed_the_tmpfs(matrix: dict) -> None:
    case = _case(matrix, "disk")
    assert "16777216" in case["tail"], f"write was not capped at 16 MiB: {case['tail']}"


@guest
def test_memory_is_enforced_by_the_runtime(matrix: dict) -> None:
    """RLIMIT_AS comes from the OCI spec, not from the script."""
    case = _case(matrix, "memory")
    assert case["exit_code"] != 0, "an over-limit allocation must fail"
    assert "out of memory" in case["tail"].lower(), case["tail"]


@guest
def test_cpu_is_enforced_by_the_runtime(matrix: dict) -> None:
    """RLIMIT_CPU kills a busy loop without any help from the script."""
    case = _case(matrix, "cpu")
    assert case["exit_code"] != 0, "a CPU-bound loop must be stopped"
    assert case["exit_code"] == 137, f"expected SIGKILL from RLIMIT_CPU, got {case['exit_code']}"


@guest
def test_output_is_capped_by_the_launcher(matrix: dict) -> None:
    case = _case(matrix, "output")
    cap = matrix["output_cap_bytes"]
    assert case["stdout_bytes"] == cap, (
        f"captured {case['stdout_bytes']} bytes, expected the {cap}-byte cap"
    )


@guest
def test_fork_storm_is_stopped(matrix: dict) -> None:
    case = _case(matrix, "fork")
    assert case["exit_code"] != 0, "a fork storm must not run to completion"
    assert case["leftover_sandboxes_after_cleanup"] == 0


@guest
def test_no_orphan_sandboxes_or_leftover_input(matrix: dict) -> None:
    assert matrix["input_removed"] is True, "approved input was not reclaimed"
    assert matrix["final_sandboxes"] == 0, f"{matrix['final_sandboxes']} sandbox(es) leaked"
    for case in matrix["cases"]:
        assert case["leftover_sandboxes_after_cleanup"] == 0, case


def test_every_limit_kind_the_ticket_names_is_covered(matrix: dict) -> None:
    """The ticket asks for CPU / memory / PID / disk / time / output."""
    covered = {case["case"] for case in matrix["cases"]}
    assert {"timeout", "disk", "memory", "cpu", "fork", "output"} <= covered


def test_feature_switch_is_off_by_default_and_only_the_deployer_can_enable_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed run must never enable arbitrary script execution."""
    monkeypatch.delenv(probe.ARBITRARY_SCRIPTS_ENV, raising=False)
    assert probe.arbitrary_scripts_enabled() is False

    record = probe.record_failure("timeout case exceeded its limit")
    assert record["arbitrary_scripts"] is False
    assert probe.arbitrary_scripts_enabled() is False

    monkeypatch.setenv(probe.ARBITRARY_SCRIPTS_ENV, "true")
    assert probe.arbitrary_scripts_enabled() is True
