"""T031: the runs/debug pages show executing -> finalizing -> terminal honestly.

Two layers, both real:

* the page and its assets are fetched from the running API over HTTP, and the
  structural assertions are about what the browser receives (a lifecycle row, the
  "finalizing waits for exit evidence" sentence, and a polling helper);
* ``runs.js``/``debug.js`` are executed under Node, so the stage sentence, the
  lifecycle order and the "poll until terminal, never claim success early" rule
  are observed behaviour rather than source text.

What is still not_run: a DOM/browser driving the page (the same boundary F01/
T023/T027/T029 record).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_runs_page_shows_the_execution_lifecycle_and_the_finalizing_rule(t029_stack) -> None:
    response = t029_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    body = response.text
    for marker in (
        'id="detail-lifecycle"',
        "executing → finalizing → 终态",
        "finalizing",
        "退出证据",
        "不会显示成功",
        "GET /api/v1/runs/{id}",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    for marker in (
        "function runLifecycle(",
        "function lifecycleText(",
        "function shouldPollRun(",
        "function pollRunDetail(",
        "finalizing",
        "退出证据",
        "不是成功",
    ):
        assert marker in asset.text, f"runs.js is missing {marker!r}"


def test_the_debug_page_explains_finalizing_without_claiming_success(t029_stack) -> None:
    debug = t029_stack.server.http.get(f"/apps/{t029.new_uuid()}/debug")
    assert debug.status == 200
    assert "executing → finalizing → 终态" in debug.text
    assert "退出证据" in debug.text
    assert "不会在终态前显示成功" in debug.text

    asset = t029_stack.server.http.get("/assets/debug.js")
    assert asset.status == 200, asset.text
    assert "function describeRunStage(" in asset.text
    assert "finalizing" in asset.text
    assert "等待 supervisor" in asset.text


def test_the_served_pages_and_assets_parse_with_node(t029_stack, node_binary, tmp_path) -> None:
    for page in ("runs.html", "debug.html"):
        body = (st.WEB_DIR / page).read_text(encoding="utf-8")
        for index, block in enumerate(INLINE_SCRIPT.findall(body)):
            script = tmp_path / f"{page}.{index}.js"
            script.write_text(block, encoding="utf-8")
            completed = subprocess.run(
                [node_binary, "--check", str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            assert completed.returncode == 0, (page, completed.stderr)
    for asset in ("runs.js", "debug.js"):
        completed = subprocess.run(
            [node_binary, "--check", str(st.WEB_DIR / "assets" / asset)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        assert completed.returncode == 0, (asset, completed.stderr)


LIFECYCLE_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const runsSource = fs.readFileSync(webDir + '/assets/runs.js', 'utf8');
const debugSource = fs.readFileSync(webDir + '/assets/debug.js', 'utf8');

function load(extra) {
  const context = { console };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(extra, context, { filename: 'page.js' });
  return context;
}

const runs = load(runsSource);

// The lifecycle order is fixed and the current stage is derived from the Run.
assert.strictEqual(
  JSON.stringify(runs.runLifecycle({ state: 'running', phase: 'executing' })),
  JSON.stringify({ stages: ['executing', 'finalizing', 'terminal'], current: 'executing' }));
assert.strictEqual(
  runs.runLifecycle({ state: 'running', phase: 'finalizing' }).current, 'finalizing');
assert.strictEqual(runs.runLifecycle({ state: 'succeeded', phase: 'idle' }).current, 'terminal');
assert.ok(runs.lifecycleText({ state: 'running', phase: 'finalizing' }).includes('finalizing'));
assert.ok(runs.lifecycleText({ state: 'succeeded', phase: 'idle' }).includes('terminal'));

// finalizing is explained as waiting for exit evidence, never as success.
const finalizing = runs.describeRunState({ state: 'running', phase: 'finalizing' }, {});
assert.ok(finalizing.includes('等待'), finalizing);
assert.ok(finalizing.includes('退出证据'), finalizing);
assert.ok(finalizing.includes('不是成功'), finalizing);
assert.ok(!finalizing.includes('已成功'), finalizing);
assert.ok(!/%|进度条|预计/.test(finalizing), finalizing);

assert.ok(runs.describeRunState({ state: 'succeeded' }, {}).includes('终态'));
assert.ok(runs.describeRunState({ state: 'recovery_pending' }, {}).includes('recovery_pending'));

// Polling stops only on a committed terminal state.
assert.strictEqual(runs.shouldPollRun({ state: 'running', phase: 'executing' }), true);
assert.strictEqual(runs.shouldPollRun({ state: 'running', phase: 'finalizing' }), true);
assert.strictEqual(runs.shouldPollRun({ state: 'recovery_pending' }), true);
assert.strictEqual(runs.shouldPollRun({ state: 'needs_reconciliation' }), true);
assert.strictEqual(runs.shouldPollRun({ state: 'succeeded' }), false);
assert.strictEqual(runs.shouldPollRun({ state: 'failed' }), false);
assert.strictEqual(runs.shouldPollRun({ state: 'cancelled' }), false);
assert.strictEqual(runs.shouldPollRun(null), false);

// The debug page uses the same honest vocabulary.
const debug = load(debugSource);
const debugFinalizing = debug.describeRunStage({ state: 'running', phase: 'finalizing' });
assert.ok(debugFinalizing.includes('退出证据'), debugFinalizing);
assert.ok(debugFinalizing.includes('不是成功'), debugFinalizing);
assert.ok(!debugFinalizing.includes('已成功'), debugFinalizing);
assert.ok(debug.describeRunStage({ state: 'succeeded' }).includes('终态'));
assert.strictEqual(debug.shouldPollRun({ state: 'running', phase: 'finalizing' }, 0, 10), true);
assert.strictEqual(debug.shouldPollRun({ state: 'succeeded' }, 0, 10), false);

process.stdout.write('lifecycle behaviour ok\n');
"""


def test_the_lifecycle_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "lifecycle-harness.js"
    harness.write_text(
        LIFECYCLE_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "the lifecycle page logic did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "lifecycle behaviour ok" in completed.stdout, completed.stdout
