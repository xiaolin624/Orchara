"""T084 (#85): the conversation page explains failed requests and shows the queue.

The first test asserts what the compiled API serves. The second executes the
real ``chat.js``/``app.js`` bytes under Node with a minimal DOM shim: page load,
conversation selection, the 800ms request poll, the submit path and the queue row
clicks all run the product's own code. No browser is driven (same boundary as
T023/T027/T033/T074); the DOM is a hand-built element tree, never a mock of the
page logic.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from support import stack as st

NODE = shutil.which("node")
WEB = st.PRODUCT_ROOT / "apps" / "web" / "src"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_chat_page_serves_the_failure_explanation_surface(f08_stack) -> None:
    page = f08_stack.server.http.get("/chat")
    assert page.status == 200, page.text
    assert 'id="chat-request-error"' in page.text
    assert 'data-error-code=""' in page.text

    asset = f08_stack.server.http.get("/assets/chat.js")
    assert asset.status == 200, asset.text
    script = asset.text
    for marker in (
        "REQUEST_FAILURE_REASONS",
        "function describeRequestFailure(",
        "function applyRequestFailure(",
        "applyRequestFailure(document, request);",
        "no_matching_application",
        "interaction_expired",
        "interaction_closed",
        "未找到匹配的应用",
        "dataset.errorCode",
    ):
        assert marker in script, f"chat.js lost the T084 failure surface: {marker!r}"
    # The failure text is separate from the seven-state label: data-state semantics
    # (the state label) must keep the existing vocabulary.
    assert "failed: '失败'" in script
    for forbidden in ("object_key", "ORCHARA_", "BEGIN PRIVATE", "secret"):
        assert forbidden not in script, forbidden


def test_chat_js_explains_failures_and_keeps_the_queue_visible(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t084-chat-dom.js"
    harness.write_text(
        textwrap.dedent(
            r"""
            'use strict';

            const fs = require('fs');
            const vm = require('vm');
            const assert = require('assert');

            const webDir = __WEB_DIR__;
            const CONV = __CONV__;
            const R1 = __R1__;
            const R2 = __R2__;
            const R3 = __R3__;

            // The minimal DOM the real page code touches: elements by id, child
            // lists, listeners, dataset and a textContent setter that records every
            // assignment (so a transient queue-position write is observable).
            class Element {
              constructor(tag, id) {
                this.tagName = tag;
                this.id = id || '';
                this.children = [];
                this.listeners = {};
                this.dataset = {};
                this._text = '';
                this.textHistory = [];
                this.value = '';
                this.checked = false;
                this.hidden = false;
                this.className = '';
                this.files = [];
              }
              appendChild(child) { this.children.push(child); return child; }
              replaceChildren() { this.children = []; }
              addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
              set textContent(value) {
                this._text = String(value === undefined || value === null ? '' : value);
                this.textHistory.push(this._text);
              }
              get textContent() { return this._text; }
            }

            const elements = new Map();
            const elementFor = (id) => {
              if (!elements.has(id)) elements.set(id, new Element('div', id));
              return elements.get(id);
            };
            const document = {
              getElementById: (id) => elementFor(id),
              createElement: (tag) => new Element(tag),
              querySelectorAll: (selector) =>
                selector === '#chat-messages li' ? elementFor('chat-messages').children : [],
              querySelector: () => null,
            };

            function message(id, requestID) {
              return { id, role: 'user', request_id: requestID, run_id: null,
                       body_artifact_id: null, created_at: '2026-11-05T00:00:00Z',
                       kind: 'request' };
            }
            function failed(requestID, code) {
              return { id: requestID, state: 'failed', run_id: null, interaction: null,
                       error_code: code };
            }
            function queued(requestID) {
              return { id: requestID, state: 'queued', run_id: null, interaction: null,
                       error_code: null };
            }
            const GET = {
              '/api/v1/auth/me': { status: 200, body: { csrf_token: 'test-csrf', principal_id: 'p-1' } },
              '/api/v1/conversations': {
                status: 200,
                body: { items: [{ id: CONV, created_at: '2026-11-05T00:00:00Z' }] },
              },
              ['/api/v1/conversations/' + CONV + '/messages']: {
                status: 200,
                body: { items: [message('m3', R3), message('m2', R2), message('m1', R1)] },
              },
              ['/api/v1/requests/' + R1]: { status: 200, body: failed(R1, 'no_matching_application') },
              ['/api/v1/requests/' + R2]: {
                status: 200, body: queued(R2), headers: { 'X-Request-Queue-Position': '2' },
              },
              ['/api/v1/requests/' + R3]: { status: 200, body: failed(R3, 'interaction_expired') },
            };
            const POST = {
              '/api/v1/requests': {
                status: 202, body: failed(R1, 'no_matching_application'),
              },
            };
            function response(entry) {
              return {
                ok: entry.status >= 200 && entry.status < 300,
                status: entry.status,
                headers: {
                  get: (name) => (entry.headers && entry.headers[name]) || null,
                },
                text: async () => JSON.stringify(entry.body),
              };
            }
            async function fetch(path, init) {
              const method = (init && init.method) || 'GET';
              const table = method === 'POST' ? POST : GET;
              const entry = table[path];
              assert.ok(entry, 'unscripted ' + method + ' ' + path);
              return response(entry);
            }

            const timers = [];
            const context = {
              console,
              document,
              fetch,
              window: { location: { assign() {} }, open() {} },
              crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
              setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
              clearTimeout: () => {},
              hostTick: () => new Promise((resolve) => setImmediate(resolve)),
            };
            vm.createContext(context);
            // C6.01 (#155): runstream.js is part of the page's real load order;
            // C6.04 (#158): the guard follows it, before the page scripts.
for (const file of ['app.js', 'runstream.js', 'runstate-guard.js', 'attachments.js', 'chat.js']) {
              vm.runInContext(fs.readFileSync(webDir + '/assets/' + file, 'utf8'), context);
            }

            async function settle(rounds) {
              for (let index = 0; index < (rounds || 4); index += 1) {
                await context.hostTick();
              }
            }
            // The page's click listeners start an async render and return nothing,
            // so a test click is "invoke the real listener, then let the microtask
            // queue drain".
            async function click(element) {
              assert.strictEqual(element.listeners.click.length >= 1, true);
              element.listeners.click[0]();
              await settle(6);
            }

            (async () => {
              // -- page load: identity, conversation list, nothing else ---------
              for (let index = 0; index < 20 && elementFor('conversation-list').children.length === 0; index += 1) {
                await context.hostTick();
              }
              assert.strictEqual(elementFor('conversation-list').children.length, 1, 'conversation list');
              const conversationButton = elementFor('conversation-list').children[0].children[0];
              await click(conversationButton);

              // -- the ordered queue is rendered with real positions -------------
              const rows = elementFor('queue-list').children;
              assert.strictEqual(rows.length, 3, 'three queue rows: ' + rows.length);
              const rowText = (row) => row.children[0].textContent;
              assert.ok(rowText(rows[0]).startsWith('1. 失败 · ' + R1.slice(0, 8)), rowText(rows[0]));
              assert.ok(rowText(rows[1]).startsWith('2. 排队中 · ' + R2.slice(0, 8)), rowText(rows[1]));
              assert.ok(rowText(rows[2]).startsWith('3. 失败 · ' + R3.slice(0, 8)), rowText(rows[2]));
              assert.ok(elementFor('chat-queue-status').textHistory.some(
                (text) => text.indexOf('3 项请求') >= 0), 'queue summary was never rendered');
              // A queued row is cancellable from the queue itself.
              assert.strictEqual(rows[1].children.length, 2, 'queued row has a cancel button');

              // -- a failed request shows the reason, not only "失败" ------------
              await click(rows[0].children[0]);
              const failureElement = elementFor('chat-request-error');
              assert.strictEqual(elementFor('chat-request-state').textContent, '失败');
              assert.strictEqual(failureElement.hidden, false);
              assert.strictEqual(failureElement.dataset.errorCode, 'no_matching_application');
              assert.ok(failureElement.textContent.indexOf('未找到匹配的应用') >= 0, failureElement.textContent);
              assert.ok(failureElement.textContent.indexOf('换一种说法') >= 0, failureElement.textContent);
              assert.ok(failureElement.textHistory.some((text) => text.indexOf('未找到匹配的应用') >= 0));

              // -- an expired interaction explains the expiry and how to recover -
              await click(rows[2].children[0]);
              assert.strictEqual(failureElement.dataset.errorCode, 'interaction_expired');
              assert.ok(failureElement.textContent.indexOf('过期') >= 0, failureElement.textContent);
              assert.ok(failureElement.textContent.indexOf('重新提交') >= 0, failureElement.textContent);

              // -- the queued request renders its server-side queue position -----
              await click(rows[1].children[0]);
              assert.ok(
                elementFor('chat-queue-status').textHistory.some(
                  (text) => text.indexOf('排队位置：第 2 位') >= 0),
                'queue position header was never rendered: ' +
                  JSON.stringify(elementFor('chat-queue-status').textHistory),
              );

              // -- the submit path (POST response) shows the same explanation ----
              elementFor('chat-message').value = 'please summarise this csv';
              const submit = elementFor('chat-message-form').listeners.submit[0];
              await submit({ preventDefault() {} });
              await settle(4);
              assert.strictEqual(failureElement.dataset.errorCode, 'no_matching_application');
              assert.ok(failureElement.textContent.indexOf('未找到匹配的应用') >= 0);

              // -- unknown codes are said out loud; non-failures hide the block --
              const unknown = context.describeRequestFailure(
                { state: 'failed', error_code: 'some_new_code' });
              assert.strictEqual(unknown.visible, true);
              assert.ok(unknown.text.indexOf('some_new_code') >= 0, unknown.text);
              context.applyRequestFailure(document, { state: 'failed', error_code: 'some_new_code' });
              assert.strictEqual(failureElement.dataset.errorCode, 'some_new_code');
              context.applyRequestFailure(document, { state: 'dispatched', error_code: 'x' });
              assert.strictEqual(failureElement.hidden, true);
              assert.strictEqual(failureElement.dataset.errorCode, '');

              // The seven-state vocabulary is unchanged by the new block.
              assert.strictEqual(context.describeRequestState('failed'), '失败');
              assert.strictEqual(context.describeRequestState('queued'), '排队中');

              console.log('t084 chat.js DOM harness: ok');
            })().catch((error) => {
              console.error(error && error.stack ? error.stack : String(error));
              process.exit(1);
            });
            """
        )
        .replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__CONV__", json.dumps("11111111-1111-4111-8111-111111111111"))
        .replace("__R1__", json.dumps("22222222-2222-4222-8222-222222222222"))
        .replace("__R2__", json.dumps("33333333-3333-4333-8333-333333333333"))
        .replace("__R3__", json.dumps("44444444-4444-4444-8444-444444444444")),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        cwd=str(st.PRODUCT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
    assert "t084 chat.js DOM harness: ok" in completed.stdout, completed.stdout
