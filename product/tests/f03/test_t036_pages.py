"""T036: the runs page distinguishes stopping from stopped and never flips back.

Two layers, both real:

* the page/asset are fetched from the running API over HTTP, so the structural
  markers are exactly what the browser receives;
* ``runs.js`` is executed under Node, so the cancel wording, the terminal reason
  and the monotonic snapshot rule are observed behaviour.

The header values feeding the Node harness are read from a real Run whose cancel
intent and terminal cancelled state were committed by the real API.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t029, t036
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

NODE = shutil.which("node")


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_runs_page_and_asset_carry_the_cancel_panel(t029_stack) -> None:
    page = t029_stack.server.http.get("/runs")
    assert page.status == 200, page.text
    for marker in (
        'id="detail-cancel"',
        'id="detail-cancel-button"',
        'id="detail-cancel-status"',
        "POST /api/v1/runs/{id}/cancel",
        "正在停止",
        "已停止",
        "needs_reconciliation",
        "迟到结果",
    ):
        assert marker in page.text, f"runs.html is missing {marker!r}"

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    for marker in (
        "function cancelRunPath(",
        "function canCancelRun(",
        "function describeRunCancel(",
        "function mergeRunSnapshot(",
        "X-Run-Cancel-Requested-At",
        "正在停止",
        "已停止",
        "终态原因",
        "不会改变本页",
    ):
        assert marker in asset.text, f"runs.js is missing {marker!r}"
    assert "object_key" not in asset.text


def test_real_cancel_headers_and_states_feed_the_page(t029_stack) -> None:
    owner = t036.golden_owner(t029_stack)
    lease = t036.claim_lease(t029_stack, owner, execution_unit_id="unit-page-cancel")

    accepted = t036.cancel_run(owner.creator, lease["run_id"])
    assert accepted.status == 202, accepted.text
    t036.assert_run_shape(accepted.json)
    assert accepted.json["state"] == t036.STATE_CANCELLING, accepted.text
    assert t036.cancel_headers(accepted)["cancel_requested_at"] is not None, accepted.text

    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    assert detail.json["state"] == t036.STATE_CANCELLING, detail.text
    assert detail.json["phase"] == "executing", detail.text
    assert t036.cancel_headers(detail)["cancel_requested_at"] == (
        accepted.header(t036.CANCEL_HEADER)
    ), detail.text

    assert t036.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-page-cancel",
        evidence=t036.exit_evidence(lease, started=True),
    ).status == 200
    stopped = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert stopped.status == 200, stopped.text
    t036.assert_run_shape(stopped.json)
    assert stopped.json["state"] == t036.STATE_CANCELLED, stopped.text
    assert stopped.json["phase"] == "idle", stopped.text
    assert t036.cancel_headers(stopped)["cancel_requested_at"] is not None, stopped.text


def test_cancel_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t036-cancel.js"
    harness.write_text(
        CANCEL_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "the T036 cancel page logic did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "t036 cancel page ok" in completed.stdout, completed.stdout


def test_runs_asset_parses_with_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "runs.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


CANCEL_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
// C6.01 (#155): the real shared run-state bytes, loaded before the page
// script exactly like the served page, so the harness exercises the real
// dependency instead of the page-local fallback.
const runstreamSource = fs.readFileSync(webDir + '/assets/runstream.js', 'utf8');
const runsSource = fs.readFileSync(webDir + '/assets/runs.js', 'utf8');
const context = { console };
vm.createContext(context);
vm.runInContext(appSource, context, { filename: 'app.js' });
vm.runInContext(runstreamSource, context, { filename: 'runstream.js' });
vm.runInContext(
  fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'),
  context,
  { filename: 'runstate-guard.js' },
);
vm.runInContext(runsSource, context, { filename: 'runs.js' });

function headers(map) {
  return { get: (name) => (Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null) };
}

// The cancel path is built from the id, never from a caller URL.
assert.strictEqual(context.cancelRunPath('r1'), '/api/v1/runs/r1/cancel');
assert.strictEqual(context.cancelRunPath('a b/c'), '/api/v1/runs/a%20b%2Fc/cancel');

// Only non-terminal Runs can be cancelled; stopping is still cancellable (idempotent).
for (const state of ['queued', 'running', 'waiting_approval', 'waiting_children',
  'recovery_pending', 'needs_reconciliation', 'cancelling']) {
  assert.strictEqual(context.canCancelRun({ id: 'r1', state }), true, state);
}
for (const state of ['succeeded', 'failed', 'cancelled']) {
  assert.strictEqual(context.canCancelRun({ id: 'r1', state }), false, state);
}
assert.strictEqual(context.canCancelRun(null), false);
assert.strictEqual(
  context.cancelButtonLabel({ state: 'cancelling' }), '再次请求取消（幂等）',
);
assert.strictEqual(context.cancelButtonLabel({ state: 'running' }), '取消 Run');

// The three non-contract headers feed the cancel panel by exact name.
const facts = context.runRuntimeFacts(headers({
  'X-Run-Cancel-Requested-At': '2026-11-03T01:00:00Z',
  'X-Run-Waiting-Kind': 'approval',
}));
assert.strictEqual(facts.cancelRequestedAt, '2026-11-03T01:00:00Z');
assert.strictEqual(context.runRuntimeFacts(null).cancelRequestedAt, '');

const queued = context.describeRunCancel({ id: 'r1', state: 'queued' }, {});
assert.ok(queued.line.indexOf('立即生效') >= 0, queued.line);
assert.ok(queued.line.indexOf('cancelled') >= 0, queued.line);
assert.strictEqual(queued.terminal, false);

const stopping = context.describeRunCancel(
  { id: 'r1', state: 'cancelling', phase: 'executing', kind: 'debug' },
  { cancelRequestedAt: '2026-11-03T01:00:00Z' },
);
assert.strictEqual(stopping.terminal, false);
assert.ok(stopping.line.indexOf('正在停止') >= 0, stopping.line);
assert.ok(stopping.line.indexOf('cancelling') >= 0, stopping.line);
assert.ok(stopping.line.indexOf('2026-11-03T01:00:00Z') >= 0, stopping.line);
assert.ok(stopping.line.indexOf('不会显示成功') >= 0, stopping.line);
assert.ok(stopping.line.indexOf('已停止') < 0, stopping.line);

const stopped = context.describeRunCancel(
  { id: 'r1', state: 'cancelled', phase: 'idle', kind: 'debug', error_code: null },
  { cancelRequestedAt: '2026-11-03T01:00:00Z' },
);
assert.strictEqual(stopped.terminal, true);
assert.ok(stopped.line.indexOf('已停止') >= 0, stopped.line);
assert.ok(stopped.line.indexOf('不表示撤回') >= 0, stopped.line);
assert.ok(stopped.reasonLine.indexOf('终态原因：cancelled') >= 0, stopped.reasonLine);

const failed = context.describeRunCancel(
  { id: 'r1', state: 'failed', phase: 'idle', error_code: 'deadline_exceeded' }, {},
);
assert.strictEqual(failed.terminal, true);
assert.ok(failed.line.indexOf('已失败结束') >= 0, failed.line);
assert.ok(failed.reasonLine.indexOf('deadline_exceeded') >= 0, failed.reasonLine);

const succeeded = context.describeRunCancel({ id: 'r1', state: 'succeeded', phase: 'idle' }, {});
assert.ok(succeeded.line.indexOf('已成功结束') >= 0, succeeded.line);
assert.ok(succeeded.line.indexOf('迟到') >= 0, succeeded.line);

// Terminal stability: a late/stale non-terminal snapshot never flips the page.
const terminal = { id: 'r1', state: 'cancelled', revision: 4 };
assert.strictEqual(
  context.mergeRunSnapshot(terminal, { id: 'r1', state: 'running', revision: 2 }), terminal,
);
assert.strictEqual(context.mergeRunSnapshot(terminal, { id: 'r1', state: 'succeeded' }), terminal);
const running = { id: 'r1', state: 'running', revision: 2 };
const cancelling = { id: 'r1', state: 'cancelling', revision: 3 };
assert.strictEqual(context.mergeRunSnapshot(running, cancelling), cancelling);
assert.strictEqual(context.mergeRunSnapshot(cancelling, terminal), terminal);
assert.strictEqual(context.mergeRunSnapshot(null, running), running);

// The lifecycle names stopping, and the poll loop keeps asking while non-terminal.
const lifecycle = context.runLifecycle({ state: 'cancelling', phase: 'executing' });
assert.strictEqual(lifecycle.current, 'cancelling');
assert.ok(lifecycle.stages.indexOf('cancelling') >= 0, JSON.stringify(lifecycle));
const note = context.runDetailNote({ id: 'r1', state: 'cancelling', phase: 'executing' }, {});
assert.ok(note.indexOf('正在停止') >= 0, note);
assert.ok(note.indexOf('已进入终态') < 0, note);
assert.strictEqual(context.OrcharaRunState.isTerminal('cancelling'), false);
assert.strictEqual(context.OrcharaRunState.isTerminal('cancelled'), true);
assert.strictEqual(typeof context.shouldPollRun, 'undefined');

process.stdout.write('t036 cancel page ok\n');
"""
