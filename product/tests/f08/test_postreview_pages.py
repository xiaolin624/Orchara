"""Post-review fixes for #75/#80: reopening a conversation and replying with files.

The reviewer reproduced two page defects with a DOM-executing test: reopening a
conversation cleared the current request, so the interaction panel and polling never
came back and the queue was inert; and the reply handler hard-coded ``[]`` for the
attachments, so an uploaded missing-input CSV never reached the server.

This module executes the real ``chat.js`` bytes the API serves under Node against a
minimal DOM shim. The shim is not a browser: it implements exactly the elements,
listeners and selectors the page uses, and records the real ``api``/``apiFull``
calls the page makes. Two tests drive the real handlers end to end.
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


HARNESS_PRELUDE = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const webDir = __WEB_DIR__;
const CONVERSATION = '11111111-1111-4111-8111-111111111111';
const REQUEST_INPUT = '22222222-2222-4222-8222-222222222222';
const REQUEST_SECOND = '33333333-3333-4333-8333-333333333333';
const INTERACTION = '44444444-4444-4444-8444-444444444444';
const CSV_ARTIFACT = '55555555-5555-4555-8555-555555555555';
const CANDIDATE_A = '66666666-6666-4666-8666-666666666666';
const CANDIDATE_B = '77777777-7777-4777-8777-777777777777';

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
    this.type = '';
    this.href = '';
    this.files = null;
    this.dataset = {};
    this.attrs = {};
    this.name = '';
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...nodes) {
    this.children = nodes;
  }

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }

  setAttribute(name, value) {
    this.attrs[name] = String(value);
  }

  click() {
    for (const fn of this._listeners.click || []) fn({ preventDefault() {} });
  }

  submit() {
    for (const fn of this._listeners.submit || []) fn({ preventDefault() {} });
  }

  descendants() {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

const elements = [];
const byId = new Map();

function makeElement(tag, id) {
  const el = new El(tag, id);
  elements.push(el);
  if (id) byId.set(id, el);
  return el;
}

function matches(el, simple) {
  let selector = String(simple || '');
  let ok = true;
  const checked = selector.endsWith(':checked');
  if (checked) selector = selector.slice(0, -':checked'.length);
  const nameMatch = selector.match(/\[name="([^"]*)"\]/);
  if (nameMatch) {
    ok = ok && el.name === nameMatch[1];
    selector = selector.replace(nameMatch[0], '');
  }
  selector = selector.trim();
  if (selector.startsWith('#')) {
    ok = ok && el.id === selector.slice(1);
  } else if (selector.startsWith('.')) {
    ok = ok && String(el.className).split(/\s+/).includes(selector.slice(1));
  } else if (selector) {
    ok = ok && el.tagName === selector.toUpperCase();
  }
  if (checked) ok = ok && Boolean(el.checked);
  return ok;
}

function queryAll(selector, root) {
  const text = String(selector || '').trim();
  if (!text) return [];
  const parts = text.split(/\s+/);
  let candidates;
  if (parts.length > 1) {
    const first = parts.shift();
    if (!first.startsWith('#')) return [];
    const scope = byId.get(first.slice(1));
    if (!scope) return [];
    candidates = scope.descendants();
  } else {
    candidates = root ? root.descendants() : elements.slice();
  }
  const rest = parts.join(' ');
  return candidates.filter((el) => matches(el, rest));
}

const document = {
  getElementById(id) {
    return byId.get(id) || makeElement('div', id);
  },
  createElement(tag) {
    return makeElement(tag);
  },
  createTextNode(text) {
    return { nodeType: 3, textContent: String(text) };
  },
  querySelector(selector) {
    return queryAll(selector, null)[0] || null;
  },
  querySelectorAll(selector) {
    return queryAll(selector, null);
  },
};

const timers = [];
const calls = [];

function fakeHeaders(values) {
  const map = values || {};
  return {
    get(name) {
      return Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null;
    },
  };
}

const fixtures = {
  conversations: { items: [] },
  messages: { items: [] },
  requests: {},
};

function routeApi(path) {
  if (path === '/api/v1/conversations') return fixtures.conversations;
  if (/^\/api\/v1\/conversations\/[^/]+\/messages$/.test(path)) return fixtures.messages;
  const request = path.match(/^\/api\/v1\/requests\/([^/]+)$/);
  if (request) return fixtures.requests[request[1]];
  if (/^\/api\/v1\/requests\/[^/]+\/cancel$/.test(path)) {
    return { id: 'cancelled', state: 'cancel_requested' };
  }
  return {};
}

function routeApiFull(path, options) {
  if (path === '/api/v1/requests') {
    calls.push({ kind: 'submit', path: path, body: options && options.body });
    return {
      payload: fixtures.requests[REQUEST_INPUT],
      headers: fakeHeaders({}),
    };
  }
  const reply = path.match(/^\/api\/v1\/requests\/([^/]+)\/reply$/);
  if (reply) {
    calls.push({ kind: 'reply', path: path, body: options && options.body });
    const request = Object.assign({}, fixtures.requests[reply[1]], {
      state: 'routing',
      interaction: null,
    });
    return { payload: request, headers: fakeHeaders({}) };
  }
  const request = path.match(/^\/api\/v1\/requests\/([^/]+)$/);
  if (request) return { payload: fixtures.requests[request[1]], headers: fakeHeaders({}) };
  return { payload: {}, headers: fakeHeaders({}) };
}

const context = {
  console: console,
  crypto: require('crypto'),
  window: { location: { assign() {} } },
  document: document,
  setTimeout(fn, ms) {
    timers.push(fn);
    return timers.length;
  },
  clearTimeout() {},
  api: async function (path, options) {
    calls.push({ kind: 'api', path: path, options: options });
    return routeApi(path);
  },
  apiFull: async function (path, options) {
    return routeApiFull(path, options);
  },
  csrf: async function () {
    return 'csrf-token';
  },
  requireLogin: async function () {
    return {};
  },
  fetchArtifact: async function (artifactID) {
    return { artifact: { id: artifactID, state: 'ready' }, quarantineReason: null };
  },
  quarantineMessage: function (reason) {
    return String(reason || '');
  },
  shouldPollArtifact: function () {
    return false;
  },
  uploadInputForFile: function (file, mediaType, purpose) {
    return { filename: 'missing.csv', media_type: mediaType, purpose: purpose, size_bytes: 8 };
  },
  createUploadRequest: async function () {
    return { artifact_id: CSV_ARTIFACT, upload_url: 'http://upload.invalid/put' };
  },
  putUploadBytes: async function () {},
  completeUploadRequest: async function () {
    return { id: CSV_ARTIFACT, state: 'ready' };
  },
  sha256Hex: async function () {
    return 'a'.repeat(64);
  },
};

function interactionFixture(kind, proposal) {
  return {
    interaction_id: INTERACTION,
    kind: kind,
    expires_at: '2026-12-31T00:00:00Z',
    proposal: proposal,
  };
}

function requestFixture(id, state, interaction) {
  return { id: id, state: state, run_id: null, error_code: null, interaction: interaction || null };
}

function messageFixture(id, requestID) {
  return {
    id: id,
    role: 'user',
    request_id: requestID,
    run_id: null,
    body_artifact_id: null,
    created_at: '2026-01-01T00:00:00Z',
    kind: 'status',
  };
}

async function flush(times) {
  const rounds = times || 12;
  for (let index = 0; index < rounds; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

function elementByID(id) {
  return document.getElementById(id);
}

function loadPage() {
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'chat.js'), 'utf8'), context);
  elementByID('request-panel').hidden = true;
  elementByID('interaction-panel').hidden = true;
}

async function openConversation() {
  fixtures.conversations = {
    items: [{ id: CONVERSATION, created_at: '2026-01-01T00:00:00Z' }],
  };
  await flush();
  const list = elementByID('conversation-list');
  assert.strictEqual(list.children.length, 1, 'conversation list was not rendered');
  list.children[0].children[0].click();
  await flush();
}

async function main() {
__SCENARIO__
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def run_harness(node_binary: str, tmp_path, name: str, scenario: str) -> subprocess.CompletedProcess:
    script = HARNESS_PRELUDE.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))).replace(
        "__SCENARIO__", scenario
    )
    harness = tmp_path / (name + ".js")
    harness.write_text(script, encoding="utf-8")
    return subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def test_reopening_a_conversation_restores_the_pending_request_and_an_actionable_queue(
    node_binary: str, tmp_path
) -> None:
    """Post-review finding [P2]: reopening must restore the control surface (#80)."""
    scenario = r"""
  loadPage();
  fixtures.messages = {
    items: [messageFixture('m2', REQUEST_SECOND), messageFixture('m1', REQUEST_INPUT)],
  };
  fixtures.requests[REQUEST_INPUT] = requestFixture(
    REQUEST_INPUT,
    'awaiting_selection',
    interactionFixture('selection', {
      kind: 'selection',
      question: 'choose the application to continue',
      candidates: [
        { rank: 1, application_version_id: CANDIDATE_A, application_name: 'sales report',
          version_no: 3, purpose: 'sales report' },
        { rank: 2, application_version_id: CANDIDATE_B, application_name: 'quarterly report',
          version_no: 4, purpose: 'quarterly report' },
      ],
    }),
  );
  fixtures.requests[REQUEST_SECOND] = requestFixture(REQUEST_SECOND, 'queued', null);
  await openConversation();

  assert.strictEqual(elementByID('request-panel').hidden, false, 'the request panel was not restored');
  assert.strictEqual(elementByID('chat-request-id').textContent, REQUEST_INPUT,
    'the pending request was not restored');
  assert.strictEqual(elementByID('interaction-panel').hidden, false,
    'the interaction panel was not restored');
  assert.strictEqual(elementByID('interaction-candidates').children.length, 2,
    'the candidates were not rendered');
  assert.strictEqual(elementByID('interaction-select').hidden, false,
    'a selection interaction must expose the choose button');
  assert.ok(timers.length > 0, 'polling for the restored request was not resumed');

  const queueList = elementByID('queue-list');
  const rows = queueList.children;
  assert.strictEqual(rows.length, 2, 'the queue was not rendered');
  for (const row of rows) {
    assert.ok(row.children.length > 0, 'the queue row has no action: ' + row.textContent);
  }
  const queuedRow = rows.find((row) => row.dataset.requestId === REQUEST_SECOND);
  assert.ok(queuedRow, 'the queued request is not addressable from the queue');
  const cancelButton = queuedRow.children.find((child) => child.textContent.indexOf('取消') >= 0);
  assert.ok(cancelButton, 'the queued request exposes no cancel action');
  cancelButton.click();
  await flush();
  const cancelCalls = calls.filter(
    (call) => call.kind === 'api' && /\/requests\/[^/]+\/cancel$/.test(call.path));
  assert.strictEqual(cancelCalls.length, 1, JSON.stringify(calls.map((call) => call.path)));
  assert.ok(cancelCalls[0].path.endsWith('/requests/' + REQUEST_SECOND + '/cancel'),
    cancelCalls[0].path);

  // Selecting a queued request from the list shows that request (and hides the
  // previous interaction), so the queue is a real navigation surface.
  const selectButton = queuedRow.children.find((child) => child.tagName === 'BUTTON');
  selectButton.click();
  await flush();
  assert.strictEqual(elementByID('chat-request-id').textContent, REQUEST_SECOND,
    'the queue selection did not switch the request panel');
  assert.strictEqual(elementByID('interaction-panel').hidden, true,
    'the previous interaction stayed visible after switching requests');
  process.stdout.write('reopen ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t074-reopen", scenario)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "reopen ok" in completed.stdout


def test_the_reply_carries_the_uploaded_missing_input_artifact(
    node_binary: str, tmp_path
) -> None:
    """Post-review finding [P2]: the reply must send the uploaded CSV ids (#80)."""
    scenario = r"""
  loadPage();
  await openConversation();
  fixtures.requests[REQUEST_INPUT] = requestFixture(
    REQUEST_INPUT,
    'awaiting_input',
    interactionFixture('input', {
      kind: 'input',
      question: 'this application needs: sales csv',
      missing_fields: ['sales csv'],
      candidate: {
        rank: 1, application_version_id: CANDIDATE_A,
        application_name: 'sales report', purpose: 'sales report',
      },
    }),
  );

  // Submit a request; the page renders its input interaction from the 202 body.
  elementByID('chat-message').value = 'build the quarterly report';
  elementByID('chat-message-form').submit();
  await flush();
  assert.strictEqual(elementByID('interaction-panel').hidden, false,
    'the submitted request interaction was not rendered');

  // Upload the CSV the request is missing through the real attachment handlers.
  elementByID('chat-attachment-input').files = [
    { arrayBuffer: async () => new ArrayBuffer(8) },
  ];
  elementByID('chat-attachment-upload').click();
  await flush();
  assert.strictEqual(elementByID('chat-attachment-lines').children.length, 1,
    'the uploaded attachment was not picked up by the page');

  elementByID('interaction-message').value = 'sales csv attached';
  elementByID('interaction-reply').click();
  await flush();

  const replies = calls.filter((call) => call.kind === 'reply');
  assert.strictEqual(replies.length, 1, JSON.stringify(calls.map((call) => call.path)));
  const body = replies[0].body;
  assert.strictEqual(body.message, 'sales csv attached', JSON.stringify(body));
  assert.strictEqual(JSON.stringify(body.artifact_ids), JSON.stringify([CSV_ARTIFACT]),
    'the reply did not carry the uploaded attachment: ' + JSON.stringify(body));
  process.stdout.write('reply artifacts ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t079-reply-artifacts", scenario)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "reply artifacts ok" in completed.stdout
