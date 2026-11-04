/*
 * F04A/T040 canvas pure-module tests, executed by Node from pytest.
 *
 * These modules have no Vue/DOM/fetch dependency; they are the seam the canvas
 * app and the page save path share. Run with:
 *   node tests/f04a/canvas_modules.test.mjs
 */

import assert from 'node:assert/strict';

import {
  addNode,
  canConnect,
  chainOrder,
  defaultDefinition,
  defaultLayout,
  definitionToGraph,
  graphToDefinition,
  layoutFromGraph,
  linearChainProblems,
  removeNode,
} from '../../apps/web/canvas/src/lib/dsl.js';
import {
  describeType,
  isLegalSource,
  legalSources,
  sourceGroups,
  sourceType,
  upstreamNodeIds,
} from '../../apps/web/canvas/src/lib/sources.js';
import {
  documentLevelErrors,
  fieldForError,
  mapValidationErrors,
  nodeIdForError,
} from '../../apps/web/canvas/src/lib/errors.js';
import {
  ExportRefused,
  assertExportSafe,
  exportJSON,
  exportPayload,
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

function configured() {
  const definition = defaultDefinition();
  definition.input_schema = {
    type: 'object',
    properties: {
      file: { type: 'string' },
      options: { type: 'object', properties: { mode: { type: 'string' } } },
    },
    required: ['file'],
  };
  definition.output_schema = {
    type: 'object',
    properties: { summary: { type: 'string' } },
    required: ['summary'],
  };
  const python = definition.nodes.find((node) => node.type === 'python');
  python.inputs = { text: { ref: 'input#/file' } };
  python.output_schema = {
    type: 'object',
    properties: { text: { type: 'string' } },
    required: ['text'],
  };
  python.config = { tool_version: '11111111-1111-4111-8111-111111111111' };
  const agent = definition.nodes.find((node) => node.type === 'agent');
  agent.inputs = {
    text: { ref: 'node:python1#/text' },
    mode: { ref: 'input#/options/mode' },
  };
  agent.output_schema = {
    type: 'object',
    properties: { summary: { type: 'string' } },
    required: ['summary'],
  };
  agent.config = {
    instructions: 'Summarise the extracted text.',
    max_turns: 4,
    model_profile_version: '22222222-2222-4222-8222-222222222222',
    subapp_versions: [],
    tool_versions: [],
  };
  const end = definition.nodes.find((node) => node.type === 'end');
  end.inputs = { summary: { ref: 'node:agent1#/summary' } };
  return definition;
}

test('dsl -> graph -> dsl preserves ids, order, config and layout', () => {
  const definition = configured();
  const layout = {
    version: 1,
    positions: {
      start: { x: 10, y: 20 },
      python1: { x: 210, y: 20 },
      agent1: { x: 410, y: 20 },
      end: { x: 610, y: 20 },
    },
  };
  const graph = definitionToGraph(definition, layout);
  assert.deepEqual(graph.nodes.map((node) => node.id), ['start', 'python1', 'agent1', 'end']);
  assert.deepEqual(graph.nodes[1].position, { x: 210, y: 20 });
  const moved = graph.nodes.map((node) =>
    node.id === 'agent1' ? { ...node, position: { x: 999, y: 111 } } : node,
  );
  const rebuilt = graphToDefinition(moved, graph.edges, definition);
  assert.deepEqual(rebuilt.nodes.map((node) => node.id), ['start', 'python1', 'agent1', 'end']);
  assert.deepEqual(
    rebuilt.nodes.find((node) => node.id === 'python1').config,
    definition.nodes.find((node) => node.id === 'python1').config,
  );
  assert.deepEqual(
    rebuilt.nodes.find((node) => node.id === 'agent1').inputs,
    definition.nodes.find((node) => node.id === 'agent1').inputs,
  );
  assert.deepEqual(rebuilt.output_schema, definition.output_schema);
  assert.deepEqual(chainOrder(rebuilt), ['start', 'python1', 'agent1', 'end']);
  const savedLayout = layoutFromGraph(moved, layout);
  assert.deepEqual(savedLayout.positions.agent1, { x: 999, y: 111 });
  assert.deepEqual(savedLayout.positions.start, { x: 10, y: 20 });
});

test('a fresh draft has exactly one of each F04A node and a linear chain', () => {
  const definition = defaultDefinition();
  assert.deepEqual(definition.nodes.map((node) => node.type), ['start', 'python', 'agent', 'end']);
  assert.equal(definition.edges.length, 3);
  assert.deepEqual(linearChainProblems(definitionToGraph(definition, defaultLayout(definition)).nodes,
    definitionToGraph(definition, defaultLayout(definition)).edges), []);
});

test('legal sources are application input plus upstream nodes only', () => {
  const definition = configured();
  assert.deepEqual(upstreamNodeIds(definition, 'agent1'), ['start', 'python1']);
  const agentRefs = legalSources(definition, 'agent1').map((entry) => entry.ref);
  assert.ok(agentRefs.includes('input#/file'));
  assert.ok(agentRefs.includes('input#/options/mode'));
  assert.ok(agentRefs.includes('node:python1#/text'));
  assert.ok(!agentRefs.some((ref) => ref.includes('node:agent1')), 'self is not upstream');
  assert.ok(!agentRefs.some((ref) => ref.includes('node:end')), 'downstream is not legal');
  const pythonRefs = legalSources(definition, 'python1').map((entry) => entry.ref);
  assert.ok(!pythonRefs.some((ref) => ref.includes('node:agent1')));
  assert.deepEqual(legalSources(definition, 'start'), []);
  assert.ok(isLegalSource(definition, 'agent1', 'input#/file'));
  assert.ok(!isLegalSource(definition, 'agent1', 'node:end#/summary'));
  assert.equal(sourceType(definition, 'node:python1#/text'), 'string');
  assert.equal(sourceType(definition, 'input#/options/mode'), 'string');
  assert.equal(sourceType(definition, 'input#/missing'), 'unknown');
  const groups = sourceGroups(definition, 'agent1');
  assert.deepEqual(groups.map((group) => group.key), ['input', 'python1']);
  assert.equal(describeType({ type: 'array', items: { type: 'integer' } }), 'array<integer>');
});

test('connection rules refuse everything that is not one linear chain', () => {
  const definition = configured();
  const graph = definitionToGraph(definition, defaultLayout(definition));
  const openEdges = graph.edges.filter(
    (edge) => !(edge.source === 'agent1' && edge.target === 'end'),
  );
  const ok = canConnect(graph.nodes, openEdges, { source: 'agent1', target: 'end' });
  assert.equal(ok.ok, true);
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'start', target: 'start' }).code,
    'self_loop');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'start', target: 'python1' }).code,
    'duplicate_edge');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'start', target: 'agent1' }).code,
    'outgoing_exists');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'python1', target: 'end' }).code,
    'outgoing_exists');
  const openStart = graph.edges.filter((edge) => edge.source !== 'start');
  assert.equal(canConnect(graph.nodes, openStart, { source: 'start', target: 'end' }).code,
    'incoming_exists');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'end', target: 'python1' }).code,
    'end_has_no_output');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'python1', target: 'start' }).code,
    'start_has_no_input');
  assert.equal(canConnect(graph.nodes, graph.edges, { source: 'python1', target: 'python1' }).code,
    'self_loop');
});

test('a branch or join is reported as a non-linear chain', () => {
  const definition = configured();
  const graph = definitionToGraph(definition, defaultLayout(definition));
  const branched = graph.edges.concat([{ source: 'start', target: 'agent1' }]);
  const problems = linearChainProblems(graph.nodes, branched);
  assert.ok(problems.some((problem) => problem.code === 'not_linear_chain'));
});

test('add/remove keep the four-node palette honest', () => {
  const definition = configured();
  const graph = definitionToGraph(definition, defaultLayout(definition));
  assert.equal(addNode(graph.nodes, graph.edges, 'agent').code, 'node_exists');
  const added = addNode(
    graph.nodes.filter((node) => node.id !== 'agent1'),
    graph.edges.filter((edge) => edge.source !== 'agent1' && edge.target !== 'agent1'),
    'agent',
  );
  assert.equal(added.ok, true);
  const removed = removeNode(graph.nodes, graph.edges, 'agent1');
  assert.ok(!removed.nodes.some((node) => node.id === 'agent1'));
  assert.ok(!removed.edges.some((edge) => edge.source === 'agent1' || edge.target === 'agent1'));
});

test('server errors map to node and config field', () => {
  const definition = configured();
  const mapped = mapValidationErrors(definition, [
    { code: 'bad_config', pointer: '/nodes/1/config', message: 'tool_version required' },
    { code: 'bad_pointer', pointer: '/nodes/2/inputs/text', node_id: 'agent1', message: 'x' },
    { code: 'cycle', pointer: '', message: 'cycle' },
    { code: 'unknown_endpoint', pointer: '/edges/2', message: 'edge' },
    { code: 'bad_schema', pointer: '/output_schema/properties/summary/widget', message: 'w' },
  ]);
  assert.equal(mapped[0].nodeId, 'python1');
  assert.equal(mapped[0].field, 'config');
  assert.equal(mapped[1].nodeId, 'agent1');
  assert.equal(mapped[1].field, 'inputs.text');
  assert.equal(mapped[2].nodeId, '');
  assert.equal(mapped[2].documentLevel, true);
  assert.equal(mapped[3].documentLevel, true);
  assert.equal(mapped[4].nodeId, '');
  assert.equal(documentLevelErrors(mapped).length, 3);
  assert.equal(nodeIdForError(definition, { pointer: '/nodes/99/config' }), '');
  assert.equal(fieldForError('/nodes/2/config/max_turns'), 'config.max_turns');
  assert.equal(fieldForError('/nodes/2/output_schema/properties/summary/type'),
    'output_schema.properties.summary.type');
  assert.equal(fieldForError('/policy/currency'), 'policy');
  assert.equal(fieldForError(''), 'document');
});

test('export is exactly definition+layout and refuses sensitive keys', () => {
  const definition = configured();
  const layout = defaultLayout(definition);
  const payload = exportPayload(definition, layout);
  assert.deepEqual(Object.keys(payload), ['definition', 'layout']);
  assert.deepEqual(payload.definition, JSON.parse(JSON.stringify(definition)));
  assert.deepEqual(payload.layout, layout);
  const text = exportJSON(definition, layout);
  assert.ok(!/"(secret|token|password|api_key|credential|memory|personal|run_input)"\s*:/i.test(text));
  assert.equal(assertExportSafe(definition, layout), true);
  const tainted = JSON.parse(JSON.stringify(definition));
  tainted.nodes[1].config.api_key = 'sk-should-never-export';
  assert.throws(() => exportPayload(tainted, layout), (error) =>
    error instanceof ExportRefused && error.code === 'sensitive_key');
  const taintedLayout = { positions: { start: { x: 0, y: 0, secret: 'x' } } };
  assert.throws(() => exportPayload(definition, taintedLayout), (error) =>
    error.code === 'sensitive_key');
});

console.log('PASS ' + checks + ' canvas module tests');
