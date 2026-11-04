"""F10/T059 page acceptance: CSV/XLSX upload, row-range and error rendering.

The structural assertions are about what the compiled API actually serves. The
page helpers are then executed under Node so the accepted media types, the row
range rendering and the diagnosable failure location are observed behaviour, not
source text. Still ``not_run``: a real DOM/browser driving the page (same boundary
as T055 and the other page tickets).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import t059_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_knowledge_page_offers_csv_and_xlsx_uploads(t059_stack) -> None:
    response = t059_stack.server.http.get("/knowledge")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'accept=".txt,.md,.csv,.xlsx',
        ".csv",
        ".xlsx",
        "上传 txt/md/csv/xlsx 并封存",
        "行范围",
        "工作表",
        "公式",
        "失败码",
        "可检索",
        "/assets/knowledge.js",
    ):
        assert marker in body, f"knowledge.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_is_served_with_the_tabular_helpers(t059_stack) -> None:
    response = t059_stack.server.http.get("/assets/knowledge.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function mediaTypeForKnowledgeFile(",
        "function formatRowRange(",
        "function describeFailureDetail(",
        "function describeChunk(",
        "function describeDiagnostics(",
        "function describeIngestion(",
        "text/csv",
        "spreadsheetml.sheet",
        "row_start",
        "sheet_name",
    ):
        assert marker in body, f"knowledge.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_tabular_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "knowledge-tabular-page.js"
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

// The extension -> declared media type mapping now includes the tabular family.
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('sales.csv')),
  JSON.stringify({ ok: true, mediaType: 'text/csv' }));
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('book.XLSX')),
  JSON.stringify({ ok: true, mediaType:
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
assert.strictEqual(JSON.stringify(context.mediaTypeForKnowledgeFile('notes.txt')),
  JSON.stringify({ ok: true, mediaType: 'text/plain' }));
assert.strictEqual(context.mediaTypeForKnowledgeFile('report.pdf').ok, false);
assert.ok(context.mediaTypeForKnowledgeFile('report.pdf').error.includes('csv'));
assert.ok(context.mediaTypeForKnowledgeFile('no-extension').error.includes('xlsx'));

// Row ranges are rendered from the citation, with header semantics and the
// worksheet's own row numbers (a gap makes row_count smaller than the range).
assert.strictEqual(context.formatRowRange({}), '');
assert.strictEqual(
  context.formatRowRange({ row_start: 2, row_end: 11, header_row: 1 }),
  '表格行 2–11（表头行 1）');
assert.strictEqual(
  context.formatRowRange({
    sheet_name: 'Sales', row_start: 28, row_end: 44, header_row: 1, row_count: 16,
  }),
  '工作表 Sales · 表格行 28–44（表头行 1） · 共 16 行');
assert.strictEqual(context.formatRowRange({ row_start: 5, row_end: 4 }), '');

// A tabular citation is shown only when it agrees with the returned chunk, and
// an inverted row range is an inconsistent citation.
const csvChunk = {
  chunk_id: 'c1', document_id: 'd1', document_revision: 3, document_state: 'ready',
  chunk_no: 2, score: 0.75, text: 'A1,华东,120.50',
  span: { start_char: 10, end_char: 30, start_line: 2, end_line: 2 },
  citation: {
    document_id: 'd1', document_revision: 3, chunk_no: 2,
    span: { start_char: 10, end_char: 30, start_line: 2, end_line: 2 },
    text_render: 'source_utf8', row_start: 2, row_end: 3, row_count: 2,
    header_row: 1, sheet_name: '', columns: ['order_id', 'region', 'amount'],
  },
};
const shown = context.describeChunk(csvChunk);
assert.strictEqual(shown.citationOk, true);
assert.ok(shown.citationText.includes('版本 3'));
assert.ok(shown.citationText.includes('表格行 2–3'));
assert.strictEqual(shown.rowRangeText, '表格行 2–3（表头行 1）');

const inverted = context.describeChunk(Object.assign({}, csvChunk, {
  citation: Object.assign({}, csvChunk.citation, { row_start: 5, row_end: 4 }),
}));
assert.strictEqual(inverted.citationOk, false);
assert.ok(inverted.citationText.includes('引用不一致'));

const forgedSpan = context.describeChunk(Object.assign({}, csvChunk, {
  citation: Object.assign({}, csvChunk.citation, { chunk_no: 9 }),
}));
assert.strictEqual(forgedSpan.citationOk, false);

// A refused formula is diagnosable: code, explanation and the exact cell.
const formulaDiagnosticsPayload = {
  document_id: 'd1', state: 'failed', revision: 2, active_revision: null,
  error_code: 'ingest_formula_not_supported',
  detail: { sheet: 'Orders', row: 2, column: 'B', cell: 'B2' },
  chunk_count: 0,
  background_job: { state: 'failed', error_code: 'ingest_formula_not_supported',
    attempt_count: 1 },
};
const formulaDiagnostics = context.describeDiagnostics(formulaDiagnosticsPayload);
assert.strictEqual(formulaDiagnostics.detail.cell, 'B2');
assert.ok(formulaDiagnostics.detailText.includes('工作表 Orders'));
assert.ok(formulaDiagnostics.detailText.includes('行 2'));
assert.ok(formulaDiagnostics.detailText.includes('列 B'));
assert.ok(formulaDiagnostics.detailText.includes('单元格 B2'));

const formulaIngestion = context.describeIngestion(
  { id: 'd1', state: 'failed', revision: 2, source_artifact_id: 'a1' },
  formulaDiagnosticsPayload,
);
assert.ok(formulaIngestion.text.includes('ingest_formula_not_supported'));
assert.ok(formulaIngestion.text.includes('失败位置'));
assert.ok(formulaIngestion.text.includes('B2'));

// A cap refusal shows the measured limit/entry location and a human explanation.
const capDiagnostics = context.describeDiagnostics({
  state: 'failed', revision: 1, error_code: 'ingest_archive_limit_exceeded',
  detail: { limit: 'compression_ratio', value: 1024 }, chunk_count: 0,
});
assert.ok(capDiagnostics.detailText.includes('compression_ratio'));
assert.ok(capDiagnostics.errorText.includes('压缩'));
assert.strictEqual(context.describeDiagnostics({
  state: 'failed', error_code: 'ingest_empty_content', detail: null,
}).detailText, '');

assert.ok(context.failureText('ingest_formula_not_supported').includes('公式'));
assert.ok(context.failureText('ingest_invalid_csv').includes('CSV'));
assert.ok(context.failureText('ingest_row_limit_exceeded').includes('行'));
assert.strictEqual(context.failureText('some_future_code'), 'some_future_code');

process.stdout.write('knowledge tabular page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "knowledge tabular page behaviour ok" in completed.stdout
