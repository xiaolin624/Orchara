"""F08/T082: the chat page's handover-preview surface and Node-executable helpers.

Structural assertions are about what the real compiled API serves; the second test
executes ``chat.js`` under Node with the same bytes a browser receives and observes
the pure helpers (preview request body, server-summary projection, exact confirmed
handoff, artifact selection and the handling version). No DOM is driven: that stays
the same boundary as T023/T027/T033/T074/T079.
"""

from __future__ import annotations

import json
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


def test_the_chat_page_serves_the_handover_preview_surface(f08_stack) -> None:
    response = f08_stack.server.http.get("/chat")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="chat-handoff"',
        'id="chat-handoff-source-runs"',
        'id="chat-handoff-load-artifacts"',
        'id="chat-handoff-artifacts"',
        'id="chat-handoff-artifact-options"',
        'id="chat-handoff-preview-button"',
        'id="chat-handoff-preview"',
        'id="chat-handoff-scope"',
        'id="chat-handoff-summary"',
        'id="chat-handoff-confirmed"',
        'id="chat-handling"',
        "交接预览",
        "处理版本",
        "/assets/chat.js",
    ):
        assert marker in body, f"chat.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body

    asset = f08_stack.server.http.get("/assets/chat.js")
    assert asset.status == 200, asset.text
    script = asset.text
    for marker in (
        "function handoffPreviewPath(",
        "function handoffPreviewBody(",
        "function describeHandoffPreview(",
        "function handoffFromPreview(",
        "function handoffArtifactOptions(",
        "function describeHandling(",
        "X-Request-Application-Version-No",
        "handoff_scope_exceeded",
        "handoff_not_confirmed",
        "Idempotency-Key",
    ):
        assert marker in script, f"chat.js is missing {marker!r}"
    # No secret material is embedded and no object key reaches the page.
    for forbidden in ("object_key", "ORCHARA_", "secret", "BEGIN PRIVATE"):
        assert forbidden not in script, forbidden


def test_chat_js_handover_helpers_execute_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t082-chat-helpers.js"
    harness.write_text(
        r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console, crypto, window: { location: { assign() {} }, open() {} } };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/attachments.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/chat.js', 'utf8'), context);

const cid = '11111111-2222-4333-8444-555555555555';
const runID = '66666666-7777-4888-9999-aaaaaaaaaaaa';
const artifactID = 'bbbbbbbb-cccc-4ddd-8eee-ffffffffffff';
const inputArtifactID = 'cccccccc-dddd-4eee-8fff-000000000000';

// The preview endpoint path is built from the id, never from a caller URL.
assert.strictEqual(
  context.handoffPreviewPath('c1'), '/api/v1/conversations/c1/handoff-preview');
assert.strictEqual(
  context.handoffPreviewPath('a b/c'), '/api/v1/conversations/a%20b%2Fc/handoff-preview');

// The preview request body is exactly the two documented arrays, deduplicated.
const previewBody = context.handoffPreviewBody([runID, runID], [artifactID]);
assert.strictEqual(
  JSON.stringify(previewBody),
  JSON.stringify({ source_run_ids: [runID], artifact_ids: [artifactID] }));
assert.throws(
  () => context.handoffPreviewBody([], []),
  (error) => error.code === 'handoff.source_run_ids');
assert.throws(
  () => context.handoffPreviewBody(
    [runID, '11111111-2222-4333-8444-555555555555',
     '22222222-3333-4444-8555-666666666666', '33333333-4444-4555-8666-777777777777',
     '44444444-5555-4666-8777-888888888888', '55555555-6666-4777-8888-999999999999'], []),
  (error) => error.code === 'handoff_scope_exceeded');
assert.throws(
  () => context.handoffPreviewBody([runID], ['not-a-uuid']),
  (error) => error.code === 'handoff.artifact_ids');

// The page renders the server preview; it never invents a summary.
assert.strictEqual(context.describeHandoffPreview(null).visible, false);
const preview = {
  conversation_id: cid,
  from_version_id: '77777777-8888-4999-8aaa-bbbbbbbbbbbb',
  source_run_ids: [runID],
  artifact_ids: [artifactID],
  summary: '本次交接摘要（服务端生成）：仅元数据。',
  summary_sha256: 'a'.repeat(64),
  source_runs: [{ run_id: runID, application_name: '来源应用', version_no: 1 }],
  artifacts: [{ artifact_id: artifactID, purpose: 'task_input', size_bytes: 10 }],
  limits: { max_source_runs: 5, max_artifacts: 5, max_summary_runes: 2000 },
};
const described = context.describeHandoffPreview(preview);
assert.strictEqual(described.visible, true);
assert.strictEqual(described.summary, preview.summary);
assert.strictEqual(described.sourceRunCount, 1);
assert.strictEqual(described.artifactCount, 1);
assert.strictEqual(described.bounded, true);
assert.strictEqual(described.fromVersionId, preview.from_version_id);

// The submitted handoff is exactly the confirmed server preview.
assert.throws(
  () => context.handoffFromPreview(null, true),
  (error) => error.code === 'handoff_preview_required');
assert.throws(
  () => context.handoffFromPreview(preview, false),
  (error) => error.code === 'handoff_not_confirmed');
const handoff = context.handoffFromPreview(preview, true);
assert.strictEqual(
  JSON.stringify(handoff),
  JSON.stringify({
    source_run_ids: [runID],
    artifact_ids: [artifactID],
    summary: preview.summary,
    confirmed: true,
  }));
assert.strictEqual(JSON.stringify(Object.keys(handoff).sort()),
  JSON.stringify(['artifact_ids', 'confirmed', 'source_run_ids', 'summary']));

// Source-run artifact selection excludes the old Run's own input bytes: only
// produced artifacts may cross the boundary.
const options = context.handoffArtifactOptions([
  { artifact_id: artifactID, relation: 'output', purpose: 'report',
    media_type: 'text/plain', size_bytes: 12 },
  { artifact_id: inputArtifactID, relation: 'input', purpose: 'request_input' },
  { artifact_id: 'dddddddd-eeee-4fff-8000-111111111111', relation: 'log' },
]);
assert.strictEqual(options.length, 2, JSON.stringify(options));
assert.strictEqual(options[0].id, artifactID);
assert.strictEqual(options[0].selectable, true);
assert.ok(options[0].label.indexOf('report') >= 0, options[0].label);
for (const option of options) {
  assert.notStrictEqual(option.id, inputArtifactID);
}

// The handling version stays header-derived and is never guessed.
const facts = context.requestFacts({
  get: (name) => ({
    'X-Request-Application-Version-Id': preview.from_version_id,
    'X-Request-Application-Version-No': '2',
  }[name] || null),
});
assert.strictEqual(facts.versionNo, 2);
assert.strictEqual(facts.applicationVersionId, preview.from_version_id);
assert.strictEqual(
  context.describeHandling({ run_id: 'r1' }, { state: 'queued' }, { version_no: 2 }).text,
  'v2 正在处理');
assert.strictEqual(context.describeHandling(null, null, null).text, '尚无处理应用');

process.stdout.write('chat.js handover helper behaviour ok\n');
""".replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "chat.js handover helpers did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
