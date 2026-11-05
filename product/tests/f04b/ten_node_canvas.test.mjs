/*
 * F04B/T091 canvas integration: all ten node types on one authored draft.
 *
 * The modules under test are the real bytes the canvas bundles (`canvas/src/lib/*`),
 * and the fixture is the checked-in `tests/f04b/fixtures/ten_node_definition.json`:
 * one definition with a `start`, `end`, `model`, `agent`, `python`, `call_app`,
 * `retrieve`, `condition`, `join` and `approval` node, a parallel fork/join nested
 * inside a condition branch, condition edge ports and join-by-source inputs.
 *
 * The tests pin the T091 slice through the canvas seams:
 *   - the palette/default config of every type matches the contract allowlist;
 *   - the ten-node fixture round-trips byte-identically (topology order preserved);
 *   - the source selector offers join envelopes by source, whole retrieve arrays,
 *     the call_app mapping candidates and the approval proposal object, and hides
 *     branch nodes / inner joins;
 *   - export keeps exactly the ten allowlists and reports what it dropped; it
 *     refuses credential/endpoint/run-input keys (no capability the UI never
 *     showed can leave the browser);
 *   - every panel field and edge error is located with a Chinese repair hint.
 *
 * Run with:
 *   node tests/f04b/ten_node_canvas.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import {
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
  isLegalSource,
  legalSources,
  sourceType,
} from '../../apps/web/canvas/src/lib/sources.js';
import {
  fixHintForCode,
  mapValidationErrors,
} from '../../apps/web/canvas/src/lib/errors.js';
import {
  CONFIG_ALLOWLIST,
  ExportRefused,
  exportPayload,
  sanitizeDefinition,
} from '../../apps/web/canvas/src/lib/export.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = path.join(HERE, 'fixtures');
const TEN_NODE = JSON.parse(
  fs.readFileSync(path.join(FIXTURES, 'ten_node_definition.json'), 'utf8'),
);
const TEN_NODE_LAYOUT = JSON.parse(
  fs.readFileSync(path.join(FIXTURES, 'ten_node_layout.json'), 'utf8'),
);

const TEN_TYPES = [
  'start', 'end', 'model', 'agent', 'python', 'call_app', 'retrieve', 'condition', 'join',
  'approval',
];

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

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function fixture() {
  return clone(TEN_NODE);
}

function nodeById(definition, id) {
  return definition.nodes.find((node) => node.id === id);
}

function roundTrip(definition, layout = TEN_NODE_LAYOUT) {
  const graph = definitionToGraph(definition, layout);
  return graphToDefinition(graph.nodes, graph.edges, definition);
}

test('the palette offers the ten contract node types with Chinese labels', () => {
  assert.deepEqual([...NODE_TYPES].sort(), [...TEN_TYPES].sort(), NODE_TYPES);
  for (const type of TEN_TYPES) {
    assert.equal(typeof NODE_LABELS[type], 'string', type);
    assert.notEqual(NODE_LABELS[type], '', type);
    assert.notEqual(NODE_LABELS[type], type, type);
  }
  // start/end are unique; every other type is repeatable.
  assert.deepEqual(
    [...REPEATABLE_NODE_TYPES].sort(),
    [...TEN_TYPES.filter((type) => type !== 'start' && type !== 'end')].sort(),
  );
  // The palette can add each repeatable type, and a second approval/join/condition too.
  let nodes = [];
  for (const type of TEN_TYPES) {
    const added = addNode(nodes, [], type, { version: 1, positions: {} });
    assert.equal(added.ok, true, [type, added]);
    nodes = [...nodes, added.node];
  }
  const secondApproval = addNode(nodes, [], 'approval', { version: 1, positions: {} });
  assert.equal(secondApproval.ok, true, secondApproval);
  assert.notEqual(secondApproval.node.id, nodes.find((node) => node.data.nodeType === 'approval').id);
  const secondJoin = addNode([...nodes, secondApproval.node], [], 'join', { version: 1, positions: {} });
  assert.equal(secondJoin.ok, true, secondJoin);
});

test('every default node config is exactly its contract allowlist entry', () => {
  for (const type of TEN_TYPES) {
    const node = defaultNode(type, 'probe-' + type);
    assert.deepEqual(
      Object.keys(node.config).sort(),
      [...CONFIG_ALLOWLIST[type]].sort(),
      type,
    );
    assert.equal(node.type, type, type);
    assert.deepEqual(node.inputs, type === 'approval' ? { proposal: { literal: {} } } : {}, type);
  }
  // The approval proposal is a bound input, never a config key; the model response
  // schema is the node output_schema, never a config key.
  assert.ok(!CONFIG_ALLOWLIST.approval.includes('proposal'));
  assert.ok(!CONFIG_ALLOWLIST.model.includes('response_schema'));
});

test('the ten-node fixture round-trips byte-identically through the canvas', () => {
  const definition = fixture();
  const types = new Set(definition.nodes.map((node) => node.type));
  assert.deepEqual([...types].sort(), [...TEN_TYPES].sort());
  const rebuilt = roundTrip(definition);
  assert.equal(JSON.stringify(rebuilt), JSON.stringify(definition));
  // Edge ports and join-by-source inputs survive exactly.
  const chooseEdges = rebuilt.edges.filter((edge) => edge.source === 'choose');
  assert.deepEqual(chooseEdges.map((edge) => edge.port), ['yes', 'no']);
  assert.deepEqual(
    nodeById(rebuilt, 'join1').config.required_sources,
    ['join2', 'direct'],
  );
  assert.deepEqual(nodeById(rebuilt, 'join2').config.optional_sources, ['py']);
  assert.deepEqual(
    nodeById(rebuilt, 'end').inputs.branch_yes,
    { ref: 'node:join1#/results/join2/value' },
  );
});

test('the fixture nests a parallel fork/join inside a condition region', () => {
  const definition = fixture();
  assert.deepEqual(regionProblems(definition), []);
  // The condition region pairs `choose` with `join1`; the parallel region pairs
  // `fork` with `join2` and lives entirely inside the condition's yes branch.
  const rebuilt = roundTrip(definition);
  assert.deepEqual(nodeById(rebuilt, 'choose').config.join_id, 'join1');
  assert.deepEqual(nodeById(rebuilt, 'join1').config.fork_id, 'choose');
  assert.deepEqual(nodeById(rebuilt, 'fork').type, 'python');
  assert.deepEqual(nodeById(rebuilt, 'join2').config.fork_id, 'fork');
});

test('the source selector offers join envelopes by source and hides branch outputs', () => {
  const definition = fixture();
  const endSources = legalSources(definition, 'end').map((entry) => entry.ref);
  // join1 dominates end, so its envelope and each source entry are offered.
  assert.ok(endSources.includes('node:join1#/results'), endSources);
  assert.ok(endSources.includes('node:join1#/results/join2'), endSources);
  assert.ok(endSources.includes('node:join1#/results/join2/value'), endSources);
  assert.ok(endSources.includes('node:join1#/results/direct'), endSources);
  // A branch node is only readable through its join, never directly.
  assert.ok(!endSources.includes('node:approve#/approval_id'), endSources);
  assert.ok(!endSources.includes('node:direct#/summary'), endSources);
  // An inner join that can be skipped on the no path must not be offered.
  assert.ok(!endSources.includes('node:join2#/results'), endSources);
  assert.equal(sourceType(definition, 'node:join1#/results/join2/value'), 'object');
  assert.equal(sourceType(definition, 'node:join1#/results/direct/value'), 'object');
});

test('retrieve arrays, call_app mapping fields and the approval proposal are selectable', () => {
  const definition = fixture();
  // The retrieve node's citations/items are whole-array sources (T088).
  assert.equal(sourceType(definition, 'node:retrieve#/citations'), 'array<object>');
  assert.equal(sourceType(definition, 'node:retrieve#/items'), 'array<object>');
  assert.ok(isLegalSource(definition, 'child', 'node:retrieve#/citations'), 'child citation source');
  assert.ok(isLegalSource(definition, 'child', 'input#/task'), 'child input source');
  // A call_app node's input rows may map any legal upstream value (its child
  // schema is frozen, not invented by the canvas).
  const childSources = legalSources(definition, 'child').map((entry) => entry.ref);
  assert.ok(childSources.includes('node:approve2#/decision'), childSources);
  assert.ok(childSources.includes('node:join1#/results/join2/value'), childSources);
  // The approval proposal accepts an object source after a join.
  assert.ok(isLegalSource(definition, 'approve2', 'node:join1#/results'), 'approval proposal source');
  assert.equal(sourceType(definition, 'node:join1#/results'), 'object');
});

test('export keeps exactly the ten-type allowlist and reports nothing for the fixture', () => {
  const definition = fixture();
  const payload = exportPayload(definition, TEN_NODE_LAYOUT);
  // The export rebuilds configs in sorted-key order, so it is structurally equal
  // (no key lost, no key added) rather than byte-identical.
  assert.deepEqual(payload.definition, definition);
  assert.equal(JSON.stringify(payload.layout), JSON.stringify(TEN_NODE_LAYOUT));
  assert.ok(!Object.prototype.hasOwnProperty.call(payload, 'dropped'), payload.dropped);
  // Every config key of every node is in the contract allowlist.
  payload.definition.nodes.forEach((node, index) => {
    for (const key of Object.keys(node.config)) {
      assert.ok(CONFIG_ALLOWLIST[node.type].includes(key), [index, node.type, key]);
    }
  });
});

test('export drops unknown config keys per type and reports deterministic paths', () => {
  const definition = fixture();
  definition.nodes.forEach((node) => {
    node.config = { ...node.config, __smuggled_capability__: true };
  });
  nodeById(definition, 'direct').config.response_schema = { type: 'object' };
  const { definition: cleaned, dropped } = sanitizeDefinition(definition);
  const paths = dropped.map((entry) => entry.path);
  definition.nodes.forEach((node, index) => {
    assert.ok(
      paths.includes('/nodes/' + index + '/config/__smuggled_capability__'),
      [index, node.id, paths],
    );
  });
  assert.ok(
    paths.includes('/nodes/' + definition.nodes.findIndex((node) => node.id === 'direct')
      + '/config/response_schema'),
    paths,
  );
  for (const entry of dropped) {
    assert.equal(entry.reason, 'unknown_config_key', entry);
  }
  // No node may keep a key its panel never showed.
  cleaned.nodes.forEach((node) => {
    assert.ok(!Object.prototype.hasOwnProperty.call(node.config, '__smuggled_capability__'));
    assert.ok(!Object.prototype.hasOwnProperty.call(node.config, 'response_schema'));
  });
});

test('export drops an unknown node type and reports unknown_node_type', () => {
  const definition = fixture();
  const index = definition.nodes.findIndex((node) => node.id === 'child');
  definition.nodes[index] = {
    ...definition.nodes[index],
    type: 'mystery',
    config: { application_version: 'x', hidden_capability: true },
  };
  const { definition: cleaned, dropped } = sanitizeDefinition(definition);
  assert.deepEqual(cleaned.nodes[index].config, {});
  const paths = dropped.map((entry) => entry.path);
  const reasons = dropped.map((entry) => entry.reason);
  assert.ok(paths.includes('/nodes/' + index + '/config/application_version'), paths);
  assert.ok(paths.includes('/nodes/' + index + '/config/hidden_capability'), paths);
  assert.ok(reasons.every((reason) => reason === 'unknown_node_type'), reasons);
});

test('export refuses secrets, endpoints and run inputs anywhere in a ten-node draft', () => {
  for (const key of ['api_key', 'run_input', 'endpoint_ref']) {
    const definition = fixture();
    definition.nodes[definition.nodes.length - 1].config = { [key]: 'nope' };
    assert.throws(
      () => exportPayload(definition, TEN_NODE_LAYOUT),
      (error) => error instanceof ExportRefused && error.code === 'sensitive_key',
      key,
    );
    const layout = clone(TEN_NODE_LAYOUT);
    layout[key] = 'nope';
    assert.throws(
      () => exportPayload(fixture(), layout),
      (error) => error instanceof ExportRefused && error.code === 'sensitive_key',
      'layout ' + key,
    );
  }
});

test('validation pointers locate every one of the ten panels with a repair hint', () => {
  const definition = fixture();
  const cases = [
    ['/nodes/0', 'start', 'node'],
    ['/nodes/1/config/default_port', 'choose', 'config.default_port'],
    ['/nodes/1/config/cases', 'choose', 'config.cases'],
    ['/nodes/2/config/title', 'approve', 'config.title'],
    ['/nodes/3/config/model_profile_version', 'direct', 'config.model_profile_version'],
    ['/nodes/4/config/tool_version', 'fork', 'config.tool_version'],
    ['/nodes/6/config/instructions', 'agent', 'config.instructions'],
    ['/nodes/7/config/required_sources', 'join2', 'config.required_sources'],
    ['/nodes/9/config/top_k', 'retrieve', 'config.top_k'],
    ['/nodes/10/config/expires_seconds', 'approve2', 'config.expires_seconds'],
    ['/nodes/11/config/application_version', 'child', 'config.application_version'],
    ['/nodes/12/inputs/branch_no', 'end', 'inputs.branch_no'],
  ];
  for (const [pointer, nodeId, field] of cases) {
    const [mapped] = mapValidationErrors(definition, [{
      code: 'bad_config',
      pointer,
      message: 'server refusal',
    }]);
    assert.equal(mapped.nodeId, nodeId, pointer);
    assert.equal(mapped.field, field, pointer);
    assert.equal(mapped.hint, fixHintForCode('bad_config'));
    assert.ok(mapped.hint.length > 0 && mapped.hint.includes('请'), pointer);
  }
});

test('edge errors highlight the connection and every stable code has a Chinese hint', () => {
  const definition = fixture();
  const mapped = mapValidationErrors(definition, [
    { code: 'cross_region_edge', pointer: '/edges/1', message: 'choose->approve' },
    { code: 'bad_join_sources', pointer: '/nodes/8/config/required_sources', message: 'x' },
    { code: 'type_mismatch', pointer: '/nodes/12/inputs/branch_no', message: 'y' },
  ]);
  assert.equal(mapped[0].edgeIndex, 1);
  assert.equal(mapped[0].edgeId, 'choose->approve:1');
  assert.equal(mapped[0].source, 'choose');
  assert.equal(mapped[0].target, 'approve');
  assert.equal(mapped[0].field, 'edge.port');
  assert.equal(mapped[0].nodeId, 'choose');
  for (const error of mapped) {
    assert.ok(error.hint.length > 0 && /[\u4e00-\u9fff]/.test(error.hint), error.code);
  }
  // An unknown code still gets the safe fallback hint instead of an empty cell.
  assert.ok(fixHintForCode('some_future_code').includes('pointer'));
});

console.log('PASS ' + checks + ' canvas T091 ten-node tests');
