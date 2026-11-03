"""F04A/T040: the committed canvas artifact and the pure-module Node tests.

No browser is driven here. These checks prove the page receives a real, valid
bundle and that the pure graph/DSL/layout/error/export modules execute under
Node. npm is never run by the test suite; the bundle is a committed artifact and
also serves as the "tests can run without npm" guarantee.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
CANVAS = PRODUCT / "apps" / "web" / "canvas"
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
EDITOR = PRODUCT / "apps" / "web" / "src" / "editor.html"
MODULE_TEST = Path(__file__).resolve().parent / "canvas_modules.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the canvas pure-module test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_javascript(node_binary: str) -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    assert bundle.stat().st_size > 100_000
    completed = subprocess.run([node_binary, "--check", str(bundle)],
                               capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stderr


def test_the_bundle_exposes_the_mount_contract() -> None:
    text = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    assert "OrcharaCanvas" in text
    assert "mount" in text
    css = (ASSETS / "canvas.css").read_text(encoding="utf-8")
    assert ".orchara-canvas" in css
    assert ".canvas-stage" in css


def test_the_canvas_dependencies_are_pinned_and_locked() -> None:
    package = json.loads((CANVAS / "package.json").read_text(encoding="utf-8"))
    dependencies = package["dependencies"]
    assert set(dependencies) == {
        "vue", "@vue-flow/core", "@vue-flow/background", "@vue-flow/controls",
    }
    for name, version in dependencies.items():
        assert re.match(r"^\d+\.\d+\.\d+$", version), (name, version)
    assert package["devDependencies"]["esbuild"].count(".") == 2
    lock = json.loads((CANVAS / "package-lock.json").read_text(encoding="utf-8"))
    packages = lock["packages"]
    for name in dependencies:
        assert "node_modules/" + name in packages, name
    assert (CANVAS / "build.mjs").is_file()
    assert "canvas.js" in (CANVAS / "build.mjs").read_text(encoding="utf-8")


def test_the_editor_page_hosts_the_canvas_and_keeps_the_save_path() -> None:
    html = EDITOR.read_text(encoding="utf-8")
    for marker in (
        'id="canvas-root"',
        'id="draft-form"',
        'id="draft-definition"',
        'id="draft-layout"',
        'id="draft-save"',
        'id="editor-status"',
        'id="conflict-panel"',
        'id="conflict-reload"',
        'id="conflict-download"',
        'id="debug-entry"',
        "/assets/canvas.js",
        "/assets/canvas.css",
        "/assets/editor.js",
        "/assets/app.js",
    ):
        assert marker in html, marker
    assert 'method="post"' in html
    assert 'id="json-view"' in html
    assert not re.search(r"<script(?![^>]*\bsrc\s*=)[^>]*>", html)


def test_the_canvas_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run([node_binary, str(MODULE_TEST)],
                               capture_output=True, text=True, cwd=PRODUCT, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert "PASS 8 canvas module tests" in completed.stdout
