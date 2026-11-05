"""F07/T075 page surface: the editor's call_app panel and its Node-executed logic.

The page and the JavaScript asset are fetched from the running API over real
HTTP (a real compiled ``cmd/api``, a real creator session is not needed to read
a static asset). The panel's pure logic is executed under Node with a stubbed
``fetch``/``window``, which is how every page assertion in this repo runs
without a browser: the request shape, the node/mapping construction, the
upsert-and-rewire and the located-error highlight model are real code paths, not
copies. A browser DOM driving the panel is **not_run** and the report says so.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from support import stack as st

NODE = shutil.which("node")
INLINE_SCRIPT = re.compile(
    r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE
)
APPLICATION_ID = "11111111-1111-4111-8111-111111111111"
CHILD_VERSION_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


def node_binary() -> str:
    if NODE is None:
        pytest.skip(
            "node is not available on PATH; the editor logic cannot be executed "
            "(the structural page tests still run)"
        )
    return NODE


@pytest.fixture(scope="module", name="node_binary_fixture")
def node_binary_fixture() -> str:
    return node_binary()


def test_the_editor_page_serves_the_call_app_panel(t025_stack) -> None:
    page = t025_stack.server.http.get(f"/apps/{APPLICATION_ID}/edit")
    assert page.status == 200, page.text
    assert "text/html" in page.header("Content-Type")
    body = page.text
    for marker in (
        'id="subapp-card"',
        'id="callapp-application"',
        'id="callapp-version"',
        'id="callapp-node-id"',
        'id="callapp-input-rows"',
        'id="callapp-output-rows"',
        'id="callapp-add"',
        'id="callapp-validate"',
        'id="callapp-status"',
        'id="editor-node-list"',
        'id="callapp-errors"',
    ):
        assert marker in body, f"editor.html is missing {marker!r}"
    # The F04A canvas and the existing save/conflict surface are untouched.
    for marker in ('id="canvas-root"', 'id="draft-form"', 'id="conflict-panel"'):
        assert marker in body, marker

    script = t025_stack.server.http.get("/assets/editor.js")
    assert script.status == 200, script.text
    assert "javascript" in script.header("Content-Type")
    for symbol in (
        "callAppApplicationOptions",
        "callAppVersionOptions",
        "callAppChildSchemas",
        "callAppNodeFromForm",
        "upsertCallAppNode",
        "callAppValidationModel",
        "validateDraftRequest",
    ):
        assert symbol in script.text, symbol
    style = t025_stack.server.http.get("/assets/canvas.css")
    assert style.status == 200, style.text


def test_inline_scripts_and_editor_js_parse_with_node(tmp_path: Path) -> None:
    node = node_binary()
    page = (st.WEB_DIR / "editor.html").read_text(encoding="utf-8")
    for index, block in enumerate(INLINE_SCRIPT.findall(page)):
        script = tmp_path / f"editor.{index}.js"
        script.write_text(block, encoding="utf-8")
        completed = subprocess.run(
            [node, "--check", str(script)], capture_output=True, text=True, encoding="utf-8"
        )
        assert completed.returncode == 0, completed.stderr
    completed = subprocess.run(
        [node, "--check", str(st.WEB_DIR / "assets" / "editor.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr


PANEL_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = %s;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const editorSource = fs.readFileSync(webDir + '/assets/editor.js', 'utf8');
const APPLICATION_ID = %s;
const CHILD_VERSION_ID = %s;

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

function jsonResponse(status, payload, headers) {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => JSON.stringify(payload),
    headers: { get: (name) => (headers || {})[name] || null },
  };
}

function load(route) {
  const calls = [];
  const context = {
    fetch: async (path, init) => {
      calls.push({ path, init });
      return route(path, init);
    },
    window: {
      location: { pathname: '/apps/' + APPLICATION_ID + '/edit', assign() {} },
    },
    console,
  };
  vm.createContext(context);
  vm.runInContext(appSource, context, { filename: 'app.js' });
  vm.runInContext(editorSource, context, { filename: 'editor.js' });
  return { context, calls };
}

const CHILD_VERSION = {
  id: CHILD_VERSION_ID,
  application_id: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb',
  version_no: 1,
  manifest_sha256: 'c'.repeat(64),
  manifest: {
    definition: {
      dsl_version: '1.0',
      input_schema: {
        type: 'object',
        properties: { source: { type: 'string' }, mode: { type: 'string' } },
        required: ['source'],
      },
      output_schema: {
        type: 'object',
        properties: { report: { type: 'string' }, summary: { type: 'object' } },
        required: ['report', 'summary'],
      },
    },
  },
};

const CHAIN = {
  dsl_version: '1.0',
  input_schema: { type: 'object', properties: { source: { type: 'string' } }, required: ['source'] },
  output_schema: { type: 'object' },
  nodes: [
    { id: 'start', type: 'start', inputs: {}, output_schema: { type: 'object' }, config: {} },
    { id: 'python1', type: 'python', inputs: {}, output_schema: { type: 'object' },
      config: { tool_version: '11111111-1111-4111-8111-111111111111' } },
    { id: 'end', type: 'end', inputs: {}, output_schema: { type: 'object' }, config: {} },
  ],
  edges: [
    { source: 'start', target: 'python1' },
    { source: 'python1', target: 'end' },
  ],
  policy: { budget_limit: '1.00', currency: 'USD', max_active_seconds: 300,
            max_child_calls: 12, max_child_depth: 3 },
};

(async () => {
  // The catalog/version payloads become sorted, labelled picker options.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const applications = context.callAppApplicationOptions({
      items: [{ id: 'b', name: 'beta' }, { id: 'a', name: 'alpha' }],
    });
    assert.deepStrictEqual(plain(applications.map((entry) => entry.id)), ['a', 'b']);
    const versions = context.callAppVersionOptions({ items: [CHILD_VERSION] });
    assert.strictEqual(versions.length, 1);
    assert.strictEqual(versions[0].id, CHILD_VERSION_ID);
    assert.strictEqual(versions[0].versionNo, 1);
    assert.strictEqual(versions[0].label, 'v1 · ' + 'c'.repeat(12));
  }

  // A version's frozen definition is the only source of the child I/O schema.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const schemas = context.callAppChildSchemas(CHILD_VERSION);
    assert.deepStrictEqual(plain(schemas.inputSchema.required), ['source']);
    assert.deepStrictEqual(plain(schemas.outputSchema.required), ['report', 'summary']);
    assert.strictEqual(context.callAppChildSchemas({ manifest: {} }), null);
    assert.strictEqual(context.callAppChildSchemas({ manifest: { definition: {} } }), null);
    const model = context.callAppPanelModel({ manifest: {} });
    assert.strictEqual(model.callable, false);
    assert.ok(model.reason.includes('dependency_not_ready'), model.reason);
    const callable = context.callAppPanelModel(CHILD_VERSION);
    assert.strictEqual(callable.callable, true);
    assert.deepStrictEqual(plain(callable.inputRows.map((row) => row.field)), ['mode', 'source']);
    assert.strictEqual(callable.inputRows.find((row) => row.field === 'source').required, true);
  }

  // Existing mappings round-trip into the editable rows.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const schemas = context.callAppChildSchemas(CHILD_VERSION);
    const rows = context.callAppInputRows(plain(schemas.inputSchema), {
      source: { ref: 'input#/source' },
      mode: { literal: 'eu' },
    });
    const byField = Object.fromEntries(rows.map((row) => [row.field, row]));
    assert.strictEqual(byField.source.mode, 'input');
    assert.strictEqual(byField.source.value, '/source');
    assert.strictEqual(byField.mode.mode, 'literal');
    assert.strictEqual(byField.mode.value, '"eu"');
    const outputs = context.callAppOutputRows(plain(schemas.outputSchema), {
      type: 'object',
      properties: { report: { type: 'string' } },
    });
    const outputByField = Object.fromEntries(outputs.map((row) => [row.field, row]));
    assert.strictEqual(outputByField.report.include, true);
    assert.strictEqual(outputByField.report.declaredType, 'string');
    assert.strictEqual(outputByField.summary.include, false);
    assert.strictEqual(outputByField.report.required, true);
  }

  // The form becomes exactly the contract's call_app node, whatever the source.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const node = context.callAppNodeFromForm({
      nodeId: 'callapp1',
      versionId: CHILD_VERSION_ID,
      inputRows: [
        { field: 'source', mode: 'input', value: 'source' },
        { field: 'mode', mode: 'literal', value: '"eu"' },
      ],
      outputRows: [{ field: 'report', include: true, type: 'string', required: true }],
    });
    assert.deepStrictEqual(plain(node), {
      id: 'callapp1',
      type: 'call_app',
      inputs: {
        source: { ref: 'input#/source' },
        mode: { literal: 'eu' },
      },
      output_schema: {
        type: 'object',
        properties: { report: { type: 'string' } },
        required: ['report'],
      },
      config: { application_version: CHILD_VERSION_ID },
    });
    const upstream = context.callAppNodeFromForm({
      nodeId: 'callapp1',
      versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'node', value: 'python1#/text' }],
      outputRows: [],
    });
    assert.deepStrictEqual(plain(upstream.inputs.source), { ref: 'node:python1#/text' });
    const empty = context.callAppNodeFromForm({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID, inputRows: [], outputRows: [],
    });
    assert.deepStrictEqual(plain(empty.inputs), {});
    assert.deepStrictEqual(plain(empty.output_schema), { type: 'object', properties: {} });
  }

  // Malformed panel input fails locally, with the field named in the code.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const refuse = (form, expected) => {
      assert.throws(
        () => context.callAppNodeFromForm(form),
        (error) => error.code === expected,
      );
    };
    refuse({ nodeId: '1bad', versionId: CHILD_VERSION_ID }, 'call_app.node_id');
    refuse({ nodeId: 'callapp1', versionId: 'latest' }, 'call_app.version');
    refuse({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'literal', value: '' }],
    }, 'call_app.literal');
    refuse({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'literal', value: 'not json' }],
    }, 'call_app.literal');
    refuse({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'node', value: 'python1' }],
    }, 'call_app.node_ref');
    refuse({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'input', value: '/' }],
    }, 'call_app.input_pointer');
  }

  // Adding the node keeps the chain connected: python1 -> callapp1 -> end.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const node = context.callAppNodeFromForm({
      nodeId: 'callapp1',
      versionId: CHILD_VERSION_ID,
      inputRows: [{ field: 'source', mode: 'input', value: '/source' }],
      outputRows: [],
    });
    const next = context.upsertCallAppNode(CHAIN, node);
    assert.deepStrictEqual(plain(next.nodes.map((entry) => entry.id)),
      ['start', 'python1', 'callapp1', 'end']);
    const edges = next.edges.map((edge) => edge.source + '->' + edge.target);
    assert.ok(edges.includes('start->python1'), edges);
    assert.ok(edges.includes('python1->callapp1'), edges);
    assert.ok(edges.includes('callapp1->end'), edges);
    assert.ok(!edges.includes('python1->end'), edges);
    // The input document is never mutated in place.
    assert.deepStrictEqual(CHAIN.edges.map((edge) => edge.source + '->' + edge.target),
      ['start->python1', 'python1->end']);

    // Updating by id replaces the config/mapping, not the node count.
    const updated = context.upsertCallAppNode(next, {
      ...node,
      inputs: { source: { literal: 'inline' } },
    });
    assert.strictEqual(updated.nodes.filter((entry) => entry.id === 'callapp1').length, 1);
    assert.deepStrictEqual(
      plain(updated.nodes.find((entry) => entry.id === 'callapp1').inputs.source),
      { literal: 'inline' },
    );
    assert.strictEqual(updated.edges.length, next.edges.length);
    // An id owned by another node type is refused, never converted.
    assert.throws(
      () => context.upsertCallAppNode(CHAIN, { ...node, id: 'python1' }),
      (error) => error.code === 'call_app.node_id',
    );
  }

  // The located errors become the node strip's highlight model.
  {
    const { context } = load(async () => jsonResponse(200, {}));
    const node = context.callAppNodeFromForm({
      nodeId: 'callapp1', versionId: CHILD_VERSION_ID, inputRows: [], outputRows: [],
    });
    const definition = context.upsertCallAppNode(CHAIN, node);
    const model = context.callAppValidationModel(definition, [
      { code: 'io_mapping_missing_field', pointer: '/nodes/2/inputs/source',
        node_id: 'callapp1', message: 'required child input is not mapped' },
      { code: 'io_output_type_mismatch', pointer: '/nodes/2/output_schema/properties/report',
        node_id: 'callapp1', message: 'type' },
      { code: 'child_calls_exceeded', pointer: '/policy/max_child_calls',
        message: 'too many child calls' },
      { code: 'dependency_cycle', pointer: '', message: 'cycle' },
    ]);
    const highlighted = model.nodes.find((entry) => entry.nodeId === 'callapp1');
    assert.strictEqual(highlighted.callApp, true);
    assert.strictEqual(highlighted.errors.length, 2);
    assert.strictEqual(highlighted.errors[0].field, 'inputs/source');
    assert.strictEqual(model.nodes.find((entry) => entry.nodeId === 'python1').errors.length, 0);
    assert.strictEqual(model.documentLevel.length, 2);
    assert.strictEqual(model.documentLevel[0].pointer, '/policy/max_child_calls');

    // Selecting the node back into the panel restores its version and mappings.
    const prefilled = context.callAppPrefillFromNode(definition, 'callapp1', CHILD_VERSION);
    assert.strictEqual(prefilled.nodeId, 'callapp1');
    assert.strictEqual(prefilled.versionId, CHILD_VERSION_ID);
    assert.strictEqual(context.callAppPrefillFromNode(definition, 'python1', CHILD_VERSION), null);
  }

  // Validate is a real POST /validate with the CSRF token; its body drives the panel.
  {
    const { context, calls } = load(async (path) => {
      if (path === '/api/v1/auth/me') {
        return jsonResponse(200, { principal_id: 'p1', csrf_token: 'csrf-1' });
      }
      assert.strictEqual(path, '/api/v1/applications/' + APPLICATION_ID + '/validate');
      return jsonResponse(200, {
        valid: false,
        errors: [{
          node_id: 'callapp1',
          code: 'io_mapping_type_mismatch',
          pointer: '/nodes/2/inputs/source',
          message: 'the binding produces integer',
        }],
      });
    });
    const result = await context.validateDraftRequest(APPLICATION_ID);
    assert.strictEqual(result.valid, false);
    assert.strictEqual(result.errors.length, 1);
    const post = calls.find((call) => call.init.method === 'POST');
    assert.ok(post, 'validate was not sent as a POST');
    assert.strictEqual(post.init.headers['X-CSRF-Token'], 'csrf-1');
    assert.strictEqual(post.init.credentials, 'same-origin');
  }

  process.stdout.write('editor call_app panel behaviour ok\n');
})().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
"""


def test_call_app_panel_logic_executes_under_node(node_binary_fixture: str, tmp_path: Path) -> None:
    node_binary = node_binary_fixture
    harness = tmp_path / "callapp-harness.js"
    harness.write_text(
        PANEL_HARNESS % (
            json.dumps(str(st.WEB_DIR)),
            json.dumps(APPLICATION_ID),
            json.dumps(CHILD_VERSION_ID),
        ),
        encoding="utf-8",
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
        "the call_app panel did not behave as the contract requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "editor call_app panel behaviour ok" in completed.stdout, completed.stdout
