"""F04B/T086: the committed canvas artifact and the model pure-module Node tests.

No browser is driven here. These checks prove the page receives a real, valid
bundle that ships the model palette/panel, and that the pure DSL/export/source
modules execute under Node with the model node's closed config. The response
schema is the node's output_schema: the bundle must not expose a
``response_schema`` config field.

npm is never run by the test suite; the bundle is a committed artifact. The
optional rebuild check catches a stale committed asset when ``node_modules``
exists and restores the committed bytes afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
CANVAS = PRODUCT / "apps" / "web" / "canvas"
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "canvas_model.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the canvas pure-module test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_javascript(node_binary: str) -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    completed = subprocess.run([node_binary, "--check", str(bundle)],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr


def test_the_bundle_ships_the_model_palette_and_panel() -> None:
    text = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    for marker in (
        "OrcharaCanvas",
        "模型",
        "MODEL_RESPONSE_MODES",
        "config.model_profile_version",
        "config.prompt_template",
        "config.response_mode",
        "data-insert-variable",
        "插入变量",
        "响应 Schema",
        "node.output_schema",
    ):
        assert marker in text, marker
    # The "response schema" is node.output_schema; a config field named
    # response_schema would violate the contract (M1).
    assert "config.response_schema" not in text
    css = (ASSETS / "canvas.css").read_text(encoding="utf-8")
    assert ".orchara-node-model" in css


def test_the_model_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run([node_binary, str(MODULE_TEST)],
                               capture_output=True, text=True, cwd=PRODUCT, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert "PASS 4 canvas T086 model tests" in completed.stdout


def test_the_committed_bundle_matches_a_fresh_build(node_binary: str) -> None:
    """Skip unless esbuild is installed; never leave a rebuilt asset behind."""
    if not (CANVAS / "node_modules").is_dir():
        pytest.skip("canvas/node_modules is absent; the rebuild check needs esbuild")
    bundle = ASSETS / "canvas.js"
    style = ASSETS / "canvas.css"
    before = {bundle: bundle.read_bytes(), style: style.read_bytes()}
    try:
        completed = subprocess.run([node_binary, "build.mjs"], cwd=CANVAS,
                                   capture_output=True, text=True, timeout=180)
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
