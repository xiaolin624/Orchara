/*
 * F03/T029 runs page.
 *
 * Real clients of two contract endpoints:
 *
 *   GET /api/v1/runs            -> {items: Run[], next_cursor}
 *   GET /api/v1/runs/{id}       -> Run (+ X-Manifest-SHA256, and while an attempt
 *                                  is live X-Run-Attempt-Started-At /
 *                                  X-Run-Lease-Deadline)
 *
 * What the page shows, and what it deliberately does not:
 *
 *   - The list is owner-only, with the server's opaque cursor. An administrator
 *     sees only its own runs: there is no superuser read path.
 *   - state/phase/kind/revision/execution_snapshot_id come from the contract Run.
 *   - While running, the page shows the *real* lease facts the server read from
 *     PostgreSQL. It never renders a percentage, a node progress bar or an
 *     estimated finish time: claim/heartbeat/exit belong to the internal
 *     supervisor protocol (F03), and the public surface has no such data.
 *   - Every value is written with textContent; no server string becomes markup.
 *
 * The pure helpers are exported on purpose: the Node test executes them with a
 * stubbed fetch, so the page's request/format logic is observed behaviour rather
 * than source text. The DOM wiring stays at the bottom.
 */

/** The detail path for one run id. */
function runDetailPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || ''));
}

/** The durable event-stream path for one run id (T032). */
function runEventsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/events';
}

/** The list path, including only the query parameters that were supplied. */
function listRunsPath(cursor, limit) {
  const query = [];
  if (cursor !== undefined && cursor !== null && String(cursor) !== '') {
    query.push('cursor=' + encodeURIComponent(String(cursor)));
  }
  if (limit !== undefined && limit !== null && String(limit) !== '') {
    query.push('limit=' + encodeURIComponent(String(limit)));
  }
  return '/api/v1/runs' + (query.length ? '?' + query.join('&') : '');
}

/** The contract Run fields the page uses; never the raw body. */
function runSummary(run) {
  const value = run || {};
  return {
    id: value.id || '',
    state: value.state || 'unknown',
    phase: value.phase || '',
    kind: value.kind || '',
    revision: value.revision === undefined || value.revision === null ? '' : String(value.revision),
    executionSnapshotID: value.execution_snapshot_id || '',
  };
}

/** Read the non-contract headers the run detail carries (never a token). */
function runRuntimeFacts(headers) {
  if (!headers || typeof headers.get !== 'function') {
    return { manifestSHA256: '', startedAt: '', leaseDeadline: '' };
  }
  return {
    manifestSHA256: headers.get('X-Manifest-SHA256') || '',
    startedAt: headers.get('X-Run-Attempt-Started-At') || '',
    leaseDeadline: headers.get('X-Run-Lease-Deadline') || '',
  };
}

/**
 * One honest sentence about a run. Terminal states say so; a queued run says it
 * has not been claimed; a running run quotes the lease when the server supplied
 * it and otherwise says the lease facts are unavailable instead of inventing
 * progress.
 *
 * T031 adds the finalizing stage: a worker result is only a proposal, so while
 * phase=finalizing the page says the platform is waiting for supervisor exit
 * evidence and that this is not a success yet. Success is only ever rendered
 * from a committed terminal state.
 */
function describeRunState(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  if (isRunTerminal(summary.state)) {
    return '已进入终态 ' + summary.state + '（终态由 Go finalizer 在结果提议与 supervisor 退出证据齐全后提交）。';
  }
  if (summary.state === 'queued') {
    return '尚未被领取（queued）；领取、心跳与退出证据属于 F03 内部 supervisor 协议。';
  }
  if (summary.state === 'recovery_pending') {
    return '已确认执行单元退出但没有结果，Run 挂起在 recovery_pending；在恢复证据齐全前不会重新执行，也不会显示成功。';
  }
  if (summary.state === 'needs_reconciliation') {
    return 'Run 等待人工核对（needs_reconciliation）：存在未明的外部副作用，平台不把它显示为成功或失败。';
  }
  if (summary.state === 'running' && summary.phase === 'finalizing') {
    return '结果提议已保存，正在 finalizing：仍在等待 supervisor 的退出证据（进程/沙箱/数据库排空）；终态提交前这不是成功。';
  }
  if (summary.state === 'running') {
    const lease = (runtime.leaseDeadline || '').trim();
    if (!lease) {
      return '正在执行（state=running，phase=' + (summary.phase || 'unknown') +
        '）。服务器没有返回租约事实，本页不伪造进度。';
    }
    const started = (runtime.startedAt || '').trim();
    return '已领取' + (started ? '（attempt started_at ' + started + '）' : '') +
      '，当前租约至 ' + lease + '（由数据库时钟判定并随心跳续租）。';
  }
  return '状态 ' + summary.state + '（phase=' + (summary.phase || 'unknown') + '）。';
}

/** The three execution stages the page observes, never reordered. */
function runLifecycle(run) {
  const summary = runSummary(run);
  let current = 'executing';
  if (summary.phase === 'finalizing') {
    current = 'finalizing';
  }
  if (isRunTerminal(summary.state)) {
    current = 'terminal';
  }
  return { stages: ['executing', 'finalizing', 'terminal'], current: current };
}

/** One line for the lifecycle row: executing -> finalizing -> terminal. */
function lifecycleText(run) {
  const lifecycle = runLifecycle(run);
  return lifecycle.stages.join(' → ') + '（当前：' + lifecycle.current + '）';
}

function isRunTerminal(state) {
  return state === 'succeeded' || state === 'failed' || state === 'cancelled';
}

/**
 * Poll the owner-only detail until the Run is terminal. A finalizing run is
 * still non-terminal: the page keeps asking GET /runs/{id} rather than guessing
 * the outcome, so success can never be rendered before the committed state.
 */
function shouldPollRun(run) {
  if (!run || typeof run.state !== 'string') {
    return false;
  }
  return !isRunTerminal(run.state);
}

// ---------------------------------------------------------------------------
// T032: durable event timeline + SSE client (pure helpers)
// ---------------------------------------------------------------------------

/**
 * Parse one SSE block (already split on the blank line). Unknown fields are
 * ignored; a comment frame (`: heartbeat`) carries no id/event/data and is not
 * a business event.
 */
function parseSSEFrame(lines) {
  const frame = { id: '', event: '', data: '', comment: '' };
  const data = [];
  for (const line of lines) {
    if (line === '') {
      continue;
    }
    if (line.charAt(0) === ':') {
      frame.comment = line.slice(1).trim();
      continue;
    }
    const colon = line.indexOf(':');
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? '' : line.slice(colon + 1);
    if (value.charAt(0) === ' ') {
      value = value.slice(1);
    }
    if (field === 'id') {
      frame.id = value;
    } else if (field === 'event') {
      frame.event = value;
    } else if (field === 'data') {
      data.push(value);
    }
  }
  frame.data = data.join('\n');
  return frame;
}

/**
 * Split a byte-decoder buffer into complete SSE frames plus the incomplete tail.
 * A frame split across two network reads is reassembled; a comment frame is
 * returned too (the server's heartbeat is observable, never a Run seq).
 */
function parseSSEBuffer(buffer) {
  const text = String(buffer === undefined || buffer === null ? '' : buffer).replace(/\r\n/g, '\n');
  const blocks = text.split('\n\n');
  const rest = blocks.pop();
  const frames = [];
  for (const block of blocks) {
    if (block.trim() === '') {
      continue;
    }
    frames.push(parseSSEFrame(block.split('\n')));
  }
  return { frames: frames, rest: rest };
}

/**
 * Turn one data frame into an event envelope. A malformed frame, an unsafe seq
 * or an id/data disagreement is dropped: a page only renders server-committed
 * facts it can order, never a guessed frame.
 */
function runEventFromFrame(frame) {
  if (!frame || typeof frame.data !== 'string' || frame.data === '') {
    return null;
  }
  let event;
  try {
    event = JSON.parse(frame.data);
  } catch (error) {
    return null;
  }
  if (!event || typeof event !== 'object' || Array.isArray(event)) {
    return null;
  }
  const seq = Number(event.seq);
  if (!Number.isSafeInteger(seq) || seq < 1) {
    return null;
  }
  if (frame.id !== '' && frame.id !== undefined && frame.id !== null) {
    const id = Number(frame.id);
    if (!Number.isSafeInteger(id) || id !== seq) {
      return null;
    }
  }
  return Object.assign({}, event, { seq: seq });
}

/**
 * Add one event to the timeline, deduped by seq and sorted ascending. A replay
 * after reconnecting may repeat frames the page already has; they are no-ops.
 */
function applyRunEvent(existing, event) {
  const current = Array.isArray(existing) ? existing : [];
  const seq = Number(event && event.seq);
  if (!Number.isSafeInteger(seq) || seq < 1) {
    return current;
  }
  if (current.some((entry) => Number(entry.seq) === seq)) {
    return current;
  }
  const next = current.concat([Object.assign({}, event, { seq: seq })]);
  next.sort((left, right) => Number(left.seq) - Number(right.seq));
  return next;
}

/** The Last-Event-ID value for the next reconnect: the highest committed seq. */
function nextLastEventID(events) {
  let cursor = '';
  for (const event of Array.isArray(events) ? events : []) {
    const seq = Number(event && event.seq);
    if (Number.isSafeInteger(seq) && seq >= 1 && (cursor === '' || seq > Number(cursor))) {
      cursor = String(seq);
    }
  }
  return cursor;
}

/** The committed terminal state in a timeline, or '' while it is non-terminal. */
function timelineTerminalState(events) {
  let last = '';
  for (const event of Array.isArray(events) ? events : []) {
    if (event && event.type === 'run.status' && event.payload && typeof event.payload === 'object') {
      last = String(event.payload.state || '');
    }
  }
  return isRunTerminal(last) ? last : '';
}

/**
 * One honest line per durable event. Only the schema-approved metadata fields
 * are rendered; a payload's content/body/delta keys are never displayed because
 * no such field belongs to a metadata event. A `run.status` running event is
 * explicitly not a success: phase (finalizing) comes from the Run snapshot.
 */
function describeRunEvent(event) {
  const seq = Number(event && event.seq);
  const type = String((event && event.type) || '');
  const payload = event && event.payload && typeof event.payload === 'object' ? event.payload : {};
  const prefix = 'seq ' + (Number.isSafeInteger(seq) ? seq : '?') + ' · ' + (type || '事件') + ' · ';
  if (type === 'run.status') {
    const state = String(payload.state || 'unknown');
    const errorCode = payload.error_code ? String(payload.error_code) : '';
    const suffix = errorCode ? '（error_code=' + errorCode + '）' : '';
    if (state === 'running') {
      return prefix + '已提交状态 running（phase 以 Run 快照为准；finalizing 尚未成功）' + suffix;
    }
    if (isRunTerminal(state)) {
      return prefix + '已提交终态 ' + state + suffix;
    }
    return prefix + '已提交状态 ' + state + suffix;
  }
  if (type === 'node.status') {
    return prefix + '节点 ' + String(payload.node_id || '?') + ' 状态 ' +
      String(payload.state || '?') + '（attempt_no=' + String(payload.attempt_no || '?') + '）';
  }
  // Future contract types (approval.required/artifact.ready/usage.updated) are
  // listed, never rendered from unknown payload fields.
  return prefix + '已提交事件（本页不展示该类型的 payload 内容）';
}

/**
 * Turn a failed stream response into one honest status. 410 is terminal for
 * this stream: the replay window is gone, so the page must switch to the Run
 * snapshot (details.snapshot_url) instead of pretending the history is complete.
 * 401/404 stop too; everything else is retryable with the same Last-Event-ID.
 */
function streamFailure(status, payload) {
  const error = (payload && payload.error) || {};
  const code = error.code || ('http_' + status);
  if (status === 410) {
    const details = error.details || {};
    const snapshot = details.snapshot_url ? String(details.snapshot_url) : '/api/v1/runs';
    return {
      retry: false,
      text: '事件保留窗口已过期（410 ' + code + '）；不能静默续传，请改用 Run 快照：' + snapshot,
    };
  }
  if (status === 401) {
    return { retry: false, text: '登录已过期（401）；重新登录后可按 Last-Event-ID 重连事件流。' };
  }
  if (status === 404) {
    return { retry: false, text: '该 Run 不存在或不属于你（404）；事件流已停止。' };
  }
  return {
    retry: true,
    text: '事件流暂时不可用 [' + code + '] ' + (error.message || '网络或服务端错误') +
      '；将按 Last-Event-ID 重连。',
  };
}

function waitMilliseconds(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/**
 * Read the SSE stream with fetch + ReadableStream (not EventSource) so a 410 can
 * be observed and answered with the snapshot URL, and so reconnects can send an
 * explicit Last-Event-ID. The stream is idempotent: applyRunEvent dedupes seq.
 */
async function streamRunTimeline(runId, callbacks) {
  const options = callbacks || {};
  const onStatus = typeof options.onStatus === 'function' ? options.onStatus : function () {};
  const onEvent = typeof options.onEvent === 'function' ? options.onEvent : function () {};
  const isCancelled = typeof options.isCancelled === 'function' ? options.isCancelled : function () { return false; };
  let events = [];
  let cursor = Number.isSafeInteger(Number(options.afterSeq)) && Number(options.afterSeq) >= 0
    ? String(Number(options.afterSeq))
    : '';

  for (;;) {
    if (isCancelled()) {
      return;
    }
    let response;
    try {
      const headers = { Accept: 'text/event-stream' };
      if (cursor !== '') {
        headers['Last-Event-ID'] = cursor;
      }
      response = await fetch(runEventsPath(runId), {
        method: 'GET',
        headers: headers,
        credentials: 'same-origin',
        cache: 'no-store',
      });
    } catch (error) {
      onStatus('事件流连接失败，正在按 Last-Event-ID=' + (cursor || '无') + ' 重连…', 'pending');
      await waitMilliseconds(1000);
      continue;
    }

    if (!response.ok || !response.body || typeof response.body.getReader !== 'function') {
      let payload = null;
      try {
        payload = await response.json();
      } catch (error) {
        payload = null;
      }
      const failure = streamFailure(response.status, payload);
      onStatus(failure.text, failure.retry ? 'pending' : 'bad');
      if (!failure.retry) {
        return;
      }
      await waitMilliseconds(1000);
      continue;
    }

    onStatus('已连接持久事件流（Last-Event-ID ' + (cursor || '无') + '），按 seq 去重回放。', 'ok');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      if (isCancelled()) {
        if (typeof reader.cancel === 'function') {
          await reader.cancel();
        }
        return;
      }
      const chunk = await reader.read();
      if (chunk.done) {
        break;
      }
      buffer += decoder.decode(chunk.value, { stream: true });
      const parsed = parseSSEBuffer(buffer);
      buffer = parsed.rest;
      for (const frame of parsed.frames) {
        const event = runEventFromFrame(frame);
        if (!event) {
          continue; // comment/heartbeat or malformed frame: not a business fact
        }
        const next = applyRunEvent(events, event);
        if (next === events) {
          continue; // duplicate seq from a reconnect: never rendered twice
        }
        events = next;
        cursor = nextLastEventID(events);
        onEvent(events, event);
        const terminal = timelineTerminalState(events);
        if (terminal !== '') {
          onStatus('已回放到已提交终态 ' + terminal + '；事件流关闭。', 'ok');
          return;
        }
      }
    }
    onStatus('事件流已断开，正在按 Last-Event-ID=' + (cursor || '无') + ' 重连…', 'pending');
    await waitMilliseconds(1000);
  }
}

// ---------------------------------------------------------------------------
// DOM wiring (not executed by the Node behaviour test)
// ---------------------------------------------------------------------------

function runsStatus(message, kind) {
  const element = document.getElementById('runs-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function renderRunItem(run) {
  const summary = runSummary(run);
  const item = document.createElement('li');
  const state = document.createElement('strong');
  state.textContent = summary.state + ' / ' + (summary.phase || 'idle');
  const kind = document.createElement('span');
  kind.textContent = ' kind=' + summary.kind + ' revision=' + summary.revision;
  const id = document.createElement('code');
  id.textContent = ' ' + summary.id;
  const snapshot = document.createElement('span');
  snapshot.textContent = ' snapshot=' + summary.executionSnapshotID;
  const open = document.createElement('button');
  open.type = 'button';
  open.textContent = '查看详情';
  open.addEventListener('click', () => loadRunDetail(summary.id));
  item.append(state, kind, id, snapshot, ' ', open);
  document.getElementById('runs-list').appendChild(item);
}

function showRunDetail(run, facts) {
  const summary = runSummary(run);
  document.getElementById('detail-id').textContent = summary.id;
  document.getElementById('detail-state').textContent = summary.state;
  document.getElementById('detail-phase').textContent = summary.phase || '（未提供）';
  document.getElementById('detail-kind').textContent = summary.kind;
  document.getElementById('detail-revision').textContent = summary.revision;
  document.getElementById('detail-snapshot').textContent = summary.executionSnapshotID;
  document.getElementById('detail-lifecycle').textContent = lifecycleText(run);
  document.getElementById('detail-manifest').textContent = facts.manifestSHA256 || '（响应头未提供）';
  document.getElementById('detail-lease').textContent =
    summary.state === 'running' && facts.leaseDeadline
      ? 'started_at ' + (facts.startedAt || '（未提供）') + ' / lease_deadline ' + facts.leaseDeadline
      : '（没有可显示的租约事实）';
  document.getElementById('detail-note').textContent = describeRunState(run, facts);
  document.getElementById('run-detail').hidden = false;
}

// T032: one durable timeline per selected Run. Starting a new selection bumps
// the generation so the old fetch loop stops instead of appending frames to the
// wrong list; the list itself is deduped by seq.
let runTimelineGeneration = 0;

function beginRunTimeline(runId, elements) {
  const generation = ++runTimelineGeneration;
  const list = elements && elements.listID ? document.getElementById(elements.listID) : null;
  const status = elements && elements.statusID ? document.getElementById(elements.statusID) : null;
  if (list) {
    list.replaceChildren();
  }
  const isCancelled = () => generation !== runTimelineGeneration;
  const setStatus = (text, kind) => {
    if (isCancelled() || !status) {
      return;
    }
    status.textContent = text;
    status.className = 'status ' + (kind || '');
  };
  setStatus('正在连接持久事件流 ' + runEventsPath(runId) + '…', 'pending');
  if (typeof fetch !== 'function' || typeof TextDecoder === 'undefined') {
    setStatus('当前浏览器不支持流式事件读取；页面只显示 GET /runs/{id} 的已提交快照，不伪造时间线。', 'bad');
    return;
  }
  streamRunTimeline(runId, {
    isCancelled: isCancelled,
    onStatus: setStatus,
    onEvent: (events, event) => {
      if (isCancelled() || !list) {
        return;
      }
      // One line per committed event; describeRunEvent never renders payload
      // content, and the seq dedupe above already suppressed replays.
      const item = document.createElement('li');
      item.textContent = describeRunEvent(event);
      list.appendChild(item);
    },
  }).catch((error) => {
    setStatus('事件流读取失败：' + (error && error.message ? error.message : '未知错误'), 'bad');
  });
}

// T031: while the Run is not terminal the page polls the owner-only detail (the
// state is committed by the Go finalizer), and stops the moment a terminal state
// arrives. Nothing is rendered as success before that committed state.
const RUN_POLL_INTERVAL_MS = 2000;
const RUN_POLL_MAX = 60;

async function pollRunDetail(runId, polls) {
  if (polls >= RUN_POLL_MAX) {
    runsStatus('已停止轮询（' + polls + ' 次）；Run 仍未进入终态。', 'pending');
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, RUN_POLL_INTERVAL_MS));
  const result = await apiFull(runDetailPath(runId));
  const facts = runRuntimeFacts(result.headers);
  showRunDetail(result.payload, facts);
  if (shouldPollRun(result.payload)) {
    return pollRunDetail(runId, polls + 1);
  }
  runsStatus('Run ' + runId + ' 已进入终态 ' + result.payload.state + '。', 'ok');
}

async function loadRunDetail(runId) {
  runsStatus('正在加载 Run ' + runId + ' 的详情…', 'pending');
  try {
    await requireLogin();
    const result = await apiFull(runDetailPath(runId));
    const facts = runRuntimeFacts(result.headers);
    showRunDetail(result.payload, facts);
    beginRunTimeline(runId, {
      listID: 'detail-timeline',
      statusID: 'detail-timeline-status',
      previewID: 'detail-preview',
    });
    if (shouldPollRun(result.payload)) {
      runsStatus('已加载 Run ' + runId + '，仍在执行；页面会轮询到终态（不会提前显示成功）。', 'pending');
      await pollRunDetail(runId, 0);
    } else {
      runsStatus('已加载 Run ' + runId + '。', 'ok');
    }
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    runsStatus('加载详情失败 [' + error.code + '] ' + error.message, 'bad');
  }
}

async function loadRunsPage(cursor) {
  runsStatus('正在加载运行…', 'pending');
  try {
    await requireLogin();
    const page = await api(listRunsPath(cursor, 20));
    const items = page.items || [];
    for (const run of items) {
      renderRunItem(run);
    }
    const next = page.next_cursor || null;
    document.getElementById('runs-next').hidden = !next;
    document.getElementById('runs-empty').hidden = items.length !== 0;
    runsStatus(
      items.length === 0
        ? '没有属于你的 Run。'
        : '本页 ' + items.length + ' 个 Run' + (next ? '，还有下一页。' : '。'),
      'ok',
    );
    nextCursor = next;
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    runsStatus('加载失败 [' + error.code + '] ' + error.message, 'bad');
  }
}

let nextCursor = null;

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('runs-list')) {
  document.getElementById('runs-next').addEventListener('click', () => {
    if (nextCursor) {
      document.getElementById('runs-list').replaceChildren();
      loadRunsPage(nextCursor);
    }
  });
  loadRunsPage(null);
}
