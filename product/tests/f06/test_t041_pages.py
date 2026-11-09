"""F06/T041: the runs-page "受控调用账本" panel and its Node-executed helpers.

The structural assertions inspect what the browser actually receives from the
compiled API. ``runs.js`` is then executed under Node with the same public helper
functions the page calls, so the ledger projection (state/attempt/tool-call/stub
labels) is observed behaviour rather than source text.

The DOM/browser driving boundary is the same as every other page ticket in this
build: ``not_run`` (no browser driver); the helpers are pure and Node-tested.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import f06_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_runs_page_has_the_capability_ledger_panel(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="capability-panel"',
        'id="capability-rows"',
        'id="capability-status"',
        "受控调用账本",
        "物理尝试",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "params" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_js_has_the_capability_ledger_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function runCapabilitiesPath(",
        "function describeCapabilityOperation(",
        "function describeCapabilityLedger(",
        "function capabilityStateText(",
        "function capabilityKindText(",
        "function summarizeToolCalls(",
        "function loadCapabilityLedger(",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"
    assert "object_key" not in body


def test_capability_ledger_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "capability-ledger-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

// The path is built from the id; no caller URL is trusted.
assert.strictEqual(
  context.runCapabilitiesPath('r 1/x'),
  '/api/v1/runs/r%201%2Fx/capabilities',
);

// A succeeded, stub-labelled model call with a tool call and structured output.
const succeeded = context.describeCapabilityOperation({
  id: 'op-1',
  capability_kind: 'model',
  capability_version: '22222222-2222-4222-8222-222222222222',
  state: 'succeeded',
  attempts: 1,
  error_code: null,
  receipt_artifact_id: 'artifact-1',
  tool_call_summary: [{ id: 'call-1', name: 'lookup' }],
  structured_output_keys: ['report'],
  stub: true,
});
assert.strictEqual(succeeded.stateText, '成功');
assert.strictEqual(succeeded.kindText, '模型');
assert.strictEqual(succeeded.attemptsText, '1 次');
assert.strictEqual(succeeded.stub, true);
assert.ok(succeeded.stubLabel.includes('stub'), succeeded.stubLabel);
assert.strictEqual(succeeded.toolCallsText, 'call-1(lookup)');
assert.strictEqual(succeeded.structuredText, 'report');
assert.strictEqual(succeeded.hasReceipt, true);
assert.strictEqual(succeeded.errorText, '');
assert.deepStrictEqual(succeeded.structuredKeys, ['report']);
assert.ok(!JSON.stringify(succeeded).includes('params'));

// A failed tool operation shows the safe code and is not labelled stub.
const failed = context.describeCapabilityOperation({
  id: 'op-2',
  capability_kind: 'tool',
  capability_version: '11111111-1111-4111-8111-111111111111',
  state: 'failed',
  attempts: 1,
  error_code: 'provider_response_invalid',
  receipt_artifact_id: null,
  tool_call_summary: [],
  structured_output_keys: [],
  stub: false,
});
assert.strictEqual(failed.stateText, '失败');
assert.strictEqual(failed.kindText, '工具');
assert.strictEqual(failed.errorText, '错误码 provider_response_invalid');
assert.strictEqual(failed.stubLabel, '真实提供方');
assert.strictEqual(failed.toolCallsText, '无工具调用');
assert.strictEqual(failed.structuredText, '无结构化输出');
assert.strictEqual(failed.hasReceipt, false);

// Attempts are the physical ledger rows, not a hidden retry count.
const retried = context.describeCapabilityOperation({
  id: 'op-3',
  capability_kind: 'model',
  capability_version: '22222222-2222-4222-8222-222222222222',
  state: 'succeeded',
  attempts: 2,
  error_code: null,
  receipt_artifact_id: 'artifact-3',
  tool_call_summary: [],
  structured_output_keys: [],
  stub: true,
});
assert.strictEqual(retried.attemptsText, '2 次');

// The ledger body projection, including the empty case.
const ledger = context.describeCapabilityLedger({ items: [succeeded, failed] });
assert.strictEqual(ledger.count, 2);
assert.strictEqual(ledger.empty, false);
assert.ok(ledger.text.includes('2'), ledger.text);
const empty = context.describeCapabilityLedger({});
assert.strictEqual(empty.count, 0);
assert.strictEqual(empty.empty, true);
assert.ok(empty.text.includes('还没有受控调用'));

// The state vocabulary covers the contract states.
assert.strictEqual(context.capabilityStateText('awaiting_approval'), '待审批');
assert.strictEqual(context.capabilityStateText('dispatching'), '执行中');
assert.strictEqual(context.capabilityStateText('unknown'), '结果未知');

// Tool-call summary never invents entries from a malformed body.
assert.strictEqual(context.summarizeToolCalls(undefined), '无工具调用');
assert.strictEqual(context.summarizeToolCalls([]), '无工具调用');

process.stdout.write('capability ledger page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, (
        f"node failed:\n{completed.stdout}\n{completed.stderr}"
    )
    assert "capability ledger page behaviour ok" in completed.stdout
