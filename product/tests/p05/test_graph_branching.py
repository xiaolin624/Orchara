"""P05/T011: branch and join semantics in the real graph engine.

The probe needs LangGraph, which lives in the locked probe environment, so the
test drives it as a subprocess exactly like the P02 probe tests do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "p05_graph" / "graph_probe.py"
VENV_PYTHON = PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
)


@pytest.fixture(scope="module")
def report() -> dict:
    if not VENV_PYTHON.exists():
        pytest.skip("locked probe environment is missing")
    completed = subprocess.run(
        [str(VENV_PYTHON), str(PROBE)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
    )
    if completed.returncode != 0:
        pytest.fail(f"probe failed: {completed.stdout}\n{completed.stderr}")
    return json.loads(completed.stdout)


def test_probe_reports_every_assertion_true(report: dict) -> None:
    failed = [name for name, value in report["assertions"].items() if value is not True]
    assert not failed, f"failed assertions: {failed}"


def test_unselected_branch_is_skipped(report: dict) -> None:
    left = report["cases"]["route_left"]
    right = report["cases"]["route_right"]

    assert left["skipped"] == ["right"], left
    assert right["skipped"] == ["left"], right
    # and the skipped node really did not run
    assert "right" not in left["executed"]
    assert "left" not in right["executed"]


def test_join_runs_exactly_once_without_deadlock(report: dict) -> None:
    for case in ("route_left", "route_right", "parallel", "nested", "failing_branch"):
        assert report["cases"][case]["join_count"] == 1, case
        assert report["cases"][case]["executed"].count("join") == 1, case


def test_parallel_branches_both_run(report: dict) -> None:
    executed = set(report["cases"]["parallel"]["executed"])
    assert {"left", "right", "join"} <= executed, executed


def test_nested_selection_respects_the_inner_branch(report: dict) -> None:
    executed = report["cases"]["nested"]["executed"]
    assert "outer_a" in executed
    assert "inner_right" in executed
    assert "inner_left" not in executed


def test_a_failing_branch_still_converges(report: dict) -> None:
    case = report["cases"]["failing_branch"]
    assert case["failed"], "the failure must be recorded, not swallowed"
    assert case["join_count"] == 1, "the join must still run exactly once"


def test_structural_problems_are_rejected_before_execution(report: dict) -> None:
    cross_region = report["rejections"]["cross_region_edge"]
    orphan = report["rejections"]["no_active_source"]

    assert any("cross_region_edge:a->b" in reason for reason in cross_region), cross_region
    assert any(reason.startswith("no_active_source:") for reason in orphan), orphan


def test_engine_is_the_real_one(report: dict) -> None:
    """Guard against the probe quietly replacing LangGraph with a stub."""
    assert report["engine"] == "langgraph"
    assert report["probe"] == "p05-graph-branching"
