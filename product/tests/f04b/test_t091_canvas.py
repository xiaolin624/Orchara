"""F04B/T091: the ten-type canvas surface and the committed bundle.

No browser is driven (the repository test stack has no browser automation). The
checks prove the committed bundle carries every one of the ten node types'
palette/panel surface and that the real pure modules execute the ten-node fixture
under Node: palette/defaults, byte-identical graph round-trip, legal sources
(join-by-source, retrieve arrays, call_app mapping, approval proposal), export
allowlist/refusals and located repair hints.

The optional rebuild check catches a stale committed asset when
``canvas/node_modules`` exists; it restores the committed bytes afterwards.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
CANVAS = PRODUCT / "apps" / "web" / "canvas"
ASSETS = PRODUCT / "apps" / "web" / "src" / "assets"
MODULE_TEST = Path(__file__).resolve().parent / "ten_node_canvas.test.mjs"

NODE = shutil.which("node")

# Every palette label (workflow.schema.json node `type` set).
PALETTE_LABELS = (
    "开始", "结束", "模型", "Agent", "Python 工具", "子应用调用", "知识检索",
    "条件分支", "并行汇合", "人工审批",
)

# One panel marker per type; the markers are the real `data-field` keys the panel
# renderers use, so a missing panel cannot silently degrade to a bare config box.
PANEL_MARKERS = (
    "config.tool_version",
    "config.model_profile_version",
    "config.instructions",
    "config.prompt_template",
    "config.response_mode",
    "config.knowledge_base_ids",
    "config.top_k",
    "config.application_version",
    "config.cases.",
    "config.join_id",
    "config.fork_id",
    "config.optional_sources.convergence",
    "config.title",
    "config.expires_seconds",
    "approval.boundary",
    "callApp.problems",
)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the canvas ten-node test cannot run")
    return NODE


def test_the_committed_bundle_is_valid_and_ships_all_ten_node_surfaces() -> None:
    bundle = ASSETS / "canvas.js"
    assert bundle.is_file(), "the committed canvas.js bundle is missing"
    text = bundle.read_text(encoding="utf-8")
    for label in PALETTE_LABELS:
        assert label in text, label
    for marker in PANEL_MARKERS:
        assert marker in text, marker
    # The bundle must still carry the T083/T085 condition/join error surfaces.
    for marker in ("regionProblems", "cross_region_edge", "fork_requires_join"):
        assert marker in text, marker


def test_the_ten_node_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True, text=True, encoding="utf-8",
        cwd=PRODUCT, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS 12 canvas T091 ten-node tests" in completed.stdout, completed.stdout


def test_the_fixture_files_are_checked_in() -> None:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    definition = fixtures / "ten_node_definition.json"
    layout = fixtures / "ten_node_layout.json"
    assert definition.is_file() and layout.is_file()
    assert definition.stat().st_size > 5_000


def test_the_committed_bundle_matches_a_fresh_build(node_binary: str) -> None:
    """Skip unless esbuild is installed; never leave a rebuilt asset behind."""
    if not (CANVAS / "node_modules").is_dir():
        pytest.skip("canvas/node_modules is absent; the rebuild check needs esbuild")
    bundle = ASSETS / "canvas.js"
    style = ASSETS / "canvas.css"
    before = {bundle: bundle.read_bytes(), style: style.read_bytes()}
    try:
        completed = subprocess.run(
            [node_binary, "build.mjs"], cwd=CANVAS,
            capture_output=True, text=True, timeout=180,
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
