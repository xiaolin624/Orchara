/*
 * F03/T029 runs page.
 *
 * Real clients of two contract endpoints:
 *
 *   GET /api/v1/runs            -> {items: Run[], next_cursor}
 *   GET /api/v1/runs/{id}       -> Run (+ X-Manifest-SHA256, and while an attempt
 *                                  is live X-Run-Attempt-Started-At /
 *                                  X-Run-Lease-Deadline; plus the T034 active
 *                                  clock headers X-Run-Active-Elapsed-Ms /
 *                                  X-Run-Max-Active-Ms / X-Run-Deadline-Exceeded)
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

/** The owner-only artifact list path for one run id (T033). */
function runArtifactsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/artifacts';
}

/** The owner-only controlled-capability ledger path for one run id (T041). */
function runCapabilitiesPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/capabilities';
}

/** The ledger state vocabulary as the owner-facing label. */
function capabilityStateText(state) {
  switch (String(state || '')) {
    case 'succeeded':
      return '成功';
    case 'failed':
      return '失败';
    case 'dispatching':
      return '执行中';
    case 'authorized':
      return '已授权';
    case 'prepared':
      return '已准备';
    case 'awaiting_approval':
      return '待审批';
    case 'unknown':
      return '结果未知';
    default:
      return '未知状态';
  }
}

/** model/tool/connector as the label the ledger panel shows. */
function capabilityKindText(kind) {
  switch (String(kind || '')) {
    case 'model':
      return '模型';
    case 'tool':
      return '工具';
    case 'connector':
      return '连接器';
    default:
      return '能力';
  }
}

/** The risk class as the short owner-facing label (T048 connector risk). */
function capabilityRiskText(risk) {
  switch (String(risk || '')) {
    case 'pure':
      return '无风险';
    case 'read':
      return '读';
    case 'write':
      return '写';
    default:
      return '未知风险';
  }
}

/** A short, non-secret version label (the full id stays in the title attribute). */
function capabilityVersionLabel(version) {
  const text = String(version || '');
  return text.length > 12 ? text.slice(0, 8) + '…' : text;
}

/** Tool-call ids/names only: the ledger read never returns arguments. */
function summarizeToolCalls(calls) {
  const items = Array.isArray(calls) ? calls : [];
  if (items.length === 0) {
    return '无工具调用';
  }
  return items
    .map((call) => {
      const id = String((call && call.id) || '');
      const name = String((call && call.name) || '');
      return id + '(' + name + ')';
    })
    .join('、');
}

/**
 * The owner-facing projection of one capability operation. Only fields the
 * ledger endpoint returns are read; params and receipt bodies are never here.
 */
function describeCapabilityOperation(operation) {
  const value = operation || {};
  const attempts = Number(value.attempts || 0);
  const keys = Array.isArray(value.structured_output_keys)
    ? value.structured_output_keys.map((key) => String(key))
    : [];
  const toolCalls = Array.isArray(value.tool_call_summary)
    ? value.tool_call_summary.map((call) => ({
        id: String((call && call.id) || ''),
        name: String((call && call.name) || ''),
      }))
    : [];
  const errorCode = value.error_code ? String(value.error_code) : '';
  // T048: a connector operation shows only the safe target (registered origin +
  // path prefix) and its risk class. Params, query values, response bodies and
  // the credential never reach the ledger projection.
  const connectorTarget = value.connector_target ? String(value.connector_target) : '';
  const riskClass = value.risk_class ? String(value.risk_class) : '';
  const riskText = capabilityRiskText(riskClass);
  // T045: a call that switched profiles must not look like a seamless primary
  // success. The ledger projection carries the fallback profile and the closed
  // reason code; the cost/tokens are the sum over every physical attempt.
  const fallbackProfileVersionID = value.fallback_profile_version_id
    ? String(value.fallback_profile_version_id)
    : '';
  const fallbackReason = value.fallback_reason ? String(value.fallback_reason) : '';
  const fallbackReasonText = capabilityFallbackReasonText(fallbackReason);
  const chargedAmount =
    value.charged_amount === undefined || value.charged_amount === null
      ? ''
      : String(value.charged_amount);
  const tokensIn = Number(value.tokens_in || 0);
  const tokensOut = Number(value.tokens_out || 0);
  const costText =
    chargedAmount === '' ? '' : '费用 ' + chargedAmount + '，令牌 ' + tokensIn + '/' + tokensOut;
  const fallbackText =
    fallbackProfileVersionID === ''
      ? ''
      : '已切换到 ' + capabilityVersionLabel(fallbackProfileVersionID) +
        '，原因：' + fallbackReasonText;
  return {
    id: String(value.id || ''),
    kindText: capabilityKindText(value.capability_kind),
    version: String(value.capability_version || ''),
    versionLabel: capabilityVersionLabel(value.capability_version),
    state: String(value.state || ''),
    stateText: capabilityStateText(value.state),
    attempts,
    attemptsText: attempts + ' 次',
    attemptsDetailText: attempts + ' 次' + (costText === '' ? '' : ' · ' + costText),
    errorCode,
    errorText: errorCode === '' ? '' : '错误码 ' + errorCode,
    // T048: the safe connector target (origin + approved path prefix) and risk.
    connectorTarget,
    targetText: connectorTarget,
    riskClass,
    riskText,
    isConnector: String(value.capability_kind || '') === 'connector',
    toolCalls,
    toolCallsText: summarizeToolCalls(value.tool_call_summary),
    structuredKeys: keys,
    structuredText: keys.length === 0 ? '无结构化输出' : keys.join('、'),
    stub: value.stub === true,
    stubLabel: value.stub === true ? 'stub（开发桩）' : '真实提供方',
    hasReceipt: Boolean(value.receipt_artifact_id),
    fallbackProfileVersionID,
    fallbackReason,
    fallbackReasonText,
    fallbackText,
    fallback: {
      profileVersionID: fallbackProfileVersionID,
      reason: fallbackReason,
      reasonText: fallbackReasonText,
      text: fallbackText,
    },
    costText,
    tokensIn,
    tokensOut,
    chargedAmount,
  };
}

/** T045's closed fallback reason vocabulary as the owner-facing label. */
function capabilityFallbackReasonText(reason) {
  switch (String(reason || '')) {
    case 'availability':
      return '可用性故障';
    case 'rate_limited':
      return '限流';
    case 'adapter_unavailable':
      return '适配器不可用';
    default:
      return String(reason || '');
  }
}

/** The whole ledger body the panel renders; never echoes another owner's data. */
function describeCapabilityLedger(body) {
  const items = Array.isArray(body && body.items) ? body.items : [];
  const described = items.map(describeCapabilityOperation);
  return {
    items: described,
    count: described.length,
    empty: described.length === 0,
    text:
      described.length === 0
        ? '还没有受控调用（模型调用或已授权的工具调用会出现这里）。'
        : '共有 ' + described.length + ' 次受控调用。',
  };
}

/**
 * F06/T046: the decimal budget projection of one run tree. Amounts are opaque
 * decimal strings from the ledger and are displayed verbatim; the page never
 * re-computes money with binary floats.
 */
function describeRunBudget(body) {
  const budget =
    body && typeof body.budget === 'object' && body.budget !== null ? body.budget : null;
  if (!budget) {
    return {
      known: false,
      overrun: false,
      currency: '',
      limit: '',
      reserved: '',
      charged: '',
      remaining: '',
      text: '预算投影不可用（账本未返回 budget 字段）。',
    };
  }
  const currency = String(budget.currency || '');
  const limit = String(budget.budget_limit || '0.00000000');
  const reserved = String(budget.reserved_amount || '0.00000000');
  const charged = String(budget.charged_amount || '0.00000000');
  const remaining = String(budget.remaining_amount || '0.00000000');
  const overrun = budget.overrun === true;
  return {
    known: true,
    overrun,
    currency,
    limit,
    reserved,
    charged,
    remaining,
    text:
      '预算 ' + limit + ' ' + currency + '：已结算 ' + charged +
      '，预留中 ' + reserved + '，剩余 ' + remaining +
      (overrun ? '（已超支：运行不能被呈现为成功）' : ''),
  };
}

/** One owner-visible artifact download path. */
function artifactDownloadPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID || '')) + '/download';
}

/** The durable event-stream path for one run id (T032). */
function runEventsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/events';
}

/** The owner-only cancel path for one run id (T036). POST, no body. */
function cancelRunPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/cancel';
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

/** Read one non-negative integer header; absent/malformed becomes null, never 0. */
function headerNumber(headers, name) {
  if (!headers || typeof headers.get !== 'function') {
    return null;
  }
  const raw = headers.get(name);
  if (raw === null || raw === undefined || String(raw).trim() === '') {
    return null;
  }
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < 0) {
    return null;
  }
  return value;
}

/** Read the non-contract headers the run detail carries (never a token). */
function runRuntimeFacts(headers) {
  if (!headers || typeof headers.get !== 'function') {
    return {
      manifestSHA256: '', startedAt: '', leaseDeadline: '',
      executionUnit: '', cancelRequestedAt: '',
      activeElapsedMS: null, maxActiveMS: null, deadlineExceeded: false,
      waitingKind: '', waitingOutcome: '', waitingDecisionAt: '',
      waitingExitConfirmedAt: '', resumeJobID: '', resumeJobState: '',
      recoveryReason: '', recoverySince: '',
      recoveryEvidenceAt: '', recoveryEvidenceSHA256: '',
    };
  }
  return {
    manifestSHA256: headers.get('X-Manifest-SHA256') || '',
    startedAt: headers.get('X-Run-Attempt-Started-At') || '',
    leaseDeadline: headers.get('X-Run-Lease-Deadline') || '',
    // T033: diagnostic identity of the live execution unit and the recorded
    // cancel intent (the attempt token itself is never exposed).
    executionUnit: headers.get('X-Run-Execution-Unit') || '',
    cancelRequestedAt: headers.get('X-Run-Cancel-Requested-At') || '',
    // T034: accumulated/projected active time and the frozen limit. A missing
    // max header means NULL (unknown/no deadline), never 0.
    activeElapsedMS: headerNumber(headers, 'X-Run-Active-Elapsed-Ms'),
    maxActiveMS: headerNumber(headers, 'X-Run-Max-Active-Ms'),
    deadlineExceeded: String(headers.get('X-Run-Deadline-Exceeded') || '').toLowerCase() === 'true',
    // T035: the waiting hand-over and the single resume job. The decision
    // reference is never exposed; only the safe outcome and job state.
    waitingKind: headers.get('X-Run-Waiting-Kind') || '',
    waitingOutcome: headers.get('X-Run-Waiting-Outcome') || '',
    waitingDecisionAt: headers.get('X-Run-Waiting-Decided-At') || '',
    waitingExitConfirmedAt: headers.get('X-Run-Waiting-Exit-Confirmed-At') || '',
    resumeJobID: headers.get('X-Run-Resume-Job-ID') || '',
    resumeJobState: headers.get('X-Run-Resume-Job-State') || '',
    // T038: why the Run is parked, since when, and whether the audited operator
    // evidence exists (its hash, never the document or the token).
    recoveryReason: headers.get('X-Run-Recovery-Reason') || '',
    recoverySince: headers.get('X-Run-Recovery-Since') || '',
    recoveryEvidenceAt: headers.get('X-Run-Recovery-Evidence-At') || '',
    recoveryEvidenceSHA256: headers.get('X-Run-Recovery-Evidence-SHA256') || '',
  };
}

// ---------------------------------------------------------------------------
// T034: the cross-attempt active clock (pure helpers, executed by the Node test)
// ---------------------------------------------------------------------------

/** One human second value from milliseconds; never a fabricated estimate. */
function formatActiveSeconds(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || milliseconds === '') {
    return '未知';
  }
  const value = Number(milliseconds);
  if (!Number.isFinite(value) || value < 0) {
    return '未知';
  }
  const seconds = value / 1000;
  return (seconds >= 100 ? seconds.toFixed(0) : seconds.toFixed(1)) + ' 秒';
}

/**
 * The accumulated active clock and the deadline reason. The API exposes one
 * accumulated total per Run (never per-attempt rows), so two attempts render as
 * one clock that the second attempt continues rather than resets. A NULL limit is
 * reported as unknown/no deadline, not as "0 seconds".
 */
function describeActiveClock(run, facts) {
  const runtime = facts || {};
  const elapsed = Number(runtime.activeElapsedMS);
  const max = Number(runtime.maxActiveMS);
  const hasElapsed = runtime.activeElapsedMS !== undefined &&
    runtime.activeElapsedMS !== null && Number.isFinite(elapsed) && elapsed >= 0;
  const hasMax = runtime.maxActiveMS !== undefined &&
    runtime.maxActiveMS !== null && Number.isFinite(max) && max > 0;
  const errorCode = String((run && run.error_code) || '');
  const exceeded = runtime.deadlineExceeded === true || errorCode === 'deadline_exceeded';

  let line;
  if (hasElapsed) {
    line = '跨尝试累计活跃 ' + formatActiveSeconds(elapsed) + '（' + elapsed + ' ms）' +
      (hasMax
        ? ' / 上限 ' + formatActiveSeconds(max) + '（' + max + ' ms）'
        : ' / 上限未知（max_active_ms 为 NULL：未设置/无截止，不等于 0）');
  } else {
    line = '服务器没有返回累计活跃时间（本页不估算）' +
      (hasMax ? '；上限 ' + formatActiveSeconds(max) + '。' : '；上限未知（NULL = 无截止）。');
  }

  let reasonLine;
  if (exceeded) {
    reasonLine = '超时原因：累计活跃已达到/超过上限（' +
      (errorCode || 'deadline_exceeded') + '）。平台已停止接受新结果；执行单元退出后按 ' +
      'deadline_exceeded 失败收尾，且不发布输出产物。';
  } else if (hasMax) {
    reasonLine = '未超时：累计活跃仍在上限内；达到上限会记录 cancel_requested_at 与 ' +
      'error_code=deadline_exceeded，并拒绝新结果。';
  } else {
    reasonLine = '该 Run 没有活跃时间截止（max_active_ms 为 NULL = 未知/未设置，不是 0）。';
  }

  return {
    elapsedMS: hasElapsed ? elapsed : null,
    maxMS: hasMax ? max : null,
    exceeded: exceeded,
    line: line,
    reasonLine: reasonLine,
    oneClockNote: '同一 Run 的多次尝试显示为同一个累计时钟：新 attempt 从累计值继续，不重置，也不会重新获得完整时限。',
  };
}

// ---------------------------------------------------------------------------
// T035: waiting conditions and the single resume (pure helpers, Node-tested)
// ---------------------------------------------------------------------------

/**
 * The waiting/resume panel's honest content.
 *
 * The contract Run schema has no waiting fields, so the server adds non-contract
 * detail headers: the awaited kind, the stored safe outcome, the database-clock
 * decision/exit timestamps and the one resume job's state. The page renders only
 * those facts:
 *
 *   - a decision without the supervisor exit fact is not a resume;
 *   - an exit without a decision is not a resume either;
 *   - the resume job id is shown, its sealed payload and every token are not;
 *   - waiting releases the worker execution slot, never the logical session slot.
 */
function describeWaitingResume(run, facts) {
  const runtime = facts || {};
  const summary = runSummary(run);
  const kind = String(runtime.waitingKind || '');
  const outcome = String(runtime.waitingOutcome || '');
  const decidedAt = String(runtime.waitingDecisionAt || '');
  const exitAt = String(runtime.waitingExitConfirmedAt || '');
  const resumeJobID = String(runtime.resumeJobID || '');
  const resumeJobState = String(runtime.resumeJobState || '');
  const isWaiting = summary.state === 'waiting_approval' || summary.state === 'waiting_children';
  const slotNote = '等待期间释放的是 worker 执行槽；逻辑会话槽与 Run 仍占用到终态' +
    '（等待与恢复排队都不是终态），所以同一会话不会因为等待而释放。';

  if (kind === '') {
    return {
      visible: isWaiting,
      waitingLine: isWaiting
        ? '该 Run 处于 ' + summary.state + '，但服务器没有返回等待条件事实（本页不推测原因）。'
        : '没有等待条件记录（本 Run 未进入 waiting_approval / waiting_children）。',
      resumeLine: '没有恢复任务。',
      slotNote: slotNote,
    };
  }

  const kindLabel = kind === 'approval' ? 'approval（审批决定）' : 'children（子任务结果）';
  let waitingLine = '等待条件：' + kindLabel + '；state=' + summary.state +
    '、phase=' + (summary.phase || 'waiting') + '。';
  if (outcome !== '') {
    waitingLine += ' 已保存的解决结果：' + outcome +
      (decidedAt !== '' ? '（decision_at ' + decidedAt + '）' : '') + '。';
  } else {
    waitingLine += ' 解决结果尚未到达：平台只保存等待条件，不伪造批准或子结果。';
  }
  waitingLine += exitAt !== ''
    ? ' supervisor 已确认旧执行单元退出（' + exitAt + '）。'
    : ' supervisor 尚未确认旧执行单元退出：退出证据与 checkpoint 齐全前不会创建恢复任务。';

  let resumeLine;
  if (resumeJobID !== '') {
    resumeLine = '恢复任务 ' + resumeJobID + '，状态 ' + (resumeJobState || '未知') +
      '。恢复任务只由内部 supervisor 领取；本页不显示 attempt token、恢复 URL 或恢复输入内容。';
  } else if (outcome !== '' && exitAt !== '') {
    resumeLine = '决定与退出均已确认，但尚未提交恢复任务（可能被取消意图/权限/预算/截止时间拒绝）；' +
      '本页不伪造恢复进度。';
  } else {
    resumeLine = '尚未创建恢复任务：决定、退出确认与固定 checkpoint 三者齐全后才会创建唯一恢复任务。';
  }
  return { visible: true, waitingLine: waitingLine, resumeLine: resumeLine, slotNote: slotNote };
}

// ---------------------------------------------------------------------------
// T033: sandbox tool-debug panel (pure helpers, executed by the Node test)
// ---------------------------------------------------------------------------

/**
 * The panel's honest content. The public contract exposes no sandbox-internal
 * status (runsc list, pid, resources): those facts live in the supervisor's
 * `exec --once` report and the recorded exit evidence, which are operator
 * surfaces. The panel therefore renders only what the owner-facing API carries:
 * the live attempt/unit headers, the Run state/phase/error_code, the cancel
 * intent and the downloadable artifacts.
 */
function describeSandboxPanel(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  const terminal = isRunTerminal(summary.state);
  const cancelAt = String(runtime.cancelRequestedAt || '').trim();
  const unit = String(runtime.executionUnit || '').trim();
  const attemptStarted = String(runtime.startedAt || '').trim();
  const lease = String(runtime.leaseDeadline || '').trim();

  let attemptLine = '没有活动执行单元（未领取、已退出或已进入终态）。';
  if (unit !== '' || attemptStarted !== '' || lease !== '') {
    attemptLine = '执行单元 ' + (unit || '（服务器未提供）') +
      '；attempt started_at ' + (attemptStarted || '（服务器未提供）') +
      '；租约至 ' + (lease || '（服务器未提供）') + '。';
  }

  let statusLine;
  if (summary.state === 'queued') {
    statusLine = '尚未被 supervisor 领取；没有沙箱在运行。';
  } else if (terminal) {
    statusLine = '沙箱执行已结束（Run 已进入终态 ' + summary.state + '）。';
  } else if (summary.state === 'cancelling') {
    statusLine = '正在停止：已请求取消，等待执行单元退出；平台不会把停止中的 Run 显示为成功。';
  } else if (summary.phase === 'finalizing') {
    statusLine = '结果提议已保存，仍在等待 supervisor 的退出证据（进程/沙箱/数据库排空）；这不是成功。';
  } else if (cancelAt !== '') {
    statusLine = '正在停止：已请求取消，等待执行单元退出；平台不会把停止中的 Run 显示为成功。';
  } else if (summary.state === 'running') {
    statusLine = '执行中：固定预置工具在只读 rootfs 的 gVisor 沙箱内运行；具体 runsc/pid 状态属于运维报告。';
  } else {
    statusLine = '状态 ' + summary.state + '（phase=' + (summary.phase || 'unknown') + '）。';
  }

  const errorCode = String((run && run.error_code) || '');
  const exitLine = terminal
    ? '终态 ' + summary.state + (errorCode ? '（error_code=' + errorCode + '）' : '') +
      '；资源/退出细节记录在 supervisor 的 exec 报告与退出证据中。'
    : '尚未提交终态' + (errorCode ? '（error_code=' + errorCode + '）' : '') + '。';

  let stopLabel;
  if (cancelAt !== '') {
    stopLabel = terminal
      ? '已停止（cancel_requested_at ' + cancelAt + '；Run 终态 ' + summary.state + '）。'
      : '正在停止（cancel_requested_at ' + cancelAt + '；尚未提交终态）。';
  } else {
    stopLabel = '未请求取消。';
  }

  const cancelNote =
    '取消接口 POST /api/v1/runs/{id}/cancel 由 T036 提供（owner-only、幂等；会话需 CSRF，' +
    'API key 需 application:write）。请求只记录取消意图；执行单元退出后 Run 才提交 cancelled，' +
    '存在未知外部写时转 needs_reconciliation。恢复与核对的 re-drive 属于 T038：页面不会把 ' +
    'recovery_pending 或正在停止显示为已停止，也不伪造撤销。';

  return {
    visible: summary.id !== '',
    unit: unit,
    attemptLine: attemptLine,
    statusLine: statusLine,
    exitLine: exitLine,
    stopLabel: stopLabel,
    cancelNote: cancelNote,
    artifactNote: '产物来自 GET /api/v1/runs/{id}/artifacts（owner-only，四项 Artifact，日志按 purpose 排除）。',
  };
}

/** One artifact line: media type + size + a short id, never an object key. */
function artifactDisplayName(artifact) {
  const value = artifact || {};
  const media = String(value.media_type || 'application/octet-stream');
  let kind = media;
  if (media === 'text/csv') {
    kind = 'CSV';
  } else if (media === 'application/json') {
    kind = 'JSON';
  } else if (media === 'text/plain') {
    kind = 'TXT';
  } else if (media.indexOf('spreadsheetml') >= 0) {
    kind = 'XLSX';
  }
  const size = Number(value.size_bytes);
  const id = String(value.id || '');
  return kind + ' · ' + (Number.isFinite(size) ? size + ' B' : '大小未知') +
    ' · ' + id.slice(0, 8);
}

/** The artifact list's one-line summary plus its ids. */
function describeArtifactList(body) {
  const items = body && Array.isArray(body.items) ? body.items : [];
  if (items.length === 0) {
    return {
      count: 0,
      text: '本 Run 没有已登记的可下载产物（日志不作为数据列出；输入产物在附件页）。',
      ids: [],
    };
  }
  return {
    count: items.length,
    text: '本 Run 有 ' + items.length + ' 个已登记的可下载产物' +
      (body && body.next_cursor ? '（还有下一页）。' : '。'),
    ids: items.map((item) => String(item.id || '')),
  };
}

// ---------------------------------------------------------------------------
// T036: cancel intent, stopping vs stopped, and terminal stability
// ---------------------------------------------------------------------------

/** A Run can be cancelled until its terminal state is committed. */
function canCancelRun(run) {
  if (!run || typeof run.state !== 'string' || run.state === '') {
    return false;
  }
  return !isRunTerminal(run.state);
}

/** The button label: cancelling is idempotent, so a second request is allowed. */
function cancelButtonLabel(run) {
  return String((run && run.state) || '') === 'cancelling'
    ? '再次请求取消（幂等）'
    : '取消 Run';
}

/**
 * The cancel panel's honest content. "Accepted" is never "stopped": a cancel
 * request only records the intent (or immediately cancels a Run with no live
 * attempt); the terminal state is committed by the Go finalizer after the
 * supervisor's exit evidence. The terminal reason is the Run state plus its
 * error_code, never an invented explanation.
 */
function describeRunCancel(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  const cancelAt = String(runtime.cancelRequestedAt || '').trim();
  const errorCode = String((run && run.error_code) || '');
  const label = cancelButtonLabel(run);
  if (summary.state === 'cancelled') {
    return {
      visible: true,
      terminal: true,
      line: '已停止（cancelled）：平台已确认受控执行单元停止' +
        (cancelAt ? '（取消请求于 ' + cancelAt + '）' : '') +
        '；这不表示撤回已经发送的外部写入。',
      reasonLine: '终态原因：cancelled' + (errorCode ? '（error_code=' + errorCode + '）' : ''),
      buttonLabel: label,
    };
  }
  if (summary.state === 'succeeded' || summary.state === 'failed') {
    return {
      visible: true,
      terminal: true,
      line: (summary.state === 'succeeded' ? '已成功结束（succeeded）' : '已失败结束（failed）') +
        '；终态之后迟到的结果/事件不会改变本页。',
      reasonLine: '终态原因：' + summary.state +
        (errorCode ? '（error_code=' + errorCode + '）' : ''),
      buttonLabel: label,
    };
  }
  if (summary.state === 'cancelling') {
    return {
      visible: true,
      terminal: false,
      line: '正在停止（cancelling）：取消意图已记录' +
        (cancelAt ? '（' + cancelAt + '）' : '') +
        '；等待受控执行单元退出，终态提交前本页不会显示成功。',
      reasonLine: '取消意图：已记录；终态由 Go finalizer 在执行单元退出后提交。',
      buttonLabel: label,
    };
  }
  if (summary.state === 'queued') {
    return {
      visible: true,
      terminal: false,
      line: '尚未被领取（queued）：取消会立即生效（Run 直接 cancelled、job void），不会创建恢复任务。',
      reasonLine: '取消原因：排队阶段取消不执行任何单元。',
      buttonLabel: label,
    };
  }
  return {
    visible: true,
    terminal: false,
    line: cancelAt !== ''
      ? '正在停止：已请求取消（' + cancelAt + '），等待执行单元退出。'
      : '未请求取消；取消后本页会显示“正在停止”，终态以服务端提交为准。',
    reasonLine: cancelAt !== '' ? '取消意图：已记录（尚未提交终态）。' : '取消意图：无。',
    buttonLabel: label,
  };
}

// ---------------------------------------------------------------------------
// T038: conservative recovery (parked, never taken over automatically)
// ---------------------------------------------------------------------------

/**
 * The recovery panel's honest content.
 *
 * T038 parks a Run whose live attempt lease expired beyond the grace window in
 * `recovery_pending` (reason `lease_expired`), and T031 parks an exit without a
 * result (`exit_without_result`). Neither is retried automatically: claim selects
 * only `queued` Runs, the job stays `held`, the execution slot stays allocated and
 * no attempt token is ever swapped. A parked Run only leaves this state through
 * the operator command after audited evidence is recorded, so the page shows the
 * reason, since when, whether evidence exists, and the operator command — it
 * never renders a "resume" button.
 */
function describeRunRecovery(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  const reason = String(runtime.recoveryReason || '').trim();
  const since = String(runtime.recoverySince || '').trim();
  const evidenceAt = String(runtime.recoveryEvidenceAt || '').trim();
  const evidenceSHA = String(runtime.recoveryEvidenceSHA256 || '').trim();
  const parked = summary.state === 'recovery_pending';
  const evidenceLine = evidenceAt !== ''
    ? '已记录受审计恢复证据（recorded_at ' + evidenceAt +
      (evidenceSHA ? '，sha256 ' + evidenceSHA : '') + '）。'
    : '尚无受审计恢复证据：旧执行单元、沙箱销毁与数据库在途写的证明尚未提交。';
  const commands = '运维命令：orchara-admin runs sweep-leases --grace 60s [--dry-run]（只挂起）；' +
    'orchara-admin runs recover --run ' + (summary.id || '<run-id>') +
    ' --evidence <file> [--check-only]（证据齐全后由单个事务重新排队）。';
  const noTakeover = '平台不会自动换 token、不会自动重排，也不会创建第二个执行者：' +
    'claim 只选 queued Run，recovery_pending 必须先由受审计证据转回 queued。';

  if (isRunTerminal(summary.state)) {
    // A terminal Run never flips: recovery evidence is history here, not a way
    // back into execution.
    return {
      visible: reason !== '' || evidenceAt !== '',
      line: '终态 ' + summary.state + ' 不会被恢复改变（终态不复活）。' + evidenceLine,
      commandLine: '终态 Run 不接受恢复命令。',
    };
  }
  if (!parked) {
    return {
      visible: evidenceAt !== '',
      line: evidenceAt !== ''
        ? '恢复已完成：' + evidenceLine + ' Run 已重新排队，旧 attempt 已 finalize、旧 token 已失效。'
        : '',
      commandLine: evidenceAt !== '' ? '' : commands,
    };
  }
  let reasonLabel;
  if (reason === 'lease_expired') {
    reasonLabel = 'lease_expired（租约超期；执行宿主可能失联，旧单元是否仍在运行未知）';
  } else if (reason === 'exit_without_result') {
    reasonLabel = 'exit_without_result（supervisor 已确认退出，但没有可提交的结果）';
  } else {
    reasonLabel = reason !== ''
      ? reason
      : '服务器未返回恢复原因（本页不推测）';
  }
  return {
    visible: true,
    line: 'Run 挂起在 recovery_pending：原因 ' + reasonLabel +
      (since !== '' ? '，挂起于 ' + since : '') +
      '。job 保持 held、执行槽仍占用、旧 attempt token 未被替换。' + evidenceLine,
    commandLine: commands + ' ' + noTakeover,
  };
}

/**
 * Merge one server Run snapshot with what the page already displayed.
 *
 * A committed terminal state is final: a late/stale poll response or a late SSE
 * message must never flip the page back to a non-terminal state (STATE-MACHINES
 * §2 "再次运行创建新 Run，不能复活终态"). Non-terminal snapshots are replaced
 * normally so the page keeps observing progress.
 */
function mergeRunSnapshot(previous, next) {
  if (!next || typeof next !== 'object' || typeof next.state !== 'string') {
    return previous || next;
  }
  if (previous && typeof previous.state === 'string' && isRunTerminal(previous.state)) {
    return previous;
  }
  return next;
}

/**
 * One honest sentence about a run. Terminal states say so; a queued run says it
 * has not been claimed; a running run quotes the lease when the server supplied
 * it and otherwise says the lease facts are unavailable instead of inventing
 * progress.
 *
 * T031 adds the finalizing stage: a worker result is only a proposal, so while
 * phase=finalizing the page says the platform is waiting for supervisor exit
 * evidence and that this is not a success yet. T036 adds cancelling: the intent
 * exists but the Run is not stopped until the finalizer commits it.
 */
function describeRunState(run, facts) {
  const summary = runSummary(run);
  const runtime = facts || {};
  if (isRunTerminal(summary.state)) {
    return '已进入终态 ' + summary.state + '（终态由 Go finalizer 在结果提议与 supervisor 退出证据齐全后提交）。';
  }
  if (summary.state === 'cancelling') {
    return '正在停止（cancelling）：取消意图已记录，平台已拒绝新的结果/登记/上传/审批调用，' +
      '等待执行单元退出后由 finalizer 提交 cancelled（或存在未知外部写时转 needs_reconciliation）。';
  }
  if (summary.state === 'queued') {
    return '尚未被领取（queued）；领取、心跳与退出证据属于 F03 内部 supervisor 协议。';
  }
  if (summary.state === 'recovery_pending') {
    const reason = String(runtime.recoveryReason || '').trim();
    const since = String(runtime.recoverySince || '').trim();
    return '挂起在 recovery_pending' +
      (reason !== '' ? '（原因 ' + reason + (since !== '' ? '，挂起于 ' + since : '') + '）' : '') +
      '：旧执行单元未证明退出前不会重新执行，也不会自动换 token 接管；' +
      '只有运维提交受审计证据后才会重新排队。';
  }
  if (summary.state === 'needs_reconciliation') {
    return 'Run 等待人工核对（needs_reconciliation）：存在未明的外部副作用，平台不把它显示为成功或失败。';
  }
  if (summary.state === 'waiting_approval' || summary.state === 'waiting_children') {
    const awaited = summary.state === 'waiting_approval' ? '审批决定' : '子任务结果';
    return '等待' + awaited + '（' + summary.state + '，phase=waiting）：worker 已在固定 checkpoint 停止；' +
      'supervisor 确认旧执行单元退出后释放执行槽，决定/子结果与退出证据齐全前不会创建恢复任务。';
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
  if (summary.state === 'waiting_approval' || summary.state === 'waiting_children') {
    // Waiting is a real stage between executing and the eventual terminal state;
    // a resume returns the Run to executing/queued.
    return { stages: ['executing', 'waiting', 'terminal'], current: 'waiting' };
  }
  if (summary.state === 'cancelling') {
    // The cancel intent is its own stopping stage: still non-terminal.
    return { stages: ['executing', 'cancelling', 'terminal'], current: 'cancelling' };
  }
  if (summary.state === 'recovery_pending') {
    // Parked awaiting audited evidence: not executing, not terminal. A successful
    // recovery returns the Run to queued (a new attempt), never to "success".
    return { stages: ['executing', 'recovery_pending', 'terminal'], current: 'recovery_pending' };
  }
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

// T036: the last Run snapshot rendered. A committed terminal state is never
// replaced by a late/stale non-terminal message (mergeRunSnapshot).
let displayedRun = null;
let displayedRunID = '';

function showRunDetail(run, facts) {
  const merged = mergeRunSnapshot(displayedRun, run);
  if (!merged) {
    return null;
  }
  displayedRun = merged;
  const summary = runSummary(merged);
  document.getElementById('detail-id').textContent = summary.id;
  document.getElementById('detail-state').textContent = summary.state;
  document.getElementById('detail-phase').textContent = summary.phase || '（未提供）';
  document.getElementById('detail-kind').textContent = summary.kind;
  document.getElementById('detail-revision').textContent = summary.revision;
  document.getElementById('detail-snapshot').textContent = summary.executionSnapshotID;
  document.getElementById('detail-lifecycle').textContent = lifecycleText(merged);
  document.getElementById('detail-manifest').textContent = facts.manifestSHA256 || '（响应头未提供）';
  document.getElementById('detail-lease').textContent =
    summary.state === 'running' && facts.leaseDeadline
      ? 'started_at ' + (facts.startedAt || '（未提供）') + ' / lease_deadline ' + facts.leaseDeadline
      : '（没有可显示的租约事实）';
  document.getElementById('detail-note').textContent = describeRunState(merged, facts);
  const active = describeActiveClock(merged, facts);
  document.getElementById('detail-active').textContent = active.line + ' ' + active.oneClockNote;
  document.getElementById('detail-deadline').textContent = active.reasonLine;
  const waiting = describeWaitingResume(merged, facts);
  document.getElementById('detail-waiting').textContent = waiting.waitingLine;
  document.getElementById('detail-resume').textContent = waiting.resumeLine + ' ' + waiting.slotNote;
  const cancel = describeRunCancel(merged, facts);
  const cancelLine = document.getElementById('detail-cancel');
  if (cancelLine) {
    cancelLine.textContent = cancel.line + ' ' + cancel.reasonLine;
  }
  // T038: the parked reason/since/evidence and the operator command. There is
  // deliberately no resume button: only the audited command can re-queue.
  const recovery = describeRunRecovery(merged, facts);
  const recoveryLine = document.getElementById('detail-recovery');
  if (recoveryLine) {
    recoveryLine.textContent = recovery.visible
      ? recovery.line + ' ' + recovery.commandLine
      : '没有恢复事实（本 Run 未挂起，也没有受审计恢复证据）。';
  }
  const cancelButton = document.getElementById('detail-cancel-button');
  if (cancelButton) {
    cancelButton.hidden = !canCancelRun(merged);
    cancelButton.textContent = cancel.buttonLabel;
    cancelButton.dataset.runId = summary.id;
  }
  renderSandboxPanel(merged, facts);
  document.getElementById('run-detail').hidden = false;
  return merged;
}

// T033: the sandbox panel and the owner-only artifact list.
function renderSandboxPanel(run, facts) {
  const panel = document.getElementById('sandbox-panel');
  if (!panel) {
    return;
  }
  const content = describeSandboxPanel(run, facts || {});
  panel.hidden = !content.visible;
  document.getElementById('sandbox-unit').textContent =
    content.unit || '（没有活动的执行单元）';
  document.getElementById('sandbox-attempt').textContent = content.attemptLine;
  document.getElementById('sandbox-status').textContent = content.statusLine;
  document.getElementById('sandbox-exit').textContent = content.exitLine;
  document.getElementById('sandbox-stop').textContent = content.stopLabel;
  document.getElementById('sandbox-cancel-note').textContent = content.cancelNote;
}

function renderArtifactItem(artifact) {
  const item = document.createElement('li');
  const label = document.createElement('span');
  label.textContent = artifactDisplayName(artifact);
  const download = document.createElement('button');
  download.type = 'button';
  download.textContent = '下载';
  download.addEventListener('click', () => {
    downloadArtifact(String(artifact.id || ''));
  });
  item.append(label, ' ', download);
  document.getElementById('sandbox-artifacts').appendChild(item);
}

function sandboxArtifactsStatus(message, kind) {
  const element = document.getElementById('sandbox-artifacts-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

async function downloadArtifact(artifactID) {
  try {
    const ticket = await api(artifactDownloadPath(artifactID));
    if (!ticket.url) {
      throw new Error('下载响应没有 URL');
    }
    window.open(ticket.url, '_blank', 'noopener');
  } catch (error) {
    sandboxArtifactsStatus(
      '下载失败 [' + (error && error.code ? error.code : 'error') + '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
  }
}

async function loadRunArtifacts(runId) {
  const list = document.getElementById('sandbox-artifacts');
  if (!list) {
    return;
  }
  list.replaceChildren();
  sandboxArtifactsStatus('正在加载可下载产物…', 'pending');
  try {
    const body = await api(runArtifactsPath(runId));
    const summary = describeArtifactList(body);
    sandboxArtifactsStatus(summary.text, summary.count ? 'ok' : '');
    for (const item of body.items || []) {
      renderArtifactItem(item);
    }
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    sandboxArtifactsStatus(
      '产物列表不可用 [' + (error && error.code ? error.code : 'error') + '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
  }
}

function capabilityStatus(message, kind) {
  const element = document.getElementById('capability-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

// F06/T046: the same status pattern for the decimal budget line. An overrun is
// a 'bad' status, never a neutral one.
function budgetStatus(message, kind) {
  const element = document.getElementById('budget-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function renderCapabilityRow(operation) {
  const row = document.createElement('tr');
  const cells = [
    operation.kindText,
    operation.versionLabel,
    operation.stateText,
    operation.attemptsDetailText || operation.attemptsText,
    // T048: the safe connector target, never the params or the credential.
    operation.targetText || '—',
    operation.structuredText,
    operation.toolCallsText,
    '风险 ' + operation.riskText + ' · ' +
      operation.stubLabel +
      (operation.fallbackText ? ' · ' + operation.fallbackText : '') +
      (operation.errorText ? ' · ' + operation.errorText : ''),
  ];
  for (const text of cells) {
    const cell = document.createElement('td');
    cell.textContent = text;
    row.appendChild(cell);
  }
  if (operation.version) {
    row.title = operation.version;
  }
  return row;
}

// T041: the owner-only controlled-capability ledger. It shows identity, state,
// attempt count, a structured-output key list and tool-call ids/names; it never
// shows params, arguments or a receipt body.
async function loadCapabilityLedger(runId) {
  const rows = document.getElementById('capability-rows');
  if (!rows) {
    return;
  }
  rows.replaceChildren();
  capabilityStatus('正在加载受控调用账本…', 'pending');
  try {
    const body = await api(runCapabilitiesPath(runId));
    lastCapabilityLedger = body && typeof body === 'object' ? body : null;
    const ledger = describeCapabilityLedger(body);
    capabilityStatus(ledger.text, ledger.empty ? '' : 'ok');
    const budget = describeRunBudget(body);
    budgetStatus(budget.text, budget.overrun ? 'bad' : budget.known ? 'ok' : '');
    for (const operation of ledger.items) {
      rows.appendChild(renderCapabilityRow(operation));
    }
    renderAgentNodePanel();
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    capabilityStatus(
      '受控调用账本不可用 [' + (error && error.code ? error.code : 'error') + '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
    budgetStatus('预算投影不可用（账本请求失败）。', 'bad');
  }
}

function cancelDetailStatus(message, kind) {  const element = document.getElementById('detail-cancel-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

/**
 * Request cancel for the selected Run.
 *
 * The endpoint is owner-only and idempotent. A queued Run comes back cancelled;
 * a Run with a live unit comes back cancelling (a real "stopping" state), and the
 * page then reloads the detail (which starts/stops its poll loop) instead of
 * guessing the outcome. The 202 itself is never rendered as "stopped".
 */
async function requestCancel(runId) {
  const button = document.getElementById('detail-cancel-button');
  cancelDetailStatus('正在提交取消请求…', 'pending');
  if (button) {
    button.disabled = true;
  }
  try {
    const token = await csrf();
    const run = await api(cancelRunPath(runId), { method: 'POST', csrf: token });
    displayedRun = mergeRunSnapshot(displayedRun, run);
    cancelDetailStatus(
      run.state === 'cancelled'
        ? '已停止（cancelled）：任务已进入终态。'
        : '已接受取消意图（' + run.state + '）：等待执行单元退出，页面会轮询到终态。',
      'ok',
    );
    await loadRunDetail(runId);
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    cancelDetailStatus(
      '取消失败 [' + (error && error.code ? error.code : 'error') + '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
  } finally {
    if (button) {
      button.disabled = false;
    }
  }
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
      recordNodeStatus(event);
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
  if (displayedRunID !== runId) {
    // The user selected another Run while this loop was sleeping: the stale poll
    // must not overwrite the new selection.
    return;
  }
  const result = await apiFull(runDetailPath(runId));
  if (displayedRunID !== runId) {
    return;
  }
  const facts = runRuntimeFacts(result.headers);
  const merged = showRunDetail(result.payload, facts);
  // A committed terminal state is never re-polled even if a stale snapshot says
  // otherwise: the page stops on the merged (monotonic) state.
  if (merged && shouldPollRun(merged)) {
    return pollRunDetail(runId, polls + 1);
  }
  await loadRunArtifacts(runId);
  await loadCapabilityLedger(runId);
  await loadNodeResults(runId);
  runsStatus('Run ' + runId + ' 已进入终态 ' + (merged && merged.state ? merged.state : '') + '。', 'ok');
}

async function loadRunDetail(runId) {
  runsStatus('正在加载 Run ' + runId + ' 的详情…', 'pending');
  try {
    await requireLogin();
    if (displayedRunID !== runId) {
      // A different Run starts a fresh monotonic snapshot; the old terminal state
      // must not mask the newly selected Run.
      displayedRunID = runId;
      displayedRun = null;
      nodeStatusByID = {};
      lastNodeResults = null;
      lastCapabilityLedger = null;
    }
    const result = await apiFull(runDetailPath(runId));
    const facts = runRuntimeFacts(result.headers);
    const merged = showRunDetail(result.payload, facts);
    loadRunArtifacts(runId);
    loadCapabilityLedger(runId);
    loadNodeResults(runId);
    beginRunTimeline(runId, {
      listID: 'detail-timeline',
      statusID: 'detail-timeline-status',
      previewID: 'detail-preview',
    });
    // T044: the temporary model preview (in-process, non-persistent). It closes
    // itself on final/error and never replaces the persistent run snapshot.
    if (typeof beginModelPreview === 'function') {
      beginModelPreview(runId, { panelID: 'detail-preview' });
    }
    if (merged && shouldPollRun(merged)) {
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

// ---------------------------------------------------------------------------
// F04A/T042: the per-node result panel (data / files / capped logs).
//
// The panel reads the owner-only non-contract node-results projection and then
// the existing artifact content route; it never invents a result. A node with no
// rows after the Run reached a terminal state is shown as "no result", and a
// failed node is shown as "failed" — the two are not conflated. Logs are capped
// in the page as well as on the server, and never rendered as data.
// ---------------------------------------------------------------------------
let nodeStatusByID = {};
let lastNodeResults = null;

function runNodeResultsPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/node-results';
}

function artifactContentPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID || '')) + '/content';
}

function nodeResultKindText(kind) {
  if (kind === 'data') {
    return 'data';
  }
  if (kind === 'file') {
    return '文件';
  }
  if (kind === 'log') {
    return '日志';
  }
  return String(kind || '未知');
}

function nodeStatusText(state) {
  switch (String(state || '')) {
    case 'pending':
      return '等待';
    case 'running':
      return '运行中';
    case 'succeeded':
      return '成功';
    case 'failed':
      return '失败';
    case 'skipped':
      return '已跳过';
    case 'no_result':
      return '无结果';
    default:
      return '未知';
  }
}

function formatBytes(count) {
  const size = Number(count);
  if (!Number.isFinite(size) || size < 0) {
    return '0 B';
  }
  if (size < 1024) {
    return String(Math.floor(size)) + ' B';
  }
  return (size / 1024).toFixed(1) + ' KiB';
}

/** Cap one log view exactly like the server caps the artifact. */
function capLogText(text, limit) {
  const source = typeof text === 'string' ? text : '';
  const cap = Number.isSafeInteger(limit) && limit > 0 ? limit : 4096;
  if (source.length <= cap) {
    return { text: source, truncated: false, notice: '' };
  }
  return {
    text: source.slice(0, cap),
    truncated: true,
    notice:
      '日志已截断：只显示前 ' +
      cap +
      ' 个字符（共 ' +
      source.length +
      ' 个字符，完整内容以服务端上限为准）。',
  };
}

function prettyJSONText(raw) {
  const source = typeof raw === 'string' ? raw : '';
  try {
    return JSON.stringify(JSON.parse(source), null, 2);
  } catch (error) {
    return source;
  }
}

function describeNodeResultItem(item) {
  const entry = item && typeof item === 'object' ? item : {};
  const artifactID = String(entry.artifact_id || '');
  const label = String(entry.label || '');
  const mediaType = String(entry.media_type || '');
  const sizeBytes = Number(entry.size_bytes) || 0;
  return {
    artifactID: artifactID,
    kind: String(entry.kind || ''),
    kindText: nodeResultKindText(entry.kind),
    label: label,
    mediaType: mediaType,
    sizeBytes: sizeBytes,
    sizeText: formatBytes(sizeBytes),
    sha256: String(entry.sha256 || ''),
    line:
      nodeResultKindText(entry.kind) +
      ' · ' +
      label +
      ' · ' +
      (mediaType || '未知类型') +
      ' · ' +
      formatBytes(sizeBytes),
    downloadPath: artifactDownloadPath(artifactID),
    contentPath: artifactContentPath(artifactID),
  };
}

/**
 * Group the node-results projection by node and derive an honest status.
 *
 * `statuses` is the map filled from committed node.status events; when it has no
 * entry, a node with rows is a success (the rows only exist after the server
 * registered them), a failed/cancelled Run's silent node is failed, and a
 * succeeded Run's silent node is "no result" rather than invented output.
 */
function describeNodeResults(body, statuses, runState) {
  const items = body && Array.isArray(body.items) ? body.items : [];
  const known = statuses && typeof statuses === 'object' ? statuses : {};
  const groups = [];
  const byNode = new Map();
  for (const item of items) {
    const nodeID = String((item && item.node_id) || '');
    if (!byNode.has(nodeID)) {
      const group = { nodeID: nodeID, data: [], files: [], logs: [], count: 0 };
      byNode.set(nodeID, group);
      groups.push(group);
    }
    const group = byNode.get(nodeID);
    const described = describeNodeResultItem(item);
    group.count += 1;
    if (described.kind === 'data') {
      group.data.push(described);
    } else if (described.kind === 'file') {
      group.files.push(described);
    } else if (described.kind === 'log') {
      group.logs.push(described);
    }
  }
  // Nodes observed only through node.status events still appear, so a failed
  // node (no rows, by definition) is visible as "failed" instead of vanishing.
  for (const nodeID of Object.keys(known)) {
    if (!byNode.has(nodeID)) {
      const group = { nodeID: nodeID, data: [], files: [], logs: [], count: 0 };
      byNode.set(nodeID, group);
      groups.push(group);
    }
  }
  const state = String(runState || '');
  let dataCount = 0;
  let fileCount = 0;
  let logCount = 0;
  for (const group of groups) {
    let status = String(known[group.nodeID] || '');
    if (status === '') {
      if (group.count > 0) {
        status = 'succeeded';
      } else if (state === 'failed' || state === 'cancelled') {
        status = 'failed';
      } else if (state === 'succeeded') {
        status = 'no_result';
      }
    }
    group.status = status;
    group.statusText = nodeStatusText(status);
    group.resultText = group.count > 0 ? '有结果' : '无结果';
    dataCount += group.data.length;
    fileCount += group.files.length;
    logCount += group.logs.length;
  }
  const empty = groups.length === 0;
  return {
    visible: true,
    empty: empty,
    nodeCount: groups.length,
    dataCount: dataCount,
    fileCount: fileCount,
    logCount: logCount,
    nodes: groups,
    text: empty
      ? '该 Run 还没有节点结果（旧版单 python 执行路径不产生节点结果）。'
      : groups.length +
        ' 个节点 · data ' +
        dataCount +
        ' · 文件 ' +
        fileCount +
        ' · 日志 ' +
        logCount,
  };
}

function nodeResultsStatus(message, kind) {
  const element = document.getElementById('node-results-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

// ---------------------------------------------------------------------------
// F04A/T043: the agent node summary.
//
// The node-results read has no node-type field and the T041 ledger read has no
// logical key (adding one would change the frozen T041 projection), so the page
// links them through two closed conventions the executor documents:
//   - the agent node's tool log rows are labelled "tool-log-<call id>";
//   - a failed agent node has a node.status=failed entry but no rows, while the
//     ledger shows model operations.
// The summary never shows params or receipt bodies; tool-call ids/names and the
// per-operation state are already the non-secret ledger projection.
// ---------------------------------------------------------------------------
let lastCapabilityLedger = null;

function agentNodeIDOf(nodeBody, ledgerBody, statuses) {
  const items = nodeBody && Array.isArray(nodeBody.items) ? nodeBody.items : [];
  const toolLog = items.find(
    (item) => item && item.kind === 'log' && String(item.label || '').indexOf('tool-log-') === 0,
  );
  if (toolLog) {
    return String(toolLog.node_id || '');
  }
  const ledger = describeCapabilityLedger(ledgerBody);
  const hasModelOperation = ledger.items.some((item) => item.kindText === '模型');
  if (hasModelOperation) {
    const known = statuses && typeof statuses === 'object' ? statuses : {};
    const withRows = new Set(items.map((item) => String((item && item.node_id) || '')));
    for (const nodeID of Object.keys(known)) {
      if (known[nodeID] === 'failed' && !withRows.has(nodeID)) {
        return nodeID;
      }
    }
  }
  return '';
}

/**
 * Derive the agent panel from the node-results projection, the capability ledger,
 * the committed node.status map and the Run's state/error code.
 */
function describeAgentNode(nodeBody, ledgerBody, statuses, runState, runErrorCode) {
  const nodeID = agentNodeIDOf(nodeBody, ledgerBody, statuses);
  const items = nodeBody && Array.isArray(nodeBody.items) ? nodeBody.items : [];
  const nodeItems = nodeID === '' ? [] : items.filter((item) => String(item.node_id || '') === nodeID);
  const data = nodeItems.filter((item) => item.kind === 'data').map(describeNodeResultItem);
  const files = nodeItems.filter((item) => item.kind === 'file').map(describeNodeResultItem);
  const logs = nodeItems.filter((item) => item.kind === 'log').map(describeNodeResultItem);
  const ledger = describeCapabilityLedger(ledgerBody);
  const models = ledger.items.filter((item) => item.kindText === '模型');
  const tools = ledger.items.filter((item) => item.kindText === '工具');
  const toolCalls = [];
  for (const model of models) {
    for (const call of model.toolCalls) {
      toolCalls.push(call);
    }
  }
  const known = statuses && typeof statuses === 'object' ? statuses : {};
  let status = nodeID === '' ? '' : String(known[nodeID] || '');
  if (status === '' && nodeItems.length > 0) {
    status = 'succeeded';
  }
  if (status === '' && nodeID !== '' && String(runState || '') === 'failed') {
    status = 'failed';
  }
  const errorCode = String(runErrorCode || '');
  const present = nodeID !== '';
  const schemaFailure = present && status === 'failed' && errorCode === 'node_output_invalid';
  const toolStateText =
    tools.length === 0
      ? '还没有工具操作'
      : tools.map((item) => item.stateText + ' · ' + item.attemptsText).join('；');
  return {
    visible: true,
    present,
    nodeID,
    status,
    statusText: present ? nodeStatusText(status) : '无 Agent 节点',
    turns: models.length,
    turnsText: present ? String(models.length) + ' 轮模型调用' : '无模型调用',
    toolCalls,
    toolCallsText:
      toolCalls.length === 0
        ? '无工具调用'
        : toolCalls.map((call) => call.id + '(' + call.name + ')').join('、'),
    toolOperations: tools,
    toolStateText,
    stub: models.some((item) => item.stub),
    dataCount: data.length,
    fileCount: files.length,
    logCount: logs.length,
    data,
    files,
    logs,
    errorCode,
    schemaFailure,
    errorText: !present
      ? ''
      : schemaFailure
        ? '节点 ' + nodeID + ' 最终输出未通过 output_schema（node_output_invalid）；定位指针见 supervisor 运行报告。'
        : errorCode !== ''
          ? '节点 ' + nodeID + ' 失败：' + errorCode
          : '',
    text: !present
      ? '该 Run 没有 Agent 节点（旧版单 python 路径不产生 Agent 摘要）。'
      : 'Agent 节点 ' +
        nodeID +
        ' · ' +
        nodeStatusText(status) +
        ' · ' +
        models.length +
        ' 轮模型调用 · ' +
        (toolCalls.length === 0 ? '无工具调用' : '工具调用 ' + toolCalls.length + ' 次') +
        (tools.length === 0 ? '' : ' · 工具操作 ' + tools.length + ' 次'),
  };
}

function agentNodeStatus(message, kind) {
  const element = document.getElementById('agent-node-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

/** The persisted T047 context-trace file row of the agent node, if any. */
function contextTraceRowOf(nodeBody, ledgerBody, statuses) {
  const items = nodeBody && Array.isArray(nodeBody.items) ? nodeBody.items : [];
  let nodeID = agentNodeIDOf(nodeBody, ledgerBody, statuses);
  if (nodeID === '') {
    // An agent node may declare no tools (so it has no tool-log row) and still
    // persist a context trace; the trace label itself is enough to locate it.
    const traceRow = items.find(
      (item) => item && item.kind === 'file' && String(item.label || '') === 'context-trace',
    );
    if (traceRow) {
      nodeID = String(traceRow.node_id || '');
    }
  }
  if (nodeID === '') {
    return null;
  }
  return (
    items.find(
      (item) =>
        String((item && item.node_id) || '') === nodeID &&
        item &&
        item.kind === 'file' &&
        String(item.label || '') === 'context-trace',
    ) || null
  );
}

/** The fixed truncation-reason labels of the T047 trace document. */
function contextTruncationReasonText(reason) {
  switch (String(reason || '')) {
    case 'low_relevance_history':
      return '低相关历史';
    case 'display_only_summary':
      return '仅展示摘要';
    case 'old_tool_detail':
      return '旧工具结果细节';
    case 'verbose_upstream_text':
      return '冗长上游文本';
    case 'optional_memory':
      return '可选个人记忆';
    case 'protected_minimum':
      return '受保护最小输入';
    default:
      return String(reason || '未知原因');
  }
}

/**
 * Pure projection of the persisted T047 trace document. It renders identifiers,
 * truncation reasons, protected key paths and token counts only: the trace has
 * no message text, memory text or credential, and this function never invents
 * one.
 */
function describeContextTrace(document_) {
  const value = document_ && typeof document_ === 'object' ? document_ : {};
  const turns = Array.isArray(value.turns) ? value.turns : [];
  const sources = [];
  const truncations = [];
  const protectedPaths = [];
  let budgetTokens = 0;
  let reservedOutputTokens = 0;
  let estimatedTokens = 0;
  let memoryState = 'not_run';
  for (const turn of turns) {
    const item = turn && typeof turn === 'object' ? turn : {};
    budgetTokens = Math.max(budgetTokens, Number(item.budget_tokens || 0));
    reservedOutputTokens = Math.max(reservedOutputTokens, Number(item.reserved_output_tokens || 0));
    estimatedTokens = Math.max(estimatedTokens, Number(item.estimated_tokens || 0));
    if (item.memory_state) {
      memoryState = String(item.memory_state);
    }
    for (const source of Array.isArray(item.sources) ? item.sources : []) {
      const entry = source && typeof source === 'object' ? source : {};
      const kind = String(entry.kind || '');
      const nodeID = String(entry.node_id || '');
      const artifactID = String(entry.artifact_id || '');
      const label = String(entry.label || '');
      const key = [kind, nodeID, artifactID, label, String(entry.estimated_tokens || 0)].join('|');
      if (!sources.some((existing) => existing.key === key)) {
        sources.push({
          key,
          kind,
          nodeID,
          artifactID,
          label,
          tokens: Number(entry.estimated_tokens || 0),
          protected: Boolean(entry.protected),
        });
      }
    }
    for (const truncation of Array.isArray(item.truncations) ? item.truncations : []) {
      const entry = truncation && typeof truncation === 'object' ? truncation : {};
      truncations.push({
        source: String(entry.source || ''),
        reason: String(entry.reason || ''),
        reasonText: contextTruncationReasonText(entry.reason),
        tokensRemoved: Number(entry.tokens_removed || 0),
      });
    }
    for (const path of Array.isArray(item.protected) ? item.protected : []) {
      const text = String(path || '');
      if (text !== '' && !protectedPaths.includes(text)) {
        protectedPaths.push(text);
      }
    }
  }
  const sourceText =
    sources.length === 0
      ? '没有授权来源记录'
      : sources
          .map((source) => {
            const where = [source.kind, source.nodeID, source.artifactID, source.label]
              .filter((part) => part !== '')
              .join('/');
            return where + '（约 ' + String(source.tokens) + ' tokens）';
          })
          .join('；');
  const truncationText =
    truncations.length === 0
      ? '本次上下文未触发截断'
      : truncations
          .map(
            (entry) =>
              entry.source + ' · ' + entry.reasonText + ' · 移除 ' +
              String(entry.tokensRemoved) + ' tokens',
          )
          .join('；');
  const protectedText =
    protectedPaths.length === 0 ? '没有受保护事实键' : protectedPaths.join('、');
  const memoryText =
    memoryState === 'not_run'
      ? 'not_run（F09 个人记忆存储未实现，未注入任何记忆）'
      : memoryState;
  return {
    visible: turns.length > 0,
    turnCount: turns.length,
    budgetTokens,
    reservedOutputTokens,
    estimatedTokens,
    memoryState,
    memoryText,
    sources,
    truncations,
    protectedPaths,
    sourceText,
    truncationText,
    protectedText,
    budgetText:
      '预算 ' +
      String(budgetTokens) +
      ' tokens（预留输出 ' +
      String(reservedOutputTokens) +
      '，估算输入 ' +
      String(estimatedTokens) +
      '）',
  };
}

function contextTraceStatus(message, kind) {
  const element = document.getElementById('agent-context-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

/**
 * Load and render the persisted context trace of the agent node. The trace is a
 * registered run file, read through the existing owner-only artifact content
 * route; a missing trace is stated plainly, never re-created from model text.
 */
async function renderContextTrace(described) {
  const container = document.getElementById('agent-context-trace');
  if (!container) {
    return null;
  }
  container.replaceChildren();
  if (!described || !described.present) {
    contextTraceStatus('该 Run 没有 Agent 节点，因此没有上下文跟踪。', '');
    return null;
  }
  const row = contextTraceRowOf(lastNodeResults, lastCapabilityLedger, nodeStatusByID);
  if (!row) {
    contextTraceStatus(
      '该 Run 没有持久化的上下文跟踪文件（失败发生在首次模型调用前，或该 Run 来自旧版本）。',
      'pending',
    );
    return null;
  }
  contextTraceStatus('正在加载上下文跟踪…', 'pending');
  try {
    const document_ = JSON.parse(await fetchArtifactText(String(row.artifact_id || '')));
    const trace = describeContextTrace(document_);
    if (!trace.visible) {
      contextTraceStatus('上下文跟踪文件没有轮次记录。', 'pending');
      return trace;
    }
    const summary = document.createElement('dl');
    summary.className = 'run';
    const add = (label, content) => {
      const term = document.createElement('dt');
      term.textContent = label;
      const definition = document.createElement('dd');
      definition.textContent = content;
      summary.append(term, definition);
    };
    add('轮次 / 预算', '第 ' + String(trace.turnCount) + ' 轮 · ' + trace.budgetText);
    add('授权来源', trace.sourceText);
    add('截断', trace.truncationText);
    add('受保护事实', trace.protectedText);
    add('个人记忆', trace.memoryText);
    container.appendChild(summary);
    contextTraceStatus(
      '上下文跟踪来自服务端登记的 context-trace 文件；只含来源、截断原因与受保护键名，不含提示词或正文。',
      'ok',
    );
    return trace;
  } catch (error) {
    contextTraceStatus(
      '上下文跟踪不可用 [' + (error && error.code ? error.code : 'error') + ']',
      'bad',
    );
    return null;
  }
}

function renderAgentNodePanel() {
  const container = document.getElementById('agent-node-body');
  if (!container) {
    return null;
  }
  container.replaceChildren();
  const described = describeAgentNode(
    lastNodeResults,
    lastCapabilityLedger,
    nodeStatusByID,
    displayedRun && displayedRun.state,
    displayedRun && displayedRun.error_code,
  );
  agentNodeStatus(described.text, described.present ? 'ok' : '');
  void renderContextTrace(described);
  if (!described.present) {
    return described;
  }
  const summary = document.createElement('dl');
  summary.className = 'run';
  const add = (label, value) => {
    const term = document.createElement('dt');
    term.textContent = label;
    const definition = document.createElement('dd');
    definition.textContent = value;
    summary.append(term, definition);
  };
  add('节点 / 状态', described.nodeID + ' · ' + described.statusText);
  add('模型轮次', described.turnsText + (described.stub ? '（stub 提供方）' : ''));
  add('工具调用', described.toolCallsText);
  add('工具操作', described.toolStateText);
  add('结果', 'data ' + described.dataCount + ' · 文件 ' + described.fileCount + ' · 日志 ' + described.logCount);
  container.appendChild(summary);
  if (described.errorText !== '') {
    const failure = document.createElement('p');
    failure.className = described.schemaFailure ? 'status bad' : 'status';
    failure.textContent = described.errorText;
    container.appendChild(failure);
  }
  for (const data of described.data) {
    const row = document.createElement('div');
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = '查看 Agent 最终输出';
    const output = document.createElement('pre');
    output.className = 'hint';
    button.addEventListener('click', async () => {
      try {
        output.textContent = prettyJSONText(await fetchArtifactText(data.artifactID));
      } catch (error) {
        output.textContent =
          'Agent 输出不可用 [' + (error && error.code ? error.code : 'error') + ']';
      }
    });
    row.append(button, ' ', data.line);
    container.append(row, output);
  }
  if (described.files.length > 0) {
    const list = document.createElement('ul');
    for (const file of described.files) {
      const item = document.createElement('li');
      const download = document.createElement('button');
      download.type = 'button';
      download.textContent = '下载';
      download.addEventListener('click', () => downloadNodeArtifact(file.artifactID));
      item.append(file.label + ' · ' + file.mediaType + ' · ' + file.sizeText + ' ', download);
      list.appendChild(item);
    }
    container.appendChild(list);
  }
  return described;
}

function recordNodeStatus(event) {
  if (!event || event.type !== 'node.status') {
    return;
  }
  const payload = event.payload && typeof event.payload === 'object' ? event.payload : {};
  const nodeID = String(payload.node_id || '');
  if (nodeID === '') {
    return;
  }
  nodeStatusByID[nodeID] = String(payload.state || '');
  if (lastNodeResults) {
    renderNodeResults(lastNodeResults);
  }
}

async function fetchArtifactText(artifactID) {
  const response = await fetch(artifactContentPath(artifactID), {
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
  });
  const text = await response.text();
  if (!response.ok) {
    let payload = null;
    try {
      payload = JSON.parse(text);
    } catch (parseError) {
      payload = null;
    }
    const detail = (payload && payload.error) || {};
    const failure = new Error(detail.message || 'HTTP ' + response.status);
    failure.code = detail.code || 'http_' + response.status;
    failure.status = response.status;
    throw failure;
  }
  return text;
}

async function downloadNodeArtifact(artifactID) {
  try {
    const ticket = await api(artifactDownloadPath(artifactID));
    if (!ticket.url) {
      throw new Error('下载响应没有 URL');
    }
    window.open(ticket.url, '_blank', 'noopener');
  } catch (error) {
    nodeResultsStatus(
      '下载失败 [' +
        (error && error.code ? error.code : 'error') +
        '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
  }
}

function renderNodeResults(body) {
  lastNodeResults = body && typeof body === 'object' ? body : null;
  const container = document.getElementById('node-results-nodes');
  if (!container) {
    return null;
  }
  container.replaceChildren();
  const described = describeNodeResults(
    lastNodeResults,
    nodeStatusByID,
    displayedRun && displayedRun.state,
  );
  nodeResultsStatus(described.text, described.empty ? '' : 'ok');
  for (const group of described.nodes) {
    const section = document.createElement('section');
    const heading = document.createElement('h3');
    heading.textContent =
      group.nodeID + ' · ' + group.statusText + (group.count > 0 ? '' : ' · 无结果');
    section.appendChild(heading);

    for (const data of group.data) {
      const row = document.createElement('div');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '查看 data';
      const output = document.createElement('pre');
      output.className = 'hint';
      button.addEventListener('click', async () => {
        try {
          output.textContent = prettyJSONText(await fetchArtifactText(data.artifactID));
        } catch (error) {
          output.textContent =
            'data 不可用 [' + (error && error.code ? error.code : 'error') + ']';
        }
      });
      row.append(button, ' ', data.line);
      section.append(row, output);
    }

    if (group.files.length > 0) {
      const list = document.createElement('ul');
      for (const file of group.files) {
        const item = document.createElement('li');
        const download = document.createElement('button');
        download.type = 'button';
        download.textContent = '下载';
        download.addEventListener('click', () => downloadNodeArtifact(file.artifactID));
        item.append(file.label + ' · ' + file.mediaType + ' · ' + file.sizeText + ' ', download);
        list.appendChild(item);
      }
      section.appendChild(list);
    }

    for (const log of group.logs) {
      const row = document.createElement('div');
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = '查看日志（受限）';
      const notice = document.createElement('p');
      notice.className = 'hint';
      const output = document.createElement('pre');
      output.className = 'hint';
      button.addEventListener('click', async () => {
        try {
          const capped = capLogText(await fetchArtifactText(log.artifactID), 4096);
          output.textContent = capped.text;
          notice.textContent = capped.notice;
        } catch (error) {
          output.textContent =
            '日志不可用 [' + (error && error.code ? error.code : 'error') + ']';
        }
      });
      row.append(button, ' ', log.line);
      section.append(row, notice, output);
    }
    container.appendChild(section);
  }
  renderAgentNodePanel();
  return described;
}

async function loadNodeResults(runId) {
  try {
    const body = await api(runNodeResultsPath(runId));
    if (displayedRunID !== runId) {
      return;
    }
    renderNodeResults(body);
  } catch (error) {
    if (error.status === 401) {
      window.location.assign('/login');
      return;
    }
    nodeResultsStatus(
      '节点结果不可用 [' +
        (error && error.code ? error.code : 'error') +
        '] ' +
        (error && error.message ? error.message : '未知错误'),
      'bad',
    );
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
  const cancelButton = document.getElementById('detail-cancel-button');
  if (cancelButton) {
    cancelButton.addEventListener('click', () => {
      const runId = cancelButton.dataset.runId || (displayedRun && displayedRun.id) || '';
      if (runId !== '') {
        requestCancel(String(runId));
      }
    });
  }
  loadRunsPage(null);
}
