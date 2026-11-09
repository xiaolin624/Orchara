"""F06/T048: the runs-page ledger shows connector target/risk and never secrets.

The structural assertions inspect what the browser receives from the compiled
API. ``runs.js`` is executed under Node with the same public helpers the page
uses, so the connector projection (safe target, risk label, replay of the parked
approval state) is observed behaviour rather than source text.

The DOM/browser boundary is the same as every other page ticket: ``not_run`` (no
browser driver); the helpers are pure and Node-tested.
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


def test_runs_page_has_the_connector_target_column(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="capability-panel"',
        "受控调用账本",
        "<th scope=\"col\">目标</th>",
        "F06/T048",
        "连接器",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    # The page never receives a credential or raw params from this ticket.
    assert "secret_ref" not in body
    assert "Authorization" not in body


def test_runs_js_has_the_connector_projection_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function capabilityRiskText(",
        "connectorTarget",
        "riskClass",
        "isConnector",
        "operation.targetText",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_connector_ledger_projection_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "connector-ledger-page.js"
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

// A real connector operation projection: kind/label, safe target, risk, state,
// attempts. The secret and the params body are simply not part of the contract.
const connector = context.describeCapabilityOperation({
  id: 'op-connector-1',
  capability_kind: 'connector',
  capability_version: '44444444-4444-4444-8444-444444444444',
  state: 'succeeded',
  attempts: 1,
  risk_class: 'read',
  connector_target: 'GET http://127.0.0.1:8080/v1',
  receipt_artifact_id: 'artifact-1',
  tool_call_summary: [],
  structured_output_keys: [],
  stub: false,
});
assert.strictEqual(connector.kindText, '连接器');
assert.strictEqual(connector.isConnector, true);
assert.strictEqual(connector.riskText, '读');
assert.strictEqual(connector.targetText, 'GET http://127.0.0.1:8080/v1');
assert.strictEqual(connector.stateText, '成功');
assert.strictEqual(connector.attemptsText, '1 次');
assert.ok(!JSON.stringify(connector).includes('secret'), JSON.stringify(connector));

// The parked write-risk state is visible as awaiting approval, not as success.
const parked = context.describeCapabilityOperation({
  id: 'op-connector-2',
  capability_kind: 'connector',
  capability_version: '55555555-5555-4555-8555-555555555555',
  state: 'awaiting_approval',
  attempts: 0,
  risk_class: 'write',
  connector_target: 'POST https://connector.example.test/v2',
  receipt_artifact_id: null,
  tool_call_summary: [],
  structured_output_keys: [],
  stub: false,
});
assert.strictEqual(parked.stateText, '待审批');
assert.strictEqual(parked.riskText, '写');
assert.strictEqual(parked.attemptsText, '0 次');

// Revoked/refused calls carry the stable code; nothing about the credential.
const refused = context.describeCapabilityOperation({
  id: 'op-connector-3',
  capability_kind: 'connector',
  capability_version: '66666666-6666-4666-8666-666666666666',
  state: 'failed',
  attempts: 1,
  risk_class: 'read',
  connector_target: '',
  error_code: 'connector_target_forbidden',
  receipt_artifact_id: null,
  tool_call_summary: [],
  structured_output_keys: [],
  stub: false,
});
assert.strictEqual(refused.errorText, '错误码 connector_target_forbidden');
assert.strictEqual(refused.targetText, '');

// A model operation keeps its existing projection (no invented connector data).
const model = context.describeCapabilityOperation({
  id: 'op-model',
  capability_kind: 'model',
  capability_version: '22222222-2222-4222-8222-222222222222',
  state: 'succeeded',
  attempts: 1,
  risk_class: 'pure',
  receipt_artifact_id: 'artifact-2',
  tool_call_summary: [],
  structured_output_keys: [],
  stub: true,
});
assert.strictEqual(model.isConnector, false);
assert.strictEqual(model.targetText, '');
assert.strictEqual(model.riskText, '无风险');

assert.strictEqual(context.capabilityRiskText('write'), '写');
assert.strictEqual(context.capabilityRiskText('read'), '读');

process.stdout.write('connector ledger page behaviour ok\n');
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
    assert "connector ledger page behaviour ok" in completed.stdout
