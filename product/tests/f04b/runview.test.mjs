/*
 * F04B/T083 run-view pure-module tests, executed by Node from pytest.
 *
 * The module under test is `apps/web/src/assets/runview.js`, the classic script
 * the debug page loads. It is read as bytes and executed in a `vm` context, so
 * these assertions run the real shipped source, not a copy. The fixtures are the
 * real P05 example definitions (`docs/spec/examples/*`).
 *
 * Run with:
 *   node tests/f04b/runview.test.mjs
 */

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT = path.resolve(HERE, '..', '..');
const RUNVIEW_SOURCE = path.join(PRODUCT, 'apps', 'web', 'src', 'assets', 'runview.js');
const BRANCH_EXAMPLE = path.join(
  PRODUCT, 'docs', 'spec', 'examples', 'branch-approval', 'workflow.json',
);
const PARALLEL_EXAMPLE = path.join(
  PRODUCT, 'docs', 'spec', 'examples', 'parallel', 'workflow.json',
);

function loadRunView() {
  const context = { console };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(RUNVIEW_SOURCE, 'utf8'), context, { filename: 'runview.js' });
  return context;
}

function exampleDefinition(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** Cross-realm plain copy: objects built inside the vm have a different prototype. */
function plain(value) {
  return value === undefined ? undefined : JSON.parse(JSON.stringify(value));
}

function nodeStatus(seq, nodeID, state, attemptNo, extra) {
  return {
    seq: seq,
    type: 'node.status',
    payload: Object.assign({ node_id: nodeID, state: state, attempt_no: attemptNo }, extra || {}),
  };
}

let checks = 0;
function test(name, body) {
  body();
  checks += 1;
  console.log('ok ' + checks + ' - ' + name);
}

// --- aggregateNodeStates ---------------------------------------------------

test('aggregateNodeStates takes the latest attempt and never regresses a terminal state', () => {
  const runView = loadRunView();
  const states = runView.aggregateNodeStates([
    // Deliberately out of order and duplicated, as a reconnect replay can be.
    nodeStatus(9, 'stats', 'succeeded', 1),
    nodeStatus(3, 'stats', 'pending', 1),
    nodeStatus(5, 'stats', 'running', 1),
    nodeStatus(7, 'stats', 'running', 1),
    nodeStatus(11, 'stats', 'running', 2),
  ]);
  assert.deepStrictEqual(plain(states.stats), { state: 'running', attempt_no: 2 });
  // A terminal state is never demoted by a replay of an earlier non-terminal one.
  const replayed = runView.aggregateNodeStates([
    nodeStatus(2, 'stats', 'succeeded', 1),
    nodeStatus(1, 'stats', 'pending', 1),
  ]);
  assert.deepStrictEqual(plain(replayed.stats), { state: 'succeeded', attempt_no: 1 });
  // A failed attempt is not overwritten by an older attempt's success.
  const retried = runView.aggregateNodeStates([
    nodeStatus(4, 'stats', 'succeeded', 1),
    nodeStatus(8, 'stats', 'failed', 2),
  ]);
  assert.deepStrictEqual(plain(retried.stats), { state: 'failed', attempt_no: 2 });
  // Failed outranks succeeded in one attempt (fail-fast is never hidden).
  const sameAttempt = runView.aggregateNodeStates([
    nodeStatus(4, 'stats', 'succeeded', 1),
    nodeStatus(5, 'stats', 'failed', 1),
  ]);
  assert.strictEqual(sameAttempt.stats.state, 'failed');
});

test('aggregateNodeStates keeps skipped distinct and ignores malformed frames', () => {
  const runView = loadRunView();
  const states = runView.aggregateNodeStates([
    nodeStatus(1, 'direct', 'pending', 1),
    nodeStatus(2, 'direct', 'skipped', 1),
    // A duplicate/replayed non-terminal frame never overwrites skipped.
    nodeStatus(1, 'direct', 'pending', 1),
    nodeStatus(3, 'choose', 'succeeded', 1, { error_code: 'ignored?' }),
    { seq: 4, type: 'run.status', payload: { state: 'running' } },
    { seq: 5, type: 'node.status', payload: { state: 'running', attempt_no: 1 } },
    { seq: 6, type: 'node.status', payload: { node_id: 'x', state: 'teleported', attempt_no: 1 } },
    { seq: 7, type: 'node.status' },
    null,
    'nope',
  ]);
  assert.deepStrictEqual(plain(states.direct), { state: 'skipped', attempt_no: 1 });
  assert.deepStrictEqual(plain(states.choose), { state: 'succeeded', attempt_no: 1 });
  assert.deepStrictEqual(Object.keys(states).sort(), ['choose', 'direct']);
  assert.deepStrictEqual(plain(runView.aggregateNodeStates(null)), {});
  assert.deepStrictEqual(plain(runView.aggregateNodeStates([{ seq: 1 }])), {});
  // A failed node's stable error code is kept as the server reported it.
  const failed = runView.aggregateNodeStates([
    nodeStatus(1, 'stats', 'failed', 1, { error_code: 'node_output_invalid' }),
  ]);
  assert.deepStrictEqual(plain(failed.stats), {
    state: 'failed', attempt_no: 1, error_code: 'node_output_invalid',
  });
  // A retry that supersedes a failed attempt drops the stale error code.
  const retriedFailure = runView.aggregateNodeStates([
    nodeStatus(1, 'stats', 'failed', 1, { error_code: 'node_output_invalid' }),
    nodeStatus(2, 'stats', 'succeeded', 2),
  ]);
  assert.deepStrictEqual(plain(retriedFailure.stats), { state: 'succeeded', attempt_no: 2 });
});

console.log('PASS ' + checks + ' runview tests');
