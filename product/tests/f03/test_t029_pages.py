"""T029: the runs page is a real, honest client of the run endpoints.

Two layers, both real:

* the page and its asset are fetched from the running API over HTTP, and the
  structural assertions are about what the browser receives (query input limited
  to the run id, no credential storage, the detail fields incl. the lease facts);
* ``runs.js`` is executed under Node with stubbed headers, so path building, the
  state sentence and the "no lease, no fake progress" rule are observed
  behaviour rather than source text.

What is still not_run: a DOM/browser driving the page. That is the same boundary
F01/T023/T027 record.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st
from support import t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
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


def test_the_runs_page_is_served_with_real_link_markers(t029_stack) -> None:
    response = t029_stack.server.http.get("/runs")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text
    for marker in (
        'id="runs-status"',
        'role="status"',
        'id="runs-list"',
        'id="runs-empty"',
        'id="runs-next"',
        'id="run-detail"',
        'id="detail-id"',
        'id="detail-state"',
        'id="detail-phase"',
        'id="detail-kind"',
        'id="detail-revision"',
        'id="detail-snapshot"',
        'id="detail-manifest"',
        'id="detail-lease"',
        'id="detail-note"',
        "/api/v1/runs",
        "/api/v1/runs/{id}",
        "X-Run-Attempt-Started-At",
        "X-Run-Lease-Deadline",
        "/assets/app.js",
        "/assets/runs.js",
        "/assets/nav.js",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"

    assert "localStorage" not in body
    assert "sessionStorage" not in body
    # The page may deep-link to one run (`/runs?run=<uuid>`): F12/T095 wired that
    # so the chat page's "查看运行" link opens the run it refers to. That is the
    # only query parameter the page may read, and the value must not become a
    # secret carrier - the assertion this replaced forbade `location.search`
    # outright, which contradicted the deep link rather than protecting a
    # credential.
    assert re.findall(r"URLSearchParams\([^)]*\)\.get\(\s*'([^']+)'", body) == ["run"]
    assert "?token=" not in body


def test_runs_js_is_the_served_asset(t029_stack) -> None:
    response = t029_stack.server.http.get("/assets/runs.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text
    for marker in (
        "function runDetailPath(",
        "function listRunsPath(",
        "function runSummary(",
        "function runRuntimeFacts(",
        "function runDetailNote(",
        "function runLeaseText(",
        "X-Run-Attempt-Started-At",
        "X-Run-Lease-Deadline",
        "X-Manifest-SHA256",
        "/api/v1/runs",
    ):
        assert marker in body, f"runs.js is missing {marker!r}"


def test_the_apps_page_and_the_debug_page_link_to_runs(t029_stack) -> None:
    apps = t029_stack.server.http.get("/apps")
    assert apps.status == 200
    assert 'src="/assets/nav.js"' in apps.text
    nav = t029_stack.server.http.get("/assets/nav.js")
    assert nav.status == 200 and "href: '/runs'" in nav.text

    debug = t029_stack.server.http.get(f"/apps/{t029.new_uuid()}/debug")
    assert debug.status == 200
    assert 'src="/assets/nav.js"' in debug.text
    assert 'id="run-phase"' in debug.text, "debug.html does not show the run phase"
    assert 'id="run-lease"' in debug.text, "debug.html does not show the real lease"
    assert "X-Run-Lease-Deadline" in debug.text

    debug_js = t029_stack.server.http.get("/assets/debug.js")
    assert debug_js.status == 200
    assert "function leaseFactsFromHeaders(" in debug_js.text
    assert "X-Run-Lease-Deadline" in debug_js.text


def test_the_served_pages_and_assets_parse_with_node(t029_stack, node_binary, tmp_path) -> None:
    for page in ("runs.html", "debug.html", "apps.html"):
        body = (st.WEB_DIR / page).read_text(encoding="utf-8")
        for index, block in enumerate(INLINE_SCRIPT.findall(body)):
            script = tmp_path / f"{page}.{index}.js"
            script.write_text(block, encoding="utf-8")
            completed = subprocess.run(
                [node_binary, "--check", str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            assert completed.returncode == 0, (page, completed.stderr)
    for asset in ("runs.js", "debug.js"):
        completed = subprocess.run(
            [node_binary, "--check", str(st.WEB_DIR / "assets" / asset)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
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

function load() {
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
  return context;
}

const context = load();

// Paths are built from ids, never from a caller-supplied URL.
assert.strictEqual(context.runDetailPath('11111111-1111-4111-8111-111111111111'),
  '/api/v1/runs/11111111-1111-4111-8111-111111111111');
assert.strictEqual(context.runDetailPath('a b/c'), '/api/v1/runs/a%20b%2Fc');
assert.strictEqual(context.listRunsPath(null, null), '/api/v1/runs');
assert.strictEqual(context.listRunsPath('cursor==', 20), '/api/v1/runs?cursor=cursor%3D%3D&limit=20');

// The projection is exactly the contract fields the page shows.
const summary = context.runSummary({
  id: 'r1', state: 'running', phase: 'executing', kind: 'debug', revision: 3,
  execution_snapshot_id: 's1', application_version_id: null,
});
assert.strictEqual(JSON.stringify(summary), JSON.stringify({
  id: 'r1', state: 'running', phase: 'executing', kind: 'debug',
  revision: '3', executionSnapshotID: 's1',
}));

// The non-contract headers are read by exact name and never invented.
function headers(map) {
  return { get: (name) => map[name] || null };
}
const facts = context.runRuntimeFacts(headers({
  'X-Manifest-SHA256': 'a'.repeat(64),
  'X-Run-Attempt-Started-At': '2026-11-03T00:00:00Z',
  'X-Run-Lease-Deadline': '2026-11-03T00:00:30Z',
}));
assert.strictEqual(facts.manifestSHA256, 'a'.repeat(64));
assert.strictEqual(facts.startedAt, '2026-11-03T00:00:00Z');
assert.strictEqual(facts.leaseDeadline, '2026-11-03T00:00:30Z');
const emptyFacts = context.runRuntimeFacts(null);
assert.strictEqual(emptyFacts.manifestSHA256, '');
assert.strictEqual(emptyFacts.startedAt, '');
assert.strictEqual(emptyFacts.leaseDeadline, '');

// One honest sentence per state: no percentage, no fake progress. C6.03 (#157)
// makes the shared aggregate view the only truth; the lease headers get their
// own page row instead of being re-explained by the page.
assert.ok(context.runDetailNote({ state: 'queued' }, {}).includes('尚未被领取'));
const runningWithLease = context.runLeaseText(
  { state: 'running', phase: 'executing' },
  { startedAt: '2026-11-03T00:00:00Z', leaseDeadline: '2026-11-03T00:00:30Z' },
);
assert.ok(runningWithLease.includes('已领取'), runningWithLease);
assert.ok(runningWithLease.includes('2026-11-03T00:00:30Z'), runningWithLease);
const runningWithoutLease = context.runLeaseText(
  { state: 'running', phase: 'executing' }, {},
);
assert.ok(runningWithoutLease.includes('没有可显示的租约事实'), runningWithoutLease);
assert.ok(
  context.runDetailNote({ state: 'running', phase: 'executing' }, {}).includes('不伪造'),
);
assert.ok(context.runDetailNote({ state: 'succeeded' }, {}).includes('已成功'));
for (const text of [runningWithLease, runningWithoutLease]) {
  assert.ok(!/%|进度条|预计/.test(text), text);
}

process.stdout.write('runs.js behaviour ok\n');
"""


def test_runs_page_logic_executes_under_node(node_binary: str, tmp_path) -> None:
    harness = tmp_path / "runs-harness.js"
    harness.write_text(
        RUNS_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))), encoding="utf-8"
    )
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "runs.js did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js behaviour ok" in completed.stdout, completed.stdout
