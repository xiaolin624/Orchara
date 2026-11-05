"""T068 pages: the creator publish page and the review pages.

Two layers, both real: the pages/assets are fetched from the running API over
HTTP, and their pure logic is executed under Node from the exact served bytes.
No browser DOM is driven (same boundary as every existing page test).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import uuid

import pytest

from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
ACTION = re.compile(r"\baction\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_publish_page_is_served_per_application(t068_stack) -> None:
    response = t068_stack.server.http.get(f"/apps/{uuid.uuid4()}/publish")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text
    for marker in (
        'id="publish-form"',
        'method="post"',
        'id="publish-preview"',
        'id="publish-manifest-hash"',
        'id="publish-purpose"',
        'id="publish-model-destinations"',
        'id="publish-tools"',
        'id="publish-subapps"',
        'id="publish-expected-hash"',
        'id="publish-report-id"',
        'id="publish-submit"',
        'id="publish-status"',
        "Idempotency-Key",
        "/api/v1/applications/{id}/submit",
        "/assets/app.js",
        "/assets/publish.js",
        "/reviews",
    ):
        assert marker in body, f"publish.html is missing {marker!r}"
    assert "localStorage" not in body and "sessionStorage" not in body
    assert "?token=" not in body
    forms = FORM_TAG.findall(body)
    assert len(forms) == 1, forms
    assert ACTION.search(forms[0]) is None, forms[0]


def test_the_review_pages_are_served(t068_stack) -> None:
    listing = t068_stack.server.http.get("/reviews")
    assert listing.status == 200, listing.text
    assert "text/html" in listing.header("Content-Type")
    detail = t068_stack.server.http.get(f"/reviews/{uuid.uuid4()}")
    assert detail.status == 200, detail.text
    body = listing.text
    for marker in (
        'id="reviews-rows"',
        'id="review-detail-card"',
        'id="review-purpose"',
        'id="review-model-destinations"',
        'id="review-tools"',
        'id="review-subapps"',
        'id="review-author-cases"',
        'id="review-decision-form"',
        'id="review-decision-reason"',
        "Idempotency-Key",
        "/api/v1/reviews/{id}/decision",
        "/assets/app.js",
        "/assets/reviews.js",
    ):
        assert marker in body, f"reviews.html is missing {marker!r}"
    assert "localStorage" not in body and "sessionStorage" not in body


def test_the_page_assets_are_the_served_javascript(t068_stack) -> None:
    publish = t068_stack.server.http.get("/assets/publish.js")
    assert publish.status == 200, publish.text
    assert "javascript" in publish.header("Content-Type")
    for marker in (
        "function publicationSubmitBody(",
        "function describeReviewSnapshot(",
        "function describeToolCapability(",
        "function describeSubappVersion(",
        "function publicationPreviewPath(",
        "function publicationSubmitPath(",
        "function newPublicationIdempotencyKey(",
    ):
        assert marker in publish.text, f"publish.js is missing {marker!r}"

    reviews = t068_stack.server.http.get("/assets/reviews.js")
    assert reviews.status == 200, reviews.text
    assert "javascript" in reviews.header("Content-Type")
    for marker in (
        "function reviewDecisionBody(",
        "function reviewIDFromPath(",
        "function describeReviewQueue(",
        "function describeDecision(",
        "function snapshotBlocks(",
        "function canReview(",
        "function reviewsListPath(",
    ):
        assert marker in reviews.text, f"reviews.js is missing {marker!r}"


def test_publish_page_logic_under_node(node_binary, t068_stack) -> None:
    source = t068_stack.server.http.get("/assets/publish.js").text
    script = r"""
const vm = require('vm');
const fs = require('fs');
const code = fs.readFileSync(0, 'utf8');
const context = {};
vm.runInNewContext(code, context);
function check(value, message) { if (!value) { throw new Error(message); } }

const ok = context.publicationSubmitBody('a'.repeat(64), '11111111-1111-4111-8111-111111111111');
check(ok.ok, 'valid submit body refused');
check(Object.keys(ok.body).sort().join(',') === 'evaluation_report_id,expected_manifest_sha256', 'submit body fields');
check(ok.body.expected_manifest_sha256 === 'a'.repeat(64), 'hash normalization');
check(context.publicationSubmitBody('not-a-hash', '11111111-1111-4111-8111-111111111111').ok === false, 'bad hash accepted');
check(context.publicationSubmitBody('a'.repeat(64), 'nope').ok === false, 'bad report id accepted');
check(/^[0-9a-f]{32}$/.test(context.newPublicationIdempotencyKey()), 'idempotency key shape');
check(context.publicationSubmitPath('app id').includes('app%20id'), 'path encoding');

const snapshot = {
  manifest_sha256: 'b'.repeat(64),
  execution_revision: 3,
  draft_revision: 4,
  report_id: 'r',
  report_status: 'passed',
  report_sha256: 'c'.repeat(64),
  purpose_card: {purpose: '整理销售表', includes: ['CSV'], excludes: ['写入'], required_inputs: ['CSV'], expected_outputs: ['统计'], examples: ['一月销售']},
  model_destinations: [{model_profile_version_id: 'm', provider: 'local', model_id: 'stub', data_destination: 'local'}],
  tool_capabilities: [{tool_version_id: 't', image_digest: 'sha256:' + '1'.repeat(64), risk_class: 'write', read: true, write: true, network: true}],
  subapp_versions: [{version_id: 'v', application_id: 'a', application_name: '子应用', version_no: 2, manifest_sha256: 'd'.repeat(64)}],
  connector_policies: [{version_id: 'c', policy_sha256: 'e'.repeat(64)}],
  author_cases: {satisfied: true, case_count: 5, minimum_cases: 5, categories: ['normal', 'bad_input'], missing: []},
};
const view = context.describeReviewSnapshot(snapshot);
check(view.manifestSha256 === 'b'.repeat(64), 'manifest hash');
check(view.purpose.purpose === '整理销售表', 'purpose');
check(view.modelDestinations.length === 1 && view.modelDestinations[0].detail.includes('local'), 'model destination');
check(view.toolCapabilities[0].writeText === '写入' && view.toolCapabilities[0].networkText === '联网', 'tool capabilities');
check(view.anyWrite === true && view.anyNetwork === true, 'write/network flags');
check(view.subappVersions[0].label.includes('子应用 v2'), 'subapp label');
check(view.connectorPolicies.length === 1, 'connector policy');
check(view.authorCases.satisfied === true && view.authorCases.caseCount === 5, 'author cases');
const empty = context.describeReviewSnapshot({});
check(empty.modelDestinations.length === 0 && empty.toolCapabilities.length === 0, 'empty lists invented entries');
check(context.describePublicationFailure({code: 'manifest_mismatch', message: 'changed'}).includes('manifest_mismatch'), 'failure text');
"""
    result = subprocess.run(
        [node_binary, "-e", script],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_reviews_page_logic_under_node(node_binary, t068_stack) -> None:
    source = t068_stack.server.http.get("/assets/reviews.js").text
    script = r"""
const vm = require('vm');
const fs = require('fs');
const code = fs.readFileSync(0, 'utf8');
const context = {};
vm.runInNewContext(code, context);
function check(value, message) { if (!value) { throw new Error(message); } }

const id = '11111111-1111-4111-8111-111111111111';
check(context.reviewIDFromPath('/reviews/' + id) === id, 'review id from path');
check(context.reviewIDFromPath('/reviews') === null, 'queue path');
check(context.reviewIDFromPath('/reviews/not-a-uuid') === null, 'invalid id');
check(context.canReview({roles: ['admin']}) === true, 'admin capability');
check(context.canReview({roles: ['creator']}) === false, 'creator is not admin');
check(context.reviewsListPath(null, 20) === '/api/v1/reviews?limit=20', 'list path');
check(context.reviewsListPath('abc', null) === '/api/v1/reviews?cursor=abc', 'cursor path');

const body = context.reviewDecisionBody('approve', 'a'.repeat(64), 'looks good');
check(body.ok && body.body.decision === 'approve', 'approve body');
check(context.reviewDecisionBody('approve', 'bad', 'x').ok === false, 'bad hash accepted');
check(context.reviewDecisionBody('approve', 'a'.repeat(64), '  ').ok === false, 'empty reason accepted');
check(context.reviewDecisionBody('maybe', 'a'.repeat(64), 'x').ok === false, 'bad decision accepted');

const queue = context.describeReviewQueue([
  {id: 'b', state: 'approved', application_id: 'a', manifest_sha256: '1'},
  {id: 'a', state: 'submitted', application_id: 'a', manifest_sha256: '2'},
]);
check(queue[0].state === 'submitted' && queue[1].state === 'approved', 'queue order');

const decision = context.describeDecision({id: 'r', state: 'approved', version_id: 'v'});
check(decision.text.includes('v') && decision.versionID === 'v', 'decision text');
const rejected = context.describeDecision({id: 'r', state: 'rejected', version_id: null});
check(rejected.versionID === null && rejected.text.includes('编辑'), 'rejection text');

const blocks = context.snapshotBlocks({
  manifest_sha256: 'f'.repeat(64),
  purpose_card: {purpose: 'p', includes: [], excludes: [], required_inputs: [], expected_outputs: [], examples: []},
  model_destinations: [{model_profile_version_id: 'm', provider: 'p', model_id: 'm', data_destination: 'd'}],
  tool_capabilities: [{tool_version_id: 't', risk_class: 'read', read: true, write: false, network: false}],
  subapp_versions: [],
  connector_policies: [],
  author_cases: {satisfied: false, case_count: 2, minimum_cases: 5, categories: [], missing: ['normal']},
});
check(blocks.modelDestinations.length === 1, 'model block');
check(blocks.tools.length === 1 && blocks.tools[0].read === true && blocks.tools[0].write === false, 'tool block');
check(blocks.authorCases.satisfied === false && blocks.authorCases.missing[0] === 'normal', 'author case block');
"""
    result = subprocess.run(
        [node_binary, "-e", script],
        input=source,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
