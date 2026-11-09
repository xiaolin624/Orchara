"""F04B/T083 run view: the debug page's node-state table and its Node-executed logic.

The page and the ``runview.js`` asset are the real bytes. Three layers are proven:

* the pure functions under Node against the real P05 example definitions
  (``tests/f04b/runview.test.mjs``), including out-of-order/replayed
  ``node.status`` frames, skipped branches, condition ports and join envelopes;
* the page structure (the table, the port/join cells, the skipped-vs-failed
  legend, the cancel control and the script order) plus the real static asset
  served by a compiled ``cmd/api``;
* the real DOM wiring under Node with a minimal document shim (not a browser):
  the real ``beginRunView``/``runViewOnEvent``/cancel handlers issue the real
  request shapes and render the real table rows.

The real HTTP execution E2E (condition -> python branches -> join on runsc) is
gated on the graph runtime being merged into ``integration/t083-t091``: it needs
``internal/presetexec/graphplan.go``. Until then it is skipped with that exact
reason and the report records ``not_run``; no fabricated E2E evidence is used.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import execution_support as execution
from support import stack as st

NODE = shutil.which("node")
PRODUCT = Path(__file__).resolve().parents[2]
WEB_DIR = PRODUCT / "apps" / "web" / "src"
ASSETS = WEB_DIR / "assets"
DEBUG_HTML = WEB_DIR / "debug.html"
DEBUG_JS = ASSETS / "debug.js"
RUNVIEW_JS = ASSETS / "runview.js"
MODULE_TEST = Path(__file__).resolve().parent / "runview.test.mjs"
GRAPH_RUNTIME = PRODUCT / "internal" / "presetexec" / "graphplan.go"
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
APPLICATION_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(scope="module", name="node_binary_fixture")
def node_binary_fixture() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the run-view script cannot be executed")
    return NODE


def _run_node(node: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node, *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout, cwd=str(PRODUCT),
    )


def test_the_runview_pure_functions_pass_under_node(node_binary_fixture: str) -> None:
    completed = _run_node(node_binary_fixture, str(MODULE_TEST))
    assert completed.returncode == 0, (
        "the run-view pure functions did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "PASS 6 runview tests" in completed.stdout, completed.stdout


def test_the_run_view_assets_parse_with_node(node_binary_fixture: str, tmp_path: Path) -> None:
    for asset in (RUNVIEW_JS, DEBUG_JS):
        completed = _run_node(node_binary_fixture, "--check", str(asset))
        assert completed.returncode == 0, (asset.name, completed.stderr)
    page = DEBUG_HTML.read_text(encoding="utf-8")
    for index, block in enumerate(INLINE_SCRIPT.findall(page)):
        script = tmp_path / f"debug.{index}.js"
        script.write_text(block, encoding="utf-8")
        completed = _run_node(node_binary_fixture, "--check", str(script))
        assert completed.returncode == 0, completed.stderr


def test_the_debug_page_hosts_the_node_state_table() -> None:
    body = DEBUG_HTML.read_text(encoding="utf-8")
    for marker in (
        'id="runview-panel"',
        'id="run-node-table"',
        'id="run-node-rows"',
        'id="runview-status"',
        'id="runview-run-state"',
        'id="runview-error"',
        'id="runview-summary"',
        'id="runview-legend"',
        'id="runview-cancel"',
        'id="runview-cancel-status"',
        'id="runview-runs-link"',
        "<th>分支 / join 来源</th>",
        "已跳过",
        "失败",
        "node-results",
        "port",
        "results.",
        "Last-Event-ID",
        "410",
    ):
        assert marker in body, f"debug.html is missing {marker!r}"
    # The F02A submission surface is untouched.
    for marker in (
        'id="debug-form"',
        'id="debug-revision"',
        'id="debug-input"',
        'id="debug-submit"',
        'id="debug-status"',
        'id="run-panel" class="card" hidden',
        'id="run-timeline-status"',
        "/assets/app.js",
        "/assets/stream-preview.js",
        "/assets/runs.js",
        "/assets/debug.js",
    ):
        assert marker in body, marker
    # The page still keeps no credential and no browser storage.
    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token=" not in body
    # Script order: app.js before runview.js before runs.js/debug.js, so the
    # helpers exist before the run view wires itself.
    order = [
        body.index("/assets/app.js"),
        body.index("/assets/runview.js"),
        body.index("/assets/stream-preview.js"),
        body.index("/assets/runs.js"),
        body.index("/assets/debug.js"),
    ]
    assert order == sorted(order), order
    # The run view is a real table with a caption row, not an event log copy.
    assert body.count("<th>") == 6, body.count("<th>")


def test_the_run_view_asset_is_served_like_the_other_page_scripts(t027_stack) -> None:
    page = t027_stack.server.http.get(f"/apps/{APPLICATION_ID}/debug")
    assert page.status == 200, page.text
    assert "text/html" in page.header("Content-Type")
    assert 'id="runview-panel"' in page.text
    assert "/assets/runview.js" in page.text
    asset = t027_stack.server.http.get("/assets/runview.js")
    assert asset.status == 200, asset.text
    assert "javascript" in asset.header("Content-Type")
    for symbol in (
        "function aggregateNodeStates(",
        "function buildRunViewModel(",
        "function formatRunError(",
        "function beginRunView(",
        "function runViewOnEvent(",
        "function runViewRequestCancel(",
    ):
        assert symbol in asset.text, symbol


HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const assetsDir = __ASSETS__;
const APPLICATION_ID = __APPLICATION_ID__;
const RUN_ID = __RUN_ID__;

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
  }
  appendChild(child) { this.children.push(child); return child; }
  append(...nodes) { for (const node of nodes) this.appendChild(node); }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  click() { for (const fn of this._listeners.click || []) fn({ preventDefault() {} }); }
}

const byId = new Map();
function makeElement(tag, id) {
  const element = new El(tag, id);
  if (id) byId.set(id, element);
  return element;
}
for (const id of [
  'runview-panel', 'runview-status', 'runview-run-state', 'runview-error',
  'runview-summary', 'run-node-rows', 'runview-cancel', 'runview-cancel-status',
  'runview-runs-link',
]) {
  makeElement('div', id);
}

const document = {
  getElementById(id) { return byId.get(id) || makeElement('div', id); },
  createElement(tag) { return makeElement(tag); },
};

const timers = [];
const calls = [];
const artifactContents = {
  'a-choose': { port: 'no', matched_case: 'default' },
  'a-join': {
    results: {
      approve: { status: 'skipped' },
      direct: { status: 'succeeded', value: { summary: 'ok', nested: { count: 1 } } },
    },
  },
};
const fixtures = {
  draft: { definition: __DEFINITION__, layout: {} },
  nodeResultItems: [
    { node_id: 'choose', kind: 'data', artifact_id: 'a-choose', label: 'condition',
      media_type: 'application/json', size_bytes: 24, sha256: 'a'.repeat(64) },
    { node_id: 'join', kind: 'data', artifact_id: 'a-join', label: 'join',
      media_type: 'application/json', size_bytes: 48, sha256: 'b'.repeat(64) },
  ],
  runStateSequence: ['queued', 'succeeded'],
  runStateIndex: 0,
  errorCode: '',
};

function currentRun() {
  const index = Math.min(fixtures.runStateIndex, fixtures.runStateSequence.length - 1);
  return {
    id: RUN_ID,
    state: fixtures.runStateSequence[index],
    phase: 'executing',
    kind: 'debug',
    revision: 1,
    application_version_id: null,
    parent_run_id: null,
    output_artifact_id: null,
    error_code: fixtures.errorCode,
    execution_snapshot_id: 'snapshot-1',
  };
}

function jsonResponse(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    headers: { get: () => null },
  };
}

function artifactResponse(path) {
  const match = path.match(/^\/api\/v1\/artifacts\/([^/]+)\/content$/);
  const id = match ? decodeURIComponent(match[1]) : '';
  if (!Object.prototype.hasOwnProperty.call(artifactContents, id)) {
    return jsonResponse(404, { error: { code: 'resource_not_found', message: 'no artifact' } });
  }
  return jsonResponse(200, artifactContents[id]);
}

async function routeApi(path) {
  if (path === '/api/v1/applications/' + APPLICATION_ID + '/draft') {
    return fixtures.draft;
  }
  if (path === '/api/v1/runs/' + RUN_ID + '/node-results') {
    return { items: fixtures.nodeResultItems, next_cursor: null, generated_at: 'now' };
  }
  if (path === '/api/v1/runs/' + RUN_ID) {
    return currentRun();
  }
  throw new Error('unexpected api path: ' + path);
}

const context = {
  console: console,
  window: { location: { assign() {} } },
  document: document,
  setTimeout(fn) { timers.push(fn); return timers.length; },
  clearTimeout() {},
  requireLogin: async () => ({}),
  csrf: async () => 'csrf-1',
  api: async (path, options) => {
    calls.push({ kind: 'api', path: path, options: options });
    return routeApi(path);
  },
  apiFull: async (path, options) => {
    calls.push({ kind: 'apiFull', path: path, options: options });
    if (path === '/api/v1/runs/' + RUN_ID + '/cancel') {
      return { payload: Object.assign({}, currentRun(), { state: 'cancelling' }) };
    }
    if (path === '/api/v1/runs/' + RUN_ID) {
      return { payload: currentRun(), headers: { get: () => null } };
    }
    throw new Error('unexpected apiFull path: ' + path);
  },
  fetch: async (path) => {
    calls.push({ kind: 'fetch', path: path });
    return artifactResponse(path);
  },
};

function textOf(element) {
  let text = String(element.textContent || '');
  for (const child of element.children || []) {
    text += ' ' + textOf(child);
  }
  return text;
}

function rows() {
  return document.getElementById('run-node-rows').children;
}

function rowByID(nodeID) {
  return rows().find((row) => row.attrs['data-node-id'] === nodeID);
}

async function flush(rounds) {
  for (let index = 0; index < (rounds || 24); index += 1) {
    await new Promise((resolve) => setImmediate(resolve));
  }
}

async function runNextTimer() {
  const timer = timers.shift();
  if (timer) {
    await timer();
  }
  await flush();
}

async function main() {
  vm.createContext(context);
  vm.runInContext(
    fs.readFileSync(path.join(assetsDir, 'runview.js'), 'utf8'), context,
    { filename: 'runview.js' },
  );

  // --- scenario 1: definition + node-results, then committed SSE replay ------
  const view = context.beginRunView({
    runID: RUN_ID,
    applicationID: APPLICATION_ID,
    draftRevision: 3,
    run: currentRun(),
  });
  assert.ok(view, 'beginRunView returned no view');
  assert.strictEqual(document.getElementById('runview-panel').hidden, false);
  await flush();
  assert.ok(
    calls.some((call) => call.kind === 'api' && call.path.endsWith('/draft')),
    'the current draft definition was not loaded',
  );
  assert.ok(
    calls.some((call) => call.kind === 'fetch' && call.path.indexOf('a-choose') !== -1),
    'the condition data artifact was not read',
  );
  assert.ok(
    calls.some((call) => call.kind === 'fetch' && call.path.indexOf('a-join') !== -1),
    'the join data artifact was not read',
  );
  assert.strictEqual(rows().length, 6, 'the table does not have one row per definition node');
  // Before any status event the rows are waiting, never success.
  assert.strictEqual(rowByID('start').attrs['data-state'], 'pending');
  assert.ok(textOf(rowByID('start')).includes('等待'), textOf(rowByID('start')));

  let events = [];
  const feed = (seq, nodeID, state) => {
    const event = { seq: seq, type: 'node.status', payload: {
      node_id: nodeID, state: state, attempt_no: 1,
    } };
    events = events.concat([event]);
    context.runViewOnEvent(events, event);
  };
  // Out of order and with a duplicate frame, exactly like a Last-Event-ID replay.
  feed(9, 'start', 'succeeded');
  feed(3, 'start', 'pending');
  feed(7, 'start', 'pending');
  feed(11, 'choose', 'succeeded');
  feed(13, 'approve', 'skipped');
  feed(15, 'direct', 'succeeded');
  feed(17, 'join', 'succeeded');
  feed(19, 'end', 'succeeded');
  await flush(4);

  const skipped = rowByID('approve');
  assert.strictEqual(skipped.attrs['data-state'], 'skipped');
  assert.ok(skipped.className.includes('state-skipped'), skipped.className);
  assert.ok(textOf(skipped).includes('已跳过'), textOf(skipped));
  const failedLookalike = rowByID('direct');
  assert.ok(!failedLookalike.className.includes('state-failed'), failedLookalike.className);
  const choose = rowByID('choose');
  assert.ok(textOf(choose).includes('端口 no'), textOf(choose));
  assert.ok(textOf(choose).includes('默认'), textOf(choose));
  const join = rowByID('join');
  assert.ok(textOf(join).includes('approve=已跳过'), textOf(join));
  assert.ok(textOf(join).includes('direct=成功'), textOf(join));
  assert.ok(textOf(join).includes('查看'), textOf(join));
  assert.ok(calls.every((call) => call.kind !== 'apiFull'), 'no write happened unexpectedly');
  // One summary line from the aggregated states.
  assert.ok(
    document.getElementById('runview-summary').textContent.includes('跳过 1'),
    document.getElementById('runview-summary').textContent,
  );

  // --- scenario 2: cancel records intent, never claims "stopped" ------------
  document.getElementById('runview-cancel').click();
  await flush();
  const cancelCall = calls.find(
    (call) => call.kind === 'apiFull' && call.path === '/api/v1/runs/' + RUN_ID + '/cancel',
  );
  assert.ok(cancelCall, 'the cancel button did not POST /runs/{id}/cancel');
  assert.strictEqual(cancelCall.options.method, 'POST');
  assert.strictEqual(cancelCall.options.csrf, 'csrf-1');
  assert.ok(
    document.getElementById('runview-cancel-status').textContent.includes('已接受取消意图'),
    document.getElementById('runview-cancel-status').textContent,
  );
  assert.strictEqual(document.getElementById('runview-cancel').disabled, false,
    'a cancelling Run can be cancelled again (idempotent)');

  // --- scenario 3: the terminal state stops the poll and disables cancel ----
  fixtures.runStateIndex = 1;
  await runNextTimer();
  assert.ok(
    document.getElementById('runview-status').textContent.includes('终态'),
    document.getElementById('runview-status').textContent,
  );
  assert.strictEqual(document.getElementById('runview-cancel').disabled, true);
  assert.ok(
    document.getElementById('runview-error').textContent.includes('成功'),
    document.getElementById('runview-error').textContent,
  );
  assert.strictEqual(timers.length, 0, 'polling continued after the terminal state');

  // --- scenario 4: a 410-style fallback infers from durable node results ----
  fixtures.runStateIndex = 0;
  fixtures.runStateSequence = ['failed', 'failed'];
  fixtures.errorCode = 'invalid_activation';
  fixtures.nodeResultItems = [
    { node_id: 'direct', kind: 'data', artifact_id: 'a-direct', label: 'output',
      media_type: 'application/json', size_bytes: 2, sha256: 'c'.repeat(64) },
  ];
  artifactContents['a-direct'] = { ok: true };
  const fallback = context.beginRunView({
    runID: RUN_ID, applicationID: APPLICATION_ID, draftRevision: 3,
  });
  assert.ok(fallback);
  await flush();
  const direct = rowByID('direct');
  assert.ok(textOf(direct).includes('推断'), textOf(direct));
  assert.strictEqual(direct.attrs['data-state'], 'succeeded');
  const approve = rowByID('approve');
  assert.strictEqual(approve.attrs['data-state'], 'unknown');
  assert.ok(textOf(approve).includes('无状态事件'), textOf(approve));
  const errorLine = document.getElementById('runview-error').textContent;
  assert.ok(errorLine.includes('failed'), errorLine);
  assert.ok(errorLine.includes('invalid_activation'), errorLine);
  assert.ok(errorLine.includes('激活'), errorLine);
  assert.ok(
    document.getElementById('runview-run-state').textContent.includes('error_code=invalid_activation'),
    document.getElementById('runview-run-state').textContent,
  );

  process.stdout.write('run view page behaviour ok\n');
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def _branch_definition() -> dict:
    return json.loads(
        (PRODUCT / "docs" / "spec" / "examples" / "branch-approval" / "workflow.json")
        .read_text(encoding="utf-8")
    )


def test_the_run_view_page_renders_ports_skipped_and_join_under_node(
    node_binary_fixture: str, tmp_path: Path,
) -> None:
    harness = HARNESS.replace("__ASSETS__", json.dumps(str(ASSETS))).replace(
        "__APPLICATION_ID__", json.dumps(APPLICATION_ID)
    ).replace("__RUN_ID__", json.dumps(RUN_ID)).replace(
        "__DEFINITION__", json.dumps(_branch_definition())
    )
    script = tmp_path / "runview-page-harness.js"
    script.write_text(harness, encoding="utf-8")
    completed = _run_node(node_binary_fixture, str(script))
    assert completed.returncode == 0, (
        "the run-view page did not behave as required:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "run view page behaviour ok" in completed.stdout, completed.stdout


# ---------------------------------------------------------------------------
# Real HTTP execution E2E: gated on the graph runtime landing in the
# integration branch (internal/presetexec/graphplan.go). Until then it is
# skipped with the exact reason; the report records it as not_run.
# ---------------------------------------------------------------------------

RUNTIME_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');

const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const context = { console: console };
vm.createContext(context);
vm.runInContext(fs.readFileSync(__RUNVIEW__, 'utf8'), context, { filename: 'runview.js' });
const states = context.aggregateNodeStates(input.events);
const model = context.buildRunViewModel(
  input.definition, states, input.nodeResults, input.contents, input.run,
);
process.stdout.write(JSON.stringify({
  states: states,
  rows: model.rows.map((row) => ({
    nodeId: row.nodeId,
    type: row.type,
    state: row.state,
    stateText: row.stateText,
    skipped: row.skipped,
    failed: row.failed,
    branchPort: row.branchPort,
    matchedCase: row.matchedCase,
    branchText: row.branchText,
    joinSources: row.joinSources,
    joinText: row.joinText,
    artifactId: row.artifactId,
    errorCode: row.errorCode,
  })),
  counts: model.counts,
  text: model.text,
  error: context.formatRunError(input.run),
}));
"""


def _run_runview_harness(
    node: str, tmp_path: Path, payload: dict,
) -> dict:
    harness = RUNTIME_HARNESS.replace("__RUNVIEW__", json.dumps(str(RUNVIEW_JS)))
    script = tmp_path / "runview-runtime-harness.js"
    script.write_text(harness, encoding="utf-8")
    payload_file = tmp_path / "runview-runtime-input.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    completed = _run_node(node, str(script), str(payload_file))
    assert completed.returncode == 0, (
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    return json.loads(completed.stdout)


def _owner_reads(t033_stack, owner, run_id: str) -> tuple[dict, dict, dict]:
    """The real owner HTTP reads plus the committed node.status events."""
    from support import t027, t042

    detail = t027.get_run(owner.creator, run_id)
    assert detail.status == 200, detail.text
    results = t042.node_results(owner.creator, run_id)
    assert results.status == 200, results.text
    contents: dict[str, object] = {}
    for item in results.json["items"]:
        if item["kind"] != "data":
            continue
        content = t042.content_of(owner.creator, item["artifact_id"])
        assert content.status == 200, content.text
        try:
            contents[item["artifact_id"]] = json.loads(content.raw)
        except ValueError:
            contents[item["artifact_id"]] = content.raw.decode("utf-8", "replace")
    events = [
        {"seq": row["seq"], "type": "node.status", "payload": row["payload"]}
        for row in t042.node_events(t033_stack.dbname, run_id)
    ]
    return detail.json, results.json, {"contents": contents, "events": events}


def test_the_real_linear_run_view_shows_committed_states_and_the_data_artifact(
    t033_stack, node_binary_fixture: str, tmp_path: Path,
) -> None:
    """The real HTTP data path the run view uses, on the runtime that exists today.

    This is the linear start -> python -> end run (F04A's executor): it proves the
    page module reads a real owner-only node-results projection, a real data
    artifact and the real committed node.status payloads over HTTP. The condition
    / port / join E2E below is the graph-runtime part and stays gated.
    """
    from support import t027, t033, t042

    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; the real python node cannot execute"
        )
    definition = t042.linear_definition()
    owner = t033.golden_owner(t033_stack)
    application_id = t042.application_for(owner, definition=definition, name="t083 linear view")
    csv_id = t042.ready_csv(t033_stack, owner)
    run = t042.submit_linear_run(
        owner, application_id, artifact_id=csv_id, input_value={"source": "t083"},
    )
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report["run_state"] == "succeeded", report
    t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})

    run_body, node_results, extra = _owner_reads(t033_stack, owner, run["id"])
    data_rows = {item["node_id"]: item for item in node_results["items"] if item["kind"] == "data"}
    assert "stats" in data_rows, node_results["items"]
    assert extra["contents"][data_rows["stats"]["artifact_id"]], extra["contents"]
    model = _run_runview_harness(
        node_binary_fixture,
        tmp_path,
        {
            "definition": definition,
            "events": extra["events"],
            "nodeResults": node_results,
            "contents": extra["contents"],
            "run": run_body,
        },
    )
    rows = {row["nodeId"]: row for row in model["rows"]}
    assert [row["nodeId"] for row in model["rows"]] == ["start", "stats", "end"], model["rows"]
    assert rows["start"]["state"] == "succeeded", rows["start"]
    assert rows["stats"]["state"] == "succeeded", rows["stats"]
    assert rows["end"]["state"] == "succeeded", rows["end"]
    assert rows["stats"]["artifactId"] == data_rows["stats"]["artifact_id"], rows["stats"]
    assert rows["stats"]["failed"] is False, rows["stats"]
    assert model["counts"]["succeeded"] == 3, model["counts"]
    assert model["counts"]["skipped"] == 0, model["counts"]
    assert model["error"]["severity"] == "ok", model["error"]



@pytest.mark.skipif(
    not GRAPH_RUNTIME.is_file(),
    reason=(
        "the graph runtime is not in integration/t083-t091 "
        "(internal/presetexec/graphplan.go is absent), so a condition/join debug run "
        "cannot execute; the real HTTP run-view E2E is not_run"
    ),
)
def test_the_real_run_view_selects_the_port_and_skips_the_unselected_branch(
    t033_stack, node_binary_fixture: str, tmp_path: Path,
) -> None:
    """A real condition/join run, rendered by the real run-view module.

    Reuses the T083 runtime acceptance fixture/helpers (`tests/support/t083.py`):
    start -> choose(condition) --yes--> left(python) / --no--> right(python) ->
    join -> end, executed by the real supervisor on runsc, read back through the
    owner's real HTTP surface (Run snapshot, node-results, artifact content and
    the owner-only SSE replay).
    """
    from support import t027, t032, t033, t083

    if not t033.sandbox_available():
        pytest.skip(
            "WSL2/runsc is unavailable: `wsl -d Ubuntu-24.04 -u root -e bash -lc "
            "'runsc --version'` failed; the branch python nodes cannot execute"
        )
    definition = t083.branch_definition()
    owner, _application_id, run = t083.queue_graph_run(
        t033_stack,
        definition,
        input_value={"requires_approval": True},
        name="t083 run view",
    )
    completed, report = execution.execute_once(t033_stack)
    assert completed.returncode == 0, (completed.stderr, report)
    assert report["status"] == "executed", report
    assert report["outcome"] == "succeeded", report
    assert report.get("error_code", "") == "", report
    assert report["run_state"] == "succeeded", report
    t033.wait_run_state(t033_stack.dbname, run["id"], {"succeeded"})

    items = t083.node_results(owner.creator, run["id"], expected=6)
    assert {item["node_id"] for item in items} == {"left", "choose", "join"}, items
    condition = t083.node_data_document(owner.creator, items, "choose")
    join_document = t083.node_data_document(owner.creator, items, "join")
    assert condition == {"port": "yes", "matched_case": 0}, condition
    assert join_document["results"]["left"]["status"] == "succeeded", join_document
    assert join_document["results"]["right"]["status"] == "skipped", join_document

    detail = t027.get_run(owner.creator, run["id"])
    assert detail.status == 200, detail.text
    run_body = detail.json
    assert run_body["state"] == "succeeded", run_body
    # `t083.node_results` returns the owner-read item list; the page's
    # `node-results` body is `{items, next_cursor, generated_at}`, so the list is
    # wrapped only where the harness expects the body projection.
    node_results = items

    # The owner-only SSE replay is the surface the page consumes: the same
    # committed node.status events, with their real seq numbers.
    stream = t032.open_sse(t033_stack, owner.creator, run["id"])
    try:
        frames = t032.read_status_frames(stream, max_frames=200)
    finally:
        stream.close()
    events = []
    statuses = set()
    for frame in frames:
        envelope = t032.frame_envelope(frame)
        if envelope["type"] != "node.status":
            continue
        events.append(
            {
                "seq": envelope["seq"],
                "type": "node.status",
                "payload": envelope["payload"],
            }
        )
        statuses.add((envelope["payload"]["node_id"], envelope["payload"]["state"]))
    assert ("right", "skipped") in statuses, statuses
    assert ("left", "succeeded") in statuses, statuses
    assert ("left", "skipped") not in statuses, statuses
    joins = [
        event for event in events
        if event["payload"]["node_id"] == "join" and event["payload"]["state"] == "succeeded"
    ]
    assert len(joins) == 1, events

    # `t083.node_results` returns the owner-read list of items (not the body).
    data_rows = {item["node_id"]: item for item in node_results if item["kind"] == "data"}
    assert "choose" in data_rows and "join" in data_rows, node_results
    contents = {
        data_rows["choose"]["artifact_id"]: condition,
        data_rows["join"]["artifact_id"]: join_document,
    }
    model = _run_runview_harness(
        node_binary_fixture,
        tmp_path,
        {
            "definition": definition,
            "events": events,
            # The run-view module consumes the owner-read *body* projection
            # (`{items:[...]}`), while `t083.node_results` returns the items list.
            "nodeResults": {"items": node_results},
            "contents": contents,
            "run": run_body,
        },
    )
    rows = {row["nodeId"]: row for row in model["rows"]}
    assert rows["choose"]["branchPort"] == "yes", rows["choose"]
    assert rows["choose"]["matchedCase"] == 0, rows["choose"]
    assert rows["right"]["state"] == "skipped", rows["right"]
    assert rows["right"]["skipped"] is True, rows["right"]
    assert rows["left"]["state"] == "succeeded", rows["left"]
    assert rows["left"]["failed"] is False, rows["left"]
    sources = {entry["source"]: entry for entry in rows["join"]["joinSources"]}
    assert sources["left"]["status"] == "succeeded", sources
    assert sources["right"]["status"] == "skipped", sources
    assert sources["left"]["hasValue"] is True, sources["left"]
    assert "left=成功" in rows["join"]["joinText"], rows["join"]
    assert "right=已跳过" in rows["join"]["joinText"], rows["join"]
    assert model["error"]["severity"] == "ok", model["error"]
    assert model["counts"]["skipped"] == 1, model["counts"]

