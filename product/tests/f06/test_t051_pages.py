"""F06/T051: the runs page shows the unknown state, the limitation and reconcile.

Structural assertions inspect what the browser receives from the compiled API.
``runs.js`` is executed under Node with the same public helpers the page uses, so
the unknown-write projection, the disclosure and the reconcile request/outcome are
observed behaviour rather than source text.

``not_run``: no browser driver, so the DOM listeners themselves are not clicked;
the pure helpers and the exact request/outcome shape are Node-tested.
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


def test_runs_page_has_the_unknown_and_reconcile_markers(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="capability-panel"',
        "F06/T051",
        "结果未知",
        "核对",
        "真实写次数",
        "幂等键",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    # The page never receives a credential or raw params from this ticket.
    assert "secret_ref" not in body
    assert "Authorization" not in body


def test_runs_js_has_the_reconciliation_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function reconcileOperationPath(",
        "function reconciliationLimitationText(",
        "function describeReconciliation(",
        "function describeReconcileRequest(",
        "function describeReconcileOutcome(",
        "function describeUnknownWrites(",
        "function submitReconciliation(",
        "no_idempotency_or_query_support",
        "/reconcile",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_unknown_operation_projection_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t051-page.js"
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

const operation = {
  id: '11111111-1111-4111-8111-111111111111',
  capability_kind: 'connector',
  capability_version: '22222222-2222-4222-8222-222222222222',
  risk_class: 'write',
  state: 'unknown',
  attempts: 1,
  requires_reconciliation: true,
  reconciliation_limitation: 'no_idempotency_or_query_support',
  provider_idempotency_key: null,
};

const described = context.describeCapabilityOperation(operation);
assert.strictEqual(described.requiresReconciliation, true);
assert.strictEqual(described.reconciled, false);
assert.strictEqual(described.stateText, '结果未知');
assert.strictEqual(described.writeCountText, '真实写次数 1（核对不增加）');
assert.ok(described.reconciliationLimitationText.includes('不会自动补发'));
assert.ok(described.reconciliationLimitationText.includes('幂等键'));

const summary = context.describeUnknownWrites({ items: [operation] });
assert.strictEqual(summary.count, 1);
assert.strictEqual(summary.empty, false);
assert.ok(summary.text.includes('结果未知'));

const request = context.describeReconcileRequest(
  operation.id,
  'confirmed_success',
  '33333333-3333-4333-8333-333333333333',
  'reconcile-key-1'
);
assert.strictEqual(
  request.path,
  '/api/v1/operations/11111111-1111-4111-8111-111111111111/reconcile'
);
assert.strictEqual(request.method, 'POST');
assert.strictEqual(request.headers['Idempotency-Key'], 'reconcile-key-1');
// Cross-realm objects: compare the JSON projection, not the prototype.`nassert.strictEqual(JSON.stringify(request.body), JSON.stringify({`n  conclusion: 'confirmed_success',`n  evidence_artifact_id: '33333333-3333-4333-8333-333333333333',`n}));

const before = context.describeCapabilityOperation(operation);
const after = context.describeCapabilityOperation({
  ...operation,
  state: 'succeeded',
  receipt_artifact_id: '33333333-3333-4333-8333-333333333333',
  requires_reconciliation: false,
  reconciled_by: '44444444-4444-4444-8444-444444444444',
  reconciliation_evidence_id: '33333333-3333-4333-8333-333333333333',
  reconciliation_conclusion: 'confirmed_success',
});
assert.strictEqual(after.requiresReconciliation, false);
assert.strictEqual(after.reconciled, true);
assert.strictEqual(after.reconciliationConclusionText, '已确认写入成功');
const outcome = context.describeReconcileOutcome(before, after);
assert.strictEqual(outcome.unchanged, true);
assert.ok(outcome.text.includes('未增加'), outcome.text);
assert.ok(outcome.text.includes('真实写次数仍为 1'), outcome.text);

const increased = context.describeReconcileOutcome(before, {
  ...after,
  attempts: 2,
});
assert.strictEqual(increased.unchanged, false);
assert.ok(increased.text.includes('异常'), increased.text);

const notSent = context.describeCapabilityOperation({
  ...operation,
  state: 'failed',
  requires_reconciliation: false,
  reconciliation_conclusion: 'confirmed_not_sent',
  reconciliation_evidence_id: '33333333-3333-4333-8333-333333333333',
});
assert.strictEqual(notSent.reconciled, true);
assert.strictEqual(notSent.reconciliationConclusionText, '已确认未发送');
assert.strictEqual(notSent.attempts, 1);

const failure = context.describeCapabilityOperation({
  ...operation,
  state: 'failed',
  requires_reconciliation: false,
  reconciliation_conclusion: 'confirmed_failure',
});
assert.strictEqual(failure.reconciliationConclusionText, '已确认写入失败');

console.log('t051 page helpers ok');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    result = subprocess.run(
        [node_binary, str(harness)],
        cwd=str(st.WEB_DIR.parent),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "t051 page helpers ok" in result.stdout
