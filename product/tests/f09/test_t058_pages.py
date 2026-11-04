"""F09/T058: the memory page shows the ETag conflict and the deletion state.

Structural assertions inspect what the compiled API serves; ``memories.js`` is
executed under Node with the same public helpers the page uses, so the conflict
recovery plan, the update body/header, the deletion-state projection and the
honest "cleanup is asynchronous" wording are observed behaviour rather than
source text.

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


def test_the_memory_page_serves_the_revision_and_deletion_sections(t058_stack) -> None:
    response = t058_stack.server.http.get("/memories")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="memory-action-status"',
        'id="memories-list"',
        "编辑记忆",
        "保存修改",
        "用最新版本重试",
        "版本冲突",
        "删除这条记忆",
        "删除状态",
        "If-Match",
        "停止召回",
        "物理副本",
        "textContent",
    ):
        assert marker in body, f"memories.html is missing {marker!r}"
    # The page never promises a physical purge this ticket does not perform.
    assert "物理副本已删除" not in body
    assert "secret" not in body
    assert "localStorage" not in body


def test_the_memory_script_serves_the_t058_helpers(t058_stack) -> None:
    response = t058_stack.server.http.get("/assets/memories.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function memoryPath(",
        "function etagFromHeaders(",
        "function updatePayload(",
        "function updateHeaders(",
        "function preconditionPlan(",
        "function conflictText(",
        "function updateResultText(",
        "function deletionStateText(",
        "function describeDeletion(",
        "function deletionResultText(",
        "If-Match",
        "textContent",
    ):
        assert marker in body, f"memories.js is missing {marker!r}"
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_conflict_recovery_and_deletion_state_execute_under_node(
    node_binary: str, tmp_path
) -> None:
    harness = tmp_path / "memory-revision-page.js"
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

// The detail path interpolates a raw id always through encodeURIComponent.
assert.strictEqual(context.memoryPath('a/b'), '/api/v1/memories/a%2Fb');
assert.strictEqual(
  context.memoryPath('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'),
  '/api/v1/memories/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
);

// The strong ETag is read from the response headers, both shapes the page meets.
const headers = { get: (name) => (name === 'ETag' ? '"2"' : null) };
assert.strictEqual(context.etagFromHeaders(headers), '"2"');
assert.strictEqual(context.etagFromHeaders({ ETag: '"3"' }), '"3"');
assert.strictEqual(context.etagFromHeaders({ etag: '"4"' }), '"4"');
assert.strictEqual(context.etagFromHeaders({}), '');
assert.strictEqual(context.etagFromHeaders(null), '');

// The update body is exactly the contract's {text, confirmed:true}; blank and
// oversized text never leave the page.
assert.strictEqual(
  JSON.stringify(context.updatePayload('  The user moved to Shanghai.  ')),
  JSON.stringify({ text: '  The user moved to Shanghai.  ', confirmed: true }),
);
assert.throws(() => context.updatePayload('   '));
assert.throws(() => context.updatePayload(''));
assert.throws(() => context.updatePayload('记'.repeat(4001)));

// A write must carry the ETag it read; there is no "save anyway" path.
assert.strictEqual(
  JSON.stringify(context.updateHeaders('"2"')),
  JSON.stringify({ 'If-Match': '"2"' }),
);
assert.throws(() => context.updateHeaders(''));
assert.throws(() => context.updateHeaders(null));

// A 412 is a recoverable conflict: the page learns the current ETag and the
// current revision and never claims it overwrote the other writer.
const conflict = context.preconditionPlan({
  code: 'precondition_failed',
  status: 412,
  message: 'the memory has moved on',
  details: { current_revision: 2, current_etag: '"2"' },
});
assert.strictEqual(conflict.kind, 'conflict');
assert.strictEqual(conflict.isConflict, true);
assert.strictEqual(conflict.currentEtag, '"2"');
assert.strictEqual(conflict.currentRevision, 2);
assert.ok(conflict.message.indexOf('版本冲突') === 0, conflict.message);
assert.ok(conflict.message.indexOf('"2"') >= 0, conflict.message);
assert.ok(conflict.message.indexOf('revision 2') >= 0, conflict.message);
assert.ok(conflict.message.indexOf('静默覆盖') >= 0, conflict.message);

// A 428 is a page-side bug, not a conflict, and says how to recover.
const required = context.preconditionPlan({ code: 'precondition_required', status: 428 });
assert.strictEqual(required.kind, 'precondition_required');
assert.strictEqual(required.isConflict, false);
assert.ok(required.message.indexOf('If-Match') >= 0, required.message);

// Any other refusal keeps the server's own code and message.
const failed = context.preconditionPlan({
  code: 'resource_not_found',
  status: 404,
  message: 'that memory does not exist',
});
assert.strictEqual(failed.kind, 'failed');
assert.ok(failed.message.indexOf('resource_not_found') >= 0, failed.message);

// A successful save states the server's revision, not an optimistic claim.
const saved = context.updateResultText({
  id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  application_id: '33333333-3333-4333-8333-333333333333',
  revision: 3,
  text: 'The user moved to Shanghai.',
});
assert.ok(saved.indexOf('已保存') >= 0, saved);
assert.ok(saved.indexOf('revision 3') >= 0, saved);
assert.ok(saved.indexOf('bbbbbbbb') >= 0, saved);

// The four deletion states are translated and an unknown one is not guessed.
assert.strictEqual(context.deletionStateText('pending'), '待处理');
assert.strictEqual(context.deletionStateText('running'), '处理中');
assert.strictEqual(context.deletionStateText('completed'), '已完成');
assert.strictEqual(context.deletionStateText('failed'), '失败');
assert.strictEqual(context.deletionStateText('nonsense'), '未知删除状态');

// The page states the recall guarantee and the asynchronous cleanup honestly.
const job = {
  id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
  state: 'pending',
  resource_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
};
const deletion = context.describeDeletion(job);
assert.strictEqual(deletion.idShort, 'cccccccc');
assert.strictEqual(deletion.stateText, '待处理');
assert.strictEqual(deletion.resourceId, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
assert.ok(deletion.recallText.indexOf('停止召回') >= 0, deletion.recallText);
assert.ok(deletion.recallText.indexOf('缓存 epoch') >= 0, deletion.recallText);
assert.ok(deletion.cleanupText.indexOf('待处理') >= 0, deletion.cleanupText);
assert.ok(deletion.cleanupText.indexOf('删除治理') >= 0, deletion.cleanupText);
assert.ok(deletion.cleanupText.indexOf('不声称') >= 0, deletion.cleanupText);

const completed = context.describeDeletion({ ...job, state: 'completed' });
assert.strictEqual(completed.stateText, '已完成');
assert.ok(completed.cleanupText.indexOf('已完成') >= 0, completed.cleanupText);

const result = context.deletionResultText(job);
assert.ok(result.indexOf('已接受删除') >= 0, result);
assert.ok(result.indexOf('cccccccc') >= 0, result);
assert.ok(result.indexOf('待处理') >= 0, result);
assert.ok(result.indexOf('停止召回') >= 0, result);

// After a delete the page removes the row from the recall view by reloading the
// server list; the wording never claims the physical bytes are gone.
const deleted = context.memoryDeletedText('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb');
assert.ok(deleted.indexOf('已删除') >= 0, deleted);
assert.ok(deleted.indexOf('bbbbbbbb') >= 0, deleted);
assert.ok(deleted.indexOf('召回') >= 0, deleted);
assert.ok(deleted.indexOf('物理') === -1, deleted);
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
