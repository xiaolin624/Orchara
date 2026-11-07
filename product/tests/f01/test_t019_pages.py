"""T019 acceptance 6: the pages are real clients of the contract endpoints.

Like the T015/T017 page tests, these assertions are behavioural for a page
without a browser: the served HTML must contain the contract paths, the form
fields, the visible status elements and the 401 redirect, and must not put a
credential in a URL or in browser storage. JavaScript execution itself is not
run here (recorded as a limitation in the report).
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from conftest import (
    api_server,  # noqa: F401 - pytest fixture
    assert_no_token_in_url_or_storage,
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
)

NODE = shutil.which("node")

# Executes the *served* app.js bytes (not a source literal) in a minimal DOM
# shim; the only surface handleAuthFailure touches is location.assign and
# document.querySelectorAll('[data-auth-content]').
APP_JS_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

function region(id) {
  return {
    id: id,
    hidden: false,
    value: 'stale-secret-value',
    children: [{ tagName: 'LI' }],
    replaceChildren: function () { this.children = []; },
  };
}

const regions = [region('apps-list'), region('identity-region')];
const navigations = [];
const document = {
  body: { getAttribute: function () { return 'required'; } },
  getElementById: function (id) {
    return { id: id, addEventListener: function () {}, hidden: false, value: '' };
  },
  querySelectorAll: function (selector) {
    return selector === '[data-auth-content]' ? regions : [];
  },
  addEventListener: function () {},
  createElement: function () { return { addEventListener: function () {} }; },
};
const context = {
  console: console,
  document: document,
  URLSearchParams: URLSearchParams,
  setTimeout: function () { return 0; },
  clearTimeout: function () {},
  crypto: { randomUUID: function () { return 'f01-idempotency-key'; } },
  confirm: function () { return true; },
  fetch: async function () { throw new Error('fetch is not used by this scenario'); },
  location: {
    pathname: '/apps',
    search: '',
    assign: function (url) { navigations.push(String(url)); },
  },
};
context.window = context;
context.globalThis = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context, { filename: 'app.js' });

const failure = { status: 401, code: 'session_revoked', message: 'the session was revoked' };
assert.strictEqual(context.handleAuthFailure(failure), true, 'a 401 is an auth failure');
assert.deepStrictEqual(navigations, ['/login?error=session_revoked'],
  'a 401 must navigate to /login with the stable reason: ' + JSON.stringify(navigations));
for (const item of regions) {
  assert.strictEqual(item.children.length, 0, 'the authenticated rows must be cleared');
  assert.strictEqual(item.value, '', 'one-time values must be cleared');
  assert.strictEqual(item.hidden, true, 'the authenticated region must be hidden');
}

// Multi-page flows: the shared apiFull exit and a second direct call must not
// navigate a second time on the same page.
context.handleApiAuthFailure('/api/v1/applications', failure);
assert.strictEqual(context.handleAuthFailure(failure), true);
assert.deepStrictEqual(navigations, ['/login?error=session_revoked'],
  'the page must navigate at most once: ' + JSON.stringify(navigations));

// Disabled-principal 403 is also terminal and carries its own reason.
assert.strictEqual(
  context.handleAuthFailure({ status: 403, code: 'principal_disabled' }),
  true,
);
assert.deepStrictEqual(navigations, ['/login?error=session_revoked'],
  'a second auth failure must not navigate again: ' + JSON.stringify(navigations));
process.stdout.write('app.js 401 behaviour ok\n');
"""


@pytest.fixture
def pages_server(fresh_database, api_server):
    # The pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def test_the_apps_page_lists_the_visible_applications(pages_server) -> None:
    response = pages_server.http.get("/apps")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="apps-list"',
        'id="apps-empty"',
        'id="apps-next"',
        'id="apps-status"',
        'role="status"',
        "/assets/app.js",
        "/api/v1/applications",
        "next_cursor",
        "active_version_id",
        "下一页",
        "当前没有可见的应用",
        "requireLogin(",
        # T095 centralised the session-failure branch in app.js: every page now
        # calls the shared helper instead of repeating `error.status === 401` and
        # its redirect. The helper is executed from the served bytes in
        # test_the_served_app_js_navigates_once_and_clears_auth_content below;
        # source literals are not treated as behavioural evidence.
        "handleAuthFailure(error)",
    ):
        # The identity read is via requireLogin(); the page never renders an
        # application before that succeeds.
        assert marker in body, f"apps.html is missing {marker!r}"

    app_js = pages_server.http.get("/assets/app.js")
    assert app_js.status == 200, app_js.text
    assert "error.status === 401" in app_js.text
    assert "window.location.assign(loginURLFor(error))" in app_js.text
    assert "[data-auth-content]" in app_js.text

    # The ACL wording is honest about default deny and about admin not being a
    # content reader.
    assert "管理员" in body
    assert "只显示" in body
    assert_no_token_in_url_or_storage(body, "apps.html")


def test_the_served_app_js_navigates_once_and_clears_auth_content(
    pages_server, tmp_path
) -> None:
    """Execute the served app.js bytes: 401 -> /login, content cleared, one nav.

    This replaces the previous source-literal assertions (``error.status === 401``
    and ``[data-auth-content]``): a dead constant containing those strings would
    have passed. No Playwright/Chromium exists here; Node with a minimal DOM shim
    is the established page-behaviour method in this repository (see
    tests/f12/test_t095_page_roles.py).
    """
    if NODE is None:
        pytest.skip("node is not available on PATH; the served app.js cannot be executed")
    response = pages_server.http.get("/assets/app.js")
    assert response.status == 200, response.text
    served = tmp_path / "served-app.js"
    served.write_text(response.text, encoding="utf-8")
    harness = tmp_path / "f01-app-js-401.js"
    harness.write_text(APP_JS_HARNESS, encoding="utf-8")
    completed = subprocess.run(
        [NODE, str(harness), str(served)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "app.js 401 behaviour ok" in completed.stdout, completed.stdout + completed.stderr


def test_only_the_named_apps_path_is_served(pages_server) -> None:
    for path in ("/apps.html", "/apps/"):
        response = pages_server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"


def test_the_admin_page_has_the_grant_revoke_and_role_forms(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    body = response.text

    for marker in (
        'id="grants-panel"',
        'id="grant-form"',
        'id="grant-principal"',
        'id="grant-resource"',
        'id="grant-action"',
        'id="grant-action" name="action"',
        'value="read"',
        'id="grant-expires"',
        'id="grant-status"',
        'id="grant-result"',
        'id="grant-id"',
        'id="revoke-form"',
        'id="revoke-grant-id"',
        'id="revoke-status"',
        'id="roles-panel"',
        'id="roles-form"',
        'id="roles-principal"',
        'id="roles-status"',
        "/api/v1/admin/grants",
        "/api/v1/admin/users/",
        "/roles",
        "resource_type: 'application'",
        "method: 'DELETE'",
        "method: 'PUT'",
        "csrf: await csrf()",
        "error.status === 401",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # The visible feedback the ticket asks for: ok / bad feedback on every write.
    for marker in ("setStatus(grantStatus", "setStatus(revokeStatus", "setStatus(rolesStatus"):
        assert marker in body, f"admin.html has no feedback for {marker!r}"

    # T015/T017 behaviour must survive the additions.
    for marker in (
        'id="invite-form"',
        'id="auth-status"',
        'id="identity-principal"',
        "只显示一次",
    ):
        assert marker in body, f"admin.html lost {marker!r}"

    assert_no_token_in_url_or_storage(body, "admin.html")
