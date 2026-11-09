"""T035: the runs page explains the waiting hand-over without faking progress.

Two layers, both real:

* the page and its asset are fetched from the running API over HTTP and the
  structural markers are what the browser receives;
* ``runs.js`` is executed under Node, so the waiting/decision/exit/resume wording
  and the "logical session stays occupied" rule are observed behaviour.

The header values feeding the Node harness are also read from a real Run whose
waiting condition, decision, exit and resume were committed by the real API.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t029, t035
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

NODE = shutil.which("node")


def test_the_runs_page_and_asset_carry_the_waiting_panel(t029_stack) -> None:
    page = t029_stack.server.http.get("/runs")
    assert page.status == 200, page.text
    for marker in (
        'id="detail-waiting"',
        'id="detail-resume"',
        "等待审批/子任务",
        "逻辑会话槽",
        "X-Run-Waiting-Kind",
        "X-Run-Waiting-Outcome",
        "X-Run-Waiting-Decided-At",
        "X-Run-Waiting-Exit-Confirmed-At",
        "X-Run-Resume-Job-ID",
        "X-Run-Resume-Job-State",
        "唯一",
    ):
        assert marker in page.text, f"runs.html is missing {marker!r}"

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    for marker in (
        "function describeWaitingResume(",
        "X-Run-Waiting-Kind",
        "X-Run-Waiting-Outcome",
        "X-Run-Waiting-Decided-At",
        "X-Run-Waiting-Exit-Confirmed-At",
        "X-Run-Resume-Job-ID",
        "X-Run-Resume-Job-State",
        "逻辑会话槽",
        "不伪造",
    ):
        assert marker in asset.text, f"runs.js is missing {marker!r}"


def test_real_waiting_headers_feed_the_page(t029_stack) -> None:
    owner, lease, _ = t035.waiting_run_with_lease(
        t029_stack, execution_unit_id="unit-page-waiting", checkpoint_id="ckpt-page"
    )
    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    t035.assert_run_shape(detail.json)
    headers = t035.waiting_headers(detail)
    assert headers["kind"] == "approval", headers
    assert headers["outcome"] is None and headers["decided_at"] is None, headers
    assert headers["exit_confirmed_at"] is None and headers["resume_job_id"] is None, headers

    supervisor = t035.execution_client(t029_stack, "supervisor")
    assert supervisor.resolve_waiting(
        lease["run_id"], kind="approval", outcome="approved", reference="page-ref"
    ).status == 200
    decided = owner.creator.client.get(t029.run_path(lease["run_id"]))
    headers = t035.waiting_headers(decided)
    assert headers["kind"] == "approval" and headers["outcome"] == "approved", headers
    assert headers["decided_at"] is not None, headers
    assert headers["exit_confirmed_at"] is None and headers["resume_job_id"] is None, headers

    assert t035.confirm_waiting_exit(
        t029_stack, lease, execution_unit_id="unit-page-waiting"
    ).status == 200
    resumed = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert resumed.status == 200, resumed.text
    t035.assert_run_shape(resumed.json)
    assert resumed.json["state"] == "queued", resumed.text
    headers = t035.waiting_headers(resumed)
    waiting = t035.waiting_row(t029_stack.dbname, lease["run_id"])
    assert headers["exit_confirmed_at"] is not None, headers
    assert headers["resume_job_id"] == waiting["resume_job_id"], (headers, waiting)
    assert headers["resume_job_state"] == "ready", headers


def test_the_waiting_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t035-waiting.js"
    harness.write_text(
        WAITING_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "the T035 waiting/resume page logic did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "t035 waiting resume ok" in completed.stdout, completed.stdout


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_assets_parse_with_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "runs.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


WAITING_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console };
vm.createContext(context);
// C6.01 (#155): the real shared run-state bytes load before the page script.
vm.runInContext(fs.readFileSync(webDir + '/assets/runstream.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runstate-guard.js', 'utf8'), context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

function headers(map) {
  return { get: (name) => (Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null) };
}

// The six non-contract headers are read by exact name; absent is ''.
const facts = context.runRuntimeFacts(headers({
  'X-Run-Waiting-Kind': 'approval',
  'X-Run-Waiting-Outcome': 'approved',
  'X-Run-Waiting-Decided-At': '2026-11-03T01:00:00Z',
  'X-Run-Waiting-Exit-Confirmed-At': '2026-11-03T01:00:05Z',
  'X-Run-Resume-Job-ID': 'job-resume-1',
  'X-Run-Resume-Job-State': 'ready',
}));
assert.strictEqual(facts.waitingKind, 'approval');
assert.strictEqual(facts.waitingOutcome, 'approved');
assert.strictEqual(facts.waitingDecisionAt, '2026-11-03T01:00:00Z');
assert.strictEqual(facts.waitingExitConfirmedAt, '2026-11-03T01:00:05Z');
assert.strictEqual(facts.resumeJobID, 'job-resume-1');
assert.strictEqual(facts.resumeJobState, 'ready');
const absent = context.runRuntimeFacts(null);
assert.strictEqual(absent.waitingKind, '');
assert.strictEqual(absent.resumeJobID, '');
assert.strictEqual(absent.waitingOutcome, '');

// Decision only: the page must not claim a resume exists.
const decisionOnly = context.describeWaitingResume(
  { id: 'r1', state: 'waiting_approval', phase: 'waiting' },
  { waitingKind: 'approval', waitingOutcome: 'approved',
    waitingDecisionAt: '2026-11-03T01:00:00Z', waitingExitConfirmedAt: '',
    resumeJobID: '', resumeJobState: '' },
);
assert.strictEqual(decisionOnly.visible, true);
assert.ok(decisionOnly.waitingLine.indexOf('审批决定') >= 0, decisionOnly.waitingLine);
assert.ok(decisionOnly.waitingLine.indexOf('approved') >= 0, decisionOnly.waitingLine);
assert.ok(decisionOnly.waitingLine.indexOf('尚未确认旧执行单元退出') >= 0, decisionOnly.waitingLine);
assert.ok(decisionOnly.resumeLine.indexOf('尚未创建恢复任务') >= 0, decisionOnly.resumeLine);
assert.ok(decisionOnly.resumeLine.indexOf('决定与退出均已确认') < 0, decisionOnly.resumeLine);

// No decision yet: nothing is invented.
const noDecision = context.describeWaitingResume(
  { id: 'r1', state: 'waiting_children', phase: 'waiting' },
  { waitingKind: 'children', waitingOutcome: '', waitingExitConfirmedAt: '2026-11-03T01:00:05Z' },
);
assert.ok(noDecision.waitingLine.indexOf('子任务结果') >= 0, noDecision.waitingLine);
assert.ok(noDecision.waitingLine.indexOf('尚未到达') >= 0, noDecision.waitingLine);
assert.ok(noDecision.resumeLine.indexOf('尚未创建恢复任务') >= 0, noDecision.resumeLine);

// Both facts plus the resume job: the id/state are shown, the payload/token are not.
const resumed = context.describeWaitingResume(
  { id: 'r1', state: 'queued', phase: 'idle' },
  { waitingKind: 'approval', waitingOutcome: 'rejected',
    waitingDecisionAt: '2026-11-03T01:00:00Z',
    waitingExitConfirmedAt: '2026-11-03T01:00:05Z',
    resumeJobID: 'job-resume-1', resumeJobState: 'ready' },
);
assert.ok(resumed.resumeLine.indexOf('job-resume-1') >= 0, resumed.resumeLine);
assert.ok(resumed.resumeLine.indexOf('ready') >= 0, resumed.resumeLine);
assert.ok(resumed.resumeLine.indexOf('http') < 0, resumed.resumeLine);

// No kind/state: the panel says there is no waiting condition.
const none = context.describeWaitingResume(
  { id: 'r2', state: 'running', phase: 'executing' }, {},
);
assert.strictEqual(none.visible, false);
assert.ok(none.waitingLine.indexOf('没有等待条件') >= 0, none.waitingLine);

// The slot rule is stated for every waiting condition: worker slot released,
// logical session/Run slot occupied until terminal.
for (const result of [decisionOnly, noDecision, resumed]) {
  assert.ok(result.slotNote.indexOf('执行槽') >= 0, result.slotNote);
  assert.ok(result.slotNote.indexOf('逻辑会话槽') >= 0, result.slotNote);
  assert.ok(result.slotNote.indexOf('终态') >= 0, result.slotNote);
}

// The lifecycle shows waiting as a real stage, and the state note names the wait.
const lifecycle = context.runLifecycle({ state: 'waiting_approval', phase: 'waiting' });
assert.strictEqual(lifecycle.current, 'waiting');
assert.ok(lifecycle.stages.indexOf('waiting') >= 0, JSON.stringify(lifecycle));
assert.ok(
  context.lifecycleText({ state: 'waiting_children', phase: 'waiting' }).indexOf('waiting') >= 0
);
const note = context.runDetailNote(
  { id: 'r1', state: 'waiting_approval', phase: 'waiting' }, {},
);
assert.ok(note.indexOf('等待审批') >= 0, note);
assert.ok(note.indexOf('恢复任务') >= 0, note);
assert.ok(note.indexOf('已进入终态') < 0, note);
// The fixed-checkpoint fact lives in the waiting panel's resume sentence.
assert.ok(decisionOnly.resumeLine.indexOf('checkpoint') >= 0, decisionOnly.resumeLine);

// A waiting Run is non-terminal for the poll loop: the page keeps asking through
// the shared terminal rule (C6.05 review-2: no page-local poll helper).
assert.strictEqual(context.OrcharaRunState.isTerminal('waiting_children'), false);
assert.strictEqual(context.OrcharaRunState.isTerminal('succeeded'), true);
assert.strictEqual(typeof context.shouldPollRun, 'undefined');

process.stdout.write('t035 waiting resume ok\n');
"""
