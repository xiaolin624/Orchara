"""F06/T047: the /runs page exposes the persisted agent context trace.

Structural assertions cover what the real compiled API serves; ``runs.js`` is
executed under Node with a stubbed trace document so the trace rendering is
observed behaviour, not source text. Still not_run: a browser DOM driving the
page (the same boundary as T023/T027/T031/T033/T042/T043).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support.fixtures import t033_stack

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_runs_page_carries_the_context_trace_section(t033_stack) -> None:
    response = t033_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="agent-context-status"',
        'id="agent-context-trace"',
        "上下文跟踪",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_js_exposes_the_context_trace_client(t033_stack) -> None:
    response = t033_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function describeContextTrace(",
        "function contextTraceRowOf(",
        "function renderContextTrace(",
        "context-trace",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"
    # The trace is fetched through the existing owner-only artifact content
    # route; the page never invents a payload path.
    assert "fetchArtifactText" in body and "artifactContentPath" in body


def test_runs_js_context_trace_summary_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-context.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, window: { location: { assign() {} }, open() {} } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

const traceDocument = {
  version: 1,
  node_id: 'analysis',
  turns: [
    {
      sources: [
        { kind: 'python', node_id: 'stats', label: 'stats', estimated_tokens: 42, protected: true },
        { kind: 'start', node_id: 'start', label: 'request', estimated_tokens: 900 },
      ],
      truncations: [
        { source: 'inputs', reason: 'verbose_upstream_text', tokens_removed: 1200 },
      ],
      protected: ['stats', 'request.amount', 'request.approval'],
      estimated_tokens: 690,
      budget_tokens: 2048,
      reserved_output_tokens: 1024,
      memory_state: 'not_run',
    },
  ],
};

const trace = context.describeContextTrace(traceDocument);
assert.equal(trace.visible, true);
assert.equal(trace.turnCount, 1);
assert.equal(trace.budgetTokens, 2048);
assert.equal(trace.reservedOutputTokens, 1024);
assert.equal(trace.estimatedTokens, 690);
assert.equal(trace.memoryState, 'not_run');
assert.ok(trace.memoryText.indexOf('F09') >= 0, trace.memoryText);
assert.ok(trace.sourceText.indexOf('python/stats/stats') >= 0, trace.sourceText);
assert.ok(trace.sourceText.indexOf('start/start/request') >= 0, trace.sourceText);
assert.ok(trace.truncationText.indexOf('冗长上游文本') >= 0, trace.truncationText);
assert.ok(trace.truncationText.indexOf('1200') >= 0, trace.truncationText);
assert.ok(trace.protectedText.indexOf('request.amount') >= 0, trace.protectedText);
assert.ok(trace.budgetText.indexOf('2048') >= 0, trace.budgetText);

// Nothing beyond identifiers, reasons, key paths and counts enters the view.
const rendered = JSON.stringify(trace);
assert.ok(rendered.indexOf('sk-') < 0, rendered);
assert.ok(rendered.indexOf('Bearer') < 0, rendered);

// A failed/old run simply has no trace row; the panel states that honestly.
const nodeBody = {
  items: [
    { node_id: 'analysis', kind: 'data', artifact_id: 'ad', label: 'data',
      media_type: 'application/json', size_bytes: 24, sha256: 'e'.repeat(64) },
    { node_id: 'analysis', kind: 'file', artifact_id: 'trace', label: 'context-trace',
      media_type: 'application/json', size_bytes: 200, sha256: 'f'.repeat(64) },
    { node_id: 'analysis', kind: 'log', artifact_id: 'log', label: 'tool-log-stub-call-1',
      media_type: 'text/plain', size_bytes: 90, sha256: '2'.repeat(64) },
  ],
};
const row = context.contextTraceRowOf(nodeBody, { items: [] }, { analysis: 'succeeded' });
assert.equal(row && row.artifact_id, 'trace');
assert.equal(context.contextTraceRowOf({ items: [] }, { items: [] }, {}), null);

// An agent node with no tool calls has no tool-log row; the trace label alone
// still locates the context trace instead of hiding it.
const noToolBody = {
  items: [
    { node_id: 'quiet', kind: 'data', artifact_id: 'qd', label: 'data',
      media_type: 'application/json', size_bytes: 24, sha256: 'a'.repeat(64) },
    { node_id: 'quiet', kind: 'file', artifact_id: 'trace2', label: 'context-trace',
      media_type: 'application/json', size_bytes: 200, sha256: 'b'.repeat(64) },
  ],
};
const noToolRow = context.contextTraceRowOf(noToolBody, { items: [] }, {});
assert.equal(noToolRow && noToolRow.artifact_id, 'trace2');

const empty = context.describeContextTrace({ turns: [] });
assert.equal(empty.visible, false);
""".replace("__WEB_DIR__", repr(str(t033_stack_web_dir()))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)


def t033_stack_web_dir():
    from pathlib import Path

    return Path(__file__).resolve().parents[2] / "apps" / "web" / "src"
