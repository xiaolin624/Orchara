"""F10/T060 page acceptance: PDF upload, page range and no-text-layer rendering.

The structural assertions are about what the compiled API actually serves. The
page helpers are then executed under Node so the PDF media type, the page range,
the empty-page note and the no-text-layer failure are observed behaviour, not
source text. Still ``not_run``: a real DOM/browser driving the page (same boundary
as T055/T059 and the other page tickets).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import t060_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_knowledge_page_offers_pdf_uploads(t060_stack) -> None:
    response = t060_stack.server.http.get("/knowledge")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'accept=".txt,.md,.csv,.xlsx,.pdf',
        "application/pdf",
        ".pdf",
        "上传 txt/md/csv/xlsx/pdf 并封存",
        "页码",
        "空白页",
        "文字层",
        "失败码",
        "可检索",
        "/assets/knowledge.js",
    ):
        assert marker in body, f"knowledge.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_is_served_with_the_pdf_helpers(t060_stack) -> None:
    response = t060_stack.server.http.get("/assets/knowledge.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function mediaTypeForKnowledgeFile(",
        "function formatPageRange(",
        "function formatRowRange(",
        "function describeFailureDetail(",
        "function describeChunk(",
        "application/pdf",
        "page_start",
        "pages_without_text",
        "pdf_no_text_layer",
    ):
        assert marker in body, f"knowledge.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_pdf_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "knowledge-pdf-page.js"
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

// The extension -> declared media type mapping includes PDF (purpose=knowledge).
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('report.PDF')),
  JSON.stringify({ ok: true, mediaType: 'application/pdf' }));
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('sales.csv')),
  JSON.stringify({ ok: true, mediaType: 'text/csv' }));
assert.strictEqual(context.mediaTypeForKnowledgeFile('report.doc').ok, false);
assert.ok(context.mediaTypeForKnowledgeFile('report.doc').error.includes('pdf'));

// A PDF citation is rendered by page number; empty pages are stated explicitly
// instead of being presented as covered text.
assert.strictEqual(context.formatPageRange({}), '');
assert.strictEqual(context.formatPageRange({ row_start: 2, row_end: 3 }), '');
assert.strictEqual(
  context.formatPageRange({ pdf: { page_start: 2, page_end: 2, page_count: 3 } }),
  '第 2 页（共 3 页）');
assert.strictEqual(
  context.formatPageRange({
    pdf: { page_start: 1, page_end: 2, page_count: 3, pages_without_text: [2] },
  }),
  '第 1–2 页（共 3 页） · 空白页 2');
assert.strictEqual(
  context.formatPageRange({ pdf: { page_start: 3, page_end: 2, page_count: 3 } }), '');

// A PDF citation is shown only when it agrees with the returned chunk, including
// a page-local range that actually contains characters.
const pdfChunk = {
  chunk_id: 'c1', document_id: 'd1', document_revision: 2, document_state: 'ready',
  chunk_no: 1, score: 0.6, text: 'Refund policy',
  span: { start_char: 20, end_char: 33, start_line: 4, end_line: 4 },
  citation: {
    document_id: 'd1', document_revision: 2, chunk_no: 1,
    span: { start_char: 20, end_char: 33, start_line: 4, end_line: 4 },
    text_render: 'pdf_pages/1',
    pdf: {
      page_start: 2, page_end: 2, page_char_start: 0, page_char_end: 13,
      page_count: 3, pages_without_text: [3],
    },
  },
};
const shown = context.describeChunk(pdfChunk);
assert.strictEqual(shown.citationOk, true);
assert.strictEqual(shown.pageRangeText, '第 2 页（共 3 页） · 空白页 3');
assert.ok(shown.citationText.includes('第 2 页'));
assert.ok(shown.citationText.includes('版本 2'));

const emptyPage = context.describeChunk(Object.assign({}, pdfChunk, {
  citation: Object.assign({}, pdfChunk.citation, {
    pdf: Object.assign({}, pdfChunk.citation.pdf, { page_char_start: 5, page_char_end: 5 }),
  }),
}));
assert.strictEqual(emptyPage.citationOk, false);
assert.ok(emptyPage.citationText.includes('引用不一致'));

const forgedPage = context.describeChunk(Object.assign({}, pdfChunk, {
  citation: Object.assign({}, pdfChunk.citation, {
    pdf: Object.assign({}, pdfChunk.citation.pdf, { page_start: 0 }),
  }),
}));
assert.strictEqual(forgedPage.citationOk, false);

// A scanned PDF is diagnosable: the code, the threshold location and the
// "no OCR" explanation are all visible; nothing is presented as searchable.
const scannedDiagnosticsPayload = {
  document_id: 'd2', state: 'failed', revision: 1, active_revision: null,
  error_code: 'pdf_no_text_layer',
  detail: { part: 'text_layer', limit: 'min_text_runes=32', value: 23 },
  chunk_count: 0,
  background_job: { state: 'failed', error_code: 'pdf_no_text_layer', attempt_count: 1 },
};
const scannedDiagnostics = context.describeDiagnostics(scannedDiagnosticsPayload);
assert.strictEqual(scannedDiagnostics.stateText, '摄取失败');
assert.ok(scannedDiagnostics.errorText.includes('OCR'));
assert.ok(scannedDiagnostics.detailText.includes('text_layer'));
assert.ok(scannedDiagnostics.detailText.includes('min_text_runes=32'));
assert.ok(scannedDiagnostics.detailText.includes('23'));
const scannedIngestion = context.describeIngestion(
  { id: 'd2', state: 'failed', revision: 1, source_artifact_id: 'a2' },
  scannedDiagnosticsPayload,
);
assert.ok(scannedIngestion.text.includes('pdf_no_text_layer'));
assert.ok(scannedIngestion.text.includes('失败位置'));
assert.ok(scannedIngestion.text.includes('text_layer'));

// Empty content remains its own code; a document without a text layer never
// borrows the OCR-success or empty-content wording.
assert.ok(context.failureText('pdf_encrypted').includes('加密'));
assert.ok(context.failureText('pdf_invalid').includes('PDF'));
assert.strictEqual(context.failureText('some_future_code'), 'some_future_code');

process.stdout.write('knowledge pdf page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "knowledge pdf page behaviour ok" in completed.stdout
