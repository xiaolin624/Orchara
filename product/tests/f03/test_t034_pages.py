"""T034: the runs page shows one accumulated active clock and the timeout reason.

Two layers, both real:

* the page and its asset are fetched from the running API over HTTP, and the
  structural assertions are about what the browser receives (the two new detail
  rows and the non-contract header names);
* ``runs.js`` is executed under Node, so the one-clock rendering, the NULL-limit
  wording and the deadline reason are observed behaviour rather than source text.

The header values feeding the Node harness are also read from a real Run whose
attempt was settled by a real exit (time moved into the past with SQL, no sleep).
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from support import stack as st
from support import t029, t034
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

NODE = shutil.which("node")


def test_the_runs_page_and_asset_carry_the_active_clock_and_reason(t029_stack) -> None:
    page = t029_stack.server.http.get("/runs")
    assert page.status == 200, page.text
    for marker in (
        'id="detail-active"',
        'id="detail-deadline"',
        "累计活跃",
        "X-Run-Active-Elapsed-Ms",
        "X-Run-Max-Active-Ms",
        "X-Run-Deadline-Exceeded",
        "跨尝试累计为一个时钟",
    ):
        # The page states the single-clock rule in prose, not only in JS.
        assert marker in page.text, f"runs.html is missing {marker!r}"

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    for marker in (
        "function describeActiveClock(",
        "function formatActiveSeconds(",
        "X-Run-Active-Elapsed-Ms",
        "X-Run-Max-Active-Ms",
        "X-Run-Deadline-Exceeded",
        "deadline_exceeded",
        "不是 0",
    ):
        assert marker in asset.text, f"runs.js is missing {marker!r}"


def test_real_active_headers_feed_the_page(t029_stack) -> None:
    owner = t034.golden_owner(t029_stack)
    lease = t034.claim_lease(t029_stack, owner, execution_unit_id="unit-page")
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, lease["attempt_token"], 2)
    released = t034.execution_client(t029_stack, "supervisor").confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-page",
        evidence=t029.exit_evidence(lease, started=False, reason="spawn_failed"),
    )
    assert released.status == 200, released.text

    detail = owner.creator.client.get(t029.run_path(lease["run_id"]))
    assert detail.status == 200, detail.text
    headers = t034.active_headers(detail)
    assert headers["max_active_ms"] == t034.GOLDEN_MAX_ACTIVE_MS, headers
    t034.assert_active_ms(
        headers["active_elapsed_ms"], at_least=2000, below=3000, label="page header"
    )
    assert headers["deadline_exceeded"] is False, headers

    # A deadline leaves the reason on the Run and the boolean header on the detail.
    # The same Run is claimed again (its accumulated ~2s is below the new 4s
    # limit) and the resumed attempt pushes the projected total over it.
    t034.set_run_max_active_ms(t029_stack.dbname, lease["run_id"], 4000)
    resumed_response = t034.execution_client(t029_stack, "supervisor").claim_job(
        execution_unit_id="unit-page-2"
    )
    assert resumed_response.status == 200, resumed_response.text
    resumed = resumed_response.json
    assert resumed["run_id"] == lease["run_id"], resumed
    t034.shift_attempt_started_at_seconds(t029_stack.dbname, resumed["attempt_token"], 3)
    beat = t034.execution_client(t029_stack, "supervisor").heartbeat_run(
        resumed["run_id"],
        attempt_token=resumed["attempt_token"],
        execution_unit_id="unit-page-2",
    )
    assert beat.status == 200 and beat.json["cancel_requested"] is True, beat.text
    timed_out = owner.creator.client.get(t029.run_path(resumed["run_id"]))
    headers = t034.active_headers(timed_out)
    assert headers["deadline_exceeded"] is True and headers["max_active_ms"] == 4000, headers
    assert headers["active_elapsed_ms"] >= 4000, headers


def test_the_active_clock_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "t034-active-clock.js"
    harness.write_text(
        CLOCK_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "the T034 page logic did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "t034 active clock ok" in completed.stdout, completed.stdout


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


CLOCK_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const context = { console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/runs.js', 'utf8'), context);

function headers(map) {
  return { get: (name) => (Object.prototype.hasOwnProperty.call(map, name) ? map[name] : null) };
}

// The three non-contract headers are read by exact name; absent is null, never 0.
const facts = context.runRuntimeFacts(headers({
  'X-Run-Active-Elapsed-Ms': '7000',
  'X-Run-Max-Active-Ms': '30000',
  'X-Run-Deadline-Exceeded': 'true',
}));
assert.strictEqual(facts.activeElapsedMS, 7000);
assert.strictEqual(facts.maxActiveMS, 30000);
assert.strictEqual(facts.deadlineExceeded, true);
const absent = context.runRuntimeFacts(null);
assert.strictEqual(absent.activeElapsedMS, null);
assert.strictEqual(absent.maxActiveMS, null);
assert.strictEqual(absent.deadlineExceeded, false);

// Two attempts are one accumulated clock, never a reset or two clocks.
const two = context.describeActiveClock(
  { id: 'r1', state: 'failed', error_code: 'deadline_exceeded' },
  { activeElapsedMS: 7000, maxActiveMS: 30000, deadlineExceeded: true },
);
assert.ok(two.line.indexOf('累计活跃 7.0 秒') >= 0, two.line);
assert.ok(two.line.indexOf('上限 30.0 秒') >= 0, two.line);
assert.strictEqual(two.line.split('累计活跃').length - 1, 1, two.line);
assert.ok(two.oneClockNote.indexOf('同一个累计时钟') >= 0, two.oneClockNote);
assert.ok(two.oneClockNote.indexOf('不重置') >= 0, two.oneClockNote);
assert.ok(two.reasonLine.indexOf('deadline_exceeded') >= 0, two.reasonLine);
assert.ok(two.reasonLine.indexOf('不发布输出产物') >= 0, two.reasonLine);
assert.ok(two.reasonLine.indexOf('已成功') < 0, two.reasonLine);

// Under the limit: the page says so without inventing a percentage.
const under = context.describeActiveClock(
  { id: 'r1', state: 'running' },
  { activeElapsedMS: 1000, maxActiveMS: 30000, deadlineExceeded: false },
);
assert.ok(under.reasonLine.indexOf('未超时') >= 0, under.reasonLine);
assert.ok(!/%|进度条|预计/.test(under.reasonLine), under.reasonLine);

// NULL max: unknown/no deadline, explicitly not "0 seconds".
const noMax = context.describeActiveClock(
  { id: 'r1', state: 'running' },
  { activeElapsedMS: 5000, maxActiveMS: null, deadlineExceeded: false },
);
assert.ok(noMax.reasonLine.indexOf('NULL') >= 0, noMax.reasonLine);
assert.ok(noMax.reasonLine.indexOf('不是 0') >= 0, noMax.reasonLine);
assert.ok(noMax.line.indexOf('上限未知') >= 0, noMax.line);
assert.ok(noMax.line.indexOf('上限 0') < 0, noMax.line);

// Reason comes from the committed error_code even if the header was absent.
const codeOnly = context.describeActiveClock(
  { id: 'r1', state: 'failed', error_code: 'deadline_exceeded' },
  { activeElapsedMS: 9000, maxActiveMS: 9000, deadlineExceeded: false },
);
assert.strictEqual(codeOnly.exceeded, true);
assert.ok(codeOnly.reasonLine.indexOf('deadline_exceeded') >= 0, codeOnly.reasonLine);

// Seconds are formatted from milliseconds, never fabricated.
assert.strictEqual(context.formatActiveSeconds(7000), '7.0 秒');
assert.strictEqual(context.formatActiveSeconds(900000), '900 秒');
assert.strictEqual(context.formatActiveSeconds(null), '未知');

process.stdout.write('t034 active clock ok\n');
"""
