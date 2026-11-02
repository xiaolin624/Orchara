/*
 * F02A/T027 debug submission page.
 *
 * The page is a real client of two contract endpoints:
 *
 *   POST /api/v1/applications/{id}/debug-runs  -> 202 Run (+ X-Manifest-SHA256)
 *   GET  /api/v1/runs/{id}                     -> 200 Run (+ X-Manifest-SHA256)
 *
 * What it demonstrates, and what it deliberately does not:
 *
 *   - The creator picks the current draft revision, enters a JSON test input and
 *     optionally up to five artifact ids. The submission carries one generated
 *     Idempotency-Key per attempt, so a retried click cannot create a second run
 *     and a network failure is safe to repeat.
 *   - After 202 the page shows the queued run: state, phase, kind, revision, the
 *     fixed execution_snapshot_id and the manifest hash from the non-contract
 *     X-Manifest-SHA256 header (the Run schema has no hash field, ADR 0004 §38).
 *     It then polls GET /runs/{id} and stops on a terminal state.
 *   - F03 delivers claiming: while an attempt is live the detail response also
 *     carries X-Run-Attempt-Started-At / X-Run-Lease-Deadline, and the page shows
 *     those real facts. It never fakes progress.
 *   - No secret is ever put in a URL or in browser storage: the session cookie is
 *     HttpOnly, CSRF comes from GET /auth/me, and the idempotency key lives only
 *     in this page's memory.
 *
 * The pure helpers at the top are exported on purpose: the Node test executes
 * them with stubbed fetch, which is the only way a page assertion can be
 * behavioural without a browser. The DOM wiring stays at the bottom.
 */

/** The application id from /apps/{id}/debug, or null for any other path. */
function debugApplicationId(pathname) {
  const match = /^\/apps\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\/debug\/?$/.exec(
    String(pathname || ''),
  );
  return match ? match[1].toLowerCase() : null;
}

function debugRunsPath(applicationId) {
  return '/api/v1/applications/' + encodeURIComponent(applicationId) + '/debug-runs';
}

function debugRunPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(runId);
}

function debugFailure(code, message) {
  const failure = new Error(message);
  failure.code = code;
  failure.status = 0;
  return failure;
}

/**
 * One Idempotency-Key per submission attempt: a UUID when the browser exposes
 * crypto.randomUUID, otherwise 32 random hex characters. It is an operation
 * name, not a credential; the server binds it to this principal, path and body.
 */
function newIdempotencyKey() {
  const source = typeof crypto !== 'undefined' ? crypto : null;
  if (source && typeof source.randomUUID === 'function') {
    return source.randomUUID();
  }
  if (source && typeof source.getRandomValues === 'function') {
    const bytes = new Uint8Array(16);
    source.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  // Last resort: still unique per attempt, and never a security decision.
  return 'debug-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

const UUID_PATTERN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;

/** Split the artifact textarea on lines, commas or whitespace. */
function splitArtifactIDs(value) {
  return String(value === undefined || value === null ? '' : value)
    .split(/[\s,]+/)
    .map((entry) => entry.trim())
    .filter((entry) => entry !== '');
}

/** Parse one textarea as a JSON object; the message names the field. */
function parseDebugInput(text) {
  let value;
  try {
    value = JSON.parse(String(text === undefined || text === null ? '' : text));
  } catch (error) {
    throw debugFailure('input', '测试输入不是合法 JSON：' + error.message);
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw debugFailure('input', '测试输入必须是 JSON 对象（契约 input 是 object）');
  }
  return value;
}

/**
 * The exact POST body. Client-side checks mirror the server's structural rules
 * so the creator sees the reason next to the field; the server stays the
 * authority (422 validation_failed / located errors).
 */
function debugPayloadFromFields(fields) {
  const source = fields || {};
  const revisionText = String(source.draftRevision === undefined || source.draftRevision === null ? '' : source.draftRevision).trim();
  if (!/^\d+$/.test(revisionText)) {
    throw debugFailure('draft_revision', '请填写当前草稿 revision（≥1 的整数）');
  }
  const revision = Number(revisionText);
  if (!Number.isSafeInteger(revision) || revision < 1) {
    throw debugFailure('draft_revision', 'revision 必须是 ≥1 的整数');
  }
  const input = parseDebugInput(source.inputText);
  const artifactIDs = splitArtifactIDs(source.artifactIDsText);
  if (artifactIDs.length > 5) {
    throw debugFailure('artifact_ids', '最多只能引用 5 个输入 artifact（契约 maxItems=5）');
  }
  for (const id of artifactIDs) {
    if (!UUID_PATTERN.test(id)) {
      throw debugFailure('artifact_ids', '输入 artifact 必须是 UUID：' + id);
    }
  }
  return { draft_revision: revision, input: input, artifact_ids: artifactIDs };
}

/** The Run fields the page shows; never the raw body. */
function runSummary(run) {
  const value = run || {};
  return {
    id: value.id || '',
    state: value.state || 'unknown',
    kind: value.kind || '',
    revision: value.revision === undefined || value.revision === null ? '' : String(value.revision),
    executionSnapshotID: value.execution_snapshot_id || '',
  };
}

/**
 * A run is polled while it can still change on its own. queued/running and the
 * transitional states continue; terminal states stop. F03 owns the transitions,
 * so today the poll simply shows a stable queued run.
 */
function runIsTerminal(state) {
  return state === 'succeeded' || state === 'failed' || state === 'cancelled';
}

/**
 * T031: one honest sentence per execution stage. A result proposal moves the
 * Run to phase=finalizing, but that is still not success: the Go finalizer waits
 * for supervisor exit/sandbox/DB-drain evidence. Success is only ever described
 * for a committed terminal state.
 */
function describeRunStage(run) {
  const summary = runSummary(run || {});
  // The debug page's runSummary deliberately carries no phase; read the field
  // from the contract Run body itself.
  const phase = (run && run.phase) || '';
  if (runIsTerminal(summary.state)) {
    return '已进入终态 ' + summary.state + '（由 Go finalizer 在结果提议与 supervisor 退出证据齐全后提交）。';
  }
  if (summary.state === 'recovery_pending') {
    return '已确认退出但没有结果，Run 挂起在 recovery_pending；恢复证据齐全前不会重新执行。';
  }
  if (summary.state === 'needs_reconciliation') {
    return 'Run 等待人工核对（needs_reconciliation）；不显示为成功或失败。';
  }
  if (summary.state === 'running' && phase === 'finalizing') {
    return '结果提议已保存，正在 finalizing：等待 supervisor 的退出证据（进程/沙箱/数据库排空），终态前这不是成功。';
  }
  if (summary.state === 'running') {
    return '正在执行（executing）；结果提议与退出证据由 F03 supervisor 协议提交。';
  }
  return '当前阶段 ' + (phase || 'idle') + '（' + summary.state + '）。';
}

function shouldPollRun(run, pollsSoFar, maxPolls) {
  if (!run || typeof run.state !== 'string') {
    return false;
  }
  if (runIsTerminal(run.state)) {
    return false;
  }
  const polls = Number.isFinite(pollsSoFar) ? pollsSoFar : 0;
  const limit = Number.isFinite(maxPolls) ? maxPolls : 40;
  return polls < limit;
}

/** Read the header the contract Run schema cannot carry. */
function manifestSHAFromHeaders(headers) {
  if (!headers || typeof headers.get !== 'function') {
    return '';
  }
  return headers.get('X-Manifest-SHA256') || '';
}

/**
 * The real claim/lease facts F03 attaches to a run detail while an attempt is
 * live. They are non-secret timestamps; the attempt token is never exposed.
 */
function leaseFactsFromHeaders(headers) {
  if (!headers || typeof headers.get !== 'function') {
    return { startedAt: '', leaseDeadline: '' };
  }
  return {
    startedAt: headers.get('X-Run-Attempt-Started-At') || '',
    leaseDeadline: headers.get('X-Run-Lease-Deadline') || '',
  };
}

/** Submit one debug run. A 202 body is the queued Run. */
async function submitDebugRequest(applicationId, payload, idempotencyKey) {
  if (!idempotencyKey) {
    throw debugFailure('idempotency_key', '缺少 Idempotency-Key，未能提交');
  }
  const result = await apiFull(debugRunsPath(applicationId), {
    method: 'POST',
    body: payload,
    csrf: await csrf(),
    headers: { 'Idempotency-Key': idempotencyKey },
  });
  return {
    run: result.payload,
    manifestSHA256: manifestSHAFromHeaders(result.headers),
    lease: leaseFactsFromHeaders(result.headers),
  };
}

/** One poll of GET /runs/{id}. */
async function fetchRun(runId) {
  const result = await apiFull(debugRunPath(runId));
  return {
    run: result.payload,
    manifestSHA256: manifestSHAFromHeaders(result.headers),
    lease: leaseFactsFromHeaders(result.headers),
  };
}

/** The current draft revision, so the creator never has to guess it. */
async function fetchDraftRevision(applicationId) {
  const result = await apiFull('/api/v1/applications/' + encodeURIComponent(applicationId) + '/draft');
  const etag = result.headers && typeof result.headers.get === 'function' ? result.headers.get('ETag') : null;
  const match = /^"(\d+)"$/.exec(String(etag || ''));
  return match ? Number(match[1]) : null;
}

// ---------------------------------------------------------------------------
// DOM wiring (not executed by the Node behaviour test)
// ---------------------------------------------------------------------------

const DEBUG_POLL_INTERVAL_MS = 1500;
const DEBUG_MAX_POLLS = 40;

function debugStatus(message, kind) {
  const element = document.getElementById('debug-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function debugShowRun(run, manifestSHA256, note, frozenRevision, lease) {
  const panel = document.getElementById('run-panel');
  if (!panel) {
    return;
  }
  const summary = runSummary(run);
  document.getElementById('run-id').textContent = summary.id;
  document.getElementById('run-state').textContent = summary.state;
  document.getElementById('run-phase').textContent = (run && run.phase) || 'idle';
  document.getElementById('run-kind').textContent = summary.kind;
  document.getElementById('run-revision').textContent = summary.revision;
  document.getElementById('run-snapshot').textContent = summary.executionSnapshotID;
  document.getElementById('run-manifest').textContent = manifestSHA256 || '（响应头未提供）';
  const facts = lease || {};
  document.getElementById('run-lease').textContent =
    summary.state === 'running' && facts.leaseDeadline
      ? 'started_at ' + (facts.startedAt || '（未提供）') + ' / lease_deadline ' + facts.leaseDeadline
      : '（没有可显示的租约事实）';
  if (frozenRevision !== undefined && frozenRevision !== null && frozenRevision !== '') {
    // The frozen draft revision is a submission fact: the contract Run schema
    // does not expose the snapshot's draft_revision (there is no snapshot read
    // endpoint yet), so the page shows the revision the user froze.
    document.getElementById('run-draft-revision').textContent = String(frozenRevision);
  }
  document.getElementById('run-note').textContent = note || '';
  panel.hidden = false;
}

function initDebug() {
  const applicationId = debugApplicationId(window.location.pathname);
  if (!applicationId) {
    debugStatus('这个地址没有指向一个调试入口（应为 /apps/<application id>/debug）。', 'bad');
    return;
  }
  let idempotencyKey = newIdempotencyKey();
  let pollTimer = null;
  let polls = 0;
  let frozenRevision = null;

  async function loadRevision() {
    try {
      await requireLogin();
      const revision = await fetchDraftRevision(applicationId);
      if (revision !== null) {
        document.getElementById('debug-revision').value = String(revision);
        debugStatus('已加载当前草稿 revision ' + revision + '。提交会冻结这个 revision。', 'ok');
      } else {
        debugStatus('无法读取当前草稿 revision；请刷新页面或先保存草稿。', 'bad');
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      debugStatus('加载草稿失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function poll(runId) {
    polls += 1;
    try {
      const polled = await fetchRun(runId);
      debugShowRun(
        polled.run,
        polled.manifestSHA256,
        describeRunStage(polled.run) + ' 本页只显示服务端返回的 state/phase 与租约事实。',
        frozenRevision,
        polled.lease,
      );
      if (shouldPollRun(polled.run, polls, DEBUG_MAX_POLLS)) {
        pollTimer = setTimeout(() => poll(runId), DEBUG_POLL_INTERVAL_MS);
      } else {
        stopPolling();
        if (runIsTerminal(polled.run.state)) {
          debugStatus('Run 已进入终态 ' + polled.run.state + '。', 'ok');
        } else {
          debugStatus('已停止轮询（' + polls + ' 次）；Run 仍为 ' + polled.run.state + '。', 'pending');
        }
      }
    } catch (error) {
      stopPolling();
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      debugStatus('轮询失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function submit(event) {
    event.preventDefault();
    let payload;
    try {
      payload = debugPayloadFromFields({
        draftRevision: document.getElementById('debug-revision').value,
        inputText: document.getElementById('debug-input').value,
        artifactIDsText: document.getElementById('debug-artifacts').value,
      });
    } catch (error) {
      debugStatus('无法提交 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    stopPolling();
    debugStatus('正在提交（Idempotency-Key ' + idempotencyKey + '）…', 'pending');
    try {
      const submitted = await submitDebugRequest(applicationId, payload, idempotencyKey);
      const summary = runSummary(submitted.run);
      frozenRevision = payload.draft_revision;
      debugShowRun(
        submitted.run,
        submitted.manifestSHA256,
        '已固定快照 ' + summary.executionSnapshotID + '。Run 现在排队；领取与执行由 F03 supervisor 协议完成。',
        frozenRevision,
        submitted.lease,
      );
      debugStatus('已创建 queued debug Run ' + summary.id + '（revision ' + summary.revision + '）。', 'ok');
      // T032: the panel's durable timeline is the same SSE client the runs page
      // uses (runs.js is loaded first). It dedupes by seq, reconnects with
      // Last-Event-ID, and shows the 410 snapshot URL instead of faking history.
      if (typeof beginRunTimeline === 'function') {
        beginRunTimeline(summary.id, {
          listID: 'run-timeline',
          statusID: 'run-timeline-status',
          previewID: 'run-preview',
        });
      }
      // A new attempt gets a new key; the completed one is never reused.
      idempotencyKey = newIdempotencyKey();
      document.getElementById('debug-key').textContent = idempotencyKey;
      polls = 0;
      poll(summary.id);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      if (error.status === 409 && error.code === 'revision_conflict') {
        const current = error.details && error.details.current_revision;
        debugStatus(
          'revision 冲突：服务器上的草稿 revision 是 ' +
            (current === undefined || current === null ? '未知' : current) +
            '，本页填写的是 ' +
            payload.draft_revision +
            '。旧 revision 不会被调试；刷新页面取得当前 revision 后重试（同一 Idempotency-Key 可安全重试）。',
          'bad',
        );
        return;
      }
      if (error.status === 422 && error.details && Array.isArray(error.details.errors)) {
        const located = error.details.errors
          .map((entry) => (entry.node_id ? entry.node_id + ' ' : '') + entry.code + ' @ ' + entry.pointer)
          .join('；');
        debugStatus('草稿不能执行 [' + error.code + '] ' + located, 'bad');
        return;
      }
      debugStatus('提交失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  document.getElementById('debug-form').addEventListener('submit', submit);
  document.getElementById('debug-key').textContent = idempotencyKey;
  loadRevision();
}

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('debug-form')) {
  initDebug();
}
