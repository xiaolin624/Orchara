"""F06/T045: the fallback facts in the runs-page capability ledger.

The structural assertions inspect what the browser actually receives from the
compiled API. ``runs.js`` is then executed under Node with the same public helper
functions the page calls, so the projection is observed behaviour rather than
source text: a switched call names the fallback profile, the closed reason and
the summed attempt cost/tokens, and a non-switched call shows neither.

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


def test_runs_js_has_the_fallback_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function capabilityFallbackReasonText(",
        "fallback_profile_version_id",
        "fallback_reason",
        "charged_amount",
        "attemptsDetailText",
        "fallbackText",
        "costText",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_runs_page_hint_documents_the_fallback_projection(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    assert "已切换到" in body
    assert "合计费用" in body
    assert "已切换到" not in response.headers.get("content-type", "")


def test_fallback_ledger_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "capability-fallback-page.js"
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
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

// The closed reason vocabulary is a label, never raw provider text.
assert.strictEqual(context.capabilityFallbackReasonText('availability'), '可用性故障');
assert.strictEqual(context.capabilityFallbackReasonText('rate_limited'), '限流');
assert.strictEqual(context.capabilityFallbackReasonText('adapter_unavailable'), '适配器不可用');
assert.strictEqual(context.capabilityFallbackReasonText(''), '');

// A switched model call: fallback profile + reason + summed attempt cost.
const switched = context.describeCapabilityOperation({
  id: 'op-fallback',
  capability_kind: 'model',
  capability_version: '22222222-2222-4222-8222-222222222222',
  state: 'succeeded',
  attempts: 4,
  error_code: null,
  receipt_artifact_id: 'artifact-1',
  tool_call_summary: [],
  structured_output_keys: [],
  stub: true,
  fallback_profile_version_id: '33333333-3333-4333-8333-333333333333',
  fallback_reason: 'availability',
  tokens_in: 111,
  tokens_out: 22,
  charged_amount: '0.00048000',
});
assert.strictEqual(switched.attempts, 4);
assert.strictEqual(switched.attemptsText, '4 次');
assert.ok(switched.attemptsDetailText.startsWith('4 次 · 费用 '), switched.attemptsDetailText);
assert.ok(switched.attemptsDetailText.includes('0.00048000'), switched.attemptsDetailText);
assert.ok(switched.attemptsDetailText.includes('令牌 111/22'), switched.attemptsDetailText);
assert.strictEqual(switched.fallbackProfileVersionID, '33333333-3333-4333-8333-333333333333');
assert.strictEqual(switched.fallbackReason, 'availability');
assert.strictEqual(switched.fallbackReasonText, '可用性故障');
assert.ok(switched.fallbackText.startsWith('已切换到 '), switched.fallbackText);
assert.ok(switched.fallbackText.includes('原因：可用性故障'), switched.fallbackText);
assert.ok(!switched.fallbackText.includes('param'), switched.fallbackText);

// A call that did not switch shows neither a fallback label nor a cost.
const primaryOnly = context.describeCapabilityOperation({
  id: 'op-primary',
  capability_kind: 'model',
  capability_version: '22222222-2222-4222-8222-222222222222',
  state: 'succeeded',
  attempts: 1,
  error_code: null,
  receipt_artifact_id: 'artifact-2',
  tool_call_summary: [],
  structured_output_keys: [],
  stub: true,
});
assert.strictEqual(primaryOnly.fallbackText, '');
assert.strictEqual(primaryOnly.costText, '');
assert.strictEqual(primaryOnly.attemptsDetailText, '1 次');
// The vm realm's Object prototype differs; compare the wire shape.
assert.strictEqual(
  JSON.stringify(primaryOnly.fallback),
  JSON.stringify({ profileVersionID: '', reason: '', reasonText: '', text: '' }),
);

// The ledger body projection keeps the fallback facts per item (the body
// carries raw ledger entries, so the helper describes each one).
const ledger = context.describeCapabilityLedger({
  items: [
    {
      id: 'op-fallback',
      capability_kind: 'model',
      capability_version: '22222222-2222-4222-8222-222222222222',
      state: 'succeeded',
      attempts: 4,
      fallback_profile_version_id: '33333333-3333-4333-8333-333333333333',
      fallback_reason: 'availability',
      tokens_in: 111,
      tokens_out: 22,
      charged_amount: '0.00048000',
    },
    {
      id: 'op-primary',
      capability_kind: 'model',
      capability_version: '22222222-2222-4222-8222-222222222222',
      state: 'succeeded',
      attempts: 1,
    },
  ],
});
assert.strictEqual(ledger.count, 2);
assert.strictEqual(ledger.items[0].fallbackReason, 'availability');
assert.strictEqual(ledger.items[1].fallbackReason, '');

process.stdout.write('capability fallback page behaviour ok\n');
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
    assert "capability fallback page behaviour ok" in completed.stdout
