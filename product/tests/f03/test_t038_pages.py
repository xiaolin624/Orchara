"""T038: the runs page explains recovery_pending and never fakes a resume.

Two layers, both real:

* the page/asset are fetched from the running API over HTTP, so the structural
  markers (including the exact operator command) are what the browser receives;
* ``runs.js`` is executed under Node, so the recovery wording, the evidence
  existence rule and the monotonic terminal rule are observed behaviour.

The header values feeding the Node harness are read from a real Run parked by the
real sweep and recovered by the real admin command.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t038
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


def test_the_runs_page_and_asset_carry_the_recovery_panel(t029_stack) -> None:
    page = t029_stack.server.http.get("/runs")
    assert page.status == 200, page.text
    for marker in (
        'id="detail-recovery"',
        "recovery_pending",
        "recovery_reason=lease_expired",
        "exit_without_result",
        "orchara-admin runs sweep-leases --grace 60s",
        "orchara-admin runs recover --run",
        "--check-only",
        "X-Run-Recovery-Reason",
        "X-Run-Recovery-Evidence-SHA256",
        "不会自动接管",
        "不会创建第二个",
        "不提供伪造的",
    ):
        assert marker in page.text, f"runs.html is missing {marker!r}"

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    for marker in (
        "function describeRunRecovery(",
        "X-Run-Recovery-Reason",
        "X-Run-Recovery-Since",
        "X-Run-Recovery-Evidence-At",
        "X-Run-Recovery-Evidence-SHA256",
        "orchara-admin runs recover",
        "orchara-admin runs sweep-leases",
        "尚无受审计恢复证据",
        "不会自动换 token",
        "不会被恢复改变",
    ):
        assert marker in asset.text, f"runs.js is missing {marker!r}"
    assert "object_key" not in asset.text
    assert "attempt_token" not in asset.text


def test_real_recovery_headers_and_states_feed_the_page(t029_stack, tmp_path) -> None:
    owner, lease, _park = t038.parked_run(
        t029_stack, execution_unit_id="unit-page-recovery"
    )
    run_id = lease["run_id"]

    parked = owner.creator.client.get(t038.run_path(run_id))
    assert parked.status == 200, parked.text
    assert parked.json["state"] == "recovery_pending", parked.text
    headers = t038.recovery_headers(parked)
    assert headers["reason"] == t038.REASON_LEASE_EXPIRED, headers
    assert headers["since"] is not None, headers
    assert headers["evidence_at"] is None, headers
    assert headers["evidence_sha256"] is None, headers
    assert lease["attempt_token"] not in parked.text

    evidence = t038.recovery_evidence("unit-page-recovery")
    expected_sha = t038.expected_evidence_sha256(evidence)
    report = t038.recover_ok(
        t029_stack, run_id, evidence=evidence, directory=tmp_path
    )
    assert report["status"] == "recovered", report

    recovered = owner.creator.client.get(t038.run_path(run_id))
    assert recovered.status == 200, recovered.text
    assert recovered.json["state"] == "queued", recovered.text
    headers = t038.recovery_headers(recovered)
    assert headers["reason"] is None, headers
    assert headers["since"] is None, headers
    assert headers["evidence_at"] is not None, headers
    assert headers["evidence_sha256"] == expected_sha, headers
    assert lease["attempt_token"] not in recovered.text
    assert "operator_recovery" not in recovered.text


def test_recovery_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t038-recovery.js"
    harness.write_text(
        RECOVERY_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        "the T038 recovery page logic did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "t038 recovery page ok" in completed.stdout, completed.stdout


def test_runs_asset_parses_with_node(node_binary: str) -> None:
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "runs.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


RECOVERY_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const runsSource = fs.readFileSync(webDir + '/assets/runs.js', 'utf8');
const context = { console };
vm.createContext(context);
vm.runInContext(appSource, context, { filename: 'app.js' });
vm.runInContext(runsSource, context, { filename: 'runs.js' });

function headers(map) {
  return { get: (name) => (Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null) };
}

// The four non-contract headers feed the recovery panel by exact name.
const facts = context.runRuntimeFacts(headers({
  'X-Run-Recovery-Reason': 'lease_expired',
  'X-Run-Recovery-Since': '2026-11-03T01:00:00Z',
  'X-Run-Recovery-Evidence-At': '2026-11-03T02:00:00Z',
  'X-Run-Recovery-Evidence-SHA256': 'a'.repeat(64),
}));
assert.strictEqual(facts.recoveryReason, 'lease_expired');
assert.strictEqual(facts.recoverySince, '2026-11-03T01:00:00Z');
assert.strictEqual(facts.recoveryEvidenceAt, '2026-11-03T02:00:00Z');
assert.strictEqual(facts.recoveryEvidenceSHA256, 'a'.repeat(64));
const empty = context.runRuntimeFacts(null);
assert.strictEqual(empty.recoveryReason, '');
assert.strictEqual(empty.recoveryEvidenceAt, '');

// A parked Run shows the reason, since when, the missing evidence and the
// operator command. No resume is implied.
const parked = context.describeRunRecovery(
  { id: 'r1', state: 'recovery_pending', phase: 'idle' },
  { recoveryReason: 'lease_expired', recoverySince: '2026-11-03T01:00:00Z' },
);
assert.strictEqual(parked.visible, true);
for (const needle of [
  'recovery_pending', 'lease_expired', '2026-11-03T01:00:00Z',
  '尚无受审计恢复证据', 'held', '执行槽仍占用', '未被替换',
]) {
  assert.ok(parked.line.indexOf(needle) >= 0, needle + ' in ' + parked.line);
}
for (const needle of [
  'orchara-admin runs recover --run r1', '--evidence', '--check-only',
  'orchara-admin runs sweep-leases', '不会自动换 token',
]) {
  assert.ok(parked.commandLine.indexOf(needle) >= 0, needle + ' in ' + parked.commandLine);
}
assert.ok(parked.line.indexOf('已恢复') < 0, parked.line);

// The T031 exit-without-result park has its own honest wording.
const exitPark = context.describeRunRecovery(
  { id: 'r2', state: 'recovery_pending' },
  { recoveryReason: 'exit_without_result' },
);
assert.ok(exitPark.line.indexOf('exit_without_result') >= 0, exitPark.line);
assert.ok(exitPark.line.indexOf('确认退出') >= 0, exitPark.line);

// After a successful recovery the evidence exists and the Run is queued again.
const recovered = context.describeRunRecovery(
  { id: 'r1', state: 'queued' },
  { recoveryEvidenceAt: '2026-11-03T02:00:00Z', recoveryEvidenceSHA256: 'b'.repeat(64) },
);
assert.strictEqual(recovered.visible, true);
assert.ok(recovered.line.indexOf('恢复已完成') >= 0, recovered.line);
assert.ok(recovered.line.indexOf('b'.repeat(64)) >= 0, recovered.line);
assert.ok(recovered.line.indexOf('旧 token 已失效') >= 0, recovered.line);

// A terminal Run never flips and never accepts the recovery command.
const terminal = context.describeRunRecovery(
  { id: 'r1', state: 'succeeded' },
  { recoveryEvidenceAt: '2026-11-03T02:00:00Z' },
);
assert.strictEqual(terminal.visible, true);
assert.ok(terminal.line.indexOf('不会被恢复改变') >= 0, terminal.line);
assert.ok(terminal.commandLine.indexOf('不接受恢复命令') >= 0, terminal.commandLine);

// Terminal stability: a late/stale recovery snapshot never revives the page.
const cancelled = { id: 'r1', state: 'cancelled', revision: 5 };
assert.strictEqual(
  context.mergeRunSnapshot(cancelled, { id: 'r1', state: 'recovery_pending' }), cancelled,
);
assert.strictEqual(
  context.mergeRunSnapshot(cancelled, { id: 'r1', state: 'succeeded' }), cancelled,
);

// The lifecycle names the parked stage; polling continues because it is not terminal.
const lifecycle = context.runLifecycle({ state: 'recovery_pending', phase: 'idle' });
assert.strictEqual(lifecycle.current, 'recovery_pending');
assert.ok(lifecycle.stages.indexOf('recovery_pending') >= 0, JSON.stringify(lifecycle));
const note = context.describeRunState(
  { id: 'r1', state: 'recovery_pending', phase: 'idle' },
  { recoveryReason: 'lease_expired' },
);
assert.ok(note.indexOf('recovery_pending') >= 0, note);
assert.ok(note.indexOf('lease_expired') >= 0, note);
assert.ok(note.indexOf('不会自动换 token') >= 0, note);
assert.ok(note.indexOf('已进入终态') < 0, note);
assert.strictEqual(context.shouldPollRun({ state: 'recovery_pending' }), true);
assert.strictEqual(context.shouldPollRun({ state: 'cancelled' }), false);

process.stdout.write('t038 recovery page ok\n');
"""
