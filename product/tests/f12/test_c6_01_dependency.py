"""C6.01 (#155) page shared-state dependency: explicit interface + failure guard.

手段声明（硬性，先读）
--------------------
本文件把三类证据分开记录，互不冒充：

1. **Node 行为测试**：执行 ``apps/web/src/assets/`` 里**已提交的真实字节**
   （``runstream.js``、``runstate-guard.js``、``chat.js``、``runs.js``、
   ``runview.js``）。DOM/fetch 是最小手写 shim（先例 ``tests/f12/test_t096_pages.py``）。
   它证明真实脚本字节 + 真实门禁逻辑 + 真实页面 adapter 的观察暂停，不证明浏览器布局、
   资源加载时序或真实 Chrome 行为。
2. **真实服务检查**：编译后的 ``cmd/api`` 进程经 loopback socket 提供 HTML 与静态资源；
   断言实际共享字节、脚本顺序、提示元素与暂停按钮标记。
3. **真实浏览器（Playwright + 已安装 Chrome，headless）**：真实 404、真实语法错误、
   真实“可解析但未完整导出”三类资源故障，逐页断言提示、暂停、导航/重载与
   无实时观察请求；并跑正常路径对照。它与 Node 隔离结果分开记录。

被测 interface（独立于实现写在下面）：``OrcharaRunState`` 命名空间 + guard。
C6.04 (#158) 已删除 C6.01 暂留的 14 个旧全局出口：本文件断言它们不存在。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import f00b, stack as st, t024, t029, t032
from support.certs import make_stack_certificates, unique_internal_addr
from support.runtime_stack import Stack
from support.stack import WEB_DIR

WEB = WEB_DIR
NODE = shutil.which("node")

# The explicit shared-state surface the guard must require. This list is the
# independent spec (design §C6 + the operations the three pages call); it is not
# generated from the asset, so the asset cannot make its own check tautological.
REQUIRED_SURFACE = {
    "interfaceVersion": "number",
    "view": "function",
    "failure": "function",
    "correct": "function",
    "snapshotCorrection": "function",
    "snapshotPath": "function",
    "safeSnapshotPath": "function",
    "isTerminal": "function",
    "stateLabel": "function",
    "phaseLabel": "function",
    "cancelAcceptedText": "function",
    "handlingText": "function",
    "shouldPoll": "function",
    "childSummary": "function",
    "artifactReadout": "function",
    "states": "object",
    "phases": "object",
}


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page logic cannot be executed")
    return NODE


# ---------------------------------------------------------------------------
# Node invocation (one JSON object on stdout; non-zero exit is a hard failure)
# ---------------------------------------------------------------------------
def run_node(tmp_path: Path, script: str, name: str) -> dict:
    path = tmp_path / name
    path.write_text(script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [NODE, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"node harness {name} failed (exit {completed.returncode}):\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"node harness {name} printed nothing:\n{completed.stderr}"
    payload = json.loads(lines[-1])
    assert isinstance(payload, dict), payload
    payload["_stdout"] = completed.stdout
    return payload


def _web() -> str:
    return json.dumps(str(WEB))


# ---------------------------------------------------------------------------
# Node harness 1: the explicit interface, and the deleted legacy aliases
# ---------------------------------------------------------------------------
INTERFACE_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const required = __REQUIRED__;

const context = { console: console };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
);

const api = context.OrcharaRunState;
const out = {
  has_interface: Boolean(api && typeof api === 'object'),
  interface_version: api ? api.interfaceVersion : null,
  surface: {},
  finalizing: null,
  cancelled: null,
  failure_410: null,
  snapshot_path: null,
  child_summary: null,
};

for (const name of Object.keys(required)) {
  out.surface[name] = api ? typeof api[name] : 'missing';
}
if (api) {
  out.surface.interfaceVersion = api.interfaceVersion;
}
if (api) {
  const finalizing = api.view({ state: 'running', phase: 'finalizing' }, {});
  out.finalizing = {
    label: finalizing.label,
    phase: finalizing.phase,
    terminal: finalizing.terminal,
    isSuccess: finalizing.isSuccess,
    isFailure: finalizing.isFailure,
  };
  const cancelled = api.view({ state: 'cancelled', phase: 'idle' }, {});
  out.cancelled = {
    label: cancelled.label,
    terminal: cancelled.terminal,
    externalWritesNote: cancelled.externalWritesNote,
  };
  const failure = api.failure(410, {
    error: {
      code: 'event_cursor_expired',
      details: { snapshot_url: '/api/v1/runs/run-1' },
    },
  });
  out.failure_410 = { retry: failure.retry, kind: failure.kind };
  out.snapshot_path = api.snapshotPath(failure, 'run-1');
  out.child_summary = api.childSummary(
    [{ id: 'c1', state: 'queued' }],
    { id: 'p1', state: 'waiting_children', phase: 'waiting' }
  );
}

process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_the_explicit_interface_exports_the_required_surface(
    node_binary: str, tmp_path: Path
) -> None:
    script = (
        INTERFACE_HARNESS.replace("__WEB_DIR__", _web())
        .replace("__REQUIRED__", json.dumps(REQUIRED_SURFACE))
    )
    out = run_node(tmp_path, script, "c601-interface.js")

    assert out["has_interface"] is True, out
    assert out["interface_version"] == 1, out
    for name, expected in REQUIRED_SURFACE.items():
        if name == "interfaceVersion":
            assert out["surface"]["interfaceVersion"] == 1, out["surface"]
        else:
            assert out["surface"][name] == expected, (name, out["surface"])
    # The interface delegates to the same committed rules, not a re-implementation.
    assert out["finalizing"] == {
        "label": "正在收尾（finalizing）",
        "phase": "finalizing",
        "terminal": False,
        "isSuccess": False,
        "isFailure": False,
    }, out["finalizing"]
    assert out["cancelled"]["terminal"] is True, out["cancelled"]
    assert (
        "不撤销" in out["cancelled"]["externalWritesNote"]
        or "不表示撤回" in out["cancelled"]["externalWritesNote"]
    ), out["cancelled"]
    assert out["failure_410"] == {"retry": False, "kind": "cursor_expired"}, out
    assert out["snapshot_path"] == "/api/v1/runs/run-1", out
    assert out["child_summary"]["waiting"] == 1, out["child_summary"]
    assert out["child_summary"]["parentMayShowSuccess"] is False, out["child_summary"]


# ---------------------------------------------------------------------------
# Node harness 2: the guard's complete dependency-ready check
# ---------------------------------------------------------------------------
GUARD_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const mode = __MODE__;

function control(id, paused) {
  return {
    id: id,
    paused: paused,
    disabled: false,
    attrs: {},
    dataset: {},
    setAttribute(name, value) { this.attrs[name] = String(value); },
  };
}

const banner = {
  hidden: true,
  textContent: '',
  className: '',
  attrs: {},
  dataset: {},
  setAttribute(name, value) { this.attrs[name] = String(value); },
};
const pausedControls = [
  control('chat-submit', true),
  control('detail-cancel-button', true),
  control('debug-submit', true),
];
const navigation = control('nav-apps', false);
const documentShim = {
  getElementById(id) { return id === 'runstate-status' ? banner : null; },
  querySelectorAll(selector) {
    return selector === '[data-runstate-paused]' ? pausedControls : [];
  },
};

const context = { console: console, document: documentShim };
vm.createContext(context);

let syntaxError = '';
if (mode === 'ready' || mode === 'incomplete') {
  let source = fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8');
  if (mode === 'incomplete') {
    const before = source;
    source = source.replace('artifactReadout: artifactReadout,', '');
    if (source === before) {
      throw new Error('the incomplete-surface mutation did not apply');
    }
  }
  vm.runInContext(source, context, { filename: 'runstream.js' });
} else if (mode === 'syntax') {
  // A classic script with a parse error executes nothing at all: no namespace.
  try {
    vm.runInContext('function ({', context, { filename: 'runstream.js' });
  } catch (error) {
    syntaxError = String(error && error.message ? error.message : error);
  }
}
// mode 'missing' loads no shared script at all (the 404 branch).

const guardSource = fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8');
vm.runInContext(guardSource, context, { filename: 'runstate-guard.js' });

const guard = context.OrcharaRunStateGuard;
const out = {
  mode: mode,
  syntax_error: syntaxError,
  has_guard: Boolean(guard),
  ready: guard ? guard.ready : null,
  reason: guard && guard.status ? guard.status.reason : null,
  missing: guard && guard.status ? guard.status.missing : null,
  message: guard ? guard.MESSAGE : null,
  required: guard ? guard.REQUIRED : null,
  banner: {
    hidden: banner.hidden,
    text: banner.textContent,
    reason: banner.attrs['data-runstate-reason'] || '',
  },
  paused: pausedControls.map((item) => ({
    id: item.id,
    disabled: item.disabled,
    ariaDisabled: item.attrs['aria-disabled'] || '',
    applied: item.attrs['data-runstate-paused-applied'] || '',
  })),
  navigation: {
    disabled: navigation.disabled,
    ariaDisabled: navigation.attrs['aria-disabled'] || '',
  },
};

process.stdout.write(JSON.stringify(out) + '\n');
"""


def _guard_report(tmp_path: Path, mode: str) -> dict:
    script = (
        GUARD_HARNESS.replace("__WEB_DIR__", _web()).replace("__MODE__", json.dumps(mode))
    )
    return run_node(tmp_path, script, f"c601-guard-{mode}.js")


def test_the_guard_requires_exactly_the_declared_surface(node_binary: str, tmp_path: Path) -> None:
    report = _guard_report(tmp_path, "ready")
    declared = {entry["name"]: entry["type"] for entry in report["required"]}
    assert set(declared) == set(REQUIRED_SURFACE), (declared, REQUIRED_SURFACE)
    for name, expected in REQUIRED_SURFACE.items():
        if name == "interfaceVersion":
            assert declared[name] in ("number", "interfaceVersion"), declared[name]
        else:
            assert declared[name] == expected, (name, declared[name])
    assert report["message"] == "运行状态暂不可用", report


def test_a_complete_shared_script_keeps_the_normal_path(
    node_binary: str, tmp_path: Path
) -> None:
    report = _guard_report(tmp_path, "ready")
    assert report["ready"] is True, report
    assert report["reason"] == "ok", report
    assert report["missing"] == [], report
    assert report["banner"]["hidden"] is True, report["banner"]
    assert report["banner"]["text"] == "", report["banner"]
    for item in report["paused"]:
        assert item["disabled"] is False, item
        assert item["applied"] == "", item


def test_a_missing_shared_script_shows_the_explicit_message_and_pauses_controls(
    node_binary: str, tmp_path: Path
) -> None:
    report = _guard_report(tmp_path, "missing")
    assert report["ready"] is False, report
    assert report["reason"] == "missing", report
    assert set(report["missing"]) == set(REQUIRED_SURFACE), report
    assert report["banner"]["hidden"] is False, report["banner"]
    assert report["banner"]["text"].startswith("运行状态暂不可用"), report["banner"]
    assert "404 或语法错误" in report["banner"]["text"], report["banner"]
    assert report["banner"]["reason"] == "missing", report["banner"]
    for item in report["paused"]:
        assert item["disabled"] is True, item
        assert item["ariaDisabled"] == "true", item
        assert item["applied"] == "true", item
    # Navigation is never disabled by the guard.
    assert report["navigation"]["disabled"] is False, report["navigation"]


def test_a_syntax_error_leaves_no_surface_and_takes_the_same_message(
    node_binary: str, tmp_path: Path
) -> None:
    report = _guard_report(tmp_path, "syntax")
    assert report["syntax_error"], report
    assert report["ready"] is False, report
    assert report["reason"] == "missing", report
    assert report["banner"]["text"].startswith("运行状态暂不可用"), report["banner"]
    for item in report["paused"]:
        assert item["disabled"] is True, item


def test_an_incomplete_export_names_the_missing_operations(
    node_binary: str, tmp_path: Path
) -> None:
    report = _guard_report(tmp_path, "incomplete")
    assert report["ready"] is False, report
    assert report["reason"] == "incomplete", report
    assert "artifactReadout" in report["missing"], report["missing"]
    assert report["banner"]["text"].startswith("运行状态暂不可用"), report["banner"]
    assert "未完整导出" in report["banner"]["text"], report["banner"]
    assert "artifactReadout" in report["banner"]["text"], report["banner"]
    for item in report["paused"]:
        assert item["disabled"] is True, item


# ---------------------------------------------------------------------------
# Node harness 3: the chat page never executes the old business fallback when
# the dependency is unavailable (and keeps the shared rule when it is ready).
# ---------------------------------------------------------------------------
CHAT_GATING_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const mode = __MODE__;

const context = { console: console };
vm.createContext(context);
if (mode === 'ready') {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
    context,
    { filename: 'runstream.js' }
  );
}
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'chat.js'), 'utf8'),
  context,
  { filename: 'chat.js' }
);

const finalizing = context.describeHandling(
  { id: 'r1', run_id: 'run-1' }, { state: 'running', phase: 'finalizing' }, null
);
const succeeded = context.describeHandling(
  { id: 'r1', run_id: 'run-1' }, { state: 'succeeded' }, { version_no: 3 }
);
const noApplication = context.describeHandling(null, null, null);

process.stdout.write(JSON.stringify({
  mode: mode,
  finalizing_text: finalizing.text,
  succeeded_text: succeeded.text,
  no_application_text: noApplication.text,
  polls_after_dispatch: context.shouldPollHandling('dispatched', 'running', 0, 100),
  polls_after_terminal: context.shouldPollHandling('dispatched', 'succeeded', 1, 100),
}) + '\n');
"""


def _chat_gating(tmp_path: Path, mode: str) -> dict:
    script = (
        CHAT_GATING_HARNESS.replace("__WEB_DIR__", _web()).replace("__MODE__", json.dumps(mode))
    )
    return run_node(tmp_path, script, f"c601-chat-{mode}.js")


def test_chat_page_fault_mode_does_not_run_the_old_handling_fallback(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_gating(tmp_path, "fault")
    assert report["finalizing_text"] == "运行状态暂不可用", report
    assert report["succeeded_text"] == "运行状态暂不可用", report
    assert report["polls_after_dispatch"] is False, report
    assert report["polls_after_terminal"] is False, report
    # Page-independent copy is untouched.
    assert report["no_application_text"] == "尚无处理应用", report


def test_chat_page_normal_path_keeps_the_shared_handling_rule(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_gating(tmp_path, "ready")
    assert "正在收尾（finalizing）" in report["finalizing_text"], report
    assert report["succeeded_text"] == "v3 已处理", report
    assert report["polls_after_dispatch"] is True, report
    assert report["polls_after_terminal"] is False, report


# C6.02 (#156) removed the "legacy isolated harness" contract this file used to
# pin: chat.js no longer has a page-local three-outcome fallback. The explicit
# namespace usage is asserted by tests/f12/test_c6_02_runstate_view.py, and the
# dependency-failure behaviour stays in the fault-mode tests above.


# ---------------------------------------------------------------------------
# Node harness 4: the runs page stops the detail observation in fault mode and
# keeps it in the normal path.
# ---------------------------------------------------------------------------
RUNS_GATING_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const mode = __MODE__;
const RUN = '11111111-2222-4333-8444-555555555555';

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
  append(child) { this.children.push(child); return child; }
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

const documentShim = {
  getElementById(id) {
    if (id === 'runs-list') return null; // do not auto-start the list page
    if (!byId.has(id)) byId.set(id, element('div', id));
    return byId.get(id);
  },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

// The real runs.html starts with the detail panel hidden.
documentShim.getElementById('run-detail').hidden = true;

const apiFullCalls = [];
const fetchCalls = [];
const timers = [];

const context = {
  console: console,
  document: documentShim,
  TextDecoder: TextDecoder,
  setTimeout(fn) { timers.push(fn); return timers.length; },
  clearTimeout() {},
  window: { location: { assign() {} } },
  api: async (pathname) => { throw new Error('unexpected api ' + pathname); },
  apiFull: async (pathname) => {
    apiFullCalls.push(pathname);
    return {
      payload: {
        id: RUN, state: 'running', phase: 'executing', kind: 'debug', revision: 1,
        execution_snapshot_id: 'snap-1', output_artifact_id: null,
      },
      status: 200,
      headers: { get: () => null },
    };
  },
  fetch: async (pathname) => {
    fetchCalls.push(pathname);
    return {
      ok: false,
      status: 404,
      body: null,
      async json() { return { error: { code: 'resource_not_found', message: 'gone' } }; },
    };
  },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
};

vm.createContext(context);
if (mode === 'ready') {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
    context,
    { filename: 'runstream.js' }
  );
}
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context,
  { filename: 'runs.js' }
);

const flush = async () => {
  for (let i = 0; i < 80; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

(async () => {
  const out = { mode: mode };
  if (mode === 'fault') {
    await context.loadRunDetail(RUN);
    await flush();
    out.detail_api_calls = apiFullCalls.slice();
    out.detail_status = documentShim.getElementById('runs-status').textContent;
    out.detail_hidden = documentShim.getElementById('run-detail').hidden;
    fetchCalls.length = 0;
    context.beginRunTimeline(RUN, {
      listID: 'detail-timeline',
      statusID: 'detail-timeline-status',
    });
    await flush();
    out.timeline_fetch_calls = fetchCalls.slice();
    out.timeline_status = documentShim.getElementById('detail-timeline-status').textContent;
  } else {
    // The normal path keeps polling (the 2s timer stays pending); the assertion
    // is about the requests it starts, so do not await the never-ending poll.
    const pending = context.loadRunDetail(RUN);
    await flush();
    out.detail_api_calls = apiFullCalls.slice();
    out.detail_status = documentShim.getElementById('runs-status').textContent;
    out.detail_hidden = documentShim.getElementById('run-detail').hidden;
    out.detail_state = documentShim.getElementById('detail-state').textContent;
    out.timeline_fetch_calls = fetchCalls.slice();
    void pending;
  }
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def _runs_gating(tmp_path: Path, mode: str) -> dict:
    script = (
        RUNS_GATING_HARNESS.replace("__WEB_DIR__", _web()).replace("__MODE__", json.dumps(mode))
    )
    return run_node(tmp_path, script, f"c601-runs-{mode}.js")


def test_runs_page_fault_mode_pauses_detail_and_timeline_observation(
    node_binary: str, tmp_path: Path
) -> None:
    report = _runs_gating(tmp_path, "fault")
    assert report["detail_api_calls"] == [], report
    assert "运行状态暂不可用" in report["detail_status"], report
    assert report["detail_hidden"] is True, report
    assert report["timeline_fetch_calls"] == [], report
    assert "运行状态暂不可用" in report["timeline_status"], report


def test_runs_page_normal_path_starts_the_detail_observation(
    node_binary: str, tmp_path: Path
) -> None:
    report = _runs_gating(tmp_path, "ready")
    run_path = "/api/v1/runs/11111111-2222-4333-8444-555555555555"
    assert report["detail_api_calls"] == [run_path], report
    assert report["detail_hidden"] is False, report
    assert report["detail_state"] == "running", report


# ---------------------------------------------------------------------------
# Node harness 5: the run-view module refuses to start its tick in fault mode.
# ---------------------------------------------------------------------------
RUNVIEW_GATING_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.hidden = true;
    this.disabled = false;
    this.dataset = {};
    this.attrs = {};
    this.innerHTML = '';
    this.value = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener() {}
  setAttribute(name, value) { this.attrs[name] = String(value); }
}

const byId = new Map();
function element(tag, id) {
  const item = new El(tag, id);
  if (id) byId.set(id, item);
  return item;
}
const documentShim = {
  getElementById(id) {
    if (!byId.has(id)) byId.set(id, element('div', id));
    return byId.get(id);
  },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const apiCalls = [];
const context = {
  console: console,
  document: documentShim,
  window: { location: { assign() {} } },
  api: async (pathname) => { apiCalls.push(pathname); return {}; },
  apiFull: async (pathname) => {
    apiCalls.push(pathname);
    return { payload: { id: 'r1', state: 'running', phase: 'executing' }, status: 200,
      headers: { get: () => null } };
  },
  fetch: async (pathname) => {
    apiCalls.push(pathname);
    return { ok: false, status: 404, async json() { return { items: [] }; } };
  },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
  setTimeout() { return 1; },
  clearTimeout() {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runview.js'), 'utf8'),
  context,
  { filename: 'runview.js' }
);

const view = context.beginRunView({ runID: 'r1', applicationID: 'a1', draftRevision: 1 });
process.stdout.write(JSON.stringify({
  view: view === null ? null : { runID: view.runID },
  api_calls: apiCalls,
  status: documentShim.getElementById('runview-status').textContent,
  panel_hidden: documentShim.getElementById('runview-panel').hidden,
}) + '\n');
"""


def test_runview_module_refuses_to_start_observation_in_fault_mode(
    node_binary: str, tmp_path: Path
) -> None:
    script = RUNVIEW_GATING_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c601-runview-fault.js")
    assert report["view"] is None, report
    assert report["api_calls"] == [], report
    assert "运行状态暂不可用" in report["status"], report
    assert report["panel_hidden"] is False, report


# ===========================================================================
# 2. real served pages: the compiled API process serves the real shared bytes,
#    in the real order, with the banner and paused-control markers.
# ===========================================================================
SHARED_ASSETS = ("runstream.js", "runstate-guard.js")
DEBUG_PAGE_PATH = "/apps/11111111-2222-4333-8444-555555555555/debug"

# page path -> (page script that consumes run state, paused control ids)
PAGE_SPECS = {
    "/chat": ("chat.js", ("chat-submit", "chat-cancel", "interaction-reply")),
    "/runs": ("runs.js", ("detail-cancel-button",)),
    DEBUG_PAGE_PATH: ("debug.js", ("debug-submit", "runview-cancel")),
}


@pytest.fixture(scope="module")
def c601_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> Stack:
    """One real API process over a throwaway migrated database (own DB)."""
    tmp_path = tmp_path_factory.mktemp("c601-stack")
    try:
        database = st.create_database("orchara_c601_")
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


def test_the_shared_assets_are_served_exactly_as_committed(c601_stack) -> None:
    for name in SHARED_ASSETS:
        response = c601_stack.server.http.get(f"/assets/{name}")
        assert response.status == 200, (name, response.text)
        assert "javascript" in response.header("Content-Type"), (
            name,
            response.header("Content-Type"),
        )
        committed = (WEB / "assets" / name).read_text(encoding="utf-8")
        assert response.text == committed, f"{name}: served bytes differ from the committed bytes"


def test_each_page_loads_runstream_then_guard_then_its_page_script(c601_stack) -> None:
    for path, (page_script, paused_ids) in PAGE_SPECS.items():
        body = c601_stack.server.http.get(path).text
        runstream_marker = '<script src="/assets/runstream.js"></script>'
        guard_marker = '<script src="/assets/runstate-guard.js"></script>'
        script_marker = f'<script src="/assets/{page_script}"></script>'
        assert runstream_marker in body, f"{path} does not load the real shared script"
        assert guard_marker in body, f"{path} does not load the dependency guard"
        assert script_marker in body, f"{path} does not load {page_script}"
        assert body.index(runstream_marker) < body.index(guard_marker), path
        assert body.index(guard_marker) < body.index(script_marker), path
        # The explicit failure surface: one banner element, hidden on the normal
        # path, plus the paused-control markers the guard disables on failure.
        assert 'id="runstate-status"' in body, path
        for control_id in paused_ids:
            assert re.search(
                r'<button[^>]*id="' + re.escape(control_id) + r'"[^>]*data-runstate-paused',
                body,
            ), f"{path} does not mark #{control_id} as paused"
        # Navigation is independent of the shared state and never marked paused.
        assert '<nav' in body, path


# ===========================================================================
# 3. real Chrome (Playwright): resource 404 / syntax error / incomplete export.
#    This is browser acceptance, not the Node shim.
# ===========================================================================
FAULT_ROUTES = {
    "404": dict(status=404, content_type="text/html", body="not found"),
    "syntax_error": dict(
        status=200,
        content_type="application/javascript",
        body="function ({ this is not valid javascript",
    ),
    "incomplete_export": dict(
        status=200,
        content_type="application/javascript",
        body=(
            "globalThis.OrcharaRunState = { interfaceVersion: 1,"
            " view: function () { return {}; } };"
        ),
    ),
}

EVIDENCE: list[dict] = []


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


@pytest.fixture(scope="module")
def browser_owner(c601_stack) -> dict:
    owner = t029.golden_owner(c601_stack, login="c601-owner-" + f00b.new_uuid()[:8])
    run = t032.queued_run(c601_stack, owner)
    return {
        "cookie": owner.creator.cookie,
        "application_id": owner.application_id,
        "run_id": run["id"],
    }


@pytest.fixture(scope="module", autouse=True)
def _write_evidence():
    yield
    if os.environ.get("C601_WRITE_EVIDENCE") != "1":
        return
    target = Path(__file__).resolve().parents[2] / "reports" / "architecture"
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "C6.01 (#155)",
        "browser": "playwright channel=chrome headless, real cmd/api over loopback",
        "cases": EVIDENCE,
    }
    (target / "C6.01-browser-evidence.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@contextlib.contextmanager
def _browser_page(browser, stack, cookie, *, fault: dict | None):
    context = browser.new_context()
    context.add_cookies(
        [{"name": st.SESSION_COOKIE, "value": cookie, "url": stack.server.base_url}]
    )
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    if fault is not None:
        page.route("**/assets/runstream.js", lambda route: route.fulfill(**fault))
    try:
        yield page, requests, page_errors
    finally:
        context.close()


def _paths(requests: list[str], base_url: str) -> list[str]:
    return [url[len(base_url):].split("?")[0] for url in requests if url.startswith(base_url)]


def _assert_failure_surface(page, expected_reason: str) -> None:
    banner = page.locator("#runstate-status")
    assert banner.is_visible(), "the explicit dependency-failure banner is not visible"
    assert "运行状态暂不可用" in banner.inner_text(), banner.inner_text()
    assert banner.get_attribute("data-runstate-status") == "unavailable"
    assert banner.get_attribute("data-runstate-reason") == expected_reason


@pytest.mark.parametrize("fault_name", ["404", "syntax_error", "incomplete_export"])
@pytest.mark.parametrize("page_kind", ["runs", "chat", "debug"])
def test_real_browser_dependency_failure_pauses_the_affected_page(
    browser, c601_stack, browser_owner, fault_name: str, page_kind: str
) -> None:
    fault = FAULT_ROUTES[fault_name]
    expected_reason = "incomplete" if fault_name == "incomplete_export" else "missing"
    base = c601_stack.server.base_url
    debug_url = f"{base}/apps/{browser_owner['application_id']}/debug"
    specs = {
        "runs": ("runs.js", ("detail-cancel-button",)),
        "chat": ("chat.js", ("chat-submit", "chat-cancel", "interaction-reply")),
        "debug": ("debug.js", ("debug-submit", "runview-cancel")),
    }
    page_script, paused_ids = specs[page_kind]
    if page_kind == "runs":
        url = f"{base}/runs?run={browser_owner['run_id']}"
    elif page_kind == "chat":
        url = f"{base}/chat"
    else:
        url = debug_url

    case = {
        "page": page_kind,
        "fault": fault_name,
        "expected_reason": expected_reason,
        "url": url.replace(base, ""),
    }
    with _browser_page(browser, c601_stack, browser_owner["cookie"], fault=fault) as (
        page,
        requests,
        page_errors,
    ):
        page.goto(url, wait_until="load")
        page.wait_for_selector("#runstate-status:not([hidden])", timeout=15000)
        _assert_failure_surface(page, expected_reason)

        # Every paused control is disabled by the guard, and marked as applied.
        for control_id in paused_ids:
            control = page.locator("#" + control_id)
            assert control.get_attribute("data-runstate-paused-applied") == "true", (
                control_id,
                page_kind,
            )
            assert control.is_disabled(), (control_id, page_kind)

        paths = _paths(requests, base)
        detail_path = f"/api/v1/runs/{browser_owner['run_id']}"
        assert not any(path == detail_path for path in paths), (
            f"{page_kind}: the detail observation started in dependency-failure mode: {paths}"
        )
        assert not any(path.endswith("/events") for path in paths), paths

        case.update(
            {
                "banner_text": page.locator("#runstate-status").inner_text(),
                "paused_controls": {
                    control_id: {
                        "disabled": page.locator("#" + control_id).is_disabled(),
                        "applied": page.locator("#" + control_id).get_attribute(
                            "data-runstate-paused-applied"
                        ),
                    }
                    for control_id in paused_ids
                },
                "detail_requests": [p for p in paths if p == detail_path],
                "events_requests": [p for p in paths if p.endswith("/events")],
                "page_errors": page_errors,
            }
        )

        if page_kind == "runs":
            # The independent run list is still read (navigation inside the page).
            assert "/api/v1/runs" in paths, paths
            assert page.locator("#run-detail").get_attribute("hidden") is not None
            # Opening the list row shows the paused status; it never fetches the
            # detail in failure mode.
            page.locator("#runs-list button").first.click()
            page.wait_for_timeout(400)
            paths_after_click = _paths(requests, base)
            assert not any(path == detail_path for path in paths_after_click), paths_after_click
            assert "运行状态暂不可用" in page.inner_text("#runs-status")
        elif page_kind == "chat":
            # An independent function is retained: creating a conversation still
            # works while Run state is unavailable.
            page.locator("#conversation-create").click()
            page.wait_for_function(
                "document.getElementById('conversation-status').textContent.indexOf('已创建会话') >= 0",
                timeout=15000,
            )
            assert not any(path.startswith("/api/v1/runs/") for path in _paths(requests, base)), [
                p for p in _paths(requests, base) if p.startswith("/api/v1/runs/")
            ]
        else:
            # The draft revision read is independent and still completes.
            page.wait_for_function(
                "document.getElementById('debug-revision').value !== ''",
                timeout=15000,
            )
            # A forced submit event must be refused by the page adapter, with no
            # debug-run POST (the disabled control is only the first line).
            page.evaluate(
                "document.getElementById('debug-form').dispatchEvent("
                "new Event('submit', {bubbles: true, cancelable: true}))"
            )
            page.wait_for_timeout(400)
            assert not any(
                path.endswith("/debug-runs") for path in _paths(requests, base)
            ), _paths(requests, base)
            assert "运行状态暂不可用" in page.inner_text("#debug-status")

        # Reload is the offered recovery path and keeps the explicit message.
        page.reload(wait_until="load")
        page.wait_for_selector("#runstate-status:not([hidden])", timeout=15000)
        _assert_failure_surface(page, expected_reason)
        case["reload_banner_visible"] = True

        # Navigation is independent: a real nav link still loads another page.
        page.locator('nav a[href="/apps"]').click()
        page.wait_for_url("**/apps")
        assert page.locator("h1").count() >= 1
        case["navigation_after_failure"] = page.url.replace(base, "")
        EVIDENCE.append(case)


def test_real_browser_normal_path_is_unchanged(
    browser, c601_stack, browser_owner
) -> None:
    base = c601_stack.server.base_url
    case = {"page": "normal", "url": "/runs?run=<id>"}
    with _browser_page(browser, c601_stack, browser_owner["cookie"], fault=None) as (
        page,
        requests,
        page_errors,
    ):
        page.goto(f"{base}/runs?run={browser_owner['run_id']}", wait_until="load")
        # The real detail path still reads the Run snapshot and starts the
        # durable timeline (normal state logic preserved).
        page.wait_for_function(
            "document.getElementById('detail-state').textContent !== ''", timeout=15000
        )
        assert page.locator("#runstate-status").is_hidden()
        assert page.locator("#detail-cancel-button").get_attribute(
            "data-runstate-paused-applied"
        ) is None
        paths = _paths(requests, base)
        assert f"/api/v1/runs/{browser_owner['run_id']}" in paths, paths
        assert any(path.endswith("/events") for path in paths), paths
        assert page.inner_text("#detail-state") in ("queued", "running", "succeeded", "failed", "cancelled", "waiting_children", "waiting_approval", "cancelling", "recovery_pending", "needs_reconciliation")
        assert page.locator("#detail-note").inner_text() != ""
        case["detail_state"] = page.inner_text("#detail-state")
        case["detail_note"] = page.inner_text("#detail-note")
        case["page_errors"] = page_errors

        # The other two pages keep the normal path too: no banner, controls live.
        page.goto(f"{base}/chat", wait_until="load")
        page.wait_for_timeout(300)
        assert page.locator("#runstate-status").is_hidden()
        assert not page.locator("#chat-submit").is_disabled()
        page.goto(
            f"{base}/apps/{browser_owner['application_id']}/debug", wait_until="load"
        )
        page.wait_for_function(
            "document.getElementById('debug-revision').value !== ''", timeout=15000
        )
        assert page.locator("#runstate-status").is_hidden()
        assert not page.locator("#debug-submit").is_disabled()
        EVIDENCE.append(case)

