"""F04A/T043: the /runs Agent node panel is served and its logic runs under Node.

The structural assertions are what the browser receives from the real compiled
API; ``runs.js`` is executed under Node with stubbed projections so the Agent
summary is observed behaviour, not source text. Still not_run: a browser DOM
driving the page (the same boundary as T023/T027/T031/T033/T042).
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


def test_the_runs_page_carries_the_agent_node_panel(t033_stack) -> None:
    response = t033_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="agent-node-panel"',
        'id="agent-node-status"',
        'id="agent-node-body"',
        "Agent 节点",
        "工具调用",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_js_exposes_the_agent_client(t033_stack) -> None:
    response = t033_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function describeAgentNode(",
        "function agentNodeIDOf(",
        "function renderAgentNodePanel(",
        "function loadCapabilityLedger(",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"
    # The panel links the existing two owner reads; it never invents a payload
    # path and never renders params or a receipt body.
    assert "runNodeResultsPath" in body and "runCapabilitiesPath" in body
    assert "params" not in body.split("function describeAgentNode(")[1].split("function ")[0]


def test_runs_js_agent_summary_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-agent.js"
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
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

const nodeBody = {
  items: [
    { node_id: 'stats', kind: 'data', artifact_id: 'sd', label: 'data',
      media_type: 'application/json', size_bytes: 88, sha256: 'a'.repeat(64) },
    { node_id: 'stats', kind: 'file', artifact_id: 'sf1', label: 'summary.json',
      media_type: 'application/json', size_bytes: 106, sha256: 'b'.repeat(64) },
    { node_id: 'stats', kind: 'file', artifact_id: 'sf2', label: 'summary.csv',
      media_type: 'text/csv', size_bytes: 36, sha256: 'c'.repeat(64) },
    { node_id: 'stats', kind: 'log', artifact_id: 'sl', label: 'log',
      media_type: 'text/plain', size_bytes: 120, sha256: 'd'.repeat(64) },
    { node_id: 'analysis', kind: 'data', artifact_id: 'ad', label: 'data',
      media_type: 'application/json', size_bytes: 24, sha256: 'e'.repeat(64) },
    { node_id: 'analysis', kind: 'file', artifact_id: 'af1', label: 'summary.json',
      media_type: 'application/json', size_bytes: 106, sha256: 'f'.repeat(64) },
    { node_id: 'analysis', kind: 'file', artifact_id: 'af2', label: 'summary.csv',
      media_type: 'text/csv', size_bytes: 36, sha256: '1'.repeat(64) },
    { node_id: 'analysis', kind: 'log', artifact_id: 'al', label: 'tool-log-stub-call-1',
      media_type: 'text/plain', size_bytes: 90, sha256: '2'.repeat(64) },
  ],
};
const ledgerBody = {
  items: [
    { id: 'op1', capability_kind: 'model', capability_version: '22222222-2222-4222-8222-222222222222',
      state: 'succeeded', attempts: 1, error_code: null, receipt_artifact_id: 'r1',
      tool_call_summary: [{ id: 'stub-call-1', name: 'sales_stats' }],
      structured_output_keys: ['text'], stub: true },
    { id: 'op2', capability_kind: 'tool', capability_version: '11111111-1111-4111-8111-111111111111',
      state: 'succeeded', attempts: 1, error_code: null, receipt_artifact_id: 'r2',
      tool_call_summary: [], structured_output_keys: [], stub: false },
    { id: 'op3', capability_kind: 'model', capability_version: '22222222-2222-4222-8222-222222222222',
      state: 'succeeded', attempts: 1, error_code: null, receipt_artifact_id: 'r3',
      tool_call_summary: [], structured_output_keys: ['text'], stub: true },
  ],
};
const statuses = { start: 'succeeded', stats: 'succeeded', analysis: 'succeeded', end: 'succeeded' };

const described = context.describeAgentNode(nodeBody, ledgerBody, statuses, 'succeeded', '');
assert.strictEqual(described.present, true);
assert.strictEqual(described.nodeID, 'analysis');
assert.strictEqual(described.statusText, '成功');
assert.strictEqual(described.turns, 2, JSON.stringify(described));
assert.strictEqual(described.turnsText, '2 轮模型调用');
assert.strictEqual(
  described.toolCalls.map((call) => call.id + '/' + call.name).join(','),
  'stub-call-1/sales_stats',
);
assert.ok(described.toolCallsText.indexOf('stub-call-1') >= 0, described.toolCallsText);
assert.strictEqual(described.toolOperations.length, 1);
assert.strictEqual(described.toolOperations[0].stateText, '成功');
assert.ok(described.toolStateText.indexOf('成功') >= 0, described.toolStateText);
assert.strictEqual(described.dataCount, 1);
assert.strictEqual(described.fileCount, 2);
assert.strictEqual(described.logCount, 1);
assert.strictEqual(described.stub, true);
assert.ok(described.text.indexOf('analysis') >= 0, described.text);
assert.ok(described.text.indexOf('2 轮模型调用') >= 0, described.text);
assert.strictEqual(described.schemaFailure, false);

// The final-output data row is the only Agent data; the tool log is separate.
assert.strictEqual(described.data[0].label, 'data');
assert.strictEqual(
  described.files.map((file) => file.label).join(','),
  'summary.json,summary.csv',
);
assert.strictEqual(described.logs[0].label, 'tool-log-stub-call-1');

// A failed schema validation has no agent rows: the node is located from the
// failed node.status entry plus the model operations in the ledger.
const failedBody = { items: nodeBody.items.slice(0, 4) };
const failedStatuses = { start: 'succeeded', stats: 'succeeded', analysis: 'failed', end: 'skipped' };
const failed = context.describeAgentNode(
  failedBody, ledgerBody, failedStatuses, 'failed', 'node_output_invalid',
);
assert.strictEqual(failed.present, true);
assert.strictEqual(failed.nodeID, 'analysis');
assert.strictEqual(failed.statusText, '失败');
assert.strictEqual(failed.schemaFailure, true);
assert.ok(failed.errorText.indexOf('analysis') >= 0, failed.errorText);
assert.ok(failed.errorText.indexOf('node_output_invalid') >= 0, failed.errorText);
assert.strictEqual(failed.dataCount, 0);

// An ordinary run with no agent node is not misrepresented as an agent run.
const legacy = context.describeAgentNode(
  { items: [] }, { items: [] }, {}, 'succeeded', '',
);
assert.strictEqual(legacy.present, false);
assert.ok(legacy.text.indexOf('没有 Agent 节点') >= 0, legacy.text);
assert.strictEqual(context.describeAgentNode(null, null, null, null, null).present, false);

// Nothing sensitive is echoed by the summary.
const rendered = JSON.stringify(described);
assert.ok(rendered.indexOf('params') < 0, rendered);
assert.ok(rendered.indexOf('arguments') < 0, rendered);
assert.ok(rendered.indexOf('objective') < 0, rendered);

process.stdout.write('runs.js agent summary behaviour ok\n');
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
        "runs.js agent summary did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js agent summary behaviour ok" in completed.stdout, completed.stdout
