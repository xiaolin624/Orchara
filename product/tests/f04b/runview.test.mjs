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

function statesFrom(pairs) {
  return Object.fromEntries(
    pairs.map(([nodeID, state, attemptNo]) => [nodeID, { state, attempt_no: attemptNo }]),
  );
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

// --- buildRunViewModel: order, state, skipped ------------------------------

test('buildRunViewModel renders one row per definition node in topological order', () => {
  const runView = loadRunView();
  const definition = exampleDefinition(BRANCH_EXAMPLE);
  const nodeStates = statesFrom([
    ['start', 'succeeded', 1],
    ['choose', 'succeeded', 1],
    ['approve', 'skipped', 1],
    ['direct', 'succeeded', 1],
    ['join', 'succeeded', 1],
    ['end', 'succeeded', 1],
  ]);
  const model = runView.buildRunViewModel(
    definition, nodeStates, { items: [] }, {}, { state: 'succeeded' },
  );
  assert.deepStrictEqual(plain(model.rows.map((row) => row.nodeId)),
    ['start', 'choose', 'approve', 'direct', 'join', 'end']);
  const byID = Object.fromEntries(model.rows.map((row) => [row.nodeId, row]));
  assert.strictEqual(byID.choose.type, 'condition');
  assert.strictEqual(byID.choose.state, 'succeeded');
  assert.strictEqual(byID.choose.stateText, '成功');
  assert.strictEqual(byID.choose.attemptNo, 1);
  assert.strictEqual(byID.choose.attemptText, '1');
  assert.strictEqual(byID.choose.skipped, false);
  // An unselected branch is skipped, visually distinct from failed.
  assert.strictEqual(byID.approve.state, 'skipped');
  assert.strictEqual(byID.approve.skipped, true);
  assert.strictEqual(byID.approve.failed, false);
  assert.ok(byID.approve.classNames.includes('state-skipped'), byID.approve.classNames);
  assert.strictEqual(byID.direct.stateText, '成功');
  assert.deepStrictEqual(plain(model.counts), {
    total: 6, pending: 0, waiting: 0, running: 0,
    succeeded: 5, failed: 0, skipped: 1, unknown: 0,
  });
  assert.ok(model.text.includes('6 个节点'), model.text);
  assert.ok(model.text.includes('跳过 1'), model.text);
  // A shuffled definition (the P05 shuffle case) is still ordered so a node never
  // precedes its own predecessor.
  const shuffled = {
    ...definition,
    nodes: definition.nodes.slice().reverse(),
  };
  const shuffledModel = runView.buildRunViewModel(shuffled, nodeStates, { items: [] }, {}, null);
  assert.deepStrictEqual(plain(shuffledModel.rows.map((row) => row.nodeId)),
    ['start', 'choose', 'direct', 'approve', 'join', 'end']);
  // A state/result for a node the definition does not contain is appended, not hidden.
  const extra = runView.buildRunViewModel(
    definition,
    statesFrom([['ghost', 'running', 1]]),
    { items: [] },
    {},
    { state: 'running' },
  );
  assert.strictEqual(extra.rows[extra.rows.length - 1].nodeId, 'ghost');
  assert.strictEqual(extra.rows[extra.rows.length - 1].typeText, '（定义中不存在）');
});

test('buildRunViewModel carries run error codes and honest unknown states', () => {
  const runView = loadRunView();
  const definition = exampleDefinition(PARALLEL_EXAMPLE);
  // A failed run with a failed node: the run error_code is attributed to the
  // failed row only (node.status itself has no error_code field).
  const failedModel = runView.buildRunViewModel(
    definition,
    statesFrom([['start', 'succeeded', 1], ['left', 'failed', 1]]),
    { items: [] },
    {},
    { state: 'failed', error_code: 'node_output_invalid' },
  );
  const failedLeft = failedModel.rows.find((row) => row.nodeId === 'left');
  assert.strictEqual(failedLeft.errorCode, 'node_output_invalid');
  assert.ok(failedLeft.errorText.includes('node_output_invalid'), failedLeft.errorText);
  const startRow = failedModel.rows.find((row) => row.nodeId === 'start');
  assert.strictEqual(startRow.errorCode, '');
  assert.deepStrictEqual(plain(failedModel.counts), {
    total: 5, pending: 0, waiting: 0, running: 0,
    succeeded: 1, failed: 1, skipped: 0, unknown: 3,
  });
  const unknownRow = failedModel.rows.find((row) => row.nodeId === 'join');
  assert.strictEqual(unknownRow.state, 'unknown');
  assert.strictEqual(unknownRow.stateText, '无状态事件');
  // A live run's silent node is "waiting", not a fabricated success.
  const liveModel = runView.buildRunViewModel(
    definition, {}, { items: [] }, {}, { state: 'running' },
  );
  assert.strictEqual(liveModel.rows[0].state, 'pending');
  assert.strictEqual(liveModel.rows[0].observed, false);
  // Registered results without a status event are inference, labelled as such.
  const inferredModel = runView.buildRunViewModel(
    definition,
    {},
    { items: [{ node_id: 'left', kind: 'data', artifact_id: 'a-left' }] },
    {},
    { state: 'succeeded' },
  );
  const inferred = inferredModel.rows.find((row) => row.nodeId === 'left');
  assert.strictEqual(inferred.state, 'succeeded');
  assert.strictEqual(inferred.inferred, true);
  assert.ok(inferred.stateText.includes('推断'), inferred.stateText);
  assert.strictEqual(inferred.artifactId, 'a-left');
});

// --- buildRunViewModel: condition port and the join envelope ---------------

test('buildRunViewModel reads the selected port and the join envelope from data artifacts', () => {
  const runView = loadRunView();
  const definition = exampleDefinition(BRANCH_EXAMPLE);
  const nodeStates = statesFrom([
    ['start', 'succeeded', 1],
    ['choose', 'succeeded', 1],
    ['approve', 'skipped', 1],
    ['direct', 'succeeded', 1],
    ['join', 'succeeded', 1],
    ['end', 'succeeded', 1],
  ]);
  const nodeResults = {
    items: [
      { node_id: 'choose', kind: 'data', artifact_id: 'a-choose', media_type: 'application/json' },
      { node_id: 'join', kind: 'data', artifact_id: 'a-join', media_type: 'application/json' },
      { node_id: 'direct', kind: 'file', artifact_id: 'a-file', media_type: 'text/csv' },
    ],
  };
  const contents = {
    'a-choose': { port: 'no', matched_case: 'default' },
    'a-join': {
      results: {
        approve: { status: 'skipped' },
        direct: {
          status: 'succeeded',
          value: { summary: 'ok', nested: { a: 1 }, long: 'x'.repeat(300) },
        },
      },
    },
  };
  const model = runView.buildRunViewModel(
    definition, nodeStates, nodeResults, contents, { state: 'succeeded' },
  );
  const byID = Object.fromEntries(model.rows.map((row) => [row.nodeId, row]));
  assert.strictEqual(byID.choose.artifactId, 'a-choose');
  assert.strictEqual(byID.choose.branchPort, 'no');
  assert.strictEqual(byID.choose.matchedCase, 'default');
  assert.ok(byID.choose.branchText.includes('no'), byID.choose.branchText);
  assert.ok(byID.choose.branchText.includes('默认'), byID.choose.branchText);
  // The join sources keep the definition's required/optional order and the
  // envelope's value is summarised (truncated) with the full value available.
  assert.deepStrictEqual(plain(byID.join.joinSources.map((entry) => entry.source)),
    ['approve', 'direct']);
  assert.deepStrictEqual(plain(byID.join.joinSources.map((entry) => entry.status)),
    ['skipped', 'succeeded']);
  assert.strictEqual(byID.join.joinSources[0].statusText, '已跳过');
  assert.strictEqual(byID.join.joinSources[0].hasValue, false);
  assert.strictEqual(byID.join.joinSources[1].statusText, '成功');
  assert.strictEqual(byID.join.joinSources[1].hasValue, true);
  assert.ok(byID.join.joinSources[1].valueSummary.includes('summary'));
  assert.ok(byID.join.joinSources[1].valueSummary.length <= 140,
    byID.join.joinSources[1].valueSummary.length);
  assert.ok(byID.join.joinSources[1].valueText.includes('"summary"'));
  assert.ok(byID.join.joinText.includes('approve=已跳过'), byID.join.joinText);
  assert.ok(byID.join.joinText.includes('direct=成功'), byID.join.joinText);
  // matched_case is 0-based: the first case is shown as the first case.
  const first = runView.buildRunViewModel(
    definition, nodeStates, nodeResults,
    { 'a-choose': { port: 'yes', matched_case: 0 }, 'a-join': contents['a-join'] },
    { state: 'succeeded' },
  );
  const yesRow = first.rows.find((row) => row.nodeId === 'choose');
  assert.ok(yesRow.branchText.includes('第 1 个 case'), yesRow.branchText);
  // A failed join source keeps its stable error code in the source line.
  const failedJoin = runView.buildRunViewModel(
    definition, nodeStates, nodeResults,
    {
      'a-choose': { port: 'yes', matched_case: 0 },
      'a-join': {
        results: {
          approve: { status: 'failed', error_code: 'sandbox_failed' },
          direct: { status: 'skipped' },
        },
      },
    },
    { state: 'failed', error_code: 'sandbox_failed' },
  );
  const failedJoinRow = failedJoin.rows.find((row) => row.nodeId === 'join');
  assert.strictEqual(failedJoinRow.joinSources[0].errorCode, 'sandbox_failed');
  // A required source's failure is a failure, never converged error data.
  assert.strictEqual(failedJoinRow.joinSources[0].converged, false);
  assert.strictEqual(failedJoinRow.joinSources[0].statusText, '失败');
  assert.ok(failedJoinRow.joinSources[0].line.includes('sandbox_failed'),
    failedJoinRow.joinSources[0].line);
  // F04B/T085: an *optional* source's explicit pure-compute failure is converged
  // error data — the run succeeds and the row must say so, not "失败".
  const optionalDefinition = JSON.parse(JSON.stringify(definition));
  for (const node of optionalDefinition.nodes) {
    if (node.type === 'join') {
      node.config.required_sources = ['direct'];
      node.config.optional_sources = ['approve'];
    }
  }
  const converged = runView.buildRunViewModel(
    optionalDefinition, nodeStates, nodeResults,
    {
      'a-choose': { port: 'yes', matched_case: 0 },
      'a-join': {
        results: {
          approve: { status: 'failed', error_code: 'node_output_invalid' },
          direct: { status: 'succeeded', value: { summary: 'ok' } },
        },
      },
    },
    { state: 'succeeded' },
  );
  const convergedRow = converged.rows.find((row) => row.nodeId === 'join');
  const optionalEntry = convergedRow.joinSources.find((entry) => entry.source === 'approve');
  assert.strictEqual(optionalEntry.optional, true);
  assert.strictEqual(optionalEntry.converged, true);
  assert.strictEqual(optionalEntry.statusText, '已收敛的错误');
  assert.ok(optionalEntry.line.includes('node_output_invalid'), optionalEntry.line);
  assert.ok(optionalEntry.line.includes('已收敛'), optionalEntry.line);
  assert.strictEqual(optionalEntry.hasValue, false);
  assert.strictEqual(convergedRow.convergedErrors, 1);
  assert.ok(convergedRow.joinText.includes('approve=已收敛的错误'), convergedRow.joinText);
  assert.ok(convergedRow.joinText.includes('optional'), convergedRow.joinText);
  const requiredEntry = convergedRow.joinSources.find((entry) => entry.source === 'direct');
  assert.strictEqual(requiredEntry.converged, false);
  assert.strictEqual(requiredEntry.statusText, '成功');
  // Without the artifact bytes the port/envelope is simply not claimed.
  const withoutContents = runView.buildRunViewModel(
    definition, nodeStates, nodeResults, {}, { state: 'succeeded' },
  );
  const plainChoose = withoutContents.rows.find((row) => row.nodeId === 'choose');
  assert.strictEqual(plainChoose.branchPort, '');
  assert.strictEqual(plainChoose.branchText, '');
  const plainJoin = withoutContents.rows.find((row) => row.nodeId === 'join');
  assert.deepStrictEqual(plain(plainJoin.joinSources), []);
  assert.strictEqual(plainJoin.joinText, '');
});

// --- formatRunError, cancel intent and paths -------------------------------

test('formatRunError names the stable code and never turns intent into success', () => {
  const runView = loadRunView();
  const failed = runView.formatRunError({ state: 'failed', error_code: 'invalid_activation' });
  assert.strictEqual(failed.code, 'invalid_activation');
  assert.strictEqual(failed.severity, 'bad');
  assert.strictEqual(failed.terminal, true);
  assert.ok(failed.text.includes('invalid_activation'), failed.text);
  assert.ok(failed.hint.length > 0, 'a known code should carry a human hint');
  // An unrecognised code is still shown verbatim; no invented explanation.
  const unknownCode = runView.formatRunError({ state: 'failed', error_code: 'brand_new_code' });
  assert.ok(unknownCode.text.includes('brand_new_code'), unknownCode.text);
  assert.strictEqual(unknownCode.hint, '');
  const noCode = runView.formatRunError({ state: 'failed' });
  assert.ok(noCode.text.includes('未给出'), noCode.text);
  const succeeded = runView.formatRunError({ state: 'succeeded' });
  assert.strictEqual(succeeded.severity, 'ok');
  assert.strictEqual(succeeded.terminal, true);
  assert.ok(succeeded.text.includes('成功'), succeeded.text);
  const cancelled = runView.formatRunError({ state: 'cancelled' });
  assert.strictEqual(cancelled.terminal, true);
  assert.strictEqual(cancelled.severity, '');
  assert.ok(cancelled.text.includes('取消'), cancelled.text);
  const live = runView.formatRunError({ state: 'running' });
  assert.strictEqual(live.terminal, false);
  assert.strictEqual(live.severity, 'pending');
  assert.ok(live.text.includes('running'), live.text);
  const empty = runView.formatRunError(null);
  assert.strictEqual(empty.terminal, false);
  assert.strictEqual(empty.severity, '');
  // A cancel request is only possible before a terminal state is committed.
  assert.strictEqual(runView.runViewCanCancel({ state: 'running' }), true);
  assert.strictEqual(runView.runViewCanCancel({ state: 'queued' }), true);
  assert.strictEqual(runView.runViewCanCancel({ state: 'cancelling' }), true);
  assert.strictEqual(runView.runViewCanCancel({ state: 'succeeded' }), false);
  assert.strictEqual(runView.runViewCanCancel({ state: 'failed' }), false);
  assert.strictEqual(runView.runViewCanCancel(null), false);
  assert.strictEqual(runView.runViewCancelLabel({ state: 'cancelling' }), '再次请求取消（幂等）');
  assert.strictEqual(runView.runViewCancelLabel({ state: 'running' }), '取消 Run');
  // Paths encode their ids and never put a credential in a URL.
  assert.strictEqual(runView.runViewCancelPath('r1'), '/api/v1/runs/r1/cancel');
  assert.strictEqual(runView.runViewNodeResultsPath('r1'), '/api/v1/runs/r1/node-results');
  assert.strictEqual(runView.runViewRunPath('r1'), '/api/v1/runs/r1');
  assert.strictEqual(runView.runViewEventsPath('r1'), '/api/v1/runs/r1/events');
  assert.strictEqual(runView.runViewArtifactContentPath('a b'), '/api/v1/artifacts/a%20b/content');
});

console.log('PASS ' + checks + ' runview tests');
