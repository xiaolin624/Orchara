/*
 * F04B/T083 run view: the node-state table of a debug Run.
 *
 * This is a classic browser script (no build step, no module loader): the debug
 * page loads it after `app.js` and before `debug.js`. The pure functions at the
 * top are also executed by `tests/f04b/runview.test.mjs` under Node with a `vm`
 * context, so the shipped bytes are what the tests assert.
 *
 * Data surface (documented in reports/f04b/T083-runview.md; **not** a contract
 * change):
 *
 *   - contract SSE `node.status` events: `{node_id, state, attempt_no}` with
 *     state in pending/running/succeeded/failed/skipped (waiting is reserved for
 *     T087). `skipped` is an unselected branch, never a failure.
 *   - non-contract `GET /api/v1/runs/{id}/node-results` (owner-only) points at
 *     the `kind="data"` artifact each node registered.
 *   - the condition node's data artifact is the non-contract convention
 *     `{"port": "yes|no|...", "matched_case": 0|1|...|"default"}`.
 *   - the join node's data artifact is the COMPILER-CONTRACT envelope
 *     `{"results": {"<source>": {"status": ..., "value": {...}, "error_code": ...}}}`.
 *   - the artifact bytes are read through the contract route
 *     `GET /api/v1/artifacts/{id}/content` (owner-only).
 *   - the run's terminal state and `error_code` come from `GET /api/v1/runs/{id}`.
 *
 * Nothing here invents progress: a state is only rendered from a committed event,
 * from a committed Run terminal state, or is labelled as inferred from the
 * registered node results (the SSE replay window can be gone: 410).
 */

// State ranks inside one attempt: a later -- or replayed -- frame must never
// demote a committed fact, and a fail-fast failure outranks a success.
const RUNVIEW_STATE_RANK = {
  pending: 1,
  waiting: 2,
  running: 3,
  skipped: 4,
  succeeded: 5,
  failed: 6,
};

const RUNVIEW_STATE_TEXT = {
  pending: '等待',
  waiting: '等待中',
  running: '运行中',
  skipped: '已跳过',
  succeeded: '成功',
  failed: '失败',
  unknown: '无状态事件',
};

const RUNVIEW_TYPE_TEXT = {
  start: '开始',
  end: '结束',
  python: 'Python',
  agent: 'Agent',
  call_app: '调用应用',
  condition: '条件',
  join: '汇合',
  approval: '审批',
  model: '模型',
  retrieve: '检索',
};

const RUNVIEW_TERMINAL_STATES = { succeeded: true, failed: true, cancelled: true };

/**
 * Fold committed `node.status` events into `{node_id: {state, attempt_no}}`.
 *
 * Events may be out of order and repeated (a reconnect with Last-Event-ID
 * replays frames the page already applied), so the fold is by attempt first and
 * by state rank second, not "last write wins". An unknown state, a missing node
 * id or a non-node.status event is ignored: the page only renders facts the
 * server committed.
 */
function aggregateNodeStates(events) {
  const aggregated = {};
  const source = Array.isArray(events) ? events : [];
  for (const event of source) {
    if (!event || typeof event !== 'object' || event.type !== 'node.status') {
      continue;
    }
    const payload = event.payload;
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      continue;
    }
    const nodeID = String(payload.node_id || '');
    const state = String(payload.state || '');
    if (nodeID === '' || !Object.prototype.hasOwnProperty.call(RUNVIEW_STATE_RANK, state)) {
      continue;
    }
    const attemptNo = Number(payload.attempt_no);
    const attempt = Number.isSafeInteger(attemptNo) && attemptNo >= 0 ? attemptNo : 0;
    // The contract node.status payload has no error_code; a future extension is
    // only meaningful for a failed node, so a code on any other state is dropped.
    const errorCode =
      state === 'failed' && typeof payload.error_code === 'string' && payload.error_code !== ''
        ? payload.error_code
        : '';
    const current = aggregated[nodeID];
    if (!current) {
      aggregated[nodeID] = { state: state, attempt_no: attempt };
      if (errorCode !== '') {
        aggregated[nodeID].error_code = errorCode;
      }
      continue;
    }
    if (attempt > current.attempt_no) {
      // A new attempt supersedes the previous attempt entirely: its outcome and
      // error code are no longer this node's fact.
      aggregated[nodeID] = { state: state, attempt_no: attempt };
      if (errorCode !== '') {
        aggregated[nodeID].error_code = errorCode;
      }
      continue;
    }
    if (attempt === current.attempt_no && RUNVIEW_STATE_RANK[state] > RUNVIEW_STATE_RANK[current.state]) {
      current.state = state;
    }
    if (errorCode !== '' && !current.error_code) {
      current.error_code = errorCode;
    }
  }
  return aggregated;
}

/**
 * Definition order is not execution order: a saved draft may list nodes in any
 * order (the P05 `linear-sales-nodes-shuffled` case). Kahn's algorithm with the
 * definition index as the tie-break yields a stable order in which no node is
 * rendered before a predecessor. A cyclic or partial definition falls back to
 * the remaining definition order instead of dropping nodes.
 */
function runViewOrderedNodeIDs(nodes, edges) {
  const ids = [];
  const seen = {};
  for (const node of nodes) {
    const id = String((node && node.id) || '');
    if (id !== '' && !seen[id]) {
      seen[id] = true;
      ids.push(id);
    }
  }
  const index = new Map(ids.map((id, position) => [id, position]));
  const indegree = new Map(ids.map((id) => [id, 0]));
  const outgoing = new Map(ids.map((id) => [id, []]));
  for (const edge of Array.isArray(edges) ? edges : []) {
    const source = String((edge && edge.source) || '');
    const target = String((edge && edge.target) || '');
    if (source === target || !index.has(source) || !index.has(target)) {
      continue;
    }
    outgoing.get(source).push(target);
    indegree.set(target, indegree.get(target) + 1);
  }
  const ordered = [];
  const ready = ids.filter((id) => indegree.get(id) === 0);
  while (ready.length > 0) {
    ready.sort((left, right) => index.get(left) - index.get(right));
    const id = ready.shift();
    ordered.push(id);
    for (const next of outgoing.get(id)) {
      const remaining = indegree.get(next) - 1;
      indegree.set(next, remaining);
      if (remaining === 0) {
        ready.push(next);
      }
    }
  }
  for (const id of ids) {
    if (ordered.indexOf(id) === -1) {
      ordered.push(id);
    }
  }
  return ordered;
}

/** The first `kind="data"` node-result row per node (the artifact to read). */
function runViewDataRowsByNode(nodeResults) {
  const items = nodeResults && Array.isArray(nodeResults.items) ? nodeResults.items : [];
  const byNode = {};
  for (const item of items) {
    if (!item || typeof item !== 'object' || item.kind !== 'data') {
      continue;
    }
    const nodeID = String(item.node_id || '');
    if (nodeID === '' || byNode[nodeID]) {
      continue;
    }
    byNode[nodeID] = item;
  }
  return byNode;
}

function runViewTypeText(type, inDefinition) {
  if (inDefinition === false) {
    return '（定义中不存在）';
  }
  const text = RUNVIEW_TYPE_TEXT[type];
  return text || String(type || '未知');
}

/**
 * Build the ordered node-state rows of a Run.
 *
 * `definition` is a draft definition (`{nodes, edges}`), `nodeStates` comes from
 * aggregateNodeStates, `nodeResults` is the owner-only node-results projection,
 * `artifactContents` maps an artifact id to already-fetched parsed JSON, and
 * `run` is the contract Run snapshot. Nothing is invented: when there is no
 * state event and no registered result, the row is `unknown` on a terminal run,
 * and `pending` (not success) while the Run is still live.
 */
function buildRunViewModel(definition, nodeStates, nodeResults, artifactContents, run) {
  const source = definition && typeof definition === 'object' ? definition : {};
  const nodes = Array.isArray(source.nodes)
    ? source.nodes.filter((node) => node && typeof node === 'object')
    : [];
  const known = nodeStates && typeof nodeStates === 'object' ? nodeStates : {};
  const contents = artifactContents && typeof artifactContents === 'object' ? artifactContents : {};
  const dataRows = runViewDataRowsByNode(nodeResults);
  const runValue = run && typeof run === 'object' ? run : {};
  const runState = String(runValue.state || '');
  const runErrorCode = String(runValue.error_code || '');
  const runTerminal = RUNVIEW_TERMINAL_STATES[runState] === true;

  const orderedIDs = runViewOrderedNodeIDs(nodes, source.edges);
  const definitionByID = {};
  for (const node of nodes) {
    const id = String(node.id || '');
    if (id !== '' && !definitionByID[id]) {
      definitionByID[id] = node;
    }
  }
  const extraIDs = [];
  for (const nodeID of Object.keys(known)) {
    if (!Object.prototype.hasOwnProperty.call(definitionByID, nodeID)) {
      extraIDs.push(nodeID);
    }
  }
  for (const nodeID of Object.keys(dataRows)) {
    if (!Object.prototype.hasOwnProperty.call(definitionByID, nodeID) && extraIDs.indexOf(nodeID) === -1) {
      extraIDs.push(nodeID);
    }
  }

  const rows = [];
  for (const nodeID of orderedIDs.concat(extraIDs)) {
    const node = definitionByID[nodeID] || null;
    const type = node ? String(node.type || '') : '';
    const config = node && node.config && typeof node.config === 'object' ? node.config : {};
    const label = typeof config.title === 'string' ? config.title : '';
    const stateEntry = known[nodeID] && typeof known[nodeID] === 'object' ? known[nodeID] : null;
    const dataRow = dataRows[nodeID] || null;
    let state;
    let inferred = false;
    if (stateEntry) {
      state = String(stateEntry.state || 'unknown');
    } else if (dataRow) {
      // The server only registers a node result after the node produced it; the
      // page labels this as inference because no event survived the replay window.
      state = 'succeeded';
      inferred = true;
    } else if (runTerminal) {
      state = 'unknown';
    } else {
      state = 'pending';
    }
    const observed = Boolean(stateEntry);
    const attemptNo = stateEntry && Number.isSafeInteger(Number(stateEntry.attempt_no))
      ? Number(stateEntry.attempt_no)
      : null;
    let errorCode = '';
    if (stateEntry && typeof stateEntry.error_code === 'string' && stateEntry.error_code !== '') {
      errorCode = stateEntry.error_code;
    } else if (state === 'failed' && runState === 'failed' && runErrorCode !== '') {
      // node.status carries no error_code (contract gap), so a failed node can
      // only show the Run's stable error code.
      errorCode = runErrorCode;
    }
    const stateText = inferred
      ? (RUNVIEW_STATE_TEXT[state] || state) + '（由节点结果推断）'
      : RUNVIEW_STATE_TEXT[state] || state;
    const row = {
      nodeId: nodeID,
      type: type,
      typeText: runViewTypeText(type, Boolean(node)),
      label: label,
      inDefinition: Boolean(node),
      state: state,
      stateText: stateText,
      observed: observed,
      inferred: inferred,
      attemptNo: attemptNo,
      attemptText: attemptNo === null ? '' : String(attemptNo),
      errorCode: errorCode,
      errorText: errorCode === '' ? '' : '稳定错误码 ' + errorCode,
      skipped: state === 'skipped',
      failed: state === 'failed',
      condition: type === 'condition',
      branchPort: '',
      matchedCase: null,
      branchText: '',
      join: type === 'join',
      joinSources: [],
      joinText: '',
      artifactId: dataRow ? String(dataRow.artifact_id || '') : '',
      artifactKind: dataRow ? String(dataRow.kind || '') : '',
      mediaType: dataRow ? String(dataRow.media_type || '') : '',
      content: dataRow ? contents[String(dataRow.artifact_id || '')] : undefined,
      classNames: ['run-node-row', 'state-' + state].concat(inferred ? ['inferred'] : []),
    };
    rows.push(row);
  }

  const counts = {
    total: rows.length,
    pending: 0,
    waiting: 0,
    running: 0,
    succeeded: 0,
    failed: 0,
    skipped: 0,
    unknown: 0,
  };
  for (const row of rows) {
    if (Object.prototype.hasOwnProperty.call(counts, row.state)) {
      counts[row.state] += 1;
    } else {
      counts.unknown += 1;
    }
  }
  const text =
    counts.total +
    ' 个节点 · 成功 ' +
    counts.succeeded +
    ' · 运行中 ' +
    counts.running +
    ' · 跳过 ' +
    counts.skipped +
    ' · 失败 ' +
    counts.failed +
    (counts.unknown === 0 ? '' : ' · 无状态事件 ' + counts.unknown);
  return {
    visible: true,
    empty: counts.total === 0,
    rows: rows,
    counts: counts,
    runState: runState,
    runErrorCode: runErrorCode,
    runTerminal: runTerminal,
    text: text,
    sourceText:
      '节点状态来自已提交的 node.status 事件；条件端口与 join 结果来自节点 data artifact'
      + '（非契约数据面，owner 可见）。',
  };
}

function runViewNodeResultsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/node-results';
}

function runViewArtifactContentPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID || '')) + '/content';
}

function runViewRunPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || ''));
}

function runViewCancelPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/cancel';
}

function runViewEventsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/events';
}
