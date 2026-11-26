"""T6 (GitHub #170): the data and capability pages.

Knowledge, attachments, memories, personal data, tools and models share one
skeleton: a page header with a real one-line statistic, a primary action, a data
area with a column reference / row hover / empty row, and honest empty, loading
and error states. Destructive actions are visually separated from the primary
action and keep their confirmation.

Two means, the established ones in this repository:

* static seams -- the committed bytes of the six pages (and their page scripts)
  are parsed. The T6 skeleton hooks (``data-t6-stats`` / ``data-t6-stat`` /
  ``data-t6-count`` / ``data-t6-scope`` / ``data-t6-danger``), the honesty rules
  and the untouched business id inventory are contractual here.
* one execution seam -- ``assets/memories.js`` is executed as the committed
  bytes under Node with a minimal DOM/fetch shim, proving that the stat hooks
  are filled from the server answer and that a candidate/memory row is rendered
  from the contract projection rather than from invented fields.

What this file does not prove: browser layout, the 375px reflow, hover colour or
the real rendered contrast. Those stay in ``tools/verify_frontend.py`` (Chrome).
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

# The business id inventory each page already owned. T6 may change markup,
# wording, columns and styling; it may not rename, drop or invent an id.
BUSINESS_ID_DIGESTS = {
    "knowledge.html": "b6b9ef92a73c7a8e",
    "attachments.html": "2e3f866bd15dd2d7",
    "memories.html": "f9e954c22b76757d",
    "personal-data.html": "ecfc2b453dc8cae2",
    "tools.html": "c8d76a43b91f25a7",
    "models.html": "83ae8b11ecfaec3d",
}

# The page-local script that renders the data area. attachments.html has no
# inline script at all (T030), so its rendering lives in the served asset.
PAGE_SCRIPTS = {
    "knowledge.html": "knowledge.html",
    "attachments.html": "assets/attachments.js",
    "memories.html": "assets/memories.js",
    "personal-data.html": "assets/personal-data.js",
    "tools.html": "tools.html",
    "models.html": "models.html",
}

# Every stat value the committed HTML may ship is the em dash: a number is only
# ever written after the server answered, never baked into the page.
STAT_PLACEHOLDER = "—"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def page_script(name: str) -> str:
    return (WEB / PAGE_SCRIPTS[name]).read_text(encoding="utf-8")


def page_source(name: str) -> str:
    """The page bytes plus the bytes that render its data area."""
    return read_page(name) + "\n" + page_script(name)


def stat_values(html: str) -> dict[str, str]:
    return dict(re.findall(r'data-t6-stat="([a-z0-9-]+)"[^>]*>([^<]*)<', html))


# ---------------------------------------------------------------------------
# 1. the shared skeleton
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("page", sorted(BUSINESS_ID_DIGESTS))
def test_every_data_page_has_the_t6_skeleton(page: str) -> None:
    html = read_page(page)

    # A stat row: real, server-derived values only (empty until the read lands).
    assert "data-t6-stats=" in html, f"{page} has no stat row"
    values = stat_values(html)
    assert len(values) >= 2, (page, values)
    # A loading surface, an empty state and a status/error surface.
    assert "skeleton" in html, f"{page} lost its loading skeleton"
    assert "empty-state" in html, f"{page} has no empty state"
    assert 'role="status"' in html, f"{page} has no live status surface"
    # The shared card head (title slot + action/note slot) is used at least once.
    assert "card-head" in html, f"{page} has no card head"

    # The stat row is filled by the page's own script, not by the markup: the
    # script addresses the data-t6-stat hook and names each value it writes.
    script = page_script(page)
    assert "data-t6-stat=" in script, f"{page}: no script writes the stat hooks"
    for key in values:
        assert f"'{key}'" in script, (
            f"{page}: the {key} stat is never filled by the page script"
        )


@pytest.mark.parametrize("page", sorted(BUSINESS_ID_DIGESTS))
def test_no_stat_value_is_invented_before_the_server_answers(page: str) -> None:
    html = read_page(page)
    for key, value in stat_values(html).items():
        assert value.strip() == STAT_PLACEHOLDER, (
            f"{page}: stat {key} ships {value!r} before any read; a metric may only "
            "be rendered after the API answered"
        )


@pytest.mark.parametrize(
    "page", ["knowledge.html", "memories.html", "tools.html", "models.html"]
)
def test_list_pages_state_the_scope_of_their_client_side_toolbar(page: str) -> None:
    html = read_page(page)
    # A toolbar: a search hook, and (where a closed state vocabulary exists) a
    # filter chip set.
    assert "data-t6-search" in html, f"{page} has no client-side search"
    scope = re.search(r"data-t6-scope[^>]*>(.*?)</p>", html, re.S)
    assert scope, f"{page} does not say what its search/filter applies to"
    assert "已加载" in scope.group(1), scope.group(1)
    assert "不会重新检索服务端" in scope.group(1), scope.group(1)
    # The loaded-count note is a toolbar slot, not an invented total.
    assert "data-t6-count" in html, f"{page} has no loaded-count note"


@pytest.mark.parametrize(
    "page", ["knowledge.html", "memories.html", "personal-data.html"]
)
def test_phone_pages_keep_their_class_and_state_the_mobile_scope(page: str) -> None:
    html = read_page(page)
    assert 'href="/assets/mobile.css"' in html, page
    assert 'data-mobile-scope="no-full-canvas-editing"' in html, page
    note = html[html.index('data-mobile-scope="no-full-canvas-editing"'):]
    note = note[: note.index("</p>")]
    assert "画布" in note and ("不承诺" in note or "不在" in note), (page, note)
    # The page-local stylesheet owns a single-column rule for the phone.
    style = html[html.index("<style>"): html.index("</style>")]
    assert "@media (max-width: 720px)" in style, page
    assert "grid-template-columns: 1fr" in style or "grid-template-columns: minmax(0, 1fr)" in style, page


@pytest.mark.parametrize("page", sorted(BUSINESS_ID_DIGESTS))
def test_wide_data_areas_scroll_inside_a_named_region(page: str) -> None:
    html = read_page(page)
    for match in re.finditer(r"<table\b", html):
        wrapper = html.rindex('<div class="table-scroll"', 0, match.start())
        assert wrapper >= 0, f"{page}: a <table> is not inside .table-scroll"
        region = html[wrapper: match.start()]
        assert 'role="region"' in region, f"{page}: the scroll region is unnamed"
        assert "tabindex=" in region, f"{page}: the scroll region is not focusable"
        assert "aria-label=" in region, f"{page}: the scroll region has no label"


# ---------------------------------------------------------------------------
# 2. per-page structure and honesty
# ---------------------------------------------------------------------------
def test_knowledge_page_is_a_library_document_and_retrieval_workspace() -> None:
    html = read_page("knowledge.html")

    values = stat_values(html)
    for key in ("bases", "documents", "searchable", "failed"):
        assert key in values, (key, values)

    # The document table has a column reference and the real contract columns.
    head = re.search(r"data-t6-doc-head[^>]*>(.*?)</div>", html, re.S)
    assert head, "knowledge.html has no document column header"
    for label in ("文档", "摄取状态", "版本", "来源附件", "操作"):
        assert label in head.group(1), (label, head.group(1))

    # The document projection is only id/knowledge_base_id/state/revision/
    # source_artifact_id: the page must say that it cannot show a name, a type or
    # an update time instead of inventing those columns.
    honesty = re.search(r"data-t6-doc-missing[^>]*>(.*?)</p>", html, re.S)
    assert honesty, "knowledge.html does not state which document fields the API omits"
    for word in ("名称", "类型", "更新时间", "不显示"):
        assert word in honesty.group(1), (word, honesty.group(1))

    # The ingestion failure surface keeps the real code and the chunk count.
    assert 'id="document-error-code"' in html and "失败码" in html
    assert 'id="document-chunks"' in html and "片段数" in html
    # Retrieval and answer keep the "an empty result is not an error" wording.
    assert "不是错误" in html
    assert "引用不可用" in html
    # The state filter chips describe the closed document-state vocabulary.
    for state in ("all", "ready", "active", "failed"):
        assert f'data-t6-doc-state="{state}"' in html, state


def test_attachment_page_uploads_renders_and_bridges_a_tool_run() -> None:
    html = read_page("attachments.html")

    values = stat_values(html)
    for key in ("upload", "size", "tool"):
        assert key in values, (key, values)

    # The upload zone keeps a real file input and exactly one POST form.
    assert "data-t6-upload" in html
    assert 'type="file"' in html and 'id="upload-file"' in html
    assert len(re.findall(r"<form\b", html)) == 1, "the attachments page gained a second form"
    # No inline script may be re-introduced on this page (T030).
    assert re.findall(r"<script(?![^>]*\bsrc\s*=)[^>]*>", html) == []

    # The list absence is stated, not papered over with fake rows.
    note = re.search(r"data-t6-no-list[^>]*>(.*?)</p>", html, re.S)
    assert note, "attachments.html does not explain the missing artifact list"
    for word in ("没有", "列表", "个人数据"):
        assert word in note.group(1), (word, note.group(1))

    # The artifact detail keeps every contract fact and the quarantine reason.
    for marker in (
        'id="artifact-media-type"', 'id="artifact-size"', 'id="artifact-download"',
        'id="artifact-quarantine"', 'id="artifact-quarantine-reason"',
    ):
        assert marker in html, marker
    # The tool bridge keeps the Decimal summary and the download names.
    for word in ("Decimal", "summary.csv", "summary.json", "预置销售统计工具"):
        assert word in html, word


def test_memory_page_shows_candidates_memories_and_the_deletion_state() -> None:
    html = read_page("memories.html")

    values = stat_values(html)
    for key in ("candidates", "memories"):
        assert key in values, (key, values)

    # The contract has no enable/disable endpoint; the page says so instead of
    # shipping a toggle that would 404.
    assert "没有启用/停用接口" in html, "memories.html does not state the missing toggle"
    # The destructive action is separated and keeps its confirmation.
    danger = re.search(r"data-t6-danger[^>]*>", html)
    assert danger, "memories.html has no visually separated destructive zone"
    assert "btn-danger" in page_source("memories.html"), (
        "the delete action is not styled as destructive"
    )
    assert "window.confirm(" in page_script("memories.html")
    # The revision conflict recovery keeps its wording and control.
    assert "用最新版本重试" in html and "版本冲突" in html
    assert "If-Match" in html
    assert "物理副本" in html


def test_personal_data_page_states_impact_and_the_missing_export() -> None:
    html = read_page("personal-data.html")

    values = stat_values(html)
    for key in ("memories", "documents", "grants"):
        assert key in values, (key, values)

    # The impact statement comes first: what stops immediately, what is async.
    impact = re.search(r"data-t6-impact[^>]*>(.*?)</section>", html, re.S)
    assert impact, "personal-data.html has no impact statement"
    for word in ("立即停止访问", "remaining_scopes", "异步", "不声称"):
        assert word in impact.group(1), (word, impact.group(1))

    # There is no export endpoint in this build: say so instead of showing a
    # dead button.
    export_note = re.search(r"data-t6-no-export[^>]*>(.*?)</p>", html, re.S)
    assert export_note, "personal-data.html does not mention the export state"
    assert "未提供导出接口" in export_note.group(1), export_note.group(1)

    # Deletion keeps the confirmation, the deletion_job wording and the danger
    # separation; the artifact delete form is still driven by the real id input.
    assert "data-t6-danger" in html
    assert "btn-danger" in page_source("personal-data.html")
    assert "deletion_job" in html and "物理副本" in html
    assert 'id="artifact-id"' in html and 'id="delete-artifact"' in html
    assert "window.confirm(" in page_script("personal-data.html")
    # The grant surface keeps its consent facts verbatim.
    for word in ("不包含训练同意", "每次正文读取都会写审计", "到期或撤销后立即失效"):
        assert word in html, word


def test_tools_page_submits_reviews_and_lists_capabilities() -> None:
    html = read_page("tools.html")

    values = stat_values(html)
    for key in ("visible", "ready", "review"):
        assert key in values, (key, values)

    # The submit form keeps every contract field and the risk class control.
    for marker in (
        'id="submit-form"', 'id="source-artifact-id"', 'id="input-schema"',
        'id="output-schema"', 'id="dependency-lock"', 'id="risk-class"',
    ):
        assert marker in html, marker
    # The list has a column reference and the review queue is its own surface.
    head = re.search(r"data-t6-tool-head[^>]*>(.*?)</div>", html, re.S)
    assert head, "tools.html has no tool column header"
    for label in ("工具", "构建状态", "审批", "操作"):
        assert label in head.group(1), (label, head.group(1))
    assert 'id="review-card"' in html and "data-t6-review" in html
    # Revoking is destructive and separated; the build digest wording stays.
    assert "data-t6-danger" in html
    assert "btn-danger" in page_source("tools.html")
    assert "内容包摘要" in html and "dependency_revoked" in html


def test_models_page_reports_availability_and_the_bound_report() -> None:
    html = read_page("models.html")

    values = stat_values(html)
    for key in ("versions", "passed", "pending"):
        assert key in values, (key, values)

    # The registration form tag is asserted verbatim elsewhere; the fields stay.
    for marker in (
        'id="model-name"', 'id="model-provider"', 'id="model-endpoint"',
        'id="model-credential"', 'id="model-destination"', 'id="model-config"',
    ):
        assert marker in html, marker
    # Row anatomy: availability + the binding report, and the honest warning.
    head = re.search(r"data-t6-model-head[^>]*>(.*?)</div>", html, re.S)
    assert head, "models.html has no model column header"
    for label in ("模型", "可用状态", "数据去向", "绑定报告", "操作"):
        assert label in head.group(1), (label, head.group(1))
    assert "不能进入共享应用" in html
    # The list projection has no provider/context-window field: say so.
    honesty = re.search(r"data-t6-model-missing[^>]*>(.*?)</p>", html, re.S)
    assert honesty, "models.html does not state which model fields the API omits"
    for word in ("provider", "上下文窗口", "不显示"):
        assert word in honesty.group(1), (word, honesty.group(1))


# ---------------------------------------------------------------------------
# 3. business ids, auth semantics and role gates
# ---------------------------------------------------------------------------
def test_the_six_pages_keep_every_business_id() -> None:
    for name, expected in BUSINESS_ID_DIGESTS.items():
        html = read_page(name)
        ids = sorted({value for value in re.findall(r'\bid="([^"]+)"', html) if value not in SHELL_IDS})
        digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
        assert digest == expected, (name, ids)


def test_the_data_regions_keep_the_server_owned_auth_content_semantics() -> None:
    expected = {
        "knowledge.html": ["kb-list"],
        "attachments.html": ["artifact-panel"],
        "memories.html": ["candidates-list", "memories-list"],
        "personal-data.html": [
            "memories-list", "documents-list", "grants-list",
        ],
        "tools.html": ["review-list", "tools-list"],
        "models.html": ["models-list"],
    }
    for name, ids in expected.items():
        html = read_page(name)
        for anchor in ids:
            match = re.search(r'id="' + anchor + r'"([^>]*)>', html)
            assert match, (name, anchor)
            assert "data-auth-content" in match.group(1), (name, anchor)


def test_the_models_register_card_and_pass_through_the_models_list_are_untouched() -> None:
    html = read_page("models.html")
    # tests/f06/test_t039_pages.py asserts this literal tag.
    assert '<form id="register-form" method="post">' in html
    # tests/f12/test_t095_page_roles.py drives #models-next appended rows.
    assert re.search(r'<ul[^>]*id="models-list"', html)
    assert 'id="models-next"' in html


def test_row_helpers_never_render_a_class_name_as_text() -> None:
    """A rendered row must never print its own CSS classes.

    Both pages build rows with a ``node(tag, text, className)`` helper; passing the
    class in the text slot puts "t6-cell-main" on screen. This is a static guard
    for that exact mistake (found by the real-browser screenshot review).
    """
    for page in ("tools.html", "models.html"):
        script = page_script(page)
        for match in re.finditer(r"node\('([a-z]+)',\s*'([^']*)'", script):
            text = match.group(2)
            assert not text.startswith("t6-"), (page, match.group(0))
            assert not text.startswith("btn "), (page, match.group(0))


# ---------------------------------------------------------------------------
# 4. one execution seam: memories.js renders the contract projection
# ---------------------------------------------------------------------------
MEMORY_RENDER_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const APP_ID = '33333333-3333-4333-8333-333333333333';
const CANDIDATE_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const MEMORY_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.rows = 0;
    this.dataset = {};
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    return (this._text || '') + this.children.map((child) => child.textContent || '').join('');
  }
  setAttribute(name, value) { this.attributes = this.attributes || {}; this.attributes[name] = value; }
  appendChild(child) { this.children.push(child); return child; }
  append() { for (const node of arguments) this.appendChild(node); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  removeChild(child) { this.children = this.children.filter((entry) => entry !== child); }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
  descendants() {
    const out = [];
    const walk = (node) => { for (const child of node.children || []) { out.push(child); walk(child); } };
    walk(this);
    return out;
  }
}

const ids = [
  'candidates-list', 'candidates-status', 'candidates-empty', 'candidates-next',
  'memories-list', 'memories-status', 'memories-empty', 'memories-next',
  'decision-status', 'memory-action-status', 'memory-conflict-retry',
  'deletion-card', 'deletion-status',
];
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));

// The stat hooks are looked up by attribute; the shim only implements the two
// selector forms the page uses.
const stats = new Map();
for (const key of ['candidates', 'memories', 'delete']) stats.set(key, new El('span', 'stat-' + key));
const countNode = new El('span', 'count');

const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector(selector) {
    const stat = /^\[data-t6-stat="([a-z0-9-]+)"\]$/.exec(selector);
    if (stat) return stats.get(stat[1]) || null;
    if (selector === '[data-t6-count]') return countNode;
    if (selector === '[data-t6-search]') return null;
    return null;
  },
  querySelectorAll() { return []; },
  addEventListener() {},
};

const calls = [];
async function api(path) {
  calls.push(path);
  if (path.indexOf('/api/v1/memory-candidates') === 0) {
    return { items: [{
      id: CANDIDATE_ID, application_id: APP_ID,
      text: 'The user prefers quarterly reports.', state: 'pending',
      expires_at: '2026-12-01T00:00:00Z',
    }], next_cursor: null };
  }
  if (path.indexOf('/api/v1/memories') === 0) {
    return { items: [{
      id: MEMORY_ID, application_id: APP_ID, revision: 2,
      text: 'The user moved to Shanghai.',
    }], next_cursor: null };
  }
  return {};
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  api: api,
  csrf: async () => 'csrf-token',
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: (fn) => 1,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/app.js', 'utf8'), context);
context.api = api;
context.csrf = async () => 'csrf-token';
context.requireLogin = async () => ({ roles: ['user'] });
vm.runInContext(fs.readFileSync(webDir + '/assets/memories.js', 'utf8'), context);

async function flush() { for (let i = 0; i < 24; i += 1) await new Promise((r) => setImmediate(r)); }

(async () => {
  await flush();

  // The stat row is filled from the server answer, never from the markup.
  assert.strictEqual(stats.get('candidates').textContent, '1',
    'the pending candidate stat was not filled: ' + stats.get('candidates').textContent);
  assert.strictEqual(stats.get('memories').textContent, '1',
    'the memory stat was not filled: ' + stats.get('memories').textContent);
  assert.ok(String(countNode.textContent).indexOf('已加载') === 0, countNode.textContent);

  // One row per contract object, rendering the projection's own facts.
  const candidates = byId.get('candidates-list');
  assert.strictEqual(candidates.children.length, 1, 'the candidate card did not render');
  const card = candidates.children[0];
  assert.strictEqual(card.tagName, 'LI');
  const candidateText = card.textContent;
  assert.ok(candidateText.indexOf('待确认') >= 0, candidateText);
  assert.ok(candidateText.indexOf('来源：Run 内自动抽取') >= 0, candidateText);
  assert.ok(candidateText.indexOf('应用范围') >= 0, candidateText);
  assert.ok(candidateText.indexOf('过期时间') >= 0, candidateText);

  const memories = byId.get('memories-list');
  assert.strictEqual(memories.children.length, 1, 'the memory row did not render');
  const memory = memories.children[0];
  const memoryText = memory.textContent;
  assert.ok(memoryText.indexOf('revision 2') >= 0, memoryText);
  assert.ok(memoryText.indexOf('The user moved to Shanghai.') >= 0, memoryText);
  // The delete control travels with the row and is a button, not a link.
  const deleteButton = memory.descendants().find(
    (node) => node.tagName === 'BUTTON' && String(node.textContent).indexOf('删除这条记忆') >= 0,
  );
  assert.ok(deleteButton, 'the memory row has no delete action');
  assert.ok(String(deleteButton.className).indexOf('btn-danger') >= 0, deleteButton.className);

  process.stdout.write('memory render ok\n');
})().catch((error) => { console.error(error && error.stack ? error.stack : error); process.exitCode = 1; });
"""


def test_memory_rows_and_stats_render_from_the_contract_projection(
    node_binary: str, tmp_path
) -> None:
    harness = tmp_path / "t170-memory-render.js"
    harness.write_text(
        MEMORY_RENDER_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "memory render ok" in completed.stdout
