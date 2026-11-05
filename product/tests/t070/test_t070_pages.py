"""T070 pages: the version/revocation admin page and the catalog availability text.

Two layers, both real (the same boundary as every other page ticket): the compiled
API serves the page and the asset over real HTTP, and the pure helpers are
executed under Node from the exact bytes a browser receives. No browser/DOM
automation exists in this repo, so the DOM wiring itself is structural only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid

import pytest

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
ACTION = re.compile(r"\baction\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
METHOD_POST = re.compile(r"\bmethod\s*=\s*[\"']?post[\"']?", re.IGNORECASE)

VERSIONS_HARNESS = """
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = %s;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const versionsSource = fs.readFileSync(webDir + '/assets/versions.js', 'utf8');

const context = { window: { location: { pathname: '/' } }, console };
vm.createContext(context);
vm.runInContext(appSource, context, { filename: 'app.js' });
vm.runInContext(versionsSource, context, { filename: 'versions.js' });

// The revoke request body is exactly {reason, stop_existing} (no Idempotency-Key:
// the contract has none for revokeVersion).
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(context.revokeVersionBody('  withdrawn from service  ', true))),
  { ok: true, body: { reason: 'withdrawn from service', stop_existing: true } },
);
assert.deepStrictEqual(
  JSON.parse(JSON.stringify(context.revokeVersionBody('because', false))),
  { ok: true, body: { reason: 'because', stop_existing: false } },
);
assert.strictEqual(context.revokeVersionBody('   ', true).ok, false);
assert.strictEqual(context.revokeVersionBody('x'.repeat(2001), true).ok, false);
assert.strictEqual(context.revokeVersionBody('ok', 'yes').ok, false);
assert.strictEqual(context.revokeVersionBody('ok', undefined).ok, false);

// Only an administrator may revoke a published version.
assert.strictEqual(context.canRevokeVersion({ roles: ['admin'] }), true);
assert.strictEqual(context.canRevokeVersion({ roles: ['creator'] }), false);
assert.strictEqual(context.canRevokeVersion({ roles: [] }), false);
assert.strictEqual(context.canRevokeVersion(null), false);

// Path ids only; no query string and no invented id.
const application = '11111111-1111-4111-8111-111111111111';
const version = '22222222-2222-4222-8222-222222222222';
assert.strictEqual(context.applicationIdFromVersionsPath('/apps/' + application + '/versions'), application);
assert.strictEqual(context.applicationIdFromVersionsPath('/apps/' + application + '/versions/'), application);
assert.strictEqual(context.applicationIdFromVersionsPath('/apps/not-an-id/versions'), null);
assert.strictEqual(context.applicationIdFromVersionsPath('/apps/' + application + '/edit'), null);
assert.strictEqual(context.versionsPath(application), '/api/v1/applications/' + application + '/versions');
assert.strictEqual(context.revokeVersionPath(version), '/api/v1/versions/' + version + '/revoke');
assert.strictEqual(context.runnableVersionPath(version), '/api/v1/versions/' + version + '/runnable');

// The catalog shows an unavailable application; the Version contract has no
// state field, so an active_version_id of null is the only availability signal.
const unavailable = context.describeCatalogAvailability({ id: application, active_version_id: null });
assert.ok(unavailable.indexOf('不可用') >= 0, unavailable);
assert.ok(unavailable.indexOf('撤销') >= 0, unavailable);
const available = context.describeCatalogAvailability({ id: application, active_version_id: version });
assert.ok(available.indexOf('可用') >= 0, available);

const described = context.describeVersion({ id: version, version_no: 2, manifest_sha256: 'a'.repeat(64) });
assert.ok(described.indexOf('v2') >= 0, described);
assert.ok(described.indexOf('a'.repeat(12)) >= 0, described);
assert.strictEqual(context.describeVersion({ version_no: 3 }).indexOf('v3') >= 0, true);

const options = context.describeVersionOptions([
  { id: version, version_no: 2, manifest_sha256: 'b'.repeat(64) },
  { id: application, version_no: 1, manifest_sha256: 'c'.repeat(64) },
]);
assert.strictEqual(options.length, 2);
assert.strictEqual(options[0].id, version);
assert.ok(options[0].label.indexOf('v2') >= 0, options[0].label);

assert.ok(context.describeRevokeResult({ id: version, state: 'revoked' }).indexOf('已撤销') >= 0);
const failure = context.describeRevocationFailure({ code: 'version_revoked', message: 'no', status: 409 });
assert.ok(failure.indexOf('version_revoked') >= 0, failure);
const unavailableFailure = context.describeRevocationFailure({ code: 'resource_not_found', message: 'gone', status: 404 });
assert.ok(unavailableFailure.indexOf('resource_not_found') >= 0, unavailableFailure);

process.stdout.write('versions.js behaviour ok\\n');
"""


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH")
    return NODE


def test_the_versions_page_is_served_per_application(t070_stack) -> None:
    application_id = str(uuid.uuid4())
    response = t070_stack.server.http.get(f"/apps/{application_id}/versions")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text
    for marker in (
        'id="versions-page"',
        'id="versions-list"',
        'id="versions-status"',
        'id="application-availability"',
        'id="revoke-form"',
        'id="revoke-version-id"',
        'id="revoke-reason"',
        'id="revoke-stop-existing"',
        'id="revoke-submit"',
        'id="revoke-status"',
        'role="status"',
        "撤销",
        "不可用",
        "/assets/app.js",
        "/assets/versions.js",
        "/api/v1/applications/",
    ):
        assert marker in body, f"versions.html is missing {marker!r}"

    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token=" not in body

    forms = FORM_TAG.findall(body)
    assert len(forms) == 1, forms
    for form in forms:
        assert METHOD_POST.search(form), f"the revoke form must be method=post: {form}"
        assert ACTION.search(form) is None, f"the revoke form must not carry an action: {form}"


def test_the_versions_asset_is_the_served_asset(t070_stack) -> None:
    response = t070_stack.server.http.get("/assets/versions.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    for marker in (
        "function revokeVersionBody(",
        "function canRevokeVersion(",
        "function describeCatalogAvailability(",
        "function applicationIdFromVersionsPath(",
        "function describeVersionOptions(",
    ):
        assert marker in body, f"versions.js is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body


def test_the_catalog_page_exposes_the_version_page_and_availability(t070_stack) -> None:
    response = t070_stack.server.http.get("/apps")
    assert response.status == 200, response.text
    body = response.text
    assert "/assets/versions.js" in body
    assert "describeCatalogAvailability" in body
    assert "/versions" in body, "the catalog must link each application to its versions page"


def test_versions_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "versions-harness.js"
    harness.write_text(VERSIONS_HARNESS % json.dumps(str(st.WEB_DIR)), encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "versions.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
