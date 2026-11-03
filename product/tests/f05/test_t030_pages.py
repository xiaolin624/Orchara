"""T030: the attachments page is a real client of the preset-tool bridge.

Layer one fetches the page/asset from the running API over HTTP and asserts what
the browser receives: no inline script, no query-string action, the tool-run
controls, the Decimal table, the diagnostics panel and the download list.

Layer two executes the pure helpers under Node with a stubbed ``fetch``: the
exact POST body/headers, the state vocabulary, the poll rule, the Decimal-string
table (no Number conversion) and the content/download paths are observed
behaviour, not source text.

A DOM-driving browser is still not run; that boundary is the same one T023/T027/
T028 record.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t030_stack,
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


def test_the_attachment_page_offers_the_preset_tool(t030_stack) -> None:
    response = t030_stack.server.http.get("/attachments")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="tool-panel"',
        'id="tool-artifact-id"',
        'id="tool-run-submit"',
        'id="tool-run-status"',
        'id="tool-run-id"',
        'id="tool-run-state"',
        'id="tool-run-error-code"',
        'id="tool-summary"',
        'id="tool-summary-total"',
        'id="tool-summary-count"',
        'id="tool-summary-region-rows"',
        'id="tool-diagnostics"',
        'id="tool-diagnostics-code"',
        'id="tool-diagnostics-message"',
        'id="tool-diagnostics-fix"',
        'id="tool-downloads"',
        "/assets/attachments.js",
        "Decimal",
        "sales_stats",
        "summary.csv",
        "summary.json",
    ):
        assert marker in body, f"attachments.html is missing {marker!r}"

    # The page never interpolates request data into markup: all scripts are
    # external, there is no inline user content, and no query string/storage.
    assert INLINE_SCRIPT.findall(body) == []
    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_attachments_js_is_the_served_asset_with_the_tool_helpers(t030_stack) -> None:
    response = t030_stack.server.http.get("/assets/attachments.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    for marker in (
        "function toolRunBody(",
        "function toolRunPath(",
        "function toolRunStatusPath(",
        "function toolArtifactContentPath(",
        "async function createToolRunRequest(",
        "async function fetchToolRun(",
        "async function fetchToolArtifactJSON(",
        "function toolRunStatusLabel(",
        "function shouldPollToolRun(",
        "function summaryRows(",
        "function summaryTotals(",
        "function diagnosticsFields(",
        "function toolDownloadLinks(",
        "Idempotency-Key",
        "/api/v1/tool-runs",
    ):
        assert marker in body, f"attachments.js is missing {marker!r}"
    # Money must never be converted to a binary float in the page.
    assert "parseFloat(" not in body
    assert "Number(" not in body


def test_the_tool_page_parses_with_node(t030_stack, node_binary, tmp_path) -> None:
    body = (st.WEB_DIR / "attachments.html").read_text(encoding="utf-8")
    assert INLINE_SCRIPT.findall(body) == []
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "attachments.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


TOOL_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const pageSource = fs.readFileSync(webDir + '/assets/attachments.js', 'utf8');
const { webcrypto } = require('crypto');

function jsonResponse(status, payload, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    headers: { get: (name) => (headers || {})[name] || null },
  };
}

function load(route) {
  const calls = [];
  const context = {
    fetch: async (path, init) => {
      calls.push({ path, init });
      return route(path, init);
    },
    crypto: webcrypto,
    window: { location: { assign: () => {} } },
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(pageSource, context, { filename: 'attachments.js' });
  return { context, calls };
}

(async () => {
  // Exact create body; whitespace is trimmed, an empty id is refused locally.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(JSON.stringify(context.toolRunBody('sales_stats', '  abc  ')), JSON.stringify({
      tool: 'sales_stats',
      artifact_id: 'abc',
    }));
    assert.throws(() => context.toolRunBody('other', 'abc'), (error) => error.code === 'tool');
    assert.throws(() => context.toolRunBody('sales_stats', '   '), (error) => error.code === 'artifact_id');
  }

  // Path builders encode the id and never add a query string.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(context.toolRunStatusPath('a b'), '/api/v1/tool-runs/a%20b');
    assert.strictEqual(context.toolArtifactContentPath('a b'), '/api/v1/artifacts/a%20b/content');
    assert.strictEqual(context.toolRunPath(), '/api/v1/tool-runs');
  }

  // createToolRunRequest: POST JSON + CSRF + required idempotency key.
  {
    const { context, calls } = load(async () => jsonResponse(202, {
      id: 'job-1', state: 'ready', tool: 'sales_stats', artifact_id: 'art-1',
    }));
    const created = await context.createToolRunRequest(
      'sales_stats', 'art-1', 'csrf-tool', 'idem-tool',
    );
    assert.strictEqual(created.id, 'job-1');
    const post = calls.find((call) => call.init.method === 'POST');
    assert.ok(post, 'no POST was sent');
    assert.strictEqual(post.path, '/api/v1/tool-runs');
    assert.strictEqual(post.init.headers['X-CSRF-Token'], 'csrf-tool');
    assert.strictEqual(post.init.headers['Idempotency-Key'], 'idem-tool');
    assert.strictEqual(post.init.body, JSON.stringify({ tool: 'sales_stats', artifact_id: 'art-1' }));
  }

  // State vocabulary/poll decisions use the background-job states.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(context.toolRunStatusLabel('ready'), '排队中');
    assert.strictEqual(context.toolRunStatusLabel('leased'), '运行中');
    assert.strictEqual(context.toolRunStatusLabel('succeeded'), '成功');
    assert.strictEqual(context.toolRunStatusLabel('failed'), '失败');
    assert.strictEqual(context.toolRunStatusLabel('unknown_future'), '未知状态');
    assert.strictEqual(context.shouldPollToolRun('ready', 0, 3), true);
    assert.strictEqual(context.shouldPollToolRun('leased', 2, 3), true);
    assert.strictEqual(context.shouldPollToolRun('leased', 3, 3), false);
    assert.strictEqual(context.shouldPollToolRun('succeeded', 0, 3), false);
    assert.strictEqual(context.shouldPollToolRun(null, 0, 3), false);
  }

  // Decimal summary: exact strings, no Number conversion, validated shape.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const summary = {
      total: '250.00',
      regions: { '东区': '200.00', '西区': '50.00' },
      order_count: 3,
    };
    assert.strictEqual(JSON.stringify(context.summaryRows(summary)), JSON.stringify([
      { region: '东区', total: '200.00' },
      { region: '西区', total: '50.00' },
    ]));
    assert.strictEqual(JSON.stringify(context.summaryTotals(summary)), JSON.stringify({ total: '250.00', orderCount: '3' }));
    assert.strictEqual(
      JSON.stringify(context.summaryRows({ total: '0.30', regions: { '北区': '0.30' }, order_count: 2 })),
      JSON.stringify([{ region: '北区', total: '0.30' }]),
    );
    assert.throws(() => context.summaryRows({ total: 250.0, regions: {}, order_count: 3 }));
    assert.throws(() => context.summaryRows({ total: '250.0', regions: {}, order_count: 3 }));
    assert.throws(() => context.summaryTotals({ total: '1.00', regions: { x: '1.00' }, order_count: '3' }));
  }

  // Diagnostics are rendered from safe plain fields only.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const fields = context.diagnosticsFields({
      error_code: 'missing_column',
      message: '缺少必需列: date',
      fix: '请使用四列表头',
      retryable: false,
      location: { row: 1, column: 'date' },
    });
    assert.strictEqual(fields.code, 'missing_column');
    assert.strictEqual(fields.message, '缺少必需列: date');
    assert.strictEqual(fields.fix, '请使用四列表头');
    assert.ok(fields.location.includes('第 1 行'));
    assert.ok(fields.location.includes('date'));
    assert.strictEqual(fields.retryable, false);
    assert.strictEqual(context.diagnosticsFields(null), null);
  }

  // Download links come from the artifact content endpoint, never an object key.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const run = {
      id: 'job-1',
      state: 'succeeded',
      error_code: null,
      artifacts: [
        { id: 'sum', purpose: 'tool_summary', state: 'ready', media_type: 'application/json', size_bytes: 10 },
        { id: 'csv', purpose: 'tool_artifact', state: 'ready', media_type: 'text/csv', size_bytes: 20 },
        { id: 'log', purpose: 'log', state: 'ready', media_type: 'text/plain', size_bytes: 30 },
      ],
    };
    assert.strictEqual(JSON.stringify(context.toolDownloadLinks(run)), JSON.stringify([
      { label: '下载 summary.csv', url: '/api/v1/artifacts/csv/content' },
      { label: '下载 summary.json', url: '/api/v1/artifacts/sum/content' },
    ]));
    assert.strictEqual(context.toolRunArtifact(run, 'diagnostics'), null);
    assert.strictEqual(context.toolRunArtifact({ artifacts: [] }, 'tool_summary'), null);
  }

  // The content fetch parses JSON and surfaces the API's stable error code.
  {
    const { context, calls } = load(async (path, init) => {
      assert.strictEqual(path, '/api/v1/artifacts/sum/content');
      assert.strictEqual(init.credentials, 'same-origin');
      assert.strictEqual(init.method, 'GET');
      return jsonResponse(200, { total: '1.00', regions: { x: '1.00' }, order_count: 1 });
    });
    const document_ = await context.fetchToolArtifactJSON('sum');
    assert.strictEqual(document_.total, '1.00');
    assert.strictEqual(calls.length, 1);
  }

  process.stdout.write('tool page behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_tool_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "tool-harness.js"
    harness.write_text(
        TOOL_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "attachments.js did not behave as the tool bridge requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "tool page behaviour ok" in completed.stdout, completed.stdout
