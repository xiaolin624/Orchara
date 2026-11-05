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

// F04B/T085: an optional source's explicit pure-read/compute failure is folded
// into the join envelope as error data. The run (and the other branches) are not
// failed by it, so the run view names it differently from a failed branch/Run.
const RUNVIEW_CONVERGED_TEXT = '已收敛的错误';

// The runtime brief's stable failure codes, with one honest sentence each. An
// unknown code is still shown verbatim; no explanation is invented for it.
const RUNVIEW_ERROR_HINT = {
  invalid_activation: '运行期没有已激活的入边（条件端口与 join 的 required_sources 不匹配时会出现）；请检查草稿分支与 join 配置。',
  condition_operand_invalid: '条件比较的操作数类型不符（gt/gte/lt/lte 只接受 JSON number）；请改用同类型字面量。',
  node_input_invalid: '节点输入缺失或无法解析；请检查该节点的 inputs 绑定。',
  node_output_invalid: '节点输出未通过 output_schema；请检查节点输出字段与 end 的映射。',
  node_unsupported_model: '本版本执行器尚不支持 model 节点。',
  node_unsupported_retrieve: '本版本执行器尚不支持 retrieve 节点。',
  node_unsupported_approval: '本版本执行器尚不支持 approval 节点。',
  tool_unsupported_graph: '执行器不支持该图（旧线性执行器仍会拒绝 F04B 节点）。',
  invalid_region: '分支/汇合区域不合法；请检查 condition 的 join_id 与 join 的 fork_id 是否配对。',
  cross_region_edge: '存在跨区边；分支内部节点只能连到配对的 join。',
  bad_port: '边引用了条件节点未声明的端口。',
  bad_join_sources: 'join 的 required_sources/optional_sources 与前驱不一致。',
  sandbox_failed: '沙箱执行失败（设施问题）；可重试并查看运行报告。',
  budget_exceeded: '预算已用尽。',
};

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

/** Cap one rendered value; the page keeps the full value behind a details toggle. */
function runViewTruncate(text, limit) {
  const source = String(text === undefined || text === null ? '' : text);
  const cap = Number.isSafeInteger(limit) && limit > 0 ? limit : 120;
  return source.length <= cap ? source : source.slice(0, cap - 1) + '…';
}

function runViewPrettyJSON(value) {
  try {
    return runViewTruncate(JSON.stringify(value, null, 2), 4000);
  } catch (error) {
    return '';
  }
}

/** The condition artifact's `matched_case`: a 0-based index, or the default port. */
function runViewMatchedCaseText(matchedCase) {
  if (matchedCase === 'default') {
    return '默认分支';
  }
  if (typeof matchedCase === 'number' && Number.isSafeInteger(matchedCase) && matchedCase >= 0) {
    return '第 ' + (matchedCase + 1) + ' 个 case';
  }
  if (typeof matchedCase === 'string' && /^\d+$/.test(matchedCase)) {
    return '第 ' + (Number(matchedCase) + 1) + ' 个 case';
  }
  return '匹配信息缺失';
}

/** Fill the condition row from the non-contract `{port, matched_case}` artifact. */
function runViewDescribeCondition(row) {
  if (!row.condition || !row.content || typeof row.content !== 'object' || Array.isArray(row.content)) {
    return;
  }
  const port = typeof row.content.port === 'string' ? row.content.port : '';
  if (port === '') {
    return;
  }
  row.branchPort = port;
  row.matchedCase = row.content.matched_case === undefined ? null : row.content.matched_case;
  row.branchText = '端口 ' + port + '（' + runViewMatchedCaseText(row.content.matched_case) + '）';
}

/**
 * Fill the join row from the COMPILER-CONTRACT envelope
 * `{results: {<source>: {status, value?, error_code?}}}`. Sources keep the
 * definition's required+optional order; an extra envelope key is still shown.
 */
function runViewDescribeJoin(row, config) {
  if (!row.join || !row.content || typeof row.content !== 'object' || Array.isArray(row.content)) {
    return;
  }
  const results = row.content.results;
  if (!results || typeof results !== 'object' || Array.isArray(results)) {
    return;
  }
  const declared = [];
  const optionalSources = [];
  for (const key of ['required_sources', 'optional_sources']) {
    const list = Array.isArray(config[key]) ? config[key] : [];
    for (const value of list) {
      const source = String(value === undefined || value === null ? '' : value);
      if (source === '' || declared.indexOf(source) !== -1) {
        continue;
      }
      declared.push(source);
      if (key === 'optional_sources') {
        optionalSources.push(source);
      }
    }
  }
  const ordered = declared.filter((source) =>
    Object.prototype.hasOwnProperty.call(results, source));
  for (const source of Object.keys(results)) {
    if (ordered.indexOf(source) === -1) {
      ordered.push(source);
    }
  }
  let convergedErrors = 0;
  row.joinSources = ordered.map((source) => {
    const entry = results[source] && typeof results[source] === 'object' ? results[source] : {};
    const status = String(entry.status || '');
    const errorCode = typeof entry.error_code === 'string' ? entry.error_code : '';
    const hasValue = entry.value !== undefined && entry.value !== null;
    let valueSummary = '';
    if (hasValue) {
      try {
        valueSummary = runViewTruncate(JSON.stringify(entry.value), 120);
      } catch (error) {
        valueSummary = '';
      }
    }
    // F04B/T085: an optional source's explicit pure-read/compute failure is
    // converged error data, not a Run failure. It is rendered as "已收敛的错误"
    // so the run view never confuses it with a failed Run/branch.
    const optional = optionalSources.indexOf(source) !== -1;
    const converged = optional && status === 'failed' && errorCode !== '';
    if (converged) {
      convergedErrors += 1;
    }
    const statusText = converged
      ? RUNVIEW_CONVERGED_TEXT
      : (RUNVIEW_STATE_TEXT[status] || status || '未知');
    return {
      source: source,
      optional: optional,
      converged: converged,
      status: status,
      statusText: statusText,
      hasValue: hasValue,
      valueSummary: valueSummary,
      valueText: hasValue ? runViewPrettyJSON(entry.value) : '',
      errorCode: errorCode,
      line:
        source + '=' + statusText + (errorCode === '' ? '' : '（' + errorCode + '）') +
        (hasValue ? ' · ' + valueSummary : '') +
        (converged ? ' · optional 纯读/计算错误已收敛为错误数据，Run 不受影响' : ''),
    };
  });
  row.convergedErrors = convergedErrors;
  row.joinText =
    '汇合 ' + row.joinSources.length + ' 个来源 · ' +
    row.joinSources
      .map((source) => source.source + '=' + source.statusText + (source.errorCode === '' ? '' : '(' + source.errorCode + ')'))
      .join('、') +
    (convergedErrors > 0 ? '；其中 ' + convergedErrors + ' 个 optional 分支错误已收敛' : '');
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
      convergedErrors: 0,
      artifactId: dataRow ? String(dataRow.artifact_id || '') : '',
      artifactKind: dataRow ? String(dataRow.kind || '') : '',
      mediaType: dataRow ? String(dataRow.media_type || '') : '',
      content: dataRow ? contents[String(dataRow.artifact_id || '')] : undefined,
      classNames: ['run-node-row', 'state-' + state].concat(inferred ? ['inferred'] : []),
    };
    runViewDescribeCondition(row);
    runViewDescribeJoin(row, config);
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

/**
 * One honest sentence for the Run's state and stable error code.
 *
 * "Accepted" (202 cancel) is never "stopped"; a non-terminal state is never
 * success. `hint` is only present for a code the runtime brief documents.
 */
function formatRunError(run) {
  const value = run && typeof run === 'object' ? run : null;
  const state = value && typeof value.state === 'string' ? value.state : '';
  const code = value && typeof value.error_code === 'string' ? value.error_code : '';
  const terminal = RUNVIEW_TERMINAL_STATES[state] === true;
  if (!value) {
    return {
      code: '',
      state: '',
      terminal: false,
      severity: '',
      hint: '',
      text: '（尚未收到 Run 快照；页面不会在没有服务端事实时显示成功或失败。）',
    };
  }
  if (state === 'succeeded') {
    return {
      code: code,
      state: state,
      terminal: true,
      severity: 'ok',
      hint: '',
      text: '运行成功（succeeded）；节点状态以已提交的 node.status 事件为准。',
    };
  }
  if (state === 'failed') {
    return {
      code: code,
      state: state,
      terminal: true,
      severity: 'bad',
      hint: code === '' ? '' : RUNVIEW_ERROR_HINT[code] || '',
      text:
        code === ''
          ? '运行失败（failed），服务端未给出 error_code；请查看运行报告与失败节点。'
          : '运行失败（failed），稳定错误码 ' + code + '。',
    };
  }
  if (state === 'cancelled') {
    return {
      code: code,
      state: state,
      terminal: true,
      severity: '',
      hint: '',
      text: '运行已取消（cancelled）：平台已确认受控执行单元停止；这不表示撤回已经发送的外部写入。',
    };
  }
  if (state === 'recovery_pending') {
    return {
      code: code,
      state: state,
      terminal: false,
      severity: 'pending',
      hint: '',
      text: 'Run 挂起在 recovery_pending（已确认退出但没有结果）；恢复证据齐全前不会重新执行。',
    };
  }
  if (state === 'needs_reconciliation') {
    return {
      code: code,
      state: state,
      terminal: false,
      severity: 'pending',
      hint: '',
      text: 'Run 等待人工核对（needs_reconciliation）；不显示为成功或失败。',
    };
  }
  if (state === 'cancelling') {
    return {
      code: code,
      state: state,
      terminal: false,
      severity: 'pending',
      hint: '',
      text: '已收到取消意图（cancelling），等待执行单元退出并提交终态。',
    };
  }
  if (state === 'running' || state === 'queued' || state === 'waiting_approval' ||
      state === 'waiting_children') {
    return {
      code: code,
      state: state,
      terminal: false,
      severity: 'pending',
      hint: '',
      text: 'Run 仍在进行（state=' + state + '）；页面会继续从服务端事实刷新。',
    };
  }
  return {
    code: code,
    state: state,
    terminal: false,
    severity: '',
    hint: '',
    text: '（Run 快照没有可识别的 state；页面不推断状态。）',
  };
}

/** A Run can be cancelled until its terminal state is committed. */
function runViewCanCancel(run) {
  if (!run || typeof run.state !== 'string' || run.state === '') {
    return false;
  }
  return RUNVIEW_TERMINAL_STATES[run.state] !== true;
}

/** Cancelling is idempotent, so a second request is allowed and labelled. */
function runViewCancelLabel(run) {
  return String((run && run.state) || '') === 'cancelling'
    ? '再次请求取消（幂等）'
    : '取消 Run';
}

/** Record one cancel intent. A 202 only records intent; it is never "stopped". */
async function runViewRequestCancel(runId) {
  const token = await csrf();
  const result = await apiFull(runViewCancelPath(runId), { method: 'POST', csrf: token });
  return result.payload;
}

/** Read one data artifact as JSON (owner-only contract content route). */
async function runViewFetchArtifactContent(artifactID) {
  const response = await fetch(runViewArtifactContentPath(artifactID), {
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
  });
  const text = await response.text();
  if (!response.ok) {
    const failure = new Error('HTTP ' + response.status);
    failure.code = 'http_' + response.status;
    failure.status = response.status;
    throw failure;
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    return text;
  }
}

// ---------------------------------------------------------------------------
// DOM wiring (not part of the pure-function tests; the pytest page harness
// drives these bytes with a document shim).
// ---------------------------------------------------------------------------

const RUNVIEW_POLL_INTERVAL_MS = 700;
const RUNVIEW_MAX_POLLS = 600;

let activeRunView = null;

function runViewElement(id) {
  if (typeof document === 'undefined' || !document || typeof document.getElementById !== 'function') {
    return null;
  }
  return document.getElementById(id);
}

function runViewStatus(message, kind) {
  const element = runViewElement('runview-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function runViewCancelStatus(message, kind) {
  const element = runViewElement('runview-cancel-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

/** One table cell; textContent only, so a node id can never inject markup. */
function runViewCell(text, className) {
  const cell = document.createElement('td');
  cell.textContent = text === undefined || text === null ? '' : String(text);
  if (className) {
    cell.className = className;
  }
  return cell;
}

/** The condition port / join-source cell of one row. */
function runViewBranchCell(details) {
  const cell = document.createElement('td');
  cell.className = 'run-node-branch';
  if (details.branchText !== '') {
    const port = document.createElement('span');
    port.className = 'node-port';
    port.textContent = details.branchText;
    cell.appendChild(port);
  }
  if (details.joinSources.length > 0) {
    const summary = document.createElement('p');
    summary.className = 'hint';
    summary.textContent = details.joinText;
    cell.appendChild(summary);
    for (const source of details.joinSources) {
      const line = document.createElement('p');
      // `state-converged` marks an optional branch's folded error as error data,
      // visually distinct from a failed branch or a failed Run.
      line.className = 'run-node-join-source state-' + (source.converged ? 'converged' : source.status);
      line.textContent = source.line;
      cell.appendChild(line);
      if (source.hasValue && source.valueText !== '') {
        const expandable = document.createElement('details');
        const toggle = document.createElement('summary');
        toggle.textContent = '查看 ' + source.source + ' 的值（截断展示，完整值以 artifact 为准）';
        const pre = document.createElement('pre');
        pre.className = 'hint';
        pre.textContent = source.valueText;
        expandable.appendChild(toggle);
        expandable.appendChild(pre);
        cell.appendChild(expandable);
      }
    }
  }
  if (details.branchText === '' && details.joinSources.length === 0) {
    cell.textContent = '';
  }
  return cell;
}

function runViewRowElement(row) {
  const tr = document.createElement('tr');
  tr.className = row.classNames.join(' ');
  tr.setAttribute('data-node-id', row.nodeId);
  tr.setAttribute('data-state', row.state);
  const name = document.createElement('td');
  const code = document.createElement('code');
  code.textContent = row.nodeId;
  name.appendChild(code);
  if (row.label !== '') {
    const label = document.createElement('p');
    label.className = 'hint';
    label.textContent = row.label;
    name.appendChild(label);
  }
  const badge = document.createElement('span');
  badge.className = 'node-state state-' + row.state;
  badge.textContent = row.stateText;
  const stateCell = document.createElement('td');
  stateCell.appendChild(badge);
  const attemptCell = runViewCell(row.attemptText, 'run-node-attempt');
  const errorCell = document.createElement('td');
  errorCell.className = 'run-node-error';
  errorCell.textContent = row.errorCode;
  tr.appendChild(name);
  tr.appendChild(runViewCell(row.typeText, 'run-node-type'));
  tr.appendChild(stateCell);
  tr.appendChild(attemptCell);
  tr.appendChild(runViewBranchCell(row));
  tr.appendChild(errorCell);
  return tr;
}

/**
 * Render the current view model: the table, the Run state/error sentence and the
 * cancel button. Every value comes from buildRunViewModel, so this function adds
 * no independent truth.
 */
function renderRunView(view) {
  if (!view || activeRunView !== view) {
    return null;
  }
  const model = buildRunViewModel(
    view.definition,
    view.nodeStates,
    view.nodeResults,
    view.artifactContents,
    view.run,
  );
  view.model = model;
  const container = runViewElement('run-node-rows');
  if (container && typeof container.replaceChildren === 'function') {
    container.replaceChildren();
    for (const row of model.rows) {
      container.appendChild(runViewRowElement(row));
    }
  }
  const stateLine = runViewElement('runview-run-state');
  if (stateLine) {
    stateLine.textContent =
      'Run state：' + (model.runState || '（未知）') +
      (model.runErrorCode === '' ? '' : ' · error_code=' + model.runErrorCode);
  }
  const summary = runViewElement('runview-summary');
  if (summary) {
    summary.textContent = model.text;
  }
  const error = formatRunError(view.run);
  const errorElement = runViewElement('runview-error');
  if (errorElement) {
    errorElement.textContent = error.text + (error.hint === '' ? '' : ' ' + error.hint);
    errorElement.className = 'status ' + error.severity;
  }
  const cancelButton = runViewElement('runview-cancel');
  if (cancelButton) {
    cancelButton.textContent = runViewCancelLabel(view.run);
    cancelButton.disabled = !runViewCanCancel(view.run);
  }
  return model;
}

function stopRunView() {
  if (activeRunView) {
    activeRunView.stopped = true;
    if (activeRunView.timer !== null) {
      clearTimeout(activeRunView.timer);
      activeRunView.timer = null;
    }
  }
  activeRunView = null;
}

async function loadRunViewDefinition(view) {
  if (activeRunView !== view || view.stopped || view.applicationID === '') {
    return;
  }
  try {
    const draft = await api('/api/v1/applications/' + encodeURIComponent(view.applicationID) + '/draft');
    view.definition = draft && draft.definition && typeof draft.definition === 'object'
      ? draft.definition
      : null;
    runViewStatus('已加载当前草稿定义；节点表按定义顺序显示。', 'ok');
  } catch (error) {
    view.definitionError = error && error.code ? error.code : 'error';
    runViewStatus(
      '草稿定义不可用 [' + view.definitionError + ']；节点表只显示服务端返回的节点事实。',
      'pending',
    );
  }
  renderRunView(view);
}

/** Fetch the data artifact content of condition/join nodes, at most once each. */
async function refreshRunViewArtifacts(view) {
  const definition = view.definition;
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const byID = {};
  for (const node of nodes) {
    if (node && (node.type === 'condition' || node.type === 'join')) {
      byID[String(node.id || '')] = String(node.type);
    }
  }
  if (Object.keys(byID).length === 0) {
    return;
  }
  const items = view.nodeResults && Array.isArray(view.nodeResults.items) ? view.nodeResults.items : [];
  for (const item of items) {
    if (!item || item.kind !== 'data') {
      continue;
    }
    const nodeID = String(item.node_id || '');
    const artifactID = String(item.artifact_id || '');
    if (
      !Object.prototype.hasOwnProperty.call(byID, nodeID) ||
      artifactID === '' ||
      view.requestedArtifacts[artifactID]
    ) {
      continue;
    }
    view.requestedArtifacts[artifactID] = true;
    try {
      view.artifactContents[artifactID] = await runViewFetchArtifactContent(artifactID);
    } catch (error) {
      view.artifactErrors[artifactID] = error && error.code ? error.code : 'error';
    }
  }
}

/** One poll: Run snapshot + durable node results + condition/join artifact bytes. */
async function runViewTick(view) {
  if (activeRunView !== view || view.stopped) {
    return;
  }
  view.polls += 1;
  let failure = '';
  try {
    view.run = await api(runViewRunPath(view.runID));
  } catch (error) {
    if (error && error.status === 401) {
      window.location.assign('/login');
      return;
    }
    if (error && error.status === 404) {
      runViewStatus('该 Run 不存在或不属于当前账户（404）；节点表已停止刷新。', 'bad');
      return;
    }
    failure = (error && error.code ? error.code : 'error') + ' ' + (error && error.message ? error.message : '');
  }
  try {
    view.nodeResults = await api(runViewNodeResultsPath(view.runID));
  } catch (error) {
    if (error && error.status === 401) {
      window.location.assign('/login');
      return;
    }
    view.nodeResultsError = error && error.code ? error.code : 'error';
  }
  await refreshRunViewArtifacts(view);
  if (activeRunView !== view || view.stopped) {
    return;
  }
  const model = renderRunView(view);
  if (failure !== '') {
    runViewStatus('刷新 Run 失败 [' + failure + ']；将重试。', 'pending');
  } else if (model && model.runTerminal) {
    // One extra durable sweep already happened in this tick: the executor
    // registers node results before the Run's terminal state is committed.
    runViewStatus('Run 已进入终态 ' + (model.runState || '') + '；节点状态以已提交事实为准。', 'ok');
    view.stopped = true;
    return;
  } else if (model) {
    runViewStatus(
      '正在跟踪 Run（第 ' + view.polls + ' 次刷新）；' + model.text +
      '。条件端口/join 结果来自 data artifact。',
      'pending',
    );
  }
  if (view.polls >= RUNVIEW_MAX_POLLS) {
    runViewStatus('已停止刷新（' + view.polls + ' 次）；Run 仍未进入终态。', 'pending');
    return;
  }
  view.timer = setTimeout(() => {
    void runViewTick(view);
  }, RUNVIEW_POLL_INTERVAL_MS);
}

/**
 * Start the node-state view for one Run. `options` = `{runID, applicationID,
 * draftRevision, run}`. The view polls the Run snapshot and the owner-only node
 * results; SSE `node.status` events are fed in through runViewOnEvent, which the
 * page wires as the timeline's onEvent callback.
 */
function beginRunView(options) {
  const opts = options && typeof options === 'object' ? options : {};
  const runID = String(opts.runID || '');
  if (runID === '') {
    return null;
  }
  stopRunView();
  const view = {
    runID: runID,
    applicationID: String(opts.applicationID || ''),
    draftRevision: opts.draftRevision === undefined || opts.draftRevision === null
      ? null
      : opts.draftRevision,
    definition: null,
    definitionError: '',
    events: [],
    nodeStates: {},
    nodeResults: null,
    nodeResultsError: '',
    artifactContents: {},
    artifactErrors: {},
    requestedArtifacts: {},
    run: opts.run && typeof opts.run === 'object' ? opts.run : null,
    model: null,
    polls: 0,
    timer: null,
    stopped: false,
  };
  activeRunView = view;
  const panel = runViewElement('runview-panel');
  if (panel) {
    panel.hidden = false;
  }
  const container = runViewElement('run-node-rows');
  if (container && typeof container.replaceChildren === 'function') {
    container.replaceChildren();
  }
  runViewCancelStatus('', '');
  runViewStatus('正在加载节点状态…', 'pending');
  renderRunView(view);
  void loadRunViewDefinition(view);
  void runViewTick(view);
  return view;
}

/**
 * Feed one committed SSE event (the timeline's deduped list) into the active
 * view. A `run.status` event updates the Run state/error code without waiting for
 * the next poll; node.status events rebuild the state map by aggregation, so a
 * replayed frame cannot change what the table shows.
 */
function runViewOnEvent(events, event) {
  const view = activeRunView;
  if (!view || view.stopped) {
    return null;
  }
  if (Array.isArray(events)) {
    view.events = events;
  } else if (event && typeof event === 'object') {
    view.events = view.events.concat([event]);
  }
  view.nodeStates = aggregateNodeStates(view.events);
  if (event && event.type === 'run.status' && event.payload && typeof event.payload === 'object') {
    const payload = event.payload;
    const next = Object.assign({}, view.run && typeof view.run === 'object' ? view.run : {});
    if (typeof payload.state === 'string' && payload.state !== '') {
      next.state = payload.state;
    }
    if (typeof payload.error_code === 'string' && payload.error_code !== '') {
      next.error_code = payload.error_code;
    }
    view.run = next;
  }
  return renderRunView(view);
}

/** Record one cancel intent for the active Run and refresh the panel honestly. */
async function runViewCancel() {
  const view = activeRunView;
  if (!view) {
    return null;
  }
  const button = runViewElement('runview-cancel');
  if (button) {
    button.disabled = true;
  }
  runViewCancelStatus('正在请求取消（202 只表示意图，终态由 finalizer 提交）…', 'pending');
  try {
    const run = await runViewRequestCancel(view.runID);
    view.run = run && typeof run === 'object' ? run : view.run;
    renderRunView(view);
    runViewCancelStatus(
      '已接受取消意图（state=' + String((view.run && view.run.state) || '') + '）；页面会刷新到终态。',
      'ok',
    );
    return run;
  } catch (error) {
    if (error && error.status === 401) {
      window.location.assign('/login');
      return null;
    }
    runViewCancelStatus(
      '取消失败 [' + (error && error.code ? error.code : 'error') + '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
    if (button) {
      button.disabled = false;
    }
    return null;
  }
}

if (typeof document !== 'undefined' && document.getElementById) {
  const cancelButton = runViewElement('runview-cancel');
  if (cancelButton && typeof cancelButton.addEventListener === 'function') {
    cancelButton.addEventListener('click', () => {
      void runViewCancel();
    });
  }
}


