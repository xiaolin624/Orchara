"""T064 acceptance: the knowledge Q&A view and its Node-executed client helpers.

The structural assertions are about what the compiled API actually serves. The
page helpers are then executed under Node so the answer request body, the
citation-membership rule and the four distinct outcome presentations are observed
behaviour, not source text. Still ``not_run``: a real DOM/browser driving the
page (same boundary as the other page tickets).
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


def test_the_knowledge_page_serves_the_grounded_answer_view(t055_stack) -> None:
    response = t055_stack.server.http.get("/knowledge")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="answer-form"',
        'id="answer-query"',
        'id="answer-top-k"',
        'id="answer-submit"',
        'id="answer-status"',
        'id="answer-panel"',
        'id="answer-generation"',
        'id="answer-text"',
        'id="answer-retrieval"',
        'id="answer-citations"',
        'id="answer-citation-panel"',
        'id="answer-citation-text"',
        'id="answer-citation-status"',
        "knowledgeAnswerPath(",
        "buildAnswerBody(",
        "describeAnswer(",
        "describeAnswerRefusal(",
        "answerOutcomeText(",
        "不是错误",
        "引用不可用",
        "未配置模型",
    ):
        assert marker in body, f"knowledge.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_knowledge_js_serves_the_answer_helpers(t055_stack) -> None:
    response = t055_stack.server.http.get("/assets/knowledge.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function knowledgeAnswerPath(",
        "function buildAnswerBody(",
        "function answerOutcomeText(",
        "function describeAnswer(",
        "function describeAnswerCitation(",
        "function describeAnswerGeneration(",
        "function describeAnswerRefusal(",
        "function describeAnswerRetrieval(",
    ):
        assert marker in body, f"knowledge.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_knowledge_answer_helpers_execute_under_node(
    node_binary: str, tmp_path
) -> None:
    harness = tmp_path / "knowledge-answer.js"
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

// The answer path is built from the selected KB id, never from a caller URL.
assert.strictEqual(
  context.knowledgeAnswerPath('b 1/x'),
  '/api/v1/knowledge-bases/b%201%2Fx/answer',
);

// The request body is exactly {query, top_k}, with the same 1..50 bounds.
assert.strictEqual(
  JSON.stringify(context.buildAnswerBody('退款政策', undefined)),
  JSON.stringify({ ok: true, body: { query: '退款政策', top_k: 8 } }),
);
assert.strictEqual(
  JSON.stringify(context.buildAnswerBody('退款政策', '3')),
  JSON.stringify({ ok: true, body: { query: '退款政策', top_k: 3 } }),
);
assert.strictEqual(context.buildAnswerBody('   ', '3').ok, false);
assert.strictEqual(context.buildAnswerBody('q', '0').ok, false);
assert.strictEqual(context.buildAnswerBody('q', '51').ok, false);

// Four distinct outcome presentations, including the empty-answer outcome.
assert.ok(context.answerOutcomeText('answered').includes('引用'));
assert.ok(context.answerOutcomeText('no_results').includes('不是错误'));
assert.ok(context.answerOutcomeText('answer_without_citation').includes('没有引用'));
assert.ok(context.answerOutcomeText('citation_not_found').includes('不属于本次检索'));
assert.ok(context.answerOutcomeText('citation_unreadable').includes('不可读'));

const generation = context.describeAnswerGeneration({
  kind: 'extractive_stub', version: 'extractive-stub/1', stub: true,
  model_provider: 'not_configured',
});
assert.strictEqual(generation.stub, true);
assert.ok(generation.text.includes('extractive-stub/1'));
assert.ok(generation.text.includes('未配置模型'));

const retrieval = context.describeAnswerRetrieval({
  embedding_model_revision: 'local-hash-v1', top_k: 3, acl_epoch: 2,
  chunk_ids: ['c1', 'c2'],
});
assert.strictEqual(retrieval.topK, 3);
assert.strictEqual(retrieval.aclEpoch, 2);
assert.strictEqual(retrieval.chunkCount, 2);
assert.ok(retrieval.text.includes('local-hash-v1'));

// An answered response: a citation is shown only when its provenance agrees with
// the returned chunk AND the chunk id belongs to this answer's retrieval set.
const citation = {
  chunk_id: 'c1',
  document_id: 'd1',
  document_revision: 1,
  chunk_no: 0,
  score: 0.5,
  text: '华北地区十月销售额 1234.50 元',
  span: { start_char: 0, end_char: 20, start_line: 1, end_line: 1 },
  citation: {
    document_id: 'd1', document_revision: 1, chunk_no: 0,
    span: { start_char: 0, end_char: 20, start_line: 1, end_line: 1 },
    source_sha256: 'a'.repeat(64),
  },
};
const payload = {
  outcome: 'answered',
  answer: {
    text: '根据本次检索到的 1 个片段整理：华北地区十月销售额 1234.50 元',
    generation: {
      kind: 'extractive_stub', version: 'extractive-stub/1',
      stub: true, model_provider: 'not_configured',
    },
    citations: [citation],
  },
  retrieval: {
    embedding_model_revision: 'local-hash-v1', top_k: 8, acl_epoch: 1,
    chunk_ids: ['c1', 'c2'],
  },
};
const answered = context.describeAnswer(payload);
assert.strictEqual(answered.outcome, 'answered');
assert.strictEqual(answered.empty, false);
assert.strictEqual(answered.citations.length, 1);
assert.strictEqual(answered.citations[0].citationOk, true);
assert.strictEqual(answered.citationsOk, true);
assert.ok(answered.citations[0].citationText.includes('版本 1'));
assert.ok(answered.text.includes('1234.50'));
assert.strictEqual(answered.retrieval.chunkIds.length, 2);

const outsideRetrieval = context.describeAnswer({
  outcome: 'answered',
  answer: {
    text: 'x',
    generation: payload.answer.generation,
    citations: [Object.assign({}, citation, { chunk_id: 'c9' })],
  },
  retrieval: { embedding_model_revision: 'local-hash-v1', top_k: 8, acl_epoch: 1, chunk_ids: ['c1'] },
});
assert.strictEqual(outsideRetrieval.citations[0].inThisRetrieval, false);
assert.strictEqual(outsideRetrieval.citations[0].citationOk, false);
assert.ok(outsideRetrieval.citations[0].citationText.includes('不属于本次检索'));
assert.strictEqual(outsideRetrieval.citationsOk, false);

const staleProvenance = context.describeAnswer({
  outcome: 'answered',
  answer: {
    text: 'x',
    generation: payload.answer.generation,
    citations: [Object.assign({}, citation, {
      citation: Object.assign({}, citation.citation, { document_revision: 2 }),
    })],
  },
  retrieval: { chunk_ids: ['c1'] },
});
assert.strictEqual(staleProvenance.citations[0].citationOk, false);

// No results is an empty answer, not an error: nothing is rendered as a source.
const empty = context.describeAnswer({
  outcome: 'no_results',
  answer: { text: '', generation: payload.answer.generation, citations: [] },
  retrieval: { embedding_model_revision: 'local-hash-v1', top_k: 8, acl_epoch: 1, chunk_ids: [] },
});
assert.strictEqual(empty.empty, true);
assert.strictEqual(empty.text, '');
assert.strictEqual(empty.citations.length, 0);
assert.ok(empty.outcomeText.includes('不是错误'));

// Refusals keep their own code, status and message; the page never receives an
// answer body to display.
const refusal = context.describeAnswerRefusal({
  code: 'citation_unreadable', status: 409,
  message: 'a cited chunk is no longer readable',
  details: { outcome: 'citation_unreadable', chunk_id: 'c1' },
});
assert.strictEqual(refusal.outcome, 'citation_unreadable');
assert.strictEqual(refusal.status, 409);
assert.strictEqual(refusal.chunkID, 'c1');
assert.ok(refusal.outcomeText.includes('不可读'));
assert.strictEqual(
  JSON.stringify(context.describeAnswerRefusal({
    code: 'citation_not_found', details: { outcome: 'citation_not_found', chunk_id: 'c9' },
  })),
  JSON.stringify({
    code: 'citation_not_found', status: 0, message: '',
    outcome: 'citation_not_found',
    outcomeText: context.answerOutcomeText('citation_not_found'),
    chunkID: 'c9',
  }),
);

process.stdout.write('knowledge answer page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "knowledge answer page behaviour ok" in completed.stdout
