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
 */
function describeRunState(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  if (summary.state === 'succeeded' || summary.state === 'failed' || summary.state === 'cancelled') {
    return '已进入终态 ' + summary.state + '。';
  }
  if (summary.state === 'queued') {
    return '尚未被领取（queued）；领取、心跳与退出证据属于 F03 内部 supervisor 协议。';
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
  document.getElementById('detail-manifest').textContent = facts.manifestSHA256 || '（响应头未提供）';
  document.getElementById('detail-lease').textContent =
    summary.state === 'running' && facts.leaseDeadline
      ? 'started_at ' + (facts.startedAt || '（未提供）') + ' / lease_deadline ' + facts.leaseDeadline
      : '（没有可显示的租约事实）';
  document.getElementById('detail-note').textContent = describeRunState(run, facts);
  document.getElementById('run-detail').hidden = false;
}

async function loadRunDetail(runId) {
  runsStatus('正在加载 Run ' + runId + ' 的详情…', 'pending');
  try {
    await requireLogin();
    const result = await apiFull(runDetailPath(runId));
    const facts = runRuntimeFacts(result.headers);
    showRunDetail(result.payload, facts);
    runsStatus('已加载 Run ' + runId + '。', 'ok');
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
