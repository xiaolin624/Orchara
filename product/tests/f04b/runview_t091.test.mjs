/*
 * F04B/T091 run-view tests: the two-approval workflow table.
 *
 * The module under test is the real `apps/web/src/assets/runview.js` bytes, run
 * in a `vm` context. The definition is the checked-in ten-node fixture; the node
 * states, node-results and artifact contents are the documented shapes the real
 * executor/owner routes produce (T083 `{port, matched_case}`, T085 join envelope,
 * T087 approval decision artifact).
 *
 * Run with:
 *   node tests/f04b/runview_t091.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT = path.resolve(HERE, '..', '..');
const RUNVIEW_SOURCE = path.join(PRODUCT, 'apps', 'web', 'src', 'assets', 'runview.js');
const TEN_NODE = path.join(HERE, 'fixtures', 'ten_node_definition.json');

function loadRunView() {
  const context = { console };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(RUNVIEW_SOURCE, 'utf8'), context, { filename: 'runview.js' });
  return context;
}

/** Cross-realm plain copy: vm objects have a different prototype. */
function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function definition() {
  return JSON.parse(fs.readFileSync(TEN_NODE, 'utf8'));
}

function states(pairs) {
  return Object.fromEntries(
    pairs.map(([nodeID, state, attemptNo]) => [nodeID, { state, attempt_no: attemptNo }]),
  );
}

function nodeResult(nodeID, artifactID) {
  return {
    node_id: nodeID,
    kind: 'data',
    artifact_id: artifactID,
    label: 'data',
    media_type: 'application/json',
  };
}

const APPROVAL_DOCUMENT = {
  approval_id: '0b7b6f44-6a34-4e6f-a1a8-1d2c3e4f5a6b',
  decision: 'approved',
  decided_at: '2026-11-06T01:00:00Z',
  params_sha256: 'a'.repeat(64),
  proposal: { subject: 'order-42', amount: '1250.00' },
};

const INNER_JOIN = {
  results: {
    agent: {
      status: 'succeeded',
      value: { text: 'stub-agent:0123456789abcdef' },
    },
    py: { status: 'failed', error_code: 'node_output_invalid' },
  },
};

const OUTER_JOIN = {
  results: {
    join2: { status: 'succeeded', value: INNER_JOIN },
    direct: { status: 'skipped' },
  },
};

let checks = 0;
function test(name, body) {
  body();
  checks += 1;
  console.log('ok ' + checks + ' - ' + name);
}

test('a waiting approval names the pending human decision instead of bare running', () => {
  const runView = loadRunView();
  const model = runView.buildRunViewModel(
    definition(),
    // The first attempt parked at approve: the node emitted pending/running and
    // the Run is waiting_approval. approve2 has not started at all.
    states([
      ['start', 'succeeded', 1],
      ['choose', 'succeeded', 1],
      ['approve', 'running', 1],
      ['direct', 'skipped', 1],
    ]),
    { items: [] },
    {},
    { state: 'waiting_approval', error_code: null },
  );
  const approve = model.rows.find((row) => row.nodeId === 'approve');
  assert.equal(approve.approvalWaiting, true);
  assert.ok(approve.approvalText.includes('等待人工审批'), approve.approvalText);
  const approve2 = model.rows.find((row) => row.nodeId === 'approve2');
  assert.equal(approve2.approvalWaiting, false, approve2);
  assert.equal(approve2.approvalText, '', approve2);
  assert.equal(model.runState, 'waiting_approval');
});

test('an approved approval row shows the real server decision artifact', () => {
  const runView = loadRunView();
  const model = runView.buildRunViewModel(
    definition(),
    states([
      ['approve', 'succeeded', 2],
      ['approve2', 'succeeded', 3],
    ]),
    {
      items: [
        nodeResult('approve', 'artifact-approve'),
        nodeResult('approve2', 'artifact-approve2'),
      ],
    },
    {
      'artifact-approve': APPROVAL_DOCUMENT,
      'artifact-approve2': Object.assign({}, APPROVAL_DOCUMENT, {
        approval_id: '11111111-2222-4333-8444-555555555555',
      }),
    },
    { state: 'succeeded', error_code: null },
  );
  const approve = model.rows.find((row) => row.nodeId === 'approve');
  assert.equal(approve.state, 'succeeded');
  assert.ok(approve.approvalText.includes('审批已通过'), approve.approvalText);
  assert.ok(approve.approvalText.includes('0b7b6f44'), approve.approvalText);
  assert.equal(approve.approvalWaiting, false);
  const approve2 = model.rows.find((row) => row.nodeId === 'approve2');
  assert.ok(approve2.approvalText.includes('11111111'), approve2.approvalText);
});

test('the nested join rows render each source and the converged optional error', () => {
  const runView = loadRunView();
  const model = runView.buildRunViewModel(
    definition(),
    states([
      ['join2', 'succeeded', 1],
      ['join1', 'succeeded', 1],
    ]),
    {
      items: [nodeResult('join2', 'artifact-join2'), nodeResult('join1', 'artifact-join1')],
    },
    { 'artifact-join2': INNER_JOIN, 'artifact-join1': OUTER_JOIN },
    { state: 'succeeded', error_code: null },
  );
  const inner = model.rows.find((row) => row.nodeId === 'join2');
  assert.equal(inner.joinSources.length, 2, inner.joinSources);
  assert.ok(inner.joinText.includes('已收敛的错误'), inner.joinText);
  assert.ok(inner.joinText.includes('node_output_invalid'), inner.joinText);
  assert.equal(inner.convergedErrors, 1);
  const outer = model.rows.find((row) => row.nodeId === 'join1');
  assert.ok(outer.joinText.includes('join2=成功'), outer.joinText);
  assert.ok(outer.joinText.includes('direct=已跳过'), outer.joinText);
});

test('the ten-node table keeps every node in topology order with approvals present', () => {
  const runView = loadRunView();
  const model = runView.buildRunViewModel(
    definition(),
    states([]),
    { items: [] },
    {},
    { state: 'running', error_code: null },
  );
  assert.equal(model.rows.length, 13, model.rows.length);
  assert.deepEqual(
    plain(model.rows.slice(0, 2).map((row) => row.nodeId)),
    ['start', 'choose'],
  );
  const types = new Set(model.rows.map((row) => row.type));
  for (const type of ['start', 'end', 'model', 'agent', 'python', 'call_app', 'retrieve',
                      'condition', 'join', 'approval']) {
    assert.ok(types.has(type), type);
  }
  assert.ok(model.sourceText.includes('审批'), model.sourceText);
});

console.log('PASS ' + checks + ' runview T091 tests');
