"""F04B/T083: the committed canvas artifact and the pure-module Node tests.

No browser is driven here. These checks prove the page receives a real, valid
bundle that carries the condition/join palette and panels, and that the pure
graph/DSL/source/region/error/export modules execute under Node against the P05
corpus. npm is never run by the test suite; the bundle is a committed artifact.

The optional rebuild check catches a stale committed asset when `node_modules`
exists; it restores the committed bytes afterwards so the working tree stays
clean.
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
MODULE_TEST = Path(__file__).resolve().parent / "canvas_f04b.test.mjs"

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


def test_the_bundle_ships_the_condition_and_join_surfaces() -> None:
    text = (ASSETS / "canvas.js").read_text(encoding="utf-8")
    for marker in (
        "OrcharaCanvas",
        "条件分支",
        "并行汇合",
        "config.cases.",
        "config.join_id",
        "config.fork_id",
        "required_sources",
        "optional_sources",
        "edge.port",
        "orchara-edge-invalid",
        "node-default",
        "regionProblems",
        "no_free_port",
        "fork_requires_join",
        "cross_region_edge",
        # F04B/T085: the optional branch's convergence rule is visible in the
        # join panel (only pure read/compute errors are absorbed; writes,
        # authorization, budget and approval failures are never downgraded).
        "config.optional_sources.convergence",
        "仅纯读/计算错误可收敛",
        "不会降级",
    ):
        assert marker in text, marker
    css = (ASSETS / "canvas.css").read_text(encoding="utf-8")
    for marker in (".orchara-canvas", ".canvas-stage", ".orchara-port-label",
                   ".orchara-edge-invalid", ".case-row", ".port-chip"):
        assert marker in css, marker


def test_the_canvas_dependencies_stay_pinned_and_locked() -> None:
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


def test_the_canvas_pure_modules_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run([node_binary, str(MODULE_TEST)],
                               capture_output=True, text=True, cwd=PRODUCT, timeout=120)
    assert completed.returncode == 0, completed.stderr
    assert "PASS 9 canvas F04B module tests" in completed.stdout


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
