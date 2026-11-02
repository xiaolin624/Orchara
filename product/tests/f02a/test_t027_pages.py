"""T027: the debug entry is a real client of the debug contract.

Two layers, both real:

* the page and the JavaScript asset are fetched from the running API over HTTP,
  and the structural assertions are about what the browser receives (POST form,
  no query-string action, no credential storage, the run panel fields);
* the page's request/validation/polling logic is executed under Node with a
  stubbed ``fetch`` (like the F01/T023 page tests), so the generated
  Idempotency-Key, the POST body, the CSRF header, the response-header hash and
  the polling stop rule are observed behaviour rather than source text.

What is still not run: a DOM/browser driving the form. That limitation is the
same one F01/T023 record and is repeated in the T027 report.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
import subprocess

import pytest

from support import stack as st
from support import t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
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
            "node is not available on PATH; the debug page logic cannot be executed "
            "(the structural page tests still run)"
        )
    return NODE


def test_the_debug_page_is_served_per_application(t027_stack) -> None:
    response = t027_stack.server.http.get(f"/apps/{t027.new_uuid()}/debug")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="debug-form"',
        'method="post"',
        'id="debug-revision"',
        'id="debug-input"',
        'id="debug-artifacts"',
        'id="debug-submit"',
        'id="debug-status"',
        'role="status"',
        'id="debug-key"',
        'id="run-panel"',
        'id="run-id"',
        'id="run-state"',
        'id="run-kind"',
        'id="run-revision"',
        'id="run-draft-revision"',
        'id="run-snapshot"',
        'id="run-manifest"',
        'id="run-note"',
        "Idempotency-Key",
        "409",
        "/api/v1/applications/{id}/debug-runs",
        "/api/v1/runs/{id}",
        "/assets/app.js",
        "/assets/debug.js",
        "/apps",
    ):
        assert marker in body, f"debug.html is missing {marker!r}"

    # No credential or token in a URL and no browser storage.
    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token=" not in body

    forms = FORM_TAG.findall(body)
    assert len(forms) == 1, forms
    for form in forms:
        assert METHOD_POST.search(form), f"the debug form must be method=post: {form}"
        action = ACTION.search(form)
        assert action is None, f"the debug form must not carry an action: {form}"

    for path in ("/debug", "/apps/", "/assets/"):
        response = t027_stack.server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"


def test_debug_js_is_the_served_asset(t027_stack) -> None:
    response = t027_stack.server.http.get("/assets/debug.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    for marker in (
        "function newIdempotencyKey(",
        "function debugPayloadFromFields(",
        "function submitDebugRequest(",
        "function fetchRun(",
        "function fetchDraftRevision(",
        "function shouldPollRun(",
        "function runSummary(",
        "X-Manifest-SHA256",
        "Idempotency-Key",
    ):
        assert marker in body, f"debug.js is missing {marker!r}"


def test_the_editor_and_the_apps_page_link_into_the_debug_entry(t027_stack) -> None:
    editor = t027_stack.server.http.get(f"/apps/{t027.new_uuid()}/edit")
    assert editor.status == 200, editor.text
    assert 'id="debug-entry"' in editor.text, "the editor has no debug entry"
    assert "调试入口" in editor.text

    editor_js = t027_stack.server.http.get("/assets/editor.js")
    assert editor_js.status == 200
    assert "'/debug'" in editor_js.text, "editor.js does not build the debug page path"
    assert "debug-entry" in editor_js.text

    apps = t027_stack.server.http.get("/apps")
    assert apps.status == 200
    assert "+ '/debug'" in apps.text, "apps.html does not link the debug page"


def test_the_real_page_serves_for_a_real_application(t027_stack) -> None:
    admin = t027.admin_session(t027_stack)
    creator = t027.invite_and_activate(
        t027_stack, admin, "t027-page-" + secrets.token_hex(4), roles=("creator",)
    )
    application = t027.create_application_or_fail(creator, "debug page app")
    page = t027_stack.server.http.get(f"/apps/{application['id']}/debug")
    assert page.status == 200
    assert "debug.html" not in page.text
    # The run panel starts hidden; nothing is rendered before a submission.
    assert 'id="run-panel" class="card" hidden' in page.text


def test_inline_scripts_and_debug_js_parse_with_node(t027_stack, node_binary, tmp_path) -> None:
    for page in ("debug.html", "editor.html", "apps.html"):
        body = (st.WEB_DIR / page).read_text(encoding="utf-8")
        for index, block in enumerate(INLINE_SCRIPT.findall(body)):
            script = tmp_path / f"{page}.{index}.js"
            script.write_text(block, encoding="utf-8")
            completed = subprocess.run(
                [node_binary, "--check", str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=60,
            )
            assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "debug.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


DEBUG_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const debugSource = fs.readFileSync(webDir + '/assets/debug.js', 'utf8');
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
  const context = {
    fetch: async (path, init) => {
      calls.push({ path, init });
      return route(path, init);
    },
    crypto: webcrypto,
    window: { location: { pathname: '/apps/11111111-1111-4111-8111-111111111111/debug', assign() {} } },
    console,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(debugSource, context, { filename: 'debug.js' });
  return { context, calls };
}

(async () => {
  // Path parsing is the only application-id source; query strings are never used.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(
      context.debugApplicationId('/apps/11111111-1111-4111-8111-111111111111/debug'),
      '11111111-1111-4111-8111-111111111111',
    );
    assert.strictEqual(
      context.debugApplicationId('/apps/11111111-1111-4111-8111-111111111111/debug/'),
      '11111111-1111-4111-8111-111111111111',
    );
    assert.strictEqual(context.debugApplicationId('/apps/not-an-id/debug'), null);
    assert.strictEqual(context.debugApplicationId('/apps/11111111-1111-4111-8111-111111111111/edit'), null);
    assert.strictEqual(context.debugRunsPath('abc'), '/api/v1/applications/abc/debug-runs');
    assert.strictEqual(context.debugRunPath('r1'), '/api/v1/runs/r1');
  }

  // A fresh idempotency key per attempt: UUID-shaped and never repeated.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const first = context.newIdempotencyKey();
    const second = context.newIdempotencyKey();
    assert.ok(typeof first === 'string' && first.length > 0);
    assert.notStrictEqual(first, second);
    assert.ok(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(first),
      'the key is not UUID v4 shaped: ' + first,
    );
  }

  // The POST body mirrors the contract; shape problems are located client-side.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const payload = context.debugPayloadFromFields({
      draftRevision: '3',
      inputText: '{"b":1,"a":2}',
      artifactIDsText:
        '11111111-1111-4111-8111-111111111111\n22222222-2222-4222-8222-222222222222,',
    });
    assert.strictEqual(JSON.stringify(payload), JSON.stringify({
      draft_revision: 3,
      input: { b: 1, a: 2 },
      artifact_ids: [
        '11111111-1111-4111-8111-111111111111',
        '22222222-2222-4222-8222-222222222222',
      ],
    }));
    assert.strictEqual(JSON.stringify(context.splitArtifactIDs('')), '[]');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '', inputText: '{}', artifactIDsText: '' }), (error) => error.code === 'draft_revision');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '0', inputText: '{}', artifactIDsText: '' }), (error) => error.code === 'draft_revision');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '2x', inputText: '{}', artifactIDsText: '' }), (error) => error.code === 'draft_revision');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '1', inputText: 'not json', artifactIDsText: '' }), (error) => error.code === 'input');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '1', inputText: '[1,2]', artifactIDsText: '' }), (error) => error.code === 'input');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '1', inputText: 'null', artifactIDsText: '' }), (error) => error.code === 'input');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '1', inputText: '{}', artifactIDsText: 'not-a-uuid' }), (error) => error.code === 'artifact_ids');
    const six = Array.from({ length: 6 }, (_, index) => '1111111' + index + '-1111-4111-8111-111111111111').join(' ');
    assert.throws(() => context.debugPayloadFromFields({ draftRevision: '1', inputText: '{}', artifactIDsText: six }), (error) => error.code === 'artifact_ids');
  }

  // Run projection, terminal detection and the polling stop rule.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const summary = context.runSummary({
      id: 'r1',
      state: 'queued',
      kind: 'debug',
      revision: 1,
      execution_snapshot_id: 's1',
    });
    assert.strictEqual(JSON.stringify(summary), JSON.stringify({
      id: 'r1',
      state: 'queued',
      kind: 'debug',
      revision: '1',
      executionSnapshotID: 's1',
    }));
    assert.strictEqual(context.runIsTerminal('queued'), false);
    assert.strictEqual(context.runIsTerminal('running'), false);
    assert.strictEqual(context.runIsTerminal('succeeded'), true);
    assert.strictEqual(context.runIsTerminal('failed'), true);
    assert.strictEqual(context.runIsTerminal('cancelled'), true);
    assert.strictEqual(context.shouldPollRun({ state: 'queued' }, 0, 3), true);
    assert.strictEqual(context.shouldPollRun({ state: 'queued' }, 3, 3), false);
    assert.strictEqual(context.shouldPollRun({ state: 'succeeded' }, 0, 3), false);
    assert.strictEqual(context.shouldPollRun(null, 0, 3), false);
  }

  // Submission: POST + CSRF + Idempotency-Key + the response-header hash.
  {
    const { context, calls } = load(async (path) => {
      if (path === '/api/v1/auth/me') {
        return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-debug' });
      }
      return jsonResponse(202, {
        id: 'run-1',
        state: 'queued',
        revision: 1,
        application_version_id: null,
        parent_run_id: null,
        output_artifact_id: null,
        error_code: null,
        kind: 'debug',
        execution_snapshot_id: 'snap-1',
        phase: 'idle',
      }, { 'X-Manifest-SHA256': 'a'.repeat(64) });
    });
    const payload = { draft_revision: 1, input: { text: 'hello' }, artifact_ids: [] };
    const submitted = await context.submitDebugRequest(
      '11111111-1111-4111-8111-111111111111',
      payload,
      'key-1',
    );
    assert.strictEqual(submitted.run.state, 'queued');
    assert.strictEqual(submitted.manifestSHA256, 'a'.repeat(64));
    const post = calls.find((call) => call.init.method === 'POST');
    assert.ok(post, 'no POST was sent');
    assert.strictEqual(post.path, '/api/v1/applications/11111111-1111-4111-8111-111111111111/debug-runs');
    assert.strictEqual(post.init.headers['Idempotency-Key'], 'key-1');
    assert.strictEqual(post.init.headers['X-CSRF-Token'], 'csrf-debug');
    assert.strictEqual(post.init.credentials, 'same-origin');
    assert.strictEqual(post.init.redirect, 'error');
    assert.strictEqual(post.init.body, JSON.stringify(payload));

    // No key means no request at all.
    let refused = null;
    try {
      await context.submitDebugRequest('11111111-1111-4111-8111-111111111111', payload, '');
    } catch (error) {
      refused = error;
    }
    assert.ok(refused && refused.code === 'idempotency_key');
  }

  // One poll is a plain GET of the run path, exposing the same header.
  {
    const { context, calls } = load(async (path) => {
      assert.strictEqual(path, '/api/v1/runs/run-1');
      return jsonResponse(200, { id: 'run-1', state: 'queued', kind: 'debug', revision: 1, execution_snapshot_id: 'snap-1' }, { 'X-Manifest-SHA256': 'b'.repeat(64) });
    });
    const polled = await context.fetchRun('run-1');
    assert.strictEqual(polled.run.state, 'queued');
    assert.strictEqual(polled.manifestSHA256, 'b'.repeat(64));
    assert.strictEqual(calls[0].init.method, 'GET');
    assert.strictEqual(calls[0].init.body, undefined);
  }

  // The current revision comes from the draft's strong ETag, never a guess.
  {
    const { context } = load(async (path) => {
      assert.strictEqual(path, '/api/v1/applications/11111111-1111-4111-8111-111111111111/draft');
      return jsonResponse(200, { definition: {}, layout: {}, capability_card: {} }, { ETag: '"7"' });
    });
    assert.strictEqual(await context.fetchDraftRevision('11111111-1111-4111-8111-111111111111'), 7);
    const missing = load(async () => jsonResponse(200, {}));
    assert.strictEqual(await missing.context.fetchDraftRevision('11111111-1111-4111-8111-111111111111'), null);
  }

  process.stdout.write('debug.js behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_debug_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "debug-harness.js"
    harness.write_text(
        DEBUG_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "debug.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "debug.js behaviour ok" in completed.stdout, completed.stdout
