/*
 * F04B/T087 canvas pure-module tests: the human approval node.
 *
 * The canvas modules are DOM-free; these assertions execute the same bytes the
 * page bundles. They pin the palette entry, the contract config shape
 * ({title, expires_seconds}), the `proposal` bound input, the round-trip through
 * the graph builder and the export allowlist. The built bundle is inspected by
 * tests/f04b/test_t087_canvas.py; no browser is driven.
 *
 * Run with:
 *   node tests/f04b/t087_canvas.test.mjs
 */

import assert from 'node:assert/strict';

import {
  DEFAULT_POSITIONS,
  NODE_LABELS,
  NODE_TYPES,
  REPEATABLE_NODE_TYPES,
  addNode,
  defaultNode,
  definitionToGraph,
  graphToDefinition,
  regionProblems,
} from '../../apps/web/canvas/src/lib/dsl.js';
import {
  CONFIG_ALLOWLIST,
  sanitizeDefinition,
} from '../../apps/web/canvas/src/lib/export.js';

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

function authoredDefinition() {
  return {
    dsl_version: '1.0',
    input_schema: {
      type: 'object',
      properties: {
        order: {
          type: 'object',
          properties: { subject: { type: 'string' }, amount: { type: 'string' } },
        },
      },
    },
    output_schema: {
      type: 'object',
      properties: { approval_id: { type: 'string' } },
      required: ['approval_id'],
    },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, config: {}, output_schema: { type: 'object' } },
      {
        id: 'approve1',
        type: 'approval',
        inputs: { proposal: { ref: 'input#/order' } },
        output_schema: {
          type: 'object',
          properties: { approval_id: { type: 'string' } },
          required: ['approval_id'],
        },
        config: { title: 'Approve the order', expires_seconds: 3600 },
      },
      {
        id: 'end',
        type: 'end',
        inputs: { approval_id: { ref: 'node:approve1#/approval_id' } },
        config: {},
        output_schema: { type: 'object' },
      },
    ],
    edges: [
      { source: 'start', target: 'approve1' },
      { source: 'approve1', target: 'end' },
    ],
    policy: {
      budget_limit: '1.00',
      currency: 'USD',
      max_active_seconds: 300,
      max_child_calls: 0,
      max_child_depth: 0,
    },
  };
}

test('the palette offers the approval node with its contract config defaults', () => {
  assert.ok(NODE_TYPES.includes('approval'), NODE_TYPES);
  assert.equal(NODE_LABELS.approval, '人工审批');
  assert.ok(REPEATABLE_NODE_TYPES.includes('approval'));
  assert.ok(DEFAULT_POSITIONS.approval);
  const node = defaultNode('approval', 'approve1');
  assert.deepEqual(node.config, { title: '', expires_seconds: 3600 });
  assert.deepEqual(node.inputs, { proposal: { literal: {} } });
});

test('the palette can add a second approval node', () => {
  const first = addNode([], [], 'approval', { version: 1, positions: {} });
  assert.equal(first.ok, true, first);
  const second = addNode([first.node], [], 'approval', { version: 1, positions: {} });
  assert.equal(second.ok, true, second);
  assert.notEqual(second.node.id, first.node.id);
  assert.deepEqual(second.node.data.config, { title: '', expires_seconds: 3600 });
});

test('an authored approval graph round-trips config, inputs and edges', () => {
  const definition = authoredDefinition();
  assert.deepEqual(regionProblems(definition), []);
  const graph = definitionToGraph(definition, { version: 1, positions: {} });
  assert.equal(graph.nodes.length, 3);
  const approvalNode = graph.nodes.find((node) => node.id === 'approve1');
  assert.equal(approvalNode.data.nodeType, 'approval');
  assert.deepEqual(approvalNode.data.config, {
    title: 'Approve the order',
    expires_seconds: 3600,
  });
  const rebuilt = graphToDefinition(graph.nodes, graph.edges, definition);
  assert.deepEqual(
    rebuilt.nodes.find((node) => node.id === 'approve1').config,
    { title: 'Approve the order', expires_seconds: 3600 },
  );
  assert.deepEqual(
    rebuilt.nodes.find((node) => node.id === 'approve1').inputs,
    { proposal: { ref: 'input#/order' } },
  );
  assert.deepEqual(rebuilt.edges, definition.edges);
});

test('a literal proposal survives the graph round-trip', () => {
  const definition = authoredDefinition();
  definition.nodes[1].inputs = {
    proposal: { literal: { subject: 'order-42', amount: '1250.00' } },
  };
  const graph = definitionToGraph(definition, { version: 1, positions: {} });
  const rebuilt = graphToDefinition(graph.nodes, graph.edges, definition);
  assert.deepEqual(
    rebuilt.nodes.find((node) => node.id === 'approve1').inputs.proposal,
    { literal: { subject: 'order-42', amount: '1250.00' } },
  );
});

test('the export allowlist keeps exactly title and expires_seconds', () => {
  assert.deepEqual(CONFIG_ALLOWLIST.approval, ['title', 'expires_seconds']);
  const definition = authoredDefinition();
  definition.nodes[1].config = {
    title: 'Approve the order',
    expires_seconds: 3600,
    proposal: { subject: 'smuggled' },
    tool_version: 'not-this-node',
  };
  const { definition: cleaned, dropped } = sanitizeDefinition(definition);
  const node = cleaned.nodes.find((item) => item.id === 'approve1');
  assert.deepEqual(node.config, { title: 'Approve the order', expires_seconds: 3600 });
  const paths = dropped.map((item) => item.path);
  assert.ok(paths.includes('/nodes/1/config/proposal'), paths);
  assert.ok(paths.includes('/nodes/1/config/tool_version'), paths);
  // The bound proposal input is part of the node's `inputs`, not its config, so
  // it survives unchanged.
  assert.deepEqual(node.inputs, { proposal: { ref: 'input#/order' } });
});

test('a non-approval type cannot smuggle the approval config through export', () => {
  const definition = authoredDefinition();
  definition.nodes[1].type = 'python';
  const { definition: cleaned, dropped } = sanitizeDefinition(definition);
  const node = cleaned.nodes.find((item) => item.id === 'approve1');
  assert.deepEqual(node.config, {});
  const paths = dropped.map((item) => item.path);
  assert.ok(paths.includes('/nodes/1/config/title'), paths);
  assert.ok(paths.includes('/nodes/1/config/expires_seconds'), paths);
});
