"""T023: the creator editor page is a real client of the draft contract.

Two layers, both real:

* the page and the JavaScript asset are fetched from the running API over HTTP,
  and the structural assertions are about what the browser receives (form
  method, no query-string actions, no credential storage, conflict controls);
* the editor's request/validation logic is executed under Node with a stubbed
  ``fetch`` (like ``tests/f01/test_f01_app_js.py`` does for ``app.js``), so the
  If-Match header, the 412 branch and the payload shape are observed behaviour,
  not source text.

What is still not run: a DOM/browser driving the form. That limitation is the
same one F01 records and is repeated in the T023 report.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import f02a as f
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
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


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip(
            "node is not available on PATH; the editor logic cannot be executed "
            "(the structural page tests still run)"
        )
    return NODE


def test_the_editor_page_is_served_per_application(f02a_stack) -> None:
    response = f02a_stack.server.http.get(f"/apps/{f.new_uuid()}/edit")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="draft-form"',
        'method="post"',
        'id="draft-revision"',
        'id="editor-status"',
        'role="status"',
        'id="card-purpose"',
        'id="card-includes"',
        'id="card-excludes"',
        'id="card-required-inputs"',
        'id="card-expected-outputs"',
        'id="card-examples"',
        'id="draft-definition"',
        'id="draft-layout"',
        'id="draft-save"',
        'id="conflict-panel"',
        'id="conflict-reload"',
        'id="conflict-download"',
        'id="conflict-status"',
        "重载服务器版本",
        "保留本地副本",
        "If-Match",
        "412",
        "/assets/app.js",
        "/assets/editor.js",
        "/apps",
        "/api/v1/applications/{id}/draft",
    ):
        assert marker in body, f"editor.html is missing {marker!r}"

    # No credential or token in a URL and no browser storage.
    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token=" not in body

    forms = FORM_TAG.findall(body)
    assert len(forms) == 1, forms
    for form in forms:
        assert METHOD_POST.search(form), f"the editor form must be method=post: {form}"
        action = ACTION.search(form)
        assert action is None, f"the editor form must not carry an action: {form}"


def test_only_the_named_editor_paths_are_served(f02a_stack) -> None:
    for path in ("/editor", "/apps/", "/assets/"):
        response = f02a_stack.server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"


def test_editor_js_is_the_served_asset(f02a_stack) -> None:
    response = f02a_stack.server.http.get("/assets/editor.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    assert "function saveDraftRequest(" in body
    assert "function loadDraftRequest(" in body
    assert "'If-Match': etag" in body
    assert "isPreconditionConflict" in body
    assert "localCopyJSON" in body


def test_the_apps_page_links_into_the_editor_and_can_create(f02a_stack) -> None:
    response = f02a_stack.server.http.get("/apps")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        'id="create-app-form"',
        'id="create-app-name"',
        'id="create-status"',
        'method="post"',
        "api('/api/v1/applications'",
        "method: 'POST'",
        "'/apps/' + encodeURIComponent(application.id) + '/edit'",
        "编辑草稿",
    ):
        assert marker in body, f"apps.html is missing {marker!r}"

    # The T019 contract markers must survive the addition.
    for marker in ('id="apps-list"', 'id="apps-empty"', 'id="apps-next"', "active_version_id"):
        assert marker in body, f"apps.html lost {marker!r}"

    forms = FORM_TAG.findall(body)
    for form in forms:
        assert METHOD_POST.search(form), f"apps.html serves a form without method=post: {form}"
        action = ACTION.search(form)
        if action is not None:
            assert "?" not in action.group(1), form
    assert "location.search" not in body
    assert "localStorage" not in body


def test_the_real_creator_path_serves_the_page_and_refreshes_the_saved_draft(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-page-owner")
    application = f.create_application_or_fail(owner, "page flow")

    page = f02a_stack.server.http.get(f"/apps/{application['id']}/edit")
    assert page.status == 200
    assert "editor.html" not in page.text

    draft = f.get_draft(owner, application["id"])
    assert draft.status == 200 and draft.header("ETag") == '"1"'
    saved = f.save_draft(
        owner, application["id"], draft.header("ETag"),
        definition={"dsl_version": "1.0", "nodes": []},
        layout={"viewport": {"x": 5, "y": 6, "zoom": 1}},
        capability_card=f.full_card(),
    )
    assert saved.status == 200, saved.text
    refreshed = f.get_draft(owner, application["id"])
    assert refreshed.status == 200
    assert refreshed.header("ETag") == '"2"'
    assert refreshed.json["layout"] == {"viewport": {"x": 5, "y": 6, "zoom": 1}}


def test_inline_scripts_and_editor_js_parse_with_node(f02a_stack, node_binary, tmp_path) -> None:
    for page in ("apps.html", "editor.html"):
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
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "editor.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


EDITOR_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = %s;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const editorSource = fs.readFileSync(webDir + '/assets/editor.js', 'utf8');

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
    window: { location: { pathname: '/apps/11111111-1111-4111-8111-111111111111/edit', assign() {} } },
    console,
  };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(editorSource, context, { filename: 'editor.js' });
  return { context, calls };
}

(async () => {
  // Path parsing is the only application-id source; query strings are never used.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(
      context.editorApplicationId('/apps/11111111-1111-4111-8111-111111111111/edit'),
      '11111111-1111-4111-8111-111111111111',
    );
    assert.strictEqual(context.editorApplicationId('/apps/11111111-1111-4111-8111-111111111111/edit/'), '11111111-1111-4111-8111-111111111111');
    assert.strictEqual(context.editorApplicationId('/apps/not-an-id/edit'), null);
    assert.strictEqual(context.editorApplicationId('/editor'), null);
  }

  // An untouched card saves as {}; a touched card is the full six-key shape.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(JSON.stringify(context.capabilityCardFromFields({})), '{}');
    assert.strictEqual(JSON.stringify(context.capabilityCardFromFields({ purpose: '  ', includes: '\n\n' })), '{}');
    const card = context.capabilityCardFromFields({
      purpose: ' summarise ',
      includes: 'one\n two \n\n',
      excludes: '',
      required_inputs: 'csv',
      expected_outputs: 'report',
      examples: 'a\nb',
    });
    assert.strictEqual(JSON.stringify(card), JSON.stringify({
      purpose: 'summarise',
      includes: ['one', 'two'],
      excludes: [],
      required_inputs: ['csv'],
      expected_outputs: ['report'],
      examples: ['a', 'b'],
    }));
  }

  // The PUT body mirrors the contract; invalid JSON and a short example list
  // are refused client-side with a located reason.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const payload = context.draftPayloadFromFields({
      definition: '{"dsl_version":"1.0","nodes":[]}',
      layout: '{}',
      capabilityCard: {},
    });
    assert.strictEqual(JSON.stringify(payload), JSON.stringify({
      definition: { dsl_version: '1.0', nodes: [] },
      layout: {},
      capability_card: {},
    }));
    assert.throws(() => context.draftPayloadFromFields({ definition: 'not json', layout: '{}', capabilityCard: {} }), (error) => error.code === 'invalid_json');
    assert.throws(
      () => context.draftPayloadFromFields({ definition: '{}', layout: '{}', capabilityCard: { purpose: 'p', examples: 'one' } }),
      (error) => error.code === 'capability_card.examples',
    );
    assert.strictEqual(context.revisionFromETag('"7"'), 7);
    assert.strictEqual(context.revisionFromETag('W/"7"'), null);
  }

  // GET returns the draft and the strong ETag a save must echo.
  {
    const { context, calls } = load(async (path) => {
      assert.strictEqual(path, '/api/v1/applications/11111111-1111-4111-8111-111111111111/draft');
      return jsonResponse(200, { definition: {}, layout: {}, capability_card: {} }, { ETag: '"3"' });
    });
    const loaded = await context.loadDraftRequest('11111111-1111-4111-8111-111111111111');
    assert.strictEqual(loaded.etag, '"3"');
    assert.strictEqual(JSON.stringify(loaded.draft), JSON.stringify({ definition: {}, layout: {}, capability_card: {} }));
    assert.strictEqual(calls.length, 1);
  }

  // A save sends PUT + If-Match + CSRF and returns the new ETag.
  {
    const { context, calls } = load(async (path) => {
      if (path === '/api/v1/auth/me') {
        return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-1' });
      }
      assert.strictEqual(path, '/api/v1/applications/11111111-1111-4111-8111-111111111111/draft');
      return jsonResponse(200, { id: 'app', state: 'saved' }, { ETag: '"4"' });
    });
    const result = await context.saveDraftRequest(
      '11111111-1111-4111-8111-111111111111',
      '"3"',
      { definition: {}, layout: {}, capability_card: {} },
    );
    assert.strictEqual(result.headers.get('ETag'), '"4"');
    const put = calls.find((call) => call.init.method === 'PUT');
    assert.ok(put, 'no PUT was sent');
    assert.strictEqual(put.init.headers['If-Match'], '"3"');
    assert.strictEqual(put.init.headers['X-CSRF-Token'], 'csrf-1');
    assert.strictEqual(put.init.credentials, 'same-origin');
    assert.strictEqual(put.init.redirect, 'error');
    assert.strictEqual(put.init.body, JSON.stringify({ definition: {}, layout: {}, capability_card: {} }));
  }

  // 412 is a conflict, carries the current revision, and is never retried.
  {
    const { context, calls } = load(async (path) => {
      if (path === '/api/v1/auth/me') {
        return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-2' });
      }
      return jsonResponse(412, {
        error: {
          code: 'precondition_failed',
          message: 'the draft has moved on',
          request_id: 'req-412',
          details: { current_revision: 9, current_etag: '"9"' },
        },
      });
    });
    try {
      await context.saveDraftRequest('11111111-1111-4111-8111-111111111111', '"8"', { definition: {}, layout: {}, capability_card: {} });
      throw new Error('the 412 save resolved');
    } catch (error) {
      assert.strictEqual(context.isPreconditionConflict(error), true);
      assert.strictEqual(error.status, 412);
      assert.strictEqual(error.details.current_revision, 9);
      assert.strictEqual(error.details.current_etag, '"9"');
    }
    const puts = calls.filter((call) => call.init.method === 'PUT');
    assert.strictEqual(puts.length, 1, 'a conflicted save was retried automatically');
  }

  // The local copy is the exact payload, pretty-printed.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    assert.strictEqual(
      context.localCopyJSON({ definition: {}, layout: {}, capability_card: {} }),
      '{\n  "definition": {},\n  "layout": {},\n  "capability_card": {}\n}',
    );
  }

  process.stdout.write('editor.js behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_editor_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "editor-harness.js"
    harness.write_text(EDITOR_HARNESS % json.dumps(str(st.WEB_DIR)), encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "editor.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "editor.js behaviour ok" in completed.stdout, completed.stdout
