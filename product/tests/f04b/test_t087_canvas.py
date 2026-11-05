"""F04B/T087: the committed canvas artifact carries the approval node surface.

No browser is driven (the same boundary every canvas ticket records): the pure
modules execute under Node and the committed bundle is inspected/syntax-checked.
``npm`` is never run by the suite; the bundle is a committed build artifact, and
``test_canvas_f04b_assets.py`` owns the stale-build comparison.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "t087_canvas.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the canvas pure-module test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_javascript_and_ships_the_approval_surface(
    node_binary: str,
) -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    completed = subprocess.run(
        [node_binary, "--check", str(bundle)],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    text = bundle.read_text(encoding="utf-8")
    for marker in (
        "人工审批",
        "config.title",
        "config.expires_seconds",
        "inputs.proposal.literal",
        "approval.boundary",
        "不授权后续外部写",
    ):
        assert marker in text, marker
    css = (ASSETS / "canvas.css").read_text(encoding="utf-8")
    assert ".orchara-node-approval" in css


def test_the_approval_canvas_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True, text=True, cwd=PRODUCT, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.count("ok ") >= 6, completed.stdout
