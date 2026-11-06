/*
 * F12/T096: the truthful Run-state surface for interrupted and waiting runs.
 *
 * Why this module exists
 * ----------------------
 * The durable SSE timeline is the only live signal a page gets, and it is
 * deliberately small: `run.status` carries `state` (+ optional `error_code`) and
 * never `phase` (contracts/event.schema.json). `finalizing` is a **phase** on the
 * owner-only Run snapshot (`GET /api/v1/runs/{id}`), and `cancelling`,
 * `recovery_pending`, `needs_reconciliation` are **states**. A page that maps
 * `state` alone either never sees `finalizing` or invents an outcome.
 *
 * This module is the one place that turns (state, phase, children, artifacts)
 * into the text and actions the pages render. It is pure: no fetch, no DOM, no
 * timers, so the Node page test executes the exact bytes the API serves.
 *
 * The 410 rule
 * ------------
 * A replay cursor older than the retention window answers
 * `410 event_cursor_expired` with `details.snapshot_url`. API.md §4 requires the
 * client to read the current Run snapshot and rebuild from it. Silently
 * continuing from the current point (or replaying from 0) would fake a complete
 * history, so `runStreamCorrectFromSnapshot` drops the cursor and returns the
 * snapshot-backed view instead.
 */

const RUNSTREAM_TERMINAL_STATES = Object.freeze({
  succeeded: true,
  failed: true,
  cancelled: true,
});

/** One label per contract `Run.state` value; unknown states stay explicit. */
const RUNSTREAM_STATE_LABELS = Object.freeze({
  queued: '尚未被领取（queued）',
  running: '执行中（running）',
  waiting_approval: '等待审批（waiting_approval）',
  waiting_children: '等待子任务（waiting_children）',
  cancelling: '正在停止（cancelling）',
  recovery_pending: '等待恢复（recovery_pending）',
  needs_reconciliation: '等待人工核对（needs_reconciliation）',
  succeeded: '已成功（succeeded）',
  failed: '已失败（failed）',
  cancelled: '已停止（cancelled）',
});

/** One label per contract `Run.phase` value; `finalizing` is phase-only. */
const RUNSTREAM_PHASE_LABELS = Object.freeze({
  idle: '空闲（idle）',
  executing: '执行中（executing）',
  waiting: '等待中（waiting）',
  finalizing: '收尾中（finalizing，等待退出证据）',
});

function runStreamIsTerminal(state) {
  return RUNSTREAM_TERMINAL_STATES[String(state || '')] === true;
}

/** One label per contract `Run.state`, '' when the server sent an unknown value. */
function runStreamStateLabel(state) {
  return RUNSTREAM_STATE_LABELS[String(state || '')] || '';
}

/** One label per contract `Run.phase`, '' when the server sent an unknown value. */
function runStreamPhaseLabel(phase) {
  return RUNSTREAM_PHASE_LABELS[String(phase || '')] || '';
}

/** A same-origin Run snapshot path, or '' for anything a server should not send. */
function runStreamSafeSnapshotPath(value) {
  const raw = String(value === undefined || value === null ? '' : value).trim();
  if (raw === '' || raw.charAt(0) !== '/') {
    return '';
  }
  if (raw.indexOf('://') >= 0 || raw.indexOf('//') >= 0) {
    return '';
  }
  if (raw.indexOf('..') >= 0 || raw.indexOf('\\') >= 0) {
    return '';
  }
  if (/\s/.test(raw)) {
    return '';
  }
  if (raw.indexOf('/api/v1/runs/') !== 0) {
    return '';
  }
  return raw;
}

/**
 * Classify a failed event-stream response.
 *
 * `kind` is the page's decision input: `cursor_expired` must fetch the snapshot,
 * `auth`/`gone` are terminal for the stream, `transient` retries with the same
 * Last-Event-ID. A 410 never retries: the retention window is gone for good.
 */
function runStreamFailure(status, payload) {
  const error = (payload && payload.error) || {};
  const code = error.code || ('http_' + status);
  if (status === 410) {
    const details = error.details && typeof error.details === 'object' ? error.details : {};
    const snapshotURL = details.snapshot_url ? String(details.snapshot_url) : '';
    const snapshotPath = runStreamSafeSnapshotPath(snapshotURL);
    return {
      retry: false,
      kind: 'cursor_expired',
      code: code,
      snapshotURL: snapshotURL,
      snapshotPath: snapshotPath,
      text:
        '事件保留窗口已过期（410 ' + code + '）：不能静默续传，必须按 snapshot_url ' +
        '重新读取 Run 快照并重建视图：' + (snapshotPath || snapshotURL || '（服务端未提供）'),
    };
  }
  if (status === 401) {
    return {
      retry: false,
      kind: 'auth',
      code: code,
      snapshotURL: '',
      snapshotPath: '',
      text: '登录已过期（401）；重新登录后可按 Last-Event-ID 重连事件流。',
    };
  }
  if (status === 404) {
    return {
      retry: false,
      kind: 'gone',
      code: code,
      snapshotURL: '',
      snapshotPath: '',
      text: '该 Run 不存在或不属于你（404）；事件流已停止。',
    };
  }
  return {
    retry: true,
    kind: 'transient',
    code: code,
    snapshotURL: '',
    snapshotPath: '',
    text:
      '事件流暂时不可用 [' + code + '] ' + (error.message || '网络或服务端错误') +
      '；将按 Last-Event-ID 重连（410 保留窗口分支不会重连）。',
  };
}

/**
 * The snapshot path to rebuild from: the server's documented `details` extension
 * when it is a safe same-origin Run path, otherwise the page's own run id.
 * A foreign origin or traversal string is never followed.
 */
function runStreamSnapshotPath(failure, runID) {
  const value = failure && typeof failure === 'object' ? failure : {};
  const safe = runStreamSafeSnapshotPath(value.snapshotPath || value.snapshotURL || '');
  if (safe !== '') {
    return safe;
  }
  const id = runID === undefined || runID === null ? '' : String(runID).trim();
  if (id === '') {
    return '';
  }
  return '/api/v1/runs/' + encodeURIComponent(id);
}

/**
 * Rebuild the page view from a Run snapshot after the event history expired.
 * `snapshot` is either the Run body or the `apiFull` result `{payload, headers}`.
 */
function runStreamSnapshotCorrection(failure, runID, snapshot) {
  const path = runStreamSnapshotPath(failure, runID);
  const candidate = snapshot && typeof snapshot === 'object' && snapshot.payload !== undefined
    ? snapshot.payload
    : snapshot;
  const run = candidate && typeof candidate === 'object' ? candidate : null;
  const state = run && typeof run.state === 'string' ? run.state : '';
  if (path === '' || state === '') {
    return {
      applied: false,
      reason: path === '' ? 'no_snapshot_path' : 'snapshot_invalid',
      snapshotPath: path,
      historyLost: true,
      resumeCursor: '',
      run: null,
      truth: null,
      statusText:
        '事件保留窗口已过期（410），但没有可安全使用的 Run 快照；页面停止时间线，' +
        '不静默续传，也不伪造历史。',
      timelineNotice: '（持久事件历史已超出保留窗口，且快照不可用；本页没有回放旧帧。）',
    };
  }
  const truth = runStreamTruth(run, {});
  const phase = typeof run.phase === 'string' && run.phase !== '' ? run.phase : 'unknown';
  return {
    applied: true,
    reason: 'rebuilt_from_snapshot',
    snapshotPath: path,
    historyLost: true,
    // The expired cursor is dropped: the stream is not resumed from it.
    resumeCursor: '',
    run: run,
    truth: truth,
    statusText:
      '已按 410 快照重建视图：state=' + state + '，phase=' + phase + '（' + truth.label +
      '）；旧游标已丢弃，不静默续传。',
    timelineNotice:
      '（持久事件历史已超出保留窗口；本页按当前 Run 快照重建，未回放旧帧，' +
      '也没有从旧游标静默续传。）',
  };
}

/**
 * Fetch the snapshot and rebuild. `options.fetchSnapshot(path)` must return the
 * Run body or `{payload, headers}`; a failed fetch is reported, never swallowed.
 */
async function runStreamCorrectFromSnapshot(failure, options) {
  const opts = options && typeof options === 'object' ? options : {};
  const path = runStreamSnapshotPath(failure, opts.runID);
  if (path === '') {
    return runStreamSnapshotCorrection(
      failure,
      opts.runID,
      null
    );
  }
  let snapshot;
  try {
    if (typeof opts.fetchSnapshot !== 'function') {
      throw new Error('no snapshot fetcher');
    }
    snapshot = await opts.fetchSnapshot(path);
  } catch (error) {
    return {
      applied: false,
      reason: 'snapshot_failed',
      snapshotPath: path,
      historyLost: true,
      resumeCursor: '',
      run: null,
      truth: null,
      statusText:
        '事件保留窗口已过期（410），快照 ' + path + ' 读取失败；页面停止时间线，' +
        '不静默续传（错误：' + (error && error.message ? error.message : 'unknown') + '）。',
      timelineNotice: '（持久事件历史已超出保留窗口；快照读取失败，本页没有回放旧帧。）',
    };
  }
  return runStreamSnapshotCorrection(failure, opts.runID, snapshot);
}

/**
 * The (state, phase) -> label/tone/detail/actions mapping.
 *
 * `isSuccess` is true only for a committed `succeeded`; `isFailure` only for
 * `failed`. `finalizing` and `cancelling` are neither. `actions` are the honest
 * things a reader can do next; the page never invents progress.
 */
function runStreamTruth(run, facts) {
  const value = run && typeof run === 'object' ? run : {};
  const runtime = facts && typeof facts === 'object' ? facts : {};
  const state = typeof value.state === 'string' ? value.state : '';
  const phase = typeof value.phase === 'string' ? value.phase : '';
  const errorCode =
    value.error_code === undefined || value.error_code === null ? '' : String(value.error_code);
  const terminal = runStreamIsTerminal(state);
  const label = RUNSTREAM_STATE_LABELS[state] || ('未知状态（' + (state || 'unknown') + '）');
  const truth = {
    state: state,
    phase: phase,
    phaseLabel: RUNSTREAM_PHASE_LABELS[phase] || '',
    stage: terminal ? 'terminal' : (state || 'unknown'),
    label: label,
    tone: '',
    detail: '',
    actions: [],
    terminal: terminal,
    isSuccess: state === 'succeeded',
    isFailure: state === 'failed',
    canCancel: state !== '' && !terminal,
    cancelLabel: state === 'cancelling' ? '再次请求取消（幂等）' : '取消 Run',
    externalWritesNote: '',
  };
  if (state === 'succeeded') {
    truth.tone = 'ok';
    truth.detail =
      '已成功（succeeded）：终态由 Go finalizer 在结果提议与 supervisor 退出证据齐全后提交。';
    truth.actions = ['查看最终产物（output_artifact_id）', '查看受控调用账本'];
    return truth;
  }
  if (state === 'failed') {
    truth.tone = 'bad';
    truth.detail =
      '已失败（failed）' +
      (errorCode ? '，稳定错误码 ' + errorCode : '，服务端未给出 error_code') +
      '；失败是终态，终态不会复活。';
    truth.actions = ['查看失败节点与 error_code', '查看受控调用账本'];
    return truth;
  }
  if (state === 'cancelled') {
    truth.detail =
      '已停止（cancelled）：平台已确认受控执行单元停止；这不表示撤回已经发生的外部写入。';
    truth.externalWritesNote =
      '已停止不等于已撤回：取消只停止受控执行，不撤销已经发生的外部写。';
    truth.actions = ['查看最终快照'];
    return truth;
  }
  if (state === 'cancelling') {
    truth.tone = 'pending';
    truth.stage = 'cancelling';
    truth.detail =
      '正在停止（cancelling）：取消意图已记录，等待受控执行单元退出后由 Go finalizer ' +
      '提交终态；不会撤回已经发生的外部写入。';
    truth.externalWritesNote =
      '不会撤回/撤销已经发生的外部写入；只有 committed cancelled 才证明执行单元停止。';
    truth.actions = ['刷新快照（读取 state/phase）', '再次请求取消（幂等）'];
    return truth;
  }
  if (state === 'needs_reconciliation') {
    truth.tone = 'pending';
    truth.detail =
      '等待人工核对（needs_reconciliation）：存在未明的外部副作用，平台不把它显示为成功或' +
      '失败，也不声称外部写已撤销。';
    truth.externalWritesNote = '未知外部写需要人工核对；页面不会声称已撤销。';
    truth.actions = ['核对未知外部写并提交证据', '刷新快照'];
    return truth;
  }
  if (state === 'recovery_pending') {
    truth.tone = 'pending';
    const reason = String(runtime.recoveryReason || '').trim();
    truth.detail =
      '等待恢复（recovery_pending）' +
      (reason ? '（原因 ' + reason + '）' : '') +
      '：已确认退出但没有可提交的结果；受审计恢复证据齐全前不会重新执行，也不会自动换 token 接管。';
    truth.actions = ['查看恢复原因与证据', '刷新快照'];
    return truth;
  }
  if (state === 'waiting_children') {
    truth.tone = 'pending';
    truth.stage = 'waiting';
    truth.detail =
      '等待子任务（waiting_children，phase=waiting）：子结果与退出证据齐全前父 Run 不会显示成功。';
    truth.actions = ['查看子运行列表（N/M）', '刷新快照'];
    return truth;
  }
  if (state === 'waiting_approval') {
    truth.tone = 'pending';
    truth.stage = 'waiting';
    truth.detail =
      '等待审批决定（waiting_approval，phase=waiting）：决定与退出证据齐全前不会创建恢复任务。';
    truth.actions = ['前往审批队列', '刷新快照'];
    return truth;
  }
  if (state === 'queued') {
    truth.tone = 'pending';
    truth.detail = '尚未被领取（queued）：取消会立即生效（Run 直接 cancelled、job void），不会创建恢复任务。';
    truth.actions = ['取消 Run', '刷新快照'];
    return truth;
  }
  if (state === 'running' && phase === 'finalizing') {
    truth.tone = 'pending';
    truth.stage = 'finalizing';
    truth.label = '正在收尾（finalizing）';
    truth.detail =
      '正在收尾（phase=finalizing，state 仍为 running）：结果提议已保存，仍在等待 supervisor ' +
      '的退出证据（进程/沙箱/数据库排空）；终态提交前这不是成功，也不是失败。';
    truth.actions = [
      '刷新快照（phase 只在 Run 快照里，SSE 的 run.status 没有 phase）',
      '查看事件时间线',
    ];
    return truth;
  }
  if (state === 'running') {
    truth.tone = 'pending';
    truth.label = '执行中（running）';
    truth.detail =
      '正在执行（state=running，phase=' + (phase || 'unknown') +
      '）；页面不伪造进度，终态由 Go finalizer 提交。';
    truth.actions = ['查看事件时间线', '取消 Run'];
    return truth;
  }
  truth.detail = '状态 ' + (state || 'unknown') + '（phase=' + (phase || 'unknown') + '）。';
  truth.actions = ['刷新快照'];
  return truth;
}

/**
 * The one sentence a cancel request may show. "Accepted" is never "stopped" and
 * never "rolled back": cancel stops controlled execution, it does not undo
 * external writes that already happened.
 */
function runStreamCancelAcceptedText(run) {
  const state = run && typeof run.state === 'string' ? run.state : '';
  if (state === 'cancelled') {
    return '已停止（cancelled）：平台已确认受控执行单元停止；不表示撤回已经发生的外部写入。';
  }
  if (state === 'cancelling') {
    return '已接受取消意图（cancelling）：正在停止，等待受控执行单元退出；' +
      '不会撤回已经发生的外部写入。';
  }
  if (state === 'needs_reconciliation') {
    return '已接受取消意图，但 Run 转为 needs_reconciliation（存在未明外部写）：' +
      '平台不会声称外部写已经撤回，等待人工核对。';
  }
  return '已接受取消意图（state=' + (state || 'unknown') +
    '）：正在等待受控执行单元退出，终态以服务端提交为准；不会撤回已经发生的外部写入。';
}

/** The dialogue page's handling sentence; never guesses a version. */
function runStreamHandlingText(run, versionNo) {
  if (!run || typeof run.state !== 'string' || run.state === '') {
    return '尚无处理应用';
  }
  const version = Number.isInteger(versionNo) && versionNo > 0 ? versionNo : 0;
  const prefix = version > 0 ? 'v' + version : '处理版本';
  const state = run.state;
  const phase = typeof run.phase === 'string' ? run.phase : '';
  if (state === 'succeeded') {
    return prefix + ' 已处理';
  }
  if (state === 'failed') {
    return prefix + ' 处理失败';
  }
  if (state === 'cancelled') {
    return prefix + ' 已取消';
  }
  if (state === 'cancelling') {
    return prefix + ' 正在停止（cancelling）：不会撤回已经发生的外部写入，终态以服务端提交为准';
  }
  if (state === 'running' && phase === 'finalizing') {
    return prefix + ' 正在收尾（finalizing）：结果提议已保存，等待 supervisor 退出证据，尚未成功';
  }
  if (state === 'waiting_children') {
    return prefix + ' 等待子任务（waiting_children）：子结果未回前不会显示成功';
  }
  if (state === 'waiting_approval') {
    return prefix + ' 等待审批（waiting_approval）';
  }
  if (state === 'needs_reconciliation') {
    return prefix + ' 等待人工核对（needs_reconciliation）：平台不给出成功结论，等待人工核对';
  }
  if (state === 'recovery_pending') {
    return prefix + ' 等待恢复（recovery_pending）：恢复证据齐全前不会重新执行';
  }
  return prefix + ' 正在处理';
}

/**
 * The dialogue poll rule. A `dispatched` Request is stable but its Run is not,
 * so the page keeps reading the Run until a committed terminal state; without
 * this the page never observes finalizing/succeeded/cancelling.
 */
function runStreamShouldPoll(requestState, runState, attempts, maxAttempts) {
  const state = String(requestState || '');
  if (state === '' || state === 'cancelled' || state === 'failed') {
    return false;
  }
  const limit = Math.max(1, Number(maxAttempts) || 1);
  if ((Number(attempts) || 0) >= limit) {
    return false;
  }
  if (state === 'dispatched') {
    if (!runState) {
      // The Run read has not succeeded yet; keep observing instead of freezing.
      return true;
    }
    return !runStreamIsTerminal(runState);
  }
  return (
    state === 'queued' ||
    state === 'routing' ||
    state === 'awaiting_input' ||
    state === 'awaiting_selection'
  );
}

/**
 * Parent/child waiting: N/M completion, one line, and the hard rule that a
 * parent may only read as success when its own terminal state is succeeded and
 * every child is terminal without a failed/cancelled child.
 */
function runStreamChildSummary(children, parentRun) {
  const list = Array.isArray(children) ? children : [];
  const rows = list.map((child) => {
    const value = child && typeof child === 'object' ? child : {};
    const state = typeof value.state === 'string' ? value.state : '';
    const truth = runStreamTruth({ state: state });
    return {
      id: String(value.id || ''),
      state: state,
      label: truth.label,
      terminal: truth.terminal,
      succeeded: state === 'succeeded',
      failed: state === 'failed' || state === 'cancelled',
      hasResult: state === 'succeeded' && value.output_artifact_id !== undefined &&
        value.output_artifact_id !== null && String(value.output_artifact_id) !== '',
    };
  });
  const total = rows.length;
  const completed = rows.filter((row) => row.terminal).length;
  const waiting = total - completed;
  const failedCount = rows.filter((row) => row.failed).length;
  const parentState = parentRun && typeof parentRun.state === 'string' ? parentRun.state : '';
  const parentTerminal = runStreamIsTerminal(parentState);
  const parentMayShowSuccess =
    parentState === 'succeeded' && waiting === 0 && failedCount === 0;
  let line;
  if (total === 0) {
    line = '没有独立子运行（未委派）。';
  } else if (waiting > 0) {
    line =
      '等待子任务（已完成 ' + completed + '/' + total + '，' + waiting +
      ' 个子运行尚未终态）：父 Run 在子结果未回前不会显示成功。';
  } else {
    line =
      '子任务已全部进入终态（' + completed + '/' + total + '，失败/取消 ' + failedCount +
      ' 个）。';
  }
  return {
    total: total,
    completed: completed,
    waiting: waiting,
    failed: failedCount,
    rows: rows,
    line: line,
    parentState: parentState,
    parentTerminal: parentTerminal,
    parentMayShowSuccess: parentMayShowSuccess,
    parentLine:
      parentState === 'waiting_children'
        ? '父 Run 处于 waiting_children（phase=waiting）。'
        : '',
  };
}

/**
 * Artifact readiness. The contract's Artifact is only
 * `{id,state,media_type,size_bytes}`: it has no relation/purpose and the Run's
 * artifact list only exposes registered, non-deleted artifacts. So the only
 * honest labels come from (artifact.state, Run terminal state, output_artifact_id):
 *
 *   - not `ready`            -> not usable as input, not a final output;
 *   - ready, Run non-terminal -> "unfinished run artifact" (never final);
 *   - ready + succeeded + id == output_artifact_id -> final output;
 *   - anything else          -> node/intermediate artifact, not declared final.
 */
function runStreamArtifactReadout(body, run) {
  const value = run && typeof run === 'object' ? run : {};
  const runState = typeof value.state === 'string' ? value.state : '';
  const terminal = runStreamIsTerminal(runState);
  const succeeded = runState === 'succeeded';
  const finalID =
    value.output_artifact_id === undefined || value.output_artifact_id === null
      ? ''
      : String(value.output_artifact_id);
  const items = body && Array.isArray(body.items) ? body.items : [];
  let finalCount = 0;
  let unfinishedCount = 0;
  let otherCount = 0;
  const described = items.map((artifact) => {
    const item = artifact && typeof artifact === 'object' ? artifact : {};
    const id = String(item.id || '');
    const state = String(item.state || '');
    const ready = state === 'ready';
    const isFinal = ready && succeeded && finalID !== '' && id === finalID;
    const unfinished = ready && !terminal;
    let label;
    if (!ready) {
      label =
        '未就绪（state=' + (state || 'unknown') +
        '）：ready 之前不能作为任务输入，也不是最终产物';
    } else if (unfinished) {
      label =
        '未完成运行产物（运行 state=' + runState +
        ' 未终态）：即使产物已 ready 也不是最终成功产物';
    } else if (isFinal) {
      label = '最终产物（output_artifact_id）：由 succeeded 终态提交';
    } else {
      label = '节点/中间产物（未声明为最终产物）';
    }
    if (isFinal) {
      finalCount += 1;
    } else if (unfinished) {
      unfinishedCount += 1;
    } else {
      otherCount += 1;
    }
    return {
      id: id,
      state: state,
      mediaType: String(item.media_type || ''),
      sizeBytes: Number(item.size_bytes) || 0,
      ready: ready,
      // A ready artifact may be used as input; before ready it must not be.
      usableAsInput: ready,
      finalOutput: isFinal,
      unfinishedRunArtifact: unfinished,
      label: label,
    };
  });
  const summaryText =
    described.length === 0
      ? '本 Run 没有已登记的产物（或当前账户无权读取）。'
      : '本 Run 有 ' + described.length + ' 个已登记产物：最终产物 ' + finalCount +
        ' 个，未完成运行产物 ' + unfinishedCount + ' 个，其它/未就绪 ' + otherCount + ' 个。';
  return {
    items: described,
    count: described.length,
    finalCount: finalCount,
    unfinishedCount: unfinishedCount,
    otherCount: otherCount,
    summaryText: summaryText,
  };
}
