"""F04B/T088: the run view's retrieve citation surface (real shipped bytes).

The debug page's node-state table already renders condition/join details from the
node data artifacts. T088 adds the retrieve row: the citations projection is
rendered as links to the real ``GET /api/v1/knowledge-citations/{chunk_id}`` route,
and opening one shows the stored source (or an "unavailable" message after a
revocation — never a cached body).

No browser is driven: ``runview.js`` is a classic script, so it is read as bytes
and executed in a Node ``vm`` with a minimal document/fetch shim
(``runview_t088.test.mjs``). The page wiring/markers are checked against the same
committed asset; a real browser DOM stays ``not_run`` (same boundary as T083).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PRODUCT = Path(__file__).resolve().parents[2]
WEB_DIR = PRODUCT / "apps" / "web" / "src"
ASSETS = WEB_DIR / "assets"
RUNVIEW_JS = ASSETS / "runview.js"
DEBUG_HTML = WEB_DIR / "debug.html"
MODULE_TEST = Path(__file__).resolve().parent / "runview_t088.test.mjs"

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the runview pure-module test cannot run")
    return NODE


def test_the_runview_asset_is_valid_javascript(node_binary: str) -> None:
    assert RUNVIEW_JS.is_file(), "the committed runview.js asset is missing"
    completed = subprocess.run(
        [node_binary, "--check", str(RUNVIEW_JS)], capture_output=True, text=True, timeout=120
    )
    assert completed.returncode == 0, completed.stderr


def test_the_runview_ships_the_retrieve_citation_surface() -> None:
    text = RUNVIEW_JS.read_text(encoding="utf-8")
    for marker in (
        "retrieve: '检索'",
        "runViewDescribeRetrieve",
        "run-node-citation",
        "/api/v1/knowledge-citations/",
        "runViewOpenCitation",
        "retrieve_unavailable",
        "retrieve_denied",
        "该引用已不可用",
        "未提供缓存正文",
        "refreshRunViewArtifacts",
    ):
        assert marker in text, marker
    # The retrieve node's data artifact (the citations) is prefetched.
    assert "node.type === 'retrieve'" in text
    # The debug page still loads the real asset and styles the citation links.
    page = DEBUG_HTML.read_text(encoding="utf-8")
    assert "/assets/runview.js" in page
    assert ".run-node-citation" in page
    assert ".run-node-citation-status" in page


def test_the_retrieve_runview_pure_functions_pass_under_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, str(MODULE_TEST)],
        capture_output=True,
        text=True,
        cwd=PRODUCT,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert "PASS 7 runview T088 tests" in completed.stdout
