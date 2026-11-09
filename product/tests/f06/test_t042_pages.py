"""F04A/T042: the /runs node-results panel is served and its logic runs under Node.

The structural assertions are what the browser receives from the real compiled
API; ``runs.js`` is then executed under Node with stubbed inputs so the panel's
grouping, its "no result" vs "failed" distinction, the capped log view and the
content/download paths are observed behaviour, not source text.

Still ``not_run``: a DOM/browser driving the page (the same boundary as
T023/T027/T029/T031/T032/T033).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
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


def test_the_runs_page_carries_the_node_results_panel(t033_stack) -> None:
    response = t033_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="node-results-panel"',
        'id="node-results-status"',
        'id="node-results-nodes"',
        "节点结果",
        "data / 文件 / 日志",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_js_exposes_the_node_result_client(t033_stack) -> None:
    response = t033_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function runNodeResultsPath(",
        "function artifactContentPath(",
        "function describeNodeResults(",
        "function describeNodeResultItem(",
        "function capLogText(",
        "function loadNodeResults(",
        "function recordNodeStatus(",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"
    # The panel reads the owner-only projection and the regular content route; it
    # must never invent a node-results mutation path.
    assert "'/node-results'" in body
    assert "/node-results", "the path builder is missing"
    assert "object_key" not in body


def test_runs_js_node_results_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-node-results.js"
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

// Paths are built from ids, never from a caller URL.
assert.strictEqual(context.runNodeResultsPath('r1'), '/api/v1/runs/r1/node-results');
assert.strictEqual(context.runNodeResultsPath('a b/c'), '/api/v1/runs/a%20b%2Fc/node-results');
assert.strictEqual(context.artifactContentPath('a1'), '/api/v1/artifacts/a1/content');

// Logs are capped client-side and say so; short logs are untouched.
const longLog = 'x'.repeat(9000);
const capped = context.capLogText(longLog, 4096);
assert.strictEqual(capped.truncated, true);
assert.strictEqual(capped.text.length, 4096);
assert.ok(capped.notice.indexOf('截断') >= 0, capped.notice);
const short = context.capLogText('stdout:\nok\n', 4096);
assert.strictEqual(short.truncated, false);
assert.strictEqual(short.notice, '');
assert.strictEqual(context.capLogText(null, 10).text, '');

// Data is pretty-printed JSON; non-JSON stays visible (never dropped silently).
assert.strictEqual(context.prettyJSONText('{"b":1,"a":2}'), JSON.stringify({ b: 1, a: 2 }, null, 2));
assert.strictEqual(context.prettyJSONText('not json'), 'not json');

// Status vocabulary is closed and distinguishes failure from no result.
assert.strictEqual(context.nodeStatusText('succeeded'), '成功');
assert.strictEqual(context.nodeStatusText('failed'), '失败');
assert.strictEqual(context.nodeStatusText('no_result'), '无结果');
assert.strictEqual(context.nodeStatusText('skipped'), '已跳过');
assert.strictEqual(context.nodeResultKindText('file'), '文件');

const item = context.describeNodeResultItem({
  node_id: 'stats', kind: 'file', artifact_id: 'a1', label: 'summary.csv',
  media_type: 'text/csv', size_bytes: 36, sha256: 'f'.repeat(64),
});
assert.ok(item.line.indexOf('文件') >= 0, item.line);
assert.ok(item.line.indexOf('summary.csv') >= 0, item.line);
assert.ok(item.line.indexOf('36 B') >= 0, item.line);
assert.strictEqual(item.downloadPath, '/api/v1/artifacts/a1/download');
assert.strictEqual(item.contentPath, '/api/v1/artifacts/a1/content');

const body = {
  items: [
    { node_id: 'stats', kind: 'data', artifact_id: 'd1', label: 'data',
      media_type: 'application/json', size_bytes: 88, sha256: 'a'.repeat(64) },
    { node_id: 'stats', kind: 'file', artifact_id: 'f1', label: 'summary.json',
      media_type: 'application/json', size_bytes: 106, sha256: 'b'.repeat(64) },
    { node_id: 'stats', kind: 'file', artifact_id: 'f2', label: 'summary.csv',
      media_type: 'text/csv', size_bytes: 36, sha256: 'c'.repeat(64) },
    { node_id: 'stats', kind: 'log', artifact_id: 'l1', label: 'log',
      media_type: 'text/plain', size_bytes: 120, sha256: 'd'.repeat(64) },
  ],
};
const statuses = { start: 'succeeded', stats: 'succeeded', end: 'succeeded' };
const described = context.describeNodeResults(body, statuses, 'succeeded');
assert.strictEqual(described.empty, false);
assert.strictEqual(described.nodeCount, 3, JSON.stringify(described));
assert.strictEqual(described.dataCount, 1);
assert.strictEqual(described.fileCount, 2);
assert.strictEqual(described.logCount, 1);
const stats = described.nodes.find((node) => node.nodeID === 'stats');
assert.strictEqual(stats.statusText, '成功');
assert.strictEqual(stats.data[0].label, 'data');
assert.strictEqual(stats.files.map((file) => file.label).join(','), 'summary.json,summary.csv');
assert.strictEqual(stats.logs.length, 1);

// A failed node with no rows is "failed", never "no result"; a succeeded node
// with no rows is "no result", not an invented output.
const failed = context.describeNodeResults(
  { items: [] },
  { start: 'succeeded', stats: 'failed', end: 'skipped' },
  'failed',
);
assert.strictEqual(failed.empty, false);
const failedStats = failed.nodes.find((node) => node.nodeID === 'stats');
assert.strictEqual(failedStats.statusText, '失败');
assert.strictEqual(failedStats.resultText, '无结果');
assert.strictEqual(failedStats.count, 0);
const skippedEnd = failed.nodes.find((node) => node.nodeID === 'end');
assert.strictEqual(skippedEnd.statusText, '已跳过');

const noResult = context.describeNodeResults({ items: [] }, { stats: 'succeeded' }, 'succeeded');
assert.strictEqual(noResult.nodes[0].statusText, '成功');
assert.strictEqual(noResult.nodes[0].resultText, '无结果');

// A node with registered rows is a success even before the SSE catches up.
const fallback = context.describeNodeResults(body, {}, 'running');
assert.strictEqual(fallback.nodes[0].statusText, '成功');

// Nothing is invented for an old single-python run with no node rows.
const legacy = context.describeNodeResults({ items: [] }, {}, 'succeeded');
assert.strictEqual(legacy.empty, true);
assert.ok(legacy.text.indexOf('没有节点结果') >= 0, legacy.text);
assert.strictEqual(context.describeNodeResults(null, null, null).nodeCount, 0);

process.stdout.write('runs.js node results behaviour ok\n');
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
        "runs.js node results did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js node results behaviour ok" in completed.stdout, completed.stdout
