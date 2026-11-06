/*
 * F04B/T089 canvas call_app pure-module tests, executed by Node from pytest.
 *
 * The canvas call_app authoring surface is: the palette/default node (dsl.js),
 * the fixed-version picker + child I/O mapping helpers (lib/callapp.js) and the
 * positive export allowlist (lib/export.js). Everything here runs without a DOM
 * or a browser; the page wiring is asserted through the committed bundle
 * markers in test_t089_callapp_canvas.py.
 *
 * Run with:
 *   node tests/f04b/callapp_canvas.test.mjs
 */

import assert from 'node:assert/strict';

import {
  NODE_LABELS,
  NODE_TYPES,
  REPEATABLE_NODE_TYPES,
  addNode,
  defaultDefinition,
  defaultLayout,
  definitionToGraph,
  graphToDefinition,
} from '../../apps/web/canvas/src/lib/dsl.js';
import {
  callAppApplicationOptions,
  callAppBinding,
  callAppChildSchemas,
  callAppInputRows,
  callAppNodeFromForm,
  callAppOutputRows,
  callAppPanelModel,
  callAppPrefillFromNode,
  callAppProblems,
  callAppVersionOptions,
} from '../../apps/web/canvas/src/lib/callapp.js';
import { exportPayload } from '../../apps/web/canvas/src/lib/export.js';

const CHILD_VERSION = '33333333-4444-4555-8666-777777777777';
const OTHER_VERSION = '44444444-5555-4666-8777-888888888888';

const CHILD_INPUT_SCHEMA = {
  type: 'object',
  properties: { source: { type: 'string' }, region: { type: 'string' } },
  required: ['source', 'region'],
  additionalProperties: false,
};

const CHILD_OUTPUT_SCHEMA = {
  type: 'object',
  properties: { echo: { type: 'string' }, count: { type: 'integer' } },
  required: ['echo'],
  additionalProperties: false,
};

function versionView(overrides = {}) {
  return {
    id: CHILD_VERSION,
    application_id: '11111111-2222-4333-8444-555555555555',
    version_no: 3,
    manifest_sha256: 'ab'.repeat(32),
    manifest: {
      manifest_version: 'orchara-execution-manifest/1.0',
      definition: {
        dsl_version: '1.0',
        input_schema: CHILD_INPUT_SCHEMA,
        output_schema: CHILD_OUTPUT_SCHEMA,
        nodes: [],
        edges: [],
        policy: {},
      },
    },
    ...overrides,
  };
}

/** A parent definition with one configurable call_app node plus an end node. */
function parentDefinition(node) {
  return {
    dsl_version: '1.0',
    input_schema: {
      type: 'object',
      properties: { source: { type: 'string' }, count: { type: 'integer' } },
      required: ['source'],
      additionalProperties: false,
    },
    output_schema: { type: 'object' },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, config: {}, output_schema: { type: 'object' } },
      node,
      { id: 'end', type: 'end', inputs: {}, config: {}, output_schema: { type: 'object' } },
    ],
    edges: [
      { source: 'start', target: node.id },
      { source: node.id, target: 'end' },
    ],
    policy: defaultDefinition().policy,
  };
}

function callApp(id = 'child', inputs = { source: { ref: 'input#/source' }, region: { literal: 'eu' } }, outputSchema = CHILD_OUTPUT_SCHEMA) {
  return {
    id,
    type: 'call_app',
    inputs,
    output_schema: JSON.parse(JSON.stringify(outputSchema)),
    config: { application_version: CHILD_VERSION },
  };
}

let checks = 0;
function test(name, body) {
  try {
    body();
    checks += 1;
    console.log('ok ' + checks + ' - ' + name);
  } catch (error) {
    console.error('not ok - ' + name);
    throw error;
  }
}

function problemCodes(problems) {
  return problems.map((problem) => problem.code);
}

test('palette offers call_app with the fixed-version-only default node', () => {
  assert.ok(NODE_TYPES.includes('call_app'), NODE_TYPES);
  assert.ok(REPEATABLE_NODE_TYPES.includes('call_app'), REPEATABLE_NODE_TYPES);
  assert.equal(NODE_LABELS.call_app, '子应用调用');

  const graph = definitionToGraph(defaultDefinition(), defaultLayout(defaultDefinition()));
  const added = addNode(graph.nodes, graph.edges, 'call_app', {});
  assert.equal(added.ok, true);
  assert.equal(added.node.data.nodeType, 'call_app');
  assert.equal(added.node.class, 'orchara-node orchara-node-call_app');
  // The default node pins nothing and never names a dynamic "latest".
  assert.deepEqual(added.node.data.config, { application_version: '' });
  const addedAgain = addNode([...graph.nodes, added.node], graph.edges, 'call_app', {});
  assert.equal(addedAgain.ok, true);
  assert.notEqual(addedAgain.node.id, added.node.id);
});

test('call_app nodes survive a graph -> definition round trip with config and inputs', () => {
  const node = callApp();
  const definition = parentDefinition(node);
  const graph = definitionToGraph(definition, null);
  const flow = graph.nodes.find((item) => item.id === 'child');
  assert.equal(flow.data.nodeType, 'call_app');
  assert.deepEqual(flow.data.config, { application_version: CHILD_VERSION });
  const moved = graph.nodes.map((item) =>
    item.id === 'child' ? { ...item, position: { x: 500, y: 200 } } : item,
  );
  const rebuilt = graphToDefinition(moved, graph.edges, definition);
  const child = rebuilt.nodes.find((item) => item.id === 'child');
  assert.deepEqual(child.config, { application_version: CHILD_VERSION });
  assert.deepEqual(child.inputs, definition.nodes[1].inputs);
  assert.deepEqual(child.output_schema, definition.nodes[1].output_schema);
});

test('application and published-version options are ACL-filtered rows with stable labels', () => {
  const applications = callAppApplicationOptions({
    items: [
      { id: 'bbbbbbbb-1111-4222-8333-444444444444', name: 'Zeta' },
      { id: 'aaaaaaaa-1111-4222-8333-444444444444', name: 'Alpha' },
    ],
  });
  assert.deepEqual(applications.map((entry) => entry.name), ['Alpha', 'Zeta']);

  const versions = callAppVersionOptions({ items: [versionView()] });
  assert.equal(versions.length, 1);
  assert.equal(versions[0].id, CHILD_VERSION);
  assert.equal(versions[0].versionNo, 3);
  assert.equal(versions[0].manifestSha256, 'ab'.repeat(32));
  assert.ok(versions[0].label.startsWith('v3 · abababababab'), versions[0].label);
  // A malformed/partial row is not a selectable fixed version.
  const bad = callAppVersionOptions({ items: [{ id: 'latest', version_no: 1 }] });
  assert.deepEqual(bad, []);
});

test('child schemas come from the frozen manifest definition only', () => {
  const schemas = callAppChildSchemas(versionView());
  assert.deepEqual(schemas.inputSchema, CHILD_INPUT_SCHEMA);
  assert.deepEqual(schemas.outputSchema, CHILD_OUTPUT_SCHEMA);
  assert.equal(callAppChildSchemas({ manifest: {} }), null);
  assert.equal(callAppChildSchemas(null), null);
  const noOutput = versionView();
  delete noOutput.manifest.definition.output_schema;
  assert.equal(callAppChildSchemas(noOutput), null);
});

test('input rows expose literal / input / node bindings per declared child field', () => {
  const rows = callAppInputRows(CHILD_INPUT_SCHEMA, {
    source: { ref: 'input#/source' },
    region: { literal: 'eu' },
  });
  assert.deepEqual(rows.map((row) => row.field), ['region', 'source']);
  const region = rows.find((row) => row.field === 'region');
  assert.equal(region.mode, 'literal');
  assert.equal(region.value, '"eu"');
  assert.equal(region.required, true);
  assert.equal(region.type, 'string');
  const source = rows.find((row) => row.field === 'source');
  assert.equal(source.mode, 'input');
  assert.equal(source.value, '/source');

  const withNode = callAppInputRows(CHILD_INPUT_SCHEMA, {
    source: { ref: 'node:previous#/text' },
  }).find((row) => row.field === 'source');
  assert.equal(withNode.mode, 'node');
  assert.equal(withNode.value, 'previous#/text');

  const unmapped = callAppInputRows(CHILD_INPUT_SCHEMA, {}).find((row) => row.field === 'source');
  assert.equal(unmapped.mode, 'none');
  assert.equal(unmapped.value, '');
});

test('output rows default to the child schema and stay editable per field', () => {
  const rows = callAppOutputRows(CHILD_OUTPUT_SCHEMA, { properties: { echo: { type: 'string' } } });
  assert.deepEqual(rows.map((row) => row.field), ['count', 'echo']);
  const echo = rows.find((row) => row.field === 'echo');
  assert.equal(echo.include, true);
  assert.equal(echo.required, true);
  assert.equal(echo.type, 'string');
  assert.equal(echo.declaredType, 'string');
  const count = rows.find((row) => row.field === 'count');
  assert.equal(count.include, false);
  assert.equal(count.required, false);
});

test('callAppBinding builds the documented literal/ref bindings and refuses junk', () => {
  assert.deepEqual(callAppBinding('literal', '{"a":1}'), { literal: { a: 1 } });
  assert.deepEqual(callAppBinding('input', 'source'), { ref: 'input#/source' });
  assert.deepEqual(callAppBinding('input', '/nested/source'), { ref: 'input#/nested/source' });
  assert.deepEqual(callAppBinding('node', 'previous#/text'), { ref: 'node:previous#/text' });
  assert.equal(callAppBinding('none', ''), null);
  for (const [mode, value, code] of [
    ['literal', '', 'call_app.literal'],
    ['literal', '{nope', 'call_app.literal'],
    ['input', '', 'call_app.input_pointer'],
    ['node', 'previous', 'call_app.node_ref'],
    ['bogus', 'x', 'call_app.mode'],
  ]) {
    assert.throws(() => callAppBinding(mode, value), (error) => error.code === code, mode);
  }
});

test('callAppNodeFromForm writes exactly the fixed version, minimal mapping and parent schema', () => {
  const node = callAppNodeFromForm({
    nodeId: 'child2',
    versionId: CHILD_VERSION,
    inputRows: [
      { field: 'source', mode: 'input', value: '/source' },
      { field: 'region', mode: 'literal', value: '"eu"' },
    ],
    outputRows: [
      { field: 'echo', include: true, required: true, type: 'string' },
      { field: 'count', include: false, required: false, type: 'integer' },
    ],
  });
  assert.deepEqual(node, {
    id: 'child2',
    type: 'call_app',
    inputs: { source: { ref: 'input#/source' }, region: { literal: 'eu' } },
    output_schema: {
      type: 'object',
      properties: { echo: { type: 'string' } },
      required: ['echo'],
    },
    config: { application_version: CHILD_VERSION },
  });
  assert.throws(
    () => callAppNodeFromForm({ nodeId: '1bad', versionId: CHILD_VERSION }),
    (error) => error.code === 'call_app.node_id',
  );
  assert.throws(
    () => callAppNodeFromForm({ nodeId: 'child', versionId: 'latest' }),
    (error) => error.code === 'call_app.version',
  );
});

test('the panel model says why an unpublishable version is not callable', () => {
  const model = callAppPanelModel(versionView());
  assert.equal(model.callable, true);
  assert.deepEqual(model.inputRows.map((row) => row.field), ['region', 'source']);
  assert.deepEqual(model.outputRows.map((row) => row.field), ['count', 'echo']);
  const broken = callAppPanelModel({ id: OTHER_VERSION, manifest: {} });
  assert.equal(broken.callable, false);
  assert.ok(broken.reason.length > 0);
  assert.deepEqual(broken.inputRows, []);
});

test('an existing node is pre-filled from its pinned version', () => {
  const definition = parentDefinition(callApp('child', { source: { ref: 'input#/source' } }));
  const prefill = callAppPrefillFromNode(definition, 'child', versionView());
  assert.equal(prefill.nodeId, 'child');
  assert.equal(prefill.versionId, CHILD_VERSION);
  assert.equal(prefill.inputRows.find((row) => row.field === 'source').mode, 'input');
  assert.equal(prefill.outputRows.find((row) => row.field === 'echo').include, true);
  assert.equal(callAppPrefillFromNode(definition, 'end', versionView()), null);
});

test('local mapping hints mirror the server: missing required, unknown field, type mismatch', () => {
  const schemas = callAppChildSchemas(versionView());

  const good = parentDefinition(callApp());
  assert.deepEqual(callAppProblems(good.nodes[1], good, schemas), []);

  const missing = parentDefinition(callApp('child', { source: { ref: 'input#/source' } }));
  const missingProblems = callAppProblems(missing.nodes[1], missing, schemas);
  assert.deepEqual(problemCodes(missingProblems), ['call_app.missing_required_input']);
  assert.equal(missingProblems[0].field, 'inputs.region');
  assert.ok(missingProblems[0].message.includes('region'), missingProblems[0].message);

  const unknown = parentDefinition(
    callApp('child', { source: { ref: 'input#/source' }, region: { literal: 'eu' }, extra: { literal: 1 } }),
  );
  assert.deepEqual(problemCodes(callAppProblems(unknown.nodes[1], unknown, schemas)),
    ['call_app.unknown_input']);
  assert.equal(callAppProblems(unknown.nodes[1], unknown, schemas)[0].field, 'inputs.extra');

  const wrongType = parentDefinition(
    callApp('child', { source: { ref: 'input#/count' }, region: { literal: 5 } }),
  );
  const typeProblems = callAppProblems(wrongType.nodes[1], wrongType, schemas);
  assert.deepEqual(problemCodes(typeProblems).sort(),
    ['call_app.type_mismatch', 'call_app.type_mismatch']);
  assert.ok(typeProblems.every((problem) => problem.field.startsWith('inputs.')), typeProblems);

  const illegal = parentDefinition(callApp('child', { source: { ref: 'node:end#/missing' }, region: { literal: 'eu' } }));
  const illegalProblems = callAppProblems(illegal.nodes[1], illegal, schemas);
  assert.ok(problemCodes(illegalProblems).includes('call_app.illegal_source'), illegalProblems);

  // An unknown static type is left to the server instead of guessing.
  const unknownType = parentDefinition(
    callApp('child', { source: { ref: 'input#/anything' }, region: { literal: 'eu' } }),
  );
  unknownType.input_schema.properties.anything = {};
  assert.deepEqual(callAppProblems(unknownType.nodes[1], unknownType, schemas), []);

  // No pinned version / no frozen schema is a config problem, not a mapping one.
  const noVersion = parentDefinition(callApp('child', {}));
  noVersion.nodes[1].config = { application_version: '' };
  assert.deepEqual(problemCodes(callAppProblems(noVersion.nodes[1], noVersion, null)),
    ['call_app.version']);
});

test('export allowlist keeps only application_version for call_app', () => {
  const node = callApp();
  node.config.latest = true;
  const definition = parentDefinition(node);
  const payload = exportPayload(definition, { version: 1, positions: {} });
  assert.deepEqual(payload.definition.nodes[1].config, { application_version: CHILD_VERSION });
  assert.deepEqual(payload.dropped.map((entry) => entry.path),
    ['/nodes/1/config/latest']);
  const clean = parentDefinition(callApp());
  const cleanPayload = exportPayload(clean, { version: 1, positions: {} });
  assert.deepEqual(cleanPayload.definition, clean);
  assert.equal(cleanPayload.dropped, undefined);
});

console.log('PASS ' + checks + ' call_app canvas module tests');
