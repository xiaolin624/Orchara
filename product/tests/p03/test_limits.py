"""P03/T014: over-limit scripts are stopped and their resources reclaimed.

The matrix is honest about one gap: the OCI cgroup memory limit is not honoured
on this host, so an 80 MiB allocation survives a 64 MiB limit. Memory is
therefore enforced by the launcher with ``ulimit -v``, and the test asserts both
facts - the enforced path works and the un-enforced path is recorded rather than
hidden.
"""

from __future__ import annotations

import json
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
    raise AssertionError(f"case {name} missing from {list(c['case'] for c in matrix['cases'])}")


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
def test_memory_is_enforced_by_the_launcher(matrix: dict) -> None:
    case = _case(matrix, "memory_ulimit")
    assert case["exit_code"] != 0, "an over-limit allocation must fail"
    assert "out of memory" in case["tail"].lower()


@guest
def test_the_oci_cgroup_limit_is_recorded_as_not_enforced(matrix: dict) -> None:
    """Honesty check: the un-enforced path must stay visible in the evidence."""
    case = _case(matrix, "memory_oci")
    assert "survived=80000000" in case["tail"], (
        "if the cgroup limit starts working, update the report instead of leaving this assertion"
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


def test_feature_switch_is_off_by_default_and_stays_off_after_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed limit run must leave arbitrary script execution disabled.

    The flag file is redirected into tmp so running the suite cannot rewrite the
    committed evidence under reports/p03/.
    """
    monkeypatch.setattr(probe, "FEATURE_FLAG_PATH", tmp_path / "feature-flag.json")

    assert probe.arbitrary_scripts_enabled() is False
    record = probe.record_failure("timeout case exceeded its limit")
    assert record["arbitrary_scripts"] is False
    assert probe.arbitrary_scripts_enabled() is False
    # the failure is recorded, but the capability stays closed
    assert (tmp_path / "feature-flag.json").exists()
