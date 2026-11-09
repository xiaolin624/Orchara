"""T074 (#75): the conversation page and its Node-executable helpers.

Structural assertions are about what the real compiled API serves; the second test
executes ``chat.js`` under Node with the same bytes a browser receives and observes
the pure helpers' behaviour (queue text, handling-version wording, handover
confirmation, idempotency attempt). No DOM is driven: that stays the same boundary
as T023/T027/T033.
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


def test_the_conversation_page_is_served_with_the_real_surfaces(f08_stack) -> None:
    response = f08_stack.server.http.get("/chat")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="conversation-list"',
        'id="conversation-create"',
        'id="chat-panel"',
        'id="chat-message-form"',
        'id="chat-version-id"',
        'id="chat-handoff-source-runs"',
        'id="chat-handoff-summary"',
        'id="chat-handoff-confirmed"',
        'id="chat-attachments"',
        'id="chat-attachment-input"',
        'id="request-panel"',
        'id="chat-request-state"',
        'id="chat-handling"',
        'id="chat-cancel"',
        'id="queue-panel"',
        'id="queue-list"',
        'id="chat-messages"',
        "会话",
        "/assets/chat.js",
    ):
        assert marker in body, f"chat.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body

    asset = f08_stack.server.http.get("/assets/chat.js")
    assert asset.status == 200, asset.text
    script = asset.text
    for marker in (
        "function conversationPath(",
        "function conversationMessagesPath(",
        "function requestPath(",
        "function requestCancelPath(",
        "function describeRequestState(",
        "function describeQueue(",
        "function describeInteraction(",
        "function describeHandoff(",
        "function describeHandling(",
        "function requestFacts(",
        "function submitBody(",
        "function handoffBody(",
        "function newSubmissionAttempt(",
        "X-Request-Application-Version-Id",
        "Idempotency-Key",
    ):
        assert marker in script, f"chat.js is missing {marker!r}"
    # No secret material is embedded and no object key reaches the page.
    for forbidden in ("object_key", "ORCHARA_", "secret", "BEGIN PRIVATE"):
        assert forbidden not in script, forbidden


def test_chat_js_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "chat-helpers.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} }, open() {} } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/attachments.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/chat.js', 'utf8'), context);

// Paths are built from the id and never from a caller URL.
assert.strictEqual(context.conversationPath('c1'), '/api/v1/conversations/c1');
assert.strictEqual(context.conversationPath('a b/c'), '/api/v1/conversations/a%20b%2Fc');
assert.strictEqual(
  context.conversationMessagesPath('c1'), '/api/v1/conversations/c1/messages');
assert.strictEqual(context.requestPath('r1'), '/api/v1/requests/r1');
assert.strictEqual(context.requestCancelPath('r1'), '/api/v1/requests/r1/cancel');
assert.strictEqual(context.artifactContentPath('a1'), '/api/v1/artifacts/a1/content');

// Seven contract states, and an unknown state is said out loud.
const labels = [
  ['queued', '排队中'], ['routing', '正在选择应用'], ['awaiting_input', '等待补充信息'],
  ['awaiting_selection', '等待选择应用'], ['dispatched', '已分派到处理版本'],
  ['cancelled', '已取消'], ['failed', '失败'],
];
for (const [state, label] of labels) {
  assert.strictEqual(context.describeRequestState(state), label, state);
}
assert.strictEqual(context.describeRequestState('weird'), '未知状态');
assert.strictEqual(context.describeRequestState(''), '未知状态');

// Queue text is derived from the real order; it never invents a position.
const queue = context.describeQueue(
  [{ id: 'r1', state: 'routing' }, { id: 'r2', state: 'queued' }, { id: 'r3', state: 'queued' }],
  'r2',
);
assert.strictEqual(queue.rows.length, 3);
assert.strictEqual(JSON.stringify(queue.rows.map((row) => row.position)), '[1,2,3]');
assert.strictEqual(queue.rows[0].active, false);
assert.strictEqual(queue.rows[1].active, true);
assert.strictEqual(queue.rows[1].label, '排队中');
assert.ok(queue.text.indexOf('3 项请求') >= 0, queue.text);
assert.ok(queue.text.indexOf('3 项未完成') >= 0, queue.text);
assert.ok(queue.text.indexOf('占逻辑会话槽') >= 0, queue.text);
const cancelling = context.describeQueue([{ id: 'r1', state: 'cancelled' }], 'r1');
assert.strictEqual(cancelling.rows[0].cancelable, false);
assert.strictEqual(context.describeQueue(null, 'x').rows.length, 0);

// Pending interaction: nothing is rendered when there is none.
assert.strictEqual(context.describeInteraction(null).visible, false);
assert.strictEqual(context.describeInteraction(null).closed, true);
const interaction = context.describeInteraction({
  interaction_id: 'i1', kind: 'selection', expires_at: '2026-11-05T00:00:00Z',
  proposal: { question: '请选择要使用的应用', candidates: [
    { application_version_id: 'v1', label: '销售 v1' },
  ] },
});
assert.strictEqual(interaction.visible, true);
assert.strictEqual(interaction.kind, 'selection');
assert.strictEqual(interaction.prompt, '请选择要使用的应用');
assert.strictEqual(interaction.candidates.length, 1);
assert.strictEqual(interaction.closed, false);

// Handover projection.
assert.strictEqual(context.describeHandoff(null).visible, false);
const handoff = context.describeHandoff({
  summary: '只交接摘要', artifact_ids: ['a1', 'a2'], confirmed: true,
});
assert.strictEqual(handoff.summary, '只交接摘要');
assert.strictEqual(handoff.artifactCount, 2);
assert.strictEqual(handoff.confirmed, true);

// Handling version: never guessed before a Run exists.
assert.strictEqual(context.describeHandling(null, null, null).text, '尚无处理应用');
assert.strictEqual(
  context.describeHandling({ run_id: null }, null, null).text, '尚无处理应用');
assert.strictEqual(
  context.describeHandling({ run_id: 'u1' }, { state: 'queued' }, { version_no: 3 }).text,
  'v3 正在处理');
assert.strictEqual(
  context.describeHandling({ run_id: 'u1' }, { state: 'succeeded' }, { version_no: 3 }).text,
  'v3 已处理');
assert.strictEqual(
  context.describeHandling({ run_id: 'u1' }, { state: 'cancelled' }, { version_no: 3 }).text,
  'v3 已取消');

// Documented non-contract response headers are read by exact name.
function headers(map) { return { get: (name) => map[name] || null }; }
const facts = context.requestFacts(headers({
  'X-Request-Conversation-Id': 'c1',
  'X-Request-Application-Version-Id': 'v1',
  'X-Request-Application-Version-No': '3',
  'X-Request-Queue-Position': '2',
  'X-Request-Interaction-Id': 'i1',
  'X-Request-Correlation-Id': 'k1',
}));
assert.strictEqual(facts.applicationVersionId, 'v1');
assert.strictEqual(facts.versionNo, 3);
assert.strictEqual(facts.queuePosition, 2);
assert.strictEqual(facts.correlationId, 'k1');
assert.strictEqual(JSON.stringify(context.requestFacts(null)), JSON.stringify({
  conversationId: '', applicationVersionId: '', versionNo: 0,
  interactionId: '', correlationId: '', queuePosition: 0,
}));

// The submit body is exactly RequestInput; no extra field is invented.
const cid = '11111111-2222-4333-8444-555555555555';
const vid = '66666666-7777-4888-9999-aaaaaaaaaaaa';
const aid = 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff';
const body = context.submitBody(cid, '你好', [aid], vid, null);
assert.strictEqual(JSON.stringify(body), JSON.stringify({
  conversation_id: cid, message: '你好', artifact_ids: [aid], application_version_id: vid,
}));
assert.throws(() => context.submitBody(cid, '', [], null, null), /不能为空/);
assert.throws(() => context.submitBody('nope', 'x', [], null, null), /会话/);
assert.throws(
  () => context.submitBody(cid, 'x', [aid, aid, aid, aid, aid, aid], null, null),
  /最多 5 个附件/);

// Handover confirmation is a local gate before the server gate.
assert.throws(
  () => context.handoffBody('run-1', '摘要', true, []), /UUID/);
const confirmedHandoff = context.handoffBody(
  [vid, cid], '只交接这两个来源', true, [aid]);
assert.strictEqual(confirmedHandoff.confirmed, true);
assert.strictEqual(JSON.stringify(confirmedHandoff.source_run_ids), JSON.stringify([vid, cid]));
assert.strictEqual(JSON.stringify(confirmedHandoff.artifact_ids), JSON.stringify([aid]));
assert.throws(
  () => context.handoffBody([vid], '摘要', false, []),
  (error) => error.code === 'handoff_not_confirmed');

// One submission attempt owns one key (retry reuses the same object/key).
const attempt = context.newSubmissionAttempt(body);
assert.ok(attempt.key && attempt.key.length >= 8, attempt.key);
assert.strictEqual(attempt.body, body);
assert.strictEqual(JSON.stringify(context.submitRequestHeaders(attempt)), JSON.stringify({
  'Idempotency-Key': attempt.key,
}));

// C6.05 (review-2): chat.js has no Request-only polling rule. The shared
// shouldPoll owns the decision: a dispatched request keeps observing the Run
// (including before the first Run read), and stops only on a terminal Run.
assert.strictEqual(typeof context.shouldPollRequest, 'undefined');
assert.strictEqual(context.OrcharaRunState.shouldPoll('queued', '', 0, 10), true);
assert.strictEqual(context.OrcharaRunState.shouldPoll('routing', '', 0, 10), true);
assert.strictEqual(context.OrcharaRunState.shouldPoll('dispatched', '', 0, 10), true);
assert.strictEqual(context.OrcharaRunState.shouldPoll('dispatched', 'running', 0, 10), true);
assert.strictEqual(context.OrcharaRunState.shouldPoll('dispatched', 'succeeded', 0, 10), false);
assert.strictEqual(context.OrcharaRunState.shouldPoll('cancelled', 'running', 0, 10), false);
assert.strictEqual(context.OrcharaRunState.shouldPoll('failed', 'running', 0, 10), false);
assert.strictEqual(context.OrcharaRunState.shouldPoll('queued', '', 10, 10), false);

// The message projection labels each kind and only requests/results fetch a body.
assert.strictEqual(
  context.messageLine({ role: 'user', kind: 'request', body_artifact_id: 'a1' }).fetchBody,
  true);
assert.strictEqual(
  context.messageLine({ role: 'system', kind: 'status', body_artifact_id: null }).fetchBody,
  false);
assert.ok(
  context.messageLine({ role: 'assistant', kind: 'result', body_artifact_id: 'a1' })
    .label.indexOf('结果') >= 0);
assert.strictEqual(context.attachmentPickerLines([aid])[0].id, aid);

process.stdout.write('chat.js helper behaviour ok\n');
""".replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "chat.js helpers did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
