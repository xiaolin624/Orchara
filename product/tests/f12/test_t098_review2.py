"""F12 second review, page findings F1/F2 (T098 halves).

手段声明（硬性）: 本文件**不是 Playwright / 真实浏览器 DOM**。它用 Node 执行
``apps/web/src/assets/personal-data.js`` 的**已提交真实字节**，DOM/fetch 只是最小
手写 shim（先例 ``tests/support/t096.py``、``tests/f12/test_t098_mobile_pages.py``）。
它证明真实脚本字节、真实请求头、真实渲染文本与“没有发出请求”，**不证明**浏览器
表单控件语义、CSS 布局或真实网络。

覆盖第二轮复核的两条页面发现：

* F1（Standards P1）：诊断授权“默认不勾选”在空选择时省略
  ``X-Diagnostic-Object-Scope``，而服务端在**没有该头**时推导该 Run 的**全部**
  对象（``internal/diagnostics/grants.go:8-10``）。页面现在必须拒绝空选择并说明
  原因，非空选择必须总带该头且等于选择。
* F2（Standards P2）：``indexing`` / ``failed`` 的说明必须承认“若此前已有已就绪
  索引，旧的仍是检索来源”（ADR 0018:26,58-59），并区分首次索引。
"""

from __future__ import annotations

import json
import shutil

import pytest

from support import stack as st
from support import t096

WEB = st.WEB_DIR
NODE = shutil.which("node")

RUN_ID = "11111111-1111-4111-8111-111111111111"
GRANTEE_ID = "22222222-2222-4222-8222-222222222222"
ARTIFACT_A = "33333333-3333-4333-8333-333333333333"
ARTIFACT_B = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page bytes cannot be executed")
    return NODE


# ---------------------------------------------------------------------------
# F1: the diagnostic grant request must never omit the object scope header
# ---------------------------------------------------------------------------
DIAGNOSTIC_GRANT_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const RUN_ID = __RUN_ID__;
const GRANTEE_ID = __GRANTEE_ID__;
const ARTIFACT_A = __ARTIFACT_A__;
const ARTIFACT_B = __ARTIFACT_B__;
const EXPIRY = '2099-01-01T00:00';

class El {
  constructor(tag, id) {
    this.tagName = String(tag || 'div').toUpperCase();
    this.id = id || '';
    this.children = [];
    this._listeners = {};
    this.className = '';
    this._text = '';
    this.hidden = false;
    this.value = '';
    this.checked = false;
    this.type = '';
    this.dataset = {};
  }
  set textContent(value) { this._text = String(value || ''); if (value === '') this.children = []; }
  get textContent() {
    return (this._text || '') + this.children.map((child) => child.textContent || '').join('');
  }
  appendChild(child) { this.children.push(child); return child; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...nodes) { this.children = nodes; }
  addEventListener(type, fn) {
    (this._listeners[type] = this._listeners[type] || []).push(fn);
  }
  setAttribute() {}
  descendants() {
    const out = [];
    const walk = (node) => { for (const child of node.children || []) { out.push(child); walk(child); } };
    walk(this);
    return out;
  }
  querySelectorAll(selector) {
    if (selector === 'input[type="checkbox"]') {
      return this.descendants().filter((el) => el.tagName === 'INPUT' && el.type === 'checkbox');
    }
    return [];
  }
}

const ids = [
  'grant-run-id', 'grant-grantee-id', 'grant-purposes', 'grant-expires',
  'grant-objects-list', 'grant-objects-status', 'grant-status',
  'grants-list', 'grants-empty', 'action-status',
];
const byId = new Map();
for (const id of ids) byId.set(id, new El('div', id));
const document = {
  getElementById(id) { return byId.get(id) || null; },
  createElement(tag) { return new El(tag); },
  createTextNode(text) { return { textContent: String(text) }; },
  addEventListener() {},
};

const apiCalls = [];
const fetchCalls = [];
const csrfCalls = [];
async function api(pathname, options) {
  apiCalls.push({ path: pathname, options: options || {} });
  if (pathname === '/api/v1/diagnostic-grants' && (!options || !options.method)) {
    return { items: [], next_cursor: null };
  }
  return { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', state: 'active' };
}

const context = {
  console: console,
  window: { location: { assign() {} }, confirm: () => true },
  document: document,
  api: api,
  fetch: async function (pathname) {
    fetchCalls.push(pathname);
    throw new Error('unexpected fetch ' + pathname);
  },
  csrf: async function () { csrfCalls.push(true); return 'csrf-token'; },
  requireLogin: async () => ({ roles: ['user'] }),
  setTimeout: () => 1,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/personal-data.js', 'utf8'),
  context,
  { filename: 'personal-data.js' }
);

function grantInput(objects) {
  return {
    runID: RUN_ID, granteeID: GRANTEE_ID, purposes: 'troubleshooting',
    expiresAt: EXPIRY, objects: objects,
  };
}

(async () => {
  const out = {};

  // F1-a: the request builder refuses an empty selection instead of silently
  // omitting the header (which makes the server derive the whole run).
  let helperError = null;
  try {
    context.diagnosticGrantCreateRequest(grantInput([]));
  } catch (error) {
    helperError = error && error.message ? error.message : String(error);
  }
  out.empty_helper_threw = helperError !== null;
  out.empty_helper_error = helperError;
  out.empty_helper_header = null;

  // F1-b: a single object -> the documented header equals the selection.
  const single = context.diagnosticGrantCreateRequest(grantInput([ARTIFACT_A]));
  out.single_header = single.headers['X-Diagnostic-Object-Scope'] || null;
  out.single_header_keys = Object.keys(single.headers).sort();

  // F1-c: multiple objects -> every selected entry, in selection order.
  const multi = context.diagnosticGrantCreateRequest(grantInput([ARTIFACT_A, ARTIFACT_B]));
  out.multi_header = multi.headers['X-Diagnostic-Object-Scope'] || null;
  out.multi_header_keys = Object.keys(multi.headers).sort();

  // F1-d: the page submit path with nothing ticked: the error is shown in
  // #grant-status, and neither the API client, the CSRF endpoint nor fetch ran.
  context.renderGrantObjects([
    { id: ARTIFACT_A, state: 'ready' },
    { id: ARTIFACT_B, state: 'ready' },
  ]);
  const list = document.getElementById('grant-objects-list');
  const boxes = list.querySelectorAll('input[type="checkbox"]');
  out.list_boxes = boxes.map((box) => ({ value: box.value, checked: box.checked }));
  out.selected_default = context.selectedGrantObjects();

  document.getElementById('grant-run-id').value = RUN_ID;
  document.getElementById('grant-grantee-id').value = GRANTEE_ID;
  document.getElementById('grant-purposes').value = 'troubleshooting';
  document.getElementById('grant-expires').value = EXPIRY;

  await context.createDiagnosticGrant();
  out.empty_page_status = document.getElementById('grant-status').textContent;
  out.empty_api_calls = apiCalls.slice();
  out.empty_fetch_calls = fetchCalls.slice();
  out.empty_csrf_calls = csrfCalls.length;

  // F1-e: the page submit path with one ticked object really sends the header
  // that matches the visible selection (the checkbox carries the artifact id).
  boxes[0].checked = true;
  await context.createDiagnosticGrant();
  const postsAfterSingle = apiCalls.filter(
    (call) => call.options && call.options.method === 'POST'
  );
  out.single_page_posts = postsAfterSingle.map((call) => ({
    path: call.path,
    headers: call.options.headers,
    body: call.options.body,
  }));
  out.single_page_status = document.getElementById('grant-status').textContent;

  // F1-f: and with two ticked objects the header stays equal to the selection.
  boxes[0].checked = false;
  boxes[1].checked = true;
  boxes[0].checked = true;
  await context.createDiagnosticGrant();
  const postsAfterMulti = apiCalls.filter(
    (call) => call.options && call.options.method === 'POST'
  );
  out.multi_page_posts = postsAfterMulti.map((call) => ({
    path: call.path,
    headers: call.options.headers,
    body: call.options.body,
  }));
  out.multi_page_status = document.getElementById('grant-status').textContent;
  out.csrf_calls = csrfCalls.length;

  process.stdout.write(JSON.stringify(out) + '\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : error);
  process.exitCode = 1;
});
"""


def diagnostic_grant_report(node_binary: str, tmp_path) -> dict:
    script = (
        DIAGNOSTIC_GRANT_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
        .replace("__RUN_ID__", json.dumps(RUN_ID))
        .replace("__GRANTEE_ID__", json.dumps(GRANTEE_ID))
        .replace("__ARTIFACT_A__", json.dumps(ARTIFACT_A))
        .replace("__ARTIFACT_B__", json.dumps(ARTIFACT_B))
    )
    return t096.run_node(
        node_binary, tmp_path, script, name="t098-review2-grant.js"
    )


def test_an_empty_diagnostic_grant_selection_is_refused_and_never_sent(
    node_binary: str, tmp_path
) -> None:
    report = diagnostic_grant_report(node_binary, tmp_path)

    # F1-a: refused, with the reason (UX.md:35 / DATA-POLICY.md:7).
    assert report["empty_helper_threw"] is True, report
    assert "至少勾选一个诊断对象" in report["empty_helper_error"], report
    assert "全部" in report["empty_helper_error"], report

    # F1-b/c: an explicit selection always carries the header, exactly.
    expected_single = f"artifact:{ARTIFACT_A}"
    expected_multi = f"artifact:{ARTIFACT_A},artifact:{ARTIFACT_B}"
    assert report["single_header"] == expected_single, report
    assert report["multi_header"] == expected_multi, report
    assert report["single_header_keys"] == ["X-Diagnostic-Object-Scope"], report
    assert report["multi_header_keys"] == ["X-Diagnostic-Object-Scope"], report

    # F1-d: the page shows the refusal and sends nothing at all.
    assert report["selected_default"] == [], report
    assert report["empty_page_status"].startswith("请至少勾选一个诊断对象"), report
    assert "全部对象" in report["empty_page_status"], report
    assert report["empty_api_calls"] == [], report
    assert report["empty_fetch_calls"] == [], report
    assert report["empty_csrf_calls"] == 0, report

    # F1-e/f: the real page path with a selection produces the real POST shape.
    assert len(report["single_page_posts"]) == 1, report
    single_post = report["single_page_posts"][0]
    assert single_post["path"] == "/api/v1/diagnostic-grants", report
    assert single_post["headers"] == {
        "X-Diagnostic-Object-Scope": expected_single
    }, report
    assert single_post["body"]["run_id"] == RUN_ID, report

    assert len(report["multi_page_posts"]) == 2, report
    multi_post = report["multi_page_posts"][1]
    assert multi_post["headers"] == {
        "X-Diagnostic-Object-Scope": expected_multi
    }, report
    # The page must never reject the user's own ticked selection: before the
    # checkbox value fix it carried "artifact:<uuid>" into a builder that expects
    # a bare uuid and the status read "artifact id 必须是 UUID" with no request.
    for status_text in (report["single_page_status"], report["multi_page_status"]):
        assert "artifact id 必须是 UUID" not in status_text, report


# ---------------------------------------------------------------------------
# F2: "indexing"/"failed" must not claim that search is empty for a ready doc
# ---------------------------------------------------------------------------
DOCUMENT_READOUT_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');

const webDir = __WEB_DIR__;
const context = {
  console: console,
  document: {
    getElementById() { return null; },
    createElement() { return {}; },
    addEventListener() {},
  },
  window: { location: { assign() {} } },
  setTimeout: () => 0,
  clearTimeout: () => {},
};
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(webDir + '/assets/personal-data.js', 'utf8'),
  context,
  { filename: 'personal-data.js' }
);

const identities = ['uploaded', 'scanning', 'indexing', 'ready', 'failed', 'deleting', 'deleted'];
const out = { notes: {}, states: {} };
for (const state of identities) {
  const readout = context.documentReadout({
    id: '55555555-5555-4555-8555-555555555555', state: state, revision: 1,
  });
  out.notes[state] = readout.note;
  out.states[state] = readout.stateText;
}
process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_rebuild_and_failure_notes_keep_the_previous_index_readable(
    node_binary: str, tmp_path
) -> None:
    script = DOCUMENT_READOUT_HARNESS.replace("__WEB_DIR__", json.dumps(str(WEB)))
    report = t096.run_node(
        node_binary, tmp_path, script, name="t098-review2-readout.js"
    )
    indexing = report["notes"]["indexing"]
    failed = report["notes"]["failed"]

    # The false claims the review found are gone, literally.
    for name, note in (("indexing", indexing), ("failed", failed)):
        assert "完成前检索为空" not in note, (name, note)
        assert "不会返回旧正文" not in note, (name, note)

    # Both notes must state the ADR 0018 rule (old active index stays readable)
    # and distinguish the first-time case where there is no old index.
    for name, note in (("indexing", indexing), ("failed", failed)):
        assert "旧" in note, (name, note)
        assert "索引" in note, (name, note)
        assert "首次" in note, (name, note)

    # The other states keep their existing, truthful vocabulary.
    assert "可检索" in report["notes"]["ready"], report
    assert "扫描" in report["notes"]["scanning"], report
    assert "不可访问" in report["notes"]["deleted"], report
