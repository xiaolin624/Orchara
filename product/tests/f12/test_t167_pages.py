"""T3 (GitHub #167): the application workspace (/apps) and the purpose catalog (/catalog).

Two means, the established ones in this repository:

* static seams -- the committed bytes of ``apps.html`` and ``catalog.html`` are
  parsed. The T3 structure (stat row, toolbar, row anatomy, template strip,
  pager, empty/loading/error surfaces) and the business id inventory are
  contractual here, and so is the honesty rule: no metric may be rendered that
  the API does not return.
* one execution seam -- ``apps.html``'s inline script is executed as the
  committed bytes under Node with a minimal DOM/fetch shim (the same technique
  as ``tests/f12/test_t095_page_roles.py``). It proves that search, status
  filtering and sorting act on the already loaded ``#apps-list`` rows only and
  never issue a second request.

What this file does not prove: browser layout, the 375px reflow, or the real
rendered contrast. Those stay in ``tools/verify_frontend.py`` (real Chrome).
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess

import pytest

from support import stack as st

WEB = st.WEB_DIR
NODE = shutil.which("node")

# Shell ids owned by T1's shared frame (tests/f12/test_t165_shell.py).
SHELL_IDS = {
    "app-sidebar",
    "nav-scrim",
    "sidebar-toggle",
    "sidebar-collapse",
    "main-content",
    "account-label",
    "account-role",
}

# The business ids both pages already owned before T3. T3 may change markup,
# wording, columns and styling; it may not rename, drop or invent an id.
BUSINESS_ID_DIGESTS = {
    "apps.html": "4f094a1a684c36d1",
    "catalog.html": "c37a5a87a8ab19ec",
}


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. static structure
# ---------------------------------------------------------------------------
def test_the_apps_page_is_a_scannable_workspace() -> None:
    html = read_page("apps.html")

    # The stat row exists and only names facts the list response can support.
    assert "data-apps-stats" in html, "apps.html has no stat row"
    for key in ("loaded", "published", "draft"):
        assert f'data-apps-stat="{key}"' in html, f"the stat row is missing the {key} value"
    for label in ("已加载", "已发布", "草稿"):
        assert label in html, f"the stat row is missing the {label} label"

    # The toolbar: client-side search, status chips, sort, and a loaded-count note.
    assert "data-apps-search" in html, "the toolbar has no search input"
    for value in ("all", "published", "draft"):
        assert f'data-apps-filter-value="{value}"' in html, f"the {value} filter chip is missing"
    assert "data-apps-sort" in html, "the toolbar has no sort control"
    assert "data-apps-count" in html, "the toolbar has no loaded-count note"
    # The client-side scope must be stated on the page, not just in the script.
    scope = re.search(r"data-apps-scope[^>]*>(.*?)</p>", html, re.S)
    assert scope, "apps.html does not explain what filtering applies to"
    assert "已加载" in scope.group(1) and "只作用于" in scope.group(1), scope.group(1)

    # Rows, loading skeleton, empty states, pager.
    assert re.search(r'<ul[^>]*id="apps-list"', html), "the rows must keep a list container"
    assert "skeleton" in html, "the loading skeleton is gone"
    assert 'id="apps-empty"' in html and "还没有可见的应用" in html
    assert "data-apps-filter-empty" in html, "there is no state for 'nothing matches the filter'"
    assert 'id="apps-next"' in html and "下一页" in html
    assert 'id="apps-status"' in html

    # The hidden-by-default create surfaces keep both their ids and their gate.
    for anchor in ("create-app-card", "template-card"):
        match = re.search(r'id="' + anchor + r'"([^>]*)>', html)
        assert match, anchor
        assert 'data-requires-role="creator"' in match.group(1), anchor
        assert "hidden" in match.group(1), anchor
    assert 'id="create-app-form"' in html and 'id="create-app-name"' in html
    assert 'id="create-app-submit"' in html and 'id="create-from-template"' in html
    assert 'id="create-status"' in html and 'id="template-status"' in html


def test_the_catalog_page_states_version_availability_and_scope() -> None:
    html = read_page("catalog.html")

    # The search surface keeps every id and the honest retrieval wording.
    for marker in (
        'id="catalog-search-form"',
        'id="catalog-query"',
        'id="catalog-search-submit"',
        'id="catalog-retrieval"',
        'id="catalog-status"',
        'id="catalog-count"',
        'id="catalog-results"',
        'id="catalog-empty"',
        'id="catalog-next"',
        "回退检索（索引落后）",
    ):
        assert marker in html, f"catalog.html is missing {marker!r}"

    # An application without an active version is not silently rendered as usable.
    assert "暂无可用版本" in html, "the catalog does not name the unavailable state"
    # The availability rule and the "submit with this version" path stay stated.
    assert "用这个版本提交" in html
    assert "active version 指针" in html
    assert "不显示未发布草稿" in html


def test_the_two_pages_keep_every_business_id() -> None:
    for name, expected in BUSINESS_ID_DIGESTS.items():
        html = read_page(name)
        ids = sorted({value for value in re.findall(r'\bid="([^"]+)"', html) if value not in SHELL_IDS})
        digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
        assert digest == expected, (name, ids)


def test_the_pages_do_not_invent_metrics_without_a_data_source() -> None:
    """``GET /api/v1/applications`` returns id/name/active_version_id only.

    The catalog projection returns application_id/application_name/version_id/
    version_no/manifest_sha256/capability_card only. Neither carries an owner,
    an activity timestamp or a weekly run count, so the pages must not render
    one: a plausible-looking number is worse than no number. The collapsed
    "usage notes" block may still *explain* the absence -- it is documentation,
    not a row of data.
    """
    for name in ("apps.html", "catalog.html"):
        html = read_page(name)
        visible = re.sub(r'<details class="usage-notes">.*?</details>', "", html, flags=re.S)
        for fabricated in ("本周", "次运行", "最近活动", "负责人", "上周", "分钟前"):
            assert fabricated not in visible, (name, fabricated)


# ---------------------------------------------------------------------------
# 2. the inline script, executed as the committed bytes
# ---------------------------------------------------------------------------
HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const webDir = __WEB_DIR__;

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
    this.disabled = false;
    this.title = '';
    this.href = '';
    this.dataset = {};
    this.attributes = {};
    const attrs = attributes || {};
    for (const key of Object.keys(attrs)) this.setAttribute(key, attrs[key]);
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
  appendChild(child) { this.children.push(child); return child; }
  append() {
    for (const node of arguments) {
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }
  replaceChildren() {
    this.children = [];
    this._text = '';
    for (const node of arguments) {
      this.appendChild(typeof node === 'string' ? { nodeType: 3, textContent: node } : node);
    }
  }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  dispatch(type, event) {
    const payload = event || {};
    if (typeof payload.preventDefault !== 'function') payload.preventDefault = function () {};
    for (const fn of this._listeners[type] || []) fn(payload);
  }
  click() { this.dispatch('click'); }
  setAttribute(name, value) {
    this.attributes[String(name)] = String(value);
    if (name === 'hidden') this.hidden = true;
    if (name === 'class') this.className = String(value);
    if (name === 'id') this.id = String(value);
    if (String(name).startsWith('data-')) this.dataset[camel(String(name).slice(5))] = String(value);
  }
  getAttribute(name) {
    if (name === 'hidden') return this.hidden ? '' : null;
    if (name === 'class') return this.className || null;
    if (Object.prototype.hasOwnProperty.call(this.attributes, name)) return this.attributes[name];
    return null;
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

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status: status,
    headers: { get: function () { return null; } },
    text: async function () { return JSON.stringify(body); },
  };
}

function errorResponse(status, code, message) {
  return jsonResponse(status, { error: { code: code, message: message || code, request_id: 'req-t3' } });
}

function identityPayload(roles) {
  return {
    principal_id: '11111111-1111-4111-8111-111111111111',
    kind: 'user',
    roles: roles || ['user'],
    csrf_token: 'csrf-t3',
    expires_at: '2099-01-01T00:00:00Z',
  };
}

async function flush(rounds) {
  for (let index = 0; index < (rounds || 60); index += 1) {
    await new Promise(function (resolve) { setImmediate(resolve); });
  }
}

function createPage(fetchHandler) {
  const registry = [];
  const byId = new Map();
  const body = new El('body');
  body.setAttribute('data-auth', 'required');

  function make(tag, id, attributes) {
    const element = new El(tag, id, attributes);
    registry.push(element);
    if (element.id) byId.set(element.id, element);
    return element;
  }

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
  const context = {
    console: console,
    document: document,
    location: {
      pathname: '/apps',
      search: '',
      assign: function (url) { navigations.push(String(url)); },
    },
    URLSearchParams: URLSearchParams,
    setTimeout: function () { return 0; },
    clearTimeout: function () {},
    crypto: { randomUUID: function () { return 't3-idempotency-key'; } },
    confirm: function () { return true; },
    fetch: async function (requestPath, init) {
      requests.push({ path: String(requestPath), init: init || {} });
      return fetchHandler(String(requestPath), init || {});
    },
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);

  const page = {
    context: context,
    document: document,
    byId: byId,
    make: make,
    navigations: navigations,
    requests: requests,
    q: function (selector) { return document.querySelector(selector); },
    qa: function (selector) { return document.querySelectorAll(selector); },
    runFile: function (asset) {
      vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', asset), 'utf8'), context);
    },
    runInline: function (htmlFile) {
      const html = fs.readFileSync(path.join(webDir, htmlFile), 'utf8');
      const pattern = /<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g;
      const blocks = [];
      let match;
      while ((match = pattern.exec(html)) !== null) blocks.push(match[1]);
      assert.ok(blocks.length > 0, 'no inline script in ' + htmlFile);
      vm.runInContext(blocks[blocks.length - 1], context, { filename: htmlFile });
    },
  };

  // The document surface the committed apps.html inline script addresses by id.
  for (const id of [
    'apps-list', 'apps-status', 'apps-empty', 'apps-next', 'create-app-form',
    'create-app-name', 'create-status', 'create-from-template', 'template-status',
  ]) {
    make('div', id);
  }
  // apps.html ships these two hidden; mirror the committed markup.
  byId.get('apps-empty').hidden = true;
  byId.get('apps-next').hidden = true;
  const createCard = make('section', 'create-app-card', { 'data-requires-role': 'creator' });
  createCard.hidden = true;
  const templateCard = make('section', 'template-card', { 'data-requires-role': 'creator' });
  templateCard.hidden = true;
  // The T3 presentation hooks (no new ids: T1's business id digest is a contract).
  make('input', '', { 'data-apps-search': '' });
  make('select', '', { 'data-apps-sort': '' });
  for (const value of ['all', 'published', 'draft']) {
    make('button', '', { 'data-apps-filter-value': value });
  }
  for (const key of ['loaded', 'published', 'draft']) {
    make('span', '', { 'data-apps-stat': key });
  }
  make('span', '', { 'data-apps-count': '' });
  make('span', '', { 'data-apps-pager-note': '' });
  make('div', '', { 'data-apps-filter-empty': '' });

  page.createCard = createCard;
  page.templateCard = templateCard;
  return page;
}

function buildAppsPage(fetchHandler) {
  const page = createPage(fetchHandler);
  page.runFile('app.js');
  page.runFile('nav.js');
  page.runFile('versions.js');
  page.runInline('apps.html');
  return page;
}

function rowPill(row) {
  return row.children.filter(function (child) {
    return child && String(child.className).split(/\s+/).indexOf('pill') !== -1;
  })[0];
}

function rowLinks(row) {
  return row.children
    .filter(function (child) { return child && child.tagName === 'A'; })
    .map(function (anchor) { return { label: anchor.textContent, href: anchor.href }; });
}

const PUBLISHED = {
  id: '11111111-1111-4111-8111-111111111111',
  name: '销售周报助手',
  active_version_id: 'aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa',
};
const DRAFT = {
  id: '22222222-2222-4222-8222-222222222222',
  name: '团队知识问答',
  active_version_id: null,
};

async function main() {
__SCENARIO__
}

main().catch(function (error) {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def run_harness(node_binary: str, tmp_path, name: str, scenario: str) -> subprocess.CompletedProcess:
    script = HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))).replace("__SCENARIO__", scenario)
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


def test_search_filter_and_sort_act_on_the_loaded_rows_only(node_binary: str, tmp_path) -> None:
    """AC: client-side narrowing must never look like (or issue) a server search."""
    scenario = r"""
  const page = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user', 'creator']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [PUBLISHED, DRAFT], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();

  const list = page.byId.get('apps-list');
  assert.strictEqual(list.children.length, 2, 'both loaded applications must render: ' + list.children.length);

  // Row anatomy: status pill with both encodings (shape + text), a version cell and
  // the three row actions as direct children (the links T095 asserts on).
  const publishedRow = list.children.filter(function (row) {
    return row.textContent.indexOf('销售周报助手') !== -1;
  })[0];
  assert.ok(publishedRow, 'the published application is missing');
  const publishedPill = rowPill(publishedRow);
  assert.ok(publishedPill, 'the row has no status pill');
  assert.strictEqual(publishedPill.textContent, '已发布');
  assert.strictEqual(publishedPill.getAttribute('data-tone'), 'success');
  assert.strictEqual(publishedPill.getAttribute('data-shape'), 'check');

  const draftRow = list.children.filter(function (row) {
    return row.textContent.indexOf('团队知识问答') !== -1;
  })[0];
  assert.ok(draftRow, 'the draft application is missing');
  const draftPill = rowPill(draftRow);
  assert.strictEqual(draftPill.textContent, '草稿');
  assert.strictEqual(draftPill.getAttribute('data-tone'), 'muted');
  assert.strictEqual(draftPill.getAttribute('data-shape'), 'square');

  const links = rowLinks(publishedRow).map(function (link) { return link.href; });
  assert.ok(links.some(function (href) { return /\/edit$/.test(href); }), JSON.stringify(links));
  assert.ok(links.some(function (href) { return /\/debug$/.test(href); }), JSON.stringify(links));
  assert.ok(links.some(function (href) { return /\/versions$/.test(href); }), JSON.stringify(links));

  // The stat row describes the loaded set, all of it.
  assert.strictEqual(page.q('[data-apps-stat="loaded"]').textContent, '2');
  assert.strictEqual(page.q('[data-apps-stat="published"]').textContent, '1');
  assert.strictEqual(page.q('[data-apps-stat="draft"]').textContent, '1');
  assert.ok(/已加载 2/.test(page.q('[data-apps-count]').textContent),
    'the toolbar must name the loaded count: ' + page.q('[data-apps-count]').textContent);

  // Search: local narrowing only, and never a second request.
  const before = page.requests.length;
  const search = page.q('[data-apps-search]');
  search.value = '知识';
  search.dispatch('input');
  assert.strictEqual(list.children.length, 1, 'search must narrow the loaded rows');
  assert.ok(list.children[0].textContent.indexOf('团队知识问答') !== -1);
  assert.strictEqual(page.requests.length, before, 'searching must not query the server again');
  assert.strictEqual(page.q('[data-apps-stat="loaded"]').textContent, '2',
    'the loaded count must not shrink when the view is filtered');

  // Status chips narrow the same loaded rows and keep aria-pressed truthful.
  search.value = '';
  search.dispatch('input');
  const publishedChip = page.q('[data-apps-filter-value="published"]');
  publishedChip.dispatch('click');
  assert.strictEqual(publishedChip.getAttribute('aria-pressed'), 'true');
  assert.strictEqual(page.q('[data-apps-filter-value="all"]').getAttribute('aria-pressed'), 'false');
  assert.strictEqual(list.children.length, 1);
  assert.ok(list.children[0].textContent.indexOf('销售周报助手') !== -1);
  assert.strictEqual(page.requests.length, before, 'filtering must not query the server again');

  // A filter that matches nothing shows the filter-specific empty state, not "no apps".
  search.value = '不存在的应用';
  search.dispatch('input');
  assert.strictEqual(list.children.length, 0);
  assert.strictEqual(page.q('[data-apps-filter-empty]').hidden, false, 'the filter-empty state must show');
  assert.strictEqual(page.byId.get('apps-empty').hidden, true,
    'a filter miss must not claim the workspace is empty');
  assert.strictEqual(page.requests.length, before);

  // Clearing everything brings both rows back and hides the filter-empty state.
  search.value = '';
  search.dispatch('input');
  page.q('[data-apps-filter-value="all"]').dispatch('click');
  assert.strictEqual(list.children.length, 2);
  assert.strictEqual(page.q('[data-apps-filter-empty]').hidden, true);

  // Sort reorders the same loaded rows.
  const sort = page.q('[data-apps-sort]');
  sort.value = 'name';
  sort.dispatch('change');
  const order = list.children.map(function (row) { return row.textContent; });
  assert.ok(order[0].indexOf('团队知识问答') !== -1, 'sorting by name is ascending: ' + JSON.stringify(order));
  assert.strictEqual(page.requests.length, before, 'sorting must not query the server again');
  process.stdout.write('apps view ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t3-apps-view", scenario)
    assert_passed(completed, "apps view ok")


def test_a_pagination_failure_keeps_the_rows_and_the_local_view_usable(node_binary: str, tmp_path) -> None:
    """A failed next page is an error, not a reason to freeze the loaded rows."""
    scenario = r"""
  const page = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user', 'creator']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      if (requestPath.indexOf('cursor=') !== -1) {
        return errorResponse(500, 'internal_error', 'the database is unavailable');
      }
      return jsonResponse(200, { items: [DRAFT], next_cursor: 'next-page-cursor' });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();

  const list = page.byId.get('apps-list');
  assert.strictEqual(list.children.length, 1, 'the first page must render');
  const nextButton = page.byId.get('apps-next');
  assert.strictEqual(nextButton.hidden, false, 'a next page must be offered');

  nextButton.click();
  await flush();
  assert.strictEqual(list.children.length, 1, 'a failed next page must not drop the rows');
  const status = page.byId.get('apps-status');
  assert.ok(/内部|internal_error/.test(status.textContent), status.textContent);
  assert.ok(status.className.indexOf('bad') !== -1, 'the failure must be a failure state');

  // The local view stays usable over what is already on screen.
  page.q('[data-apps-filter-value="published"]').dispatch('click');
  assert.strictEqual(list.children.length, 0, 'the status filter must still narrow the loaded rows');
  assert.strictEqual(page.q('[data-apps-filter-empty]').hidden, false);
  page.q('[data-apps-filter-value="all"]').dispatch('click');
  assert.strictEqual(list.children.length, 1);
  process.stdout.write('apps pagination failure ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t3-apps-next-failure", scenario)
    assert_passed(completed, "apps pagination failure ok")


def test_a_first_load_failure_replaces_the_loading_skeleton_with_the_error(node_binary: str, tmp_path) -> None:
    scenario = r"""
  const page = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user', 'creator']));
    return errorResponse(500, 'internal_error', 'the database is unavailable');
  });
  await flush();

  assert.strictEqual(page.byId.get('apps-list').children.length, 0,
    'a failed first load must not leave the loading skeleton on screen');
  const status = page.byId.get('apps-status');
  assert.ok(/internal_error/.test(status.textContent), status.textContent);
  assert.strictEqual(page.byId.get('apps-empty').hidden, true,
    'a failed load is not the same fact as an empty workspace');
  process.stdout.write('apps first-load failure ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t3-apps-first-failure", scenario)
    assert_passed(completed, "apps first-load failure ok")


def test_a_read_only_caller_gets_rows_without_creator_actions(node_binary: str, tmp_path) -> None:
    scenario = r"""
  const page = buildAppsPage(async function (requestPath) {
    if (requestPath === '/api/v1/auth/me') return jsonResponse(200, identityPayload(['user']));
    if (requestPath.indexOf('/api/v1/applications') === 0) {
      return jsonResponse(200, { items: [PUBLISHED], next_cursor: null });
    }
    return errorResponse(404, 'not_found', requestPath);
  });
  await flush();

  assert.strictEqual(page.createCard.hidden, true, 'a non-creator must not get the create form');
  assert.strictEqual(page.templateCard.hidden, true, 'a non-creator must not get the template action');
  const row = page.byId.get('apps-list').children[0];
  const links = rowLinks(row).map(function (link) { return link.href; });
  assert.ok(links.every(function (href) { return !/\/edit$/.test(href); }), JSON.stringify(links));
  assert.ok(links.every(function (href) { return !/\/debug$/.test(href); }), JSON.stringify(links));
  assert.ok(links.some(function (href) { return /\/versions$/.test(href); }), JSON.stringify(links));
  // The read-only row still carries the real status, not a guessed one.
  assert.strictEqual(rowPill(row).textContent, '已发布');
  process.stdout.write('apps read-only ok\n');
"""
    completed = run_harness(node_binary, tmp_path, "t3-apps-readonly", scenario)
    assert_passed(completed, "apps read-only ok")
