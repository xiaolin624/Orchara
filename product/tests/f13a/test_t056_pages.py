"""T056 acceptance: the evaluation page is served and its logic executes under Node.

The structural half asserts what the compiled API actually serves. The behavioural
half runs the same ``evaluate.js`` a browser would receive in a Node VM and asserts
the paths, request bodies, per-case row derivation (expectation / actual / failure
reason / attachments) and the count-only summary. Still ``not_run``: a real DOM /
browser driving the page (same boundary as the other page tests).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

NODE = shutil.which("node")
APPLICATION_ID = "33333333-3333-4333-8333-333333333333"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_evaluation_page_is_served_with_the_creator_path(t056_stack) -> None:
    response = t056_stack.server.http.get(f"/apps/{APPLICATION_ID}/evaluate")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="evaluate-form"',
        'id="evaluate-dataset-file"',
        'id="evaluate-fixture-file"',
        'id="evaluate-upload-submit"',
        'id="evaluate-upload-status"',
        'id="evaluate-artifact-panel"',
        'id="evaluate-dataset-id"',
        'id="evaluate-fixture-id"',
        'id="evaluation-submit-form"',
        'id="evaluation-draft-revision"',
        'id="evaluation-dataset-artifact"',
        'id="evaluation-submit"',
        'id="evaluation-status"',
        'id="evaluation-panel"',
        'id="evaluation-report-id"',
        'id="evaluation-summary"',
        'id="case-rows"',
        'id="case-empty"',
        "/assets/evaluate.js",
        "evaluationDraftPath(",
        "evaluationCasesPath(",
        "caseArtifactDownloadPath(",
        "期望",
        "实际",
        "失败原因",
        "实际附件",
    ):
        assert marker in body, f"evaluate.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body
    # The page states that expectations come from the sealed report, not from
    # recomputation in the browser.
    assert "只读报告" in body


def test_evaluate_js_is_served_with_the_client_helpers(t056_stack) -> None:
    response = t056_stack.server.http.get("/assets/evaluate.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function evaluationDraftPath(",
        "function evaluationCasesPath(",
        "function caseArtifactDownloadPath(",
        "function artifactUploadBody(",
        "function caseInputMediaType(",
        "function caseInputUploadBody(",
        "function evaluateDraftBody(",
        "function newEvaluationIdempotencyKey(",
        "function evaluationStateLabel(",
        "function shouldPollEvaluation(",
        "function caseVerdictLabel(",
        "function describeCase(",
        "function describeCases(",
        "function describeCaseSummary(",
        "function describeFailure(",
    ):
        assert marker in body, f"evaluate.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_evaluate_js_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "evaluate-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = {
  console,
  window: { location: { assign() {}, pathname: '/apps/app-1/evaluate' } },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/evaluate.js', 'utf8'), context);

// Paths are built from ids, never from a caller URL.
assert.strictEqual(context.evaluationDraftPath('a 1/x'), '/api/v1/applications/a%201%2Fx/evaluations');
assert.strictEqual(context.evaluationDraftRevisionPath('a1'), '/api/v1/applications/a1/draft');
assert.strictEqual(context.evaluationPath('e 1'), '/api/v1/evaluations/e%201');
assert.strictEqual(context.evaluationReportPath('r1'), '/api/v1/evaluation-reports/r1');
assert.strictEqual(context.evaluationCasesPath('r1'), '/api/v1/evaluation-reports/r1/cases');
assert.strictEqual(context.caseArtifactDownloadPath('a 1'),
  '/api/v1/artifacts/a%201/download');

// Upload bodies are exactly the T028 shapes and are bounded client-side.
const dataset = context.artifactUploadBody(1024);
assert.strictEqual(JSON.stringify(dataset), JSON.stringify({ ok: true, body: {
  media_type: 'application/json', size_bytes: 1024, purpose: 'evaluation_dataset',
} }));
assert.strictEqual(context.artifactUploadBody(0).ok, false);
assert.strictEqual(context.artifactUploadBody(4 * 1024 * 1024 + 1).ok, false);

assert.strictEqual(JSON.stringify(context.caseInputMediaType('input.CSV')),
  JSON.stringify({ ok: true, mediaType: 'text/csv' }));
assert.strictEqual(context.caseInputMediaType('input.xlsx').mediaType,
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
assert.strictEqual(context.caseInputMediaType('input.pdf').ok, false);
assert.strictEqual(context.caseInputMediaType('no-extension').ok, false);
assert.strictEqual(JSON.stringify(context.caseInputUploadBody({ name: 'x.csv', size: 10 })),
  JSON.stringify({ ok: true, body: { media_type: 'text/csv', size_bytes: 10, purpose: 'task_input' } }));
assert.strictEqual(context.caseInputUploadBody({ name: 'x.txt', size: 10 }).ok, false);

// evaluateDraft's body is exactly two members.
assert.strictEqual(JSON.stringify(context.evaluateDraftBody(3, ' dataset-1 ')),
  JSON.stringify({ ok: true, body: { draft_revision: 3, dataset_artifact_id: 'dataset-1' } }));
assert.strictEqual(context.evaluateDraftBody(0, 'd').ok, false);
assert.strictEqual(context.evaluateDraftBody('x', 'd').ok, false);
assert.strictEqual(context.evaluateDraftBody(1, '').ok, false);
assert.ok(context.newEvaluationIdempotencyKey().length > 0);

// The evaluation lifecycle: queued/running poll, terminal stops.
assert.strictEqual(context.shouldPollEvaluation('queued'), true);
assert.strictEqual(context.shouldPollEvaluation('running'), true);
assert.strictEqual(context.shouldPollEvaluation('succeeded'), false);
assert.strictEqual(context.shouldPollEvaluation('failed'), false);
assert.strictEqual(context.evaluationStateLabel('succeeded'), '已结束（报告已封存）');
assert.strictEqual(context.caseVerdictLabel('blocked'), '阻塞');

// A case row carries expectation, actual, the located failure reason and the
// downloadable actual attachments; amounts stay the report's decimal strings.
const payload = {
  report_id: 'r1',
  status: 'passed',
  cases: [
    {
      case_id: 'sales-happy-csv',
      verdict: 'passed',
      assertions: [
        { kind: 'run_state', expected: 'succeeded', actual: 'succeeded', passed: true },
        { kind: 'decimal_equals', pointer: '/summary/total', expected: '250.00',
          actual: '250.00', passed: true },
      ],
      facts: { run_id: 'run-1', run_state: 'succeeded' },
      attachments: [
        { artifact_id: 'att-1', label: 'summary.csv', media_type: 'text/csv',
          size_bytes: 30, sha256: 'abc' },
      ],
    },
    {
      case_id: 'sales-bad',
      verdict: 'failed',
      reason_code: 'assertion_failed',
      detail: '1 of 2 assertions failed',
      assertions: [
        { kind: 'run_state', expected: 'succeeded', actual: 'failed', passed: false,
          detail: 'the run reached failed' },
        { kind: 'failure_reason', expected: 'missing_column', actual: 'other_code',
          passed: false },
      ],
      facts: { run_id: 'run-2', run_state: 'failed', run_error_code: 'other_code' },
      attachments: [],
    },
  ],
};
const view = context.describeCases(payload);
assert.strictEqual(view.reportId, 'r1');
assert.strictEqual(view.cases.length, 2);
const passed = view.cases[0];
assert.strictEqual(passed.caseId, 'sales-happy-csv');
assert.strictEqual(passed.verdictLabel, '通过');
assert.ok(passed.expectation.includes('run_state = succeeded（期望）'));
assert.ok(passed.actual.includes('decimal_equals /summary/total = 250.00（通过）'));
assert.strictEqual(passed.attachments[0].downloadPath, '/api/v1/artifacts/att-1/download');
assert.strictEqual(passed.attachments[0].label, 'summary.csv');

const failed = view.cases[1];
assert.strictEqual(failed.verdictLabel, '失败');
assert.ok(failed.reasons.includes('assertion_failed'));
assert.ok(failed.reasons.includes('run_error_code=other_code'));
assert.ok(failed.actual.some((line) => line.includes('run_state = failed')));

const summary = context.describeCaseSummary(payload);
assert.strictEqual(summary.total, 2);
assert.strictEqual(summary.passed, 1);
assert.strictEqual(summary.failed, 1);
assert.ok(summary.text.includes('共 2 个案例'));
assert.ok(!summary.text.includes('%'), 'a percentage that cannot be derived must not be shown');

// A blocked case is counted separately and never hidden in "failed".
const blocked = context.describeCaseSummary({ report_id: 'r2', status: 'not_run', cases: [
  { case_id: 'c', verdict: 'blocked', assertions: [], facts: {}, attachments: [] },
] });
assert.strictEqual(blocked.blocked, 1);
assert.strictEqual(blocked.passed, 0);
assert.ok(context.describeFailure({ code: 'insufficient_scope', status: 403 }).includes('insufficient_scope'));

process.stdout.write('evaluate.js page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)], capture_output=True, text=True, timeout=60
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "evaluate.js page behaviour ok" in completed.stdout
