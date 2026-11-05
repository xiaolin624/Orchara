"""F04B/T089: the committed canvas bundle and the call_app pure-module tests.

No browser is driven here. The checks prove the page receives a real, valid
bundle that carries the call_app palette/panel surfaces (fixed-version picker,
literal/input/node I/O mapping, child + parent output schema), and that the pure
call_app helpers plus the palette/export tables execute under Node.

npm is never run by the test suite; the bundle is a committed artifact. The
optional rebuild check catches a stale committed asset when `node_modules`
exists; it restores the committed bytes afterwards so the working tree stays
clean.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
CANVAS = PRODUCT / "apps" / "web" / "canvas"
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "callapp_canvas.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the call_app pure-module test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_javascript(node_binary: str) -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    completed = subprocess.run(
        [node_binary, "--check", str(bundle)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr


def test_the_bundle_ships_the_call_app_surfaces() -> None:
    text = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    for marker in (
        "call_app",
        "子应用调用",
        "固定子版本",
        "application_version",
        "callAppVersionOptions",
        "callAppInputRows",
        "callAppChildSchemas",
        "callAppProblems",
        "call_app.missing_required_input",
        "call_app.type_mismatch",
        "inputs.new",
    ):
        assert marker in text, marker


def test_the_canvas_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True,
        text=True,
        cwd=PRODUCT,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS 12 call_app canvas module tests" in completed.stdout, completed.stdout


def test_the_committed_bundle_matches_a_fresh_build(node_binary: str) -> None:
    """Skip unless esbuild is installed; never leave a rebuilt asset behind."""
    if not (CANVAS / "node_modules").is_dir():
        pytest.skip("canvas/node_modules is absent; the rebuild check needs esbuild")
    bundle = ASSETS / "canvas.js"
    style = ASSETS / "canvas.css"
    before = {bundle: bundle.read_bytes(), style: style.read_bytes()}
    try:
        completed = subprocess.run(
            [node_binary, "build.mjs"], cwd=CANVAS, capture_output=True, text=True, timeout=180
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert bundle.read_bytes() == before[bundle], (
            "apps/web/src/assets/canvas.js is stale: run `npm run build` in "
            "apps/web/canvas and commit the rebuilt asset"
        )
        assert style.read_bytes() == before[style], (
            "apps/web/src/assets/canvas.css is stale: run `npm run build` and commit it"
        )
    finally:
        for path, data in before.items():
            path.write_bytes(data)
