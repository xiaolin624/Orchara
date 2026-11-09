"""T098 (GitHub #99) page acceptance: mobile journeys, conflict/expiry/no-result copy.

The browser-facing half of T098 is verified the way this repository already
verifies page logic: Node executes the **committed** page bytes against a minimal
DOM/fetch shim, and the static structure of the committed HTML/CSS is asserted
directly. This is **not** Playwright and **not** a real browser DOM: CSS pixel
layout, media-query evaluation and real viewport reflow are ``not_run`` (see
``product/reports/f12/T098-mobile-and-personal-data.md``).

The tests in this file are the page half; ``tests/f12/test_t098_stack.py`` holds
the real HTTP/PostgreSQL half.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st

WEB = st.WEB_DIR
NODE = shutil.which("node")

# The four phone journeys of the ticket plus the two pages that share the same
# responsive surface (personal data shows documents and memories; knowledge shows
# ingestion state and citation reopening).
MOBILE_PAGES = {
    "chat.html": "page-chat",
    "approvals.html": "page-approvals",
    "runs.html": "page-runs",
    "personal-data.html": "page-personal-data",
    "memories.html": "page-memories",
    "knowledge.html": "page-knowledge",
}

# Pages that must state the ticket's boundary: small screens are supported for
# the conversation/approval/result paths, not for full canvas editing.
SCOPE_BOUNDARY_PAGES = ("chat.html", "runs.html", "approvals.html", "editor.html", "debug.html")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the committed page bytes cannot be executed")
    return NODE


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_mobile_stylesheet_is_linked_after_each_pages_own_style() -> None:
    css_path = WEB / "assets" / "mobile.css"
    assert css_path.exists(), "the shared mobile stylesheet is missing"
    css = css_path.read_text(encoding="utf-8")

    # The breakpoint and the touch target are the two commitments the ticket
    # makes; the exact pixel result still needs a browser (not_run).
    assert "@media (max-width: 720px)" in css
    assert "min-height: 44px" in css
    assert ".table-scroll" in css and "overflow-x: auto" in css
    assert "flex-wrap: wrap" in css
    # A phone-safe form control: full width and 16px so iOS does not zoom on focus.
    assert "font-size: 16px" in css

    for name, body_class in MOBILE_PAGES.items():
        html = read_page(name)
        assert '<meta name="viewport"' in html, f"{name} lost its viewport meta"
        assert 'href="/assets/mobile.css"' in html, f"{name} does not load the mobile stylesheet"
        # The shared file must come after the page's own <style> block; otherwise
        # a page-local rule would win at equal specificity. chat.html has no local
        # <style> (it loads the editor's canvas.css), so there the shared file
        # follows that stylesheet instead.
        if "</style>" in html:
            assert html.index("</style>") < html.index('href="/assets/mobile.css"'), name
        else:
            assert html.index('href="/assets/canvas.css"') < html.index(
                'href="/assets/mobile.css"'
            ), name
        assert f'class="{body_class}"' in html, f"{name} does not declare its page class"


def test_the_wide_ledger_table_scrolls_instead_of_overflowing_the_phone() -> None:
    html = read_page("runs.html")
    table = html.index("<table>")
    wrapper = html.rindex('<div class="table-scroll"', 0, table)
    assert wrapper < table
    # The region is keyboard-focusable and named for assistive technology.
    region = html[wrapper:table]
    assert 'role="region"' in region
    assert "tabindex=" in region
    assert "aria-label=" in region
    assert html.index("</table>") < html.index("</div>", html.index("</table>"))


def test_each_phone_journey_state_that_small_screens_keep_is_named_in_the_page() -> None:
    # A page-level declaration of the ticket boundary, not a CSS promise.
    for name in SCOPE_BOUNDARY_PAGES:
        html = read_page(name)
        assert 'data-mobile-scope="no-full-canvas-editing"' in html, name
        note = html[html.index('data-mobile-scope="no-full-canvas-editing"'):]
        note = note[: note.index("</p>")]
        assert "画布" in note and ("不承诺" in note or "不在" in note), (name, note)


def run_node(node_binary: str, tmp_path, name: str, script: str) -> str:
    harness = tmp_path / (name + ".js")
    harness.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return completed.stdout


APPROVAL_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const CAPABILITY_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const NODE_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const EXPIRED_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const RUN_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';
const PROPOSAL_CAP = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee';
const PROPOSAL_NODE = 'ffffffff-ffff-4fff-8fff-ffffffffffff';
const PROPOSAL_EXPIRED = '99999999-9999-4999-8999-999999999999';
const HASH = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.dataset = {};
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}
const ids = [
  'approvals-status', 'approvals-list', 'approvals-empty', 'approvals-next',
  'approval-detail', 'detail-id', 'detail-run', 'detail-state', 'detail-params-sha',
  'detail-expires', 'detail-target', 'detail-risk', 'detail-params', 'detail-destination',
  'detail-sources', 'detail-proposal-sha', 'approval-expiry-note', 'approve-button',
  'reject-button', 'decision-status',
];
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
};

const calls = [];
let listItems = [{ id: CAPABILITY_ID, run_id: RUN_ID, state: 'pending',
  proposal_artifact_id: PROPOSAL_CAP, params_sha256: HASH,
  expires_at: '2026-11-04T12:00:00Z' }];
let detailByID = {};
let proposalByID = {};
let decisionError = null;

function approval(id, state, proposalID) {
  return { id: id, run_id: RUN_ID, state: state, proposal_artifact_id: proposalID || PROPOSAL_CAP,
    params_sha256: HASH, expires_at: '2026-11-04T12:00:00Z' };
}
function capabilityProposal() {
  return { kind: 'capability_proposal', operation_id: PROPOSAL_CAP, node_key: 'agent:node-1',
    target: { origin: 'https://connector.example.test', method: 'POST', path: '/v1/orders' },
    params: { path: '/v1/orders' }, params_sha256: HASH,
    data_destination: 'https://connector.example.test', risk_class: 'write',
    snapshot_id: 'snapshot-id', snapshot_sha256: 'snapshot-sha' };
}
function nodeProposal() {
  return { kind: 'node_approval', title: 'Approve the order', node_id: 'approve1',
    node_key: 'node:approve1', run_id: RUN_ID, proposal: { amount: '1250.00' },
    params_sha256: HASH, expires_at: '2026-11-04T12:00:00Z' };
}

detailByID[CAPABILITY_ID] = approval(CAPABILITY_ID, 'pending', PROPOSAL_CAP);
proposalByID[PROPOSAL_CAP] = capabilityProposal();
detailByID[NODE_ID] = approval(NODE_ID, 'pending', PROPOSAL_NODE);
proposalByID[PROPOSAL_NODE] = nodeProposal();
detailByID[EXPIRED_ID] = approval(EXPIRED_ID, 'expired', PROPOSAL_EXPIRED);
proposalByID[PROPOSAL_EXPIRED] = nodeProposal();

async function api(path, options) {
  calls.push({ path: path, options: options || {} });
  if (path.indexOf('/api/v1/approvals?') === 0 || path === '/api/v1/approvals') {
    return { items: listItems, next_cursor: null };
  }
  const decision = path.match(/^\/api\/v1\/approvals\/([^/]+)\/decision$/);
  if (decision) {
    if (decisionError) throw decisionError;
    return approval(decision[1], 'approved');
  }
  const detail = path.match(/^\/api\/v1\/approvals\/([^/]+)$/);
  if (detail) return detailByID[detail[1]] || approval(detail[1], 'pending');
  return {};
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  crypto: require('crypto'),
  fetch: async (path) => {
    calls.push({ path: path, fetch: true });
    const id = path.match(/\/artifacts\/([^/]+)\/content$/)[1];
    return { ok: true, text: async () => JSON.stringify(proposalByID[id] || nodeProposal()) };
  },
  setTimeout: (fn) => { return 1; },
  clearTimeout: () => {},
};

vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// app.js declares its own fetch-backed api(); the page test replaces it with the
// recording stub so the real page bytes are driven by the fixture responses.
context.api = api;
context.csrf = async () => 'csrf-token';
vm.runInContext(fs.readFileSync(webDir + '/assets/approvals.js', 'utf8'), context);

async function flush() { for (let i = 0; i < 12; i += 1) await new Promise((r) => setImmediate(r)); }
function textOK(id, needle, label) {
  const element = byId.get(id);
  assert.ok(element.textContent.indexOf(needle) >= 0,
    (label || id) + ' is missing ' + needle + ': ' + element.textContent);
}

(async () => {
  await flush();
  assert.strictEqual(byId.get('approvals-list').children.length, 1, 'the approval list did not render');

  // --- capability approval: lazy decision-path expiry, not a sweeper ---------
  await context.loadApprovalDetail(CAPABILITY_ID);
  await flush();
  const note = byId.get('approval-expiry-note');
  assert.strictEqual(note.hidden, false, 'the capability expiry note is hidden');
  assert.strictEqual(note.dataset.expiryKind, 'capability', note.dataset.expiryKind);
  assert.strictEqual(note.dataset.expiryState, 'pending', note.dataset.expiryState);
  assert.ok(note.textContent.indexOf('没有后台过期扫描') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('approval_expired') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('不能复活') >= 0, note.textContent);
  assert.strictEqual(byId.get('approve-button').disabled, false, 'a pending approval must be decidable');

  // A real 409 approval_expired: the page says expired, offers the next step,
  // disables both buttons and re-reads the server state (no silent retry).
  detailByID[CAPABILITY_ID] = approval(CAPABILITY_ID, 'expired');
  decisionError = Object.assign(new Error('the approval deadline passed'), {
    status: 409, code: 'approval_expired',
  });
  const before = calls.filter((call) => call.path.endsWith('/decision')).length;
  await context.decideApproval('approve');
  await flush();
  const after = calls.filter((call) => call.path.endsWith('/decision')).length;
  assert.strictEqual(after - before, 1, 'a refused decision must be sent exactly once');
  textOK('decision-status', 'approval_expired');
  textOK('decision-status', '过期');
  textOK('decision-status', '重新提交');
  textOK('decision-status', '不提供复活');
  assert.strictEqual(byId.get('approve-button').disabled, true, 'expired approval must stay undecidable');
  assert.strictEqual(byId.get('reject-button').disabled, true, 'expired approval must stay undecidable');
  assert.strictEqual(note.dataset.expiryState, 'expired', 'the page did not re-read the server state');
  assert.ok(note.textContent.indexOf('已过期') >= 0, note.textContent);
  const retries = calls.filter((call) => call.path.endsWith('/decision')).length;
  assert.strictEqual(retries, 1, 'the page retried the decision behind the user');

  // --- node approval: the server sweeper owns the expiry --------------------
  decisionError = null;
  await context.loadApprovalDetail(NODE_ID);
  await flush();
  assert.strictEqual(note.dataset.expiryKind, 'node', note.dataset.expiryKind);
  assert.ok(note.textContent.indexOf('sweeper') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('30 秒') >= 0, note.textContent);
  assert.ok(note.textContent.indexOf('approval_expired') >= 0, note.textContent);

  // --- an already expired approval never offers a decision -----------------
  await context.loadApprovalDetail(EXPIRED_ID);
  await flush();
  assert.strictEqual(byId.get('approve-button').disabled, true);
  assert.strictEqual(byId.get('reject-button').disabled, true);
  assert.strictEqual(note.dataset.expiryState, 'expired', note.dataset.expiryState);
  assert.ok(note.textContent.indexOf('已过期') >= 0, note.textContent);

  // --- pure rules -----------------------------------------------------------
  const mismatch = context.describeDecisionFailure({
    status: 409, code: 'approval_params_mismatch', message: 'changed',
  });
  assert.strictEqual(mismatch.reload, true);
  assert.ok(mismatch.text.indexOf('重新打开详情') >= 0, mismatch.text);
  assert.ok(mismatch.text.indexOf('不要照旧点确认') >= 0, mismatch.text);
  assert.strictEqual(context.approvalExpiryKind(undefined), 'capability');
  assert.strictEqual(context.approvalExpiryKind('node_approval'), 'node');
  const expiredNote = context.approvalExpiryNote({ state: 'expired' }, 'capability');
  assert.strictEqual(expiredNote.visible, true);
  assert.strictEqual(expiredNote.tone, 'bad');
  assert.ok(expiredNote.text.indexOf('不能复活') >= 0, expiredNote.text);
  assert.strictEqual(context.approvalExpiryNote({ state: 'approved' }).visible, false);
  process.stdout.write('approval expiry ok\n');
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; });
"""


def test_the_approval_page_explains_both_expiry_mechanisms_and_never_revives(
    node_binary: str, tmp_path
) -> None:
    out = run_node(
        node_binary,
        tmp_path,
        "t098-approval-expiry",
        APPROVAL_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
    )
    assert "approval expiry ok" in out


CHAT_FAILURE_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.textContent = '';
    this.className = '';
    this.hidden = true;
    this.dataset = {};
    this.children = [];
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
}
const errorElement = new El('p', 'chat-request-error');
const document = {
  // No #chat-panel: initChat returns before wiring the page, so this harness
  // drives the committed pure helpers and the real failure block only.
  getElementById(id) { return id === 'chat-request-error' ? errorElement : null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
};
const context = {
  console: console,
  document: document,
  window: { location: { assign() {} }, confirm: () => true },
};
vm.createContext(context);
context.setTimeout = (fn) => 1;
context.clearTimeout = () => {};
// C6.01 (#155): the real shared run-state bytes load before chat.js.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/chat.js', 'utf8'), context);

// --- 409 idempotency_conflict: same key + different body --------------------
const conflict = context.describeSubmitFailure({
  status: 409, code: 'idempotency_conflict', message: 'the key already names another body',
});
assert.strictEqual(conflict.reuseKey, false, 'a conflicting key must not be replayed');
assert.strictEqual(conflict.retryable, false);
assert.ok(conflict.text.indexOf('idempotency_conflict') >= 0, conflict.text);
assert.ok(conflict.text.indexOf('新任务') >= 0, conflict.text);
assert.ok(conflict.text.indexOf('旧键') >= 0, conflict.text);

// --- a network failure is the one safe retry: same key ----------------------
const network = context.describeSubmitFailure({ code: 'network_error', message: 'offline' });
assert.strictEqual(network.reuseKey, true);
assert.ok(network.text.indexOf('同一幂等键') >= 0, network.text);

// --- a closed interaction is explained, never retried silently --------------
const closed = context.describeReplyFailure({
  status: 409, code: 'interaction_closed', message: 'already closed',
});
assert.strictEqual(closed.retryable, false);
assert.ok(closed.text.indexOf('interaction_closed') >= 0, closed.text);
assert.ok(closed.text.indexOf('旧答复不会复活任务') >= 0, closed.text);
assert.ok(closed.text.indexOf('重新提交需求') >= 0, closed.text);

// --- no result: an empty match is a state with a next step, not "loading" ---
const noMatch = context.applyRequestFailure(document, {
  state: 'failed', error_code: 'no_matching_application',
});
assert.strictEqual(noMatch.visible, true);
assert.strictEqual(errorElement.hidden, false);
assert.strictEqual(errorElement.dataset.errorCode, 'no_matching_application');
assert.ok(errorElement.textContent.indexOf('未找到匹配的应用') >= 0, errorElement.textContent);
assert.ok(errorElement.textContent.indexOf('应用目录') >= 0, errorElement.textContent);

const expired = context.applyRequestFailure(document, {
  state: 'failed', error_code: 'interaction_expired',
});
assert.strictEqual(expired.visible, true);
assert.ok(errorElement.textContent.indexOf('过期') >= 0, errorElement.textContent);
assert.ok(errorElement.textContent.indexOf('重新提交') >= 0, errorElement.textContent);
assert.strictEqual(errorElement.dataset.retryable, 'false');

// A non-failed request hides the block: no stale failure next to a live queue.
context.applyRequestFailure(document, { state: 'dispatched', error_code: null });
assert.strictEqual(errorElement.hidden, true);
process.stdout.write('chat failure ok\n');
"""


def test_chat_reports_conflict_expiry_and_no_result_with_a_next_step(
    node_binary: str, tmp_path
) -> None:
    out = run_node(
        node_binary,
        tmp_path,
        "t098-chat-failure",
        CHAT_FAILURE_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
    )
    assert "chat failure ok" in out


MEMORY_CANDIDATE_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const APP_ID = '11111111-1111-4111-8111-111111111111';
const CANDIDATE_ID = '22222222-2222-4222-8222-222222222222';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.dataset = {};
    this.onclick = null;
    this.rows = 0;
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  click() {
    if (typeof this.onclick === 'function') this.onclick({ preventDefault() {} });
    for (const fn of this._listeners.click || []) fn({ preventDefault() {} });
  }
  descendants() {
    const out = [];
    const walk = (node) => { for (const child of node.children || []) { out.push(child); walk(child); } };
    walk(this);
    return out;
  }
}
const ids = [
  'candidates-list', 'candidates-status', 'candidates-empty', 'candidates-next',
  'decision-status', 'memories-list', 'memories-status', 'memories-empty',
  'memories-next', 'memory-action-status', 'memory-conflict-retry',
  'deletion-status', 'deletion-card',
];
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
};

const candidate = {
  id: CANDIDATE_ID, application_id: APP_ID,
  text: 'The user prefers quarterly reports.',
  state: 'pending', expires_at: '2026-12-01T00:00:00Z',
};
let candidateItems = [candidate];
let decisionError = null;
const calls = [];

async function api(path, options) {
  calls.push({ path: path, options: options || {} });
  if (path.indexOf('/api/v1/memory-candidates?') === 0 || path === '/api/v1/memory-candidates') {
    return { items: candidateItems, next_cursor: null };
  }
  if (path.match(/^\/api\/v1\/memory-candidates\/[^/]+\/decision$/)) {
    if (decisionError) throw decisionError;
    return { candidate_id: CANDIDATE_ID, state: 'confirmed', memory_id: '33333333-3333-4333-8333-333333333333' };
  }
  if (path.indexOf('/api/v1/memories') === 0) {
    return { items: [], next_cursor: null };
  }
  return {};
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  crypto: require('crypto'),
  api: api,
  csrf: async () => 'csrf-token',
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: (fn) => 1,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
context.api = api;
context.csrf = async () => 'csrf-token';
context.requireLogin = async () => ({ roles: ['user'] });
vm.runInContext(fs.readFileSync(webDir + '/assets/memories.js', 'utf8'), context);

async function flush() { for (let i = 0; i < 16; i += 1) await new Promise((r) => setImmediate(r)); }
function textOf(element) {
  const parts = [element.textContent || ''];
  for (const child of element.descendants ? element.descendants() : []) parts.push(child.textContent || '');
  return parts.join('\n');
}
function findButton(root, needle) {
  return root.descendants().find(
    (el) => el.tagName === 'BUTTON' && String(el.textContent).indexOf(needle) >= 0,
  );
}

(async () => {
  await flush();
  const list = byId.get('candidates-list');
  assert.strictEqual(list.children.length, 1, 'the candidate card did not render');
  const card = list.children[0];

  // --- provenance + save scope are explicit; the interaction is sealed -------
  assert.strictEqual(card.dataset.selection, 'none', 'a candidate defaults to a decision');
  const text = textOf(card);
  assert.ok(text.indexOf('来源：Run 内自动抽取') >= 0, text);
  assert.ok(text.indexOf('交互（Run）：服务端封存') >= 0, text);
  assert.ok(text.indexOf('保存范围：仅当前使用者 + 应用 ' + APP_ID) >= 0, text);
  assert.ok(text.indexOf('确认前不可召回') >= 0, text);

  // --- no default selection: no checked control, no batch "select all" ------
  const inputs = card.descendants().filter((el) => el.tagName === 'INPUT');
  assert.ok(inputs.every((input) => input.checked !== true), 'a candidate control was pre-checked');
  assert.strictEqual(inputs.filter((input) => input.type === 'checkbox').length, 0,
    'the candidate card exposes a batch checkbox');
  const decisionsBefore = calls.filter((call) => /\/decision$/.test(call.path)).length;
  assert.strictEqual(decisionsBefore, 0, 'a decision was sent before the user clicked');

  // --- an explicit click sends exactly one decision with a fresh key --------
  const confirm = findButton(card, '确认并保存为记忆');
  assert.ok(confirm, 'the pending candidate has no confirm action');
  confirm.click();
  await flush();
  const decisions = calls.filter((call) => /\/decision$/.test(call.path));
  assert.strictEqual(decisions.length, 1, JSON.stringify(calls.map((call) => call.path)));
  assert.strictEqual(JSON.stringify(decisions[0].options.body), JSON.stringify({ decision: 'confirm' }));
  assert.ok(decisions[0].options.headers['Idempotency-Key'], 'the decision carried no Idempotency-Key');

  // --- a real 409 expiry is explained; nothing is saved and nothing revives --
  decisionError = Object.assign(new Error('the candidate expired'), {
    status: 409, code: 'memory_candidate_expired',
  });
  confirm.click();
  await flush();
  const decisionStatus = byId.get('decision-status');
  assert.ok(decisionStatus.textContent.indexOf('memory_candidate_expired') >= 0, decisionStatus.textContent);
  assert.ok(decisionStatus.textContent.indexOf('没有保存任何记忆') >= 0, decisionStatus.textContent);
  assert.ok(decisionStatus.textContent.indexOf('重新运行') >= 0, decisionStatus.textContent);
  assert.ok(decisionStatus.textContent.indexOf('不能复活') >= 0, decisionStatus.textContent);
  assert.strictEqual(calls.filter((call) => /\/decision$/.test(call.path)).length, 2,
    'the page retried a refused decision');

  // --- an expired candidate is not shown as decidable -----------------------
  const expired = context.describeCandidate(Object.assign({}, candidate, { state: 'expired' }));
  assert.strictEqual(expired.isPending, false);
  assert.strictEqual(expired.defaultDecision, 'none');
  assert.strictEqual(expired.stateText, '已过期');

  // --- other refusals keep their own next step ------------------------------
  const confirmed = context.decisionFailurePlan({ status: 409, code: 'memory_candidate_confirmed' });
  assert.ok(confirmed.text.indexOf('不会写第二条') >= 0, confirmed.text);
  const conflict = context.decisionFailurePlan({ status: 409, code: 'idempotency_conflict' });
  assert.ok(conflict.text.indexOf('旧键覆盖') >= 0, conflict.text);
  assert.strictEqual(context.decisionFailurePlan({ status: 500, code: 'internal_error' }).refresh, true);
  process.stdout.write('memory candidate ok\n');
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; });
"""


def test_memory_candidates_show_source_and_save_scope_and_default_to_no_choice(
    node_binary: str, tmp_path
) -> None:
    out = run_node(
        node_binary,
        tmp_path,
        "t098-memory-candidate",
        MEMORY_CANDIDATE_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
    )
    assert "memory candidate ok" in out


PERSONAL_DATA_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const KB_ID = '11111111-1111-4111-8111-111111111111';
const DOC_READY = '22222222-2222-4222-8222-222222222222';
const DOC_FAILED = '33333333-3333-4333-8333-333333333333';
const DOC_DELETED = '44444444-4444-4444-8444-444444444444';
const ARTIFACT_A = '55555555-5555-4555-8555-555555555555';
const ARTIFACT_B = '66666666-6666-4666-8666-666666666666';
const DELETION_ID = '77777777-7777-4777-8777-777777777777';
const MEMORY_ID = '88888888-8888-4888-8888-888888888888';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.type = '';
    this.dataset = {};
    this.rows = 0;
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    const own = this._text || '';
    return own + this.children.map((child) => child.textContent || '').join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  descendants() {
    const out = [];
    const walk = (node) => { for (const child of node.children || []) { out.push(child); walk(child); } };
    walk(this);
    return out;
  }
  querySelectorAll(selector) {
    if (selector === 'input[type="checkbox"]') {
      return this.descendants().filter((el) => el.tagName === 'INPUT' && el.type === 'checkbox');
    }
    return [];
  }
}
const ids = [
  'memories-status', 'memories-list', 'memories-empty',
  'documents-status', 'documents-list', 'documents-empty',
  'grant-run-id', 'grant-load-objects', 'grant-objects-status', 'grant-objects-list',
  'grant-grantee-id', 'grant-purposes', 'grant-expires', 'create-grant', 'grant-status',
  'grants-list', 'grants-empty', 'deletion-card', 'deletion-status',
  'deletion-refresh-status', 'deletion-refresh', 'action-status',
  'delete-artifact', 'artifact-id', 'diagnostic-grants-card',
];
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const readyHandlers = [];
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  addEventListener(type, fn) { if (type === 'DOMContentLoaded') readyHandlers.push(fn); },
};

const documents = [
  { id: DOC_READY, knowledge_base_id: KB_ID, state: 'ready', revision: 1 },
  { id: DOC_FAILED, knowledge_base_id: KB_ID, state: 'failed', revision: 1 },
  { id: DOC_DELETED, knowledge_base_id: KB_ID, state: 'deleted', revision: 2 },
];
const calls = [];
async function api(path, options) {
  calls.push({ path: path, options: options || {} });
  if (path === '/api/v1/memories' || path.indexOf('/api/v1/memories?') === 0) {
    return { items: [], next_cursor: null };
  }
  if (path === '/api/v1/knowledge-bases' || path.indexOf('/api/v1/knowledge-bases?') === 0) {
    return { items: [{ id: KB_ID, name: 't098 knowledge' }], next_cursor: null };
  }
  if (path.indexOf('/api/v1/knowledge-bases/' + KB_ID + '/documents') === 0) {
    return { items: documents, next_cursor: null };
  }
  if (path.indexOf('/api/v1/knowledge-documents/' + DOC_FAILED + '/diagnostics') === 0) {
    return { state: 'failed', error_code: 'ingest_empty_content', detail: { part: 'body' } };
  }
  if (path === '/api/v1/diagnostic-grants') return { items: [], next_cursor: null };
  return {};
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  api: api,
  csrf: async () => 'csrf-token',
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: (fn) => 1,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
context.api = api;
context.csrf = async () => 'csrf-token';
context.requireLogin = async () => ({ roles: ['user'] });
vm.runInContext(fs.readFileSync(webDir + '/assets/personal-data.js', 'utf8'), context);

  async function flush() { for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r)); }
function textOf(element) { return element.textContent || ''; }
function byState(state) {
  return byId.get('documents-list').children.find(
    (entry) => entry.dataset.documentState === state,
  );
}

(async () => {
  for (const handler of readyHandlers) handler();
  await flush();

  // --- every contract document state is named; failed shows the real code ----
  const labels = {
    uploaded: '已上传', scanning: '扫描中', indexing: '索引构建中', ready: '可检索',
    failed: '摄取失败', deleting: '删除中', deleted: '已删除',
  };
  for (const state of Object.keys(labels)) {
    const readout = context.documentReadout({
      id: '99999999-9999-4999-8999-999999999999', state: state, revision: 1,
    });
    assert.ok(readout.stateText.indexOf(labels[state]) >= 0, state + ' -> ' + readout.stateText);
    assert.ok(readout.note.length > 0, state + ' has no explanation');
    if (state === 'failed') {
      assert.strictEqual(readout.needsDiagnostics, true);
      assert.ok(readout.nextStep.indexOf('重试摄取') >= 0, readout.nextStep);
    }
    if (state === 'deleted' || state === 'deleting') {
      assert.strictEqual(readout.canDelete, false, state + ' must not offer another delete');
      assert.ok(readout.note.indexOf('不可访问') >= 0 || readout.note.indexOf('停止') >= 0,
        readout.note);
    }
  }
  assert.ok(textOf(byState('ready')).indexOf('可检索') >= 0, textOf(byState('ready')));
  const failed = byState('failed');
  assert.ok(textOf(failed).indexOf('摄取失败') >= 0, textOf(failed));
  assert.ok(textOf(failed).indexOf('ingest_empty_content') >= 0, textOf(failed));
  assert.ok(textOf(failed).indexOf('解析结果为空') >= 0, textOf(failed));
  assert.ok(textOf(byState('deleted')).indexOf('已删除') >= 0, textOf(byState('deleted')));

  // --- diagnostic object scopes default to no selection ---------------------
  context.renderGrantObjects([
    { id: ARTIFACT_A, state: 'ready' },
    { id: ARTIFACT_B, state: 'ready' },
  ]);
  const grantList = byId.get('grant-objects-list');
  const boxes = grantList.querySelectorAll('input[type="checkbox"]');
  assert.strictEqual(boxes.length, 2, 'the grant objects were not listed');
  assert.ok(boxes.every((box) => box.checked === false), 'an object was pre-selected');
  assert.strictEqual(JSON.stringify(context.selectedGrantObjects()), '[]',
    'the page would grant untouched objects');
  context.setStatus('grant-objects-status', '已列出 2 个对象，默认不勾选；请逐个选择要授权的范围。', 'ok');
  assert.ok(byId.get('grant-objects-status').textContent.indexOf('默认不勾选') >= 0);

  // --- deletion: immediate stop vs asynchronous cleanup/backup --------------
  const job = { id: DELETION_ID, state: 'pending', resource_id: MEMORY_ID,
    remaining_scopes: ['object_store', 'backup_catalog'] };
  const view = context.describeDeletionJob(job);
  assert.ok(view.accessStoppedText.indexOf('立即停止访问') >= 0, view.accessStoppedText);
  assert.ok(view.accessStoppedText.indexOf('tombstone') >= 0, view.accessStoppedText);
  assert.ok(view.cleanupPolicyText.indexOf('异步物理清理') >= 0, view.cleanupPolicyText);
  assert.ok(view.cleanupPolicyText.indexOf('备份') >= 0, view.cleanupPolicyText);
  assert.ok(view.cleanupPolicyText.indexOf('完成前') >= 0, view.cleanupPolicyText);
  assert.strictEqual(view.stateText, '待处理');
  assert.strictEqual(view.remainingText.indexOf('对象存储中的密封对象'), 0, view.remainingText);
  assert.ok(view.remainingText.indexOf('备份删除账本') >= 0, view.remainingText);
  context.renderDeletion(job);
  const deletedCard = byId.get('deletion-status');
  assert.strictEqual(byId.get('deletion-card').hidden, false);
  assert.ok(deletedCard.textContent.indexOf('立即停止访问') >= 0, deletedCard.textContent);
  assert.ok(deletedCard.textContent.indexOf('异步清理与备份期限') >= 0, deletedCard.textContent);
  assert.ok(deletedCard.textContent.indexOf('待处理') >= 0, deletedCard.textContent);
  assert.ok(deletedCard.textContent.indexOf('剩余清理范围') >= 0, deletedCard.textContent);

  // --- the description never claims the physical bytes are gone ------------
  assert.ok(view.cleanupPolicyText.indexOf('本页不声称') === -1, view.cleanupPolicyText);
  assert.strictEqual(context.documentFailureText('embedding_provider_not_configured'),
    '未配置 embedding provider');
  process.stdout.write('personal data ok\n');
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; });
"""


def test_personal_data_shows_ingestion_states_and_never_grant_selection_by_default(
    node_binary: str, tmp_path
) -> None:
    out = run_node(
        node_binary,
        tmp_path,
        "t098-personal-data",
        PERSONAL_DATA_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
    )
    assert "personal data ok" in out


KNOWLEDGE_CITATION_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const BASE_ID = '11111111-1111-4111-8111-111111111111';
const DOC_ID = '22222222-2222-4222-8222-222222222222';
const CHUNK_ID = '33333333-3333-4333-8333-333333333333';
const CHUNK_TEXT = '华东地区十月的销售额是 987.25 元，订单数为 31。';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.type = '';
    this.dataset = {};
  }
  get firstChild() { return this.children.length ? this.children[0] : null; }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    return (this._text || '') + this.children.map((child) => child.textContent || '').join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  descendants() {
    const out = [];
    const walk = (node) => { for (const child of node.children || []) { out.push(child); walk(child); } };
    walk(this);
    return out;
  }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
  submit() { for (const fn of this._listeners.submit || []) fn({ preventDefault() {} }); }
}

// Pre-create every element id the committed page HTML declares. This makes the
// test follow the real document structure instead of a hand-copied id list.
const html = fs.readFileSync(webDir + '/knowledge.html', 'utf8');
const ids = [];
for (const match of html.matchAll(/id="([^"]+)"/g)) ids.push(match[1]);
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || new El('div', id); },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
};

const chunk = {
  chunk_id: CHUNK_ID, document_id: DOC_ID, document_revision: 1, document_state: 'ready',
  chunk_no: 1, score: 0.91, text: CHUNK_TEXT,
  span: { start_char: 0, end_char: Array.from(CHUNK_TEXT).length },
  citation: {
    document_id: DOC_ID, document_revision: 1, chunk_no: 1,
    span: { start_char: 0, end_char: Array.from(CHUNK_TEXT).length },
  },
};
let retrievalEmpty = false;
let citationCalls = 0;
const calls = [];

async function api(path, options) {
  calls.push({ path: path, options: options || {} });
  if (path.indexOf('/api/v1/knowledge-bases?') === 0 || path === '/api/v1/knowledge-bases') {
    return { items: [{ id: BASE_ID, name: 't098 KB', revision: 1 }], next_cursor: null };
  }
  if (path === '/api/v1/knowledge-bases/' + BASE_ID + '/retrieval-queries') {
    if (retrievalEmpty) {
      return { items: [], retrieval: { embedding_model_revision: 'local-hash-v1',
        top_k: 8, acl_epoch: 1, index: { active_indexes: 1,
          embedding_model_revision: 'local-hash-v1', excluded_indexes: 0 } } };
    }
    return {
      items: [chunk],
      retrieval: { embedding_model_revision: 'local-hash-v1', top_k: 8, acl_epoch: 1,
        index: { active_indexes: 1, embedding_model_revision: 'local-hash-v1',
          excluded_indexes: 0 } },
    };
  }
  if (path === '/api/v1/knowledge-citations/' + CHUNK_ID) {
    citationCalls += 1;
    if (citationCalls === 1) return chunk;
    throw Object.assign(new Error('the cited source is no longer readable'), {
      status: 404, code: 'resource_not_found',
    });
  }
  return {};
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  crypto: require('crypto').webcrypto,
  api: api,
  csrf: async () => 'csrf-token',
  requireLogin: async () => ({ roles: ['user'] }),
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  setTimeout: (fn) => 1,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
context.api = api;
context.csrf = async () => 'csrf-token';
context.requireLogin = async () => ({ roles: ['user'] });
vm.runInContext(fs.readFileSync(webDir + '/assets/knowledge.js', 'utf8'), context);
// The page's inline script is part of the committed bytes: extract and execute it.
const inline = html.match(/<script>([\s\S]*?)<\/script>/g)
  .map((block) => block.replace(/^<script>/, '').replace(/<\/script>$/, ''))
  .join('\n');
vm.runInContext(inline, context);

async function flush() { for (let i = 0; i < 20; i += 1) await new Promise((r) => setImmediate(r)); }
function findButton(root, needle) {
  return root.descendants().find(
    (node) => node.tagName === 'BUTTON' && String(node.textContent).indexOf(needle) >= 0,
  );
}
async function search(query) {
  byId.get('query-text').value = query;
  byId.get('query-top-k').value = '8';
  byId.get('query-form').submit();
  await flush();
}

(async () => {
  await flush();
  assert.strictEqual(byId.get('kb-list').children.length, 1, 'the knowledge base did not load');

  // --- a readable citation: the page shows the real body --------------------
  await search('十月的销售额');
  const open = findButton(byId.get('results'), '打开引用');
  assert.ok(open, 'the retrieval chunk exposes no citation action');
  open.click();
  await flush();
  assert.strictEqual(byId.get('citation-text').textContent, CHUNK_TEXT,
    'the citation body was not rendered');
  assert.strictEqual(byId.get('citation-panel').dataset.citationAvailable, 'true');
  assert.ok(byId.get('citation-status').textContent.indexOf('引用当前可读') >= 0,
    byId.get('citation-status').textContent);

  // --- the same citation after revocation: 404 and no stale body ------------
  open.click();
  await flush();
  assert.strictEqual(byId.get('citation-text').textContent, '',
    'the page fell back to the previously read citation body');
  assert.strictEqual(byId.get('citation-panel').dataset.citationAvailable, 'false');
  assert.strictEqual(byId.get('citation-meta').textContent, '引用不可用');
  assert.ok(byId.get('citation-status').textContent.indexOf('引用不可用') >= 0,
    byId.get('citation-status').textContent);
  assert.ok(byId.get('citation-status').textContent.indexOf('不会回退') >= 0,
    byId.get('citation-status').textContent);
  assert.ok(byId.get('citation-status').textContent.indexOf('重新检索') >= 0,
    byId.get('citation-status').textContent);

  // --- an empty retrieval is an explained empty state, not a spinner --------
  retrievalEmpty = true;
  await search('不存在的资料');
  const resultsText = byId.get('results').textContent;
  assert.ok(resultsText.indexOf('没有检索到可读片段') >= 0, resultsText);
  assert.ok(resultsText.indexOf('下一步') >= 0, resultsText);
  assert.ok(byId.get('query-status').textContent.indexOf('空结果不是错误') >= 0,
    byId.get('query-status').textContent);
  assert.ok(byId.get('query-status').textContent.indexOf('下一步') >= 0,
    byId.get('query-status').textContent);

  // --- pure rules: 403 is unavailable too, and success keeps the body -------
  const forbidden = context.citationOpenFailure({ status: 403, code: 'forbidden' });
  assert.strictEqual(forbidden.clearBody, true);
  assert.ok(forbidden.text.indexOf('授权') >= 0, forbidden.text);
  const success = context.citationOpenSuccess(chunk);
  assert.strictEqual(success.available, true);
  assert.strictEqual(success.clearBody, false);
  assert.strictEqual(success.chunk.text, CHUNK_TEXT);
  const emptyView = context.describeRetrieval({ items: [] });
  assert.strictEqual(emptyView.empty, true);
  assert.ok(emptyView.emptyText.indexOf('不是错误') >= 0, emptyView.emptyText);
  assert.ok(emptyView.emptyNextText.indexOf('下一步') >= 0, emptyView.emptyNextText);
  process.stdout.write('knowledge citation ok\n');
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; });
"""


def test_a_revoked_citation_is_cleared_and_never_served_from_the_previous_body(
    node_binary: str, tmp_path
) -> None:
    out = run_node(
        node_binary,
        tmp_path,
        "t098-knowledge-citation",
        KNOWLEDGE_CITATION_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
    )
    assert "knowledge citation ok" in out
