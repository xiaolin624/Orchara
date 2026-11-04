"""F09/T054: the personal-memory page shows real candidates and the memory list.

Structural assertions inspect what the compiled API serves; ``memories.js`` is
executed under Node with the same public helpers the page uses, so the candidate
projection, the exact decision body, the confirm/reject wording and the
no-default rule are observed behaviour rather than source text.

The DOM/browser boundary is the same as every other page ticket: ``not_run`` (no
browser driver); the helpers are pure and Node-tested.
"""

from __future__ import annotations

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


def test_the_memory_page_is_served_with_the_real_sections(t054_stack) -> None:
    response = t054_stack.server.http.get("/memories")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="candidates-list"',
        'id="candidates-status"',
        'id="candidates-empty"',
        'id="candidates-next"',
        'id="memories-list"',
        'id="memories-status"',
        'id="memories-empty"',
        'id="memories-next"',
        "待确认",
        "已确认",
        "已拒绝",
        "已过期",
        "来源",
        "应用范围",
        "过期时间",
        "当前记忆",
        "不可召回",
        "不会自动保存",
        "全部使用 textContent",
    ):
        assert marker in body, f"memories.html is missing {marker!r}"
    # No credential material and no placeholder rows are shipped.
    assert "secret" not in body
    assert "localStorage" not in body


def test_the_memory_script_is_served_with_the_expected_helpers(t054_stack) -> None:
    response = t054_stack.server.http.get("/assets/memories.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function candidateStateText(",
        "function describeCandidate(",
        "function describeMemory(",
        "function decisionPayload(",
        "function decisionConfirmationText(",
        "function decisionPath(",
        "function candidatesListPath(",
        "function memoriesListPath(",
        "function newDecisionKey(",
        "function decisionResultText(",
        "Idempotency-Key",
        "textContent",
    ):
        assert marker in body, f"memories.js is missing {marker!r}"
    # The page never builds markup from content: no innerHTML assignment.
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_memory_page_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "memory-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = {
  console,
  window: { location: { assign() {} }, confirm: () => true },
  document: { createElement: () => ({ appendChild() {}, setAttribute() {} }) },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/memories.js', 'utf8'), context);

// The four candidate states are translated, and an unknown state is not guessed.
assert.strictEqual(context.candidateStateText('pending'), '待确认');
assert.strictEqual(context.candidateStateText('confirmed'), '已确认');
assert.strictEqual(context.candidateStateText('rejected'), '已拒绝');
assert.strictEqual(context.candidateStateText('expired'), '已过期');
assert.strictEqual(context.candidateStateText('nonsense'), '未知状态');

// One real contract MemoryCandidate: the page renders exactly these facts.
const candidate = context.describeCandidate({
  id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  application_id: '33333333-3333-4333-8333-333333333333',
  text: 'The user prefers concise weekly summaries.',
  state: 'pending',
  expires_at: '2026-11-11T08:05:49Z',
});
assert.strictEqual(candidate.stateText, '待确认');
assert.strictEqual(candidate.isPending, true);
assert.strictEqual(candidate.applicationText, '33333333-3333-4333-8333-333333333333');
assert.strictEqual(candidate.expiresAt, '2026-11-11T08:05:49Z');
assert.ok(candidate.sourceText.indexOf('来源') === 0, candidate.sourceText);
assert.ok(candidate.sourceText.indexOf('Run') >= 0, candidate.sourceText);
assert.strictEqual(candidate.text, 'The user prefers concise weekly summaries.');
assert.strictEqual(candidate.idShort, 'aaaaaaaa');
assert.strictEqual(context.describeCandidate({}).isPending, false);

// One real contract Memory: no state, no expiry; revision is shown.
const memory = context.describeMemory({
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  application_id: '33333333-3333-4333-8333-333333333333',
  revision: 1,
  text: 'The user prefers concise weekly summaries.',
});
assert.strictEqual(memory.revision, 1);
assert.strictEqual(memory.revisionText, 'revision 1');
assert.strictEqual(memory.text, 'The user prefers concise weekly summaries.');
assert.strictEqual(memory.idShort, 'bbbbbbbb');

// A decision needs an explicit choice; there is no default and no free text.
assert.throws(() => context.decisionPayload(undefined));
assert.throws(() => context.decisionPayload('maybe'));
assert.strictEqual(
  JSON.stringify(context.decisionPayload('confirm')),
  JSON.stringify({ decision: 'confirm' }),
);
assert.strictEqual(
  JSON.stringify(context.decisionPayload('reject')),
  JSON.stringify({ decision: 'reject' }),
);
assert.ok(context.decisionConfirmationText('confirm').indexOf('确认') >= 0);
assert.ok(context.decisionConfirmationText('confirm').indexOf('不会自动保存') >= 0);
assert.ok(context.decisionConfirmationText('reject').indexOf('不会生成记忆') >= 0);

// A fresh Idempotency-Key is minted per decision and two decisions never share one.
const keyOne = context.newDecisionKey();
const keyTwo = context.newDecisionKey();
assert.ok(typeof keyOne === 'string' && keyOne.length >= 8, keyOne);
assert.notStrictEqual(keyOne, keyTwo);

// Paths interpolate a raw id always through encodeURIComponent.
assert.strictEqual(
  context.decisionPath('a/b'),
  '/api/v1/memory-candidates/a%2Fb/decision',
);
assert.strictEqual(context.candidatesListPath(), '/api/v1/memory-candidates');
assert.strictEqual(
  context.candidatesListPath({ cursor: 'c1', limit: 20, applicationId: 'a b' }),
  '/api/v1/memory-candidates?cursor=c1&limit=20&application_id=a%20b',
);
assert.strictEqual(context.memoriesListPath(), '/api/v1/memories');
assert.strictEqual(
  context.memoriesListPath({ cursor: null, limit: 5 }),
  '/api/v1/memories?limit=5',
);

// The decision result is stated with the server's own answer, not optimistically.
const confirmed = context.decisionResultText({
  candidate_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  state: 'confirmed',
  memory_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
});
assert.ok(confirmed.indexOf('已确认') >= 0, confirmed);
assert.ok(confirmed.indexOf('bbbbbbbb') >= 0, confirmed);
const rejected = context.decisionResultText({
  candidate_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  state: 'rejected',
  memory_id: null,
});
assert.ok(rejected.indexOf('已拒绝') >= 0, rejected);
assert.ok(rejected.indexOf('未保存') >= 0, rejected);

process.stdout.write('memory page behaviour ok\n');
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
    assert "memory page behaviour ok" in completed.stdout
