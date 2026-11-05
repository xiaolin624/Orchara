"""F11/T069: the personal-data page's deletion progress.

Structural assertions inspect what the compiled API serves; ``personal-data.js``
is executed under Node with the same public helpers the page uses, so the path
construction, the bounded progress poll, the remaining-scope projection and the
honest "cleanup is asynchronous" wording are observed behaviour rather than source
text. The browser DOM boundary is ``not_run`` (no browser driver), like every
other page ticket.
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


def test_the_personal_data_page_serves_delete_and_progress_sections(t069_stack) -> None:
    response = t069_stack.server.http.get("/personal-data")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="memories-list"',
        'id="documents-list"',
        'id="artifact-id"',
        'id="delete-artifact"',
        'id="deletion-card"',
        'id="deletion-status"',
        "remaining",
        "物理副本",
        "deletion_job",
    ):
        assert marker in body, f"personal-data.html is missing {marker!r}"
    # The page never promises a purge this ticket does not perform.
    assert "物理副本已删除" not in body
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_personal_data_script_serves_the_t069_helpers(t069_stack) -> None:
    response = t069_stack.server.http.get("/assets/personal-data.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function resourcePath(",
        "function deletionPath(",
        "function artifactDeletionPlan(",
        "function deletionStateText(",
        "function remainingScopesText(",
        "function describeDeletionJob(",
        "function shouldPollDeletion(",
        "function deletionConfirmationText(",
        "function deletionResultText(",
        "DELETION_POLL_MAX_ATTEMPTS",
        "textContent",
    ):
        assert marker in body, f"personal-data.js is missing {marker!r}"
    assert "innerHTML" not in body
    assert "localStorage" not in body


def test_the_deletion_progress_helpers_execute_under_node(
    node_binary: str, tmp_path
) -> None:
    harness = tmp_path / "personal-data-page.js"
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
  document: { addEventListener() {}, createElement: () => ({ appendChild() {}, setAttribute() {} }) },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/personal-data.js', 'utf8'), context);

// Resource paths interpolate ids only through encodeURIComponent.
assert.strictEqual(context.resourcePath('memory', 'a/b'), '/api/v1/memories/a%2Fb');
assert.strictEqual(
  context.resourcePath('knowledge_document', '00000000-0000-4000-8000-000000000001'),
  '/api/v1/knowledge-documents/00000000-0000-4000-8000-000000000001',
);
assert.strictEqual(
  context.resourcePath('artifact', 'x'), '/api/v1/artifacts/x'
);
assert.strictEqual(
  context.deletionPath('a/b'), '/api/v1/deletions/a%2Fb'
);
assert.throws(() => context.resourcePath('principal', 'x'));

// An artifact id is validated before any request is sent.
const plan = context.artifactDeletionPlan(' 00000000-0000-4000-8000-000000000001 ');
assert.strictEqual(plan.artifactID, '00000000-0000-4000-8000-000000000001');
assert.strictEqual(plan.path, '/api/v1/artifacts/00000000-0000-4000-8000-000000000001');
for (const raw of ['', 'not-a-uuid', '00000000-0000-4000-8000', null, undefined]) {
  assert.throws(() => context.artifactDeletionPlan(raw));
}

// The contract state vocabulary is closed; an unknown value is not guessed.
assert.strictEqual(context.deletionStateText('pending'), '待处理');
assert.strictEqual(context.deletionStateText('running'), '执行中');
assert.strictEqual(context.deletionStateText('completed'), '已完成');
assert.strictEqual(context.deletionStateText('failed'), '失败');
assert.strictEqual(context.deletionStateText('weird'), '未知删除状态');
assert.strictEqual(context.deletionStateText(undefined), '未知删除状态');

// Remaining scopes are projected in the documented vocabulary order, and an
// unknown scope is stated as unknown rather than dropped.
const text = context.remainingScopesText([
  'backup_catalog', 'object_store', 'search_index', 'not_a_scope',
]);
assert.ok(text.indexOf('对象存储') < text.indexOf('检索索引'), text);
assert.ok(text.indexOf('检索索引') < text.indexOf('备份删除账本'), text);
assert.ok(text.indexOf('未知清理范围') >= 0, text);
assert.strictEqual(context.remainingScopesText([]), '无（服务端报告所有清理范围已完成）');
assert.strictEqual(context.remainingScopesText(null), '无（服务端报告所有清理范围已完成）');

// One contract DeletionJob is the only accepted shape.
const job = {
  id: '11111111-1111-4111-8111-111111111111',
  state: 'pending',
  remaining_scopes: ['object_store', 'search_index'],
};
const view = context.describeDeletionJob(job);
assert.strictEqual(view.id, job.id);
assert.strictEqual(view.stateText, '待处理');
assert.strictEqual(view.isOpen, true);
assert.ok(view.progressText.indexOf('删除任务') === 0, view.progressText);
assert.ok(view.progressText.indexOf('对象存储') >= 0, view.progressText);
assert.throws(() => context.describeDeletionJob(null));
assert.throws(() => context.describeDeletionJob({ id: 'x' }));

// Progress polling is bounded: a T069 job honestly stays pending, so the page
// must stop and show the server's last answer instead of spinning forever.
assert.strictEqual(context.shouldPollDeletion({ state: 'pending' }, 0), true);
assert.strictEqual(context.shouldPollDeletion({ state: 'running' }, 4), true);
assert.strictEqual(context.shouldPollDeletion({ state: 'pending' }, 5), false);
assert.strictEqual(context.shouldPollDeletion({ state: 'completed' }, 0), false);
assert.strictEqual(context.shouldPollDeletion({ state: 'failed' }, 0), false);
assert.strictEqual(context.shouldPollDeletion(null, 0), false);

// The human-facing answer names the job and the remaining scopes, never the
// resource id as the job id, and never claims the bytes are gone.
const result = context.deletionResultText(job, 'memory');
assert.ok(result.indexOf(job.id) >= 0, result);
assert.ok(result.indexOf('剩余清理范围') >= 0, result);
assert.ok(result.indexOf('物理副本') < 0, result);
const confirmText = context.deletionConfirmationText('memory', job.id);
assert.ok(confirmText.indexOf('停止被读取') >= 0, confirmText);
assert.ok(confirmText.indexOf('后台清理') >= 0, confirmText);
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
