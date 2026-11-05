"""F07/T078 page surface: the parent Run's independent child Runs panel.

The page and asset are fetched from the running API over real HTTP; the panel's
logic is executed under Node with a stubbed JSON client, so the request paths, the
projection, the "no success before a terminal child" rule and the structured
result read are observed behaviour rather than source text. Driving a real browser
DOM remains **not_run** (the same boundary every page ticket in this repo records).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    t029_stack,
)

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
PARENT_ID = "11111111-2222-4333-8444-555555555555"
CHILD_ID = "99999999-8888-4777-8666-555555555555"
OUTPUT_ID = "77777777-6666-4555-8444-333333333333"


def node_path() -> str:
    if NODE is None:
        pytest.skip("node is not available on PATH; the page logic cannot be executed")
    return NODE


@pytest.fixture(scope="module", name="node_binary")
def node_binary_fixture() -> str:
    return node_path()


def test_the_runs_page_serves_the_child_runs_panel(t029_stack) -> None:
    page = t029_stack.server.http.get("/runs")
    assert page.status == 200, page.text
    assert "text/html" in page.header("Content-Type")
    body = page.text
    for marker in (
        'id="children-panel"',
        'id="children-status"',
        'id="children-list"',
        "F07/T078",
        "独立子运行",
        "checkpoint thread",
        "不会显示为成功",
        "同一父调用键",
    ):
        assert marker in body, f"runs.html is missing {marker!r}"
    # The existing run detail surface is untouched.
    for marker in ('id="run-detail"', 'id="detail-timeline"', "/assets/runs.js"):
        assert marker in body, marker

    asset = t029_stack.server.http.get("/assets/runs.js")
    assert asset.status == 200, asset.text
    assert "javascript" in asset.header("Content-Type")
    for symbol in (
        "function runChildrenPath(",
        "function artifactContentPath(",
        "function describeChildRun(",
        "function loadChildRuns(",
        "function childRunSummaryText(",
        "function loadChildRunsPanel(",
        "/children",
    ):
        assert symbol in asset.text, f"runs.js is missing {symbol!r}"


def test_the_served_page_and_asset_parse_with_node(node_binary: str, tmp_path: Path) -> None:
    page = (st.WEB_DIR / "runs.html").read_text(encoding="utf-8")
    for index, block in enumerate(INLINE_SCRIPT.findall(page)):
        script = tmp_path / f"runs.{index}.js"
        script.write_text(block, encoding="utf-8")
        completed = subprocess.run(
            [node_binary, "--check", str(script)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [node_binary, "--check", str(st.WEB_DIR / "assets" / "runs.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert completed.returncode == 0, completed.stderr


CHILD_HARNESS = r"""
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

const PARENT = __PARENT__;
const CHILD = __CHILD__;
const OUTPUT = __OUTPUT__;

// Paths are built from ids, never from caller URLs.
assert.strictEqual(context.runChildrenPath(PARENT), '/api/v1/runs/' + PARENT + '/children');
assert.strictEqual(context.runChildrenPath('a b/c'), '/api/v1/runs/a%20b%2Fc/children');
assert.strictEqual(context.artifactContentPath(OUTPUT), '/api/v1/artifacts/' + OUTPUT + '/content');
assert.strictEqual(context.artifactContentPath('a b/c'), '/api/v1/artifacts/a%20b%2Fc/content');

// Only contract Run fields are projected.
const done = context.describeChildRun({
  id: CHILD, state: 'succeeded', revision: 4, kind: 'published',
  application_version_id: 'v1', parent_run_id: PARENT,
  output_artifact_id: OUTPUT, error_code: null,
  execution_snapshot_id: 's1', phase: 'idle',
});
assert.strictEqual(done.id, CHILD);
assert.strictEqual(done.kind, 'published');
assert.strictEqual(done.versionID, 'v1');
assert.strictEqual(done.outputArtifactID, OUTPUT);
assert.strictEqual(done.hasStructuredResult, true);
assert.strictEqual(done.errorCode, '');
assert.ok(context.childRunSummaryText(done).includes('有结构化结果'));

const live = context.describeChildRun({
  id: CHILD, state: 'running', kind: 'published', output_artifact_id: null,
  parent_run_id: PARENT,
});
assert.strictEqual(live.hasStructuredResult, false);
assert.ok(!context.childRunSummaryText(live).includes('有结构化结果'));
const failed = context.describeChildRun({
  id: CHILD, state: 'failed', kind: 'published', output_artifact_id: null,
  error_code: 'child_output_schema_invalid',
});
assert.strictEqual(failed.hasStructuredResult, false);
assert.ok(context.childRunSummaryText(failed).includes('child_output_schema_invalid'));

// The panel loads the owner-only list and then each succeeded child's structured
// result; a non-terminal child never triggers a result read.
(async () => {
  const calls = [];
  const children = await context.loadChildRuns(PARENT, async (path) => {
    calls.push(path);
    if (path === '/api/v1/runs/' + PARENT + '/children') {
      return {
        items: [
          { id: CHILD, state: 'succeeded', kind: 'published', parent_run_id: PARENT,
            application_version_id: 'v1', output_artifact_id: OUTPUT, error_code: null,
            execution_snapshot_id: 's1', phase: 'idle', revision: 2 },
          { id: 'aaaa', state: 'running', kind: 'published', parent_run_id: PARENT,
            application_version_id: 'v1', output_artifact_id: null, error_code: null,
            execution_snapshot_id: 's2', phase: 'executing', revision: 1 },
        ],
        next_cursor: null, generated_at: '2026-11-05T00:00:00Z',
      };
    }
    if (path === '/api/v1/artifacts/' + OUTPUT + '/content') {
      return { echo: 'child-result' };
    }
    throw new Error('unexpected path ' + path);
  });
  assert.deepStrictEqual(calls, [
    '/api/v1/runs/' + PARENT + '/children',
    '/api/v1/artifacts/' + OUTPUT + '/content',
  ]);
  assert.strictEqual(children.length, 2);
  assert.strictEqual(children[0].structuredResult.echo, 'child-result');
  assert.strictEqual(children[1].structuredResult, undefined);

  // A refused result read is shown as unavailable, never as a result.
  const partial = await context.loadChildRuns(PARENT, async (path) => {
    if (path.endsWith('/children')) {
      return { items: [{ id: CHILD, state: 'succeeded', output_artifact_id: OUTPUT }] };
    }
    const error = new Error('gone');
    error.code = 'resource_not_found';
    throw error;
  });
  assert.strictEqual(partial[0].resultError, 'resource_not_found');

  process.stdout.write('runs.js child panel behaviour ok\n');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""


def test_the_child_runs_panel_logic_executes_under_node(
    node_binary: str, tmp_path: Path
) -> None:
    harness = tmp_path / "runs-children-harness.js"
    source = (
        CHILD_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR)))
        .replace("__PARENT__", json.dumps(PARENT_ID))
        .replace("__CHILD__", json.dumps(CHILD_ID))
        .replace("__OUTPUT__", json.dumps(OUTPUT_ID))
    )
    harness.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [node_binary, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "runs.js did not behave as the child panel requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "runs.js child panel behaviour ok" in completed.stdout, completed.stdout
