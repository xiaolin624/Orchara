"""T095 (GitHub #96): page behaviour, executed as the committed bytes.

Means of verification (stated in the report's first paragraph as well): there is
no Playwright/Chromium in this environment. These tests execute the **real
committed JavaScript bytes** of ``assets/app.js``, ``assets/nav.js`` and the
page's own inline ``<script>`` (extracted from the committed HTML) inside Node
with a minimal DOM/fetch shim. The shim implements only the DOM surface the
pages use; it proves request shapes, rendered navigation, and failure handling,
not browser layout, CSS, or real event bubbling.
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


HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const webDir = __WEB_DIR__;

// ---------------------------------------------------------------------------
// minimal DOM: only the surface the pages under test actually use
// ---------------------------------------------------------------------------
function camel(name) {
  return String(name).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
}

class El {
  constructor(tag, id, attributes) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this._text = '';
    this.hidden = false;
    this.className = '';
    this.value = '';
    this.checked = false;
    this.type = '';
    this.href = '';
    this.disabled = false;
    this.dataset = {};
    this.attributes = {};
    this.name = '';
    const attrs = attributes || {};
    for (const key of Object.keys(attrs)) {
      this.setAttribute(key, attrs[key]);
    }
  }

  get textContent() {
    if (this.children.length) {
      return this.children.map(function (child) {
        return child && child.textContent !== undefined ? child.textContent : '';
      }).join('');
    }
    return this._text;
  }

  set textContent(value) {
    this._text = String(value === undefined || value === null ? '' : value);
    this.children = [];
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  append() {
    for (let index = 0; index < arguments.length; index += 1) {
      const node = arguments[index];
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }

  replaceChildren() {
    this.children = [];
    this._text = '';
    for (let index = 0; index < arguments.length; index += 1) {
      const node = arguments[index];
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }

  removeChild(child) {
    this.children = this.children.filter(function (candidate) { return candidate !== child; });
  }

  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }

  dispatch(type, event) {
    const payload = event || { preventDefault: function () {} };
    if (typeof payload.preventDefault !== 'function') {
      payload.preventDefault = function () {};
    }
    for (const fn of this._listeners[type] || []) {
      fn(payload);
    }
  }

  click() { this.dispatch('click'); }
  submit() { this.dispatch('submit'); }

  setAttribute(name, value) {
    this.attributes[String(name)] = String(value);
    if (name === 'hidden') this.hidden = true;
    if (String(name).startsWith('data-')) this.dataset[camel(String(name).slice(5))] = String(value);
  }

  getAttribute(name) {
    if (name === 'hidden') return this.hidden ? '' : null;
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    return null;
  }

  hasAttribute(name) { return this.getAttribute(name) !== null; }

  descendants() {
    const out = [];
    const walk = (node) => {
      for (const child of node.children || []) {
        if (!child || !child.children) continue;
        out.push(child);
        walk(child);
      }
    };
    walk(this);
    return out;
  }
}

function matches(el, selector) {
  let text = String(selector || '').trim();
  if (!text || !el || !el.tagName) return false;
  if (text.endsWith(':checked')) {
    text = text.slice(0, -':checked'.length);
    if (!el.checked) return false;
  }
  const attribute = text.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
  if (attribute) {
    const value = el.getAttribute(attribute[1]);
    if (value === null) return false;
    if (attribute[2] !== undefined && value !== attribute[2]) return false;
    return true;
  }
  if (text.startsWith('#')) return el.id === text.slice(1);
  if (text.startsWith('.')) return String(el.className).split(/\s+/).indexOf(text.slice(1)) !== -1;
  return el.tagName === text.toUpperCase();
}

function createPage(options) {
  const opts = options || {};
  const registry = [];
  const byId = new Map();

  function register(element) {
    registry.push(element);
    if (element.id) byId.set(element.id, element);
    return element;
  }

  function make(tag, id, attributes) {
    return register(new El(tag, id, attributes));
  }

  const body = make('body');
  if (opts.auth) body.setAttribute('data-auth', opts.auth);

  function queryAll(selector) {
    const parts = String(selector || '').split(',').map(function (part) { return part.trim(); });
    return registry.filter(function (element) {
      return parts.some(function (part) { return matches(element, part); });
    });
  }

  const document = {
    body: body,
    getElementById: function (id) { return byId.get(id) || make('div', id); },
    createElement: function (tag) { return make(tag); },
    createTextNode: function (text) { return { nodeType: 3, textContent: String(text) }; },
    querySelector: function (selector) { return queryAll(selector)[0] || null; },
    querySelectorAll: queryAll,
    addEventListener: function () {},
  };

  const navigations = [];
  const requests = [];
  const timers = [];
  const location = {
    pathname: opts.pathname || '/',
    search: opts.search || '',
    assign: function (url) { navigations.push(String(url)); },
  };
  // A classic script's globals *are* window in a browser. Mirror that here so
  // `window.OrcharaNav` and a bare `OrcharaNav` resolve to the same object, the
  // way the committed page code sees them.
  const context = {
    console: console,
    location: location,
    document: document,
    URLSearchParams: URLSearchParams,
    setTimeout: function (fn) { timers.push(fn); return timers.length; },
    clearTimeout: function () {},
    crypto: { randomUUID: function () { return 't095-idempotency-key'; } },
    confirm: function () { return true; },
    fetch: async function (requestPath, init) {
      requests.push({ path: String(requestPath), init: init || {} });
      assert.strictEqual(typeof opts.fetch, 'function', 'the scenario did not install fetch');
      return opts.fetch(String(requestPath), init || {});
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);

  const page = {
    context: context,
    window: context,
    document: document,
    body: body,
    byId: byId,
    make: make,
    navigations: navigations,
    requests: requests,
    timers: timers,
    runFile: function (asset) {
      vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', asset), 'utf8'), context);
    },
    runSource: function (source, label) {
      vm.runInContext(source, context, { filename: label || 'inline.js' });
    },
    runInline: function (htmlFile) {
      const html = fs.readFileSync(path.join(webDir, htmlFile), 'utf8');
      const pattern = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
      const blocks = [];
      let match;
      while ((match = pattern.exec(html)) !== null) blocks.push(match[1]);
      assert.ok(blocks.length > 0, 'no inline script in ' + htmlFile);
      page.runSource(blocks[blocks.length - 1], htmlFile);
    },
    nav: function () { return document.querySelector('nav'); },
    navLinks: function () {
      const nav = page.nav();
      if (!nav) return [];
      return nav.children
        .filter(function (child) { return child && child.tagName === 'A'; })
        .map(function (anchor) { return { label: anchor.textContent, href: anchor.href }; });
    },
    navLabels: function () { return page.navLinks().map(function (link) { return link.label; }); },
    navLinksTo: function (href) {
      return page.navLinks().filter(function (link) { return link.href === href; });
    },
  };
  return page;
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return null; } },
    text: async function () { return JSON.stringify(body); },
  };
}

function identityPayload(roles, kind) {
  return {
    principal_id: '11111111-1111-4111-8111-111111111111',
    kind: kind || 'user',
    roles: roles || ['user'],
    csrf_token: 'csrf-t095',
    expires_at: '2099-01-01T00:00:00Z',
  };
}

function errorResponse(status, code, message) {
  return jsonResponse(status, {
    error: { code: code, message: message || code, request_id: 'req-t095' },
  });
}

async function flush(rounds) {
  for (let index = 0; index < (rounds || 40); index += 1) {
    await new Promise(function (resolve) { setImmediate(resolve); });
  }
}

function appFixture(id, name) {
  return { id: id, name: name || 'sales report', active_version_id: null };
}

const APP_ID = '22222222-2222-4222-8222-222222222222';

// The real apps.html role-gated controls and ids. The page under test is still
// the committed inline script; only the document surface is shimmed, exactly as
// the previous page tests in tests/f08 do.
function buildAppsPage(fetchHandler, options) {
  const settings = options || {};
  const page = createPage({
    pathname: '/apps',
    auth: settings.auth || 'required',
    fetch: fetchHandler,
  });
  page.make('nav', 'main-nav');
  const createCard = page.make('section', 'create-app-card', { 'data-requires-role': 'creator' });
  createCard.hidden = true;
  const templateCard = page.make('section', 'template-card', { 'data-requires-role': 'creator' });
  templateCard.hidden = true;
  const appsList = page.make('ul', 'apps-list', { 'data-auth-content': '' });
  for (const id of [
    'apps-status', 'apps-empty', 'apps-next', 'create-app-form', 'create-app-name',
    'create-status', 'create-from-template', 'template-status',
  ]) {
    page.make('div', id);
  }
  page.runFile('app.js');
  page.runFile('nav.js');
  page.runFile('versions.js');
  page.runInline('apps.html');
  return { page: page, createCard: createCard, templateCard: templateCard, appsList: appsList };
}

function appLinks(item) {
  return item.children
    .filter(function (child) { return child && child.tagName === 'A'; })
    .map(function (anchor) { return { label: anchor.textContent, href: anchor.href }; });
}

function assertNavHas(page, label) {
  assert.ok(
    page.navLabels().indexOf(label) !== -1,
    'navigation is missing "' + label + '": ' + JSON.stringify(page.navLabels()),
  );
}

function assertNavMissing(page, label) {
  assert.ok(
    page.navLabels().indexOf(label) === -1,
    'navigation must not show "' + label + '": ' + JSON.stringify(page.navLabels()),
  );
}

async function main() {
__SCENARIO__
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def run_harness(node_binary: str, tmp_path, name: str, scenario: str) -> subprocess.CompletedProcess:
    script = HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))).replace(
        "__SCENARIO__", scenario
    )
    harness = tmp_path / (name + ".js")
    harness.write_text(script, encoding="utf-8")
    return subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def assert_passed(completed: subprocess.CompletedProcess, marker: str) -> None:
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert marker in completed.stdout, completed.stdout + completed.stderr


def test_navigation_is_rendered_per_role_and_the_page_re_reads_identity_on_refresh(
    node_binary: str, tmp_path
) -> None:
    """AC1 + AC3: different roles get different navigation; a refresh re-reads /auth/me."""
    scenario = r"""
  // Load 1: an administrator. The same committed bytes must render the admin
  // entry, and the "creation" label because this administrator is also a creator.
  const adminPage = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return jsonResponse(200, identityPayload(['admin', 'user', 'creator']));
    }
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  }).page;
  await flush();
  assertNavHas(adminPage, '管理台');
  assertNavHas(adminPage, '应用与创作');
  assertNavHas(adminPage, '个人数据');
  assertNavHas(adminPage, '退出');
  assert.strictEqual(
    adminPage.requests.filter(function (request) {
      return request.path === '/api/v1/auth/me';
    }).length,
    1,
    'one page load must read /auth/me exactly once',
  );
  assert.deepStrictEqual(adminPage.navigations, [], 'a valid identity must not navigate away');

  // A plain reader: same bytes, no admin entry, and the creation affordance is
  // labelled as read-only instead of offering a form the server will refuse.
  const userPage = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  }).page;
  await flush();
  assertNavMissing(userPage, '管理台');
  assertNavHas(userPage, '应用（只读）');
  assertNavMissing(userPage, '应用与创作');
  assertNavHas(userPage, '个人数据');

  // Load 2 (a refresh): a *new* page context with a new /auth/me answer. The
  // navigation must follow the fresh answer, not a remembered role.
  const refreshed = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  }).page;
  await flush();
  assertNavMissing(refreshed, '管理台');
  assert.strictEqual(
    refreshed.requests.filter(function (request) {
      return request.path === '/api/v1/auth/me';
    }).length,
    1,
    'a refresh must re-read /auth/me (exactly once) instead of reusing a snapshot',
  );

  // A service principal is not a human: the personal-data and catalog entries
  // are not part of its navigation, because the server refuses them.
  const servicePage = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return jsonResponse(200, identityPayload([], 'service'));
    }
    return jsonResponse(200, { items: [], next_cursor: null });
  }).page;
  await flush();
  assertNavMissing(servicePage, '个人数据');
  assertNavMissing(servicePage, '应用（只读）');
  assertNavMissing(servicePage, '管理台');
  assertNavHas(servicePage, '状态');
  process.stdout.write('nav roles ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-nav-roles", scenario)
    assert_passed(completed, "nav roles ok")


def test_a_401_or_disabled_principal_lands_on_login_and_never_keeps_stale_rows(
    node_binary: str, tmp_path
) -> None:
    """AC3: 401 and 403 principal_disabled clear the old list and go to /login with a reason."""
    scenario = r"""
  // 401: the session is gone. The page must navigate once, to /login, and the
  // stale row the previous page load had rendered must not survive.
  const expired = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return errorResponse(401, 'unauthenticated', 'a session cookie is required');
    }
    return jsonResponse(200, { items: [appFixture(APP_ID)], next_cursor: null });
  }).page;
  expired.byId.get('apps-list').appendChild(expired.make('li', 'stale-row'));
  await flush();
  assert.deepStrictEqual(expired.navigations, ['/login'],
    'a 401 must navigate to /login exactly once: ' + JSON.stringify(expired.navigations));
  assert.strictEqual(expired.byId.get('apps-list').children.length, 0,
    'stale application rows must be cleared when the session is rejected');
  assert.ok(expired.byId.get('apps-status').className.indexOf('bad') !== -1,
    'the page must show a failure state, not a success state');

  // 401 session_revoked: the stable code the server actually returns after a
  // disable (the session is revoked before the principal state is reported).
  // The login page needs that reason to explain the state.
  const revoked = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return errorResponse(401, 'session_revoked', 'the session was revoked');
    }
    return jsonResponse(200, { items: [appFixture(APP_ID)], next_cursor: null });
  }).page;
  await flush();
  assert.deepStrictEqual(revoked.navigations, ['/login?error=session_revoked'],
    'a revoked session must reach /login with the stable code: ' +
      JSON.stringify(revoked.navigations));
  assert.strictEqual(revoked.byId.get('apps-list').children.length, 0,
    'stale rows must be cleared when the session was revoked');

  // 403 principal_disabled: the account is disabled. Not a 401, and the login
  // page needs the reason so it can explain the account state.
  const disabled = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return errorResponse(403, 'principal_disabled', 'this account is disabled');
    }
    return jsonResponse(200, { items: [appFixture(APP_ID)], next_cursor: null });
  }).page;
  disabled.byId.get('apps-list').appendChild(disabled.make('li', 'stale-row'));
  await flush();
  assert.deepStrictEqual(disabled.navigations, ['/login?error=principal_disabled'],
    'a disabled principal must reach /login with the reason: ' + JSON.stringify(disabled.navigations));
  assert.strictEqual(disabled.byId.get('apps-list').children.length, 0,
    'stale rows must be cleared when the principal is disabled');

  // A data request rejected after a successful identity read: the identity was
  // valid a moment ago, the list had real rows, and the next read is refused.
  // The page must not keep showing the rows or pretend the read succeeded.
  let disabledNow = false;
  const midSession = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      if (disabledNow) return errorResponse(403, 'principal_disabled', 'this account is disabled');
      return jsonResponse(200, { items: [appFixture(APP_ID)], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  }).page;
  await flush();
  assert.strictEqual(midSession.byId.get('apps-list').children.length, 1,
    'the first page load should have rendered the visible application');
  disabledNow = true;
  await midSession.context.loadPage(null);
  await flush();
  assert.strictEqual(midSession.byId.get('apps-list').children.length, 0,
    'the refused read must clear the previously rendered rows');
  assert.deepStrictEqual(midSession.navigations, ['/login?error=principal_disabled'],
    'the refused read must navigate once with the disabled reason: ' +
      JSON.stringify(midSession.navigations));
  process.stdout.write('auth failure ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-auth-failure", scenario)
    assert_passed(completed, "auth failure ok")


def test_a_non_creator_sees_no_creator_entry_and_no_dead_click_link(
    node_binary: str, tmp_path
) -> None:
    """AC1 reverse: entries the server will refuse are not rendered as clickable."""
    scenario = r"""
  const reader = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [appFixture(APP_ID, 'shared sales report')], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();
  assert.strictEqual(reader.createCard.hidden, true,
    'the create form must not be offered to a caller without the creator role');
  assert.strictEqual(reader.templateCard.hidden, true,
    'the template-create button must not be offered to a caller without the creator role');
  const readerRows = reader.appsList.children;
  assert.strictEqual(readerRows.length, 1, 'the readable application should still be listed');
  const readerLinks = appLinks(readerRows[0]);
  const readerHrefs = readerLinks.map(function (link) { return link.href; });
  assert.ok(readerHrefs.every(function (href) { return !/\/edit$/.test(href); }),
    'a read-only caller must not get an edit link: ' + JSON.stringify(readerHrefs));
  assert.ok(readerHrefs.every(function (href) { return !/\/debug$/.test(href); }),
    'a read-only caller must not get a debug link: ' + JSON.stringify(readerHrefs));
  assert.ok(readerHrefs.some(function (href) { return /\/versions$/.test(href); }),
    'the readable versions link must remain: ' + JSON.stringify(readerHrefs));

  const creator = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') {
      return jsonResponse(200, identityPayload(['user', 'creator']));
    }
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [appFixture(APP_ID)], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();
  assert.strictEqual(creator.createCard.hidden, false,
    'a creator must see the create form');
  const creatorLinks = appLinks(creator.appsList.children[0]).map(function (link) { return link.href; });
  assert.ok(creatorLinks.some(function (href) { return /\/edit$/.test(href); }), JSON.stringify(creatorLinks));
  assert.ok(creatorLinks.some(function (href) { return /\/debug$/.test(href); }), JSON.stringify(creatorLinks));
  process.stdout.write('roles gated ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-gated-entries", scenario)
    assert_passed(completed, "roles gated ok")


def test_a_refused_creator_action_converges_the_page_to_the_current_identity(
    node_binary: str, tmp_path
) -> None:
    """AC3: after a role revocation the next refusal makes the page stop advertising creation."""
    scenario = r"""
  let identityReads = 0;
  const page = buildAppsPage(async function (requestPath, init) {
    if (requestPath === '/api/v1/auth/me') {
      identityReads += 1;
      // The load saw a creator; after the server refuses the write, the fresh
      // identity read observes that the role has been taken away.
      return jsonResponse(200, identityPayload(identityReads === 1 ? ['user', 'creator'] : ['user']));
    }
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      const method = (init && init.method) || 'GET';
      if (method === 'POST') {
        return errorResponse(403, 'creator_role_required',
          'the creator role is required to create, validate or debug an application');
      }
      return jsonResponse(200, { items: [], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();
  const fixture = page;
  assert.strictEqual(fixture.createCard.hidden, false, 'the creator starts with the create form');
  assertNavHas(fixture.page, '应用与创作');

  fixture.page.byId.get('create-app-name').value = 't095 should fail';
  fixture.page.byId.get('create-app-form').submit();
  await flush();

  assert.deepStrictEqual(fixture.page.navigations, [],
    'a role refusal is not a session failure; the user stays on the page: ' +
      JSON.stringify(fixture.page.navigations));
  const status = fixture.page.byId.get('create-status');
  assert.ok(status.textContent.indexOf('creator_role_required') !== -1,
    'the refusal must be shown in the API terms: ' + status.textContent);
  assert.strictEqual(fixture.createCard.hidden, true,
    'after the role is gone the create form must stop being offered');
  assertNavHas(fixture.page, '应用（只读）');
  assertNavMissing(fixture.page, '应用与创作');
  assert.ok(identityReads >= 2, 'the refusal must trigger a fresh identity read');
  process.stdout.write('converged ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-converge", scenario)
    assert_passed(completed, "converged ok")


def test_the_logout_link_revokes_the_session_instead_of_just_linking_to_login(
    node_binary: str, tmp_path
) -> None:
    """AC1/AC3: a visible "logout" must really call logout, not be a decorative link."""
    scenario = r"""
  const fixture = buildAppsPage(async function (requestPath, init) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath === '/api/v1/auth/logout') {
      return jsonResponse(200, { state: 'logged_out' });
    }
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();
  const nav = fixture.page.nav();
  const logout = nav.children.filter(function (child) {
    return child && child.tagName === 'A' && /退出/.test(child.textContent);
  })[0];
  assert.ok(logout, 'the navigation must expose a logout control: ' + JSON.stringify(fixture.page.navLabels()));
  let prevented = false;
  nav.dispatch('click', {
    target: logout,
    preventDefault: function () { prevented = true; },
  });
  await flush();
  const logoutCalls = fixture.page.requests.filter(function (request) {
    return request.path === '/api/v1/auth/logout';
  });
  assert.strictEqual(logoutCalls.length, 1,
    'clicking logout must call POST /api/v1/auth/logout once: ' +
      JSON.stringify(fixture.page.requests.map(function (request) { return request.path; })));
  assert.strictEqual((logoutCalls[0].init && logoutCalls[0].init.method) || 'GET', 'POST');
  assert.ok(prevented, 'the logout anchor must prevent the plain navigation');
  assert.deepStrictEqual(fixture.page.navigations, ['/login'],
    'logout must navigate to /login after the server revokes the session: ' +
      JSON.stringify(fixture.page.navigations));
  process.stdout.write('logout ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-logout", scenario)
    assert_passed(completed, "logout ok")


def test_the_versions_page_follows_next_cursor_instead_of_silently_dropping_it(
    node_binary: str, tmp_path
) -> None:
    """AC2 (UI half): a list with ``next_cursor`` exposes a next page and uses it."""
    scenario = r"""
  const page = createPage({
    pathname: '/apps/' + APP_ID + '/versions',
    auth: 'required',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
      if (requestPath === '/api/v1/applications/' + APP_ID) {
        return jsonResponse(200, { id: APP_ID, name: 'app', active_version_id: null });
      }
      if (requestPath === '/api/v1/applications/' + APP_ID + '/versions') {
        return jsonResponse(200, {
          items: [{ id: 'v1', version_no: 1, manifest: {}, manifest_sha256: 'a' }],
          next_cursor: 'cursor-t095-1',
        });
      }
      if (requestPath === '/api/v1/applications/' + APP_ID + '/versions?cursor=cursor-t095-1') {
        return jsonResponse(200, {
          items: [{ id: 'v2', version_no: 2, manifest: {}, manifest_sha256: 'b' }],
          next_cursor: null,
        });
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  page.make('nav', 'main-nav');
  for (const id of [
    'versions-list', 'versions-status', 'application-availability', 'revoke-form',
    'revoke-version-id', 'revoke-reason', 'revoke-stop-existing', 'revoke-status', 'versions-next',
  ]) page.make('div', id);
  page.runFile('app.js');
  page.runFile('nav.js');
  page.runFile('versions.js');
  page.runInline('versions.html');
  await flush();
  assert.strictEqual(page.byId.get('versions-list').children.length, 1,
    'the first page of versions should be rendered');
  assert.strictEqual(page.byId.get('versions-next').hidden, false,
    'a non-null next_cursor must expose the next page control');
  page.byId.get('versions-next').click();
  await flush();
  assert.strictEqual(page.byId.get('versions-list').children.length, 2,
    'following next_cursor must append the next page instead of dropping it');
  assert.strictEqual(page.byId.get('versions-next').hidden, true,
    'the control must disappear when the server says there is no next page');
  const cursored = page.requests.filter(function (request) {
    return request.path.indexOf('cursor=cursor-t095-1') !== -1;
  });
  assert.strictEqual(cursored.length, 1, 'the next page must be requested with the cursor: ' +
    JSON.stringify(page.requests.map(function (request) { return request.path; })));
  process.stdout.write('versions paging ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-versions-paging", scenario)
    assert_passed(completed, "versions paging ok")


def test_the_models_page_follows_next_cursor_instead_of_only_announcing_it(
    node_binary: str, tmp_path
) -> None:
    """AC2 (UI half): models computed next_cursor before but rendered no control."""
    scenario = r"""
  const page = createPage({
    pathname: '/models',
    auth: 'required',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
      if (requestPath === '/api/v1/models?limit=50') {
        return jsonResponse(200, {
          items: [{ id: 'm1', name: 'model one', model_id: 'stub-1', validation_state: 'passed', data_destination: 'local' }],
          next_cursor: 'cursor-models-1',
        });
      }
      if (requestPath === '/api/v1/models?limit=50&cursor=cursor-models-1') {
        return jsonResponse(200, {
          items: [{ id: 'm2', name: 'model two', model_id: 'stub-2', validation_state: 'passed', data_destination: 'local' }],
          next_cursor: null,
        });
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  page.make('nav', 'main-nav');
  for (const id of [
    'register-card', 'register-form', 'register-status', 'models-status', 'models-list',
    'report-card', 'report-status', 'report-body', 'model-endpoint', 'model-credential',
    'model-destination', 'models-next',
  ]) page.make('div', id);
  page.runFile('app.js');
  page.runFile('nav.js');
  page.runFile('models.js');
  page.runInline('models.html');
  await flush();
  assert.strictEqual(page.byId.get('models-list').children.length, 1,
    'the first page of model versions should be rendered');
  assert.strictEqual(page.byId.get('models-next').hidden, false,
    'a non-null next_cursor must render the next page control');
  page.byId.get('models-next').click();
  await flush();
  assert.strictEqual(page.byId.get('models-list').children.length, 2,
    'the next page must be appended, not silently dropped');
  assert.strictEqual(page.byId.get('models-next').hidden, true,
    'the control must disappear on the last page');
  const cursored = page.requests.filter(function (request) {
    return request.path.indexOf('cursor=cursor-models-1') !== -1;
  });
  assert.strictEqual(cursored.length, 1, 'the next page must carry the cursor: ' +
    JSON.stringify(page.requests.map(function (request) { return request.path; })));
  process.stdout.write('models paging ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-models-paging", scenario)
    assert_passed(completed, "models paging ok")


def test_personal_data_follows_next_cursor_for_its_inventory(node_binary: str, tmp_path) -> None:
    """AC2 (UI half): the personal-data inventory must not stop at the first page."""
    scenario = r"""
  const page = createPage({
    pathname: '/personal-data',
    auth: 'required',
    fetch: async function () { throw new Error('collectAllPages should use api, not fetch'); },
  });
  page.runFile('personal-data.js');
  const seen = [];
  page.context.api = async function (requestPath) {
    seen.push(requestPath);
    if (requestPath === '/api/v1/memories') {
      return { items: [{ id: 'memory-1' }], next_cursor: 'cursor-memory-1' };
    }
    if (requestPath === '/api/v1/memories?cursor=cursor-memory-1') {
      return { items: [{ id: 'memory-2' }], next_cursor: null };
    }
    return { items: [], next_cursor: null };
  };
  const memories = await page.context.collectAllPages('/api/v1/memories');
  assert.strictEqual(
    JSON.stringify(memories.map(function (memory) { return memory.id; })),
    JSON.stringify(['memory-1', 'memory-2']),
    'the second page of the inventory was dropped: ' + JSON.stringify(memories),
  );
  assert.strictEqual(
    JSON.stringify(seen),
    JSON.stringify(['/api/v1/memories', '/api/v1/memories?cursor=cursor-memory-1']),
    'the cursor request shape changed: ' + JSON.stringify(seen),
  );
  process.stdout.write('personal data paging ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-personal-data-paging", scenario)
    assert_passed(completed, "personal data paging ok")


def test_every_page_loads_the_single_navigation_source() -> None:
    """The navigation is one module on every page, not 22 hand-maintained lists.

    This is a static seam (the file inventory), so it is deliberately cheap; the
    behaviour of the module itself is executed by the Node tests above.
    """
    web_dir = st.WEB_DIR
    pages = sorted(web_dir.glob("*.html"))
    assert len(pages) == 22, [page.name for page in pages]
    for page in pages:
        text = page.read_text(encoding="utf-8")
        assert '<script src="/assets/nav.js"></script>' in text, page.name
        assert "<nav" in text, page.name
        if page.name in {"login.html", "activate.html", "index.html"}:
            assert 'data-auth="optional"' in text, page.name
        else:
            assert 'data-auth="optional"' not in text, page.name

    apps = (web_dir / "apps.html").read_text(encoding="utf-8")
    # The two creator-only cards are rendered closed by default and opened only
    # for an identity that holds the creator role.
    assert 'data-requires-role="creator"' in apps
    assert 'data-auth-content' in apps
    # The editor/debug links are no longer unconditional markup: they are built
    # only for a creator, which the Node test observes from the real inline script.
    assert "const canWrite" in apps

    # Logout is rendered by the shared navigation, including the icon/label
    # children. Its actual session-revocation behavior is tested above.
    nav = (web_dir / "assets/nav.js").read_text(encoding="utf-8")
    assert "data-orchara-logout" in nav


def test_the_login_page_explains_a_disabled_principal_and_routes_by_role(
    node_binary: str, tmp_path
) -> None:
    """AC3: disabled identity is explained; human accounts enter the conversation workspace."""
    scenario = r"""
  // An already-revoked visitor is sent to /login?error=principal_disabled by the
  // page they were on. The login page must explain that specific state.
  const disabled = createPage({
    pathname: '/login',
    search: '?error=principal_disabled',
    auth: 'optional',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/me') {
        return errorResponse(401, 'unauthenticated', 'a session cookie is required');
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  for (const id of ['login-form', 'login', 'password', 'login-status']) disabled.make('div', id);
  disabled.make('nav', 'main-nav');
  disabled.runFile('app.js');
  disabled.runFile('nav.js');
  disabled.runInline('login.html');
  await flush();
  const reason = disabled.byId.get('login-status').textContent;
  assert.ok(reason.indexOf('已被禁用') !== -1,
    'the login page must explain principal_disabled: ' + reason);
  assertNavHas(disabled, '登录');
  assertNavMissing(disabled, '管理台');
  assert.deepStrictEqual(disabled.navigations, [],
    'an anonymous login page must not redirect to itself');

  // A revoked session redirects with its own stable code; the page must explain
  // that too, because the server cannot report "disabled" on this path.
  const revokedPage = createPage({
    pathname: '/login',
    search: '?error=session_revoked',
    auth: 'optional',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/me') {
        return errorResponse(401, 'unauthenticated', 'a session cookie is required');
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  for (const id of ['login-form', 'login', 'password', 'login-status']) revokedPage.make('div', id);
  revokedPage.make('nav', 'main-nav');
  revokedPage.runFile('app.js');
  revokedPage.runFile('nav.js');
  revokedPage.runInline('login.html');
  await flush();
  assert.ok(revokedPage.byId.get('login-status').textContent.indexOf('已被撤销') !== -1,
    'the login page must explain session_revoked: ' +
      revokedPage.byId.get('login-status').textContent);

  // A human sign-in enters the shared conversation workspace.
  let signedIn = false;
  const login = createPage({
    pathname: '/login',
    search: '',
    auth: 'optional',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/login') {
        signedIn = true;
        return jsonResponse(200, {
          principal_id: '11111111-1111-4111-8111-111111111111',
          csrf_token: 'csrf-t095',
          expires_at: '2099-01-01T00:00:00Z',
        });
      }
      if (requestPath === '/api/v1/auth/me') {
        return jsonResponse(200, identityPayload(signedIn ? ['user'] : ['user']));
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  for (const id of ['login-form', 'login', 'password', 'login-status']) login.make('div', id);
  login.make('nav', 'main-nav');
  login.runFile('app.js');
  login.runFile('nav.js');
  login.runInline('login.html');
  await flush();
  login.byId.get('login').value = 't095.reader';
  login.byId.get('password').value = 'correct horse battery staple';
  login.byId.get('login-form').submit();
  await flush();
  assert.deepStrictEqual(login.navigations, ['/chat'],
    'a human sign-in must land on /chat: ' +
      JSON.stringify(login.navigations));

  // Administrators use the same default workspace, with management in navigation.
  const adminLogin = createPage({
    pathname: '/login',
    search: '',
    auth: 'optional',
    fetch: async function (requestPath) {
      if (requestPath === '/api/v1/auth/login') {
        return jsonResponse(200, {
          principal_id: '11111111-1111-4111-8111-111111111111',
          csrf_token: 'csrf-t095',
          expires_at: '2099-01-01T00:00:00Z',
        });
      }
      if (requestPath === '/api/v1/auth/me') {
        return jsonResponse(200, identityPayload(['admin', 'user']));
      }
      return errorResponse(404, 'not_found', requestPath);
    },
  });
  for (const id of ['login-form', 'login', 'password', 'login-status']) adminLogin.make('div', id);
  adminLogin.make('nav', 'main-nav');
  adminLogin.runFile('app.js');
  adminLogin.runFile('nav.js');
  adminLogin.runInline('login.html');
  await flush();
  adminLogin.byId.get('login').value = 't095.admin';
  adminLogin.byId.get('password').value = 'correct horse battery staple';
  adminLogin.byId.get('login-form').submit();
  await flush();
  assert.deepStrictEqual(adminLogin.navigations, ['/chat'],
    'an administrator sign-in must land on /chat: ' +
      JSON.stringify(adminLogin.navigations));
  process.stdout.write('login ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t095-login", scenario)
    assert_passed(completed, "login ok")
