"""T037: the creator tools page and admin review queue (served + Node-executed).

The structural assertions are about what the browser actually receives from the
compiled API. ``tools.js`` is then executed under Node with the same public
functions the page calls, so the payload builders, hash binding and review queue
projection are observed behaviour rather than source text.

Still ``not_run``: a DOM/browser driving the page (same boundary as T023/T027/
T029/T031/T032/T033).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t037
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_tools_page_is_served_with_submit_and_review_sections(t033_stack) -> None:
    response = t033_stack.server.http.get("/tools")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="submit-card"',
        'id="submit-form"',
        'id="source-artifact-id"',
        'id="input-schema"',
        'id="output-schema"',
        'id="dependency-lock"',
        'id="review-card"',
        'id="review-list"',
        'id="tools-list"',
        "/assets/tools.js",
        "内容包摘要",
        "dependency_revoked",
    ):
        assert marker in body, f"tools.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "object_key" not in body


def test_tools_js_is_served_with_the_client_helpers(t033_stack) -> None:
    response = t033_stack.server.http.get("/assets/tools.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function toolsPath(",
        "function toolBuildPath(",
        "function toolReviewPath(",
        "function toolRevokePath(",
        "function buildSubmitBody(",
        "function reviewBody(",
        "function revokeBody(",
        "function describeToolVersion(",
        "function describeBuildFacts(",
        "function describeReviewQueue(",
        "function canSubmit(",
        "function canReview(",
    ):
        assert marker in body, f"tools.js is missing {marker!r}"
    assert "object_key" not in body
    assert "localStorage" not in body


def test_tools_js_behaviour_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "tools-page.js"
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
vm.runInContext(fs.readFileSync(webDir + '/assets/tools.js', 'utf8'), context);

// Paths are built from the id, never from a caller URL.
assert.strictEqual(context.toolsPath(), '/api/v1/tools');
assert.strictEqual(context.toolPath('t 1/x'), '/api/v1/tools/t%201%2Fx');
assert.strictEqual(context.toolBuildPath('t1'), '/api/v1/tools/t1/build');
assert.strictEqual(context.toolReviewPath('t1'), '/api/v1/tools/t1/review');
assert.strictEqual(context.toolRevokePath('t1'), '/api/v1/tools/t1/revoke');
assert.strictEqual(context.artifactDownloadPath('a1'), '/api/v1/artifacts/a1/download');

// The submit body is the exact ToolInput; connectors stay empty.
const submit = context.buildSubmitBody({
  sourceArtifactId: ' 11111111-1111-4111-8111-111111111111 ',
  riskClass: 'read',
  inputSchema: '{"type":"object"}',
  outputSchema: '{"type":"object","properties":{}}',
  dependencyLock: '{"requirements":[]}',
});
assert.strictEqual(submit.ok, true, submit.error);
assert.deepStrictEqual(Object.keys(submit.body).sort(), [
  'connector_version_ids', 'dependency_lock', 'input_schema',
  'output_schema', 'risk_class', 'source_artifact_id',
]);
assert.strictEqual(submit.body.connector_version_ids.length, 0);
assert.strictEqual(submit.body.risk_class, 'read');

// Malformed or non-object schemas are refused before any request.
assert.strictEqual(context.buildSubmitBody({
  sourceArtifactId: 'x', riskClass: 'pure', inputSchema: '[]',
  outputSchema: '{}', dependencyLock: '{}',
}).ok, false);
assert.strictEqual(context.buildSubmitBody({
  sourceArtifactId: '', riskClass: 'pure', inputSchema: '{}',
  outputSchema: '{}', dependencyLock: '{}',
}).ok, false);
assert.strictEqual(context.buildSubmitBody({
  sourceArtifactId: 'x', riskClass: 'dangerous', inputSchema: '{}',
  outputSchema: '{}', dependencyLock: '{}',
}).ok, false);
assert.ok(context.buildSubmitBody({
  sourceArtifactId: 'x', riskClass: 'pure', inputSchema: 'not json',
  outputSchema: '{}', dependencyLock: '{}',
}).error.indexOf('input_schema') >= 0);

// Review binds the exact manifest hash and a non-empty reason.
const hash = 'a'.repeat(64);
const review = context.reviewBody('approve', hash.toUpperCase(), '  looks good  ');
assert.strictEqual(review.ok, true, review.error);
assert.deepStrictEqual(JSON.parse(JSON.stringify(review.body)), { decision: 'approve', manifest_sha256: hash, reason: 'looks good' });
assert.strictEqual(context.reviewBody('approve', 'not-a-hash', 'reason').ok, false);
assert.strictEqual(context.reviewBody('maybe', hash, 'reason').ok, false);
assert.strictEqual(context.reviewBody('reject', hash, '   ').ok, false);
assert.strictEqual(context.revokeBody(' ').ok, false);
assert.deepStrictEqual(JSON.parse(JSON.stringify(context.revokeBody(' unsafe ').body)), { reason: 'unsafe' });

// The contract projection is described without inventing fields.
const described = context.describeToolVersion({
  id: 't1', state: 'ready', approved: false, image_digest: 'sha256:' + 'b'.repeat(64),
  manifest_sha256: hash, input_schema: {}, output_schema: {},
});
assert.strictEqual(described.ready, true);
assert.strictEqual(described.approved, false);
assert.strictEqual(described.approvedText.indexOf('未批准'), 0);
assert.strictEqual(described.digest.indexOf('sha256:') >= 0, true);
assert.strictEqual(described.manifestSha256, hash);
assert.strictEqual(context.describeToolVersion({ state: 'revoked' }).ready, false);

// Build facts: ids become download paths; no object key is accepted or emitted.
const facts = context.describeBuildFacts({
  state: 'ready', build_bundle_digest: 'sha256:' + 'c'.repeat(64),
  build_log_artifact_id: 'log1', sbom_artifact_id: 'sbom1',
  build_finished_at: '2026-11-03T00:00:00Z', review_decision: 'approved',
  review_reason: 'ok', build_error_code: '', object_key: 'sealed/SECRET',
});
assert.strictEqual(facts.logPath, '/api/v1/artifacts/log1/download');
assert.strictEqual(facts.sbomPath, '/api/v1/artifacts/sbom1/download');
assert.ok(facts.stateLine.indexOf('sha256:') >= 0);
assert.ok(facts.stateLine.indexOf('approved') >= 0);
assert.ok(JSON.stringify(facts).indexOf('SECRET') < 0);
const failed = context.describeBuildFacts({
  state: 'failed', build_error_code: 'dependencies_unavailable',
  build_log_artifact_id: 'log2',
});
assert.strictEqual(failed.buildErrorCode, 'dependencies_unavailable');
assert.ok(failed.stateLine.indexOf('dependencies_unavailable') >= 0);

// Review queue: ready and not approved only.
const queue = context.describeReviewQueue([
  { id: 'a', state: 'ready', approved: false },
  { id: 'b', state: 'ready', approved: true },
  { id: 'c', state: 'failed', approved: false },
  { id: 'd', state: 'ready' },
]);
assert.strictEqual(queue.count, 2);
assert.deepStrictEqual(queue.ids, ['a', 'd']);
assert.ok(queue.text.indexOf('2') >= 0);
assert.strictEqual(context.describeReviewQueue([]).count, 0);
assert.strictEqual(context.describeReviewQueue(null).count, 0);

// Role gates read the identity response, never a client-chosen flag.
assert.strictEqual(context.canSubmit({ roles: ['creator'] }), true);
assert.strictEqual(context.canSubmit({ roles: ['user'] }), false);
assert.strictEqual(context.canReview({ roles: ['admin'] }), true);
assert.strictEqual(context.canReview({ roles: ['creator', 'admin'] }), true);
assert.strictEqual(context.canReview({ roles: ['creator'] }), false);
assert.strictEqual(context.canReview(null), false);

process.stdout.write('tools.js page behaviour ok\n');
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
        "tools.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "tools.js page behaviour ok" in completed.stdout
