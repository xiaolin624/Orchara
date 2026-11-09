"""T032: the run pages render the durable timeline, dedupe by seq and never fake.

Two real layers:

* the pages/assets are served by the compiled API over real HTTP, and the
  structural assertions are about what the browser receives;
* ``runs.js`` is executed under Node with stubbed inputs, so SSE frame parsing,
  seq-dedup, reconnect/final wording and the no-preview rule are observed
  behaviour rather than source text.

Still ``not_run``: a DOM/browser driving the page (same boundary as
F01/T023/T027/T029/T031).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st
from support import t032
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t032_stack,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def test_the_runs_page_is_served_with_timeline_markers(t032_stack) -> None:
    response = t032_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    body = response.text
    for marker in (
        'id="detail-timeline"',
        'id="detail-timeline-status"',
        'id="detail-preview"',
        'data-preview="not_run"',
        "/api/v1/runs/{id}/events",
        "Last-Event-ID",
        "finalizing",
        "F06",
        "/assets/runs.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"

    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "location.search" not in body


def test_runs_js_is_the_served_timeline_client(t032_stack) -> None:
    response = t032_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    body = response.text
    for marker in (
        "function runEventsPath(",
        "function parseSSEFrame(",
        "function parseSSEBuffer(",
        "function applyRunEvent(",
        "function nextLastEventID(",
        "function describeRunEvent(",
        "function timelineTerminalState(",
        "function streamFailure(",
        "function streamRunTimeline(",
        "Last-Event-ID",
        "410",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_the_debug_page_renders_the_same_durable_timeline(t032_stack) -> None:
    debug = t032_stack.server.http.get(f"/apps/{t032.new_uuid()}/debug")
    assert debug.status == 200
    for marker in (
        'id="run-timeline"',
        'id="run-timeline-status"',
        'id="run-preview"',
        'data-preview="not_run"',
        "/assets/runs.js",
        "Last-Event-ID",
    ):
        assert marker in debug.text, f"debug.html is missing {marker!r}"

    debug_js = t032_stack.server.http.get("/assets/debug.js")
    assert debug_js.status == 200
    assert "beginRunTimeline(" in debug_js.text


def test_the_served_pages_and_assets_parse_with_node(t032_stack, node_binary: str, tmp_path) -> None:
    for page in ("runs.html", "debug.html"):
        body = (st.WEB_DIR / page).read_text(encoding="utf-8")
        for index, block in enumerate(INLINE_SCRIPT.findall(body)):
            script = tmp_path / f"{page}.{index}.js"
            script.write_text(block, encoding="utf-8")
            completed = subprocess.run(
                [node_binary, "--check", str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
            assert completed.returncode == 0, (page, completed.stderr)
    for asset in ("runs.js", "debug.js"):
        completed = subprocess.run(
            [node_binary, "--check", str(st.WEB_DIR / "assets" / asset)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert completed.returncode == 0, (asset, completed.stderr)


RUNS_HARNESS = r"""
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

// The event-stream path is built from the id, never from a caller URL.
assert.strictEqual(context.runEventsPath('11111111-1111-4111-8111-111111111111'),
  '/api/v1/runs/11111111-1111-4111-8111-111111111111/events');
assert.strictEqual(context.runEventsPath('a b/c'), '/api/v1/runs/a%20b%2Fc/events');

// A frame split across two network chunks is reassembled; comments are frames.
const chunked = 'id: 3\nevent: run.status\ndata: {"seq":3}\n\n: heartbeat\n\ndata: {"seq":4';
const parsed = context.parseSSEBuffer(chunked);
assert.strictEqual(parsed.frames.length, 2, JSON.stringify(parsed));
assert.strictEqual(parsed.frames[0].id, '3');
assert.strictEqual(parsed.frames[0].event, 'run.status');
assert.strictEqual(parsed.frames[0].data, '{"seq":3}');
assert.strictEqual(parsed.frames[1].comment, 'heartbeat');
assert.strictEqual(parsed.rest, 'data: {"seq":4');

const frame = context.parseSSEFrame(['id: 7', 'event: node.status', 'data: {"a":1}', 'data: {"b":2}']);
assert.strictEqual(frame.id, '7');
assert.strictEqual(frame.event, 'node.status');
assert.strictEqual(frame.data, '{"a":1}\n{"b":2}');

// Events are ordered by seq and a repeated seq is a no-op (dedupe).
const base = [
  { seq: 1, type: 'run.status', payload: { state: 'queued' } },
  { seq: 2, type: 'run.status', payload: { state: 'running' } },
];
let events = context.applyRunEvent(base, { seq: 2, type: 'run.status', payload: { state: 'running' } });
assert.strictEqual(events.length, 2, JSON.stringify(events));
events = context.applyRunEvent(events, { seq: 3, type: 'run.status', payload: { state: 'failed' } });
assert.deepStrictEqual(events.map((event) => event.seq), [1, 2, 3]);
events = context.applyRunEvent(events, { seq: 5, type: 'node.status', payload: { node_id: 'n' } });
events = context.applyRunEvent(events, { seq: 4, type: 'node.status', payload: { node_id: 'n2' } });
assert.deepStrictEqual(events.map((event) => event.seq), [1, 2, 3, 4, 5]);
assert.strictEqual(context.applyRunEvent(events, { seq: 0 }).length, 5);
assert.strictEqual(context.nextLastEventID(events), '5');
assert.strictEqual(context.nextLastEventID([]), '');

// One honest line per event; payload content is never rendered, and a running
// proposal is explicitly not a success yet.
const running = context.describeRunEvent({ seq: 3, type: 'run.status', payload: { state: 'running' } });
assert.ok(running.indexOf('seq 3') >= 0, running);
assert.ok(running.indexOf('finalizing') >= 0, running);
assert.ok(running.indexOf('尚未成功') >= 0, running);
const succeeded = context.describeRunEvent({ seq: 4, type: 'run.status', payload: { state: 'succeeded' } });
assert.ok(succeeded.indexOf('终态') >= 0, succeeded);
const failed = context.describeRunEvent({ seq: 5, type: 'run.status', payload: { state: 'failed', error_code: 'tool_crashed' } });
assert.ok(failed.indexOf('tool_crashed') >= 0, failed);
const node = context.describeRunEvent({ seq: 6, type: 'node.status', payload: { node_id: 'n1', state: 'running', attempt_no: 2 } });
assert.ok(node.indexOf('n1') >= 0 && node.indexOf('attempt_no=2') >= 0, node);
const bodyLike = context.describeRunEvent({
  seq: 7, type: 'run.status',
  payload: { state: 'running', content: 'SECRET-BODY', body: 'SECRET-BODY' },
});
assert.ok(bodyLike.indexOf('SECRET-BODY') < 0, bodyLike);

assert.strictEqual(context.timelineTerminalState(events.slice(0, 3)), 'failed');
assert.strictEqual(context.timelineTerminalState([{ seq: 1, type: 'run.status', payload: { state: 'running' } }]), '');
assert.strictEqual(context.timelineTerminalState([]), '');

// 410 is terminal for the stream and points at the Run snapshot; other
// failures are retryable with the same Last-Event-ID cursor.
const expired = context.streamFailure(410, { error: { code: 'event_cursor_expired',
  details: { snapshot_url: '/api/v1/runs/r1' } } });
assert.strictEqual(expired.retry, false, JSON.stringify(expired));
assert.ok(expired.text.indexOf('/api/v1/runs/r1') >= 0, expired.text);
assert.strictEqual(context.streamFailure(404, { error: { code: 'resource_not_found' } }).retry, false);
assert.strictEqual(context.streamFailure(500, { error: { code: 'internal_error' } }).retry, true);
assert.strictEqual(context.streamFailure(0, null).retry, true);

process.stdout.write('runs.js timeline behaviour ok\n');
"""


def test_runs_timeline_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-timeline-harness.js"
    harness.write_text(
        RUNS_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
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
        "runs.js timeline did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js timeline behaviour ok" in completed.stdout, completed.stdout
