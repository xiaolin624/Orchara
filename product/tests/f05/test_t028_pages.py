"""T028: the attachment page is a real client of the upload contract.

Two layers, both real:

* the page and the JavaScript asset are fetched from the running API over HTTP,
  and the structural assertions are about what the browser receives (POST form,
  no query-string action, no credential storage, the status/download panel);
* the page's hashing, request-building, PUT and polling logic is executed under
  Node with a stubbed ``fetch``, so the SHA-256 digest, the CSRF header, the
  cookie-less signed PUT, the quarantine-reason header and the stop rule are
  observed behaviour rather than source text.

A DOM/browser driving the file input is still not run; that boundary is the same
one F01/T023/T027 record and is repeated in the T028 report.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st
from support import t028
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t028_stack,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
ACTION = re.compile(r"\baction\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
METHOD_POST = re.compile(r"\bmethod\s*=\s*[\"']?post[\"']?", re.IGNORECASE)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip(
            "node is not available on PATH; the attachment page logic cannot be "
            "executed (the structural page tests still run)"
        )
    return NODE


def test_the_attachment_page_is_served(t028_stack) -> None:
    response = t028_stack.server.http.get("/attachments")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="upload-form"',
        'method="post"',
        'id="upload-file"',
        'type="file"',
        'id="upload-media-type"',
        'id="upload-purpose"',
        'id="upload-submit"',
        'id="upload-status"',
        'role="status"',
        'id="artifact-panel"',
        'id="artifact-id"',
        'id="artifact-state"',
        'id="artifact-media-type"',
        'id="artifact-size"',
        'id="artifact-download"',
        'id="artifact-quarantine"',
        'id="artifact-quarantine-reason"',
        "quarantined",
        "20 MiB",
        "/assets/app.js",
        "/assets/attachments.js",
        "/apps",
        "/admin",
    ):
        assert marker in body, f"attachments.html is missing {marker!r}"

    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token=" not in body

    forms = FORM_TAG.findall(body)
    assert len(forms) == 1, forms
    for form in forms:
        assert METHOD_POST.search(form), f"the upload form must be method=post: {form}"
        assert ACTION.search(form) is None, f"the upload form must not carry an action: {form}"

    for path in ("/attachments/", "/assets/attachments.js/", "/app/attachments"):
        assert t028_stack.server.http.get(path).status in (301, 307, 404)


def test_attachments_js_is_the_served_asset(t028_stack) -> None:
    response = t028_stack.server.http.get("/assets/attachments.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    for marker in (
        "function uploadInputForFile(",
        "async function sha256Hex(",
        "async function createUploadRequest(",
        "async function putUploadBytes(",
        "async function completeUploadRequest(",
        "async function fetchArtifact(",
        "async function fetchDownloadLink(",
        "function artifactIsTerminal(",
        "function shouldPollArtifact(",
        "function quarantineMessage(",
        "X-Artifact-Quarantine-Reason",
        "credentials: 'omit'",
        "/api/v1/artifacts/",
    ):
        assert marker in body, f"attachments.js is missing {marker!r}"


def test_the_apps_and_debug_pages_link_into_the_attachment_page(t028_stack) -> None:
    apps = t028_stack.server.http.get("/apps")
    assert apps.status == 200
    assert 'href="/attachments"' in apps.text, "apps.html does not link the attachment page"

    debug = t028_stack.server.http.get(f"/apps/{t028.new_uuid()}/debug")
    assert debug.status == 200
    assert 'href="/attachments"' in debug.text, "debug.html does not link the attachment page"


def test_inline_scripts_and_attachments_js_parse_with_node(t028_stack, node_binary, tmp_path) -> None:
    body = (st.WEB_DIR / "attachments.html").read_text(encoding="utf-8")
    for index, block in enumerate(INLINE_SCRIPT.findall(body)):
        script = tmp_path / f"attachments.{index}.js"
        script.write_text(block, encoding="utf-8")
        completed = subprocess.run(
            [node_binary, "--check", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "attachments.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


ATTACHMENTS_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const pageSource = fs.readFileSync(webDir + '/assets/attachments.js', 'utf8');
const { webcrypto } = require('crypto');

function jsonResponse(status, payload, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    headers: { get: (name) => (headers || {})[name] || null },
  };
}

function load(route) {
  const calls = [];
  const assignments = [];
  const context = {
    fetch: async (path, init) => {
      calls.push({ path, init });
      return route(path, init);
    },
    crypto: webcrypto,
    window: { location: { assign: (target) => assignments.push(target) } },
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(pageSource, context, { filename: 'attachments.js' });
  return { context, calls, assignments };
}

(async () => {
  // SHA-256 is the browser's own WebCrypto result, not a re-implementation.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(
      await context.sha256Hex(new TextEncoder().encode('abc')),
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
    assert.strictEqual(
      await context.sha256Hex(new TextEncoder().encode('')),
      'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
    );
  }

  // UploadInput validation mirrors the contract before any request is sent.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const payload = context.uploadInputForFile({ size: 12, name: 'orders.csv' }, 'text/csv', 'task_input');
    assert.strictEqual(JSON.stringify(payload), JSON.stringify({
      media_type: 'text/csv',
      size_bytes: 12,
      purpose: 'task_input',
    }));
    assert.throws(() => context.uploadInputForFile({ size: 0 }, 'text/plain', 'task_input'), (error) => error.code === 'size_bytes');
    assert.throws(() => context.uploadInputForFile({ size: 20 * 1024 * 1024 + 1 }, 'text/plain', 'task_input'), (error) => error.code === 'size_bytes');
    assert.throws(() => context.uploadInputForFile({ size: 1 }, 'application/pdf', 'task_input'), (error) => error.code === 'media_type');
    assert.throws(() => context.uploadInputForFile({ size: 1 }, 'text/plain', 'report'), (error) => error.code === 'purpose');
    assert.throws(() => context.uploadInputForFile(null, 'text/plain', 'task_input'), (error) => error.code === 'file');
  }

  // Paths use encodeURIComponent and never a query string.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(context.artifactPath('a b'), '/api/v1/artifacts/a%20b');
    assert.strictEqual(context.artifactCompletePath('x'), '/api/v1/artifacts/x/complete');
    assert.strictEqual(context.artifactDownloadPath('x'), '/api/v1/artifacts/x/download');
  }

  // createUpload: POST JSON with the session CSRF token.
  {
    const { context, calls } = load(async () => jsonResponse(201, {
      artifact_id: '11111111-1111-4111-8111-111111111111',
      upload_url: 'http://api/object-links/token',
      expires_at: '2026-11-03T00:00:00Z',
    }));
    const created = await context.createUploadRequest(
      { media_type: 'text/plain', size_bytes: 5, purpose: 'task_input' },
      'csrf-upload',
    );
    assert.strictEqual(created.artifact_id, '11111111-1111-4111-8111-111111111111');
    const post = calls.find((call) => call.init.method === 'POST');
    assert.ok(post, 'no POST was sent');
    assert.strictEqual(post.path, '/api/v1/artifacts/uploads');
    assert.strictEqual(post.init.headers['X-CSRF-Token'], 'csrf-upload');
    assert.strictEqual(post.init.headers['Content-Type'], 'application/json');
    assert.strictEqual(post.init.body, JSON.stringify({ media_type: 'text/plain', size_bytes: 5, purpose: 'task_input' }));
  }

  // The signed PUT carries no ambient credential and no CSRF header.
  {
    const { context, calls } = load(async (path, init) => {
      assert.strictEqual(path, 'http://api/object-links/token');
      assert.strictEqual(init.method, 'PUT');
      assert.strictEqual(init.credentials, 'omit');
      assert.strictEqual(init.cache, 'no-store');
      assert.strictEqual(init.headers['Content-Type'], 'application/octet-stream');
      assert.strictEqual(init.headers['Authorization'], undefined);
      assert.strictEqual(init.headers['X-CSRF-Token'], undefined);
      return { status: 204, text: async () => '', headers: { get: () => null } };
    });
    assert.strictEqual(await context.putUploadBytes('http://api/object-links/token', btoaBytes('hello')), true);
    assert.strictEqual(calls.length, 1);
  }

  // A failed PUT surfaces the API's stable code, not a token-bearing URL.
  {
    const { context } = load(async () => jsonResponse(413, {
      error: { code: 'payload_too_large', message: 'too large', request_id: 'r' },
    }), 413);
    let refused = null;
    try {
      await context.putUploadBytes('http://api/object-links/token', new Uint8Array([1]));
    } catch (error) {
      refused = error;
    }
    assert.ok(refused && refused.code === 'payload_too_large' && refused.status === 413);
  }

  // completeUpload: POST the lowercase hash with CSRF.
  {
    const { context, calls } = load(async () => jsonResponse(202, {
      id: '11111111-1111-4111-8111-111111111111',
      state: 'scanning',
      media_type: 'text/plain',
      size_bytes: 5,
    }));
    const artifact = await context.completeUploadRequest(
      '11111111-1111-4111-8111-111111111111',
      'a'.repeat(64),
      'csrf-complete',
    );
    assert.strictEqual(artifact.state, 'scanning');
    const call = calls[calls.length - 1];
    assert.strictEqual(call.path, '/api/v1/artifacts/11111111-1111-4111-8111-111111111111/complete');
    assert.strictEqual(call.init.headers['X-CSRF-Token'], 'csrf-complete');
    assert.strictEqual(call.init.body, JSON.stringify({ sha256: 'a'.repeat(64) }));
  }

  // Polling reads the four-field Artifact and the quarantine reason header.
  {
    const { context, calls } = load(async (path) => {
      assert.strictEqual(path, '/api/v1/artifacts/11111111-1111-4111-8111-111111111111');
      return jsonResponse(200, {
        id: '11111111-1111-4111-8111-111111111111',
        state: 'quarantined',
        media_type: 'application/json',
        size_bytes: 9,
      }, { 'X-Artifact-Quarantine-Reason': 'invalid_json' });
    });
    const polled = await context.fetchArtifact('11111111-1111-4111-8111-111111111111');
    assert.strictEqual(polled.artifact.state, 'quarantined');
    assert.strictEqual(polled.quarantineReason, 'invalid_json');
    assert.strictEqual(calls[0].init.method, 'GET');
    assert.strictEqual(calls[0].init.body, undefined);
  }

  // Terminal/poll decisions and the safe Chinese reason mapping.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(context.artifactIsTerminal('ready'), true);
    assert.strictEqual(context.artifactIsTerminal('quarantined'), true);
    assert.strictEqual(context.artifactIsTerminal('scanning'), false);
    assert.strictEqual(context.shouldPollArtifact('scanning', 0, 3), true);
    assert.strictEqual(context.shouldPollArtifact('scanning', 3, 3), false);
    assert.strictEqual(context.shouldPollArtifact('ready', 0, 3), false);
    assert.strictEqual(context.shouldPollArtifact(null, 0, 3), false);
    assert.ok(context.quarantineMessage('xlsx_macro_present').includes('宏'));
    assert.ok(context.quarantineMessage('unknown_future_code').includes('未知原因代码'));
    assert.ok(context.quarantineMessage('').includes('隔离'));
  }

  // A fresh download link is requested at click time, not at scan time.
  {
    const { context, calls } = load(async () => jsonResponse(200, {
      url: 'http://api/object-links/download-token',
      expires_at: '2026-11-03T00:00:00Z',
    }));
    const link = await context.fetchDownloadLink('11111111-1111-4111-8111-111111111111');
    assert.strictEqual(link.url, 'http://api/object-links/download-token');
    assert.strictEqual(calls[0].path, '/api/v1/artifacts/11111111-1111-4111-8111-111111111111/download');
  }

  process.stdout.write('attachments.js behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});

function btoaBytes(value) {
  return new TextEncoder().encode(value);
}
"""


def test_attachment_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "attachments-harness.js"
    harness.write_text(
        ATTACHMENTS_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "attachments.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "attachments.js behaviour ok" in completed.stdout, completed.stdout
