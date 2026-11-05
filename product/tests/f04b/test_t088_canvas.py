"""F04B/T088: the committed canvas bundle and the retrieve pure-module tests.

No browser is driven here. These checks prove the page receives a valid committed
bundle carrying the retrieve palette/panel and that the pure dsl/sources/export
modules (the same bytes the page uses) execute under Node against a concrete
start -> retrieve -> end definition.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "canvas_t088.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the canvas pure-module test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_javascript(node_binary: str) -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    completed = subprocess.run(
        [node_binary, "--check", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr


def test_the_bundle_ships_the_retrieve_surface() -> None:
    text = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    for marker in (
        "知识检索",
        "config.knowledge_base_ids",
        "config.top_k",
        "knowledge-bases?limit=100",
        "output_schema.template",
        "retrieveOutputSchema",
    ):
        assert marker in text, marker


def test_the_retrieve_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True,
        text=True,
        cwd=PRODUCT,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS 6 canvas T088 module tests" in completed.stdout
