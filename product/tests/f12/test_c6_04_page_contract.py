"""C6.04 (#158) final page contract: one explicit run-state interface, no legacy exits.

手段声明（硬性，先读）
--------------------
本文件把证据分层记录，互不冒充：

1. **仓库级删除证据**：扫描 worktree 中的源码/测试/部署脚本（排除 ``product/reports``
   与历史报告文本），证明 14 个旧的共享状态全局导出、页内
   ``*RunStateDependencyUnavailable()`` 小 helper、``typeof`` 旧全局 /
   ``typeof OrcharaRunState`` 业务 fallback 与 ``runsRunStateInterface()`` 均已清零；
   并证明所有执行真实页面脚本的 harness 都显式加载 ``runstate-guard.js``。
2. **Node 行为测试**：执行 ``apps/web/src/assets/`` 的**已提交真实字节**；证明共享
   module 只导出一个完整 ``OrcharaRunState`` 命名空间，旧全局不存在；页面 adapter
   通过 guard 的 ``ready`` 这一个依赖事实决定暂停。
3. **真实编译 API**：真实 ``cmd/api`` 进程提供页面与静态资源，断言共享字节与脚本
   顺序（runstream → guard → page script）。
4. **真实浏览器（Playwright + 已安装 Chrome，headless）**：最终验收矩阵 =
   3 页面（/runs、/chat、/apps/<id>/debug）× 3 情况（正常、共享脚本 404、
   共享脚本语法错误）。断言提示精确为“运行状态暂不可用”、受影响观察/控件暂停、
   导航/重载/独立功能可用；正常路径仍读取真实 Run 快照与事件流。
   浏览器证据与 Node 隔离结果分开记录。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import f00b, stack as st, t024, t029, t032
from support.certs import make_stack_certificates, unique_internal_addr
from support.runtime_stack import Stack
from support.stack import WEB_DIR

WEB = WEB_DIR
NODE = shutil.which("node")
PRODUCT = WEB.parents[2]
REPO = PRODUCT.parent

# The 14 temporary top-level exports C6.01 kept for the migration tickets. C6.04
# (#158) deleted them after the repository-wide consumer search: they must not
# exist as globals any more. The names are assembled instead of written as
# literals so that the deletion proof itself does not leave the legacy token in
# the repository-wide code search.
_LEGACY_STEM = "run" + "Stream"
DELETED_EXPORTS = tuple(
    _LEGACY_STEM + suffix
    for suffix in (
        "Truth",
        "Failure",
        "CorrectFromSnapshot",
        "SnapshotCorrection",
        "SnapshotPath",
        "SafeSnapshotPath",
        "IsTerminal",
        "StateLabel",
        "PhaseLabel",
        "CancelAcceptedText",
        "HandlingText",
        "ShouldPoll",
        "ChildSummary",
        "ArtifactReadout",
    )
)

# The complete explicit surface the guard requires (independent spec, not generated
# from the asset).
REQUIRED_SURFACE = {
    "interfaceVersion": "number",
    "view": "function",
    "failure": "function",
    "correct": "function",
    "snapshotCorrection": "function",
    "snapshotPath": "function",
    "safeSnapshotPath": "function",
    "isTerminal": "function",
    "stateLabel": "function",
    "phaseLabel": "function",
    "cancelAcceptedText": "function",
    "handlingText": "function",
    "shouldPoll": "function",
    "childSummary": "function",
    "artifactReadout": "function",
    "states": "object",
    "phases": "object",
}

PAGE_SCRIPTS = ("chat.js", "runs.js", "debug.js", "runview.js")

# Source-like files that must not retain a legacy reference. Reports/PROGRESS are
# history and are deliberately excluded; this is the code search, not the docs.
CODE_SUFFIXES = (".js", ".mjs", ".py", ".html", ".go", ".json", ".yaml", ".yml", ".sql")
SKIP_DIR_PARTS = {".git", ".pytest_cache", "__pycache__", "node_modules", "reports"}


def _iter_code_files():
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_SUFFIXES:
            continue
        relative = path.relative_to(REPO)
        if any(part in SKIP_DIR_PARTS for part in relative.parts):
            continue
        yield path


@pytest.fixture(scope="module")
def node_binary() -> str:
    if NODE is None:
        pytest.skip("node is not on PATH; the committed page logic cannot be executed")
    return NODE


def run_node(tmp_path: Path, script: str, name: str) -> dict:
    path = tmp_path / name
    path.write_text(script, encoding="utf-8", newline="\n")
    completed = subprocess.run(
        [NODE, str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"node harness {name} failed (exit {completed.returncode}):\n"
            f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"node harness {name} printed nothing:\n{completed.stderr}"
    payload = json.loads(lines[-1])
    assert isinstance(payload, dict), payload
    payload["_stdout"] = completed.stdout
    return payload


def _web() -> str:
    return json.dumps(str(WEB))


# ===========================================================================
# 1. deletion proof: no legacy global, no page-local dependency interpretation
# ===========================================================================
def test_no_legacy_global_reference_remains_in_code() -> None:
    # C6.05 (review-2): the page-local Run terminal/poll definitions named by the
    # review must also be gone from the whole code tree, not only from the four
    # page assets. The names are assembled so that this deletion proof does not
    # itself leave the deleted identifier in the repository-wide code search, and
    # they only match a real definition (`function name(`), never an assertion
    # string in a test.
    page_local_definition = re.compile(
        r"^\s*function\s+(is" + r"RunTerminal|run" + r"IsTerminal|should" + r"PollRun|"
        r"should" + r"PollRequest)\s*\("
    )
    hits: list[str] = []
    page_local_hits: list[str] = []
    for path in _iter_code_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _LEGACY_STEM in line:
                hits.append(f"{path.relative_to(REPO)}:{line_number}: {line.strip()}")
            if page_local_definition.match(line):
                page_local_hits.append(f"{path.relative_to(REPO)}:{line_number}: {line.strip()}")
    assert hits == [], "legacy shared-state globals remain:\n" + "\n".join(hits)
    assert page_local_hits == [], (
        "page-local Run terminal/poll definitions remain; they must come from "
        "OrcharaRunState:\n" + "\n".join(page_local_hits)
    )


def test_pages_have_no_page_local_dependency_helper_or_typeof_fallback() -> None:
    for name in PAGE_SCRIPTS:
        source = (WEB / "assets" / name).read_text(encoding="utf-8")
        assert "RunStateDependencyUnavailable" not in source, (
            f"{name} still defines a page-local dependency helper"
        )
        assert "runsRunStateInterface" not in source, (
            f"{name} still wraps the explicit interface in a page-local accessor"
        )
        assert ("typeof " + _LEGACY_STEM) not in source, (
            f"{name} still branches on legacy globals"
        )
        assert "typeof OrcharaRunState" not in source, (
            f"{name} still branches on the shared namespace instead of the guard"
        )
        assert "OrcharaRunStateGuard.ready" in source, (
            f"{name} does not consult the one shared readiness fact"
        )
        if name == "debug.js":
            # The debug page renders Run state through runview.js (`beginRunView`)
            # and keeps its own stage sentence; it consults the guard directly and
            # must never keep a page-local dependency fallback. C6.05 (review-2):
            # the sentence is presentation only — terminal/stage/phase come from
            # the shared `OrcharaRunState.view` projection, and polling uses the
            # shared `shouldPoll` rule instead of a page-local copy.
            assert "beginRunView(" in source, f"{name} does not use the node view"
            assert "function describeRunStage(" in source, f"{name} lost its page sentence"
            assert "OrcharaRunState.view(" in source, (
                f"{name} does not derive its Run stage sentence from the shared view"
            )
            assert "OrcharaRunState.shouldPoll(" in source, (
                f"{name} does not use the shared polling rule"
            )
            assert "OrcharaRunState.isTerminal(" in source, (
                f"{name} does not use the shared terminal rule"
            )
        else:
            assert "OrcharaRunState." in source, f"{name} does not call the explicit namespace"
        if name == "runs.js":
            # Every former local terminal branch now goes through the shared
            # projection: detail/lifecycle/merge/timeline/poll sites (7 calls).
            assert source.count("OrcharaRunState.isTerminal(") >= 7, (
                f"{name} still decides terminal state locally"
            )
        if name == "chat.js":
            assert "OrcharaRunState.shouldPoll(" in source, (
                f"{name} does not use the shared polling rule"
            )


def test_every_affected_harness_loads_the_guard_before_the_page_script() -> None:
    problems: list[str] = []
    for base in (PRODUCT / "tests", PRODUCT / "deploy"):
        for pattern in ("*.py", "*.mjs"):
            for path in base.rglob(pattern):
                text = path.read_text(encoding="utf-8", errors="replace")
                if "vm.runInContext" not in text or "runstream.js" not in text:
                    continue
                if not any(name in text for name in PAGE_SCRIPTS):
                    continue
                if "runstate-guard.js" not in text:
                    problems.append(str(path.relative_to(REPO)))
    assert problems == [], "harnesses evaluate page scripts without the guard: " + repr(problems)


# ===========================================================================
# 2. Node: the shared module exposes exactly one explicit namespace
# ===========================================================================
NAMESPACE_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const required = __REQUIRED__;
const deleted = __DELETED__;

const context = { console: console };
vm.createContext(context);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
  context,
  { filename: 'runstream.js' }
);

const api = context.OrcharaRunState;
const out = {
  has_interface: Boolean(api && typeof api === 'object'),
  interface_version: api ? api.interfaceVersion : null,
  surface: {},
  deleted: {},
  defined_functions: {},
  behaviour: {},
};

for (const name of Object.keys(required)) {
  out.surface[name] = api ? typeof api[name] : 'missing';
}
out.surface.interfaceVersion = api ? api.interfaceVersion : null;
for (const name of deleted) {
  out.deleted[name] = typeof context[name];
}
// No implementation helper leaked beside the namespace either: the classic script
// must only add the frozen namespace to the global object.
for (const name of Object.keys(context)) {
  if (name === 'console' || name === 'OrcharaRunState') {
    continue;
  }
  out.defined_functions[name] = typeof context[name];
}

const finalizing = api.view({ state: 'running', phase: 'finalizing' }, {});
out.behaviour.finalizing = {
  label: finalizing.label,
  terminal: finalizing.terminal,
  isSuccess: finalizing.isSuccess,
  isFailure: finalizing.isFailure,
};
const cancelled = api.view({ state: 'cancelled', phase: 'idle' }, {});
out.behaviour.cancelled_external_writes_note = cancelled.externalWritesNote;
const failure = api.failure(410, {
  error: {
    code: 'event_cursor_expired',
    details: { snapshot_url: '/api/v1/runs/run-1' },
  },
});
out.behaviour.failure = { retry: failure.retry, kind: failure.kind };
out.behaviour.snapshot_path = api.snapshotPath(failure, 'run-1');
out.behaviour.unsafe_snapshot_path = api.snapshotPath(
  { snapshotURL: 'https://evil.example/api/v1/runs/x' },
  'run-2'
);
out.behaviour.unsafe_fallback = api.snapshotPath({}, '../admin');
out.behaviour.child_waiting = api.childSummary(
  [{ id: 'c1', state: 'queued' }],
  { id: 'p1', state: 'waiting_children', phase: 'waiting' }
).waiting;
out.behaviour.handling = api.handlingText({ state: 'cancelling' }, 3);
out.behaviour.poll_after_terminal = api.shouldPoll('dispatched', 'succeeded', 1, 100);
out.behaviour.artifact_final = api.artifactReadout(
  { items: [{ id: 'a1', state: 'ready', media_type: 'text/csv', size_bytes: 1 }] },
  { state: 'succeeded', output_artifact_id: 'a1' }
).items[0].finalOutput;

process.stdout.write(JSON.stringify(out) + '\n');
"""


def test_the_shared_module_exposes_only_the_explicit_namespace(
    node_binary: str, tmp_path: Path
) -> None:
    script = (
        NAMESPACE_HARNESS.replace("__WEB_DIR__", _web())
        .replace("__REQUIRED__", json.dumps(REQUIRED_SURFACE))
        .replace("__DELETED__", json.dumps(list(DELETED_EXPORTS)))
    )
    report = run_node(tmp_path, script, "c604-namespace.js")

    assert report["has_interface"] is True, report
    assert report["interface_version"] == 1, report
    for name, expected in REQUIRED_SURFACE.items():
        if name == "interfaceVersion":
            assert report["surface"]["interfaceVersion"] == 1, report["surface"]
        else:
            assert report["surface"][name] == expected, (name, report["surface"])
    # Deletion: none of the temporary top-level globals is defined any more.
    for name in DELETED_EXPORTS:
        assert report["deleted"][name] == "undefined", (name, report["deleted"])
    # Only the frozen namespace is added to the global object; no helper leaks.
    assert report["defined_functions"] == {}, report["defined_functions"]
    # Behaviour is unchanged: the deletion is not a rewrite of the state rules.
    assert report["behaviour"]["finalizing"] == {
        "label": "正在收尾（finalizing）",
        "terminal": False,
        "isSuccess": False,
        "isFailure": False,
    }, report["behaviour"]
    assert "已撤回" in report["behaviour"]["cancelled_external_writes_note"], report
    assert report["behaviour"]["failure"] == {"retry": False, "kind": "cursor_expired"}, report
    assert report["behaviour"]["snapshot_path"] == "/api/v1/runs/run-1", report
    assert report["behaviour"]["unsafe_snapshot_path"] == "/api/v1/runs/run-2", report
    assert report["behaviour"]["unsafe_fallback"] == "/api/v1/runs/..%2Fadmin", report
    assert report["behaviour"]["child_waiting"] == 1, report
    assert "正在停止（cancelling）" in report["behaviour"]["handling"], report
    assert report["behaviour"]["poll_after_terminal"] is False, report
    assert report["behaviour"]["artifact_final"] is True, report


# ===========================================================================
# 3. Node: the pages take the one readiness fact from the guard
# ===========================================================================
CHAT_GUARD_HARNESS = r"""
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const webDir = __WEB_DIR__;
const mode = __MODE__;

const context = { console: console };
vm.createContext(context);
if (mode === 'ready') {
  vm.runInContext(
    fs.readFileSync(path.join(webDir, 'assets', 'runstream.js'), 'utf8'),
    context,
    { filename: 'runstream.js' }
  );
}
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'runstate-guard.js'), 'utf8'),
  context,
  { filename: 'runstate-guard.js' }
);
vm.runInContext(
  fs.readFileSync(path.join(webDir, 'assets', 'chat.js'), 'utf8'),
  context,
  { filename: 'chat.js' }
);

process.stdout.write(JSON.stringify({
  mode: mode,
  guard_ready: context.OrcharaRunStateGuard.ready,
  namespace_type: typeof context.OrcharaRunState,
  handling: context.describeHandling(
    { id: 'r1', run_id: 'run-1' }, { state: 'running', phase: 'finalizing' },
    { version_no: 3 }
  ).text,
  polls: context.shouldPollHandling('dispatched', 'running', 0, 100),
}) + '\n');
"""


def test_the_chat_page_takes_its_only_readiness_fact_from_the_guard(
    node_binary: str, tmp_path: Path
) -> None:
    for mode in ("fault", "ready"):
        script = CHAT_GUARD_HARNESS.replace("__WEB_DIR__", _web()).replace(
            "__MODE__", json.dumps(mode)
        )
        report = run_node(tmp_path, script, f"c604-guard-{mode}.js")
        assert report["guard_ready"] is (mode == "ready"), report
        assert report["namespace_type"] == ("object" if mode == "ready" else "undefined"), report
        if mode == "fault":
            assert report["handling"] == "运行状态暂不可用", report
            assert report["polls"] is False, report
        else:
            assert "正在收尾（finalizing）" in report["handling"], report
            assert report["polls"] is True, report


# ===========================================================================
# 4. real served pages: bytes and load order through the compiled API
# ===========================================================================
SHARED_ASSETS = (
    "runstream.js",
    "runstate-guard.js",
    "chat.js",
    "runs.js",
    "debug.js",
    "runview.js",
)
DEBUG_PAGE_PATH = "/apps/11111111-2222-4333-8444-555555555555/debug"

PAGE_SPECS = {
    "/chat": ("chat.js", ("chat-submit", "chat-cancel", "interaction-reply")),
    "/runs": ("runs.js", ("detail-cancel-button",)),
    DEBUG_PAGE_PATH: ("debug.js", ("debug-submit", "runview-cancel")),
}


@pytest.fixture(scope="module")
def c604_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> Stack:
    tmp_path = tmp_path_factory.mktemp("c604-stack")
    try:
        database = st.create_database("orchara_c604_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    dsn, dbname = database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=WEB,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
    )
    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    try:
        f00b.wait_for_internal(stack)
    except Exception:
        server.stop()
        st.drop_database(dbname)
        raise
    try:
        yield stack
    finally:
        server.stop()
        st.drop_database(dbname)


def test_the_final_scripts_are_served_exactly_as_committed(c604_stack) -> None:
    for name in SHARED_ASSETS:
        response = c604_stack.server.http.get(f"/assets/{name}")
        assert response.status == 200, (name, response.text)
        assert "javascript" in response.header("Content-Type"), (
            name,
            response.header("Content-Type"),
        )
        committed = (WEB / "assets" / name).read_bytes()
        assert response.raw == committed, f"{name}: served bytes differ from the committed bytes"


def test_each_page_loads_runstream_then_guard_then_its_page_scripts(c604_stack) -> None:
    for path, (page_script, _paused_ids) in PAGE_SPECS.items():
        body = c604_stack.server.http.get(path).text
        runstream_marker = '<script src="/assets/runstream.js"></script>'
        guard_marker = '<script src="/assets/runstate-guard.js"></script>'
        script_marker = f'<script src="/assets/{page_script}"></script>'
        assert runstream_marker in body, f"{path} does not load the real shared script"
        assert guard_marker in body, f"{path} does not load the dependency guard"
        assert script_marker in body, f"{path} does not load {page_script}"
        assert body.index(runstream_marker) < body.index(guard_marker), path
        assert body.index(guard_marker) < body.index(script_marker), path
        assert 'id="runstate-status"' in body, path
    # The debug page loads runview.js and runs.js after the guard too.
    body = c604_stack.server.http.get(DEBUG_PAGE_PATH).text
    assert body.index("/assets/runstate-guard.js") < body.index("/assets/runview.js")
    assert body.index("/assets/runstate-guard.js") < body.index("/assets/runs.js")
    assert body.index("/assets/runstate-guard.js") < body.index("/assets/debug.js")


# ===========================================================================
# 5. real browser final matrix: normal / 404 / syntax error on the three pages
# ===========================================================================
FAULTS = {
    "normal": None,
    "404": dict(status=404, content_type="text/html", body="not found"),
    "syntax_error": dict(
        status=200,
        content_type="application/javascript",
        body="function ({ this is not valid javascript",
    ),
}

EVIDENCE: list[dict] = []


@pytest.fixture(scope="module")
def browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover - environment missing
        pytest.skip(f"Playwright is not installed: {exc}")
    with sync_playwright() as playwright:
        try:
            instance = playwright.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # pragma: no cover - browser missing
            pytest.skip(f"installed Chrome cannot be driven headless: {exc}")
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture(scope="module")
def browser_owner(c604_stack) -> dict:
    owner = t029.golden_owner(c604_stack, login="c604-owner-" + f00b.new_uuid()[:8])
    run = t032.queued_run(c604_stack, owner)
    return {
        "cookie": owner.creator.cookie,
        "application_id": owner.application_id,
        "run_id": run["id"],
    }


@pytest.fixture(scope="module", autouse=True)
def _write_evidence():
    yield
    if os.environ.get("C604_WRITE_EVIDENCE") != "1":
        return
    target = Path(__file__).resolve().parents[2] / "reports" / "architecture"
    target.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "C6.04 (#158)",
        "browser": "playwright channel=chrome headless, real cmd/api over loopback",
        "matrix": "3 pages x (normal, 404, syntax_error)",
        "environment": {
            "platform": os.name,
            "node": shutil.which("node"),
            "pytest": pytest.__version__,
        },
        "cases": EVIDENCE,
    }
    (target / "C6.04-browser-evidence.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


@contextlib.contextmanager
def _browser_page(browser, stack, cookie, *, fault):
    context = browser.new_context()
    context.add_cookies(
        [{"name": st.SESSION_COOKIE, "value": cookie, "url": stack.server.base_url}]
    )
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    if fault is not None:
        page.route("**/assets/runstream.js", lambda route: route.fulfill(**fault))
    try:
        yield page, requests, page_errors
    finally:
        context.close()


def _paths(requests: list[str], base_url: str) -> list[str]:
    return [url[len(base_url):].split("?")[0] for url in requests if url.startswith(base_url)]


def _page_url(base: str, page_kind: str, owner: dict) -> str:
    if page_kind == "runs":
        return f"{base}/runs?run={owner['run_id']}"
    if page_kind == "chat":
        return f"{base}/chat"
    return f"{base}/apps/{owner['application_id']}/debug"


def _assert_failure_surface(page) -> None:
    banner = page.locator("#runstate-status")
    assert banner.is_visible(), "the explicit dependency-failure banner is not visible"
    text = banner.inner_text()
    # The Q7 message is exact and is the only headline; the reason follows it.
    assert text.startswith("运行状态暂不可用"), text
    assert text.split("：", 1)[0] == "运行状态暂不可用", text
    assert banner.get_attribute("data-runstate-message") == "运行状态暂不可用", text
    assert banner.get_attribute("data-runstate-status") == "unavailable"
    assert banner.get_attribute("data-runstate-reason") == "missing"


def _assert_paused_controls(page, paused_ids) -> dict:
    state = {}
    for control_id in paused_ids:
        control = page.locator("#" + control_id)
        assert control.get_attribute("data-runstate-paused-applied") == "true", control_id
        assert control.is_disabled(), control_id
        state[control_id] = {
            "disabled": control.is_disabled(),
            "applied": control.get_attribute("data-runstate-paused-applied"),
        }
    return state


def _assert_no_run_observation(page_kind: str, paths: list[str], owner: dict) -> None:
    detail_path = f"/api/v1/runs/{owner['run_id']}"
    assert not any(path == detail_path for path in paths), (page_kind, paths)
    assert not any(path.endswith("/events") for path in paths), (page_kind, paths)
    assert not any(path.endswith("/debug-runs") for path in paths), (page_kind, paths)


@pytest.mark.parametrize("fault_name", ["normal", "404", "syntax_error"])
@pytest.mark.parametrize("page_kind", ["runs", "chat", "debug"])
def test_real_browser_final_matrix(
    browser, c604_stack, browser_owner, fault_name: str, page_kind: str
) -> None:
    fault = FAULTS[fault_name]
    base = c604_stack.server.base_url
    url = _page_url(base, page_kind, browser_owner)
    specs = {
        "runs": ("runs.js", ("detail-cancel-button",)),
        "chat": ("chat.js", ("chat-submit", "chat-cancel", "interaction-reply")),
        "debug": ("debug.js", ("debug-submit", "runview-cancel")),
    }
    page_script, paused_ids = specs[page_kind]
    case = {
        "page": page_kind,
        "case": fault_name,
        "page_script": page_script,
        "url": url.replace(base, ""),
        "browser_version": browser.version,
    }
    with _browser_page(browser, c604_stack, browser_owner["cookie"], fault=fault) as (
        page,
        requests,
        page_errors,
    ):
        page.goto(url, wait_until="load")
        if fault is None:
            page.wait_for_timeout(300)
            case["guard_ready"] = page.evaluate("OrcharaRunStateGuard.ready")
            case["namespace_type"] = page.evaluate("typeof OrcharaRunState")
            case["legacy_global_type"] = page.evaluate(
                "typeof window." + DELETED_EXPORTS[0]
            )
            case["banner_hidden"] = page.locator("#runstate-status").is_hidden()
            case["paused_applied"] = page.locator(
                "[data-runstate-paused-applied='true']"
            ).count()
            assert case["guard_ready"] is True, case
            assert case["namespace_type"] == "object", case
            assert case["legacy_global_type"] == "undefined", case
            assert case["banner_hidden"] is True, case
            assert case["paused_applied"] == 0, case
            paths = _paths(requests, base)
            if page_kind == "runs":
                page.wait_for_function(
                    "document.getElementById('detail-state').textContent !== ''", timeout=15000
                )
                assert f"/api/v1/runs/{browser_owner['run_id']}" in paths, paths
                assert any(path.endswith("/events") for path in paths), paths
                case["detail_state"] = page.inner_text("#detail-state")
                case["detail_note"] = page.inner_text("#detail-note")
                assert case["detail_note"] != "", case
            elif page_kind == "chat":
                assert not page.locator("#chat-submit").is_disabled(), case
                assert page.locator("#runstate-status").is_hidden(), case
            else:
                page.wait_for_function(
                    "document.getElementById('debug-revision').value !== ''", timeout=15000
                )
                assert not page.locator("#debug-submit").is_disabled(), case
        else:
            page.wait_for_selector("#runstate-status:not([hidden])", timeout=15000)
            _assert_failure_surface(page)
            case["banner_text"] = page.locator("#runstate-status").inner_text()
            case["banner_message"] = page.locator("#runstate-status").get_attribute(
                "data-runstate-message"
            )
            case["paused_controls"] = _assert_paused_controls(page, paused_ids)
            case["guard_ready"] = page.evaluate("OrcharaRunStateGuard.ready")
            case["namespace_type"] = page.evaluate("typeof OrcharaRunState")
            assert case["guard_ready"] is False, case
            assert case["namespace_type"] == "undefined", case
            paths = _paths(requests, base)
            _assert_no_run_observation(page_kind, paths, browser_owner)
            if page_kind == "runs":
                # The independent run list is still read; opening a row never fetches
                # the detail in failure mode.
                assert "/api/v1/runs" in paths, paths
                assert page.locator("#run-detail").get_attribute("hidden") is not None
                page.locator("#runs-list button").first.click()
                page.wait_for_timeout(400)
                assert not any(
                    path == f"/api/v1/runs/{browser_owner['run_id']}"
                    for path in _paths(requests, base)
                )
                assert "运行状态暂不可用" in page.inner_text("#runs-status")
                case["independent_function"] = "run list + paused row click"
            elif page_kind == "chat":
                page.locator("#conversation-create").click()
                page.wait_for_function(
                    "document.getElementById('conversation-status').textContent"
                    ".indexOf('已创建会话') >= 0",
                    timeout=15000,
                )
                assert not any(
                    path.startswith("/api/v1/runs/") for path in _paths(requests, base)
                )
                case["independent_function"] = "conversation create"
            else:
                page.wait_for_function(
                    "document.getElementById('debug-revision').value !== ''", timeout=15000
                )
                page.evaluate(
                    "document.getElementById('debug-form').dispatchEvent("
                    "new Event('submit', {bubbles: true, cancelable: true}))"
                )
                page.wait_for_timeout(400)
                assert not any(
                    path.endswith("/debug-runs") for path in _paths(requests, base)
                )
                assert "运行状态暂不可用" in page.inner_text("#debug-status")
                case["independent_function"] = "draft revision + refused submit"
            # Reload keeps the explicit message; navigation stays usable.
            page.reload(wait_until="load")
            page.wait_for_selector("#runstate-status:not([hidden])", timeout=15000)
            _assert_failure_surface(page)
            case["reload_banner_visible"] = True
            page.locator('nav a[href="/apps"]').click()
            page.wait_for_url("**/apps")
            assert page.locator("h1").count() >= 1
            case["navigation_after_failure"] = page.url.replace(base, "")
            case["detail_requests"] = [
                p for p in paths if p == f"/api/v1/runs/{browser_owner['run_id']}"
            ]
            case["events_requests"] = [p for p in paths if p.endswith("/events")]
        case["page_errors"] = page_errors
        EVIDENCE.append(case)
