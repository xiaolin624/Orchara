/*
 * F04B/T086 canvas pure-module tests, executed by Node from pytest.
 *
 * The canvas must let a creator author a model node: the palette, the closed
 * config (fixed profile, prompt template, response mode) and the export
 * allowlist. The response schema is the node's output_schema — a
 * `response_schema` config key must never survive an export, because it does not
 * exist in the contract.
 *
 * Run with:
 *   node tests/f04b/canvas_model.test.mjs
 */

import assert from 'node:assert/strict';

import {
  MODEL_RESPONSE_MODES,
  NODE_LABELS,
  NODE_TYPES,
  REPEATABLE_NODE_TYPES,
  addNode,
  chainOrder,
  defaultDefinition,
  defaultLayout,
  defaultNode,
  definitionToGraph,
  graphToDefinition,
  regionProblems,
} from '../../apps/web/canvas/src/lib/dsl.js';
import { isLegalSource, legalSources, sourceType } from '../../apps/web/canvas/src/lib/sources.js';
import {
  CONFIG_ALLOWLIST,
  ExportRefused,
  exportPayload,
} from '../../apps/web/canvas/src/lib/export.js';

const MODEL_PROFILE = '22222222-2222-4222-8222-222222222222';

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

function modelDefinition() {
  return {
    dsl_version: '1.0',
    input_schema: {
      type: 'object',
      properties: { task: { type: 'string' } },
      required: ['task'],
      additionalProperties: false,
    },
    output_schema: {
      type: 'object',
      properties: { summary: { type: 'string' } },
      required: ['summary'],
      additionalProperties: false,
    },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, output_schema: { type: 'object' }, config: {} },
      {
        id: 'analyse',
        type: 'model',
        inputs: { task: { ref: 'input#/task' } },
        output_schema: {
          type: 'object',
          properties: { summary: { type: 'string' } },
          required: ['summary'],
          additionalProperties: false,
        },
        config: {
          model_profile_version: MODEL_PROFILE,
          prompt_template: 'Summarize {{task}}',
          response_mode: 'json',
        },
      },
      {
        id: 'end',
        type: 'end',
        inputs: { summary: { ref: 'node:analyse#/summary' } },
        output_schema: { type: 'object' },
        config: {},
      },
    ],
    edges: [
      { source: 'start', target: 'analyse' },
      { source: 'analyse', target: 'end' },
    ],
    policy: defaultDefinition().policy,
  };
}

function sortedNodes(definition) {
  return [...definition.nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

test('palette offers a model node with the closed config defaults', () => {
  assert.ok(NODE_TYPES.includes('model'));
  assert.equal(NODE_LABELS.model, '模型');
  assert.deepEqual(MODEL_RESPONSE_MODES, ['json', 'text']);
  // A model node is repeatable: a workflow may run several generations.
  assert.ok(REPEATABLE_NODE_TYPES.includes('model'));

  const defaults = defaultNode('model', 'analyse');
  assert.deepEqual(defaults.config, {
    model_profile_version: '',
    prompt_template: '',
    response_mode: 'json',
  });
  // The "response schema" is node.output_schema, never a config key.
  assert.equal(Object.prototype.hasOwnProperty.call(defaults.config, 'response_schema'), false);
  assert.deepEqual(defaults.output_schema, { type: 'object', properties: {} });

  const definition = modelDefinition();
  const graph = definitionToGraph(definition, defaultLayout(definition));
  const added = addNode(graph.nodes, graph.edges, 'model', {});
  assert.equal(added.ok, true);
  assert.notEqual(added.node.id, 'analyse');
  const second = addNode([...graph.nodes, added.node], graph.edges, 'model', {});
  assert.equal(second.ok, true);
  assert.notEqual(second.node.id, added.node.id);
});

test('a model node survives a graph -> definition round trip', () => {
  const definition = modelDefinition();
  const graph = definitionToGraph(definition, null);
  const analyse = graph.nodes.find((node) => node.id === 'analyse');
  assert.equal(analyse.data.nodeType, 'model');
  assert.equal(analyse.label, '模型');
  assert.deepEqual(analyse.data.config, definition.nodes[1].config);
  // The closed config survives reordering and position edits.
  const moved = graph.nodes.map((node) =>
    node.id === 'analyse' ? { ...node, position: { x: 640, y: 420 } } : node,
  );
  const rebuilt = graphToDefinition(moved, graph.edges, definition);
  assert.deepEqual(rebuilt.edges, definition.edges);
  assert.deepEqual(sortedNodes(rebuilt), sortedNodes(definition));
  assert.deepEqual(chainOrder(rebuilt), ['start', 'analyse', 'end']);
  assert.deepEqual(regionProblems(rebuilt), []);
});

test('a model node output is a legal source and the graph has no region problem', () => {
  const definition = modelDefinition();
  assert.deepEqual(regionProblems(definition), []);
  const sources = legalSources(definition, 'end').map((entry) => entry.ref);
  assert.ok(sources.includes('node:analyse#/summary'), sources);
  assert.ok(isLegalSource(definition, 'end', 'node:analyse#/summary'));
  assert.equal(sourceType(definition, 'node:analyse#/summary'), 'string');
  // The model node itself may read only the application input on this path.
  const modelSources = legalSources(definition, 'analyse').map((entry) => entry.ref);
  assert.ok(modelSources.includes('input#/task'), modelSources);
});

test('export keeps the model allowlist and never carries response_schema', () => {
  assert.deepEqual(CONFIG_ALLOWLIST.model, [
    'model_profile_version',
    'prompt_template',
    'response_mode',
  ]);

  const definition = modelDefinition();
  const payload = exportPayload(definition, { version: 1, positions: {} });
  assert.deepEqual(payload.definition, definition);

  // A response_schema config key is not part of the contract: drop it and say so.
  const tainted = JSON.parse(JSON.stringify(definition));
  tainted.nodes[1].config.response_schema = { type: 'object' };
  tainted.nodes[1].config.unknown_key = 'nope';
  const audit = exportPayload(tainted, {});
  const paths = audit.dropped.map((entry) => entry.path);
  assert.ok(paths.includes('/nodes/1/config/response_schema'), paths);
  assert.ok(paths.includes('/nodes/1/config/unknown_key'), paths);
  assert.equal(audit.definition.nodes[1].config.response_schema, undefined);
  assert.deepEqual(audit.definition.nodes[1].config, definition.nodes[1].config);

  // The negative secret filter still refuses a credential field outright.
  const secret = JSON.parse(JSON.stringify(definition));
  secret.nodes[1].config.secret_ref = 'env:NEVER';
  assert.throws(() => exportPayload(secret, {}), (error) =>
    error instanceof ExportRefused && error.code === 'sensitive_key');
});

console.log('PASS ' + checks + ' canvas T086 model tests');
