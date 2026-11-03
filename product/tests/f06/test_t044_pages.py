"""F06/T044: the debug/runs temporary model preview panel and its helpers.

The structural assertions inspect what the browser actually receives from the
compiled API. ``stream-preview.js`` is then executed under Node with the same pure
helpers the pages call, so generation replacement, pending tool-call display and
final/error closing are observed behaviour rather than source text.

Browser DOM driving is ``not_run`` exactly as in T041/T042/T043: the helpers are
pure and Node-tested; ``beginModelPreview`` is the browser-only EventSource glue.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from support import t041
from support import stack as st
from support.fixtures import f06_stack  # noqa: F401 - pytest fixture

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_debug_page_has_a_real_temporary_preview_panel(f06_stack) -> None:
    response = f06_stack.server.http.get(f"/apps/{t041.GOLDEN_APPLICATION_ID}/debug")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="run-preview"',
        "/assets/stream-preview.js",
        "临时模型流式预览",
        "工具片段只显示“待定”",
    ):
        assert marker in body, f"debug.html is missing {marker!r}"
    assert "预览未实现" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_runs_page_has_the_temporary_preview_panel(f06_stack) -> None:
    response = f06_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="detail-preview"',
        "/assets/stream-preview.js",
        "临时模型流式预览",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    assert "预览未实现" not in body


def test_preview_asset_is_served_and_has_no_persistence(f06_stack) -> None:
    response = f06_stack.server.http.get("/assets/stream-preview.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function previewPath(",
        "function parsePreviewEvent(",
        "function applyPreviewFrame(",
        "function describePreview(",
        "function beginModelPreview(",
        "EventSource",
    ):
        assert marker in body, f"stream-preview.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "innerHTML" not in body
    assert "fetch(" not in body


def test_preview_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "model-preview-page.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/stream-preview.js', 'utf8'), context);

// The run id is encoded; the caller never supplies a URL.
assert.strictEqual(
  context.previewPath('r 1/x'),
  '/api/v1/runs/r%201%2Fx/model-preview',
);
assert.strictEqual(context.parsePreviewEvent('delta', 'not json'), null);
assert.strictEqual(context.parsePreviewEvent('bogus', '{"operation_id":"o","generation_id":"g"}'), null);
assert.strictEqual(context.parsePreviewEvent('delta', '{"operation_id":"op","generation_id":""}'), null);

// A failed generation's draft is replaced wholesale by the new generation.
let state = context.emptyPreviewState();
state = context.applyPreviewFrame(state, context.parsePreviewEvent('delta',
  '{"operation_id":"op","generation_id":"gen-old","text":"old partial "}'));
assert.strictEqual(state.text, 'old partial ');

// A tool-call fragment is pending only: never an executed call.
state = context.applyPreviewFrame(state, context.parsePreviewEvent('tool_delta',
  '{"operation_id":"op","generation_id":"gen-old","tool_call":{"index":0,"id":"partial","name":"lookup","arguments_fragment":"{\\"city\\":"}}'));
assert.strictEqual(state.pendingCalls.length, 1);
assert.strictEqual(state.pendingCalls[0].pending, true);
assert.strictEqual(state.pendingCalls[0].executed, false);
let described = context.describePreview(state);
assert.ok(described.pendingText.includes('待定'), described.pendingText);
assert.ok(described.pendingText.includes('未执行'), described.pendingText);

state = context.applyPreviewFrame(state, context.parsePreviewEvent('error',
  '{"operation_id":"op","generation_id":"gen-old","retryable":true,"error":{"code":"provider_transient","message":"retry"}}'));
assert.strictEqual(state.status, 'retrying');
assert.strictEqual(state.terminal, false);

state = context.applyPreviewFrame(state, context.parsePreviewEvent('delta',
  '{"operation_id":"op","generation_id":"gen-new","text":"new "}'));
assert.strictEqual(state.generationID, 'gen-new');
assert.strictEqual(state.text, 'new ');
assert.notStrictEqual(state.text, 'old partial new ');
assert.strictEqual(state.pendingCalls.length, 0);
assert.strictEqual(state.cleared, true);
assert.strictEqual(state.generations, 2);
assert.strictEqual(state.error, null);
assert.strictEqual(state.terminal, false);

// The final frame is authoritative and closes the preview.
state = context.applyPreviewFrame(state, context.parsePreviewEvent('final',
  '{"operation_id":"op","generation_id":"gen-new","data":{"text":"final text","tool_calls":[{"id":"call-1","name":"lookup","arguments":{"city":"Paris"}}],"stub":true},"receipt_artifact_id":"artifact-1","usage":{"input_tokens":1}}'));
assert.strictEqual(state.status, 'final');
assert.strictEqual(state.terminal, true);
assert.strictEqual(state.text, 'final text');
assert.strictEqual(state.pendingCalls.length, 0);
assert.strictEqual(state.receiptArtifactID, 'artifact-1');
described = context.describePreview(state);
assert.strictEqual(described.finalToolCallCount, 1);
assert.strictEqual(described.generationID, 'gen-new');
assert.ok(described.statusText.includes('完整校验结果'), described.statusText);

// A non-retryable error is terminal and does not pretend success.
let failed = context.emptyPreviewState();
failed = context.applyPreviewFrame(failed, context.parsePreviewEvent('delta',
  '{"operation_id":"op","generation_id":"gen-1","text":"draft"}'));
failed = context.applyPreviewFrame(failed, context.parsePreviewEvent('error',
  '{"operation_id":"op","generation_id":"gen-1","error":{"code":"provider_transient","message":"gave up"}}'));
assert.strictEqual(failed.status, 'error');
assert.strictEqual(failed.terminal, true);
assert.strictEqual(context.describePreview(failed).statusText, '流式调用失败（临时预览结束）');

process.stdout.write('model preview page behaviour ok\n');
""".replace("__WEB_DIR__", repr(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, f"node failed:\n{completed.stdout}\n{completed.stderr}"
    assert "model preview page behaviour ok" in completed.stdout
