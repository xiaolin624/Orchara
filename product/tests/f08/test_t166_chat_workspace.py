"""T2 (#166): the /chat conversation workspace.

Evidence layers (kept separate, never mixed):

1. **Committed page skeleton**: the served ``chat.html`` bytes must carry the
   viewport workbench (three columns, one scrolling page frame), the inline run
   card plus its right-rail mirror, the inline failure card, the composer and the
   actionable empty state. This is a static structure assertion, not a browser
   result.
2. **Real page bytes under Node**: ``assets/chat.js`` (the bytes the API serves)
   runs against a minimal DOM shim and renders one conversation with a
   finalizing Run, a result artifact and an uploaded attachment. The shim
   implements exactly the elements, listeners and selectors the page uses.
3. The real reflow (1440 viewport height, 375, 844) is measured by
   ``tools/verify_frontend.py`` and the T2 screenshots; it is ``not_run`` here and
   is never claimed by this module.

The business contract of this page (request/queue/interaction/handoff) stays in
T074/T079/T082/T084/postreview/C6.0x; this module only pins the workspace
presentation and the facts it derives from real fields.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def read_page() -> str:
    return (st.WEB_DIR / "chat.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. the committed skeleton
# ---------------------------------------------------------------------------
def test_the_chat_page_serves_the_viewport_workbench() -> None:
    html = read_page()
    for marker in (
        'class="chat-layout chat-layout--workspace"',
        'class="chat-workspace"',
        'class="chat-columns"',
        'class="chat-thread"',
        'class="chat-context"',
        'class="panel chat-rail" id="conversation-panel"',
        'class="run-card"',
        'class="run-card stream-run"',
        'class="run-card__bar"',
        'class="run-card__fill"',
        'class="steps run-card__steps"',
        'class="pill run-pill"',
        'class="pill chat-conv-pill"',
        'class="chat-conv-title"',
        'class="stream-failure"',
        'data-stream-cancel="true"',
        'data-stream-retry="true"',
        '<dt>状态</dt>',
        '<dt>阶段</dt>',
        '<dt>处理版本</dt>',
        '<dt>剩余队列</dt>',
        'class="run-card__destination"',
        '数据去向',
    ):
        assert marker in html, f"chat.html is missing {marker!r}"
    # Two run cards share one class contract: the inline stream card and the
    # right-rail mirror, each addressing the five named stages with one dot.
    assert html.count('class="step" data-step-index=') == 10, "two run cards need five dots each"
    assert html.count('class="step-dot"') == 10
    assert 'data-step="0"' in html
    # A viewport workbench: the page frame is 100dvh, the three panes scroll, and
    # the message column is 1fr with a 300px context rail.
    assert "100dvh" in html, "the chat page must fit the viewport"
    assert "grid-template-columns: minmax(0, 1fr) 300px" in html, "the context rail is 300px"
    assert "height: 4px" in html
    assert "position: sticky" in html
    assert "@media (max-width: 720px)" in html
    assert 'class="page-description"' not in html, "the workbench header keeps H1 + one meta line"
    # Every business id of the conversation page survives the workspace rewrite.
    for element_id in (
        "chat-empty",
        "chat-panel",
        "conversation-panel",
        "conversation-create",
        "conversation-list",
        "conversation-status",
        "chat-message",
        "chat-message-form",
        "chat-messages",
        "chat-submit",
        "chat-cancel",
        "chat-submit-status",
        "chat-conversation-id",
        "chat-version-id",
        "chat-handling",
        "chat-run-link",
        "queue-list",
        "queue-panel",
        "request-panel",
        "interaction-panel",
        "interaction-prompt",
        "interaction-select",
        "interaction-reply",
        "interaction-status",
        "interaction-candidates",
        "interaction-missing",
        "chat-attachment-input",
        "chat-attachment-upload",
        "chat-attachment-status",
        "chat-attachment-lines",
        "chat-attachments",
        "runstate-status",
    ):
        assert f'id="{element_id}"' in html, f"chat.html lost #{element_id}"
    # The pause markers keep the exact id → data-runstate-paused order C6.01 pins.
    for control in ("chat-submit", "chat-cancel", "interaction-reply"):
        assert f'id="{control}" data-runstate-paused' in html, control


def test_the_composer_and_the_empty_state_offer_real_actions() -> None:
    html = read_page()
    for marker in (
        'class="composer"',
        "composer__input",
        "composer__chips",
        "composer__actions",
        "composer__hint",
        "conversation-item",  # the row contract the page script fills
        "context-outputs",
        "context-handoff",
        "context-destination",
        "data-new-conversation",
    ):
        assert marker in html, f"chat.html is missing {marker!r}"
    # The empty state is a real landing: one primary action plus three named
    # suggestions that only fill the composer (never a fabricated submission).
    empty = html[html.index('id="chat-empty"') : html.index('id="chat-panel"')]
    assert empty.count("data-suggestion=") == 3, "the empty state needs three suggestions"
    assert 'data-new-conversation' in empty
    assert "新建会话" in empty
    assert "不会直接提交" in empty
    assert 'class="empty-state"' not in empty, "the empty state must not be a bare empty box"
    # The handover form and the destination note live in the context column.
    context = html[html.index('class="chat-context"') :]
    for marker in ("可交接应用", "本次产出", "数据去向", 'id="chat-handoff"', 'id="request-panel"'):
        assert marker in context, f"the context column is missing {marker!r}"
    # The composer keeps the fixed-version entry and the upload entry.
    composer = html[html.index('id="chat-message-form"') : html.index('id="chat-submit-status"')]
    for element_id in ("chat-attachment-input", "chat-attachment-upload", "chat-version-id", "chat-message"):
        assert f'id="{element_id}"' in composer, f"the composer is missing #{element_id}"


# ---------------------------------------------------------------------------
# 2. the real page bytes under Node: workspace rows, run cards, composer chips
# ---------------------------------------------------------------------------
HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const CONV_A = '11111111-1111-4111-8111-111111111111';
const CONV_B = '22222222-2222-4222-8222-222222222222';
const REQUEST = '33333333-3333-4333-8333-333333333333';
const RUN = '44444444-4444-4444-8444-444444444444';
const REQUEST_ARTIFACT = '77777777-7777-4777-8777-777777777777';
const RESULT_ARTIFACT = '55555555-5555-4555-8555-555555555555';
const UPLOAD_ARTIFACT = '66666666-6666-4666-8666-666666666666';
const SUGGESTION = '分析本月销售数据，按地区汇总金额，并生成一份可以发给管理层的周报。';

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
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.attrs = {};
    this.name = '';
    this.files = [];
    this.title = '';
    this.href = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
  }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
  submit() { for (const fn of this._listeners.submit || []) fn({ preventDefault() {} }); }
  focus() {}
}

const elements = [];
const byId = new Map();

function element(tag, id) {
  const item = new El(tag, id);
  elements.push(item);
  if (id) byId.set(id, item);
  return item;
}

function getElementById(id) {
  if (!byId.has(id)) byId.set(id, element('div', id));
  return byId.get(id);
}

function queryAll(selector) {
  const text = String(selector || '').trim();
  if (text === '#chat-messages li') {
    return getElementById('chat-messages').children.filter((row) => row.tagName === 'LI');
  }
  if (text === '#conversation-list button') {
    return getElementById('conversation-list').children.map((row) => row.children[0]).filter(Boolean);
  }
  if (text === '[data-suggestion]') {
    return elements.filter((item) => item.attrs['data-suggestion'] !== undefined);
  }
  if (text === '[data-new-conversation]') {
    return elements.filter((item) => item.attrs['data-new-conversation'] !== undefined);
  }
  return [];
}

const document = {
  getElementById,
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { nodeType: 3, textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll(selector) { return queryAll(selector); },
};

// --- real page skeleton pieces the page script fills by class ----------------
// #request-panel is the right-rail mirror and .stream-run is the inline card:
// the same class contract, so the same projection must fill both.
function buildCard(root, inline) {
  root.className = inline ? 'run-card stream-run' : 'run-card';
  root.dataset.step = '0';
  root.dataset.tone = 'muted';
  root.hidden = true;
  const bar = element('div'); bar.className = 'run-card__bar'; root.appendChild(bar);
  const fill = element('i'); fill.className = 'run-card__fill'; bar.appendChild(fill);
  const head = element('div'); head.className = 'run-card__head'; root.appendChild(head);
  const heading = element('h3'); heading.textContent = inline ? '本次运行' : '当前运行';
  head.appendChild(heading);
  const pill = element('span'); pill.className = 'pill run-pill';
  pill.dataset.tone = 'muted'; pill.dataset.shape = 'square'; head.appendChild(pill);
  const steps = element('ol'); steps.className = 'steps run-card__steps'; root.appendChild(steps);
  for (let index = 1; index <= 5; index += 1) {
    const step = element('li'); step.className = 'step';
    step.dataset.stepIndex = String(index);
    steps.appendChild(step);
  }
  const facts = element('dl'); facts.className = 'run-card__facts'; root.appendChild(facts);
  for (const name of ['state', 'phase', 'version', 'queue']) {
    const dt = element('dt'); dt.textContent = name; facts.appendChild(dt);
    const dd = element('dd'); dd.className = 'run-fact-' + name; facts.appendChild(dd);
  }
  const handling = element('p'); handling.className = 'run-card__handling'; handling.hidden = true;
  root.appendChild(handling);
  const runLine = element('p'); runLine.className = 'run-card__run'; runLine.hidden = true;
  root.appendChild(runLine);
  const runID = element('code'); runID.className = 'run-card__run-id'; runLine.appendChild(runID);
  if (inline) {
    const link = element('a'); link.className = 'run-card__run-link'; link.hidden = true;
    runLine.appendChild(link);
    const actions = element('div'); actions.className = 'run-card__actions'; root.appendChild(actions);
    const cancel = element('button'); cancel.className = 'run-card__cancel'; cancel.hidden = true;
    cancel.setAttribute('data-stream-cancel', 'true'); actions.appendChild(cancel);
  }
}

function buildStreamFailure(panel) {
  const card = element('div'); card.className = 'stream-failure'; card.hidden = true;
  panel.appendChild(card);
  const title = element('p'); title.className = 'stream-failure__title'; card.appendChild(title);
  const meta = element('p'); meta.className = 'stream-failure__meta'; card.appendChild(meta);
  const code = element('code'); code.className = 'stream-failure__code';
  code.setAttribute('data-error-code', ''); meta.appendChild(code);
  const retryable = element('span'); retryable.className = 'stream-failure__retryable';
  meta.appendChild(retryable);
  const reason = element('p'); reason.className = 'stream-failure__reason'; card.appendChild(reason);
  const advice = element('p'); advice.className = 'stream-failure__advice'; card.appendChild(advice);
}

buildCard(getElementById('request-panel'), false);
const panel = getElementById('chat-panel');
panel.hidden = true;
const title = element('h2'); title.className = 'chat-conv-title'; title.textContent = '会话';
panel.appendChild(title);
const conversationPill = element('span'); conversationPill.className = 'pill chat-conv-pill';
conversationPill.hidden = true; panel.appendChild(conversationPill);
conversationPill.appendChild(element('span', 'chat-request-state'));
const streamRunRoot = element('div');
panel.appendChild(streamRunRoot);
buildCard(streamRunRoot, true);
buildStreamFailure(panel);
const outputs = element('ul'); outputs.className = 'context-outputs'; panel.appendChild(outputs);
const outputCount = element('span'); outputCount.className = 'context-output-count';
panel.appendChild(outputCount);
getElementById('interaction-panel').hidden = true;
const suggestion = element('button');
suggestion.setAttribute('data-suggestion', SUGGESTION);

// --- fetch shims ------------------------------------------------------------
function fakeHeaders(values) {
  const map = values || {};
  return {
    get(name) {
      return Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;
    },
  };
}

const now = new Date();
const older = new Date(now.getTime() - 1000 * 60 * 60 * 24 * 20);
const fixtures = {
  conversations: {
    items: [
      { id: CONV_A, created_at: now.toISOString(), revision: 1 },
      { id: CONV_B, created_at: older.toISOString(), revision: 2 },
    ],
  },
  messages: {
    items: [
      {
        id: 'm2', role: 'assistant', kind: 'result', request_id: REQUEST, run_id: RUN,
        body_artifact_id: RESULT_ARTIFACT, created_at: now.toISOString(),
      },
      {
        id: 'm1', role: 'user', kind: 'request', request_id: REQUEST, run_id: null,
        body_artifact_id: REQUEST_ARTIFACT, created_at: now.toISOString(),
      },
    ],
  },
  request: { id: REQUEST, state: 'dispatched', run_id: RUN, error_code: null, interaction: null },
  run: { id: RUN, state: 'running', phase: 'finalizing' },
};
const calls = [];

const context = {
  console,
  crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
  window: { location: { assign() {} } },
  document,
  setTimeout() { return 1; },
  clearTimeout() {},
  api: async function (pathname) {
    calls.push({ kind: 'api', path: pathname });
    if (pathname === '/api/v1/conversations') return fixtures.conversations;
    if (/^\/api\/v1\/conversations\/[^/]+\/messages$/.test(pathname)) return fixtures.messages;
    if (pathname === '/api/v1/runs/' + RUN) return fixtures.run;
    const request = pathname.match(/^\/api\/v1\/requests\/([^/]+)$/);
    if (request) return fixtures.request;
    return {};
  },
  apiFull: async function (pathname) {
    const request = pathname.match(/^\/api\/v1\/requests\/([^/]+)$/);
    if (request) {
      return {
        payload: fixtures.request,
        headers: fakeHeaders({
          'X-Request-Application-Version-No': '2',
          'X-Request-Queue-Position': '1',
        }),
      };
    }
    if (pathname === '/api/v1/requests') {
      calls.push({ kind: 'submit', path: pathname });
      return { payload: fixtures.request, headers: fakeHeaders({}) };
    }
    return { payload: {}, headers: fakeHeaders({}) };
  },
  csrf: async () => 'csrf',
  requireLogin: async () => ({ roles: ['user'] }),
  fetchArtifact: async () => ({ artifact: { id: RESULT_ARTIFACT, state: 'ready' }, quarantineReason: null }),
  quarantineMessage: (reason) => String(reason || ''),
  shouldPollArtifact: () => false,
  uploadInputForFile: (file, mediaType, purpose) => ({ media_type: mediaType, purpose }),
  createUploadRequest: async () => ({ artifact_id: UPLOAD_ARTIFACT, upload_url: 'http://upload.invalid/put' }),
  putUploadBytes: async () => {},
  completeUploadRequest: async () => ({ id: UPLOAD_ARTIFACT, state: 'ready' }),
  sha256Hex: async () => 'a'.repeat(64),
  // The page reads its own sealed artifact bytes (runs.js is not loaded here).
  fetch: async () => ({ ok: true, status: 200, text: async () => 'sealed request body' }),
};

vm.createContext(context);
for (const name of ['runstream.js', 'runstate-guard.js', 'chat.js']) {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', name), 'utf8'),
    context,
    { filename: name },
  );
}

const flush = async () => {
  for (let index = 0; index < 60; index += 1) await new Promise((resolve) => setImmediate(resolve));
};

function childByClass(node, className) {
  const found = [];
  const walk = (parent) => {
    for (const child of parent.children || []) {
      if (String(child.className).split(/\s+/).includes(className)) found.push(child);
      walk(child);
    }
  };
  walk(node);
  return found;
}

(async () => {
  await flush();

  // -- conversation rows: two lines, a real group label and a status pill ----
  const list = getElementById('conversation-list');
  assert.strictEqual(list.children.length, 2, 'both conversations are listed');
  const first = list.children[0];
  const firstButton = first.children[0];
  assert.strictEqual(firstButton.tagName, 'BUTTON');
  assert.strictEqual(first.dataset.groupLabel, '今天', 'today is its own group');
  assert.strictEqual(list.children[1].dataset.groupLabel, '更早', 'older is its own group');
  assert.strictEqual(list.children[1].children[0].dataset.groupLabel, undefined);
  assert.ok(childByClass(firstButton, 'conversation-item__title')[0], 'row title line');
  const rowMeta = childByClass(firstButton, 'conversation-item__meta')[0];
  assert.ok(rowMeta, 'the pill and the relative time share the second line');
  assert.ok(childByClass(rowMeta, 'conversation-item__time')[0], 'relative time');
  const initialPill = childByClass(firstButton, 'pill')[0];
  assert.ok(initialPill, 'row status pill');
  assert.strictEqual(initialPill.dataset.tone, 'muted', 'an unread conversation is muted');
  assert.strictEqual(initialPill.dataset.shape, 'square');
  assert.ok(initialPill.textContent.indexOf('状态未读取') >= 0, initialPill.textContent);

  // -- open the conversation: both cards carry the same real projection ------
  await firstButton.click();
  await flush();
  const rail = getElementById('request-panel');
  assert.strictEqual(rail.hidden, false, 'the run card is shown for the selected request');
  assert.strictEqual(rail.dataset.step, '4', 'finalizing is the fourth of five stages');
  assert.strictEqual(rail.dataset.tone, 'accent', 'not a terminal tone');
  const inline = childByClass(panel, 'stream-run')[0];
  assert.ok(inline, 'the inline run card exists');
  assert.strictEqual(inline.hidden, false, 'a Run exists, so the inline card is shown');
  assert.strictEqual(inline.dataset.step, '4', 'the inline card mirrors the rail');
  assert.strictEqual(inline.dataset.tone, 'accent');
  for (const card of [rail, inline]) {
    const pill = childByClass(card, 'run-pill')[0];
    assert.strictEqual(pill.dataset.shape, 'ring', 'finalizing is a ring, never a check');
    assert.strictEqual(pill.dataset.tone, 'accent');
    assert.strictEqual(pill.textContent, '收尾中');
    assert.strictEqual(childByClass(card, 'run-fact-state')[0].textContent, '执行中（running）');
    assert.strictEqual(
      childByClass(card, 'run-fact-phase')[0].textContent,
      '收尾中（finalizing，等待退出证据）',
    );
    assert.strictEqual(childByClass(card, 'run-fact-version')[0].textContent, 'v2');
    assert.strictEqual(childByClass(card, 'run-fact-queue')[0].textContent, '没有其他排队请求');
    const steps = childByClass(card, 'run-card__steps')[0].children;
    assert.strictEqual(steps.length, 5);
    assert.strictEqual(steps[2].className, 'step is-done');
    assert.strictEqual(steps[3].className, 'step is-now');
    assert.strictEqual(steps[4].className, 'step');
  }
  assert.strictEqual(getElementById('chat-request-state').textContent, '已分派到处理版本');
  assert.strictEqual(getElementById('chat-handling').dataset.runPhase, 'finalizing');
  assert.strictEqual(rail.dataset.runPhase, 'finalizing');
  assert.strictEqual(inline.dataset.runPhase, 'finalizing');
  const runLink = getElementById('chat-run-link');
  assert.strictEqual(runLink.children.length, 1, 'the run deep link is a real link');
  assert.strictEqual(runLink.children[0].textContent, '查看运行详情');
  assert.strictEqual(runLink.children[0].href, '/runs?run=' + RUN);
  const inlineLink = childByClass(inline, 'run-card__run-link')[0];
  assert.strictEqual(inlineLink.hidden, false);
  assert.strictEqual(inlineLink.href, '/runs?run=' + RUN);
  const inlineCancel = childByClass(inline, 'run-card__cancel')[0];
  assert.strictEqual(inlineCancel.hidden, false, 'an active request stays cancelable inline');

  // -- the conversation title row: real title + request-state pill ----------
  assert.strictEqual(
    childByClass(panel, 'chat-conv-title')[0].textContent, 'sealed request body');
  const headPill = childByClass(panel, 'chat-conv-pill')[0];
  assert.strictEqual(headPill.hidden, false);
  assert.strictEqual(headPill.dataset.tone, 'accent');

  // -- the row status now carries the real queue state ----------------------
  const rowPill = childByClass(list.children[0].children[0], 'pill')[0];
  assert.ok(rowPill.textContent.indexOf('已分派') >= 0, rowPill.textContent);
  assert.strictEqual(rowPill.dataset.tone, 'accent');
  assert.strictEqual(
    childByClass(list.children[0].children[0], 'conversation-item__title')[0].textContent,
    'sealed request body',
  );

  // -- message stream: user and Orchara blocks stay distinguishable ---------
  const messages = getElementById('chat-messages').children;
  assert.strictEqual(messages.length, 2);
  assert.ok(messages[0].className.indexOf('message--user') >= 0, messages[0].className);
  assert.ok(messages[1].className.indexOf('message--assistant') >= 0, messages[1].className);
  assert.strictEqual(messages[0].dataset.requestId, REQUEST);
  assert.strictEqual(messages[1].dataset.requestId, REQUEST);
  assert.ok(childByClass(messages[0], 'message-avatar')[0], 'the avatar sits in the head row');

  // -- the result artifact is listed in 本次产出 ----------------------------
  const outputs = childByClass(panel, 'context-outputs')[0];
  assert.strictEqual(outputs.children.length, 1, 'one result artifact is listed');
  const outputLink = outputs.children[0].children[0];
  assert.strictEqual(outputLink.href, '/api/v1/artifacts/' + RESULT_ARTIFACT + '/content');
  assert.strictEqual(
    childByClass(panel, 'context-output-count')[0].textContent, '1 个');

  // -- attachment chips are removable without a request ---------------------
  getElementById('chat-attachment-input').files = [{ arrayBuffer: async () => new ArrayBuffer(8) }];
  getElementById('chat-attachment-upload').click();
  await flush();
  const chips = getElementById('chat-attachment-lines').children;
  assert.strictEqual(chips.length, 1, 'the uploaded attachment is a chip');
  assert.strictEqual(chips[0].children.length, 2, 'a chip is a label plus a remove control');
  chips[0].children[1].click();
  await flush();
  assert.strictEqual(getElementById('chat-attachment-lines').children.length, 0, 'the chip is removed');

  // -- a suggestion fills the composer; it never submits ---------------------
  suggestion.click();
  await flush();
  assert.strictEqual(getElementById('chat-message').value, SUGGESTION);
  assert.strictEqual(calls.filter((call) => call.kind === 'submit').length, 0);

  // -- the inline failure card explains the code, reason and next step ------
  const failureCard = childByClass(panel, 'stream-failure')[0];
  assert.strictEqual(failureCard.hidden, true, 'a healthy request has no failure card');
  const failure = context.describeRequestFailure(
    { state: 'failed', error_code: 'no_matching_application' },
  );
  context.applyStreamFailure(document, failure);
  assert.strictEqual(failureCard.hidden, false);
  const codeCell = childByClass(failureCard, 'stream-failure__code')[0];
  assert.strictEqual(codeCell.textContent, 'no_matching_application');
  assert.strictEqual(codeCell.dataset.errorCode, 'no_matching_application');
  assert.ok(
    childByClass(failureCard, 'stream-failure__reason')[0].textContent.indexOf('未找到匹配的应用') >= 0,
  );
  assert.strictEqual(
    childByClass(failureCard, 'stream-failure__retryable')[0].textContent, '需要修改后重新提交');
  assert.ok(
    childByClass(failureCard, 'stream-failure__advice')[0].textContent.indexOf('重新提交') >= 0);
  context.applyStreamFailure(document, context.describeRequestFailure(
    { state: 'dispatched', error_code: null },
  ));
  assert.strictEqual(failureCard.hidden, true, 'a non-failure hides the card');

  // -- the stage/tone projection never claims success early -----------------
  const ready = true;
  const finalizing = context.runCardView(
    { state: 'dispatched', run_id: RUN }, { state: 'running', phase: 'finalizing' }, 2,
    { rows: [] }, ready,
  );
  const succeeded = context.runCardView(
    { state: 'dispatched', run_id: RUN }, { state: 'succeeded', phase: 'idle' }, 2,
    { rows: [] }, ready,
  );
  const cancelling = context.runCardView(
    { state: 'dispatched', run_id: RUN }, { state: 'cancelling', phase: 'executing' }, 2,
    { rows: [] }, ready,
  );
  const reconciling = context.runCardView(
    { state: 'dispatched', run_id: RUN }, { state: 'needs_reconciliation', phase: 'executing' }, 2,
    { rows: [] }, ready,
  );
  const failed = context.runCardView({ state: 'failed', run_id: null }, null, 0, { rows: [] }, ready);
  const runFailed = context.runCardView(
    { state: 'dispatched', run_id: RUN }, { state: 'failed', phase: 'executing' }, 2,
    { rows: [] }, ready,
  );
  const unavailable = context.runCardView(
    { state: 'dispatched', run_id: RUN }, null, 0, { rows: [] }, false,
  );
  assert.strictEqual(finalizing.step, 4);
  assert.strictEqual(
    JSON.stringify(finalizing.stepClasses),
    JSON.stringify(['step is-done', 'step is-done', 'step is-done', 'step is-now', 'step']),
  );
  assert.strictEqual(finalizing.shape, 'ring');
  assert.ok(finalizing.stateText.indexOf('running') >= 0, finalizing.stateText);
  assert.notStrictEqual(finalizing.tone, 'success');
  assert.strictEqual(succeeded.step, 5);
  assert.strictEqual(succeeded.tone, 'success');
  assert.strictEqual(succeeded.shape, 'check');
  assert.strictEqual(succeeded.statusText, '已成功');
  assert.strictEqual(succeeded.stepClasses.filter((item) => item === 'step is-done').length, 5);
  assert.strictEqual(cancelling.tone, 'warning');
  assert.strictEqual(cancelling.shape, 'ring');
  assert.strictEqual(cancelling.terminal, false, 'cancelling is still stopping, not stopped');
  assert.strictEqual(reconciling.tone, 'warning');
  // A request that failed before dispatch stopped at the matching stage: only
  // that stage is highlighted, and it is a failed stage, not a waiting one.
  assert.strictEqual(failed.step, 1);
  assert.strictEqual(failed.tone, 'danger');
  assert.strictEqual(failed.shape, 'alert');
  assert.strictEqual(failed.stepClasses[0], 'step is-now is-failed');
  assert.strictEqual(failed.terminal, true);
  // A failed Run got past matching and queueing: the failure sits on the
  // execution stage, with the earlier stages done and the later ones muted.
  assert.strictEqual(runFailed.step, 3);
  assert.strictEqual(
    JSON.stringify(runFailed.stepClasses),
    JSON.stringify(['step is-done', 'step is-done', 'step is-now is-failed', 'step', 'step']),
  );
  assert.strictEqual(unavailable.stateText, '运行状态暂不可用');
  assert.strictEqual(unavailable.statusText, '运行状态不可用');
  assert.strictEqual(unavailable.shape, 'square');

  // -- the row title is the first line of the sealed request, shortened ----
  assert.strictEqual(context.conversationTitleFromMessage('  分析本月销售数据  '), '分析本月销售数据');
  assert.strictEqual(context.conversationTitleFromMessage(''), '');
  const longTitle = context.conversationTitleFromMessage('分析本月销售数据，按地区汇总金额，并生成一份可以发给管理层的周报。');
  assert.strictEqual(longTitle.slice(-1), '…');
  assert.strictEqual(Array.from(longTitle).length, 29);

  // -- a fully terminal queue never reads as success -----------------------
  const failedQueue = context.queueStatusView({
    rows: [{ id: 'r1', position: 1, state: 'failed', label: '失败', active: true }],
  });
  assert.strictEqual(failedQueue.text, '失败');
  assert.strictEqual(failedQueue.tone, 'danger');
  assert.strictEqual(failedQueue.shape, 'alert');
  const cancelledQueue = context.queueStatusView({
    rows: [{ id: 'r1', position: 1, state: 'cancelled', label: '已取消', active: true }],
  });
  assert.strictEqual(cancelledQueue.text, '已取消');
  assert.strictEqual(cancelledQueue.shape, 'dash');

  console.log('t166 chat workspace: ok');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_chat_run_cards_and_conversation_rows_render_from_real_facts(
    node_binary: str, tmp_path
) -> None:
    script = HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR)))
    harness = tmp_path / "t166-chat-workspace.js"
    harness.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "t166 chat workspace: ok" in completed.stdout, completed.stdout
