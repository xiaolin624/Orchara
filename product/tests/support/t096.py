"""F12/T096 test support: the Node page harness for the run-state truth module.

The F12 interface acceptance has no Playwright/Chromium driver (``ENV.md`` §1).
The page logic is executed as the **real committed bytes** the API serves:
``assets/runstream.js`` (new), ``assets/runs.js``, ``assets/chat.js`` are read from
``apps/web/src`` and evaluated in a ``vm`` context with a minimal DOM/fetch shim.
The harness proves request shape and rendered text, never browser layout.

Nothing here starts a stack; the real-stack tests live in
``tests/f12/test_t096_stack.py`` and feed this harness the *actual* HTTP bodies
(410 envelope, Run snapshot, children list) observed over the real socket.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SUPPORT_ROOT = Path(__file__).resolve().parent
PRODUCT_ROOT = SUPPORT_ROOT.parents[1]
CONTRACT = PRODUCT_ROOT / "contracts" / "openapi.json"


# ---------------------------------------------------------------------------
# Node invocation
# ---------------------------------------------------------------------------
def run_node(
    node_binary: str,
    tmp_path: Path,
    script: str,
    *,
    name: str = "t096-harness.js",
    timeout: int = 120,
) -> dict:
    """Execute one harness file and return its final JSON line.

    The harness prints exactly one JSON object on stdout; a non-zero exit code
    and/or a missing object is a hard failure, never a skip.
    """
    path = tmp_path / name
    path.write_text(script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [node_binary, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"node harness {name} failed (exit {completed.returncode}):\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"node harness {name} printed nothing:\n{completed.stderr}"
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken harness
        raise AssertionError(
            f"node harness {name} did not end with JSON ({exc}):\n{lines[-1]}"
        ) from exc
    assert isinstance(payload, dict), payload
    payload["_stdout"] = completed.stdout
    return payload


def contract_run_enums(contract_path: Path = CONTRACT) -> tuple[list[str], list[str]]:
    """The contract's Run.state / Run.phase enums (read-only; never rewritten)."""
    document = json.loads(contract_path.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    state = schemas["Run"]["properties"]["state"]["enum"]
    phase = schemas["Run"]["properties"]["phase"]["enum"]
    return list(state), list(phase)


# ---------------------------------------------------------------------------
# Harness 1: the pure run-state truth module (real bytes)
# ---------------------------------------------------------------------------
MODULE_HARNESS = r"""
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

const states = __STATE_ENUM__;
const phases = __PHASE_ENUM__;

const out = {
  state_labels: {},
  phase_labels: {},
  truth: {},
  failure: {},
  correction: {},
  children: {},
  artifacts: {},
  dialogue: {},
  cancel_accepted: {},
  poll: {},
};

for (const state of states) {
  out.state_labels[state] = context.runStreamStateLabel(state) || null;
}
for (const phase of phases) {
  out.phase_labels[phase] = context.runStreamPhaseLabel(phase) || null;
}

const truthOf = (run) => {
  const truth = context.runStreamTruth(run, {});
  return {
    state: truth.state,
    phase: truth.phase,
    label: truth.label,
    tone: truth.tone,
    terminal: truth.terminal,
    isSuccess: truth.isSuccess,
    isFailure: truth.isFailure,
    canCancel: truth.canCancel,
    actions: truth.actions,
    detail: truth.detail,
    externalWritesNote: truth.externalWritesNote,
  };
};

out.truth.succeeded = truthOf({ state: 'succeeded', phase: 'idle', output_artifact_id: 'a1' });
out.truth.failed = truthOf({
  state: 'failed', phase: 'idle', error_code: 'deadline_exceeded',
});
out.truth.cancelled = truthOf({ state: 'cancelled', phase: 'idle' });
out.truth.cancelling = truthOf({ state: 'cancelling', phase: 'executing' });
out.truth.finalizing = truthOf({ state: 'running', phase: 'finalizing' });
out.truth.running = truthOf({ state: 'running', phase: 'executing' });
out.truth.queued = truthOf({ state: 'queued', phase: 'idle' });
out.truth.waiting_children = truthOf({ state: 'waiting_children', phase: 'waiting' });
out.truth.waiting_approval = truthOf({ state: 'waiting_approval', phase: 'waiting' });
out.truth.recovery_pending = truthOf({ state: 'recovery_pending', phase: 'idle' });
out.truth.needs_reconciliation = truthOf({ state: 'needs_reconciliation', phase: 'idle' });

// 410: the real contract envelope shape (details.snapshot_url is the documented
// extension). The classifier must stop retrying and expose the snapshot path.
const expiredBody = __EXPIRED_BODY__;
out.failure.expired = context.runStreamFailure(410, expiredBody);
out.failure.expired_path = context.runStreamSnapshotPath(
  out.failure.expired, 'fallback-run'
);
out.failure.foreign_url = context.runStreamFailure(410, {
  error: {
    code: 'event_cursor_expired',
    message: 'expired',
    request_id: 'req-foreign',
    details: { snapshot_url: 'https://evil.example/api/v1/runs/abc' },
  },
});
out.failure.foreign_url_path = context.runStreamSnapshotPath(
  out.failure.foreign_url, 'fallback-run'
);
out.failure.traversal = context.runStreamFailure(410, {
  error: {
    code: 'event_cursor_expired',
    message: 'expired',
    request_id: 'req-traversal',
    details: { snapshot_url: '/api/v1/runs/../admin' },
  },
});
out.failure.traversal_path = context.runStreamSnapshotPath(
  out.failure.traversal, 'fallback-run'
);
out.failure.unauthorized = context.runStreamFailure(401, {
  error: { code: 'unauthenticated', message: 'login' },
});
out.failure.gone = context.runStreamFailure(404, {
  error: { code: 'resource_not_found', message: 'gone' },
});
out.failure.transient = context.runStreamFailure(500, {
  error: { code: 'internal_error', message: 'boom' },
});

// Rebuild: the page must fetch the snapshot, adopt it, and never resume the
// expired cursor silently.
(async () => {
  const snapshot = __SNAPSHOT__;
  const correction = await context.runStreamCorrectFromSnapshot(
    out.failure.expired,
    {
      runID: 'fallback-run',
      fetchSnapshot: async (snapshotPath) => {
        out.correction.fetched_path = snapshotPath;
        return { payload: snapshot, headers: { get: () => null } };
      },
    }
  );
  out.correction.applied = correction.applied;
  out.correction.snapshotPath = correction.snapshotPath;
  out.correction.historyLost = correction.historyLost;
  out.correction.resumeCursor = correction.resumeCursor;
  out.correction.statusText = correction.statusText;
  out.correction.timelineNotice = correction.timelineNotice;
  out.correction.run = correction.run;
  out.correction.truth = correction.truth
    ? { label: correction.truth.label, isSuccess: correction.truth.isSuccess }
    : null;

  out.children.waiting = context.runStreamChildSummary(__CHILD_ITEMS__, {
    id: 'parent-1', state: 'waiting_children', phase: 'waiting',
  });
  out.children.all_done = context.runStreamChildSummary(
    [
      { id: 'child-1', state: 'succeeded', output_artifact_id: 'a-child' },
      { id: 'child-2', state: 'failed', output_artifact_id: null },
    ],
    { id: 'parent-1', state: 'succeeded', phase: 'idle', output_artifact_id: 'a-parent' }
  );
  out.children.parent_success_but_child_running = context.runStreamChildSummary(
    [{ id: 'child-1', state: 'running', output_artifact_id: null }],
    { id: 'parent-1', state: 'succeeded', phase: 'idle' }
  );
  out.children.empty = context.runStreamChildSummary([], {
    id: 'parent-1', state: 'running', phase: 'executing',
  });

  const unfinished = context.runStreamArtifactReadout(
    { items: [{ id: 'a1', state: 'ready', media_type: 'application/json', size_bytes: 10 }] },
    { id: 'r1', state: 'running', phase: 'finalizing', output_artifact_id: null }
  );
  out.artifacts.unfinished = unfinished;
  out.artifacts.final = context.runStreamArtifactReadout(
    {
      items: [
        { id: 'a1', state: 'ready', media_type: 'application/json', size_bytes: 10 },
        { id: 'a2', state: 'ready', media_type: 'text/csv', size_bytes: 20 },
      ],
    },
    { id: 'r1', state: 'succeeded', phase: 'idle', output_artifact_id: 'a1' }
  );
  out.artifacts.not_ready = context.runStreamArtifactReadout(
    { items: [{ id: 'a3', state: 'scanning', media_type: 'text/csv', size_bytes: 20 }] },
    { id: 'r1', state: 'succeeded', phase: 'idle', output_artifact_id: 'a3' }
  );

  out.dialogue.finalizing = context.runStreamHandlingText(
    { state: 'running', phase: 'finalizing' }, 3
  );
  out.dialogue.cancelling = context.runStreamHandlingText(
    { state: 'cancelling', phase: 'executing' }, 3
  );
  out.dialogue.waiting_children = context.runStreamHandlingText(
    { state: 'waiting_children', phase: 'waiting' }, 3
  );
  out.dialogue.needs_reconciliation = context.runStreamHandlingText(
    { state: 'needs_reconciliation', phase: 'idle' }, 3
  );
  out.dialogue.succeeded = context.runStreamHandlingText(
    { state: 'succeeded', phase: 'idle' }, 3
  );
  out.dialogue.cancelled = context.runStreamHandlingText(
    { state: 'cancelled', phase: 'idle' }, 3
  );
  out.dialogue.queued = context.runStreamHandlingText({ state: 'queued', phase: 'idle' }, 3);
  out.dialogue.none = context.runStreamHandlingText(null, 0);

  out.cancel_accepted.cancelling = context.runStreamCancelAcceptedText({
    state: 'cancelling', phase: 'executing',
  });
  out.cancel_accepted.cancelled = context.runStreamCancelAcceptedText({
    state: 'cancelled', phase: 'idle',
  });
  out.cancel_accepted.needs_reconciliation = context.runStreamCancelAcceptedText({
    state: 'needs_reconciliation', phase: 'idle',
  });
  out.cancel_accepted.running = context.runStreamCancelAcceptedText({
    state: 'running', phase: 'executing',
  });

  out.poll = {
    queued_first: context.runStreamShouldPoll('queued', '', 0, 100),
    queued_at_cap: context.runStreamShouldPoll('queued', '', 100, 100),
    dispatched_running: context.runStreamShouldPoll('dispatched', 'running', 0, 100),
    dispatched_finalizing: context.runStreamShouldPoll(
      'dispatched', 'running', 1, 100
    ),
    dispatched_succeeded: context.runStreamShouldPoll(
      'dispatched', 'succeeded', 2, 100
    ),
    dispatched_failed: context.runStreamShouldPoll('dispatched', 'failed', 2, 100),
    dispatched_cancelled: context.runStreamShouldPoll(
      'dispatched', 'cancelled', 2, 100
    ),
    dispatched_without_run: context.runStreamShouldPoll('dispatched', '', 0, 100),
    request_cancelled: context.runStreamShouldPoll('cancelled', 'running', 0, 100),
    request_failed: context.runStreamShouldPoll('failed', 'running', 0, 100),
  };

  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def module_harness(
    web_dir: Path,
    *,
    expired_body: dict,
    snapshot: dict,
    child_items: list[dict],
    state_enum: list[str],
    phase_enum: list[str],
) -> str:
    return (
        MODULE_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__EXPIRED_BODY__", json.dumps(expired_body, ensure_ascii=False))
        .replace("__SNAPSHOT__", json.dumps(snapshot, ensure_ascii=False))
        .replace("__CHILD_ITEMS__", json.dumps(child_items, ensure_ascii=False))
        .replace("__STATE_ENUM__", json.dumps(state_enum))
        .replace("__PHASE_ENUM__", json.dumps(phase_enum))
    )


# ---------------------------------------------------------------------------
# Harness 2: the /runs page performs a snapshot fetch + rebuild on 410
# ---------------------------------------------------------------------------
RUNS_PAGE_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const RUN = __RUN_ID__;
const expiredBody = __EXPIRED_BODY__;
const snapshot = __SNAPSHOT__;

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.dataset = {};
    this.attrs = {};
  }
  appendChild(child) { this.children.push(child); return child; }
  append(node) { this.children.push(node); return node; }
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

// `runs-list` is deliberately absent: its presence is what auto-starts the runs
// list page, and this harness drives the detail timeline directly.
const document = {
  getElementById(id) {
    if (id === 'runs-list') return null;
    if (!byId.has(id)) byId.set(id, element('div', id));
    return byId.get(id);
  },
  createElement(tag) { return element(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const calls = [];
const apiFullCalls = [];
const timers = [];

async function fetch(pathname, options) {
  calls.push({ path: pathname, options: options || {} });
  if (pathname === '/api/v1/runs/' + RUN + '/events') {
    return {
      ok: false,
      status: 410,
      body: null,
      async json() { return expiredBody; },
    };
  }
  throw new Error('unexpected fetch ' + pathname);
}

const context = {
  console: console,
  document: document,
  fetch: fetch,
  TextDecoder: TextDecoder,
  setTimeout: function (fn, ms) { timers.push(fn); return timers.length; },
  clearTimeout: function () {},
  window: { location: { assign: function () {} } },
  api: async function (pathname) { throw new Error('unexpected api ' + pathname); },
  apiFull: async function (pathname) {
    apiFullCalls.push(pathname);
    return { payload: snapshot, status: 200, headers: { get: () => null } };
  },
  requireLogin: async () => ({ roles: ['user'] }),
  csrf: async () => 'csrf',
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
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
  context.beginRunTimeline(RUN, {
    listID: 'detail-timeline',
    statusID: 'detail-timeline-status',
  });
  await flush();

  const out = {
    events_requests: calls.map((call) => call.path),
    api_full_requests: apiFullCalls.slice(),
    detail_state: document.getElementById('detail-state').textContent,
    detail_phase: document.getElementById('detail-phase').textContent,
    detail_note: document.getElementById('detail-note').textContent,
    timeline_status: document.getElementById('detail-timeline-status').textContent,
    timeline_items: document.getElementById('detail-timeline').children.map(
      (child) => child.textContent
    ),
    timeline_snapshot_flag: document.getElementById('detail-timeline').dataset
      ? document.getElementById('detail-timeline').dataset.snapshotRebuilt || ''
      : '',
  };
  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def runs_page_harness(
    web_dir: Path,
    *,
    run_id: str,
    expired_body: dict,
    snapshot: dict,
) -> str:
    return (
        RUNS_PAGE_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__RUN_ID__", json.dumps(run_id))
        .replace("__EXPIRED_BODY__", json.dumps(expired_body, ensure_ascii=False))
        .replace("__SNAPSHOT__", json.dumps(snapshot, ensure_ascii=False))
    )


# ---------------------------------------------------------------------------
# Harness 3: chat.js consumes the new truth helpers once runstream.js is loaded
# ---------------------------------------------------------------------------
CHAT_HARNESS = r"""
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
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'chat.js'), 'utf8'),
  context,
  { filename: 'chat.js' }
);

function handling(request, run, version) {
  const result = context.describeHandling(request, run, version);
  return { text: result.text, handled: result.handled, versionNo: result.versionNo };
}

const out = {
  has_should_poll_handling: typeof context.shouldPollHandling === 'function',
  handling: {
    finalizing: handling({ run_id: 'r1' }, { state: 'running', phase: 'finalizing' },
      { version_no: 3 }),
    cancelling: handling({ run_id: 'r1' }, { state: 'cancelling', phase: 'executing' },
      { version_no: 3 }),
    waiting_children: handling({ run_id: 'r1' },
      { state: 'waiting_children', phase: 'waiting' }, { version_no: 3 }),
    needs_reconciliation: handling({ run_id: 'r1' },
      { state: 'needs_reconciliation', phase: 'idle' }, { version_no: 3 }),
    succeeded: handling({ run_id: 'r1' }, { state: 'succeeded', phase: 'idle' },
      { version_no: 3 }),
    queued: handling({ run_id: 'r1' }, { state: 'queued', phase: 'idle' },
      { version_no: 3 }),
    none: handling(null, null, null),
  },
  poll: {
    dispatched_running: context.shouldPollHandling('dispatched', 'running', 0, 100),
    dispatched_finalizing: context.shouldPollHandling('dispatched', 'running', 5, 100),
    dispatched_succeeded: context.shouldPollHandling('dispatched', 'succeeded', 6, 100),
    dispatched_failed: context.shouldPollHandling('dispatched', 'failed', 6, 100),
    queued_first: context.shouldPollHandling('queued', '', 0, 100),
    queued_at_cap: context.shouldPollHandling('queued', '', 100, 100),
    request_cancelled: context.shouldPollHandling('cancelled', 'running', 0, 100),
  },
  legacy_should_poll_dispatched: context.shouldPollRequest('dispatched', 0, 100),
};

process.stdout.write(JSON.stringify(out) + '\n');
"""


def chat_harness(web_dir: Path) -> str:
    return CHAT_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))


# ---------------------------------------------------------------------------
# Harness 4: one real Run snapshot -> page/chat text (real bytes)
# ---------------------------------------------------------------------------
TRUTH_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const run = __RUN__;
const context = { console: console };
vm.createContext(context);
for (const name of ['runstream.js', 'runs.js', 'chat.js']) {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', name), 'utf8'),
    context,
    { filename: name }
  );
}

const truth = context.runStreamTruth(run, {});
const out = {
  truth: {
    label: truth.label,
    terminal: truth.terminal,
    isSuccess: truth.isSuccess,
    isFailure: truth.isFailure,
    detail: truth.detail,
    actions: truth.actions,
    externalWritesNote: truth.externalWritesNote,
  },
  runs_state_note: context.describeRunState(run, {}),
  runs_lifecycle: context.runLifecycle(run),
  runs_cancel_panel: context.describeRunCancel(run, {
    cancelRequestedAt: run.cancel_requested_at || '',
  }),
  cancel_accepted: context.runStreamCancelAcceptedText(run),
  chat_handling: context.describeHandling(
    { run_id: run.id || 'run-1' }, run, { version_no: 3 }
  ),
  chat_polls_after_dispatch: context.shouldPollHandling
    ? context.shouldPollHandling('dispatched', run.state, 0, 100)
    : null,
};
process.stdout.write(JSON.stringify(out) + '\n');
"""


def truth_harness(web_dir: Path, run: dict) -> str:
    return TRUTH_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir))).replace(
        "__RUN__", json.dumps(run, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Harness 5: the /runs dedupe/cursor helpers on real SSE frames
# ---------------------------------------------------------------------------
DEDUPE_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const frames = __FRAMES__;
const context = { console: console };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runs.js'), 'utf8'),
  context,
  { filename: 'runs.js' }
);

let events = [];
let applied = 0;
for (const frame of frames) {
  const event = context.runEventFromFrame(frame);
  if (!event) {
    continue;
  }
  const next = context.applyRunEvent(events, event);
  if (next !== events) {
    applied += 1;
  }
  events = next;
}
const out = {
  applied: applied,
  seqs: events.map((event) => event.seq),
  types: events.map((event) => event.type),
  states: events.map((event) => (event.payload && event.payload.state) || ''),
  cursor: context.nextLastEventID(events),
  terminal: context.timelineTerminalState(events),
};
process.stdout.write(JSON.stringify(out) + '\n');
"""


def dedupe_harness(web_dir: Path, frames: list[dict]) -> str:
    return DEDUPE_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir))).replace(
        "__FRAMES__", json.dumps(frames, ensure_ascii=False)
    )


# ---------------------------------------------------------------------------
# Harness 6: child waiting summary on the real owner-only children list
# ---------------------------------------------------------------------------
CHILDREN_HARNESS = r"""
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

const summary = context.runStreamChildSummary(__CHILDREN__, __PARENT__);
process.stdout.write(JSON.stringify(summary) + '\n');
"""


def children_harness(web_dir: Path, children: list[dict], parent: dict) -> str:
    return (
        CHILDREN_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__CHILDREN__", json.dumps(children, ensure_ascii=False))
        .replace("__PARENT__", json.dumps(parent, ensure_ascii=False))
    )


# ---------------------------------------------------------------------------
# Harness 7: artifact readiness on the real owner-only artifact list
# ---------------------------------------------------------------------------
ARTIFACT_HARNESS = r"""
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

const readout = context.runStreamArtifactReadout(__BODY__, __RUN__);
process.stdout.write(JSON.stringify(readout) + '\n');
"""


def artifact_harness(web_dir: Path, body: dict, run: dict) -> str:
    return (
        ARTIFACT_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir)))
        .replace("__BODY__", json.dumps(body, ensure_ascii=False))
        .replace("__RUN__", json.dumps(run, ensure_ascii=False))
    )


# ---------------------------------------------------------------------------
# Harness 8: the attachments page's file-readiness vocabulary
# ---------------------------------------------------------------------------
ATTACHMENTS_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const context = { console: console };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'attachments.js'), 'utf8'),
  context,
  { filename: 'attachments.js' }
);

const states = __STATES__;
const out = { terminal: {}, note: {}, poll: {} };
for (const state of states) {
  out.terminal[state] = context.artifactIsTerminal(state);
  out.note[state] = context.artifactStateNote(state);
  out.poll[state] = context.shouldPollArtifact(state, 0, 100);
}
process.stdout.write(JSON.stringify(out) + '\n');
"""


def attachments_harness(web_dir: Path, states: list[str]) -> str:
    return ATTACHMENTS_HARNESS.replace("__WEB_DIR__", json.dumps(str(web_dir))).replace(
        "__STATES__", json.dumps(states)
    )


__all__ = [
    "CONTRACT",
    "PRODUCT_ROOT",
    "artifact_harness",
    "attachments_harness",
    "chat_harness",
    "children_harness",
    "contract_run_enums",
    "dedupe_harness",
    "module_harness",
    "runs_page_harness",
    "truth_harness",
    "run_node",
]
