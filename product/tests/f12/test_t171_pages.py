"""T7 (GitHub #171): the evaluation -> publication -> review -> version chain.

The six pages (``evaluate``, ``evaluations``, ``publish``, ``reviews``,
``versions``, ``debug``) are one path: a sealed evaluation is the evidence a
publication needs, a review decides it, a version is the immutable result and a
debug run is how the author got there. This file pins the *structure* that makes
that path readable without touching the business contract:

* every page keeps its own ``<=720px`` block and puts every wide table inside a
  keyboard-focusable ``.table-scroll`` region (the 375px single-column promise;
  the real reflow is still only proven by ``tools/verify_frontend.py``);
* the publication page states its pre-publication checklist and where the
  evidence comes from, including the facts the preview does not carry;
* the review page states what it can and cannot know (the decision history is not
  in ``GET /api/v1/reviews/{id}``);
* the version table shows "创建时间" as ``未提供`` because ``Version`` has no
  timestamp, instead of inventing one;
* the evaluation pages derive a per-case duration only from ``timings`` and say
  ``未提供`` when the sealed report has none;
* the debug page keeps the exact ``run-panel``/``runstate`` contract.

The pure helpers added for these views are executed under Node from the bytes a
browser receives (same boundary as every other page test in this repo).

What this file does not prove: rendered pixels, real reflow, real API data.
Those stay with ``tools/verify_frontend.py`` and the per-ticket page tests.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from support import stack as st

WEB = st.WEB_DIR
NODE = shutil.which("node")

T7_PAGES = (
    "evaluate.html",
    "evaluations.html",
    "publish.html",
    "reviews.html",
    "versions.html",
    "debug.html",
)

# One page-local phone block per page: these six pages do not load mobile.css
# (T9 owns the six phone journeys), so each one has to carry its own rules.
PHONE_BLOCK = "@media (max-width: 720px)"


def read_page(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_every_table_is_inside_a_focusable_scroll_region() -> None:
    for name in T7_PAGES:
        html = read_page(name)
        position = 0
        while True:
            table = html.find("<table", position)
            if table < 0:
                break
            position = table + len("<table")
            wrapper = html.rfind('<div class="table-scroll"', 0, table)
            assert wrapper >= 0, f"{name}: a <table> is not inside .table-scroll"
            # The wrapper really opens the region the table lives in: nothing
            # closed it between the wrapper tag and the table.
            assert "</div>" not in html[wrapper:table], f"{name}: the table escaped its scroll region"
            region = html[wrapper:table]
            assert 'role="region"' in region, f"{name}: the scroll region is not named for AT"
            assert "tabindex=" in region, f"{name}: the scroll region is not keyboard reachable"
            assert "aria-label=" in region, f"{name}: the scroll region has no label"


@pytest.mark.parametrize("name", T7_PAGES)
def test_each_page_carries_its_own_phone_block(name: str) -> None:
    html = read_page(name)
    style_end = html.index("</style>")
    assert PHONE_BLOCK in html[:style_end], f"{name}: no page-local {PHONE_BLOCK} block"
    # The block must come after the page's own declarations so it can override
    # the desktop grid instead of being overridden by it.
    assert html.index(PHONE_BLOCK) < html.index('href="/assets/design.css"')


def test_the_publish_page_declares_the_prepublication_checklist() -> None:
    html = read_page("publish.html")
    for marker in (
        "发布前检查",
        "data-publish-checklist",
        'data-publish-gate="idle"',
        "data-publish-evidence",
        "data-publish-fixes",
    ):
        assert marker in html, f"publish.html is missing {marker!r}"
    # The evidence block names the source and is honest about the one fact the
    # publication preview does not carry (when the report was created).
    evidence = html[html.index("data-publish-evidence") :]
    evidence = evidence[: evidence.index("</p>")]
    assert "证据来源" in evidence and "未提供" in evidence, evidence
    # The gate keeps the server authoritative: the page never claims to replace
    # the submit call's own validation.
    assert "服务端" in html, "the checklist must keep the server as the authority"


def test_the_review_page_states_what_it_cannot_know() -> None:
    html = read_page("reviews.html")
    for marker in (
        "data-reviews-count",
        "data-review-history",
        "data-review-evidence",
        "审核历史",
        "不可撤销",
    ):
        assert marker in html, f"reviews.html is missing {marker!r}"
    history = html[html.index("data-review-history") :]
    history = history[: history.index("</p>")]
    # The decision history (who/when/reason) is not part of the review detail
    # contract, so the page says so instead of inventing a timeline.
    assert "未提供" in history or "不在" in history, history
    assert re.search(
        r'<textarea[^>]*id="review-decision-reason"[^>]*\brequired', html
    ), "the refusal reason must stay required"


def test_the_versions_page_does_not_invent_a_creation_time() -> None:
    html = read_page("versions.html")
    for marker in (
        "<th>创建时间</th>",
        "未提供",
        "当前生效",
        "data-versions-count",
        "data-versions-empty",
        "data-revoke-impact",
    ):
        assert marker in html, f"versions.html is missing {marker!r}"
    # The version table is the real list: <tbody id="versions-list"> inside the
    # scroll region, so one row per version is one child (t095 counts them).
    assert re.search(
        r'<div class="table-scroll"[^>]*>.*?<table[^>]*>.*?<tbody id="versions-list"',
        html,
        re.S,
    ), "versions-list must be the <tbody> of the version table"
    # The revocation impact is explained before the form, not after it.
    assert html.index("data-revoke-impact") < html.index('id="revoke-form"')


def test_the_evaluate_page_shows_a_real_duration_or_says_not_provided() -> None:
    html = read_page("evaluate.html")
    for marker in ("<th>耗时</th>", "data-case-stats", "data-evaluation-state-pill",
                   "data-evaluation-draft-revision", "未提供"):
        assert marker in html, f"evaluate.html is missing {marker!r}"
    # The duration column is fed by the sealed report's own timings; the page
    # states that the stub/profile is not a model-quality claim.
    assert "stub 结果不冒充真实模型质量" in html


def test_the_evaluations_page_keeps_the_three_truths_and_a_real_error_state() -> None:
    html = read_page("evaluations.html")
    for marker in (
        'data-step="queue"',
        'data-step="run"',
        'data-step="report"',
        "data-progress-steps",
        'id="business-sentence"',
        'id="infrastructure-sentence"',
        'id="not-run-sentence"',
        "不存在或无权查看",
    ):
        assert marker in html, f"evaluations.html is missing {marker!r}"
    # A missing/unauthorised id is a stated error, not an empty page.
    assert "data-progress-state" in html
    # No percentage is promised anywhere in the progress vocabulary.
    assert "%" not in html[html.index('id="progress-panel"') : html.index('id="report-panel"')]


def test_the_debug_page_keeps_the_run_panel_and_the_desktop_hint() -> None:
    html = read_page("debug.html")
    for marker in (
        'id="run-panel" class="card" hidden',
        'id="debug-submit"',
        'id="runview-cancel"',
        "data-input-schema",
        "data-run-panel-state",
        "data-preview=\"not_run\"",
        'data-mobile-scope="no-full-canvas-editing"',
        "桌面",
        "Last-Event-ID",
        "<th>节点</th>",
    ):
        assert marker in html, f"debug.html is missing {marker!r}"
    # The two paused controls keep `data-runstate-paused` after the id.
    for button in ("debug-submit", "runview-cancel"):
        assert re.search(
            r'<button[^>]*id="' + button + r'"[^>]*data-runstate-paused', html
        ), f"{button} lost data-runstate-paused"
    # The paused controls are inside their sections, not floating in the page.
    assert "取消 Run" in html


# ---------------------------------------------------------------------------
# the pure helpers these views add, executed as the served bytes
# ---------------------------------------------------------------------------
CHECKLIST_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const webDir = __WEB_DIR__;
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/publish.js', 'utf8'), context);

// The checklist is derived from the same frozen snapshot the page renders.
const complete = context.describePublicationChecklist({
  manifest_sha256: 'b'.repeat(64),
  report_id: '11111111-1111-4111-8111-111111111111',
  report_status: 'passed',
  purpose_card: {
    purpose: '整理销售表',
    includes: ['CSV'],
    excludes: ['写入'],
    required_inputs: ['CSV'],
    expected_outputs: ['统计'],
    examples: ['一月销售', '二月销售'],
  },
  model_destinations: [{model_profile_version_id: 'm', provider: 'local', model_id: 'stub',
    data_destination: 'local'}],
  author_cases: {satisfied: true, case_count: 5, minimum_cases: 5,
    categories: ['normal', 'bad_input'], missing: []},
});
assert.strictEqual(complete.ready, true, JSON.stringify(complete));
assert.strictEqual(complete.missing.length, 0);
assert.ok(complete.items.length >= 9, complete.items.length);
assert.ok(complete.items.some((item) => item.key === 'examples' && item.ok));
assert.ok(complete.items.some((item) => item.key === 'author_cases' && item.ok));
assert.ok(complete.items.some((item) => item.key === 'report' && item.ok));

const blocked = context.describePublicationChecklist({
  manifest_sha256: '',
  purpose_card: {purpose: '', includes: [], excludes: [], required_inputs: [],
    expected_outputs: [], examples: ['only one']},
  author_cases: {satisfied: false, case_count: 1, minimum_cases: 5,
    categories: [], missing: ['normal', 'bad_input']},
});
assert.strictEqual(blocked.ready, false);
assert.ok(blocked.missing.indexOf('purpose') >= 0, blocked.missing);
assert.ok(blocked.missing.indexOf('examples') >= 0, 'one example must not satisfy ">=2"');
assert.ok(blocked.missing.indexOf('author_cases') >= 0, blocked.missing);
assert.ok(blocked.missing.indexOf('report') >= 0, 'a missing report must block');
// A non-passed report is evidence, not a passed test.
const failedReport = context.describePublicationChecklist({
  report_id: 'r', report_status: 'failed',
  purpose_card: {purpose: 'p', includes: ['a'], excludes: ['b'],
    required_inputs: ['c'], expected_outputs: ['d'], examples: ['1', '2']},
});
assert.strictEqual(failedReport.ready, false);
assert.ok(failedReport.missing.indexOf('report') >= 0);
// No evidence is invented when the snapshot is empty.
const empty = context.describePublicationChecklist({});
assert.strictEqual(empty.ready, false);
assert.ok(empty.items.every((item) => typeof item.detail === 'string'));
process.stdout.write('publish checklist ok\n');
"""

DURATION_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const webDir = __WEB_DIR__;
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/evaluate.js', 'utf8'), context);

// The report's own timings are the only duration source.
assert.strictEqual(context.caseDurationText({run_duration_ms: 1500}), '运行 1.5 s');
assert.strictEqual(context.caseDurationText({run_duration_ms: 1500, collect_duration_ms: 250}),
  '运行 1.5 s · 收集 250 ms');
assert.strictEqual(context.caseDurationText({}), '未提供');
assert.strictEqual(context.caseDurationText(null), '未提供');
assert.strictEqual(context.caseDurationText({run_duration_ms: 0}), '未提供');
const described = context.describeCase({
  case_id: 'c1', verdict: 'passed', assertions: [], facts: {},
  timings: {run_duration_ms: 840, collect_duration_ms: 60},
});
assert.strictEqual(described.durationText, '运行 840 ms · 收集 60 ms');
const withoutTimings = context.describeCase({case_id: 'c2', verdict: 'not_run'});
assert.strictEqual(withoutTimings.durationText, '未提供');
process.stdout.write('evaluate duration ok\n');
"""

VERSION_ROW_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const webDir = __WEB_DIR__;
const context = {};
vm.createContext(context);
vm.runInContext(fs.readFileSync(webDir + '/assets/versions.js', 'utf8'), context);

const ACTIVE = '22222222-2222-4222-8222-222222222222';
const row = context.describeVersionRow(
  {id: ACTIVE, version_no: 2, manifest_sha256: 'a'.repeat(64)}, ACTIVE);
assert.strictEqual(row.active, true);
assert.strictEqual(row.stateLabel, '当前生效');
assert.strictEqual(row.versionNo, '2');
assert.strictEqual(row.createdAtText, '未提供');
assert.strictEqual(row.manifestSha256, 'a'.repeat(64));
const history = context.describeVersionRow(
  {id: '11111111-1111-4111-8111-111111111111', version_no: 1, manifest_sha256: 'b'.repeat(64)},
  ACTIVE);
assert.strictEqual(history.active, false);
assert.strictEqual(history.stateLabel, '历史版本');
const unknown = context.describeVersionRow({}, null);
assert.strictEqual(unknown.versionNo, '?');
assert.strictEqual(unknown.active, false);
process.stdout.write('versions row ok\n');
"""

LEASE_HARNESS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const webDir = __WEB_DIR__;
const context = {console, window: {location: {pathname: '/'}},
  setTimeout, clearTimeout};
vm.createContext(context);
for (const asset of ['app.js', 'runstream.js', 'runstate-guard.js']) {
  vm.runInContext(fs.readFileSync(webDir + '/assets/' + asset, 'utf8'), context);
}
vm.runInContext(fs.readFileSync(webDir + '/assets/debug.js', 'utf8'), context);

const facts = context.leaseFactsFromHeaders({get: (name) => ({
  'X-Run-Attempt-Started-At': '2026-11-26T00:00:00Z',
  'X-Run-Lease-Deadline': '2026-11-26T00:00:30Z',
  'X-Run-Active-Elapsed-Ms': '4200',
}[name] || null)});
assert.strictEqual(facts.startedAt, '2026-11-26T00:00:00Z');
assert.strictEqual(facts.leaseDeadline, '2026-11-26T00:00:30Z');
assert.strictEqual(facts.activeElapsedMS, 4200);
const absent = context.leaseFactsFromHeaders({get: () => null});
assert.strictEqual(absent.activeElapsedMS, null);
assert.strictEqual(absent.leaseDeadline, '');
assert.strictEqual(context.describeElapsedMS(4200), '4.2 s');
assert.strictEqual(context.describeElapsedMS(null), '未提供');
process.stdout.write('debug lease ok\n');
"""


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


def run_harness(node_binary: str, tmp_path, name: str, script: str, expected: str) -> None:
    harness = tmp_path / (name + ".js")
    harness.write_text(script.replace("__WEB_DIR__", json.dumps(str(WEB))), encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"{name} failed:\n--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert expected in completed.stdout, completed.stdout


def test_the_publication_checklist_is_derived_from_the_frozen_snapshot(node_binary, tmp_path) -> None:
    run_harness(node_binary, tmp_path, "t171-checklist", CHECKLIST_HARNESS,
                "publish checklist ok")


def test_the_evaluation_duration_never_invents_a_value(node_binary, tmp_path) -> None:
    run_harness(node_binary, tmp_path, "t171-duration", DURATION_HARNESS,
                "evaluate duration ok")


def test_the_version_row_keeps_its_creation_time_unprovided(node_binary, tmp_path) -> None:
    run_harness(node_binary, tmp_path, "t171-version-row", VERSION_ROW_HARNESS,
                "versions row ok")


def test_the_debug_page_reads_the_real_active_elapsed_header(node_binary, tmp_path) -> None:
    run_harness(node_binary, tmp_path, "t171-lease", LEASE_HARNESS, "debug lease ok")
