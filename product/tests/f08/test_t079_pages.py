"""F08/T079: the chat page's interaction surface and its Node-executable helpers.

Structural assertions are about what the real compiled API serves; the second test
executes ``chat.js`` under Node with the same bytes a browser receives and observes
the pure helpers (candidate ordinals, question/missing fields, reply body/key).
No DOM is driven: that stays the same boundary as T023/T027/T033/T074.
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


def test_the_chat_page_serves_the_interaction_surface(f08_stack) -> None:
    response = f08_stack.server.http.get("/chat")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="interaction-panel"',
        'id="interaction-prompt"',
        'id="interaction-missing"',
        'id="interaction-expires"',
        'id="interaction-candidates"',
        'id="interaction-select"',
        'id="interaction-message"',
        'id="interaction-reply"',
        'id="interaction-status"',
        "候选应用",
        "/assets/chat.js",
    ):
        assert marker in body, f"chat.html is missing {marker!r}"
    # No score/probability vocabulary on the page: candidates are ordinals.
    for forbidden in ("概率", "置信度", "calibrat"):
        assert forbidden not in body, forbidden

    asset = f08_stack.server.http.get("/assets/chat.js")
    assert asset.status == 200, asset.text
    script = asset.text
    for marker in (
        "function requestReplyPath(",
        "function describeInteraction(",
        "function candidateLines(",
        "function interactionMode(",
        "function interactionReplyBody(",
        "function newInteractionAttempt(",
        "function interactionReplyHeaders(",
        "interaction_closed",
        "过期后需要重新提交需求",
        "Idempotency-Key",
    ):
        assert marker in script, f"chat.js is missing {marker!r}"
    for forbidden in ("概率", "置信度", "score", "probability", "object_key", "ORCHARA_"):
        assert forbidden not in script, forbidden


def test_chat_js_interaction_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t079-chat-helpers.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, crypto, window: { location: { assign() {} }, open() {} } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/attachments.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/chat.js', 'utf8'), context);

// Paths are built from the id, never from a caller URL.
assert.strictEqual(context.requestReplyPath('r1'), '/api/v1/requests/r1/reply');
assert.strictEqual(
  context.requestReplyPath('a b/c'), '/api/v1/requests/a%20b%2Fc/reply');

const interactionID = '11111111-2222-4333-8444-555555555555';
const versionA = '66666666-7777-4888-9999-aaaaaaaaaaaa';
const versionB = '77777777-8888-4999-8aaa-bbbbbbbbbbbb';
const artifact = 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff';

const selection = context.describeInteraction({
  interaction_id: interactionID,
  kind: 'selection',
  expires_at: '2026-11-05T00:00:00Z',
  proposal: {
    kind: 'selection',
    question: '有多个应用可能适合这个需求，请选择一个继续。',
    candidates: [
      { rank: 1, application_version_id: versionA, application_name: '销售报表',
        version_no: 3, purpose: 'sales report', required_inputs: ['sales csv'] },
      { rank: 2, application_version_id: versionB, application_name: '季度报表',
        version_no: 4, purpose: 'quarterly report', required_inputs: [] },
    ],
  },
});
assert.strictEqual(selection.visible, true);
assert.strictEqual(selection.kind, 'selection');
assert.strictEqual(selection.prompt, '有多个应用可能适合这个需求，请选择一个继续。');
assert.strictEqual(JSON.stringify(selection.candidates.map((c) => c.rank)), '[1,2]');
assert.strictEqual(JSON.stringify(selection.candidates.map((c) => c.id)), JSON.stringify([versionA, versionB]));
assert.strictEqual(selection.ordinalOnly, true);
assert.strictEqual(selection.missingFields.length, 0);
assert.strictEqual(context.interactionMode({
  kind: 'selection', proposal: { candidates: selection.candidates },
}), 'selection');

const lines = context.candidateLines({
  kind: 'selection',
  proposal: { candidates: [
    { rank: 1, application_version_id: versionA, application_name: '销售报表',
      version_no: 3, purpose: 'sales report' },
    { rank: 2, application_version_id: versionB, application_name: '季度报表',
      version_no: 4, purpose: 'quarterly report' },
  ] },
});
assert.strictEqual(lines.length, 2);
assert.ok(lines[0].text.indexOf('候选 1') === 0, lines[0].text);
assert.ok(lines[1].text.indexOf('候选 2') === 0, lines[1].text);
for (const line of lines) {
  for (const forbidden of ['概率', '置信度', 'score', 'probability']) {
    assert.ok(line.text.indexOf(forbidden) < 0, line.text);
  }
}

const input = context.describeInteraction({
  interaction_id: interactionID,
  kind: 'input',
  proposal: {
    kind: 'input',
    question: '该应用需要补充以下输入：sales csv、region mapping',
    missing_fields: ['sales csv', 'region mapping'],
    candidate: { rank: 1, application_version_id: versionA,
      application_name: '销售报表', purpose: 'sales report' },
  },
});
assert.strictEqual(input.kind, 'input');
assert.strictEqual(JSON.stringify(input.missingFields), JSON.stringify(['sales csv', 'region mapping']));
assert.strictEqual(input.candidate.id, versionA);
assert.strictEqual(context.interactionMode({
  kind: 'input', proposal: { missing_fields: ['x'] },
}), 'input');
assert.strictEqual(context.describeInteraction(null).visible, false);
assert.strictEqual(context.describeInteraction(null).closed, true);

// The reply body is exactly InteractionReply: one of the three alternatives.
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(context.interactionReplyBody(
    interactionID, '补充说明', null, null))),
  { interaction_id: interactionID, message: '补充说明' });
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(context.interactionReplyBody(
    interactionID, '', versionA, []))),
  { interaction_id: interactionID, candidate_version_id: versionA });
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(context.interactionReplyBody(
    interactionID, '', null, [artifact]))),
  { interaction_id: interactionID, artifact_ids: [artifact] });
assert.throws(
  () => context.interactionReplyBody(interactionID, '', null, []), /请选择候选或补充信息/);
assert.throws(() => context.interactionReplyBody('nope', 'x', null, null), /interaction_id/);
assert.throws(
  () => context.interactionReplyBody(interactionID, 'x'.repeat(32001), null, null),
  /32000/);

// One reply attempt owns one key; a retry reuses the same object.
const attempt = context.newInteractionAttempt({ interaction_id: interactionID, message: 'x' });
assert.ok(attempt.key && attempt.key.length >= 8, attempt.key);
assert.strictEqual(attempt.body.message, 'x');
assert.strictEqual(JSON.stringify(context.interactionReplyHeaders(attempt)),
  JSON.stringify({ 'Idempotency-Key': attempt.key }));

process.stdout.write('chat.js interaction helpers ok\n');
""".replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "chat.js interaction helpers ok" in completed.stdout
