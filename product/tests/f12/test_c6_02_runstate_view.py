"""C6.02 (#156) chat/runview migrate onto the explicit ``OrcharaRunState`` view.

手段声明（硬性，先读）
--------------------
本文件把三类证据分开记录，互不冒充：

1. **Node 行为测试**：执行 ``apps/web/src/assets/`` 里**已提交的真实字节**
   （``runstream.js``、``runstate-guard.js``、``chat.js``、``runview.js``）与最小
   DOM/fetch shim（先例 ``tests/f12/test_t096_pages.py`` / C6.01）。它证明真实脚本
   字节、真实 adapter 行为与被测 interface 的调用关系，**不是浏览器 DOM**。
2. **真实服务检查**：编译后的 ``cmd/api`` 进程经 loopback socket 提供页面与静态资源；
   断言实际共享字节与脚本顺序。
3. **真实浏览器（Playwright + 已安装 Chrome，headless）**：真实页面提交一个真实
   queued debug Run，观察 runview 的 Run 终态/取消规则、真实轮询与提示；与 Node 隔离
   结果分开记录。

被测 seam（票面已指定，不测私有 helper）：
``chat.js`` 的 handling/poll/terminal 规则、``runview.js`` 的 Run terminal/cancel
规则，以及两页在共享依赖缺失时的 Q7 暂停行为。节点表、节点专用错误解释、DOM adapter
仍属页面职责，不在本票迁入共享 module。
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

from support import f00b, stack as st, t024, t029
from support.certs import make_stack_certificates, unique_internal_addr
from support.runtime_stack import Stack
from support.stack import WEB_DIR

WEB = WEB_DIR
NODE = shutil.which("node")

A = "/api/v1/requests/" + "11111111-1111-4111-8111-111111111111"
B = "/api/v1/requests/" + "22222222-2222-4222-8222-222222222222"
RUN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
RUN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

# C6.04 (#158) deleted the old top-level exports. The token is assembled so the
# deletion proof does not leave the legacy name in the repository-wide search.
_LEGACY_STEM = "run" + "Stream"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page logic cannot be executed")
    return NODE


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
# 1. chat: the explicit namespace, not a page-local fallback
# ---------------------------------------------------------------------------
CHAT_WITNESS_HARNESS = r"""
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
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
// Witness: after the real shared bytes load, replace the declared namespace with a
// recording one. A page that calls the explicit interface sees the witness; a page
// that still calls the old top-level globals does not.
context.OrcharaRunState = {
  interfaceVersion: 1,
  handlingText: (run, versionNo) =>
    'WITNESS handling v' + versionNo + ' state=' + (run && run.state),
  shouldPoll: (requestState, runState, attempts, maxAttempts) =>
    'WITNESS poll ' + requestState + '/' + runState + '/' + attempts + '/' + maxAttempts,
  isTerminal: (state) => 'WITNESS terminal ' + state,
};
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'chat.js'), 'utf8'),
  context,
  { filename: 'chat.js' }
);

const handling = context.describeHandling(
  { id: 'r1', run_id: 'run-1' }, { state: 'running', phase: 'finalizing' }, { version_no: 7 }
);
process.stdout.write(JSON.stringify({
  handling_text: handling.text,
  poll: context.shouldPollHandling('dispatched', 'running', 3, 100),
  // A request without a Run never asks the shared view to invent one.
  no_run_text: context.describeHandling({ id: 'r2', run_id: 'run-2' }, null, null).text,
  no_application_text: context.describeHandling(null, null, null).text,
}) + '\n');
"""


def test_chat_reads_run_state_through_the_explicit_namespace(
    node_binary: str, tmp_path: Path
) -> None:
    script = CHAT_WITNESS_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-chat-witness.js")
    assert report["handling_text"] == "WITNESS handling v7 state=running", report
    assert report["poll"] == "WITNESS poll dispatched/running/3/100", report
    # The page keeps its own "snapshot not read yet" copy; it never invents state.
    assert report["no_run_text"] == "处理版本 处理状态未知（尚未读到 Run 快照）", report
    assert report["no_application_text"] == "尚无处理应用", report


CHAT_RULES_HARNESS = r"""
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
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
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

function handling(run) {
  return context.describeHandling({ id: 'r1', run_id: 'run-1' }, run, { version_no: 4 }).text;
}

const out = { handling: {}, poll: {} };
for (const run of [
  { state: 'queued', phase: 'idle' },
  { state: 'running', phase: 'executing' },
  { state: 'running', phase: 'finalizing' },
  { state: 'succeeded', phase: 'idle' },
  { state: 'failed', phase: 'idle' },
  { state: 'cancelled', phase: 'idle' },
  { state: 'cancelling', phase: 'executing' },
  { state: 'waiting_children', phase: 'waiting' },
  { state: 'waiting_approval', phase: 'waiting' },
  { state: 'recovery_pending', phase: 'idle' },
  { state: 'needs_reconciliation', phase: 'idle' },
]) {
  out.handling[run.state + '/' + run.phase] = handling(run);
}
out.poll = {
  dispatched_queued: context.shouldPollHandling('dispatched', 'queued', 0, 100),
  dispatched_running: context.shouldPollHandling('dispatched', 'running', 5, 100),
  dispatched_finalizing_state: context.shouldPollHandling('dispatched', 'running', 6, 100),
  dispatched_succeeded: context.shouldPollHandling('dispatched', 'succeeded', 7, 100),
  dispatched_failed: context.shouldPollHandling('dispatched', 'failed', 7, 100),
  dispatched_cancelled: context.shouldPollHandling('dispatched', 'cancelled', 7, 100),
  dispatched_without_run: context.shouldPollHandling('dispatched', '', 0, 100),
  queued_first: context.shouldPollHandling('queued', '', 0, 100),
  queued_at_cap: context.shouldPollHandling('queued', '', 100, 100),
  request_cancelled: context.shouldPollHandling('cancelled', 'running', 0, 100),
  request_failed: context.shouldPollHandling('failed', 'running', 0, 100),
};
process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_chat_handling_covers_the_contract_states_without_claiming_an_outcome(
    node_binary: str, tmp_path: Path
) -> None:
    script = CHAT_RULES_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-chat-rules.js")
    handling = report["handling"]

    assert handling["queued/idle"] == "v4 正在处理", handling
    assert handling["running/executing"] == "v4 正在处理", handling
    # finalizing is a phase, not a terminal: never "已处理".
    finalizing = handling["running/finalizing"]
    assert "正在收尾（finalizing）" in finalizing, handling
    assert "已处理" not in finalizing, handling
    assert "尚未成功" in finalizing, handling
    assert handling["succeeded/idle"] == "v4 已处理", handling
    assert handling["failed/idle"] == "v4 处理失败", handling
    assert handling["cancelled/idle"] == "v4 已取消", handling
    # A cancel is intent until the Run reports stopped.
    cancelling = handling["cancelling/executing"]
    assert "正在停止（cancelling）" in cancelling, handling
    assert "不会撤回" in cancelling, handling
    assert "已取消" not in cancelling, handling
    assert "子任务" in handling["waiting_children/waiting"], handling
    assert "等待审批（waiting_approval）" in handling["waiting_approval/waiting"], handling
    assert "等待恢复（recovery_pending）" in handling["recovery_pending/idle"], handling
    # Unknown external writes wait for reconciliation; never success or failure.
    reconciliation = handling["needs_reconciliation/idle"]
    assert "等待人工核对（needs_reconciliation）" in reconciliation, handling
    assert "不给出成功结论" in reconciliation, handling
    assert "已处理" not in reconciliation and "处理失败" not in reconciliation, handling


def test_chat_poll_rule_continues_after_dispatched_and_keeps_the_cap(
    node_binary: str, tmp_path: Path
) -> None:
    script = CHAT_RULES_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-chat-poll.js")
    poll = report["poll"]
    # A dispatched Request is stable; its Run is not. The page keeps observing it
    # through queued/running/finalizing and stops only at a committed terminal.
    assert poll["dispatched_queued"] is True, poll
    assert poll["dispatched_running"] is True, poll
    assert poll["dispatched_finalizing_state"] is True, poll
    assert poll["dispatched_without_run"] is True, poll
    assert poll["dispatched_succeeded"] is False, poll
    assert poll["dispatched_failed"] is False, poll
    assert poll["dispatched_cancelled"] is False, poll
    assert poll["queued_first"] is True, poll
    # The cap is the existing one (100); it is not expanded by this migration.
    assert poll["queued_at_cap"] is False, poll
    assert poll["request_cancelled"] is False, poll
    assert poll["request_failed"] is False, poll


# ---------------------------------------------------------------------------
# 2. runview: the explicit namespace, not a page-local terminal dictionary
# ---------------------------------------------------------------------------
RUNVIEW_WITNESS_HARNESS = r"""
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
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
// Witness: the page's Run classification must come from this namespace, not from
// its own terminal dictionary. The witness answers differently on purpose.
context.OrcharaRunState = {
  interfaceVersion: 1,
  view: (run) => ({
    state: 'running',
    phase: 'finalizing',
    stage: 'finalizing',
    label: 'WITNESS',
    terminal: false,
    isSuccess: false,
    isFailure: false,
    canCancel: true,
    cancelLabel: 'WITNESS cancel label',
    externalWritesNote: '',
  }),
  isTerminal: (state) => state === 'queued',
  cancelAcceptedText: (run) => 'WITNESS receipt state=' + (run && run.state),
};
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runview.js'), 'utf8'),
  context,
  { filename: 'runview.js' }
);

const error = context.formatRunError({ state: 'queued', error_code: '' });
const modelQueued = context.buildRunViewModel({ nodes: [], edges: [] }, {}, { items: [] }, {}, { state: 'queued' });
const modelRunning = context.buildRunViewModel({ nodes: [], edges: [] }, {}, { items: [] }, {}, { state: 'running' });
process.stdout.write(JSON.stringify({
  error_text: error.text,
  error_terminal: error.terminal,
  can_cancel_succeeded: context.runViewCanCancel({ state: 'succeeded' }),
  label: context.runViewCancelLabel({ state: 'cancelled' }),
  queued_run_terminal: modelQueued.runTerminal,
  running_run_terminal: modelRunning.runTerminal,
}) + '\n');
"""


def test_runview_reads_run_rules_through_the_explicit_namespace(
    node_binary: str, tmp_path: Path
) -> None:
    script = RUNVIEW_WITNESS_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-runview-witness.js")
    # The witness says "running/finalizing" even though the input Run is queued:
    # the page must show the shared view's stage, not its own state branch.
    assert "正在收尾（phase=finalizing）" in report["error_text"], report
    assert report["error_terminal"] is False, report
    # canCancel/cancelLabel come from the shared view, not a page dictionary.
    assert report["can_cancel_succeeded"] is True, report
    assert report["label"] == "WITNESS cancel label", report
    # The node table's terminal flag also comes from the shared rule: `queued` is
    # terminal only because the witness says so.
    assert report["queued_run_terminal"] is True, report
    assert report["running_run_terminal"] is False, report


RUNVIEW_RULES_HARNESS = r"""
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
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
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

const out = { errors: {}, cancel: {} };
for (const run of [
  { state: 'queued', phase: 'idle' },
  { state: 'running', phase: 'executing' },
  { state: 'running', phase: 'finalizing' },
  { state: 'succeeded', phase: 'idle' },
  { state: 'failed', phase: 'idle', error_code: 'invalid_activation' },
  { state: 'failed', phase: 'idle', error_code: 'brand_new_code' },
  { state: 'failed', phase: 'idle' },
  { state: 'cancelled', phase: 'idle' },
  { state: 'cancelling', phase: 'executing' },
  { state: 'recovery_pending', phase: 'idle' },
  { state: 'needs_reconciliation', phase: 'idle' },
]) {
  out.errors[run.state + '/' + run.phase + '/' + (run.error_code || '')] =
    context.formatRunError(run);
}
out.errors['none'] = context.formatRunError(null);
for (const state of ['queued', 'running', 'cancelling', 'succeeded', 'failed', 'cancelled', '']) {
  out.cancel[state] = {
    can: context.runViewCanCancel(state === '' ? null : { state: state }),
    label: context.runViewCancelLabel(state === '' ? null : { state: state }),
  };
}
process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_runview_run_error_keeps_node_hints_and_shared_terminal_states(
    node_binary: str, tmp_path: Path
) -> None:
    script = RUNVIEW_RULES_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-runview-rules.js")
    errors = report["errors"]

    assert errors["succeeded/idle/"]["terminal"] is True, errors
    assert errors["succeeded/idle/"]["severity"] == "ok", errors
    failed = errors["failed/idle/invalid_activation"]
    assert failed["terminal"] is True, failed
    assert failed["severity"] == "bad", failed
    # Node-specific error explanation stays here (not moved to the shared module).
    assert "没有已激活的入边" in failed["hint"], failed
    assert "invalid_activation" in failed["text"], failed
    unknown_code = errors["failed/idle/brand_new_code"]
    assert unknown_code["hint"] == "", unknown_code  # no invented explanation
    assert "brand_new_code" in unknown_code["text"], unknown_code
    no_code = errors["failed/idle/"]
    assert "未给出 error_code" in no_code["text"], no_code
    cancelled = errors["cancelled/idle/"]
    assert cancelled["terminal"] is True, cancelled
    assert "已取消（cancelled）" in cancelled["text"], cancelled
    assert "不表示撤回" in cancelled["text"], cancelled

    # finalizing is phase-only: not terminal, not success, not failure.
    finalizing = errors["running/finalizing/"]
    assert finalizing["terminal"] is False, finalizing
    assert finalizing["severity"] == "pending", finalizing
    assert "finalizing" in finalizing["text"], finalizing
    assert "不是成功" in finalizing["text"], finalizing
    # A cancel intent is not a stopped Run.
    cancelling = errors["cancelling/executing/"]
    assert cancelling["terminal"] is False, cancelling
    assert "取消意图（cancelling）" in cancelling["text"], cancelling
    assert "不表示撤回" in cancelling["text"], cancelling
    # Unknown external writes wait for reconciliation; no outcome is claimed.
    reconciliation = errors["needs_reconciliation/idle/"]
    assert reconciliation["terminal"] is False, reconciliation
    assert "人工核对（needs_reconciliation）" in reconciliation["text"], reconciliation
    assert "成功" not in reconciliation["text"].replace("不显示为成功", ""), reconciliation
    assert "失败" not in reconciliation["text"].replace("或失败", ""), reconciliation
    assert "recovery_pending" in errors["recovery_pending/idle/"]["text"], errors
    assert errors["none"]["terminal"] is False, errors["none"]
    assert "不会在没有服务端事实时显示成功或失败" in errors["none"]["text"], errors["none"]


def test_runview_cancel_rules_match_the_shared_view(node_binary: str, tmp_path: Path) -> None:
    script = RUNVIEW_RULES_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-runview-cancel.js")
    cancel = report["cancel"]
    for state in ("queued", "running", "cancelling"):
        assert cancel[state]["can"] is True, (state, cancel)
    for state in ("succeeded", "failed", "cancelled"):
        assert cancel[state]["can"] is False, (state, cancel)
    assert cancel[""]["can"] is False, cancel
    assert cancel["cancelling"]["label"] == "再次请求取消（幂等）", cancel
    assert cancel["running"]["label"] == "取消 Run", cancel


# ---------------------------------------------------------------------------
# 3. deletion: no page-local fallback survives; no legacy global survives (#158)
# ---------------------------------------------------------------------------
def test_the_two_migrated_pages_have_no_page_local_run_state_fallback() -> None:
    chat = (WEB / "assets" / "chat.js").read_text(encoding="utf-8")
    runview = (WEB / "assets" / "runview.js").read_text(encoding="utf-8")
    for name, source in (("chat.js", chat), ("runview.js", runview)):
        # C6.04: the old `typeof <legacy> === 'function'` fallbacks are gone.
        assert ("typeof " + _LEGACY_STEM) not in source, (
            f"{name} still branches on the legacy globals"
        )
        assert "OrcharaRunState." in source, f"{name} does not call the explicit namespace"
    # The page-local Run terminal dictionary is gone; terminal comes from the view.
    assert "RUNVIEW_TERMINAL_STATES" not in runview
    assert "OrcharaRunState.isTerminal(" in runview
    # The dialog page consumes the shared handling/poll/terminal rules.
    for call in (
        "OrcharaRunState.handlingText(",
        "OrcharaRunState.shouldPoll(",
        "OrcharaRunState.isTerminal(",
    ):
        assert call in chat, call
    # C6.04 removed the temporary globals: runstream.js only defines the explicit
    # frozen namespace, and the legacy names are not function declarations there.
    runstream = (WEB / "assets" / "runstream.js").read_text(encoding="utf-8")
    assert ("function " + _LEGACY_STEM) not in runstream
    assert "global.OrcharaRunState = Object.freeze({" in runstream


def test_the_changed_page_scripts_parse_with_node(node_binary: str, tmp_path: Path) -> None:
    for name in ("runstream.js", "runstate-guard.js", "chat.js", "runview.js"):
        completed = subprocess.run(
            [node_binary, "--check", str(WEB / "assets" / name)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, (name, completed.stderr)


# ---------------------------------------------------------------------------
# 4. dependency failure (Q7): the pages pause, never fall back to page rules
# ---------------------------------------------------------------------------
CHAT_FAULT_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const context = { console: console };
vm.createContext(context);
// No shared script at all (404 / syntax error), like the real failure mode.
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
process.stdout.write(JSON.stringify({
  ready: context.OrcharaRunStateGuard.ready,
  handling: context.describeHandling(
    { id: 'r1', run_id: 'run-1' }, { state: 'running', phase: 'finalizing' }, { version_no: 3 }
  ).text,
  polls: context.shouldPollHandling('dispatched', 'running', 0, 100),
}) + '\n');
"""


def test_chat_pauses_when_the_shared_dependency_is_unavailable(
    node_binary: str, tmp_path: Path
) -> None:
    script = CHAT_FAULT_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-chat-fault.js")
    assert report["ready"] is False, report
    assert report["handling"] == "运行状态暂不可用", report
    assert report["polls"] is False, report


RUNVIEW_FAULT_HARNESS = r"""
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
  }
  appendChild(child) { this.children.push(child); return child; }
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
const calls = [];
const context = {
  console: console,
  document: {
    getElementById(id) {
      if (!byId.has(id)) byId.set(id, element('div', id));
      return byId.get(id);
    },
    createElement(tag) { return element(tag); },
  },
  window: { location: { assign() {} } },
  api: async (pathname) => { calls.push(pathname); return {}; },
  apiFull: async (pathname) => {
    calls.push(pathname);
    return { payload: {}, status: 202, headers: { get: () => null } };
  },
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

(async () => {
  const view = context.beginRunView({ runID: 'r1', applicationID: 'a1', draftRevision: 1 });
  const cancelled = await context.runViewCancel();
  process.stdout.write(JSON.stringify({
    view: view === null ? null : { runID: view.runID },
    cancel: cancelled,
    api_calls: calls,
    status: context.document.getElementById('runview-status').textContent,
    cancel_status: context.document.getElementById('runview-cancel-status').textContent,
    panel_hidden: context.document.getElementById('runview-panel').hidden,
  }) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def test_runview_pauses_when_the_shared_dependency_is_unavailable(
    node_binary: str, tmp_path: Path
) -> None:
    script = RUNVIEW_FAULT_HARNESS.replace("__WEB_DIR__", _web())
    report = run_node(tmp_path, script, "c602-runview-fault.js")
    assert report["view"] is None, report
    assert report["cancel"] is None, report
    assert report["api_calls"] == [], report
    assert "运行状态暂不可用" in report["status"], report
    assert "运行状态暂不可用" in report["cancel_status"], report
    assert report["panel_hidden"] is False, report


# ---------------------------------------------------------------------------
# 5. chat DOM adapter: the real poll loop, its cap and the selection generation
# ---------------------------------------------------------------------------
CHAT_DOM_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const MODE = __MODE__;

const CONV = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
const REQ_A = '11111111-1111-4111-8111-111111111111';
const REQ_B = '22222222-2222-4222-8222-222222222222';
const RUN_A = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const RUN_B = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';

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
    this.checked = false;
    this.value = '';
    this.dataset = {};
    this.attrs = {};
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...nodes) { for (const node of nodes) this.appendChild(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  removeEventListener() {}
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
    if (!byId.has(id)) byId.set(id, element('div', id));
    return byId.get(id);
  },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll(selector) {
    if (selector === '#chat-messages li') {
      const container = byId.get('chat-messages');
      return container ? container.children.filter((row) => row.tagName === 'LI') : [];
    }
    return [];
  },
};

function fakeHeaders(values) {
  const map = values || {};
  return { get(name) { return Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null; } };
}

const POLL_SEQUENCE = [
  { id: RUN_A, state: 'queued', phase: 'idle' },
  { id: RUN_A, state: 'running', phase: 'executing' },
  { id: RUN_A, state: 'running', phase: 'finalizing' },
  { id: RUN_A, state: 'succeeded', phase: 'idle' },
];

function requestSnapshot(id) {
  if (id === REQ_A) {
    return {
      id: REQ_A, state: MODE === 'late_request' ? 'cancelled' : 'dispatched',
      run_id: RUN_A, interaction: null,
    };
  }
  return { id: REQ_B, state: 'cancelled', run_id: RUN_B, interaction: null };
}

let releaseFirstA = null;
let releasePollA = null;
const firstA = new Promise((resolve) => { releaseFirstA = resolve; });
const pollA = new Promise((resolve) => { releasePollA = resolve; });

const timers = [];
const requestReads = { A: 0, B: 0 };
const runReads = { A: 0, B: 0 };
const runStates = { A: [], B: [] };

const context = {
  console: console,
  document: documentShim,
  crypto: { randomUUID: () => 'uuid-' + Math.random().toString(16).slice(2) },
  window: { location: { assign() {} } },
  setTimeout(fn) { timers.push(fn); return timers.length; },
  clearTimeout() {},
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
  api: async (pathname) => {
    if (pathname === '/api/v1/conversations') {
      return { items: [{ id: CONV, created_at: '2026-01-01T00:00:00Z' }] };
    }
    if (pathname === '/api/v1/conversations/' + CONV + '/messages') {
      // listMessages is newest first; the page reverses it.
      return {
        items: [
          { id: 'm2', role: 'user', kind: 'request', request_id: REQ_B, created_at: '2' },
          { id: 'm1', role: 'user', kind: 'request', request_id: REQ_A, created_at: '1' },
        ],
      };
    }
    const request = pathname.match(/^\/api\/v1\/requests\/([^/]+)$/);
    if (request) {
      return requestSnapshot(request[1]);
    }
    const run = pathname.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (run) {
      const id = run[1];
      const key = id === RUN_A ? 'A' : 'B';
      const index = runReads[key];
      runReads[key] += 1;
      let snapshot;
      if (MODE === 'poll') {
        snapshot = POLL_SEQUENCE[Math.min(index, POLL_SEQUENCE.length - 1)];
      } else if (MODE === 'cap') {
        snapshot = { id: RUN_A, state: 'running', phase: 'executing' };
      } else if (id === RUN_A) {
        snapshot = MODE === 'late_request'
          ? { id: RUN_A, state: 'running', phase: 'finalizing' }
          : { id: RUN_A, state: 'running', phase: 'executing' };
      } else {
        snapshot = { id: RUN_B, state: 'succeeded', phase: 'idle' };
      }
      runStates[key].push(snapshot.state + '/' + snapshot.phase);
      return snapshot;
    }
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    const request = pathname.match(/^\/api\/v1\/requests\/([^/]+)$/);
    if (!request) {
      throw new Error('unexpected apiFull ' + pathname);
    }
    const id = request[1];
    const key = id === REQ_A ? 'A' : 'B';
    const index = requestReads[key];
    requestReads[key] += 1;
    if (MODE === 'late_request' && key === 'A' && index === 0) {
      await firstA;
    }
    if (MODE === 'late_poll' && key === 'A' && index === 1) {
      await pollA;
    }
    return {
      payload: requestSnapshot(id),
      headers: fakeHeaders({ 'X-Request-Application-Version-No': key === 'A' ? '1' : '2' }),
    };
  },
};

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
);
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
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

const flush = async () => {
  for (let i = 0; i < 200; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};
const drainTimers = async (limit) => {
  let guard = 0;
  while (timers.length > 0 && guard < limit) {
    const timer = timers.shift();
    timer();
    await flush();
    guard += 1;
  }
  return guard;
};

(async () => {
  const out = { mode: MODE };
  await flush();
  const list = documentShim.getElementById('conversation-list');
  if (!list.children.length) {
    throw new Error('conversation list was not rendered');
  }
  list.children[0].children[0].click();
  await flush();

  const queue = documentShim.getElementById('queue-list');
  const rowA = queue.children.find((row) => row.dataset.requestId === REQ_A);
  const rowB = queue.children.find((row) => row.dataset.requestId === REQ_B);

  if (MODE === 'poll') {
    // selectConversation auto-resumed the dispatched request A; each timer is
    // one poll turn whose Run snapshot moves queued -> running -> finalizing ->
    // succeeded.
    out.executed_timers = await drainTimers(200);
    out.run_reads = runReads.A;
    out.run_states = runStates.A.slice();
    out.request_reads = requestReads.A;
    out.status = documentShim.getElementById('chat-submit-status').textContent;
    out.timers_left = timers.length;
  } else if (MODE === 'cap') {
    out.executed_timers = await drainTimers(400);
    out.run_reads = runReads.A;
    out.request_reads = requestReads.A;
    out.timers_left = timers.length;
    out.status = documentShim.getElementById('chat-submit-status').textContent;
  } else if (MODE === 'late_request') {
    rowA.children[0].click();
    await flush();
    out.pending_after_a = requestReads.A;
    rowB.children[0].click();
    await flush();
    out.panel_before_late = documentShim.getElementById('chat-request-id').textContent;
    releaseFirstA();
    await flush();
    out.request_id = documentShim.getElementById('chat-request-id').textContent;
    out.handling = documentShim.getElementById('chat-handling').textContent;
    out.handling_state = documentShim.getElementById('chat-handling').dataset.runState;
    out.request_reads = { a: requestReads.A, b: requestReads.B };
  } else if (MODE === 'late_poll') {
    // The auto-resumed A already rendered and is waiting on its poll read.
    out.pending_poll = requestReads.A;
    rowB.children[0].click();
    await flush();
    out.panel_before_late = documentShim.getElementById('chat-request-id').textContent;
    releasePollA();
    await flush();
    out.request_id = documentShim.getElementById('chat-request-id').textContent;
    out.handling = documentShim.getElementById('chat-handling').textContent;
    out.handling_state = documentShim.getElementById('chat-handling').dataset.runState;
    out.request_reads = { a: requestReads.A, b: requestReads.B };
    out.status = documentShim.getElementById('chat-submit-status').textContent;
  } else {
    throw new Error('unknown mode ' + MODE);
  }
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def _chat_dom(tmp_path: Path, mode: str) -> dict:
    script = CHAT_DOM_HARNESS.replace("__WEB_DIR__", _web()).replace("__MODE__", json.dumps(mode))
    return run_node(tmp_path, script, f"c602-chat-dom-{mode}.js")


def test_chat_poll_loop_observes_a_dispatched_run_until_it_is_terminal(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_dom(tmp_path, "poll")
    # queued -> running -> running(finalizing) -> succeeded: four Run reads.
    assert report["run_states"] == [
        "queued/idle",
        "running/executing",
        "running/finalizing",
        "succeeded/idle",
    ], report
    assert report["run_reads"] == 4, report
    assert report["request_reads"] == 4, report
    # The initial read + the direct poll(0) turn + two timer turns: the last one
    # sees succeeded and schedules nothing.
    assert report["executed_timers"] == 2, report
    assert report["timers_left"] == 0, report
    assert report["status"] == "Run 已进入终态 succeeded。", report


def test_chat_poll_loop_stops_at_the_existing_cap_not_later(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_dom(tmp_path, "cap")
    # attempts 0..99 are polled and each schedules the next turn; attempts == 100
    # stops. The cap was 100 before this migration and stays 100.
    assert report["executed_timers"] == 100, report
    # initial read + poll(0) + the 100 timer turns (attempts 1..100).
    assert report["run_reads"] == 102, report
    assert report["request_reads"] == 102, report
    assert report["timers_left"] == 0, report


def test_a_late_request_read_does_not_override_a_newer_selection(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_dom(tmp_path, "late_request")
    assert report["pending_after_a"] == 1, report
    assert report["panel_before_late"] == "22222222-2222-4222-8222-222222222222", report
    assert report["request_id"] == "22222222-2222-4222-8222-222222222222", report
    # B's Run succeeded; the late A snapshot (running/finalizing) is not rendered.
    assert report["handling_state"] == "succeeded", report
    assert "已处理" in report["handling"], report
    assert "收尾" not in report["handling"], report


def test_a_late_poll_read_does_not_override_a_newer_selection(
    node_binary: str, tmp_path: Path
) -> None:
    report = _chat_dom(tmp_path, "late_poll")
    assert report["panel_before_late"] == "22222222-2222-4222-8222-222222222222", report
    assert report["request_id"] == "22222222-2222-4222-8222-222222222222", report
    assert report["handling_state"] == "succeeded", report
    # The late poll for A is dropped and does not record A's Run outcome.
    assert "Run 已进入终态" not in report["status"], report
    assert report["request_reads"]["a"] == 2, report


# ---------------------------------------------------------------------------
# 6. runview DOM adapter: cancel receipt is intent until the Run says stopped
# ---------------------------------------------------------------------------
RUNVIEW_DOM_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const MODE = __MODE__;

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = true;
    this.disabled = false;
    this.dataset = {};
    this.attrs = {};
    this.value = '';
  }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
}

const byId = new Map();
function element(tag, id) {
  const item = new El(tag, id);
  if (id) byId.set(id, item);
  return item;
}
for (const id of [
  'runview-panel', 'runview-status', 'runview-run-state', 'runview-error',
  'runview-summary', 'run-node-rows', 'runview-cancel', 'runview-cancel-status',
]) {
  element('div', id);
}
byId.get('runview-cancel').disabled = true;

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

let releaseCancel = null;
const cancelGate = new Promise((resolve) => { releaseCancel = resolve; });
const runs = {
  r1: { id: 'r1', state: 'running', phase: 'executing' },
  r2: { id: 'r2', state: 'queued', phase: 'idle' },
};
const cancelPayload = {
  intent: { id: 'r1', state: 'cancelling', phase: 'executing' },
  stopped: { id: 'r1', state: 'cancelled', phase: 'idle' },
  stale: { id: 'r1', state: 'cancelling', phase: 'executing' },
};

const context = {
  console: console,
  document: documentShim,
  window: { location: { assign() {} } },
  setTimeout() { return 1; },
  clearTimeout() {},
  csrf: async () => 'csrf',
  api: async (pathname) => {
    const run = pathname.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (run) {
      return runs[run[1]];
    }
    if (pathname.endsWith('/node-results')) {
      return { items: [] };
    }
    if (pathname.endsWith('/draft')) {
      return { definition: { nodes: [], edges: [] } };
    }
    throw new Error('unexpected api ' + pathname);
  },
  apiFull: async (pathname) => {
    if (!/\/cancel$/.test(pathname)) {
      throw new Error('unexpected apiFull ' + pathname);
    }
    if (MODE === 'stale') {
      await cancelGate;
    }
    return {
      payload: cancelPayload[MODE],
      headers: { get: () => null },
    };
  },
};

vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
);
// C6.04 (#158): the guard is part of the real page load order and its one
// readiness fact is what the page scripts consult.
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

const flush = async () => {
  for (let i = 0; i < 200; i += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
};

(async () => {
  const out = { mode: MODE };
  const first = context.beginRunView({ runID: 'r1', applicationID: 'a1', draftRevision: 1 });
  await flush();
  out.first_state = documentShim.getElementById('runview-run-state').textContent;
  if (MODE === 'stale') {
    const pending = context.runViewCancel();
    await flush();
    out.first_cancel_pending = true;
    context.beginRunView({ runID: 'r2', applicationID: 'a1', draftRevision: 1, run: runs.r2 });
    await flush();
    out.status_after_new_selection = documentShim.getElementById('runview-cancel-status').textContent;
    releaseCancel();
    await pending;
    await flush();
    out.status_after_late = documentShim.getElementById('runview-cancel-status').textContent;
    out.run_state = documentShim.getElementById('runview-run-state').textContent;
    out.cancel_label = documentShim.getElementById('runview-cancel').textContent;
    out.cancel_disabled = documentShim.getElementById('runview-cancel').disabled;
  } else {
    const run = await context.runViewCancel();
    await flush();
    out.returned_state = run && run.state;
    out.cancel_status = documentShim.getElementById('runview-cancel-status').textContent;
    out.error_text = documentShim.getElementById('runview-error').textContent;
    out.run_state = documentShim.getElementById('runview-run-state').textContent;
    out.cancel_label = documentShim.getElementById('runview-cancel').textContent;
    out.cancel_disabled = documentShim.getElementById('runview-cancel').disabled;
    out.first = first === null ? null : first.runID;
  }
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def _runview_dom(tmp_path: Path, mode: str) -> dict:
    script = RUNVIEW_DOM_HARNESS.replace("__WEB_DIR__", _web()).replace(
        "__MODE__", json.dumps(mode)
    )
    return run_node(tmp_path, script, f"c602-runview-dom-{mode}.js")


def test_runview_cancel_receipt_is_intent_until_the_run_reports_stopped(
    node_binary: str, tmp_path: Path
) -> None:
    intent = _runview_dom(tmp_path, "intent")
    assert intent["returned_state"] == "cancelling", intent
    # A 202 is intent only: the receipt never claims the Run stopped.
    assert "已接受取消意图" in intent["cancel_status"], intent
    assert "cancelling" in intent["cancel_status"], intent
    assert "已停止" not in intent["cancel_status"], intent
    assert "不会撤回" in intent["cancel_status"], intent
    # The Run line and the cancel label follow the shared cancelling shape.
    assert "已收到取消意图（cancelling）" in intent["error_text"], intent
    assert intent["cancel_label"] == "再次请求取消（幂等）", intent
    assert intent["cancel_disabled"] is False, intent

    stopped = _runview_dom(tmp_path, "stopped")
    assert stopped["returned_state"] == "cancelled", stopped
    assert "已停止（cancelled）" in stopped["cancel_status"], stopped
    assert "不表示撤回" in stopped["cancel_status"], stopped
    assert "运行已取消（cancelled）" in stopped["error_text"], stopped
    assert "成功" not in stopped["error_text"], stopped
    assert stopped["cancel_disabled"] is True, stopped


def test_runview_late_cancel_response_does_not_override_a_newer_run_selection(
    node_binary: str, tmp_path: Path
) -> None:
    report = _runview_dom(tmp_path, "stale")
    assert report["status_after_new_selection"] == "", report
    # The old Run's late 202 must not write into the new Run's panel.
    assert report["status_after_late"] == "", report
    assert "r2" in report["run_state"] or "queued" in report["run_state"], report
    assert report["cancel_label"] == "取消 Run", report


# ===========================================================================
# 7. real compiled API: the migrated scripts are served as the committed bytes,
#    in the real page load order, through the real ``cmd/api`` process.
# ===========================================================================
SHARED_ASSETS = ("runstream.js", "runstate-guard.js", "chat.js", "runview.js")
DEBUG_PAGE_PATH = "/apps/11111111-2222-4333-8444-555555555555/debug"


@pytest.fixture(scope="module")
def c602_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> Stack:
    """One real API process over a throwaway migrated database (own DB)."""
    tmp_path = tmp_path_factory.mktemp("c602-stack")
    try:
        database = st.create_database("orchara_c602_")
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


def test_the_migrated_scripts_are_served_exactly_as_committed(c602_stack) -> None:
    for name in SHARED_ASSETS:
        response = c602_stack.server.http.get(f"/assets/{name}")
        assert response.status == 200, (name, response.text)
        assert "javascript" in response.header("Content-Type"), (
            name,
            response.header("Content-Type"),
        )
        committed = (WEB / "assets" / name).read_text(encoding="utf-8")
        assert response.text == committed, f"{name}: served bytes differ from the committed bytes"


def test_the_pages_load_runstream_then_guard_then_the_page_script(c602_stack) -> None:
    for path, page_script in (("/chat", "chat.js"), (DEBUG_PAGE_PATH, "debug.js")):
        body = c602_stack.server.http.get(path).text
        runstream_marker = '<script src="/assets/runstream.js"></script>'
        guard_marker = '<script src="/assets/runstate-guard.js"></script>'
        script_marker = f'<script src="/assets/{page_script}"></script>'
        assert runstream_marker in body, f"{path} does not load the real shared script"
        assert guard_marker in body, f"{path} does not load the dependency guard"
        assert script_marker in body, f"{path} does not load {page_script}"
        assert body.index(runstream_marker) < body.index(guard_marker), path
        assert body.index(guard_marker) < body.index(script_marker), path
    # The debug page's node view consumes the same shared Run rules and loads
    # after the guard, before debug.js.
    body = c602_stack.server.http.get(DEBUG_PAGE_PATH).text
    assert body.index("/assets/runstate-guard.js") < body.index("/assets/runview.js")
    assert body.index("/assets/runview.js") < body.index("/assets/debug.js")


# ===========================================================================
# 8. real Chrome (Playwright): a real queued debug Run through the real page.
#    Cancel is intent until the Run itself reports stopped.
# ===========================================================================
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
def c602_owner(c602_stack) -> dict:
    owner = t029.golden_owner(c602_stack, login="c602-owner-" + f00b.new_uuid()[:8])
    return {"cookie": owner.creator.cookie, "application_id": owner.application_id}


@pytest.fixture(scope="module", autouse=True)
def _write_evidence():
    yield
    if os.environ.get("C602_WRITE_EVIDENCE") != "1":
        return
    target = Path(__file__).resolve().parents[2] / "reports" / "architecture"
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "C6.02 (#156)",
        "browser": "playwright channel=chrome headless, real cmd/api over loopback",
        "cases": EVIDENCE,
    }
    (target / "C6.02-browser-evidence.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@contextlib.contextmanager
def _browser_page(browser, stack, cookie):
    context = browser.new_context()
    context.add_cookies(
        [{"name": st.SESSION_COOKIE, "value": cookie, "url": stack.server.base_url}]
    )
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    try:
        yield page, requests, page_errors
    finally:
        context.close()


def _paths(requests: list[str], base_url: str) -> list[str]:
    return [url[len(base_url):].split("?")[0] for url in requests if url.startswith(base_url)]


def _submit_debug_run(page) -> None:
    """Submit through the debug page's real submit handler.

    The visible #debug-submit button sits behind the page's `data-requires-role`
    gate. On this integration branch `/assets/nav.js` has no server route (a
    pre-existing 404, recorded in the report), so nav.js cannot unhide the gate;
    the page's own submit handler is still the real code path. C6.01 used the
    same event dispatch for its debug fault-mode check.
    """
    page.evaluate(
        "document.getElementById('debug-form').dispatchEvent("
        "new Event('submit', {bubbles: true, cancelable: true}))"
    )


def test_real_browser_runview_uses_the_shared_terminal_and_cancel_rules(
    browser, c602_stack, c602_owner
) -> None:
    base = c602_stack.server.base_url
    url = f"{base}/apps/{c602_owner['application_id']}/debug"
    case: dict = {"page": "debug-runview", "url": url.replace(base, "")}

    with _browser_page(browser, c602_stack, c602_owner["cookie"]) as (
        page,
        requests,
        page_errors,
    ):
        page.goto(url, wait_until="load")
        page.wait_for_function(
            "document.getElementById('debug-revision').value !== ''", timeout=20000
        )

        # --- phase 1: a real queued Run, then a real cancel (immediate stop) ---
        _submit_debug_run(page)
        page.wait_for_function(
            "document.getElementById('runview-run-state').textContent.indexOf('Run state：') === 0",
            timeout=30000,
        )
        page.wait_for_function(
            "document.getElementById('runview-run-state').textContent.indexOf('queued') >= 0",
            timeout=20000,
        )
        queued_error = page.inner_text("#runview-error")
        assert "仍在进行（state=queued）" in queued_error, queued_error
        assert "成功" not in queued_error.replace("不会把", ""), queued_error
        cancel = page.locator("#runview-cancel")
        assert not cancel.is_disabled(), "a queued Run must be cancellable"
        assert cancel.inner_text() == "取消 Run", cancel.inner_text()

        # The 700ms node tick keeps observing the Run (real GET /runs/{id}), and
        # the real debug-run POST created the Run through the compiled API.
        page.wait_for_timeout(1800)
        paths = _paths(requests, base)
        assert any(p.endswith("/debug-runs") for p in paths), paths
        run_paths = [p for p in paths if re.fullmatch(r"/api/v1/runs/[0-9a-f-]{36}", p)]
        assert len(run_paths) >= 2, run_paths

        cancel.click()
        page.wait_for_function(
            "(() => { const text = document.getElementById('runview-cancel-status').textContent;"
            " return text.indexOf('已停止') >= 0 || text.indexOf('已接受取消意图') >= 0; })()",
            timeout=20000,
        )
        receipt = page.inner_text("#runview-cancel-status")
        # The real server commits a queued cancel immediately; the receipt may
        # only say "stopped" because the returned Run says cancelled.
        assert "已停止（cancelled）" in receipt, receipt
        assert "不表示撤回" in receipt, receipt
        page.wait_for_function(
            "document.getElementById('runview-error').textContent.indexOf('运行已取消（cancelled）') >= 0",
            timeout=20000,
        )
        stopped_error = page.inner_text("#runview-error")
        assert "已成功" not in stopped_error, stopped_error
        assert page.locator("#runview-cancel").is_disabled(), "a terminal Run is not cancellable"
        case.update(
            {
                "phase1_queued_error": queued_error,
                "phase1_cancel_receipt": receipt,
                "phase1_terminal_error": stopped_error,
                "phase1_run_gets": len(run_paths),
            }
        )

        # --- phase 2: a live attempt, so a cancel is intent (cancelling) ------
        page.goto(url, wait_until="load")
        page.wait_for_function(
            "document.getElementById('debug-revision').value !== ''", timeout=20000
        )
        _submit_debug_run(page)
        page.wait_for_function(
            "document.getElementById('run-id').textContent.length > 0", timeout=30000
        )
        run_id = page.inner_text("#run-id")
        lease = t029.execution_client(c602_stack, "supervisor").claim_job(
            worker_id="c602-supervisor", execution_unit_id="c602-unit"
        )
        assert lease.status == 200, lease.text
        assert lease.json["run_id"] == run_id, (lease.json, run_id)
        page.wait_for_function(
            "['running','dispatching'].some((state) => "
            "document.getElementById('runview-run-state').textContent.indexOf(state) >= 0)",
            timeout=20000,
        )
        live_state = page.inner_text("#runview-run-state")
        page.locator("#runview-cancel").click()
        page.wait_for_function(
            "document.getElementById('runview-cancel-status').textContent.indexOf('已接受取消意图') >= 0",
            timeout=20000,
        )
        intent_receipt = page.inner_text("#runview-cancel-status")
        assert "cancelling" in intent_receipt, intent_receipt
        assert "已停止" not in intent_receipt, intent_receipt
        page.wait_for_function(
            "document.getElementById('runview-error').textContent.indexOf('已收到取消意图（cancelling）') >= 0",
            timeout=20000,
        )
        intent_error = page.inner_text("#runview-error")
        assert "不表示撤回" in intent_error, intent_error
        paths = _paths(requests, base)
        assert any(path.endswith("/cancel") for path in paths), paths
        case.update(
            {
                "phase2_live_state": live_state,
                "phase2_cancel_receipt": intent_receipt,
                "phase2_error": intent_error,
                "page_errors": page_errors,
            }
        )
        assert page_errors == [], page_errors
    EVIDENCE.append(case)


