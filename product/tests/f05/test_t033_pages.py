"""T033: the run pages carry the sandbox tool-debug panel (served + Node-executed).

The structural assertions are about what the browser receives from the real
compiled API; ``runs.js`` is then executed under Node with stubbed inputs so the
panel's wording (stopping vs stopped), the artifact list projection and the
"no T036 endpoint, no fake cancel" rule are observed behaviour, not source text.

Still ``not_run``: a DOM/browser driving the page (same boundary as T023/T027/
T029/T031/T032).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t033
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_runs_page_is_served_with_the_sandbox_panel(t033_stack) -> None:
    response = t033_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="sandbox-panel"',
        'id="sandbox-unit"',
        'id="sandbox-attempt"',
        'id="sandbox-status"',
        'id="sandbox-exit"',
        'id="sandbox-stop"',
        'id="sandbox-cancel-note"',
        'id="sandbox-artifacts"',
        'id="sandbox-artifacts-status"',
        "沙箱工具调试",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_js_serves_the_panel_and_artifact_client(t033_stack) -> None:
    response = t033_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function runArtifactsPath(",
        "function artifactDownloadPath(",
        "function describeSandboxPanel(",
        "function artifactDisplayName(",
        "function describeArtifactList(",
        "function loadRunArtifacts(",
        "X-Run-Execution-Unit",
        "X-Run-Cancel-Requested-At",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"
    # T036 implements the cancel endpoint the T033 page deliberately did not call
    # while it was missing; the asset now owns that real path, and the old "not
    # implemented / SQL workaround" wording must be gone.
    assert "function cancelRunPath(" in body, "runs.js does not expose the cancel path"
    assert "runs/' + encodeURIComponent(String(runId || '')) + '/cancel" in body
    assert "当前未实现" not in body
    assert "object_key" not in body


def test_runs_js_panel_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-sandbox-panel.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} }, open() {} } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

// Paths are built from the id, never from a caller URL.
assert.strictEqual(context.runArtifactsPath('r1'), '/api/v1/runs/r1/artifacts');
assert.strictEqual(context.runArtifactsPath('a b/c'), '/api/v1/runs/a%20b%2Fc/artifacts');
assert.strictEqual(context.artifactDownloadPath('a1'), '/api/v1/artifacts/a1/download');

// The two new non-contract headers are read by exact name and default to ''.
function headers(map) { return { get: (name) => map[name] || null }; }
const facts = context.runRuntimeFacts(headers({
  'X-Manifest-SHA256': 'a'.repeat(64),
  'X-Run-Attempt-Started-At': '2026-11-03T00:00:00Z',
  'X-Run-Lease-Deadline': '2026-11-03T00:00:30Z',
  'X-Run-Execution-Unit': 'exec-host-1-abc',
  'X-Run-Cancel-Requested-At': '2026-11-03T00:00:10Z',
}));
assert.strictEqual(facts.executionUnit, 'exec-host-1-abc');
assert.strictEqual(facts.cancelRequestedAt, '2026-11-03T00:00:10Z');
const empty = context.runRuntimeFacts(null);
assert.strictEqual(empty.executionUnit, '');
assert.strictEqual(empty.cancelRequestedAt, '');

// A running attempt shows its unit and lease; nothing is invented when absent.
const running = context.describeSandboxPanel(
  { id: 'r1', state: 'running', phase: 'executing', kind: 'debug' },
  { executionUnit: 'exec-u1', startedAt: '2026-11-03T00:00:00Z', leaseDeadline: '2026-11-03T00:00:30Z' },
);
assert.strictEqual(running.visible, true);
assert.ok(running.attemptLine.indexOf('exec-u1') >= 0, running.attemptLine);
assert.ok(running.statusLine.indexOf('gVisor') >= 0, running.statusLine);
assert.ok(running.statusLine.indexOf('成功') < 0, running.statusLine);
assert.strictEqual(running.stopLabel, '未请求取消。');

const queued = context.describeSandboxPanel({ id: 'r1', state: 'queued' }, {});
assert.ok(queued.statusLine.indexOf('尚未被 supervisor 领取') >= 0, queued.statusLine);
assert.ok(queued.cancelNote.indexOf('T036') >= 0, queued.cancelNote);
assert.ok(queued.cancelNote.indexOf('recovery_pending') >= 0, queued.cancelNote);

// Stopping vs stopped: the cancel intent is a header, the terminal state is the
// Run; neither is inferred from the other.
const stopping = context.describeSandboxPanel(
  { id: 'r1', state: 'running', phase: 'executing' },
  { cancelRequestedAt: '2026-11-03T00:00:10Z' },
);
assert.ok(stopping.statusLine.indexOf('正在停止') >= 0, stopping.statusLine);
assert.ok(stopping.stopLabel.indexOf('正在停止') >= 0, stopping.stopLabel);
assert.ok(stopping.stopLabel.indexOf('已停止') < 0, stopping.stopLabel);

const parked = context.describeSandboxPanel(
  { id: 'r1', state: 'recovery_pending', phase: 'idle' },
  { cancelRequestedAt: '2026-11-03T00:00:10Z' },
);
assert.ok(parked.statusLine.indexOf('正在停止') >= 0, parked.statusLine);
assert.ok(parked.exitLine.indexOf('尚未提交终态') >= 0, parked.exitLine);

const stopped = context.describeSandboxPanel(
  { id: 'r1', state: 'cancelled', phase: 'idle' },
  { cancelRequestedAt: '2026-11-03T00:00:10Z' },
);
assert.ok(stopped.stopLabel.indexOf('已停止') >= 0, stopped.stopLabel);
assert.ok(stopped.statusLine.indexOf('终态') >= 0, stopped.statusLine);

// The error code is shown only from the Run body, and success is never implied.
const failed = context.describeSandboxPanel(
  { id: 'r1', state: 'failed', phase: 'idle', error_code: 'negative_amount' },
  {},
);
assert.ok(failed.exitLine.indexOf('negative_amount') >= 0, failed.exitLine);

// Artifact lines never carry an object key — only media type, size and a short id.
const line = context.artifactDisplayName({
  id: '11111111-2222-4333-8444-555555555555', state: 'ready',
  media_type: 'application/json', size_bytes: 106,
  object_key: 'sealed/SECRET-OBJECT-KEY',
});
assert.ok(line.indexOf('JSON') >= 0, line);
assert.ok(line.indexOf('106 B') >= 0, line);
assert.ok(line.indexOf('SECRET-OBJECT-KEY') < 0, line);
assert.ok(line.indexOf('11111111') >= 0, line);
assert.strictEqual(
  context.artifactDisplayName({ media_type: 'text/csv', size_bytes: 36, id: 'abcdefgh' }),
  'CSV · 36 B · abcdefgh',
);

const emptyList = context.describeArtifactList({ items: [], next_cursor: null });
assert.strictEqual(emptyList.count, 0);
assert.ok(emptyList.text.indexOf('没有') >= 0, emptyList.text);
const twoItems = context.describeArtifactList({
  items: [{ id: 'a' }, { id: 'b' }], next_cursor: null,
});
assert.strictEqual(twoItems.count, 2);
assert.deepStrictEqual(twoItems.ids, ['a', 'b']);
assert.ok(twoItems.text.indexOf('2') >= 0, twoItems.text);
const paged = context.describeArtifactList({ items: [{ id: 'a' }], next_cursor: 'c1' });
assert.ok(paged.text.indexOf('下一页') >= 0, paged.text);
assert.strictEqual(context.describeArtifactList(null).count, 0);

process.stdout.write('runs.js sandbox panel behaviour ok\n');
""".replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "runs.js sandbox panel did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js sandbox panel behaviour ok" in completed.stdout, completed.stdout
