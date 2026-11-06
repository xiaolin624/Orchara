/*
 * F04B/T083 canvas pure-module tests, executed by Node from pytest.
 *
 * These modules have no Vue/DOM/fetch dependency; they are the seam the canvas
 * app and the page save path share. Every fixture is the checked-in P05 corpus
 * (the same documents Go and Python validate), so the canvas is pinned against
 * the contract, not against a hand-written happy path.
 *
 * Run with:
 *   node tests/f04b/canvas_f04b.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CONDITION_OPS,
  JOIN_MODES,
  NODE_LABELS,
  NODE_TYPES,
  addNode,
  canConnect,
  chainOrder,
  conditionPorts,
  defaultDefinition,
  defaultLayout,
  definitionToGraph,
  graphToDefinition,
  regionProblems,
  removeNode,
} from '../../apps/web/canvas/src/lib/dsl.js';
import {
  isLegalSource,
  legalSources,
  sourceGroups,
  sourceType,
} from '../../apps/web/canvas/src/lib/sources.js';
import {
  edgeIndexFromPointer,
  fixHintForCode,
  mapValidationErrors,
} from '../../apps/web/canvas/src/lib/errors.js';
import {
  ExportRefused,
  exportJSON,
  exportPayload,
} from '../../apps/web/canvas/src/lib/export.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT = path.resolve(HERE, '..', '..');
const CORPUS = JSON.parse(
  fs.readFileSync(path.join(PRODUCT, 'probes', 'p05_dsl', 'corpus.json'), 'utf8'),
);

const CASES = new Map(CORPUS.cases.map((item) => [item.name, item]));

function fixture(name) {
  const found = CASES.get(name);
  assert.ok(found, 'the P05 corpus has no case ' + name);
  return JSON.parse(JSON.stringify(found.input));
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

function nodeIds(definition) {
  return definition.nodes.map((node) => node.id);
}

function byId(definition, id) {
  return definition.nodes.find((node) => node.id === id);
}

function sortedNodes(definition) {
  return [...definition.nodes].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** A ten-node draft the canvas itself can author (start/python/agent/condition/join/end). */
function authoredDefinition() {
  return {
    dsl_version: '1.0',
    input_schema: { type: 'object', properties: { flag: { type: 'boolean' } }, required: ['flag'] },
    output_schema: {
      type: 'object',
      properties: { summary: { type: 'string' } },
      required: ['summary'],
    },
    nodes: [
      { id: 'start', type: 'start', inputs: {}, config: {}, output_schema: { type: 'object' } },
      {
        id: 'left',
        type: 'python',
        inputs: { text: { ref: 'input#/flag' } },
        config: { tool_version: '11111111-1111-4111-8111-111111111111' },
        output_schema: { type: 'object', properties: { text: { type: 'string' } } },
      },
      {
        id: 'choose',
        type: 'condition',
        inputs: { flag: { ref: 'input#/flag' } },
        config: {
          join_id: 'join',
          default_port: 'no',
          cases: [{ input: 'flag', op: 'eq', value: true, port: 'yes' }],
        },
        output_schema: { type: 'object' },
      },
      {
        id: 'right',
        type: 'python',
        inputs: { text: { ref: 'input#/flag' } },
        config: { tool_version: '11111111-1111-4111-8111-111111111111' },
        output_schema: { type: 'object', properties: { text: { type: 'string' } } },
      },
      {
        id: 'join',
        type: 'join',
        inputs: {},
        config: {
          mode: 'all_active',
          fork_id: 'choose',
          required_sources: ['left', 'right'],
          optional_sources: [],
        },
        output_schema: { type: 'object' },
      },
      { id: 'end', type: 'end', inputs: { results: { ref: 'node:join#/results' } }, config: {}, output_schema: { type: 'object' } },
    ],
    edges: [
      { source: 'start', target: 'choose' },
      { source: 'choose', target: 'left', port: 'yes' },
      { source: 'choose', target: 'right', port: 'no' },
      { source: 'left', target: 'join' },
      { source: 'right', target: 'join' },
      { source: 'join', target: 'end' },
    ],
    policy: defaultDefinition().policy,
  };
}

test('palette offers condition/join with Chinese labels and repeatable node ids', () => {
  for (const type of ['start', 'python', 'agent', 'condition', 'join', 'end']) {
    assert.ok(NODE_TYPES.includes(type), type);
  }
  assert.equal(NODE_LABELS.condition, '条件分支');
  assert.equal(NODE_LABELS.join, '并行汇合');
  assert.deepEqual(CONDITION_OPS, ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'in', 'exists']);
  assert.deepEqual(JOIN_MODES, ['all_active']);

  const definition = authoredDefinition();
  const graph = definitionToGraph(definition, defaultLayout(definition));
  const condition = addNode(graph.nodes, graph.edges, 'condition', {});
  assert.equal(condition.ok, true);
  assert.notEqual(condition.node.id, 'choose');
  const secondCondition = addNode([...graph.nodes, condition.node], graph.edges, 'condition', {});
  assert.equal(secondCondition.ok, true);
  assert.notEqual(secondCondition.node.id, condition.node.id);
  const join = addNode(graph.nodes, graph.edges, 'join', {});
  assert.equal(join.ok, true);
  const python = addNode(graph.nodes, graph.edges, 'python', {});
  assert.equal(python.ok, true);
  assert.equal(addNode(graph.nodes, graph.edges, 'start', {}).code, 'node_exists');
  assert.equal(addNode(graph.nodes, graph.edges, 'end', {}).code, 'node_exists');

  const defaults = definitionToGraph(authoredDefinition(), null).nodes
    .find((node) => node.id === 'choose');
  assert.deepEqual(defaults.data.config.cases[0], { input: 'flag', op: 'eq', value: true, port: 'yes' });
  assert.equal(defaults.data.config.default_port, 'no');
});

test('condition ports and fork/join edges survive a graph -> definition round trip', () => {
  const definition = fixture('condition-branch-approval');
  const graph = definitionToGraph(definition, null);
  const choose = graph.nodes.find((node) => node.id === 'choose');
  assert.deepEqual(conditionPorts(choose.data.config), ['yes', 'no']);
  assert.deepEqual(choose.data.ports, ['yes', 'no']);
  const yesEdge = graph.edges.find((edge) => edge.source === 'choose' && edge.target === 'approve');
  assert.equal(yesEdge.data.port, 'yes');
  assert.equal(yesEdge.sourceHandle, 'yes');
  assert.equal(yesEdge.label, 'yes');
  const noEdge = graph.edges.find((edge) => edge.source === 'choose' && edge.target === 'direct');
  assert.equal(noEdge.data.port, 'no');
  assert.equal(graph.edges.find((edge) => edge.target === 'join').targetHandle, 'in');

  const moved = graph.nodes.map((node) =>
    node.id === 'approve' ? { ...node, position: { x: 900, y: 40 } } : node,
  );
  const rebuilt = graphToDefinition(moved, graph.edges, definition);
  assert.deepEqual(rebuilt.edges, definition.edges);
  assert.deepEqual(sortedNodes(rebuilt), sortedNodes(definition));
  assert.deepEqual(nodeIds(rebuilt), [
    'start', 'choose', 'approve', 'direct', 'join', 'end',
  ]);
});

test('parallel fork/join round trip keeps the join envelope configuration', () => {
  const definition = fixture('parallel-two-branch');
  const graph = definitionToGraph(definition, null);
  const rebuilt = graphToDefinition(graph.nodes, graph.edges, definition);
  assert.deepEqual(rebuilt.edges, definition.edges);
  const join = byId(rebuilt, 'join');
  assert.deepEqual(join.config, {
    fork_id: 'start',
    mode: 'all_active',
    required_sources: ['left', 'right'],
    optional_sources: [],
  });
  assert.deepEqual(nodeIds(rebuilt), ['start', 'left', 'right', 'join', 'end']);
  const nested = fixture('condition-nested-parallel');
  const order = chainOrder(nested);
  assert.equal(order.length, nested.nodes.length, order);
  assert.equal(new Set(order).size, nested.nodes.length);
  assert.deepEqual([...order].sort(), [...nodeIds(nested)].sort());
});

test('connection rules allow declared forks and joins and refuse implicit merges', () => {
  const definition = fixture('condition-branch-approval');
  const graph = definitionToGraph(definition, null);

  // A condition picks the next unused port automatically (cases order, then default).
  const empty = { nodes: graph.nodes, edges: [] };
  const first = canConnect(empty.nodes, empty.edges, { source: 'choose', target: 'approve' });
  assert.equal(first.ok, true);
  assert.equal(first.port, 'yes');
  const onlyYes = { nodes: graph.nodes, edges: [graph.edges[1]] };
  const second = canConnect(onlyYes.nodes, onlyYes.edges, { source: 'choose', target: 'direct' });
  assert.equal(second.ok, true);
  assert.equal(second.port, 'no');
  const both = canConnect(graph.nodes, [graph.edges[1], graph.edges[2]],
    { source: 'choose', target: 'join' });
  assert.equal(both.code, 'no_free_port');

  // An explicit port must exist for that condition and be used only once.
  assert.equal(
    canConnect(onlyYes.nodes, onlyYes.edges, { source: 'choose', target: 'direct', sourceHandle: 'yes' }).code,
    'port_exists',
  );
  assert.equal(
    canConnect(empty.nodes, empty.edges, { source: 'choose', target: 'approve', sourceHandle: 'nope' }).code,
    'bad_port',
  );

  // join accepts many incoming edges; a non-join merge is rejected.
  const withoutApproveJoin = {
    nodes: graph.nodes,
    edges: graph.edges.filter((edge) => !(edge.source === 'approve' && edge.target === 'join')),
  };
  assert.equal(
    canConnect(withoutApproveJoin.nodes, withoutApproveJoin.edges, { source: 'approve', target: 'join' }).ok,
    true,
  );
  assert.equal(
    canConnect(graph.nodes, graph.edges, { source: 'approve', target: 'direct' }).code,
    'merge_without_join',
  );
  assert.equal(
    canConnect(graph.nodes, graph.edges, { source: 'start', target: 'direct' }).code,
    'merge_without_join',
  );

  // A plain fork is allowed only when a join declares it.
  const parallel = definitionToGraph(fixture('parallel-two-branch'), null);
  assert.equal(
    canConnect(parallel.nodes, parallel.edges, { source: 'start', target: 'join' }).ok,
    true,
  );
  const undeclared = definitionToGraph(authoredDefinition(), null);
  const freshTarget = addNode(undeclared.nodes, undeclared.edges, 'python', {}).node;
  const undeclaredStart = canConnect([...undeclared.nodes, freshTarget], undeclared.edges,
    { source: 'start', target: freshTarget.id });
  assert.equal(undeclaredStart.code, 'fork_requires_join');
  assert.ok(undeclaredStart.reason.includes('join'));

  // Deleting a branch node prunes it from the paired join's sources.
  const pruned = removeNode(graph.nodes, graph.edges, 'direct');
  const rebuilt = graphToDefinition(pruned.nodes, pruned.edges, definition);
  assert.deepEqual(byId(rebuilt, 'join').config.required_sources, ['approve']);
  assert.deepEqual(byId(rebuilt, 'join').config.optional_sources, []);
});

test('regionProblems accepts every valid P05 region fixture', () => {
  for (const name of [
    'anchor-minimal-linear',
    'linear-sales',
    'linear-sales-nodes-shuffled',
    'parallel-two-branch',
    'parallel-optional-source',
    'condition-branch-approval',
    'condition-nested-parallel',
  ]) {
    assert.deepEqual(regionProblems(fixture(name)), [], name);
  }
  assert.deepEqual(regionProblems(authoredDefinition()), []);
});

test('regionProblems locates the invalid P05 fixtures like the Go validator', () => {
  const expected = {
    'merge-without-join': [['merge_without_join', '/nodes/3']],
    'join-fork-not-a-fork': [
      ['invalid_region', '/nodes/1/config/join_id'],
      ['invalid_region', '/nodes/4/config/fork_id'],
    ],
    'condition-port-not-declared': [
      ['bad_port', '/edges/1'],
      ['bad_port', '/nodes/1/config/cases'],
    ],
    'condition-branch-escapes-outer-join': [['cross_region_edge', '/edges/10']],
    'join-sources-mismatch': [['bad_join_sources', '/nodes/4/config/required_sources']],
    'precedence-port-over-join-sources': [
      ['bad_port', '/edges/1'],
      ['bad_port', '/nodes/1/config/cases'],
    ],
    // Stage 2 / stage 3 authorities that the canvas also mirrors.
    'cycle': [['cycle', '']],
    'unreachable-node': [['unreachable_node', '/nodes/4'], ['cannot_reach_end', '/nodes/4']],
    'cannot-reach-end': [['cannot_reach_end', '/nodes/4']],
    'missing-end-node': [['start_end_count', '']],
    'duplicate-node-id': [
      ['duplicate_id', '/nodes/2'],
      ['unknown_endpoint', '/edges/1'],
      ['unknown_endpoint', '/edges/2'],
    ],
    'unknown-edge-endpoint': [['unknown_endpoint', '/edges/2']],
    // Stage 5 (references / missing values) is not a region rule: the canvas
    // stays silent and the server remains the authority.
    'skipped-branch-reference': [],
  };
  for (const [name, problems] of Object.entries(expected)) {
    const actual = regionProblems(fixture(name)).map((problem) => [problem.code, problem.pointer]);
    assert.deepEqual(actual, problems, name);
  }

  // Edge-level problems carry the edge index and the source node id so the
  // canvas can highlight the connection instead of a document-level field.
  const escaped = regionProblems(fixture('condition-branch-escapes-outer-join'));
  assert.equal(escaped.length, 1);
  assert.equal(escaped[0].edgeIndex, 10);
  assert.equal(escaped[0].nodeId, 'prep');
  assert.ok(escaped[0].message.includes('region'));
  assert.ok(escaped[0].message.includes('prep'));
  assert.ok(escaped[0].message.includes('inner_join'));

  const ports = regionProblems(fixture('condition-port-not-declared'));
  assert.equal(ports[0].edgeIndex, 1);
  assert.equal(ports[0].nodeId, 'choose');
  const joinSources = regionProblems(fixture('join-sources-mismatch'));
  assert.equal(joinSources[0].nodeId, 'join');
  assert.ok(joinSources[0].message.includes('前驱'));
});

test('legal sources expose join results by source and hide pre-join branch outputs', () => {
  const definition = fixture('condition-branch-approval');
  const endRefs = legalSources(definition, 'end').map((entry) => entry.ref);
  assert.ok(endRefs.includes('node:join#/results'), endRefs);
  assert.ok(endRefs.includes('node:join#/results/approve'));
  assert.ok(endRefs.includes('node:join#/results/approve/value'));
  assert.ok(endRefs.includes('node:join#/results/direct/value'));
  assert.ok(!endRefs.some((ref) => ref.startsWith('node:approve#')), endRefs);
  assert.ok(!endRefs.some((ref) => ref.startsWith('node:direct#')), endRefs);

  // The join itself is not allowed to read its own branch inputs directly.
  const joinRefs = legalSources(definition, 'join').map((entry) => entry.ref);
  assert.ok(!joinRefs.some((ref) => ref.startsWith('node:approve#')));
  assert.ok(!joinRefs.some((ref) => ref.startsWith('node:direct#')));
  assert.ok(isLegalSource(definition, 'end', 'node:join#/results/direct'));
  assert.ok(!isLegalSource(definition, 'end', 'node:direct#'));
  assert.ok(!isLegalSource(definition, 'approve', 'node:join#/results/direct'));

  assert.equal(sourceType(definition, 'node:join#/results'), 'object');
  assert.equal(sourceType(definition, 'node:join#/results/direct/value'), 'object');
  const groups = sourceGroups(definition, 'end');
  const joinGroup = groups.find((group) => group.key === 'join');
  assert.ok(joinGroup, groups.map((group) => group.key));
  assert.ok(joinGroup.options.some((option) => option.ref === 'node:join#/results/approve'));

  const nested = fixture('condition-nested-parallel');
  const nestedRefs = legalSources(nested, 'end').map((entry) => entry.ref);
  assert.ok(nestedRefs.includes('node:outer_join#/results/inner_join/value'), nestedRefs);
  assert.ok(nestedRefs.includes('node:outer_join#/results/direct/value'));
  assert.ok(!nestedRefs.some((ref) => ref.startsWith('node:left#')));
});

test('export allowlist keeps declared config and reports dropped fields', () => {
  const definition = authoredDefinition();
  const layout = { version: 1, positions: {} };
  const payload = exportPayload(definition, layout);
  assert.deepEqual(Object.keys(payload), ['definition', 'layout']);
  assert.deepEqual(payload.definition, definition);
  assert.deepEqual(JSON.parse(exportJSON(definition, layout)), payload);

  const tainted = JSON.parse(JSON.stringify(definition));
  tainted.nodes[1].config.foo = 'bar';
  tainted.nodes[2].config.cases[0].extra = 'nope';
  // A type this canvas does not author (retrieve became a known type in T088, so
  // the unknown-type case uses a genuinely unknown type).
  tainted.nodes.push({
    id: 'mystery1',
    type: 'mystery',
    inputs: {},
    config: { knowledge_base_ids: ['11111111-1111-4111-8111-111111111111'] },
    output_schema: { type: 'object' },
  });
  tainted.extra_top_level = true;
  const audit = exportPayload(tainted, {});
  assert.ok(Array.isArray(audit.dropped) && audit.dropped.length >= 3, audit.dropped);
  const paths = audit.dropped.map((entry) => entry.path);
  assert.ok(paths.includes('/nodes/1/config/foo'), paths);
  assert.ok(paths.includes('/nodes/2/config/cases/0/extra'), paths);
  assert.ok(paths.includes('/nodes/6/config/knowledge_base_ids'), paths);
  assert.ok(paths.includes('/extra_top_level'), paths);
  assert.equal(audit.definition.nodes[1].config.foo, undefined);
  assert.equal(audit.definition.nodes[6].config.knowledge_base_ids, undefined);
  assert.equal(audit.definition.extra_top_level, undefined);
  assert.ok(audit.dropped.every((entry) => entry.reason), audit.dropped);

  const secret = JSON.parse(JSON.stringify(definition));
  secret.nodes[1].config.api_key = 'sk-never';
  assert.throws(() => exportPayload(secret, {}), (error) =>
    error instanceof ExportRefused && error.code === 'sensitive_key');
  const endpoint = JSON.parse(JSON.stringify(definition));
  endpoint.nodes[2].config.endpoint_ref = 'https://evil.example';
  assert.throws(() => exportPayload(endpoint, {}), (error) =>
    error instanceof ExportRefused && error.code === 'sensitive_key');
});

test('validation errors locate edges and carry Chinese repair hints', () => {
  const definition = fixture('condition-branch-approval');
  assert.equal(edgeIndexFromPointer('/edges/2'), 2);
  assert.equal(edgeIndexFromPointer('/edges/x'), null);
  assert.equal(edgeIndexFromPointer('/nodes/1/config'), null);

  const mapped = mapValidationErrors(definition, [
    { code: 'cross_region_edge', pointer: '/edges/2', message: 'edge crosses' },
    { code: 'bad_port', pointer: '/edges/1', message: 'port' },
    { code: 'bad_join_sources', pointer: '/nodes/4/config/required_sources', message: 'sources' },
    { code: 'invalid_region', pointer: '/nodes/1/config/join_id', message: 'pairing' },
    { code: 'merge_without_join', pointer: '', message: 'merge' },
    { code: 'unknown_endpoint', pointer: '/edges/99', message: 'gone' },
  ]);
  assert.equal(mapped[0].edgeIndex, 2);
  assert.equal(mapped[0].nodeId, 'choose');
  assert.equal(mapped[0].field, 'edge.port');
  assert.equal(mapped[0].documentLevel, false);
  assert.equal(mapped[0].edgeId, graphToDefinitionEdgeId(definition, 2));
  assert.ok(mapped[0].hint.includes('region'));
  assert.equal(mapped[1].edgeIndex, 1);
  assert.equal(mapped[1].nodeId, 'choose');
  assert.ok(mapped[1].hint.includes('端口'));
  assert.equal(mapped[2].field, 'config.required_sources');
  assert.equal(mapped[2].nodeId, 'join');
  assert.ok(mapped[2].hint.includes('直接前驱'));
  assert.equal(mapped[3].field, 'config.join_id');
  assert.equal(mapped[3].nodeId, 'choose');
  assert.ok(mapped[3].hint.includes('配对'));
  assert.equal(mapped[4].documentLevel, true);
  assert.ok(mapped[4].hint.length > 0);
  assert.equal(mapped[5].documentLevel, true);
  assert.equal(mapped[5].field, 'edges');
  assert.ok(fixHintForCode('bad_port').includes('端口'));
  assert.ok(fixHintForCode('no_such_code').length > 0);
});

function graphToDefinitionEdgeId(definition, index) {
  // The canvas edge id is stable from the definition order, so an error can
  // select the same connection the graph rendered.
  const graph = definitionToGraph(definition, null);
  return graph.edges[index].id;
}

console.log('PASS ' + checks + ' canvas F04B module tests');
