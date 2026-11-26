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


# T4 (GitHub #168): the page is a workbench, not a card stack. These markers are
# the page-level seam between the editor chrome (owned by editor.html/editor.js)
# and the canvas app (owned by apps/web/canvas/src): the toolbar and the status
# line live on the page, while the node library, stage and inspector stay inside
# the committed canvas bundle. No new business ids are introduced (t165 pins the
# editor id inventory), so the controls are addressed by data attributes.
EDITOR_WORKBENCH_MARKERS = (
    # the T1 shell slots stay: one heading, one metadata/status line, one sections block
    'class="page-heading"',
    'class="page-meta"',
    'class="page-sections"',
    # the workbench chrome
    'data-editor-workbench',
    'data-editor-statusbar',
    'data-editor-app-ref',
    'data-editor-definition-value',
    'data-editor-layout-state',
    'data-editor-preflight',
    'data-editor-zoom-value',
    'data-editor-action="undo"',
    'data-editor-action="redo"',
    'data-editor-action="zoom-out"',
    'data-editor-action="zoom-in"',
    'data-editor-action="auto-layout"',
    'data-editor-action="export"',
    'data-editor-action="validate"',
)


def test_the_editor_page_is_a_full_height_workbench() -> None:
    html = EDITOR.read_text(encoding="utf-8")
    missing = [marker for marker in EDITOR_WORKBENCH_MARKERS if marker not in html]
    assert not missing, missing
    # One page keeps exactly one h1 and one form; the toolbar actions never add
    # a competing form or a second save path.
    assert html.count("<h1") == 1
    assert html.count("<form") == 1
    # The canvas is still hosted directly by the page, but no longer wrapped in
    # a page-level card that would cap its height.
    assert '<div id="canvas-root"></div>' in html
    assert 'class="card canvas-card"' not in html
    assert "<h2>工作流画布</h2>" not in html
    # The phone boundary note survives verbatim (t098 / t165 contract).
    note = html[html.index('data-mobile-scope="no-full-canvas-editing"'):]
    note = note[: note.index("</p>")]
    assert "画布" in note and "不承诺" in note


def test_the_committed_canvas_stylesheet_defines_the_workbench() -> None:
    css = (ASSETS / "canvas.css").read_text(encoding="utf-8")
    for marker in (
        # three columns: a 232px library, the stage, a 320px inspector
        ".canvas-body",
        "grid-template-columns: 232px minmax(0, 1fr) 320px",
        "height: 100%",
        # grouped, searchable library and a fixed-bottom inspector action bar
        ".node-library-search",
        ".palette-group",
        ".config-panel",
        ".panel-actions",
        ".node-info",
        ".orchara-flow-id",
        # node type colour bars consume the shared tokens, never re-declare them
        ".orchara-flow-node.orchara-node-start::before",
        ".orchara-flow-node.orchara-node-model::before",
        ".orchara-flow-node.orchara-node-end::before",
        "var(--teal)",
        "var(--accent)",
        "var(--muted)",
        "#7a5c2e",
        # selection and invalid states are double-encoded, not colour-only
        ".vue-flow__node.orchara-node.selected",
        "[data-invalid",
        # floating viewport tools
        ".canvas-float",
    ):
        assert marker in css, marker


def test_the_committed_canvas_bundle_renders_the_grouped_library_and_inspector() -> None:
    js = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    for marker in (
        "node-library",
        "node-library-search",
        "palette-group",
        "orchara-flow-id",
        "panel-actions",
        "canvas-float",
        "搜索节点",
        "节点库",
        "发布前检查",
        "onPaneReady",
        "MarkerType",
    ):
        assert marker in js, marker


def test_the_editor_wiring_delegates_canvas_actions_to_the_canvas_api() -> None:
    source = (ASSETS / "editor.js").read_text(encoding="utf-8")
    for marker in (
        "data-editor-action",
        "canvasApi.validate()",
        "canvasApi.exportDraft()",
        "onValidation",
        "onHistory",
        "data-editor-preflight",
    ):
        assert marker in source, marker
    # The save/validate/conflict request surface is unchanged: the only write
    # path stays PUT draft with If-Match, and validation stays POST /validate.
    assert "'If-Match': etag" in source
    assert "/draft" in source and "/validate" in source


def test_the_canvas_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run([node_binary, str(MODULE_TEST)],
                               capture_output=True, text=True, cwd=PRODUCT, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert "PASS 8 canvas module tests" in completed.stdout
