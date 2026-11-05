"""F11/T073: the personal-data page's diagnostic-grant management.

Structural assertions inspect what the compiled API serves; ``personal-data.js``
is executed under Node with the same public helpers the page uses, so the request
shape (contract body + the documented non-contract object-scope header), the
purpose/expiry validation and the metadata projection are observed behaviour
rather than source text. The browser DOM boundary is ``not_run`` (no browser
driver), like every other page ticket.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st

NODE = shutil.which("node")
UUID = "00000000-0000-4000-8000-000000000001"
UUID_B = "00000000-0000-4000-8000-000000000002"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_personal_data_page_serves_the_diagnostic_grant_section(t069_stack) -> None:
    response = t069_stack.server.http.get("/personal-data")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="diagnostic-grants-card"',
        'id="grant-run-id"',
        'id="grant-load-objects"',
        'id="grant-objects-list"',
        'id="grant-grantee-id"',
        'id="grant-purposes"',
        'id="grant-expires"',
        'id="create-grant"',
        'id="grant-status"',
        'id="grants-list"',
        "不包含训练同意",
        "每次正文读取都会写审计",
        "到期或撤销后立即失效",
    ):
        assert marker in body, f"personal-data.html is missing {marker!r}"
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_personal_data_script_serves_the_t073_grant_helpers(t069_stack) -> None:
    response = t069_stack.server.http.get("/assets/personal-data.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function diagnosticGrantPath(",
        "function diagnosticObjectEntry(",
        "function diagnosticObjectScopeHeader(",
        "function diagnosticPurposes(",
        "function diagnosticExpiryISO(",
        "function diagnosticGrantStateText(",
        "function diagnosticReadRefusalText(",
        "function diagnosticGrantCreateRequest(",
        "function diagnosticGrantRevokeRequest(",
        "function describeDiagnosticGrant(",
        "function diagnosticGrantSummaryText(",
        "DIAGNOSTIC_GRANTS_PATH",
        "X-Diagnostic-Object-Scope",
        "textContent",
    ):
        assert marker in body, f"personal-data.js is missing {marker!r}"
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_diagnostic_grant_helpers_execute_under_node(
    node_binary: str, tmp_path
) -> None:
    harness = tmp_path / "personal-data-grants-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = {
  console,
  setTimeout: () => 0,
  clearTimeout: () => undefined,
  window: { location: { assign() {} }, confirm: () => true },
  document: {
    addEventListener() {},
    createElement: () => ({ appendChild() {}, setAttribute() {}, querySelectorAll: () => [] }),
    getElementById: () => null,
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/personal-data.js', 'utf8'), context);

const UUID = '00000000-0000-4000-8000-000000000001';
const UUID_B = '00000000-0000-4000-8000-000000000002';

// The object entry is always artifact:<lowercase uuid>; nothing else is accepted.
assert.strictEqual(context.diagnosticObjectEntry(' ' + UUID.toUpperCase() + ' '),
  'artifact:' + UUID);
assert.throws(() => context.diagnosticObjectEntry('not-a-uuid'));
assert.throws(() => context.diagnosticObjectEntry(''));
assert.strictEqual(
  context.diagnosticObjectScopeHeader([UUID, UUID_B]),
  'artifact:' + UUID + ',artifact:' + UUID_B,
);

// Purposes are lower-case tokens, trimmed and deduplicated; an invalid token is
// refused before any request is made.
assert.strictEqual(
  JSON.stringify(context.diagnosticPurposes('troubleshooting, quality_review ,troubleshooting,')),
  JSON.stringify(['troubleshooting', 'quality_review']),
);
assert.strictEqual(
  JSON.stringify(context.diagnosticPurposes(['incident_review'])),
  JSON.stringify(['incident_review']),
);
for (const raw of ['', '   ', 'Troubleshooting', 'has space', 'has.dot']) {
  assert.throws(() => context.diagnosticPurposes(raw), raw);
}

// The datetime-local value becomes one RFC3339 UTC instant.
const expiry = context.diagnosticExpiryISO('2099-01-01T00:00');
assert.ok(/Z$/.test(expiry), expiry);
assert.ok(Date.parse(expiry) > Date.now(), expiry);
for (const raw of ['', 'not-a-date']) {
  assert.throws(() => context.diagnosticExpiryISO(raw), raw);
}

// The create request is the contract body + the documented non-contract header
// for the explicit object snapshot. No content-ish field exists.
const request = context.diagnosticGrantCreateRequest({
  runID: UUID,
  granteeID: UUID_B,
  purposes: 'troubleshooting',
  expiresAt: '2099-01-01T00:00',
  objects: [UUID],
});
assert.strictEqual(request.path, '/api/v1/diagnostic-grants');
assert.strictEqual(request.method, 'POST');
assert.strictEqual(JSON.stringify(Object.keys(request.body).sort()),
  JSON.stringify(['expires_at', 'grantee_id', 'purposes', 'run_id']));
assert.strictEqual(JSON.stringify(Object.keys(request.headers)),
  JSON.stringify(['X-Diagnostic-Object-Scope']));
assert.strictEqual(request.headers['X-Diagnostic-Object-Scope'], 'artifact:' + UUID);
assert.strictEqual(request.body.run_id, UUID);
assert.strictEqual(request.body.grantee_id, UUID_B);
assert.strictEqual(JSON.stringify(request.body.purposes),
  JSON.stringify(['troubleshooting']));

// An absent object selection is an absent header (the server then derives the
// run's current snapshot once); it is never sent as a blank value.
const derived = context.diagnosticGrantCreateRequest({
  runID: UUID, granteeID: UUID_B, purposes: 'troubleshooting',
  expiresAt: '2099-01-01T00:00', objects: [],
});
assert.strictEqual(JSON.stringify(derived.headers), JSON.stringify({}));
assert.throws(() => context.diagnosticGrantCreateRequest({
  runID: 'bad', granteeID: UUID_B, purposes: 'troubleshooting', expiresAt: '2099-01-01T00:00',
}));

// Revocation targets the grant id, never a resource id.
const revoke = context.diagnosticGrantRevokeRequest(UUID);
assert.strictEqual(revoke.path, '/api/v1/diagnostic-grants/' + UUID);
assert.strictEqual(revoke.method, 'DELETE');
assert.throws(() => context.diagnosticGrantRevokeRequest('nope'));

// The grant projection is metadata only: id/state/run/purposes/objects/expiry.
const grant = {
  id: UUID,
  state: 'active',
  run_id: UUID_B,
  purposes: ['troubleshooting', 'quality_review'],
  object_scope: ['artifact:' + UUID],
  expires_at: '2099-01-01T00:00:00Z',
  revoked_at: null,
};
const view = context.describeDiagnosticGrant(grant);
assert.strictEqual(view.id, UUID);
assert.strictEqual(view.stateText, '有效');
assert.strictEqual(view.runID, UUID_B);
assert.strictEqual(view.purposeText, 'troubleshooting、quality_review');
assert.strictEqual(view.objectCount, 1);
assert.strictEqual(view.isActive, true);
assert.strictEqual(view.revokedAt, '');
assert.ok(context.diagnosticGrantSummaryText(grant).indexOf(UUID) === 0
  || context.diagnosticGrantSummaryText(grant).indexOf('诊断授权') === 0);
const summary = context.diagnosticGrantSummaryText(grant);
assert.ok(summary.indexOf('有效') >= 0, summary);
assert.ok(summary.indexOf('对象 1 个') >= 0, summary);
assert.throws(() => context.describeDiagnosticGrant(null));
assert.throws(() => context.describeDiagnosticGrant({ id: UUID }));
assert.strictEqual(context.describeDiagnosticGrant({ id: UUID, state: 'weird' }).stateText, '未知状态');
assert.strictEqual(context.describeDiagnosticGrant({ id: UUID, state: 'revoked' }).isActive, false);

// State vocabulary is closed; refusal text is distinct per server code.
assert.strictEqual(context.diagnosticGrantStateText('active'), '有效');
assert.strictEqual(context.diagnosticGrantStateText('expired'), '已过期');
assert.strictEqual(context.diagnosticGrantStateText('revoked'), '已撤销');
assert.strictEqual(context.diagnosticGrantStateText('weird'), '未知状态');
const revokedText = context.diagnosticReadRefusalText('diagnostic_grant_revoked');
const expiredText = context.diagnosticReadRefusalText('diagnostic_grant_expired');
const scopeText = context.diagnosticReadRefusalText('diagnostic_scope_denied');
const purposeText = context.diagnosticReadRefusalText('diagnostic_purpose_denied');
assert.notStrictEqual(revokedText, expiredText);
assert.notStrictEqual(scopeText, purposeText);
assert.ok(revokedText.indexOf('撤销') >= 0, revokedText);
assert.ok(expiredText.indexOf('过期') >= 0, expiredText);
assert.strictEqual(context.diagnosticReadRefusalText('unknown'), '诊断读取被拒绝。');
"""
        .replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        cwd=str(st.PRODUCT_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == "", completed.stderr
