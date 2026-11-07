"""F12 second review, page findings F3/F4 (T096 halves).

手段声明（硬性）: 本文件**不是 Playwright / 真实浏览器 DOM**。它用 Node 执行
``apps/web/src/assets/runs.js`` / ``debug.js`` 的**已提交真实字节**（``debug.js``
按 ``apps/web/src/debug.html`` 的真实 id 集合搭最小 DOM shim），证明：

* F3（Spec P2）：调试页收到 410 后没有 ``#detail-id`` 等 /runs 详情锚点，旧代码
  仍调用 ``showRunDetail`` 并在读到快照后抛 ``TypeError``。现在 410 重建必须由
  调用页提供 ``renderSnapshot``；没有 renderer 时明确报错、不重建、不抛异常；
  ``debug.js`` 的真实字节必须把 ``renderSnapshot`` 传给 ``beginRunTimeline``。
* F4（Spec P2）：取快照期间用户切到另一个 Run 时，迟到的快照必须被丢弃，不能
  覆盖新选择、不能追加时间线、不能再调用 renderer。

它证明真实脚本字节、真实请求路径、真实页面状态；**不证明**浏览器布局、真实
``EventSource`` 或真实网卡断线（与 ``tests/f12/test_t096_pages.py`` 相同边界）。
"""

from __future__ import annotations

import json
import shutil

import pytest

from support import stack as st
from support import t096

WEB = st.WEB_DIR
NODE = shutil.which("node")

RUN_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
RUN_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
APP_ID = "cccccccc-3333-4333-8333-cccccccccccc"


def expired_body(run_id: str, request_id: str) -> dict:
    return {
        "error": {
            "code": "event_cursor_expired",
            "message": (
                "the replay cursor is older than the run-event retention window; "
                "fetch the current run snapshot"
            ),
            "request_id": request_id,
            "details": {"snapshot_url": f"/api/v1/runs/{run_id}"},
        }
    }


SNAPSHOT_RUN = {
    "id": RUN_A,
    "state": "running",
    "phase": "finalizing",
    "kind": "debug",
    "revision": 4,
    "application_version_id": None,
    "parent_run_id": None,
    "output_artifact_id": None,
    "error_code": None,
    "execution_snapshot_id": "snap-review2",
}


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page bytes cannot be executed")
    return NODE


# ---------------------------------------------------------------------------
# F3-1: a 410 on a page without /runs detail anchors must not throw
# ---------------------------------------------------------------------------
NO_ANCHOR_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN = __RUN_ID__;
const expiredBody = __EXPIRED_BODY__;
const snapshot = __SNAPSHOT__;

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.dataset = {};
    this.textContent = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

// This page deliberately has no #detail-id / #detail-state anchors: it is the
// shape of debug.html, not runs.html. Missing ids return null like a real DOM.
const byId = new Map();
for (const id of ['detail-timeline', 'detail-timeline-status']) {
  byId.set(id, new El('div', id));
}
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const apiFullCalls = [];
async function fetch(pathname) {
  if (pathname === '/api/v1/runs/' + RUN + '/events') {
    return {
      ok: false, status: 410, body: null,
      async json() { return expiredBody; },
    };
  }
  throw new Error('unexpected fetch ' + pathname);
}
async function apiFull(pathname) {
  apiFullCalls.push(pathname);
  return { payload: snapshot, status: 200, headers: { get: () => null } };
}

const timers = [];
const context = {
  console: console,
  document: document,
  fetch: fetch,
  TextDecoder: TextDecoder,
  apiFull: apiFull,
  api: async (pathname) => { throw new Error('unexpected api ' + pathname); },
  csrf: async () => 'csrf',
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
  window: { location: { assign() {} } },
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'),
  context,
  { filename: 'runstream.js' }
);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/runs.js', 'utf8'),
  context,
  { filename: 'runs.js' }
);

async function flush() {
  for (let i = 0; i < 200; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
function statusText() {
  return document.getElementById('detail-timeline-status').textContent;
}
function timelineTexts() {
  return document.getElementById('detail-timeline').children.map((child) => child.textContent);
}

(async () => {
  const out = { no_renderer: {}, with_renderer: {}, errors: [] };

  // (a) No renderer supplied, and this page has no #detail-id: the page must
  // stop with an explicit message instead of fetching then throwing.
  try {
    context.beginRunTimeline(RUN, {
      listID: 'detail-timeline', statusID: 'detail-timeline-status',
    });
    await flush();
  } catch (error) {
    out.errors.push('no_renderer: ' + (error && error.stack ? error.stack : error));
  }
  out.no_renderer.has_detail_id = document.getElementById('detail-id') !== null;
  out.no_renderer.status = statusText();
  out.no_renderer.api_full_calls = apiFullCalls.slice();
  out.no_renderer.timeline_items = timelineTexts();

  // (b) The same anchor-less page with the page-supplied renderer: the snapshot
  // is rendered by the callback, the timeline records the rebuild, no throw.
  const rendered = [];
  const beforeCalls = apiFullCalls.length;
  try {
    context.beginRunTimeline(RUN, {
      listID: 'detail-timeline',
      statusID: 'detail-timeline-status',
      renderSnapshot: function (run) { rendered.push(run && run.id); return run; },
    });
    await flush();
  } catch (error) {
    out.errors.push('with_renderer: ' + (error && error.stack ? error.stack : error));
  }
  out.with_renderer.rendered = rendered;
  out.with_renderer.api_full_calls = apiFullCalls.slice(beforeCalls);
  out.with_renderer.status = statusText();
  out.with_renderer.timeline_items = timelineTexts();
  out.with_renderer.snapshot_flag =
    (document.getElementById('detail-timeline').dataset || {}).snapshotRebuilt || '';

  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def _no_anchor_report(node_binary: str, tmp_path) -> dict:
    script = (
        NO_ANCHOR_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_ID__", json.dumps(RUN_A))
        .replace(
            "__EXPIRED_BODY__",
            json.dumps(expired_body(RUN_A, "req-review2-f3a"), ensure_ascii=False),
        )
        .replace("__SNAPSHOT__", json.dumps(SNAPSHOT_RUN, ensure_ascii=False))
    )
    return t096.run_node(
        node_binary, tmp_path, script, name="t096-review2-no-anchor.js"
    )


def test_a_410_without_detail_anchors_does_not_throw_and_uses_the_renderer(
    node_binary: str, tmp_path
) -> None:
    report = _no_anchor_report(node_binary, tmp_path)
    assert report["errors"] == [], report

    # No #detail-id on this page (that is why the old fallback threw).
    assert report["no_renderer"]["has_detail_id"] is False, report
    # Without a renderer the page stops *before* reading the snapshot and says
    # why, instead of reading it and then crashing on a non-existent node.
    assert "没有 Run 快照渲染函数" in report["no_renderer"]["status"], report
    assert "不重建" in report["no_renderer"]["status"], report
    assert report["no_renderer"]["api_full_calls"] == [], report
    assert report["no_renderer"]["timeline_items"] == [], report

    # With the renderer supplied by the calling page the snapshot is used.
    assert report["with_renderer"]["rendered"] == [RUN_A], report
    assert report["with_renderer"]["api_full_calls"] == [f"/api/v1/runs/{RUN_A}"], report
    assert len(report["with_renderer"]["timeline_items"]) == 1, report
    assert "保留窗口" in report["with_renderer"]["timeline_items"][0], report
    assert "快照" in report["with_renderer"]["status"], report
    assert report["with_renderer"]["snapshot_flag"] == "true", report


# ---------------------------------------------------------------------------
# F3-2: the committed debug.js really passes its renderSnapshot to the timeline
# ---------------------------------------------------------------------------
DEBUG_PAGE_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');

const webDir = __WEB_DIR__;
const APP_ID = __APP_ID__;
const RUN_ID = __RUN_ID__;
const html = fs.readFileSync(webDir + '/debug.html', 'utf8');

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.type = '';
    this.dataset = {};
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    return (this._text || '') + this.children.map(
      (child) => (child && typeof child === 'object' ? child.textContent || '' : String(child))
    ).join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
  dispatch(type) {
    const event = { preventDefault() {} };
    return Promise.all((this._listeners[type] || []).map((fn) => fn(event)));
  }
}

// Follow the real document structure instead of a hand-copied id list.
const ids = [];
for (const match of html.matchAll(/id="([^"]+)"/g)) ids.push(match[1]);
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const run = {
  id: RUN_ID, state: 'succeeded', phase: 'idle', kind: 'debug',
  revision: 1, execution_snapshot_id: 'snap-debug-review2',
};
const timelineCalls = [];
const runViewCalls = [];
const apiFullCalls = [];

async function apiFull(pathname, options) {
  apiFullCalls.push(pathname);
  const headers = {
    get(name) {
      if (name === 'X-Manifest-SHA256') return 'a'.repeat(64);
      if (name === 'ETag') return '"3"';
      return null;
    },
  };
  if (pathname.indexOf('/draft') >= 0) return { payload: {}, status: 200, headers };
  return { payload: run, status: pathname.indexOf('debug-runs') >= 0 ? 202 : 200, headers };
}

const timers = [];
const context = {
  console: console,
  window: { location: { pathname: '/apps/' + APP_ID + '/debug', assign() {} } },
  document: document,
  crypto: { randomUUID: () => 'dddddddd-4444-4444-8444-dddddddddddd' },
  apiFull: apiFull,
  api: async (pathname) => { throw new Error('unexpected api ' + pathname); },
  csrf: async () => 'csrf-token',
  requireLogin: async () => ({ roles: ['creator'] }),
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
  // Stubs for the page's follow-up consumers; the assertion is about the call
  // debug.js itself makes with its committed bytes.
  beginRunTimeline: function (runID, elements) {
    timelineCalls.push({ runID: runID, elements: elements });
  },
  beginRunView: function (view) { runViewCalls.push(view); },
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/debug.js', 'utf8'),
  context,
  { filename: 'debug.js' }
);

async function flush() {
  for (let i = 0; i < 100; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

(async () => {
  await flush();
  byId.get('debug-revision').value = '3';
  byId.get('debug-input').value = '{"text": "hello"}';
  byId.get('debug-artifacts').value = '';
  await byId.get('debug-form').dispatch('submit');
  await flush();

  const out = { timeline_calls: timelineCalls.length, runview_calls: runViewCalls.slice() };
  const call = timelineCalls[0] || null;
  out.timeline_run_id = call ? call.runID : null;
  out.list_id = call && call.elements ? call.elements.listID : null;
  out.status_id = call && call.elements ? call.elements.statusID : null;
  out.has_render_snapshot = Boolean(
    call && call.elements && typeof call.elements.renderSnapshot === 'function'
  );

  let rendererError = null;
  let rendered = null;
  if (out.has_render_snapshot) {
    try {
      rendered = call.elements.renderSnapshot({
        id: RUN_ID, state: 'succeeded', phase: 'idle', revision: 1,
      });
    } catch (error) {
      rendererError = error && error.stack ? error.stack : String(error);
    }
  }
  out.renderer_error = rendererError;
  out.renderer_returned_id = rendered && rendered.id ? rendered.id : null;
  out.runview_calls_after = runViewCalls.map((view) => ({
    runID: view.runID,
    applicationID: view.applicationID,
    draftRevision: view.draftRevision === undefined ? null : view.draftRevision,
  }));
  out.status_after_renderer = byId.get('debug-status').textContent;

  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def _debug_page_report(node_binary: str, tmp_path) -> dict:
    script = (
        DEBUG_PAGE_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__APP_ID__", json.dumps(APP_ID))
        .replace("__RUN_ID__", json.dumps(RUN_A))
    )
    return t096.run_node(
        node_binary, tmp_path, script, name="t096-review2-debug.js"
    )


def test_the_debug_page_passes_its_own_snapshot_renderer_to_the_timeline(
    node_binary: str, tmp_path
) -> None:
    report = _debug_page_report(node_binary, tmp_path)
    assert report["timeline_calls"] == 1, report
    assert report["timeline_run_id"] == RUN_A, report
    assert report["list_id"] == "run-timeline", report
    assert report["status_id"] == "run-timeline-status", report
    # The committed debug.js bytes must hand the timeline a renderer; this is
    # exactly what the 410 branch needs on a page without /runs detail anchors.
    assert report["has_render_snapshot"] is True, report
    assert report["renderer_error"] is None, report
    assert report["renderer_returned_id"] == RUN_A, report
    # Invoking it rebuilds the node view from the snapshot, with the frozen
    # draft revision the page knows (the submit's 3), not an undefined variable.
    after = report["runview_calls_after"]
    assert len(after) == 2, report
    assert after[0]["runID"] == RUN_A and after[0]["draftRevision"] == 3, report
    assert after[1]["runID"] == RUN_A and after[1]["draftRevision"] == 3, report
    assert after[1]["applicationID"] == APP_ID, report
    assert "410" in report["status_after_renderer"], report
    assert "快照" in report["status_after_renderer"], report


# ---------------------------------------------------------------------------
# F4: a late 410 snapshot must not overwrite a newer Run selection
# ---------------------------------------------------------------------------
LATE_SNAPSHOT_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');

const webDir = __WEB_DIR__;
const A = __RUN_A__;
const B = __RUN_B__;
const expiredA = __EXPIRED_A__;
const expiredB = __EXPIRED_B__;
const snapshotA = __SNAPSHOT_A__;
const snapshotB = __SNAPSHOT_B__;
const lateA = __LATE_A__;
const html = fs.readFileSync(webDir + '/runs.html', 'utf8');

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.dataset = {};
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    return (this._text || '') + this.children.map(
      (child) => (child && typeof child === 'object' ? child.textContent || '' : String(child))
    ).join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute() {}
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

// The runs page's real anchors, minus #runs-list: its presence would auto-start
// the list page and this harness drives the detail timeline directly.
const ids = [];
for (const match of html.matchAll(/id="([^"]+)"/g)) ids.push(match[1]);
const byId = new Map();
for (const id of ids) if (id !== 'runs-list') byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const eventPaths = [];
const apiFullPaths = [];
let aDetailCalls = 0;
let aSnapshotRequested = false;
let resolveA = null;
const aDeferred = new Promise((resolve) => { resolveA = resolve; });

async function fetch(pathname) {
  eventPaths.push(pathname);
  const runID = pathname.indexOf(A) >= 0 ? A : B;
  return {
    ok: false, status: 410, body: null,
    async json() { return runID === A ? expiredA : expiredB; },
  };
}
async function apiFull(pathname) {
  apiFullPaths.push(pathname);
  const headers = { get: () => null };
  if (pathname === '/api/v1/runs/' + A) {
    aDetailCalls += 1;
    if (aDetailCalls === 1) {
      // The initial detail read for A; the later 410 snapshot read is deferred.
      return { payload: snapshotA, status: 200, headers: headers };
    }
    aSnapshotRequested = true;
    return aDeferred;
  }
  if (pathname === '/api/v1/runs/' + B) {
    return { payload: snapshotB, status: 200, headers: headers };
  }
  return { payload: { items: [], next_cursor: null }, status: 200, headers: headers };
}
async function api(pathname) { return { items: [], next_cursor: null }; }

const timers = [];
const context = {
  console: console,
  document: document,
  fetch: fetch,
  TextDecoder: TextDecoder,
  apiFull: apiFull,
  api: api,
  csrf: async () => 'csrf',
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: (fn, ms) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
  window: { location: { assign() {} }, open() {} },
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'),
  context,
  { filename: 'runstream.js' }
);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/runs.js', 'utf8'),
  context,
  { filename: 'runs.js' }
);
// Observe the module's own selection state (top-level let, visible to a later
// script in the same context) and every renderer call.
vm.runInContext(
  'globalThis.__probe = { id: () => displayedRunID, run: () => displayedRun };',
  context,
  { filename: 'probe.js' }
);
const realShowRunDetail = context.showRunDetail;
const renderCalls = [];
context.showRunDetail = function (run, facts) {
  renderCalls.push(run && run.id);
  return realShowRunDetail(run, facts);
};

async function flush() {
  for (let i = 0; i < 200; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
async function waitFor(predicate, label) {
  for (let i = 0; i < 200; i += 1) {
    if (predicate()) return;
    await new Promise((resolve) => setImmediate(resolve));
  }
  throw new Error('timed out waiting for ' + label);
}
function timelineItems() {
  return document.getElementById('detail-timeline').children.map((child) => child.textContent);
}

(async () => {
  const out = {};

  // Select A; its 410 snapshot request is left in flight.
  context.loadRunDetail(A);
  await waitFor(() => aSnapshotRequested, 'A snapshot request');
  out.a_selected = {
    detail_id: document.getElementById('detail-id').textContent,
    probe_id: context.__probe.id(),
  };

  // The user moves to B before A's snapshot arrives.
  context.loadRunDetail(B);
  await waitFor(
    () => timelineItems().length > 0,
    'B snapshot rebuild row'
  );
  const beforeStatus = document.getElementById('detail-timeline-status').textContent;
  const beforeItems = timelineItems();
  const beforeRenders = renderCalls.slice();
  const beforeRun = context.__probe.run();
  out.b_selected = {
    detail_id: document.getElementById('detail-id').textContent,
    detail_state: document.getElementById('detail-state').textContent,
    detail_phase: document.getElementById('detail-phase').textContent,
    probe_id: context.__probe.id(),
    probe_run_id: beforeRun && beforeRun.id ? beforeRun.id : null,
    probe_run_phase: beforeRun ? beforeRun.phase : null,
    timeline_items: beforeItems,
    timeline_status: beforeStatus,
    renders: beforeRenders,
  };

  // A's late snapshot finally arrives: it must be dropped whole.
  resolveA({ payload: lateA, status: 200, headers: { get: () => null } });
  await flush();

  const afterRun = context.__probe.run();
  out.after_late_a = {
    detail_id: document.getElementById('detail-id').textContent,
    detail_state: document.getElementById('detail-state').textContent,
    detail_phase: document.getElementById('detail-phase').textContent,
    probe_id: context.__probe.id(),
    probe_run_id: afterRun && afterRun.id ? afterRun.id : null,
    probe_run_phase: afterRun ? afterRun.phase : null,
    timeline_items: timelineItems(),
    timeline_unchanged: JSON.stringify(timelineItems()) === JSON.stringify(beforeItems),
    timeline_status: document.getElementById('detail-timeline-status').textContent,
    status_unchanged:
      document.getElementById('detail-timeline-status').textContent === beforeStatus,
    renders_after: renderCalls.slice(beforeRenders.length),
    event_paths: eventPaths.slice(),
    api_full_paths: apiFullPaths.slice(),
  };

  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def _late_snapshot_report(node_binary: str, tmp_path) -> dict:
    snapshot_a = dict(SNAPSHOT_RUN, id=RUN_A, phase="executing", revision=2)
    snapshot_b = dict(SNAPSHOT_RUN, id=RUN_B, phase="finalizing", revision=5)
    late_a = dict(
        SNAPSHOT_RUN,
        id=RUN_A,
        phase="executing",
        revision=9,
        execution_snapshot_id="snap-late-a",
    )
    script = (
        LATE_SNAPSHOT_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_A__", json.dumps(RUN_A))
        .replace("__RUN_B__", json.dumps(RUN_B))
        .replace(
            "__EXPIRED_A__",
            json.dumps(expired_body(RUN_A, "req-review2-f4a"), ensure_ascii=False),
        )
        .replace(
            "__EXPIRED_B__",
            json.dumps(expired_body(RUN_B, "req-review2-f4b"), ensure_ascii=False),
        )
        .replace("__SNAPSHOT_A__", json.dumps(snapshot_a, ensure_ascii=False))
        .replace("__SNAPSHOT_B__", json.dumps(snapshot_b, ensure_ascii=False))
        .replace("__LATE_A__", json.dumps(late_a, ensure_ascii=False))
    )
    return t096.run_node(
        node_binary, tmp_path, script, name="t096-review2-late.js"
    )


def test_a_late_410_snapshot_never_overwrites_the_newer_run_selection(
    node_binary: str, tmp_path
) -> None:
    report = _late_snapshot_report(node_binary, tmp_path)

    assert report["a_selected"]["detail_id"] == RUN_A, report
    # Before the late answer, the page is consistently on B.
    assert report["b_selected"]["probe_id"] == RUN_B, report
    assert report["b_selected"]["probe_run_id"] == RUN_B, report
    assert report["b_selected"]["detail_id"] == RUN_B, report
    assert report["b_selected"]["detail_phase"] == "finalizing", report
    assert len(report["b_selected"]["timeline_items"]) == 1, report
    assert "保留窗口" in report["b_selected"]["timeline_items"][0], report
    assert f"/api/v1/runs/{RUN_B}" in report["b_selected"]["timeline_status"], report

    after = report["after_late_a"]
    # The late A snapshot is dropped: B stays selected and displayed.
    assert after["probe_id"] == RUN_B, after
    assert after["probe_run_id"] == RUN_B, after
    assert after["probe_run_phase"] == "finalizing", after
    assert after["detail_id"] == RUN_B, after
    assert after["detail_phase"] == "finalizing", after
    # No A row is appended and the B timeline text is untouched.
    assert after["timeline_unchanged"] is True, after
    assert len(after["timeline_items"]) == 1, after
    assert after["status_unchanged"] is True, after
    assert f"/api/v1/runs/{RUN_B}" in after["timeline_status"], after
    # The renderer is not called for the stale snapshot at all.
    assert after["renders_after"] == [], after
