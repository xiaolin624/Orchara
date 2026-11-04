"""T061 acceptance: the /knowledge page shows index versions and the switch.

The server-rendered page and the Node-executed helpers are real: the page is
served by the compiled API and the helper functions are executed under Node, so
the path/body construction and the index-version/model/progress/failure/active
rendering are observed behaviour, not source text. A real DOM/browser remains
not_run (same boundary as every other page ticket).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_knowledge_page_exposes_the_index_version_section(t055_stack) -> None:
    response = t055_stack.server.http.get("/knowledge")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="index-panel"',
        'id="index-active"',
        'id="index-progress"',
        'id="index-versions"',
        'id="reindex-form"',
        'id="reindex-submit"',
        'id="reindex-status"',
        "reindexDocumentPath(",
        "describeIndexes(",
        "stale_revision",
        "当前激活索引",
        "新的索引版本",
    ):
        assert marker in body, f"knowledge.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_knowledge_js_is_served_with_the_index_helpers(t055_stack) -> None:
    response = t055_stack.server.http.get("/assets/knowledge.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function reindexDocumentPath(",
        "function buildReindexBody(",
        "function describeIndexes(",
        "function describeRetrieval(",
        "function describeIngestion(",
        "stale_revision",
        "source_deleted",
        "index_build_in_progress",
    ):
        assert marker in body, f"knowledge.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_index_version_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "knowledge-index-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} } } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/knowledge.js', 'utf8'), context);

// The rebuild path/body are built from the document id only.
assert.strictEqual(
  context.reindexDocumentPath('d 1/x'),
  '/api/v1/knowledge-documents/d%201%2Fx/reindex',
);
assert.strictEqual(JSON.stringify(context.buildReindexBody('')),
  JSON.stringify({ ok: true, body: {} }));
assert.strictEqual(JSON.stringify(context.buildReindexBody(' a1 ')),
  JSON.stringify({ ok: true, body: { source_artifact_id: 'a1' } }));

// The diagnostics projection shows version, model revision, honest progress,
// the failure code and the active switch; it never invents a percentage.
const diagnostics = {
  document_id: 'd1', state: 'failed', revision: 2, active_revision: 2,
  active_index: {
    id: 'i2', version: 2, document_revision: 2,
    embedding_model_revision: 'local-hash-v2', state: 'failed', stage: 'failed',
    chunk_count: 0, error_code: 'stale_revision', active: true,
    background_job: { id: 'j2', state: 'failed', error_code: 'stale_revision', attempt_count: 1 },
  },
  indexes: [
    {
      id: 'i2', version: 2, document_revision: 2,
      embedding_model_revision: 'local-hash-v2', state: 'failed', stage: 'failed',
      chunk_count: 0, error_code: 'stale_revision', active: true,
      background_job: { id: 'j2', state: 'failed', error_code: 'stale_revision', attempt_count: 1 },
    },
    {
      id: 'i1', version: 1, document_revision: 1,
      embedding_model_revision: 'local-hash-v1', state: 'ready', stage: 'complete',
      chunk_count: 3, active: false,
      background_job: { id: 'j1', state: 'succeeded', attempt_count: 1 },
    },
  ],
  error_code: 'stale_revision', chunk_count: 0,
  background_job: { id: 'j2', state: 'failed', error_code: 'stale_revision', attempt_count: 1 },
};
const indexes = context.describeIndexes(diagnostics);
assert.strictEqual(indexes.items.length, 2);
assert.strictEqual(indexes.active.version, 2);
assert.ok(indexes.activeText.includes('当前激活索引 v2'));
assert.ok(indexes.activeText.includes('local-hash-v2'));
assert.ok(indexes.text.includes('索引 v1'));
assert.ok(indexes.text.includes('local-hash-v1'));
assert.ok(indexes.text.includes('构建失败'));
assert.ok(indexes.text.includes('失败码 stale_revision'));
assert.ok(indexes.text.includes('当前激活'));
assert.ok(indexes.text.includes('尝试 1 次'));
assert.ok(!indexes.text.includes('%'));

// A building/queued version reports the persisted stage and the real job.
const queued = context.describeIndexes({
  indexes: [{
    id: 'i3', version: 3, document_revision: 2,
    embedding_model_revision: 'local-hash-v2', state: 'building', stage: 'queued',
    chunk_count: 0, active: false,
    background_job: { id: 'j3', state: 'ready', attempt_count: 1 },
  }],
});
assert.ok(queued.text.includes('构建中'));
assert.ok(queued.text.includes('queued'));
assert.ok(queued.text.includes('任务 ready'));
assert.strictEqual(queued.active, null);
assert.strictEqual(queued.activeText, '没有激活的索引');

// Retrieval metadata reports which embedding revision the active index used and
// how many active indexes of another model revision were excluded.
const retrieval = context.describeRetrieval({
  items: [], next_cursor: null,
  retrieval: {
    embedding_model_revision: 'local-hash-v2', top_k: 5, acl_epoch: 1,
    index: {
      embedding_model_revision: 'local-hash-v2', active_indexes: 1, excluded_indexes: 1,
    },
  },
});
assert.strictEqual(retrieval.embeddingModelRevision, 'local-hash-v2');
assert.strictEqual(retrieval.activeIndexRevision, 'local-hash-v2');
assert.strictEqual(retrieval.activeIndexes, 1);
assert.strictEqual(retrieval.excludedIndexes, 1);
assert.ok(retrieval.indexText.includes('不混用'));

const ingestion = context.describeIngestion(
  { id: 'd1', state: 'failed', revision: 2, source_artifact_id: 'a1' },
  diagnostics,
);
assert.ok(ingestion.text.includes('当前激活索引：v2'));
assert.ok(ingestion.text.includes('local-hash-v2'));

assert.ok(context.failureText('stale_revision').includes('不会激活'));
assert.ok(context.failureText('source_deleted').includes('复活'));
assert.ok(context.failureText('index_build_in_progress').includes('等待'));

process.stdout.write('knowledge.js index page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "knowledge.js index page behaviour ok" in completed.stdout
