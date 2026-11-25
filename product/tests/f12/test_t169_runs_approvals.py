"""T5 (GitHub #169): the run workbench (/runs) and the approval workbench (/approvals).

Two means, the ones this repository already uses for page work:

* static seams -- the committed bytes of ``runs.html`` / ``approvals.html`` and of
  their page assets are parsed. The two-pane structure, the section titles, the
  presentation hooks, the phone stacking rule and the business id inventory are
  contractual here. So is the honesty rule that no row, filter or stat may name a
  fact the API does not return (the Run contract has no application name and no
  timestamps) and that a state badge is a shape + text pair, never colour alone.
* execution seams -- ``assets/runs.js`` and ``assets/approvals.js`` are executed
  as the committed bytes under Node with a minimal DOM/fetch shim. They prove
  that the state badges keep the real semantics (finalizing is not success,
  cancelling is not cancelled, needs_reconciliation is not failure), that list
  filtering acts on the already loaded runs only, and that a pending approval is
  the only one that offers a decision.

What this file does not prove: browser layout, the 375px reflow or real contrast.
Those stay in ``tools/verify_frontend.py`` (real Chrome) and are evidence, not
this file's claim.
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

# The business ids both pages already owned before T5. T5 may regroup markup,
# rename section titles and restyle; it may not rename, drop or invent an id.
# New presentation hooks therefore use data-* attributes, exactly like T3 did.
BUSINESS_ID_DIGESTS = {
    "runs.html": "ba4c8d50a0eac0b1",
    "approvals.html": "3f04d708bf093efb",
}

RUN_STATES = (
    "queued",
    "running",
    "waiting_approval",
    "waiting_children",
    "cancelling",
    "recovery_pending",
    "needs_reconciliation",
    "succeeded",
    "failed",
    "cancelled",
)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def read_asset(name: str) -> str:
    return (WEB / "assets" / name).read_text(encoding="utf-8")


def page_style(html: str) -> str:
    """The page's own <style> block (T5 puts every page-specific rule there)."""
    match = re.search(r"<style>(.*?)</style>", html, re.S)
    assert match, "the page lost its own <style> block"
    return match.group(1)


def media_block(css: str, query: str, start: int = 0) -> str:
    """The balanced body of one @media block, assertions tell where it ends."""
    index = css.index(query, start)
    open_brace = css.index("{", index)
    depth = 0
    for position in range(open_brace, len(css)):
        if css[position] == "{":
            depth += 1
        elif css[position] == "}":
            depth -= 1
            if depth == 0:
                return css[index : position + 1]
    raise AssertionError(f"unbalanced @media block for {query!r}")


def stacking_breakpoints(css: str, selector: str) -> list[tuple[int, str]]:
    """Every @media (max-width: Npx) block that stacks `selector` into one column."""
    found: list[tuple[int, str]] = []
    for match in re.finditer(r"@media \(max-width: (\d+)px\)", css):
        block = media_block(css, match.group(0), match.start())
        if selector not in block:
            continue
        tail = block[block.index(selector) :]
        rule = tail[: tail.index("}") + 1] if "}" in tail else tail
        if "display: block" in rule:
            found.append((int(match.group(1)), rule))
    return found


def business_ids(name: str) -> list[str]:
    html = read_page(name)
    return sorted({value for value in re.findall(r'\bid="([^"]+)"', html) if value not in SHELL_IDS})


# ---------------------------------------------------------------------------
# 1. /runs: a two-pane workbench (list left, observation right)
# ---------------------------------------------------------------------------
def test_the_runs_page_is_a_list_and_detail_workbench() -> None:
    html = read_page("runs.html")
    css = page_style(html)

    # Two panes in one grid: a fixed-width run list and the observation column.
    assert 'class="runs-layout"' in html, "runs.html is not a two-pane workbench"
    layout = re.search(r"\.runs-layout\s*\{([^}]*)\}", css)
    assert layout, "the page has no .runs-layout rule"
    rule = layout.group(1)
    assert "display: grid" in rule, rule
    assert re.search(r"grid-template-columns:\s*380px", rule), rule

    # The list toolbar carries real, client-side hooks only (data-*, not ids).
    for hook in (
        "data-runs-filter-state",
        "data-runs-filter-kind",
        "data-runs-count",
        "data-runs-scope",
        "data-runs-filter-empty",
        "data-runs-pager-note",
    ):
        assert hook in html, f"the run list has no {hook} hook"
    scope = re.search(r"data-runs-scope[^>]*>(.*?)</p>", html, re.S)
    assert scope, "runs.html does not explain what the filters apply to"
    assert "已加载" in scope.group(1) and "只作用于" in scope.group(1), scope.group(1)
    # The API does not return an application name or a timestamp per Run, so the
    # page must say so instead of rendering invented columns.
    assert "不返回" in scope.group(1) and "伪造" in scope.group(1), scope.group(1)

    # Loading, empty, filtered-empty and error surfaces all exist.
    assert 'id="runs-status"' in html and 'id="runs-empty"' in html
    assert "skeleton" in html, "the run list lost its loading skeleton"
    assert 'id="runs-next"' in html and "下一页" in html

    # Rows: the row itself is the button (one focus stop per run).
    rows = read_asset("runs.js")
    assert "function renderRunItem(" in rows, "runs.js lost its row renderer"
    assert "run-row" in rows and "run-row-button" in rows, "the run row anatomy is gone"


def test_the_runs_detail_is_a_set_of_named_sections() -> None:
    html = read_page("runs.html")

    # The head answers "which run, what state, what phase, can I stop it".
    assert 'class="runs-detail-head"' in html
    for heading in (
        "租约与截止",
        "等待、恢复与取消",
        "状态、阶段与快照",
        "持久时间线",
        "沙箱工具调试",
        "节点结果",
        "Agent 节点",
        "受控调用账本",
        "独立子运行",
    ):
        assert heading in html, f"the run detail has no {heading} section"

    # The sections are real sections with a heading, not a flat field dump.
    assert 'class="run-section"' in html
    assert html.count('class="run-section"') >= 6

    # The cancel control stays in the head and keeps the guard marker order.
    head = html[html.index('class="runs-detail-head"') :]
    head = head[: head.index('id="detail-cancel-button"') + 200]
    assert 'id="detail-cancel-button"' in head, "the cancel control left the header band"
    assert re.search(r'<button[^>]*id="detail-cancel-button"[^>]*data-runstate-paused', html)

    # Both state and phase are double encoded: shape + text, colour third.
    assert "data-runs-detail-state" in html, "the detail head has no state badge"
    assert "data-runs-detail-phase" in html, "the detail head has no phase badge"
    for hook in ("data-tone", "data-shape"):
        assert hook in html[html.index("data-runs-detail-state") - 200 :]


def test_the_runs_page_keeps_every_business_id_and_the_runstate_banner() -> None:
    html = read_page("runs.html")
    digest = hashlib.sha256("\n".join(business_ids("runs.html")).encode()).hexdigest()[:16]
    assert digest == BUSINESS_ID_DIGESTS["runs.html"], business_ids("runs.html")

    # The dependency banner and the paused lifecycle are untouched (C6.01).
    assert 'id="runstate-status"' in html and 'role="alert"' in html
    for marker in (
        "运行状态暂不可用",
        "finalizing",
        "退出证据",
        "不会显示成功",
        "无租约不伪造进度",
    ):
        assert marker in html or marker in read_asset("runs.js"), marker


# ---------------------------------------------------------------------------
# 2. /approvals: a decision workbench
# ---------------------------------------------------------------------------
def test_the_approvals_page_is_a_list_and_decision_workbench() -> None:
    html = read_page("approvals.html")
    css = page_style(html)

    assert 'class="approvals-layout"' in html, "approvals.html is not a two-pane workbench"
    layout = re.search(r"\.approvals-layout\s*\{([^}]*)\}", css)
    assert layout, "the page has no .approvals-layout rule"
    assert "display: grid" in layout.group(1), layout.group(1)
    assert re.search(r"grid-template-columns:\s*360px", layout.group(1)), layout.group(1)

    # The pending queue states its count and scope.
    assert "data-approvals-count" in html
    assert "data-approvals-scope" in html
    assert "待审批" in html

    # The detail answers "what will be written where, and how risky is it".
    for heading in ("请求内容", "风险与数据去向", "来源版本"):
        assert heading in html, f"the approval detail has no {heading} section"
    assert 'class="approval-block"' in html

    # The decision area exists once, holds both buttons, and has no default.
    decision = re.search(r'data-approvals-decision[^>]*>(.*?)</section>', html, re.S)
    assert decision, "the decision area is gone"
    assert 'id="approve-button"' in decision.group(1)
    assert 'id="reject-button"' in decision.group(1)
    assert "没有默认选择" in decision.group(1), "the decision area must state that nothing is preselected"
    assert 'id="decision-status"' in decision.group(1)
    # A read-only surface for expired / decided / unauthorised approvals.
    assert "data-approvals-readonly" in html
    assert "data-approvals-readonly-text" in html


def test_the_approvals_page_keeps_every_id_and_the_honest_copy() -> None:
    html = read_page("approvals.html")
    digest = hashlib.sha256("\n".join(business_ids("approvals.html")).encode()).hexdigest()[:16]
    assert digest == BUSINESS_ID_DIGESTS["approvals.html"], business_ids("approvals.html")

    for marker in (
        "真实目标",
        "规范化参数",
        "数据去向",
        "没有默认选择",
        "不会撤销",
        "不会赋予额外权限",
        "发布审核是另一个",
        "过期",
    ):
        assert marker in html, f"approvals.html is missing {marker!r}"
    # No preselected control and no credential material on the page.
    assert "checked" not in html
    assert "selected" not in html
    assert "secret_ref" not in html


# ---------------------------------------------------------------------------
# 3. phone: one column, list first, decision still reachable
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "page,selector",
    [("runs.html", ".runs-layout"), ("approvals.html", ".approvals-layout")],
)
def test_the_phone_layout_stacks_both_workbenches(page: str, selector: str) -> None:
    html = read_page(page)
    css = page_style(html)
    assert 'class="mobile-tabbar"' in html, page
    assert "data-mobile-scope=\"no-full-canvas-editing\"" in html, page

    breakpoints = stacking_breakpoints(css, selector)
    assert breakpoints, f"{page} never stacks {selector} into one column"
    assert max(width for width, _rule in breakpoints) >= 720, breakpoints

    # The two panes stack in the documented order: list first, detail second.
    body = html[html.index('class="mobile-tabbar"') :]
    assert "grid-template-columns" not in body  # the tabbar is not a column rule


def test_the_decision_controls_stay_thumb_sized_on_phones() -> None:
    css = page_style(read_page("approvals.html"))
    button = re.search(r"\.approval-decision[^{]*\{([^}]*)\}", css)
    assert button, "the decision area has no page rule"
    phone = media_block(css, "@media (max-width: 720px)")
    assert "44px" in phone, "the phone decision targets are below 44px"


# ---------------------------------------------------------------------------
# 4. execution seam: the state badges keep the real semantics
# ---------------------------------------------------------------------------
BADGE_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const context = { console: console };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context,
  { filename: 'runs.js' }
);

const out = { states: {}, phases: {}, terminal: {} };
for (const state of __STATES__) {
  out.states[state] = context.runStateBadge(state);
  out.terminal[state] = context.OrcharaRunState.isTerminal(state);
}
for (const phase of ['idle', 'executing', 'waiting', 'finalizing']) {
  out.phases[phase] = context.runPhaseBadge(phase);
}
process.stdout.write(JSON.stringify(out) + '\n');
"""


def run_badge_harness(node_binary: str, tmp_path) -> dict:
    script = (
        BADGE_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__STATES__", json.dumps(list(RUN_STATES)))
    )
    path = tmp_path / "t169-badges.js"
    path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_state_badges_are_shape_plus_text_and_never_lie(node_binary: str, tmp_path) -> None:
    report = run_badge_harness(node_binary, tmp_path)
    states = report["states"]
    phases = report["phases"]

    # Each contract state keeps its own shape/tone; colour is the third encoding.
    assert states["succeeded"]["shape"] == "check"
    assert states["succeeded"]["tone"] == "success"
    assert "succeeded" in states["succeeded"]["label"]

    assert states["failed"]["shape"] == "alert"
    assert states["failed"]["tone"] == "danger"
    assert "failed" in states["failed"]["label"]

    assert states["cancelled"]["shape"] == "dash"
    assert states["cancelled"]["tone"] == "muted"

    assert states["running"]["shape"] == "dot"
    assert states["running"]["tone"] == "accent"
    assert "running" in states["running"]["label"]

    assert states["queued"]["shape"] == "ring"
    for waiting in ("waiting_approval", "waiting_children", "recovery_pending"):
        assert states[waiting]["shape"] == "ring", waiting
        assert states[waiting]["tone"] == "warning", waiting
        assert waiting in states[waiting]["label"], waiting

    # finalizing is a phase, never a success state.
    assert phases["finalizing"]["shape"] != "check"
    assert phases["finalizing"]["tone"] != "success"
    assert "finalizing" in phases["finalizing"]["label"]
    assert "已成功" not in phases["finalizing"]["label"]
    assert "收尾" in phases["finalizing"]["label"]

    # cancelling is not cancelled: the badge says the stop is still in flight.
    assert states["cancelling"]["tone"] == "warning"
    assert "cancelling" in states["cancelling"]["label"]
    assert "已停止" not in states["cancelling"]["label"]
    assert "已撤销" not in states["cancelling"]["label"]

    # needs_reconciliation is a human decision, not a failure.
    assert states["needs_reconciliation"]["tone"] == "warning"
    assert states["needs_reconciliation"]["shape"] == "alert"
    assert "人工核对" in states["needs_reconciliation"]["label"]
    assert "失败" not in states["needs_reconciliation"]["label"]

    # Only a committed success earns the check shape / success tone.
    for state in RUN_STATES:
        if report["terminal"][state]:
            continue
        assert states[state]["shape"] != "check", state
        assert states[state]["tone"] != "success", state
    assert report["terminal"]["succeeded"] is True
    assert report["terminal"]["cancelling"] is False


# ---------------------------------------------------------------------------
# 5. execution seam: rows name only contract fields, filters stay client-side
# ---------------------------------------------------------------------------
LIST_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const RUNS = __RUNS__;

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.dataset = {};
    this.attrs = {};
  }
  append(...nodes) { for (const node of nodes) this.children.push(node); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

function textOf(node) {
  if (typeof node === 'string') return node;
  let text = String(node && node.textContent !== undefined ? node.textContent : '');
  for (const child of (node && node.children) || []) text += ' ' + textOf(child);
  return text;
}

const byId = new Map();
function element(tag, id) {
  const item = new El(tag, id);
  if (id) byId.set(id, item);
  return item;
}
for (const id of ['runs-status', 'runs-list', 'runs-empty', 'runs-next', 'detail-cancel-button']) {
  element('div', id);
}

const stateFilter = element('select', '');
stateFilter.value = 'all';
const kindFilter = element('select', '');
kindFilter.value = 'all';
const countNote = element('span', '');
const filterEmpty = element('div', '');
const scopeNote = element('p', '');
const pagerNote = element('span', '');

const hooks = {
  '[data-runs-filter-state]': stateFilter,
  '[data-runs-filter-kind]': kindFilter,
  '[data-runs-count]': countNote,
  '[data-runs-filter-empty]': filterEmpty,
  '[data-runs-scope]': scopeNote,
  '[data-runs-pager-note]': pagerNote,
};

const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) } },
  querySelector(selector) { return hooks[selector] || null; },
  querySelectorAll() { return []; },
};

const calls = [];
const context = {
  console: console,
  document: document,
  window: { location: { assign() {} } },
  setTimeout(fn) { return 1; },
  clearTimeout() {},
  requireLogin: async () => ({ roles: ['user'] }),
  api: async (pathname) => {
    calls.push(pathname);
    return { items: RUNS, next_cursor: null };
  },
  apiFull: async (pathname) => { throw new Error('unexpected detail fetch ' + pathname); },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'), context, {
  filename: 'runstream.js',
});
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'), context, {
  filename: 'runs.js',
});

const flush = async () => {
  for (let index = 0; index < 60; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

function rows() {
  const list = byId.get('runs-list');
  return (list.children || []).map((item) => textOf(item));
}

(async () => {
  await flush();
  const out = {
    row_count: rows().length,
    rows: rows(),
    count_note: countNote.textContent,
    loaded_text: byId.get('runs-status').textContent,
    requests_after_load: calls.slice(),
    facts: context.runListRowFacts(RUNS[0]),
    facts_keys: Object.keys(context.runListRowFacts(RUNS[0])),
    filters: {},
    requests_after_filters: [],
    filter_empty_hidden: filterEmpty.hidden,
  };

  for (const group of ['all', 'active', 'waiting', 'attention', 'terminal']) {
    stateFilter.value = group;
    context.applyRunFilters();
    out.filters[group] = rows().length;
  }
  stateFilter.value = 'all';
  kindFilter.value = 'debug';
  context.applyRunFilters();
  out.filters.kind_debug = rows().length;
  stateFilter.value = 'terminal';
  context.applyRunFilters();
  out.filters.debug_and_terminal = rows().length;
  out.filter_empty_hidden = filterEmpty.hidden;

  out.group_of_state = {
    running: context.runStateGroup('running'),
    queued: context.runStateGroup('queued'),
    waiting_approval: context.runStateGroup('waiting_approval'),
    needs_reconciliation: context.runStateGroup('needs_reconciliation'),
    succeeded: context.runStateGroup('succeeded'),
    cancelled: context.runStateGroup('cancelled'),
    something_new: context.runStateGroup('something_new'),
  };
  out.requests_after_filters = calls.slice();
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""

RUN_FIXTURES = [
    {
        "id": "11111111-1111-4111-8111-111111111111",
        "state": "running",
        "phase": "executing",
        "kind": "debug",
        "revision": 3,
        "application_version_id": None,
        "parent_run_id": None,
        "output_artifact_id": None,
        "error_code": None,
        "execution_snapshot_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    },
    {
        "id": "22222222-2222-4222-8222-222222222222",
        "state": "succeeded",
        "phase": "idle",
        "kind": "debug",
        "revision": 4,
        "application_version_id": None,
        "parent_run_id": None,
        "output_artifact_id": None,
        "error_code": None,
        "execution_snapshot_id": "snap-2",
    },
    {
        "id": "33333333-3333-4333-8333-333333333333",
        "state": "waiting_approval",
        "phase": "waiting",
        "kind": "chat",
        "revision": 5,
        "application_version_id": None,
        "parent_run_id": None,
        "output_artifact_id": None,
        "error_code": None,
        "execution_snapshot_id": "snap-3",
    },
]


def run_list_harness(node_binary: str, tmp_path) -> dict:
    script = LIST_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB))).replace(
        "__RUNS__", json.dumps(RUN_FIXTURES, ensure_ascii=False)
    )
    path = tmp_path / "t169-list.js"
    path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_a_run_row_names_only_contract_fields(node_binary: str, tmp_path) -> None:
    report = run_list_harness(node_binary, tmp_path)
    facts = report["facts"]

    # The short id is the first 8 characters of the real id; nothing is invented.
    assert facts["idShort"] == "11111111"
    assert facts["kind"] == "debug"
    assert facts["revision"] == "3"
    # State and phase are double encoded and carry the enum name.
    assert "running" in facts["stateText"]
    assert facts["stateShape"] == "dot"
    assert "executing" in facts["phaseText"]
    assert facts["phaseShape"] != "check"
    assert facts["snapshotShort"] == "aaaaaaaa"

    # The Run contract has no application name and no timestamp: the row must
    # not grow started-at / duration / application fields.
    assert set(report["facts_keys"]) == {
        "id",
        "idShort",
        "state",
        "stateText",
        "stateTone",
        "stateShape",
        "phase",
        "phaseText",
        "phaseTone",
        "phaseShape",
        "kind",
        "revision",
        "snapshotShort",
    }, report["facts_keys"]

    row = report["rows"][0]
    assert "11111111" in row, row
    assert "running" in row, row
    assert "executing" in row, row
    assert "debug" in row, row


def test_the_run_list_filters_act_on_loaded_rows_without_refetching(
    node_binary: str, tmp_path
) -> None:
    report = run_list_harness(node_binary, tmp_path)
    assert report["row_count"] == 3, report["rows"]
    assert report["requests_after_load"] == ["/api/v1/runs?limit=20"], report["requests_after_load"]
    assert "3" in report["count_note"], report["count_note"]

    filters = report["filters"]
    assert filters["all"] == 3, filters
    assert filters["active"] == 1, filters
    assert filters["waiting"] == 1, filters
    assert filters["attention"] == 0, filters
    assert filters["terminal"] == 1, filters
    assert filters["kind_debug"] == 2, filters
    assert filters["debug_and_terminal"] == 1, filters
    # Filtering reads the loaded runs; the list endpoint is never asked again.
    assert report["requests_after_filters"] == report["requests_after_load"], report[
        "requests_after_filters"
    ]

    assert report["group_of_state"] == {
        "running": "active",
        "queued": "waiting",
        "waiting_approval": "waiting",
        "needs_reconciliation": "attention",
        "succeeded": "terminal",
        "cancelled": "terminal",
        "something_new": "unknown",
    }, report["group_of_state"]


# ---------------------------------------------------------------------------
# 6. execution seam: approvals rows, counts and the read-only states
# ---------------------------------------------------------------------------
APPROVAL_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const APPROVAL_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const RUN_ID = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const PROPOSAL_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const HASH = '0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef';
const EXPIRES = '2026-11-04T12:00:00Z';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.dataset = {};
    this.attrs = {};
  }
  append(...nodes) { for (const node of nodes) this.children.push(node); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

function textOf(node) {
  if (typeof node === 'string') return node;
  let text = String(node && node.textContent !== undefined ? node.textContent : '');
  for (const child of (node && node.children) || []) text += ' ' + textOf(child);
  return text;
}

const byId = new Map();
function element(tag, id) {
  const item = new El(tag, id);
  if (id) byId.set(id, item);
  return item;
}
for (const id of [
  'approvals-status', 'approvals-list', 'approvals-empty', 'approvals-next',
  'approval-detail', 'detail-id', 'detail-run', 'detail-state', 'detail-params-sha',
  'detail-expires', 'detail-target', 'detail-risk', 'detail-params', 'detail-destination',
  'detail-sources', 'detail-proposal-sha', 'approval-expiry-note', 'approve-button',
  'reject-button', 'decision-status',
]) {
  element('div', id);
}

const countNote = element('span', '');
const readonly = element('p', '');
readonly.hidden = true;
const readonlyText = element('span', '');
const detailState = element('span', '');
const hooks = {
  '[data-approvals-count]': countNote,
  '[data-approvals-readonly]': readonly,
  '[data-approvals-readonly-text]': readonlyText,
  '[data-approvals-detail-state]': detailState,
};

const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) } },
  querySelector(selector) { return hooks[selector] || null; },
  querySelectorAll() { return []; },
};

function approval(state) {
  return {
    id: APPROVAL_ID, run_id: RUN_ID, state: state,
    proposal_artifact_id: PROPOSAL_ID, params_sha256: HASH, expires_at: EXPIRES,
  };
}
function proposal() {
  return {
    kind: 'capability_proposal', operation_id: PROPOSAL_ID, node_key: 'agent:node-1',
    target: { origin: 'https://connector.example.test', method: 'POST', path: '/v1/orders' },
    params: { path: '/v1/orders' }, params_sha256: HASH,
    data_destination: 'https://connector.example.test', risk_class: 'write',
    snapshot_id: 'snapshot-id', snapshot_sha256: 'snapshot-sha',
  };
}

let serverState = 'pending';
let decisionFailure = null;
const context = {
  console: console,
  document: document,
  window: { location: { assign() {} }, confirm: () => true },
  crypto: require('crypto'),
  setTimeout(fn) { return 1; },
  clearTimeout() {},
  fetch: async () => ({ ok: true, text: async () => JSON.stringify(proposal()) }),
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'app.js'), 'utf8'), context, {
  filename: 'app.js',
});
// app.js declares its own fetch-backed api(); the harness replaces it with the
// recording stub (the same technique tests/f12/test_t098_mobile_pages.py uses).
context.api = async (pathname) => {
  if (pathname.indexOf('/api/v1/approvals?') === 0 || pathname === '/api/v1/approvals') {
    return { items: [approval(serverState)], next_cursor: null };
  }
  const decision = pathname.match(/^\/api\/v1\/approvals\/([^/]+)\/decision$/);
  if (decision) {
    if (decisionFailure) throw decisionFailure;
    return approval('approved');
  }
  return approval(serverState);
};
context.csrf = async () => 'csrf-token';
vm.runInContext(fs.readFileSync(path.join(webDir, 'assets', 'approvals.js'), 'utf8'), context, {
  filename: 'approvals.js',
});

const flush = async () => {
  for (let index = 0; index < 60; index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

(async () => {
  await flush();
  const list = byId.get('approvals-list');
  const out = {
    row_count: (list.children || []).length,
    row_text: (list.children || []).map((row) => textOf(row)),
    count_note: countNote.textContent,
    status_text: byId.get('approvals-status').textContent,
    pending_readonly_hidden: readonly.hidden,
    pending_state_badge: detailState.textContent,
  };

  await context.loadApprovalDetail(APPROVAL_ID);
  await flush();
  out.pending = {
    readonly_hidden: readonly.hidden,
    approve_disabled: byId.get('approve-button').disabled,
    state_text: byId.get('detail-state').textContent,
    badge: detailState.textContent,
    badge_shape: detailState.attrs['data-shape'],
    decision_status: byId.get('decision-status').textContent,
  };

  serverState = 'expired';
  await context.loadApprovalDetail(APPROVAL_ID);
  await flush();
  out.expired = {
    readonly_hidden: readonly.hidden,
    readonly_text: readonlyText.textContent,
    approve_disabled: byId.get('approve-button').disabled,
    reject_disabled: byId.get('reject-button').disabled,
    decision_status: byId.get('decision-status').textContent,
  };

  serverState = 'approved';
  await context.loadApprovalDetail(APPROVAL_ID);
  await flush();
  out.decided = {
    readonly_hidden: readonly.hidden,
    readonly_text: readonlyText.textContent,
    approve_disabled: byId.get('approve-button').disabled,
  };

  serverState = 'pending';
  decisionFailure = Object.assign(new Error('you are not the approver'), {
    status: 403, code: 'approver_mismatch',
  });
  await context.decideApproval('approve');
  await flush();
  out.refused = {
    readonly_hidden: readonly.hidden,
    readonly_text: readonlyText.textContent,
    decision_status: byId.get('decision-status').textContent,
    approve_disabled: byId.get('approve-button').disabled,
  };

  out.confirm_text = {
    approve: context.decisionConfirmationText('approve', 'capability_proposal'),
    reject: context.decisionConfirmationText('reject', 'capability_proposal'),
  };
  out.decision_payload = context.decisionPayload('approve', HASH);
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def run_approval_harness(node_binary: str, tmp_path) -> dict:
    script = APPROVAL_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
    path = tmp_path / "t169-approvals.js"
    path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_an_approval_row_carries_its_state_and_expiry(node_binary: str, tmp_path) -> None:
    report = run_approval_harness(node_binary, tmp_path)
    assert report["row_count"] == 1, report["row_text"]
    row = report["row_text"][0]
    assert "待审批" in row, row
    assert "bbbbbbbb" in row, row  # the run's short id, not an invented label
    assert "2026-11-04T12:00:00Z" in row, row  # the server's real expiry
    assert report["count_note"].startswith("待审批 1"), report["count_note"]


def test_only_a_pending_approval_offers_a_decision(node_binary: str, tmp_path) -> None:
    report = run_approval_harness(node_binary, tmp_path)

    pending = report["pending"]
    assert pending["readonly_hidden"] is True, pending
    assert pending["approve_disabled"] is False, pending
    assert "待审批" in pending["state_text"]
    assert pending["badge_shape"] == "ring", pending
    assert "没有默认" in pending["decision_status"] or "明确选择" in pending["decision_status"], pending

    expired = report["expired"]
    assert expired["readonly_hidden"] is False, expired
    assert "已过期" in expired["readonly_text"], expired
    assert expired["approve_disabled"] is True and expired["reject_disabled"] is True, expired

    decided = report["decided"]
    assert decided["readonly_hidden"] is False, decided
    assert "批准" in decided["readonly_text"], decided
    assert decided["approve_disabled"] is True, decided

    refused = report["refused"]
    assert refused["readonly_hidden"] is False, refused
    assert "approver_mismatch" in refused["decision_status"], refused
    assert "不是" in refused["readonly_text"], refused
    assert refused["approve_disabled"] is True, refused

    # The decision body still carries exactly the choice and the shown hash.
    assert report["decision_payload"] == {"decision": "approve", "params_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"}
    assert "不能撤销" in report["confirm_text"]["approve"]
    assert "approval_rejected" in report["confirm_text"]["reject"]


# ---------------------------------------------------------------------------
# 7. the shared run-state contract is untouched by this ticket
# ---------------------------------------------------------------------------
def test_the_runs_page_keeps_the_shared_script_order_and_contract() -> None:
    html = read_page("runs.html")
    order = [
        '<script src="/assets/runstream.js"></script>',
        '<script src="/assets/runstate-guard.js"></script>',
        '<script src="/assets/stream-preview.js"></script>',
        '<script src="/assets/runs.js"></script>',
    ]
    positions = [html.index(marker) for marker in order]
    assert positions == sorted(positions), order
    assert re.search(r'<script src="/assets/[a-z0-9-]+\.js"></script>', html)

    rows = read_asset("runs.js")
    assert rows.count("OrcharaRunState.isTerminal(") >= 7
    assert "OrcharaRunStateGuard.ready" in rows
    assert "typeof OrcharaRunState" not in rows
    forbidden = "run" + "Stream"
    for name in ("runs.js", "approvals.js"):
        assert forbidden not in read_asset(name), name
    for page in ("runs.html", "approvals.html"):
        assert forbidden not in read_page(page), page

    # runview.js keeps the top-level surface the debug page calls (classic script).
    view = read_asset("runview.js")
    for symbol in (
        "function beginRunView(",
        "function stopRunView(",
        "function renderRunView(",
        "function runViewOnEvent(",
        "function runViewRequestCancel(",
    ):
        assert symbol in view, symbol
    debug = read_asset("debug.js")
    for call in ("beginRunView(", "runViewOnEvent("):
        assert call in debug, call
