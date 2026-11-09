"""C6.03 (#157): the runs page detail interpretation and 410 recovery.

Evidence layers (kept separate, never substituted for each other):

1. **Node behaviour tests**: the real committed bytes ``assets/runstream.js`` and
   ``assets/runs.js`` are executed in a ``vm`` context with a minimal DOM/fetch
   shim. They observe requested paths, the rendered text and the recovery
   decisions, not the browser layout.
2. **Real compiled API**: a throwaway PostgreSQL database migrated by the real
   ``cmd/admin`` chain and the compiled ``cmd/api`` serve ``/runs`` and the real
   assets over loopback. The page logic consumes the HTTP bodies that stack
   actually returned (410 envelope, Run snapshot, children, artifacts, cancel).
3. **Real Chrome (Playwright)**: the real page is driven by a real browser
   against the real API. The 410 and unsafe-snapshot cases inject only the
   response body captured from the real API; they are recorded as browser
   observations, not as real-retention evidence (the latter is layer 2).

The page keeps its DOM renderer and the node-specific panels; the shared rules
(child summary, stream failure, truth, artifact readiness, cancel accepted text
and the 410 snapshot path/correction) are consumed from ``OrcharaRunState``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from support import (
    f00b,
    stack as st,
    t024,
    t029,
    t032,
    t033,
    t036,
    t075,
    t078,
    t081,
    t096,
)
from support.certs import make_stack_certificates, unique_internal_addr
from support.fixtures import api_server, go_binaries  # noqa: F401 - pytest fixtures
from support.runtime_stack import Stack

WEB = Path(__file__).resolve().parents[2] / "apps" / "web" / "src"
NODE = shutil.which("node")

RUN_A = "11111111-2222-4333-8444-555555555555"
RUN_B = "66666666-7777-4888-8999-aaaaaaaaaaaa"
SAFE_SNAPSHOT = "/api/v1/runs/" + RUN_A

RUN_B_QUEUED = {
    "id": RUN_B,
    "state": "queued",
    "phase": "idle",
    "kind": "debug",
    "revision": 2,
    "application_version_id": None,
    "parent_run_id": None,
    "output_artifact_id": None,
    "error_code": None,
    "execution_snapshot_id": "snap-b",
}

RUN_RUNNING = {
    "id": RUN_A,
    "state": "running",
    "phase": "executing",
    "kind": "debug",
    "revision": 1,
    "application_version_id": None,
    "parent_run_id": None,
    "output_artifact_id": None,
    "error_code": None,
    "execution_snapshot_id": "snap-a",
}


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page bytes cannot be executed")
    return NODE


# ---------------------------------------------------------------------------
# Shared Node harness scaffolding (minimal DOM, no browser)
# ---------------------------------------------------------------------------
DOM_SHIM = r"""
'use strict';

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
    this.dataset = {};
    this.attrs = {};
    this.value = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  append(node) { this.children.push(node); return node; }
  append(...nodes) { for (const node of nodes) this.children.push(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

const byId = new Map();
function element(tag, id) {
  const item = new El(tag, id);
  if (id) byId.set(id, item);
  return item;
}

function textOf(node) {
  if (typeof node === 'string') return node;
  let text = String(node && node.textContent !== undefined ? node.textContent : '');
  for (const child of (node && node.children) || []) {
    text += ' ' + textOf(child);
  }
  return text;
}

function makeDocument(options) {
  const opts = options || {};
  return {
    getElementById(id) {
      if (opts.absent && opts.absent.indexOf(id) >= 0) return null;
      if (!byId.has(id)) byId.set(id, element('div', id));
      return byId.get(id);
    },
    createElement(tag) { return element(tag); },
    createTextNode(text) { return { textContent: String(text) }; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
}

function makeContext(extra) {
  const context = Object.assign({
    console: console,
    TextDecoder: TextDecoder,
    setTimeout(fn) { return 1; },
    clearTimeout() {},
    window: { location: { assign() {} }, open() {} },
    document: makeDocument({ absent: ['runs-list'] }),
  }, extra || {});
  return context;
}

async function flush(rounds) {
  for (let index = 0; index < (rounds || 80); index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}
"""


def _harness(script: str, name: str, tmp_path: Path, node_binary: str) -> dict:
    return t096.run_node(
        node_binary,
        tmp_path,
        script,
        name=name,
    )


LATE_SELECTION_HARNESS = DOM_SHIM + r"""const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN_A = __RUN_A__;
const RUN_B = __RUN_B__;
const A = __RUN_RUNNING__;
const B = __RUN_B_QUEUED__;

const apiFullCalls = [];
const apiCalls = [];
let resolveLate;

const lateResponse = new Promise((resolve) => { resolveLate = resolve; });

const context = makeContext({
  api: async (pathname) => {
    apiCalls.push(pathname);
    if (pathname.endsWith('/children') || pathname === '/api/v1/runs/' + RUN_B + '/children') {
      return { items: [], next_cursor: null, generated_at: 'now' };
    }
    if (pathname.endsWith('/artifacts')) {
      return { items: [], next_cursor: null };
    }
    if (pathname.endsWith('/capabilities')) {
      return { items: [] };
    }
    if (pathname.endsWith('/node-results')) {
      return { items: [], next_cursor: null, generated_at: 'now' };
    }
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    apiFullCalls.push(pathname);
    if (pathname === '/api/v1/runs/' + RUN_A) {
      return lateResponse;
    }
    if (pathname === '/api/v1/runs/' + RUN_B) {
      return { payload: B, status: 200, headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull ' + pathname);
  },
  fetch: async (pathname) => {
    apiCalls.push(pathname);
    return {
      ok: false,
      status: 404,
      body: null,
      async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
    };
  },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

(async () => {
  // Selection A starts its detail read but the response is still in flight.
  const pendingA = context.loadRunDetail(RUN_A);
  await flush(12);
  const aStarted = apiFullCalls.slice();

  // The user selects B; B is still non-terminal, so its own poll keeps waiting
  // (the shimmed timer never fires). B must win the rendered detail anyway.
  const pendingB = context.loadRunDetail(RUN_B);
  await flush(20);
  const apiCallsAfterB = apiCalls.slice();

  // The previous selection's response now arrives late: it must be ignored.
  resolveLate({ payload: A, status: 200, headers: { get: () => null } });
  await flush(30);
  void pendingA;
  void pendingB;

  process.stdout.write(JSON.stringify({
    a_started: aStarted,
    api_full_calls: apiFullCalls,
    api_calls_after_b: apiCallsAfterB,
    api_calls_final: apiCalls.slice(),
    detail_id: context.document.getElementById('detail-id').textContent,
    detail_state: context.document.getElementById('detail-state').textContent,
    detail_note: context.document.getElementById('detail-note').textContent,
    runs_status: context.document.getElementById('runs-status').textContent,
  }) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def test_late_response_from_a_previous_selection_is_ignored(
    node_binary: str, tmp_path: Path
) -> None:
    script = (
        LATE_SELECTION_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_A__", json.dumps(RUN_A))
        .replace("__RUN_B__", json.dumps(RUN_B))
        .replace("__RUN_RUNNING__", json.dumps(RUN_RUNNING, ensure_ascii=False))
        .replace("__RUN_B_QUEUED__", json.dumps(RUN_B_QUEUED, ensure_ascii=False))
    )
    report = _harness(script, "c603-late-selection.js", tmp_path, node_binary)
    assert report["a_started"] == ["/api/v1/runs/" + RUN_A], report
    assert report["api_full_calls"] == [
        "/api/v1/runs/" + RUN_A,
        "/api/v1/runs/" + RUN_B,
    ], report
    # The late A response never overwrites B's rendered detail, and it does not
    # start A's panel/timeline observation after B was selected.
    assert report["detail_id"] == RUN_B, report
    assert report["detail_state"] == "queued", report
    assert not any(
        path.startswith("/api/v1/runs/" + RUN_A + "/") for path in report["api_calls_final"]
    ), report


CHILD_SUMMARY_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const PARENT = __PARENT__;
const CHILDREN = __CHILDREN__;

const context = makeContext({
  api: async (pathname) => {
    if (pathname === '/api/v1/runs/' + PARENT.id + '/children') {
      return { items: CHILDREN, next_cursor: null, generated_at: 'now' };
    }
    const match = pathname.match(/^\/api\/v1\/artifacts\/([^/]+)\/content$/);
    if (match) {
      return { artifact: decodeURIComponent(match[1]) };
    }
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    if (pathname === '/api/v1/runs/' + PARENT.id) {
      return { payload: PARENT, status: 200, headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull ' + pathname);
  },
  fetch: async () => ({
    ok: false,
    status: 404,
    body: null,
    async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
  }),
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

(async () => {
  context.showRunDetail(PARENT, {});
  await context.loadChildRunsPanel(PARENT.id);
  await flush(20);
  const list = context.document.getElementById('children-list');
  const summary = context.OrcharaRunState.childSummary(CHILDREN, PARENT);
  process.stdout.write(JSON.stringify({
    status: context.document.getElementById('children-status').textContent,
    items: list.children.map((child) => textOf(child)),
    parent_may_show_success: summary.parentMayShowSuccess,
    summary_line: summary.line,
    parent_line: summary.parentLine,
  }) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _child_summary_report(
    node_binary: str, tmp_path: Path, parent: dict, children: list[dict]
) -> dict:
    script = (
        CHILD_SUMMARY_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__PARENT__", json.dumps(parent, ensure_ascii=False))
        .replace("__CHILDREN__", json.dumps(children, ensure_ascii=False))
    )
    return _harness(script, "c603-child-summary.js", tmp_path, node_binary)


def test_child_summary_comes_from_the_shared_rule_and_guards_parent_success(
    node_binary: str, tmp_path: Path
) -> None:
    failed_parent = {
        "id": RUN_B,
        "state": "succeeded",
        "phase": "idle",
        "kind": "debug",
        "revision": 3,
        "output_artifact_id": "a-parent",
    }
    report = _child_summary_report(
        node_binary,
        tmp_path,
        failed_parent,
        [
            {
                "id": "child-failed",
                "state": "failed",
                "error_code": "child_output_schema_invalid",
                "output_artifact_id": None,
            }
        ],
    )
    assert report["parent_may_show_success"] is False, report
    assert "子任务已全部进入终态（1/1" in report["status"], report
    assert "失败/取消 1 个" in report["status"], report
    # The shared parent rule says this parent may not read as success; the page
    # must disclose that instead of leaving the succeeded snapshot unqualified.
    assert "不把该父 Run 显示为成功结论" in report["status"], report
    assert "已失败（failed）" in report["items"][0], report
    assert "child_output_schema_invalid" in report["items"][0], report

    waiting_parent = {
        "id": RUN_A,
        "state": "waiting_children",
        "phase": "waiting",
        "kind": "debug",
        "revision": 2,
        "output_artifact_id": None,
    }
    waiting = _child_summary_report(
        node_binary,
        tmp_path,
        waiting_parent,
        [{"id": "child-queued", "state": "queued", "output_artifact_id": None}],
    )
    assert waiting["parent_may_show_success"] is False, waiting
    assert "等待子任务（已完成 0/1" in waiting["status"], waiting
    assert "父 Run 处于 waiting_children" in waiting["status"], waiting
    assert "尚未被领取（queued）" in waiting["items"][0], waiting

    running_child = _child_summary_report(
        node_binary,
        tmp_path,
        failed_parent,
        [{"id": "child-running", "state": "running", "output_artifact_id": None}],
    )
    assert running_child["parent_may_show_success"] is False, running_child
    assert "不把该父 Run 显示为成功结论" in running_child["status"], running_child
    assert "已成功（succeeded）" not in running_child["status"], running_child


DETAIL_TRUTH_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN = __RUN__;
const FACTS = __FACTS__;

const context = makeContext({
  api: async (pathname) => {
    if (pathname.endsWith('/children')) {
      return { items: [], next_cursor: null, generated_at: 'now' };
    }
    if (pathname.endsWith('/artifacts')) {
      return { items: [], next_cursor: null };
    }
    if (pathname.endsWith('/capabilities')) {
      return { items: [] };
    }
    if (pathname.endsWith('/node-results')) {
      return { items: [], next_cursor: null, generated_at: 'now' };
    }
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    if (pathname === '/api/v1/runs/' + RUN.id) {
      return { payload: RUN, status: 200, headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull ' + pathname);
  },
  fetch: async () => ({
    ok: false,
    status: 404,
    body: null,
    async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
  }),
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

const shown = context.showRunDetail(RUN, FACTS);
const truth = context.OrcharaRunState.view(RUN, FACTS);
process.stdout.write(JSON.stringify({
  shown: shown ? { id: shown.id, state: shown.state } : null,
  detail_note: context.document.getElementById('detail-note').textContent,
  detail_lifecycle: context.document.getElementById('detail-lifecycle').textContent,
  detail_lease: context.document.getElementById('detail-lease').textContent,
  runs_has_describe_run_state: typeof context.describeRunState,
  runs_has_detail_note: typeof context.runDetailNote,
  runs_has_lease_text: typeof context.runLeaseText,
  shared_detail: truth.detail,
  shared_actions: truth.actions,
  shared_stage: truth.stage,
}) + '\n');
"""


def _detail_truth_report(
    node_binary: str, tmp_path: Path, run: dict, facts: dict | None = None
) -> dict:
    script = (
        DETAIL_TRUTH_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN__", json.dumps(run, ensure_ascii=False))
        .replace("__FACTS__", json.dumps(facts or {}, ensure_ascii=False))
    )
    return _harness(script, "c603-detail-truth.js", tmp_path, node_binary)


def test_detail_note_uses_the_shared_aggregate_view_not_a_page_copy(
    node_binary: str, tmp_path: Path
) -> None:
    finalizing = {
        "id": RUN_A,
        "state": "running",
        "phase": "finalizing",
        "kind": "debug",
        "revision": 4,
        "output_artifact_id": None,
        "error_code": None,
    }
    report = _detail_truth_report(node_binary, tmp_path, finalizing)
    assert report["runs_has_detail_note"] == "function", report
    assert report["runs_has_lease_text"] == "function", report
    # The page-local truth copy is deleted; the shared view is the only rule.
    assert report["runs_has_describe_run_state"] == "undefined", report
    assert report["detail_note"].startswith(report["shared_detail"]), report
    for action in report["shared_actions"]:
        assert action in report["detail_note"], (action, report)
    assert "正在收尾" in report["detail_note"], report
    assert "退出证据" in report["detail_note"], report
    assert "不是成功" in report["detail_note"], report
    assert "已成功（succeeded）" not in report["detail_note"], report
    assert "finalizing" in report["detail_lifecycle"], report

    succeeded = dict(finalizing, state="succeeded", phase="idle", output_artifact_id="a-final")
    done = _detail_truth_report(node_binary, tmp_path, succeeded)
    assert "已成功（succeeded）" in done["detail_note"], done
    assert "查看最终产物（output_artifact_id）" in done["detail_note"], done
    assert "terminal" in done["detail_lifecycle"], done


def test_lease_facts_remain_in_the_dedicated_page_row(
    node_binary: str, tmp_path: Path
) -> None:
    running = dict(
        RUN_RUNNING,
        state="running",
        phase="executing",
    )
    report = _detail_truth_report(
        node_binary,
        tmp_path,
        running,
        {
            "startedAt": "2026-11-03T00:00:00Z",
            "leaseDeadline": "2026-11-03T00:00:30Z",
        },
    )
    # The shared truth sentence no longer invents a lease; the page still shows
    # the real header facts in its own lease row.
    assert "不伪造" in report["detail_note"], report
    assert "已领取（attempt started_at 2026-11-03T00:00:00Z）" in report["detail_lease"], report
    assert "2026-11-03T00:00:30Z" in report["detail_lease"], report

    no_lease = _detail_truth_report(node_binary, tmp_path, running, {})
    assert "没有可显示的租约事实" in no_lease["detail_lease"], no_lease


STREAM_FAILURE_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const WITH_SHARED = __WITH_SHARED__;
const context = makeContext({
  api: async () => { throw new Error('unexpected api'); },
  apiFull: async () => { throw new Error('unexpected apiFull'); },
  fetch: async () => { throw new Error('unexpected fetch'); },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
if (WITH_SHARED) {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
    context, { filename: 'runstream.js' },
  );
}
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

const expired = {
  error: {
    code: 'event_cursor_expired',
    message: 'expired',
    request_id: 'req-1',
    details: { snapshot_url: '/api/v1/runs/' + __RUN_A__ },
  },
};

const out = { with_shared: WITH_SHARED };
if (WITH_SHARED) {
  for (const [name, status, payload] of [
    ['expired', 410, expired],
    ['auth', 401, { error: { code: 'unauthenticated', message: 'login' } }],
    ['gone', 404, { error: { code: 'resource_not_found', message: 'gone' } }],
    ['transient', 500, { error: { code: 'internal_error', message: 'boom' } }],
    ['network', 0, null],
  ]) {
    const failure = context.streamFailure(status, payload);
    out[name] = {
      kind: failure.kind,
      retry: failure.retry,
      snapshotPath: failure.snapshotPath,
      text: failure.text,
    };
  }
} else {
  try {
    context.streamFailure(410, expired);
    out.error = null;
  } catch (error) {
    out.error = String(error && error.message ? error.message : error);
  }
  // The retired unsafe page fallback must not fetch an unverified URL either.
  out.unsafe = { fetched: [] };
  context.apiFull = async (pathname) => {
    out.unsafe.fetched.push(pathname);
    return { payload: { id: 'x', state: 'queued' }, status: 200, headers: { get: () => null } };
  };
  context.rebuildRunTimelineFromSnapshot(
    '',
    { retry: false, kind: 'cursor_expired', snapshotURL: 'https://evil.example/api/v1/runs/x',
      snapshotPath: '' },
    () => {},
    null,
    { renderSnapshot: () => ({ state: 'queued' }) },
  ).then(
    () => { out.unsafe.resolved = true; },
    (error) => { out.unsafe.rejected = String(error && error.message ? error.message : error); },
  );
}

(async () => {
  await flush(20);
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _stream_failure_report(node_binary: str, tmp_path: Path, with_shared: bool) -> dict:
    script = (
        STREAM_FAILURE_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__WITH_SHARED__", "true" if with_shared else "false")
        .replace("__RUN_A__", json.dumps(RUN_A))
    )
    return _harness(script, "c603-stream-failure.js", tmp_path, node_binary)


def test_stream_failure_classification_is_the_shared_rule_only(
    node_binary: str, tmp_path: Path
) -> None:
    report = _stream_failure_report(node_binary, tmp_path, True)
    assert report["expired"]["kind"] == "cursor_expired", report
    assert report["expired"]["retry"] is False, report
    assert report["expired"]["snapshotPath"] == "/api/v1/runs/" + RUN_A, report
    assert "event_cursor_expired" in report["expired"]["text"], report
    assert report["auth"]["kind"] == "auth" and report["auth"]["retry"] is False, report
    assert report["gone"]["kind"] == "gone" and report["gone"]["retry"] is False, report
    assert report["transient"]["kind"] == "transient", report
    assert report["transient"]["retry"] is True, report
    assert report["network"]["kind"] == "transient", report
    # The three failure classes stay distinct; none collapses into a generic stop.
    assert len({report[name]["kind"] for name in ("expired", "auth", "gone", "transient")}) == 4


def test_missing_shared_module_is_an_explicit_error_and_never_follows_unsafe_urls(
    node_binary: str, tmp_path: Path
) -> None:
    report = _stream_failure_report(node_binary, tmp_path, False)
    # No page-local classifier survives to silently reinterpret the failure: with
    # neither the shared namespace nor the guard loaded, the page fails loudly.
    assert report["error"] is not None, report
    assert "OrcharaRunState" in report["error"], report
    assert (
        "not defined" in report["error"] or "not loaded" in report["error"]
    ), report
    # The deleted unsafe fallback used to fetch failure.snapshotURL directly; the
    # page now cannot rebuild without the shared safe-path rule.
    assert report["unsafe"]["fetched"] == [], report
    assert report["unsafe"].get("resolved") is not True, report


REBUILD_410_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN_ID = __RUN_ID__;
const EXPIRED = __EXPIRED__;
const SNAPSHOT = __SNAPSHOT__;
const DIRECT = __DIRECT__;

const fetchCalls = [];
const apiFullCalls = [];
const context = makeContext({
  api: async (pathname) => {
    if (pathname.endsWith('/children')) return { items: [], next_cursor: null };
    if (pathname.endsWith('/artifacts')) return { items: [], next_cursor: null };
    if (pathname.endsWith('/capabilities')) return { items: [] };
    if (pathname.endsWith('/node-results')) return { items: [], next_cursor: null };
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    apiFullCalls.push(pathname);
    if (pathname === '/api/v1/runs/' + SNAPSHOT.id) {
      return { payload: SNAPSHOT, status: 200, headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull ' + pathname);
  },
  fetch: async (pathname, options) => {
    fetchCalls.push({
      path: pathname,
      headers: (options && options.headers) || {},
    });
    return {
      ok: false,
      status: 410,
      body: null,
      async json() { return EXPIRED; },
    };
  },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

function statusWriter() {
  return (text, kind) => {
    context.document.getElementById('detail-timeline-status').textContent = text;
    context.document.getElementById('detail-timeline-status').className = kind || '';
  };
}

(async () => {
  if (DIRECT) {
    // No usable safe path: the page must stop without fetching anything.
    await context.rebuildRunTimelineFromSnapshot(
      '',
      context.OrcharaRunState.failure(410, EXPIRED),
      statusWriter(),
      context.document.getElementById('detail-timeline'),
      {
        renderSnapshot: (payload) => payload,
        isCancelled: () => false,
      },
    );
    await flush(20);
    process.stdout.write(JSON.stringify({
      direct: true,
      api_full_calls: apiFullCalls,
      timeline_status: context.document.getElementById('detail-timeline-status').textContent,
    }) + '\n');
    return;
  }
  context.beginRunTimeline(RUN_ID, {
    listID: 'detail-timeline',
    statusID: 'detail-timeline-status',
  });
  await flush(80);
  process.stdout.write(JSON.stringify({
    direct: false,
    fetch_calls: fetchCalls,
    api_full_calls: apiFullCalls,
    detail_state: context.document.getElementById('detail-state').textContent,
    detail_phase: context.document.getElementById('detail-phase').textContent,
    detail_note: context.document.getElementById('detail-note').textContent,
    timeline_status: context.document.getElementById('detail-timeline-status').textContent,
    timeline_items: context.document.getElementById('detail-timeline').children.map(textOf),
    snapshot_rebuilt: context.document.getElementById('detail-timeline').dataset.snapshotRebuilt || '',
  }) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _rebuild_410_report(
    node_binary: str,
    tmp_path: Path,
    *,
    run_id: str,
    snapshot_url: str,
    snapshot: dict,
    direct: bool = False,
) -> dict:
    expired = {
        "error": {
            "code": "event_cursor_expired",
            "message": "expired",
            "request_id": "req-c603",
            "details": {"snapshot_url": snapshot_url},
        }
    }
    script = (
        REBUILD_410_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_ID__", json.dumps(run_id))
        .replace("__EXPIRED__", json.dumps(expired, ensure_ascii=False))
        .replace("__SNAPSHOT__", json.dumps(snapshot, ensure_ascii=False))
        .replace("__DIRECT__", "true" if direct else "false")
    )
    return _harness(script, "c603-rebuild-410.js", tmp_path, node_binary)


def test_410_rebuild_fetches_only_the_safe_same_origin_run_snapshot(
    node_binary: str, tmp_path: Path
) -> None:
    snapshot = dict(RUN_RUNNING, state="running", phase="finalizing")
    report = _rebuild_410_report(
        node_binary,
        tmp_path,
        run_id=RUN_A,
        snapshot_url="/api/v1/runs/" + RUN_A,
        snapshot=snapshot,
    )
    assert [call["path"] for call in report["fetch_calls"]] == [
        "/api/v1/runs/" + RUN_A + "/events"
    ], report
    # The first (and only) stream request does not resume from a fabricated 0.
    assert "Last-Event-ID" not in report["fetch_calls"][0]["headers"], report
    assert report["api_full_calls"] == ["/api/v1/runs/" + RUN_A], report
    assert report["detail_state"] == "running", report
    assert report["detail_phase"] == "finalizing", report
    assert "退出证据" in report["detail_note"], report
    assert "410" in report["timeline_status"], report
    assert "旧游标已丢弃" in report["timeline_status"], report
    assert report["timeline_items"], report
    assert "保留窗口" in report["timeline_items"][0], report
    assert "未回放旧帧" in report["timeline_items"][0], report
    assert report["snapshot_rebuilt"] == "true", report


def test_unsafe_snapshot_url_is_never_followed(
    node_binary: str, tmp_path: Path
) -> None:
    snapshot = dict(RUN_RUNNING, state="queued", phase="idle")
    for url in (
        "https://evil.example/api/v1/runs/x",
        "//evil.example/api/v1/runs/x",
        "/api/v1/runs/../admin",
        "/api/v1/runs/x\\..\\secret",
    ):
        report = _rebuild_410_report(
            node_binary,
            tmp_path,
            run_id=RUN_A,
            snapshot_url=url,
            snapshot=snapshot,
        )
        assert report["api_full_calls"] == ["/api/v1/runs/" + RUN_A], (url, report)
        assert not any(
            "evil.example" in call or "admin" in call
            for call in report["api_full_calls"]
        ), (url, report)
        assert report["detail_state"] == "queued", (url, report)


def test_410_without_a_safe_path_stops_without_fabricating_history(
    node_binary: str, tmp_path: Path
) -> None:
    report = _rebuild_410_report(
        node_binary,
        tmp_path,
        run_id="",
        snapshot_url="https://evil.example/api/v1/runs/x",
        snapshot=dict(RUN_RUNNING),
        direct=True,
    )
    assert report["api_full_calls"] == [], report
    assert "没有可安全使用的 Run 快照路径" in report["timeline_status"], report
    assert "不伪造历史" in report["timeline_status"], report


ARTIFACT_READINESS_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN = __RUN__;
const ARTIFACTS = __ARTIFACTS__;

const context = makeContext({
  api: async (pathname) => {
    if (pathname === '/api/v1/runs/' + RUN.id + '/artifacts') {
      return { items: ARTIFACTS, next_cursor: null };
    }
    if (pathname.endsWith('/children')) return { items: [], next_cursor: null };
    if (pathname.endsWith('/capabilities')) return { items: [] };
    if (pathname.endsWith('/node-results')) return { items: [], next_cursor: null };
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async () => { throw new Error('unexpected apiFull'); },
  fetch: async () => ({
    ok: false,
    status: 404,
    body: null,
    async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
  }),
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

(async () => {
  context.showRunDetail(RUN, {});
  await context.loadRunArtifacts(RUN.id);
  await flush(20);
  const list = context.document.getElementById('sandbox-artifacts');
  process.stdout.write(JSON.stringify({
    status: context.document.getElementById('sandbox-artifacts-status').textContent,
    items: list.children.map(textOf),
  }) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _artifact_readiness_report(
    node_binary: str, tmp_path: Path, run: dict, artifacts: list[dict]
) -> dict:
    script = (
        ARTIFACT_READINESS_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN__", json.dumps(run, ensure_ascii=False))
        .replace("__ARTIFACTS__", json.dumps(artifacts, ensure_ascii=False))
    )
    return _harness(script, "c603-artifact-readiness.js", tmp_path, node_binary)


def test_artifact_readiness_comes_from_committed_run_facts(
    node_binary: str, tmp_path: Path
) -> None:
    finalizing = dict(
        RUN_RUNNING, state="running", phase="finalizing", output_artifact_id=None
    )
    unfinished = _artifact_readiness_report(
        node_binary,
        tmp_path,
        finalizing,
        [
            {"id": "a1", "state": "ready", "media_type": "application/json", "size_bytes": 10},
            {"id": "a2", "state": "scanning", "media_type": "text/csv", "size_bytes": 20},
        ],
    )
    assert "未完成运行产物 1 个" in unfinished["status"], unfinished
    assert "其它/未就绪 1 个" in unfinished["status"], unfinished
    assert "未完成运行产物（运行 state=running 未终态）" in unfinished["items"][0], unfinished
    assert "未就绪（state=scanning）" in unfinished["items"][1], unfinished

    succeeded = dict(RUN_RUNNING, state="succeeded", phase="idle", output_artifact_id="a1")
    final = _artifact_readiness_report(
        node_binary,
        tmp_path,
        succeeded,
        [
            {"id": "a1", "state": "ready", "media_type": "application/json", "size_bytes": 10},
            {"id": "a2", "state": "ready", "media_type": "text/csv", "size_bytes": 20},
        ],
    )
    assert "最终产物 1 个" in final["status"], final
    assert "最终产物（output_artifact_id）：由 succeeded 终态提交" in final["items"][0], final
    assert "节点/中间产物（未声明为最终产物）" in final["items"][1], final

    # A failed Run can name an artifact but it is never presented as the final
    # successful output: readiness is not an outcome.
    failed = dict(RUN_RUNNING, state="failed", phase="idle", output_artifact_id="a1")
    failed_report = _artifact_readiness_report(
        node_binary,
        tmp_path,
        failed,
        [{"id": "a1", "state": "ready", "media_type": "application/json", "size_bytes": 10}],
    )
    assert "最终产物 0 个" in failed_report["status"], failed_report
    assert "节点/中间产物" in failed_report["items"][0], failed_report
    assert "最终产物（output_artifact_id）" not in failed_report["items"][0], failed_report


CANCEL_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN = __RUN__;
const DETAIL_BODY = __DETAIL_BODY__;
const CANCELLED_BODY = __CANCELLED_BODY__;

const apiFullCalls = [];
const apiCalls = [];
const context = makeContext({
  api: async (pathname, options) => {
    apiCalls.push({ path: pathname, method: (options && options.method) || 'GET' });
    if (pathname === '/api/v1/runs/' + RUN.id + '/cancel') {
      return DETAIL_BODY;
    }
    if (pathname.endsWith('/children')) return { items: [], next_cursor: null };
    if (pathname.endsWith('/artifacts')) return { items: [], next_cursor: null };
    if (pathname.endsWith('/capabilities')) return { items: [] };
    if (pathname.endsWith('/node-results')) return { items: [], next_cursor: null };
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    apiFullCalls.push({ path: pathname, method: 'GET' });
    if (pathname === '/api/v1/runs/' + RUN.id) {
      return { payload: DETAIL_BODY, status: 200, headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull ' + pathname);
  },
  fetch: async () => ({
    ok: false,
    status: 404,
    body: null,
    async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
  }),
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf-1',
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

(async () => {
  await context.showRunDetail(RUN, {});
  // The cancel handler reloads the non-terminal detail and keeps a poll timer
  // pending; the shim never fires it, so do not await the whole handler.
  const pendingCancel = context.requestCancel(RUN.id);
  await flush(40);
  const afterIntent = {
    status: context.document.getElementById('detail-cancel-status').textContent,
    detail_state: context.document.getElementById('detail-state').textContent,
    detail_note: context.document.getElementById('detail-note').textContent,
    cancel_line: context.document.getElementById('detail-cancel').textContent,
    button_label: context.document.getElementById('detail-cancel-button').textContent,
    button_hidden: context.document.getElementById('detail-cancel-button').hidden,
    button_disabled: context.document.getElementById('detail-cancel-button').disabled,
  };
  context.showRunDetail(CANCELLED_BODY, {});
  const afterStopped = {
    detail_state: context.document.getElementById('detail-state').textContent,
    cancel_line: context.document.getElementById('detail-cancel').textContent,
    button_hidden: context.document.getElementById('detail-cancel-button').hidden,
  };
  process.stdout.write(JSON.stringify({
    api_calls: apiCalls,
    api_full_calls: apiFullCalls,
    after_intent: afterIntent,
    after_stopped: afterStopped,
  }) + '\n');
  void pendingCancel;
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _cancel_report(
    node_binary: str,
    tmp_path: Path,
    run: dict,
    detail_body: dict,
    cancelled_body: dict,
) -> dict:
    script = (
        CANCEL_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN__", json.dumps(run, ensure_ascii=False))
        .replace("__DETAIL_BODY__", json.dumps(detail_body, ensure_ascii=False))
        .replace("__CANCELLED_BODY__", json.dumps(cancelled_body, ensure_ascii=False))
    )
    return _harness(script, "c603-cancel.js", tmp_path, node_binary)


def test_cancel_accepted_is_intent_and_execution_stop_is_a_committed_fact(
    node_binary: str, tmp_path: Path
) -> None:
    running = dict(RUN_RUNNING, state="running", phase="executing")
    cancelling = dict(RUN_RUNNING, state="cancelling", phase="executing")
    cancelled = dict(
        RUN_RUNNING, state="cancelled", phase="idle", revision=3
    )
    report = _cancel_report(node_binary, tmp_path, running, cancelling, cancelled)
    cancel_call = [
        call for call in report["api_calls"]
        if call["path"].endswith("/cancel")
    ]
    assert cancel_call and cancel_call[0]["method"] == "POST", report
    intent = report["after_intent"]
    assert "已接受取消意图" in intent["status"], intent
    assert "正在停止" in intent["status"], intent
    assert "不会撤回" in intent["status"], intent
    # Accepted is not stopped, and the page keeps observing a non-terminal Run.
    assert "已停止" not in intent["status"], intent
    assert intent["detail_state"] == "cancelling", intent
    assert "正在停止" in intent["detail_note"], intent
    assert "再次请求取消（幂等）" == intent["button_label"], intent
    assert intent["button_hidden"] is False, intent

    stopped = report["after_stopped"]
    assert stopped["detail_state"] == "cancelled", stopped
    assert "已停止" in stopped["cancel_line"], stopped
    assert "不表示撤回" in stopped["cancel_line"], stopped
    assert stopped["button_hidden"] is True, stopped


TIMELINE_CLASSIFICATION_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN_ID = __RUN_ID__;
const SCENARIO = __SCENARIO__;

const fetchCalls = [];
const encoder = new TextEncoder();

function sseResponse() {
  const frames = [
    'id: 1\nevent: run.status\ndata: {"seq":1,"type":"run.status","payload":{"state":"succeeded"}}\n\n',
  ];
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        let index = 0;
        return {
          async read() {
            if (index < frames.length) {
              const value = encoder.encode(frames[index]);
              index += 1;
              return { done: false, value };
            }
            return { done: true };
          },
        };
      },
    },
  };
}

function failureResponse(status) {
  return {
    ok: false,
    status,
    body: null,
    async json() {
      return { error: { code: 'http_' + status, message: 'failure' } };
    },
  };
}

const context = makeContext({
  api: async () => { throw new Error('unexpected api'); },
  apiFull: async () => { throw new Error('unexpected apiFull'); },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
  setTimeout(fn) { setImmediate(fn); return 1; },
});

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

context.fetch = async (pathname, options) => {
  fetchCalls.push({ path: pathname, headers: (options && options.headers) || {} });
  if (SCENARIO === 'auth') return failureResponse(401);
  if (SCENARIO === 'gone') return failureResponse(404);
  if (SCENARIO === 'expired') return failureResponse(410);
  if (SCENARIO === 'transient_then_terminal') {
    return fetchCalls.length === 1 ? failureResponse(500) : sseResponse();
  }
  throw new Error('unknown scenario ' + SCENARIO);
};

(async () => {
  const statuses = [];
  const outcome = await context.streamRunTimeline(RUN_ID, {
    onStatus(text, kind) { statuses.push({ text, kind }); },
    onEvent() {},
  });
  process.stdout.write(JSON.stringify({
    scenario: SCENARIO,
    outcome,
    fetch_calls: fetchCalls,
    statuses,
  }) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exitCode = 1;
});
"""


def _timeline_classification_report(
    node_binary: str, tmp_path: Path, scenario: str
) -> dict:
    script = (
        TIMELINE_CLASSIFICATION_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_ID__", json.dumps(RUN_A))
        .replace("__SCENARIO__", json.dumps(scenario))
    )
    return _harness(
        script, f"c603-timeline-{scenario}.js", tmp_path, node_binary
    )


def test_transient_auth_and_missing_streams_stay_distinct(
    node_binary: str, tmp_path: Path
) -> None:
    auth = _timeline_classification_report(node_binary, tmp_path, "auth")
    assert auth["outcome"]["kind"] == "auth", auth
    assert len(auth["fetch_calls"]) == 1, auth

    gone = _timeline_classification_report(node_binary, tmp_path, "gone")
    assert gone["outcome"]["kind"] == "gone", gone
    assert len(gone["fetch_calls"]) == 1, gone

    expired = _timeline_classification_report(node_binary, tmp_path, "expired")
    assert expired["outcome"]["kind"] == "cursor_expired", expired
    assert len(expired["fetch_calls"]) == 1, expired
    assert expired["outcome"]["failure"]["retry"] is False, expired
    assert "410" in expired["outcome"]["failure"]["text"], expired

    transient = _timeline_classification_report(
        node_binary, tmp_path, "transient_then_terminal"
    )
    # A transient failure retried with the same (empty) cursor, then the committed
    # terminal event closed the stream. It never became a silent full replay.
    assert len(transient["fetch_calls"]) == 2, transient
    assert "Last-Event-ID" not in transient["fetch_calls"][1]["headers"], transient
    assert transient["outcome"]["kind"] == "terminal", transient
    assert transient["outcome"]["terminal"] == "succeeded", transient

# ===========================================================================
# 2. real compiled API: served bytes/order and real HTTP bodies
# ===========================================================================
EVIDENCE: list[dict] = []


@pytest.fixture(scope="module")
def c603_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> Stack:
    """One real API process over a throwaway migrated database (own DB)."""
    tmp_path = tmp_path_factory.mktemp("c603-stack")
    try:
        database = st.create_database("orchara_c603_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    dsn, dbname = database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=WEB,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
    )
    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    try:
        f00b.wait_for_internal(stack)
    except Exception:
        server.stop()
        st.drop_database(dbname)
        raise
    try:
        yield stack
    finally:
        server.stop()
        st.drop_database(dbname)


def _owner(stack: Stack, prefix: str):
    """One real creator for this database.

    ``t029.golden_owner`` seeds its golden draft under a fixed application id, so
    a module-scoped database can host only one golden owner; every case below
    reuses it and keeps its own Run rows. ``prefix`` documents the caller in the
    failure output.
    """
    del prefix
    if "golden" not in _OWNERS:
        _OWNERS["golden"] = t029.golden_owner(stack, login="c603-golden-owner")
    return _OWNERS["golden"]


_OWNERS: dict[str, object] = {}


def _cancel_queued(session, run_id: str) -> None:
    """Leave no queued Run behind for the next claim-based test."""
    response = t036.cancel_run(session, run_id)
    assert response.status == 202, response.text
    assert response.json["state"] == "cancelled", response.json


def test_the_compiled_api_serves_the_runs_page_and_the_committed_shared_bytes(
    c603_stack,
) -> None:
    body = c603_stack.server.http.get("/runs").text
    for marker in (
        'id="detail-note"',
        'id="detail-lease"',
        'id="detail-timeline"',
        'id="detail-cancel-status"',
        'id="children-status"',
        'id="sandbox-artifacts-status"',
        "/assets/runstream.js",
        "/assets/runstate-guard.js",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    order = [
        body.index("/assets/runstream.js"),
        body.index("/assets/runstate-guard.js"),
        body.index("/assets/runs.js"),
    ]
    assert order == sorted(order), order

    for name in ("runstream.js", "runs.js"):
        response = c603_stack.server.http.get(f"/assets/{name}")
        assert response.status == 200, (name, response.text)
        assert "javascript" in response.header("Content-Type"), name
        # Byte-exact: the API serves the committed file, not a re-rendered copy.
        assert response.raw == (WEB / "assets" / name).read_bytes(), (
            f"{name}: served bytes differ from committed bytes"
        )

    # The served page script no longer carries the retired page-local rules.
    runs = (WEB / "assets" / "runs.js").read_text(encoding="utf-8")
    assert ("typeof " + "run" + "Stream") not in runs
    assert "function describeRunState(" not in runs
    EVIDENCE.append(
        {
            "case": "served",
            "page": "/runs",
            "runstream_bytes_equal": True,
            "runs_bytes_equal": True,
            "script_order": order,
        }
    )


# ---------------------------------------------------------------------------
# Browser fixture (real Chrome via Playwright; the 410/unsafe cases inject only
# the response body captured from the real API, never production fault switches)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment missing
        pytest.skip(f"Playwright is not installed: {exc}")
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover - browser missing
            pytest.skip(f"installed Chrome cannot be driven headless: {exc}")
        try:
            yield instance
        finally:
            instance.close()


@contextlib.contextmanager
def _browser_page(browser, stack, cookie: str, *, events_body: dict | None = None):
    context = browser.new_context()
    context.add_cookies(
        [{"name": st.SESSION_COOKIE, "value": cookie, "url": stack.server.base_url}]
    )
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    if events_body is not None:
        payload = json.dumps(events_body, ensure_ascii=False)
        page.route(
            "**/api/v1/runs/**/events",
            lambda route: route.fulfill(
                status=410, content_type="application/json", body=payload
            ),
        )
    try:
        yield page, requests, page_errors
    finally:
        context.close()


def _paths(requests: list[str], base_url: str) -> list[str]:
    return [url[len(base_url):].split("?")[0] for url in requests if url.startswith(base_url)]


def _wait_detail_state(page, expected: str) -> None:
    page.wait_for_function(
        "document.getElementById('detail-state').textContent === "
        + json.dumps(expected),
        timeout=20000,
    )


def _wait_contains(page, element_id: str, text: str, *, timeout: int = 20000) -> str:
    page.wait_for_function(
        "document.getElementById(" + json.dumps(element_id) + ").textContent.indexOf("
        + json.dumps(text) + ") >= 0",
        timeout=timeout,
    )
    return page.inner_text("#" + element_id)


def test_real_browser_normal_detail_load_uses_the_shared_view(
    browser, c603_stack
) -> None:
    owner = _owner(c603_stack, "c603-normal")
    run = t032.queued_run(c603_stack, owner)
    run_id = run["id"]
    case = {"case": "normal_detail", "run_id": run_id}
    try:
        with _browser_page(browser, c603_stack, owner.creator.cookie) as (
            page,
            requests,
            page_errors,
        ):
            base = c603_stack.server.base_url
            page.goto(f"{base}/runs?run={run_id}", wait_until="load")
            _wait_detail_state(page, "queued")
            note = page.inner_text("#detail-note")
            assert "尚未被领取（queued）" in note, note
            assert "可操作：取消 Run；刷新快照" in note, note
            children = _wait_contains(page, "children-status", "没有独立子运行")
            artifacts = _wait_contains(page, "sandbox-artifacts-status", "没有已登记")
            assert page.locator("#runstate-status").is_hidden()
            paths = _paths(requests, base)
            assert f"/api/v1/runs/{run_id}" in paths, paths
            assert any(path.endswith("/events") for path in paths), paths
            # The real page has no fallback banner and no page error.
            assert page_errors == [], page_errors
            case.update(
                {
                    "detail_state": page.inner_text("#detail-state"),
                    "detail_note": note,
                    "children_status": children,
                    "artifacts_status": artifacts,
                    "page_errors": page_errors,
                }
            )
            EVIDENCE.append(case)
    finally:
        _cancel_queued(owner.creator, run_id)


def test_real_browser_waiting_parent_and_child_summary(
    browser, c603_stack
) -> None:
    _admin, creator = t075.creator(c603_stack)
    child_version = t078.create_child_version(c603_stack, creator, version_no=1)
    definition = t078.parent_definition([child_version["id"]])
    application_id = t081.parent_application(c603_stack, creator, definition)
    waiting = t081.waiting_parent(
        c603_stack, creator, application_id, child_version["id"]
    )
    parent_id = waiting["parent"]["id"]
    child_id = waiting["child"]["id"]
    case = {"case": "waiting_children", "parent_id": parent_id, "child_id": child_id}
    try:
        with _browser_page(browser, c603_stack, creator.cookie) as (
            page,
            requests,
            page_errors,
        ):
            base = c603_stack.server.base_url
            page.goto(f"{base}/runs?run={parent_id}", wait_until="load")
            _wait_detail_state(page, "waiting_children")
            status = _wait_contains(page, "children-status", "等待子任务")
            assert "0/1" in status, status
            assert "不会显示成功" in status, status
            assert "父 Run 处于 waiting_children" in status, status
            child_text = page.inner_text("#children-list")
            assert "尚未被领取（queued）" in child_text, child_text
            assert page_errors == [], page_errors
            case.update(
                {
                    "detail_state": page.inner_text("#detail-state"),
                    "children_status": status,
                    "children_text": child_text,
                }
            )

            # The real child executes and the parent is re-queued; the refreshed
            # child list must show 1/1 while the parent still does not read success.
            report, _completed = t078.execute_once(c603_stack)
            assert report["status"] == "executed", report
            assert report["outcome"] == "succeeded", report
            t081.wait_run_state(c603_stack.dbname, parent_id, {"queued"})
            page.reload(wait_until="load")
            _wait_detail_state(page, "queued")
            after = _wait_contains(page, "children-status", "1/1")
            assert "父 Run 处于 waiting_children" not in after, after
            assert page.inner_text("#detail-note").find("已成功（succeeded）") < 0
            case["children_status_after_child"] = after
            case["detail_state_after_child"] = page.inner_text("#detail-state")
            EVIDENCE.append(case)
    finally:
        _cancel_queued(creator, parent_id)


def test_real_browser_artifact_readiness_before_and_after_commit(
    browser, c603_stack
) -> None:
    owner = _owner(c603_stack, "c603-artifact")
    lease = t029.claim_lease(
        c603_stack, owner, execution_unit_id="unit-c603-artifact"
    )
    run_id = lease["run_id"]
    artifact_client = t033.artifact_client(c603_stack)
    artifact_id, _sha = t033.upload_run_artifact(
        c603_stack,
        artifact_client,
        lease,
        content=b'{"result":"ok"}',
        purpose="node_output",
        relation="output",
    )
    case = {"case": "artifact_readiness", "run_id": run_id, "artifact_id": artifact_id}
    with _browser_page(browser, c603_stack, owner.creator.cookie) as (
        page,
        requests,
        page_errors,
    ):
        base = c603_stack.server.base_url
        page.goto(f"{base}/runs?run={run_id}", wait_until="load")
        _wait_detail_state(page, "running")
        before = _wait_contains(page, "sandbox-artifacts-status", "未完成运行产物 1 个")
        item_before = page.inner_text("#sandbox-artifacts")
        assert "未完成运行产物（运行 state=running 未终态）" in item_before, item_before
        case["before_status"] = before
        case["before_item"] = item_before

        worker = t029.execution_client(c603_stack, "worker")
        proposed = worker.submit_result(
            run_id,
            attempt_token=lease["attempt_token"],
            suggested_state="succeeded",
            output_artifact_id=artifact_id,
        )
        assert proposed.status == 200, proposed.text
        exited = t029.execution_client(c603_stack, "supervisor").confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-c603-artifact",
            evidence=t029.exit_evidence(lease),
        )
        assert exited.status == 200, exited.text
        t081.wait_run_state(c603_stack.dbname, run_id, {"succeeded"})

        page.reload(wait_until="load")
        _wait_detail_state(page, "succeeded")
        after = _wait_contains(page, "sandbox-artifacts-status", "最终产物 1 个")
        item_after = page.inner_text("#sandbox-artifacts")
        assert "最终产物（output_artifact_id）：由 succeeded 终态提交" in item_after, item_after
        assert page_errors == [], page_errors
        case["after_status"] = after
        case["after_item"] = item_after
        case["page_errors"] = page_errors
        EVIDENCE.append(case)


def test_real_browser_cancel_accepted_is_intent_then_committed_stop(
    browser, c603_stack
) -> None:
    owner = _owner(c603_stack, "c603-cancel")
    lease = t029.claim_lease(c603_stack, owner, execution_unit_id="unit-c603-cancel")
    run_id = lease["run_id"]
    case = {"case": "cancel_intent", "run_id": run_id}
    with _browser_page(browser, c603_stack, owner.creator.cookie) as (
        page,
        requests,
        page_errors,
    ):
        base = c603_stack.server.base_url
        page.goto(f"{base}/runs?run={run_id}", wait_until="load")
        _wait_detail_state(page, "running")
        page.locator("#detail-cancel-button").click()
        intent = _wait_contains(page, "detail-cancel-status", "已接受取消意图")
        assert "正在停止" in intent, intent
        assert "不会撤回" in intent, intent
        # Accepted is never rendered as stopped before the finalizer commits it.
        assert "已停止" not in intent, intent
        _wait_detail_state(page, "cancelling")
        cancel_line = page.inner_text("#detail-cancel")
        assert "正在停止" in cancel_line, cancel_line
        assert page.inner_text("#detail-cancel-button").strip() == "再次请求取消（幂等）"

        exited = t029.execution_client(c603_stack, "supervisor").confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-c603-cancel",
            evidence=t029.exit_evidence(lease, exit_code=137),
        )
        assert exited.status == 200, exited.text
        t081.wait_run_state(c603_stack.dbname, run_id, {"cancelled"})

        page.reload(wait_until="load")
        _wait_detail_state(page, "cancelled")
        stopped = page.inner_text("#detail-cancel")
        assert "已停止" in stopped, stopped
        assert "不表示撤回" in stopped, stopped
        assert page_errors == [], page_errors
        case.update(
            {
                "intent_status": intent,
                "cancelling_line": cancel_line,
                "stopped_line": stopped,
                "page_errors": page_errors,
            }
        )
        EVIDENCE.append(case)


def test_real_browser_410_history_loss_uses_the_real_snapshot(
    browser, c603_stack, api_server, tmp_path: Path
) -> None:
    owner = _owner(c603_stack, "c603-410")
    run = t032.queued_run(c603_stack, owner)
    run_id = run["id"]
    case = {"case": "410_history_loss", "run_id": run_id}
    try:
        # The real 410 envelope comes from a real API process whose retention
        # window is 50ms (deployment configuration, not a per-request switch).
        expiring = api_server(
            c603_stack.dsn,
            extra_env={
                "ORCHARA_RUN_EVENT_RETENTION": "50ms",
                "ORCHARA_RUN_EVENT_POLL_INTERVAL": "20ms",
                "ORCHARA_RUN_EVENT_HEARTBEAT_INTERVAL": "50ms",
            },
        )
        client = st.HttpClient(expiring.base_url)
        cookie = {"Cookie": f"{st.SESSION_COOKIE}={owner.creator.cookie}"}
        time.sleep(0.2)
        expired = client.get(
            t032.events_path(run_id), headers={**cookie, "Last-Event-ID": "0"}
        )
        assert expired.status == 410, expired.text
        body = expired.json
        assert body["error"]["code"] == "event_cursor_expired", body
        assert body["error"]["details"]["snapshot_url"] == t029.run_path(run_id), body
        case["real_410_body"] = body

        with _browser_page(
            browser, c603_stack, owner.creator.cookie, events_body=body
        ) as (page, requests, page_errors):
            base = c603_stack.server.base_url
            page.goto(f"{base}/runs?run={run_id}", wait_until="load")
            _wait_contains(page, "detail-timeline-status", "旧游标已丢弃")
            _wait_detail_state(page, "queued")
            status = page.inner_text("#detail-timeline-status")
            assert "410" in status, status
            assert "快照" in status, status
            assert f"GET /api/v1/runs/{run_id}" in status, status
            timeline = page.inner_text("#detail-timeline")
            assert "保留窗口" in timeline, timeline
            assert "未回放旧帧" in timeline, timeline
            assert page.evaluate(
                "document.getElementById('detail-timeline').dataset.snapshotRebuilt"
            ) == "true"
            paths = _paths(requests, base)
            events_requests = [path for path in paths if path.endswith("/events")]
            # Exactly one 410 observation: the expired cursor is never resumed.
            assert events_requests == [f"/api/v1/runs/{run_id}/events"], events_requests
            assert page_errors == [], page_errors
            case.update(
                {
                    "detail_state": page.inner_text("#detail-state"),
                    "timeline_status": status,
                    "timeline_text": timeline,
                    "events_requests": events_requests,
                    "page_errors": page_errors,
                }
            )
            EVIDENCE.append(case)
    finally:
        _cancel_queued(owner.creator, run_id)


def test_real_browser_unsafe_snapshot_url_is_rejected(
    browser, c603_stack
) -> None:
    owner = _owner(c603_stack, "c603-unsafe")
    run = t032.queued_run(c603_stack, owner)
    run_id = run["id"]
    crafted = {
        "error": {
            "code": "event_cursor_expired",
            "message": "expired",
            "request_id": "req-c603-unsafe",
            "details": {"snapshot_url": "https://evil.example/api/v1/runs/" + run_id},
        }
    }
    case = {"case": "unsafe_snapshot", "run_id": run_id, "crafted_body": crafted}
    try:
        with _browser_page(
            browser, c603_stack, owner.creator.cookie, events_body=crafted
        ) as (page, requests, page_errors):
            base = c603_stack.server.base_url
            page.goto(f"{base}/runs?run={run_id}", wait_until="load")
            _wait_contains(page, "detail-timeline-status", "旧游标已丢弃")
            _wait_detail_state(page, "queued")
            status = page.inner_text("#detail-timeline-status")
            assert f"GET /api/v1/runs/{run_id}" in status, status
            assert "evil.example" not in status, status
            assert not any("evil.example" in url for url in requests), requests
            assert page_errors == [], page_errors
            case.update(
                {
                    "timeline_status": status,
                    "foreign_requests": [url for url in requests if "evil.example" in url],
                    "page_errors": page_errors,
                }
            )
            EVIDENCE.append(case)
    finally:
        _cancel_queued(owner.creator, run_id)


REAL_CLASSIFY_HARNESS = DOM_SHIM + r"""
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const RUN_ID = __RUN_ID__;
const BODIES = __BODIES__;

const context = makeContext({});
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context, { filename: 'runstream.js' },
);
// C6.04 (#158): the guard is part of the real page load order.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context, { filename: 'runstate-guard.js' },
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context, { filename: 'runs.js' },
);

const out = {};
for (const name of ['auth', 'gone']) {
  const failure = context.streamFailure(name === 'auth' ? 401 : 404, BODIES[name]);
  out[name] = { kind: failure.kind, retry: failure.retry, text: failure.text };
}
process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_real_api_401_and_404_are_classified_distinctly(
    c603_stack, node_binary: str, tmp_path: Path
) -> None:
    owner = _owner(c603_stack, "c603-classify")
    run = t032.queued_run(c603_stack, owner)
    run_id = run["id"]
    try:
        anonymous = st.HttpClient(c603_stack.server.base_url).get(
            t032.events_path(run_id)
        )
        assert anonymous.status == 401, anonymous.text
        missing = owner.creator.client.get(
            t032.events_path(str(uuid.uuid4()))
        )
        assert missing.status == 404, missing.text
        bodies = {"auth": anonymous.json, "gone": missing.json}
        script = (
            REAL_CLASSIFY_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
            .replace("__RUN_ID__", json.dumps(run_id))
            .replace("__BODIES__", json.dumps(bodies, ensure_ascii=False))
        )
        report = _harness(script, "c603-real-classify.js", tmp_path, node_binary)
        assert report["auth"]["kind"] == "auth", report
        assert report["auth"]["retry"] is False, report
        assert report["gone"]["kind"] == "gone", report
        assert report["gone"]["retry"] is False, report
        assert report["auth"]["kind"] != report["gone"]["kind"], report
        EVIDENCE.append(
            {
                "case": "real_401_404",
                "auth_status": anonymous.status,
                "gone_status": missing.status,
                "classification": report,
            }
        )
    finally:
        _cancel_queued(owner.creator, run_id)

@pytest.fixture(scope="module", autouse=True)
def _write_c603_evidence():
    yield
    if os.environ.get("C603_WRITE_EVIDENCE") != "1":
        return
    target = Path(__file__).resolve().parents[2] / "reports" / "architecture"
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "C6.03 (#157)",
        "browser": "playwright channel=chrome headless, real cmd/api over loopback",
        "cases": EVIDENCE,
    }
    (target / "C6.03-browser-evidence.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
