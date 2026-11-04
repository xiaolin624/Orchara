"""T055 acceptance: the /knowledge page and its Node-executed client helpers.

The structural assertions are about what the compiled API actually serves. The
page helpers are then executed under Node so the path construction, request
bodies, span rendering and the citation-consistency rule are observed behaviour,
not source text. Still ``not_run``: a real DOM/browser driving the page (same
boundary as the other page tests).
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


def test_the_knowledge_page_is_served_with_the_full_user_path(t055_stack) -> None:
    response = t055_stack.server.http.get("/knowledge")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="kb-form"',
        'id="kb-name"',
        'id="kb-list"',
        'id="kb-select"',
        'id="upload-form"',
        'id="knowledge-file"',
        'id="artifact-panel"',
        'id="ingest-form"',
        'id="document-panel"',
        'id="document-state"',
        'id="document-error-code"',
        'id="document-diagnostics"',
        'id="query-form"',
        'id="query-text"',
        'id="query-top-k"',
        'id="results"',
        'id="citation-panel"',
        "/assets/knowledge.js",
        "knowledgeBasesPath(", 
        "retrievalQueriesPath(",
        "knowledgeCitationPath(",
        "可检索",
        "失败码",
    ):
        assert marker in body, f"knowledge.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_is_served_with_the_client_helpers(t055_stack) -> None:
    response = t055_stack.server.http.get("/assets/knowledge.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function knowledgeBasesPath(",
        "function knowledgeBasePath(",
        "function knowledgeDocumentsPath(",
        "function knowledgeDocumentPath(",
        "function knowledgeDocumentDiagnosticsPath(",
        "function retrievalQueriesPath(",
        "function knowledgeCitationPath(",
        "function mediaTypeForKnowledgeFile(",
        "function buildKnowledgeBaseBody(",
        "function buildIngestBody(",
        "function buildRetrievalBody(",
        "function formatSpan(",
        "function describeChunk(",
        "function describeRetrieval(",
        "function describeIngestion(",
    ):
        assert marker in body, f"knowledge.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "knowledge-page.js"
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

// Paths are built from ids, never from a caller URL.
assert.strictEqual(context.knowledgeBasesPath(), '/api/v1/knowledge-bases');
assert.strictEqual(context.knowledgeBasePath('b 1/x'), '/api/v1/knowledge-bases/b%201%2Fx');
assert.strictEqual(context.knowledgeDocumentsPath('b1'), '/api/v1/knowledge-bases/b1/documents');
assert.strictEqual(context.knowledgeDocumentPath('d1'), '/api/v1/knowledge-documents/d1');
assert.strictEqual(
  context.knowledgeDocumentDiagnosticsPath('d1'),
  '/api/v1/knowledge-documents/d1/diagnostics',
);
assert.strictEqual(
  context.retrievalQueriesPath('b1'),
  '/api/v1/knowledge-bases/b1/retrieval-queries',
);
assert.strictEqual(context.knowledgeCitationPath('c 1'), '/api/v1/knowledge-citations/c%201');

// Media type comes from the extension, and only txt/md are accepted.
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('notes.txt')),
  JSON.stringify({ ok: true, mediaType: 'text/plain' }));
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('READ.MD')),
  JSON.stringify({ ok: true, mediaType: 'text/markdown' }));
assert.strictEqual(context.mediaTypeForKnowledgeFile('report.pdf').ok, false);
assert.strictEqual(context.mediaTypeForKnowledgeFile('no-extension').ok, false);

// Request bodies are exactly the contract/non-contract shapes.
const base = context.buildKnowledgeBaseBody('  Sales KB  ');
assert.strictEqual(JSON.stringify(base), JSON.stringify({ ok: true, body: { name: 'Sales KB' } }));
assert.strictEqual(context.buildKnowledgeBaseBody('   ').ok, false);
assert.strictEqual(JSON.stringify(context.buildIngestBody('a1')),
  JSON.stringify({ ok: true, body: { source_artifact_id: 'a1' } }));
assert.strictEqual(context.buildIngestBody('').ok, false);
assert.strictEqual(JSON.stringify(context.buildRetrievalBody('退款政策', undefined)),
  JSON.stringify({ ok: true, body: { query: '退款政策', top_k: 8 } }));
assert.strictEqual(JSON.stringify(context.buildRetrievalBody('q', '3')),
  JSON.stringify({ ok: true, body: { query: 'q', top_k: 3 } }));
assert.strictEqual(context.buildRetrievalBody('', '3').ok, false);
assert.strictEqual(context.buildRetrievalBody('q', '0').ok, false);
assert.strictEqual(context.buildRetrievalBody('q', '51').ok, false);
assert.strictEqual(context.buildRetrievalBody('q', '2.5').ok, false);

assert.strictEqual(context.formatSpan({ start_char: 10, end_char: 20, start_line: 1, end_line: 2 }),
  '字符 10–20 / 行 1–2');

// A citation is only shown when it agrees with the returned chunk.
const chunk = {
  chunk_id: 'c1', document_id: 'd1', document_revision: 3, document_state: 'ready',
  chunk_no: 2, score: 0.75, text: '第 2 个片段',
  span: { start_char: 10, end_char: 20, start_line: 1, end_line: 2 },
  citation: {
    document_id: 'd1', document_revision: 3, chunk_no: 2,
    span: { start_char: 10, end_char: 20, start_line: 1, end_line: 2 },
  },
};
const good = context.describeChunk(chunk);
assert.strictEqual(good.citationOk, true);
assert.strictEqual(good.chunkId, 'c1');
assert.strictEqual(good.documentRevision, 3);
assert.ok(good.citationText.includes('版本 3'));
assert.ok(good.citationText.includes('第 2 片段'));
assert.ok(good.citationText.includes('行 1–2'));

const forged = context.describeChunk(Object.assign({}, chunk, {
  citation: Object.assign({}, chunk.citation, { document_id: 'other-doc' }),
}));
assert.strictEqual(forged.citationOk, false);
assert.ok(forged.citationText.includes('引用不一致'));

const staleRevision = context.describeChunk(Object.assign({}, chunk, {
  citation: Object.assign({}, chunk.citation, { document_revision: 2 }),
}));
assert.strictEqual(staleRevision.citationOk, false);

// An empty retrieval answer is a normal empty list, not an error.
const empty = context.describeRetrieval({ items: [], next_cursor: null, retrieval: {
  embedding_model_revision: 'local-hash-v1', top_k: 8, acl_epoch: 1,
} });
assert.strictEqual(empty.empty, true);
assert.ok(empty.emptyText.includes('不是错误'));
assert.strictEqual(empty.embeddingModelRevision, 'local-hash-v1');
assert.strictEqual(empty.topK, 8);
assert.strictEqual(empty.aclEpoch, 1);

// States: failed is terminal, scanning/indexing are polled, ready is searchable.
assert.strictEqual(context.shouldPollDocument('scanning'), true);
assert.strictEqual(context.shouldPollDocument('indexing'), true);
assert.strictEqual(context.shouldPollDocument('ready'), false);
assert.strictEqual(context.shouldPollDocument('failed'), false);
assert.strictEqual(context.describeDocument({ state: 'failed', revision: 1 }).terminal, true);
assert.strictEqual(context.describeDocument({ state: 'ready', revision: 1 }).searchable, true);
assert.ok(context.failureText('ingest_empty_content').includes('空'));
assert.ok(context.failureText('ingest_invalid_encoding').includes('UTF-8'));
assert.strictEqual(context.failureText('some_future_code'), 'some_future_code');

const ingestion = context.describeIngestion(
  { id: 'd1', state: 'failed', revision: 2, source_artifact_id: 'a1' },
  { state: 'failed', revision: 2, active_revision: null, error_code: 'ingest_empty_content',
    chunk_count: 0, background_job: { state: 'failed', error_code: 'ingest_empty_content',
    attempt_count: 1 } },
);
assert.ok(ingestion.text.includes('ingest_empty_content'));
assert.ok(ingestion.text.includes('已写入片段：0'));
assert.strictEqual(ingestion.diagnostics.chunkCount, 0);

process.stdout.write('knowledge.js page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "knowledge.js page behaviour ok" in completed.stdout
