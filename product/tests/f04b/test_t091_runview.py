"""F04B/T091 run view: the two-approval workflow table.

The page script is the real committed asset (`apps/web/src/assets/runview.js`),
executed under Node in a `vm` context with the checked-in ten-node fixture. This
pins the T091 display additions: a waiting approval row names the pending human
decision, an approved row renders the server decision artifact, and the nested
join rows keep each source with the converged optional error.

No browser is driven (repository boundary); the DOM wiring is covered by the
existing T083 page harness, and this file proves the T091 pure-function surface.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "runview_t091.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the run-view test cannot run")
    return NODE


def test_the_runview_asset_parses_and_carries_the_approval_surface() -> None:
    text = (ASSETS / "runview.js").read_text(encoding="utf-8")
    for marker in (
        "run-node-approval",
        "等待人工审批",
        "runViewDescribeApproval",
        "已通过",
        "RUNVIEW_APPROVAL_WAITING_TEXT",
        "node.type === 'approval'",
    ):
        assert marker in text, marker
    completed = subprocess.run(
        [NODE, "--check", str(ASSETS / "runview.js")],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr


def test_the_ten_node_run_view_pure_functions_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=PRODUCT, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS 4 runview T091 tests" in completed.stdout, completed.stdout
