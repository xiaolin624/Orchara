"""F06/T050: the approval page shows the real server proposal and no default.

Structural assertions inspect what the compiled API serves; ``approvals.js`` is
executed under Node with the same public helpers the page uses, so the proposal
projection, the exact decision body and the no-default rule are observed
behaviour rather than source text.

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


def test_approval_page_is_served_with_the_real_proposal_sections(f06_stack) -> None:
    response = f06_stack.server.http.get("/approvals")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="approval-detail"',
        'id="detail-target"',
        'id="detail-params"',
        'id="detail-destination"',
        'id="detail-expires"',
        'id="approve-button"',
        'id="reject-button"',
        "真实目标",
        "规范化参数",
        "数据去向",
        "没有默认选择",
        "不会撤销",
        "不会赋予额外权限",
        "发布审核是另一个",
    ):
        assert marker in body, f"approvals.html is missing {marker!r}"
    # No decision is preselected and no credential/raw provider payload is shipped.
    assert "checked" not in body
    assert "selected" not in body
    assert "secret_ref" not in body


def test_approval_script_is_served_with_the_expected_helpers(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/approvals.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function describeProposal(",
        "function describeApproval(",
        "function decisionPayload(",
        "function decisionConfirmationText(",
        "proposalContentPath",
        "Idempotency-Key",
    ):
        assert marker in body, f"approvals.js is missing {marker!r}"


def test_approval_page_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "approval-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} }, confirm: () => true } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/approvals.js', 'utf8'), context);

// One real contract Approval projection.
const pending = context.describeApproval({
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  run_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  state: 'pending',
  proposal_artifact_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  params_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  expires_at: '2026-11-04T12:00:00Z',
});
assert.strictEqual(pending.stateText, '待审批');
assert.strictEqual(pending.isPending, true);
assert.strictEqual(pending.paramsHashShort, '0123456789ab');
assert.strictEqual(context.approvalStateText('invalidated'), '已失效');

// The sealed server proposal: the page renders exactly these facts.
const proposal = context.describeProposal({
  approval_version: 1,
  run_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  operation_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd',
  logical_key: 'agent:node-1:tool:call-1',
  node_key: 'agent:node-1',
  capability_kind: 'connector',
  capability_version: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
  risk_class: 'write',
  target: {
    origin: 'https://connector.example.test',
    method: 'POST',
    path_prefix: '/v1',
    path: '/v1/orders',
    query: { region: 'cn' },
  },
  params: { path: '/v1/orders', body: { amount: 1250 } },
  params_sha256: '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef',
  data_destination: 'https://connector.example.test',
  policy_sha256: 'policy-sha',
  manifest_sha256: 'manifest-sha',
  snapshot_id: 'snapshot-id',
  snapshot_sha256: 'snapshot-sha',
  expires_at: '2026-11-04T12:00:00Z',
});
assert.strictEqual(
  proposal.targetText,
  'POST https://connector.example.test/v1/orders',
);
assert.strictEqual(proposal.queryText, 'region=cn');
assert.strictEqual(proposal.destinationText, 'https://connector.example.test');
assert.strictEqual(proposal.riskText, 'write');
assert.ok(proposal.paramsText.indexOf('"amount": 1250') >= 0, proposal.paramsText);
assert.ok(proposal.sourcesText.indexOf('snapshot-sha') >= 0, proposal.sourcesText);
assert.ok(proposal.sourcesText.indexOf('policy-sha') >= 0, proposal.sourcesText);
assert.ok(proposal.sourcesText.indexOf('manifest-sha') >= 0, proposal.sourcesText);
assert.strictEqual(proposal.expiresAt, '2026-11-04T12:00:00Z');

// A decision needs an explicit choice AND the displayed exact params hash.
assert.throws(() => context.decisionPayload(undefined, 'abc'));
assert.throws(() => context.decisionPayload('approve', ''));
const body = context.decisionPayload('approve', 'abc123');
assert.strictEqual(JSON.stringify(body), JSON.stringify({ decision: 'approve', params_sha256: 'abc123' }));
assert.throws(() => context.decisionPayload('reject', ''));
assert.strictEqual(
  JSON.stringify(context.decisionPayload('reject', 'abc123')),
  JSON.stringify({ decision: 'reject', params_sha256: 'abc123' }),
);
assert.ok(context.decisionConfirmationText('approve').indexOf('不会赋予额外权限') >= 0);
assert.ok(context.decisionConfirmationText('reject').indexOf('不会撤销') >= 0);

// Paths never interpolate a raw id without encoding.
assert.strictEqual(
  context.approvalPath('a/b'),
  '/api/v1/approvals/a%2Fb',
);
assert.strictEqual(
  context.approvalsListPath('c1', 20),
  '/api/v1/approvals?cursor=c1&limit=20',
);
assert.strictEqual(
  context.proposalContentPath('art/1'),
  '/api/v1/artifacts/art%2F1/content',
);

process.stdout.write('approval page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "approval page behaviour ok" in completed.stdout
