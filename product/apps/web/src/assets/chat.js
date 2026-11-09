/*
 * F08/T074 conversation page.
 *
 * The page is a real client of the contract operations:
 *
 *   POST /api/v1/conversations                 -> 201 Accepted
 *   GET  /api/v1/conversations                 -> list
 *   GET  /api/v1/conversations/{id}/messages   -> listMessages
 *   POST /api/v1/requests                      -> 202 Request (Idempotency-Key)
 *   GET  /api/v1/requests/{id}                 -> 200 Request + X-Request-* headers
 *   POST /api/v1/requests/{id}/cancel          -> 202 Accepted
 *
 * The contract Request/Conversation schemas are additionalProperties:false, so the
 * handling version and the queue position are read from the documented non-contract
 * response headers, never invented as JSON fields.
 *
 * All helpers at the top are pure so the Node page test executes the same bytes the
 * browser receives; the DOM wiring is at the bottom.
 */

const REQUEST_STATE_LABELS = Object.freeze({
  queued: '排队中',
  routing: '正在选择应用',
  awaiting_input: '等待补充信息',
  awaiting_selection: '等待选择应用',
  dispatched: '已分派到处理版本',
  cancelled: '已取消',
  failed: '失败',
});
const UNKNOWN_STATE = '未知状态';
const ACTIVE_STATES = ['routing', 'awaiting_input', 'awaiting_selection', 'dispatched'];
const WAITING_STATES = ['queued', 'routing', 'awaiting_input', 'awaiting_selection'];
const TERMINAL_REQUEST_STATES = ['cancelled', 'failed'];

/**
 * F08/T084: user-readable reasons for a failed request.
 *
 * The contract carries the stable error_code on Request; before this table a
 * failed request rendered only "失败" and the reason existed only in the API
 * body. The stable code stays in the DOM as data-error-code so the page test can
 * assert the exact code, and an unknown code is said out loud rather than hidden.
 */
const REQUEST_FAILURE_REASONS = Object.freeze({
  no_matching_application:
    '未找到匹配的应用。请换一种说法描述任务，或先到应用目录确认有可用的应用。',
  interaction_expired:
    '等待的补充信息或应用选择已经过期；请重新提交需求，过期答复不会复活旧任务。',
  interaction_closed:
    '这次确认已经结束或已过期；请重新提交需求，旧答复不会复活任务。',
  route_candidate_invalid:
    '建议的应用版本不在你的授权候选内，本次请求没有执行；请重新提交需求。',
  route_model_unavailable: '路由模型暂时不可用；请稍后重新提交需求。',
  route_model_unconfigured: '部署尚未注册路由控制模型；请联系管理员。',
  route_output_invalid: '路由结果无法通过服务端校验；请重新提交需求。',
  route_budget_exhausted: '本次需求的路由尝试已用尽；请重新提交需求。',
  selected_version_invalid: '所选应用版本已不可用；请重新提交需求。',
  version_revoked: '该应用版本已被撤销；请重新提交需求。',
  version_archived: '该应用已归档；请选择其他应用。',
  version_unavailable: '该应用版本当前不可运行；请稍后重试。',
});

/** The failed-request explanation: visible only for a failed request. */
function describeRequestFailure(request) {
  const state = request && request.state ? String(request.state) : '';
  if (state !== 'failed') {
    return { visible: false, code: '', text: '', retryable: false };
  }
  const code = request && request.error_code ? String(request.error_code).trim() : '';
  const reason = REQUEST_FAILURE_REASONS[code];
  return {
    visible: true,
    code: code,
    text: reason || (
      '请求失败（原因代码 ' + (code || 'unknown') + '）。请稍后重试；若持续失败请联系管理员。'
    ),
    retryable: code === 'route_model_unavailable' || code === 'version_unavailable',
  };
}

/**
 * Render the failure explanation into #chat-request-error.
 *
 * Kept at module level (not inside initChat) so the Node page test can execute
 * the real bytes against a minimal document; renderRequest calls it on every
 * request read, including the 800ms poll path.
 */
function applyRequestFailure(document, request) {
  const failure = describeRequestFailure(request);
  const element = document.getElementById('chat-request-error');
  if (!element) {
    return failure;
  }
  element.hidden = !failure.visible;
  element.textContent = failure.text;
  element.dataset.errorCode = failure.code;
  element.dataset.retryable = failure.retryable ? 'true' : 'false';
  return failure;
}

/*
 * F12/T098: a refused write from this page is a stable code, not a generic
 * "失败了". The two important ones on this surface:
 *
 *   - 409 idempotency_conflict: the Idempotency-Key was reused with a different
 *     body. The server kept the first write; the page must not silently reuse the
 *     key again (that would replay the old body) — the user has to refresh or
 *     resubmit as a new task.
 *   - 409 interaction_closed: the interaction expired or was already answered; the
 *     old reply cannot revive the request.
 *
 * `reuseKey` is the explicit retry decision: true only when retrying with the
 * same key is safe (a network failure never reached the server).
 */
function describeSubmitFailure(error) {
  const status = Number((error && error.status) || 0);
  const code = String((error && error.code) || '');
  const message = String((error && error.message) || '提交失败');
  if (status === 409 && code === 'idempotency_conflict') {
    return {
      code: code,
      status: status,
      retryable: false,
      reuseKey: false,
      text: '这次提交与同一个幂等键的已有请求内容不同（409 idempotency_conflict）：'
        + '服务端保留了第一次提交的内容，没有执行这次请求。页面不会用旧键覆盖；'
        + '请刷新会话核对之前的请求，或把这次当作新任务重新提交（新的幂等键）。',
    };
  }
  if (status === 409 && (code === 'interaction_closed' || code === 'interaction_expired')) {
    return {
      code: code,
      status: status,
      retryable: false,
      reuseKey: false,
      text: '这次确认已经结束或已过期（409 ' + code + '）：旧答复不会复活任务。'
        + '请重新提交需求；页面不会自动重试。',
    };
  }
  if (status === 0) {
    return {
      code: code || 'network_error',
      status: 0,
      retryable: true,
      reuseKey: true,
      text: '网络中断，请求没有确认到达服务端；页面保留已填写内容。'
        + '再次提交会复用同一幂等键，不会产生第二条请求。',
    };
  }
  return {
    code: code,
    status: status,
    retryable: code === 'route_model_unavailable' || code === 'version_unavailable',
    reuseKey: true,
    text: '提交失败 [' + (code || ('HTTP ' + status)) + '] ' + message
      + '；页面保留已填写内容，再次提交会复用同一幂等键（不会重复执行）。',
  };
}

/** The reply-path counterpart of describeSubmitFailure. */
function describeReplyFailure(error) {
  const status = Number((error && error.status) || 0);
  const code = String((error && error.code) || '');
  const message = String((error && error.message) || '答复失败');
  if (status === 409 && (code === 'interaction_closed' || code === 'interaction_expired')) {
    return {
      code: code,
      status: status,
      retryable: false,
      text: '这次确认已经结束或过期（409 ' + code + '）：'
        + '该确认已过期或已结束；请重新提交需求，旧答复不会复活任务。页面不会自动重试。',
    };
  }
  if (status === 409 && code === 'idempotency_conflict') {
    return {
      code: code,
      status: status,
      retryable: false,
      text: '这次答复与同一个幂等键的已有答复内容不同（409 idempotency_conflict）：'
        + '服务端保留了第一次答复。请刷新会话核对，或重新提交需求；页面不会用旧键覆盖。',
    };
  }
  return {
    code: code,
    status: status,
    retryable: true,
    text: '答复失败 [' + (code || ('HTTP ' + status)) + '] ' + message
      + '；再次点击会复用同一幂等键（不会重复提交）。',
  };
}

function conversationPath(conversationID) {
  return '/api/v1/conversations/' + encodeURIComponent(String(conversationID || ''));
}

function conversationMessagesPath(conversationID) {
  return conversationPath(conversationID) + '/messages';
}

function requestPath(requestID) {
  return '/api/v1/requests/' + encodeURIComponent(String(requestID || ''));
}

function requestCancelPath(requestID) {
  return requestPath(requestID) + '/cancel';
}

function requestReplyPath(requestID) {
  return requestPath(requestID) + '/reply';
}

function artifactContentPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID || '')) + '/content';
}

/** Fixed Chinese text for the seven contract states; unknown is said out loud. */
function describeRequestState(state) {
  return REQUEST_STATE_LABELS[state] || UNKNOWN_STATE;
}

function requestIsTerminal(state) {
  return TERMINAL_REQUEST_STATES.includes(state);
}

/**
 * A request is polled only while its state can still move (queued -> dispatched).
 * C6.05 (review-2): this page keeps no Request-only polling rule; the shared
 * `OrcharaRunState.shouldPoll` owns the request/run poll decision.
 */

/**
 * Ordered queue projection. Items are the conversation's requests oldest first
 * (derived from the message timeline), never a locally guessed order.
 */
function describeQueue(items, activeID) {
  const list = Array.isArray(items) ? items : [];
  const rows = list.map((item, index) => ({
    id: item.id,
    position: index + 1,
    state: item.state || '',
    label: describeRequestState(item.state),
    cancelable: !requestIsTerminal(item.state),
    active: item.id === activeID || (!activeID && index === 0),
  }));
  if (rows.length === 0) {
    return { rows: [], text: '队列为空。' };
  }
  const unfinished = rows.filter((row) => !requestIsTerminal(row.state));
  const first = rows[0];
  return {
    rows: rows,
    text:
      '共 ' + rows.length + ' 项请求，' + unfinished.length + ' 项未完成；' +
      '第 1 项' + (first.state ? '（' + first.label + '）' : '') + '占逻辑会话槽。',
  };
}

/**
 * The request a conversation load must show again. The active request wins when it
 * is still in the timeline; otherwise the first request that can still move (the
 * queue slot holder, which is the one carrying a live interaction) is restored;
 * a dispatched request is shown for its handling version. A timeline whose requests
 * are all terminal restores nothing: there is no control surface left to continue.
 */
function resumableRequest(items, activeID) {
  const list = Array.isArray(items) ? items : [];
  const known = (id) => list.some((item) => item && item.id === id);
  if (activeID && known(activeID)) {
    return activeID;
  }
  const waiting = list.find((item) => item && WAITING_STATES.includes(item.state));
  if (waiting) {
    return waiting.id;
  }
  const dispatched = list.find((item) => item && item.state === 'dispatched');
  return dispatched ? dispatched.id : '';
}

/**
 * Pending interaction projection; `visible:false` means there is nothing to answer.
 *
 * The proposal is the sealed server projection (question / candidates / missing
 * fields). Candidates are ordinals only: the page never invents or displays an
 * uncalibrated model number as if it were a calibrated likelihood.
 */
function describeInteraction(interaction) {
  if (!interaction) {
    return {
      visible: false, kind: '', prompt: '', candidates: [], missingFields: [],
      expiresAt: '', closed: true, ordinalOnly: true,
    };
  }
  const proposal = interaction.proposal || {};
  const candidates = Array.isArray(proposal.candidates)
    ? proposal.candidates.map((candidate, index) => ({
        id: candidate.application_version_id || candidate.id || '',
        label: candidate.label || candidate.name || candidate.application_name ||
          candidate.version_no || '',
        rank: Number.isInteger(candidate.rank) ? candidate.rank : index + 1,
        purpose: candidate.purpose || '',
        requiredInputs: Array.isArray(candidate.required_inputs)
          ? candidate.required_inputs
          : [],
        versionNo: Number.isInteger(candidate.version_no) ? candidate.version_no : 0,
      }))
    : [];
  const candidate = proposal.candidate || null;
  const missingFields = Array.isArray(proposal.missing_fields)
    ? proposal.missing_fields
    : [];
  return {
    visible: true,
    kind: interaction.kind || '',
    prompt: proposal.question || proposal.prompt || '',
    candidates: candidates,
    candidate: candidate
      ? {
          id: candidate.application_version_id || '',
          label: candidate.application_name || candidate.purpose || '',
          rank: Number.isInteger(candidate.rank) ? candidate.rank : 0,
        }
      : null,
    missingFields: missingFields,
    expiresAt: interaction.expires_at || '',
    closed: false,
    ordinalOnly: true,
  };
}

/** Candidate list lines: ordinal, application name, version number, purpose. */
function candidateLines(interaction) {
  const described = describeInteraction(interaction);
  return described.candidates.map((candidate) => {
    const rank = candidate.rank > 0 ? candidate.rank : 1;
    const name = candidate.label || '未命名应用';
    const version = candidate.versionNo > 0 ? ' v' + candidate.versionNo : '';
    const purpose = candidate.purpose ? ' · ' + candidate.purpose : '';
    return { id: candidate.id, rank: rank, text: '候选 ' + rank + '：' + name + version + purpose };
  });
}

/** The response mode: a candidate list needs a choice, otherwise an answer. */
function interactionMode(interaction) {
  const described = describeInteraction(interaction);
  if (!described.visible) {
    return 'none';
  }
  return described.candidates.length > 0 ? 'selection' : 'input';
}

/**
 * Build the exact InteractionReply body. The contract requires at least one of
 * message / candidate_version_id / artifact_ids; the page refuses locally with the
 * same rule the server enforces.
 */
function interactionReplyBody(interactionID, message, candidateVersionID, artifactIDs) {
  if (!UUID_PATTERN.test(String(interactionID || ''))) {
    throw pageFailure('interaction_id', '缺少有效的 interaction_id');
  }
  const body = { interaction_id: String(interactionID) };
  const text = String(message === undefined || message === null ? '' : message);
  if (text.length > 32000) {
    throw pageFailure('message', '补充说明最多 32000 字');
  }
  if (text.length > 0) {
    body.message = text;
  }
  const candidate = String(candidateVersionID || '').trim();
  if (candidate) {
    if (!UUID_PATTERN.test(candidate)) {
      throw pageFailure('candidate_version_id', '候选版本 id 必须是 UUID');
    }
    body.candidate_version_id = candidate;
  }
  const rawArtifacts = Array.isArray(artifactIDs) ? artifactIDs : [];
  if (rawArtifacts.length > 5) {
    throw pageFailure('artifact_ids', '最多 5 个附件');
  }
  const artifacts = Array.from(new Set(rawArtifacts));
  for (const id of artifacts) {
    if (!UUID_PATTERN.test(String(id))) {
      throw pageFailure('artifact_ids', '附件 id 必须是 UUID');
    }
  }
  if (artifacts.length > 0) {
    body.artifact_ids = artifacts;
  }
  if (!body.message && !body.candidate_version_id && !body.artifact_ids) {
    throw pageFailure('interaction_id', '请选择候选或补充信息后再提交');
  }
  return body;
}

/**
 * The uploaded artifact ids a reply carries. The page never sends an artifact the
 * user did not upload here; the server re-validates readiness, ownership and that
 * the artifact answers the request's missing inputs.
 */
function replyArtifactIDs(uploaded) {
  const list = Array.isArray(uploaded) ? uploaded : [];
  return Array.from(new Set(list.filter(Boolean).map(String)));
}

/** One reply attempt owns one key; a retry reuses the same object/key. */
function newInteractionAttempt(body) {
  return { key: crypto.randomUUID(), body: body };
}

function interactionReplyHeaders(attempt) {
  return { 'Idempotency-Key': attempt.key };
}

/** The confirmed handover summary, as the user sees it before submitting. */
function describeHandoff(handoff) {
  if (!handoff) {
    return { visible: false, summary: '', artifactCount: 0, confirmed: false };
  }
  return {
    visible: true,
    summary: handoff.summary || '',
    artifactCount: Array.isArray(handoff.artifact_ids) ? handoff.artifact_ids.length : 0,
    confirmed: handoff.confirmed === true,
  };
}

/**
 * C6.04 (#158): `OrcharaRunStateGuard.ready` is the only dependency fact this
 * page consults. The guard performs the one complete readiness check (404, parse
 * error or incomplete export) before this script runs; there is no page-local
 * dependency helper and no second state interpretation to fall back to.
 */

/** The handling version sentence; never guesses a version that is not known. */
function describeHandling(request, run, version) {
  if (!request || !request.run_id) {
    return { text: '尚无处理应用', handled: false, versionNo: 0 };
  }
  const versionNo = version && Number.isInteger(version.version_no) ? version.version_no : 0;
  if (!OrcharaRunStateGuard.ready) {
    // No old three-outcome fallback and no raw Run snapshot as substitute state:
    // the page says out loud that Run state cannot be interpreted right now.
    return { text: '运行状态暂不可用', handled: true, versionNo: versionNo };
  }
  if (!run || typeof run.state !== 'string' || run.state === '') {
    // Page copy for "the Run snapshot has not been read yet"; the shared view is
    // only asked once there is a Run fact to interpret.
    return {
      text: (versionNo > 0 ? 'v' + versionNo : '处理版本') +
        ' 处理状态未知（尚未读到 Run 快照）',
      handled: true,
      versionNo: versionNo,
    };
  }
  // C6.02: finalizing (phase-only), cancelling, waiting_children/approval,
  // recovery_pending and needs_reconciliation all come from the one shared view.
  return {
    text: OrcharaRunState.handlingText(run, versionNo),
    handled: true,
    versionNo: versionNo,
  };
}

/**
 * F12/T096: poll the dialogue while the *Run* can still move.
 *
 * The old Request-only rule only saw the Request, and a dispatched Request stays
 * `dispatched` forever — so before this rule the conversation page stopped
 * observing the Run exactly when finalizing/cancelling/child-waiting began. The
 * shared `OrcharaRunState.shouldPoll` now owns that decision; there is no second
 * Request-only polling rule on this page.
 */
function shouldPollHandling(requestState, runState, attempts, maxAttempts) {
  if (!OrcharaRunStateGuard.ready) {
    // C6.01: the affected realtime observation is paused, not downgraded to the
    // Request-only poll (that would be the old second set of state rules).
    return false;
  }
  // C6.02: the shared rule, including the existing poll cap (100 on this page).
  return OrcharaRunState.shouldPoll(requestState, runState, attempts, maxAttempts);
}

// ---------------------------------------------------------------------------
// F08/T082: the minimal handover preview (non-contract, ADR 0030)
// ---------------------------------------------------------------------------

/** Parse the comma-separated source-Run field into a deduplicated id list. */
function handoffSourceRunIDs(value) {
  const list = Array.isArray(value)
    ? value
    : String(value === undefined || value === null ? '' : value)
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean);
  return Array.from(new Set(list.map((entry) => String(entry).trim()).filter(Boolean)));
}

function handoffPreviewPath(conversationID) {
  return conversationPath(conversationID) + '/handoff-preview';
}

function runArtifactsPath(runID) {
  return '/api/v1/runs/' + encodeURIComponent(String(runID || '')) + '/artifacts';
}

/**
 * The exact preview request: the source Runs the user named and the artifacts the
 * user selected. The server produces the summary; the page never writes one.
 */
function handoffPreviewBody(sourceRunIDs, artifactIDs) {
  const runs = handoffSourceRunIDs(sourceRunIDs);
  if (runs.length === 0) {
    throw pageFailure('handoff.source_run_ids', '交接至少需要一个来源 Run');
  }
  if (runs.length > 5) {
    throw pageFailure('handoff_scope_exceeded', '来源 Run 最多 5 个');
  }
  for (const id of runs) {
    if (!UUID_PATTERN.test(id)) {
      throw pageFailure('handoff.source_run_ids', '来源 Run id 必须是 UUID');
    }
  }
  const artifacts = handoffSourceRunIDs(artifactIDs);
  if (artifacts.length > 5) {
    throw pageFailure('handoff_scope_exceeded', '交接附件最多 5 个');
  }
  for (const id of artifacts) {
    if (!UUID_PATTERN.test(id)) {
      throw pageFailure('handoff.artifact_ids', '交接附件 id 必须是 UUID');
    }
  }
  return { source_run_ids: runs.slice().sort(), artifact_ids: artifacts.slice().sort() };
}

/** The server preview as the user sees it; nothing is invented when absent. */
function describeHandoffPreview(preview) {
  if (!preview || !preview.summary) {
    return {
      visible: false, summary: '', sourceRunCount: 0, artifactCount: 0,
      bounded: true, fromVersionId: '',
    };
  }
  const summary = String(preview.summary);
  const sourceRuns = Array.isArray(preview.source_runs) ? preview.source_runs : [];
  const artifacts = Array.isArray(preview.artifacts) ? preview.artifacts : [];
  const sourceRunIDs = Array.isArray(preview.source_run_ids) ? preview.source_run_ids : [];
  const artifactIDs = Array.isArray(preview.artifact_ids) ? preview.artifact_ids : [];
  return {
    visible: true,
    summary: summary,
    sourceRunCount: sourceRuns.length || sourceRunIDs.length,
    artifactCount: artifacts.length || artifactIDs.length,
    bounded: Array.from(summary).length <= 2000,
    fromVersionId: preview.from_version_id || '',
  };
}

/**
 * The confirmed handover object, copied verbatim from the server preview. An
 * unchecked box or a missing preview refuses locally with the same code the
 * server would answer; the summary is never editable on the page.
 */
function handoffFromPreview(preview, confirmed) {
  const described = describeHandoffPreview(preview);
  if (!described.visible) {
    throw pageFailure('handoff_preview_required', '请先生成服务端交接预览');
  }
  if (!confirmed) {
    throw pageFailure('handoff_not_confirmed', '请先确认交接范围');
  }
  return {
    source_run_ids: Array.from(preview.source_run_ids || []),
    artifact_ids: Array.from(preview.artifact_ids || []),
    summary: described.summary,
    confirmed: true,
  };
}

/**
 * Selectable artifacts of the source Runs. `input` links are the old Run's own
 * sealed request/handover bytes, so they are never offered for handover.
 */
function handoffArtifactOptions(items) {
  return (Array.isArray(items) ? items : [])
    .filter((item) => item && item.artifact_id && item.relation !== 'input')
    .map((item) => ({
      id: String(item.artifact_id),
      relation: item.relation || '',
      purpose: item.purpose || '',
      mediaType: item.media_type || '',
      sizeBytes: Number.isInteger(item.size_bytes) ? item.size_bytes : 0,
      label:
        '产出附件 · ' + (item.purpose || item.relation || 'artifact') +
        (Number.isInteger(item.size_bytes) ? ' · ' + item.size_bytes + ' 字节' : ''),
      selectable: true,
    }));
}

/** Read the documented non-contract X-Request-* headers from one response. */
function requestFacts(headers) {
  const read = (name) => {
    if (!headers) {
      return '';
    }
    const value = typeof headers.get === 'function' ? headers.get(name) : null;
    return value === null || value === undefined ? '' : String(value);
  };
  const versionNo = Number(read('X-Request-Application-Version-No'));
  return {
    conversationId: read('X-Request-Conversation-Id'),
    applicationVersionId: read('X-Request-Application-Version-Id'),
    versionNo: Number.isInteger(versionNo) ? versionNo : 0,
    interactionId: read('X-Request-Interaction-Id'),
    correlationId: read('X-Request-Correlation-Id'),
    queuePosition: Number(read('X-Request-Queue-Position')) || 0,
  };
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** Validate the exact RequestInput body before any network call. */
function submitBody(conversationID, message, artifactIDs, versionID, handoff) {
  const conversation = String(conversationID || '').trim();
  if (!UUID_PATTERN.test(conversation)) {
    throw pageFailure('conversation_id', '请先选择一个会话');
  }
  const text = String(message === undefined || message === null ? '' : message);
  if (text.length < 1) {
    throw pageFailure('message', '需求不能为空');
  }
  if (text.length > 32000) {
    throw pageFailure('message', '需求最多 32000 字');
  }
  const rawArtifacts = Array.isArray(artifactIDs) ? artifactIDs : [];
  if (rawArtifacts.length > 5) {
    throw pageFailure('artifact_ids', '最多 5 个附件');
  }
  const artifacts = Array.from(new Set(rawArtifacts));
  for (const id of artifacts) {
    if (!UUID_PATTERN.test(String(id))) {
      throw pageFailure('artifact_ids', '附件 id 必须是 UUID');
    }
  }
  const body = { conversation_id: conversation, message: text, artifact_ids: artifacts };
  const version = String(versionID || '').trim();
  if (version) {
    if (!UUID_PATTERN.test(version)) {
      throw pageFailure('application_version_id', '固定版本 id 必须是 UUID');
    }
    body.application_version_id = version;
  }
  if (handoff) {
    body.handoff = handoff;
  }
  return body;
}

/**
 * Build the confirmed handover object. The checkbox is the confirmation; an
 * unchecked box refuses locally with the same code the server would answer.
 */
function handoffBody(sourceRunIDs, summary, confirmed, artifactIDs) {
  const list = Array.isArray(sourceRunIDs)
    ? sourceRunIDs
    : String(sourceRunIDs || '')
        .split(',')
        .map((value) => value.trim())
        .filter(Boolean);
  if (list.length === 0) {
    throw pageFailure('handoff.source_run_ids', '交接至少需要一个来源 Run');
  }
  for (const id of list) {
    if (!UUID_PATTERN.test(String(id))) {
      throw pageFailure('handoff.source_run_ids', '来源 Run id 必须是 UUID');
    }
  }
  if (!confirmed) {
    throw pageFailure('handoff_not_confirmed', '请先确认交接范围');
  }
  return {
    source_run_ids: list,
    artifact_ids: Array.from(new Set(artifactIDs || [])),
    summary: String(summary === undefined || summary === null ? '' : summary),
    confirmed: true,
  };
}

/** One submission attempt: the key is created once and reused on a retry. */
function newSubmissionAttempt(body) {
  const key =
    typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : 'submit-' + Date.now() + '-' + Math.random().toString(16).slice(2);
  return { key: key, body: body };
}

function submitRequestHeaders(attempt) {
  return { 'Idempotency-Key': attempt.key };
}

function pageFailure(code, message) {
  const failure = new Error(message);
  failure.code = code;
  return failure;
}

/** Attachment picker: one line per picked, ready artifact. */
function attachmentPickerLines(artifactIDs) {
  return (artifactIDs || []).map((id, index) => ({
    id: id,
    label: '附件 ' + (index + 1) + ' · ' + String(id).slice(0, 8),
  }));
}

/** Message timeline projection (listMessages is newest first). */
function messageLine(message) {
  const role = message.role || 'system';
  const kind = message.kind || 'status';
  const labels = {
    request: '需求',
    result: '结果',
    status: '状态',
    interaction: '澄清/选择',
  };
  return {
    id: message.id,
    requestId: message.request_id,
    runId: message.run_id,
    artifactId: message.body_artifact_id,
    role: role,
    kind: kind,
    label: (labels[kind] || kind) + ' · ' + role,
    fetchBody: Boolean(message.body_artifact_id) && (kind === 'request' || kind === 'result'),
  };
}

// ---------------------------------------------------------------------------
// DOM wiring (plain JS, no framework)
// ---------------------------------------------------------------------------
function initChat() {
  const panel = document.getElementById('chat-panel');
  if (!panel) {
    return;
  }
  let currentConversationID = null;
  let currentRequestID = null;
  let queueRequests = [];
  let attachmentIDs = [];
  let pollTimer = null;
  // C6.02: one monotonic selection generation. A Request/Run read that resolves
  // after the user selected another request must not overwrite the newer view
  // (the same stale-answer guard the runs page uses for its selection).
  let selectionGeneration = 0;
  let pendingAttempt = null;
  let currentInteraction = null;
  let currentInteractionAttempt = null;
  let handoffArtifactIDs = [];
  let currentHandoffPreview = null;

  const conversationStatus = document.getElementById('conversation-status');
  const submitStatus = document.getElementById('chat-submit-status');
  const attachmentStatus = document.getElementById('chat-attachment-status');
  const handoffStatus = document.getElementById('chat-handoff-preview-status');

  function setStatus(element, message, kind) {
    if (!element) {
      return;
    }
    element.textContent = message;
    element.className = 'status ' + (kind || '');
  }

  async function loadConversations() {
    const list = document.getElementById('conversation-list');
    list.replaceChildren();
    try {
      const page = await api('/api/v1/conversations');
      for (const conversation of page.items || []) {
        const item = document.createElement('li');
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = conversation.id.slice(0, 8) + ' · ' + conversation.created_at;
        button.addEventListener('click', () => selectConversation(conversation.id));
        item.appendChild(button);
        list.appendChild(item);
      }
      if (!page.items || page.items.length === 0) {
        setStatus(conversationStatus, '还没有会话，点击“新建会话”。', 'pending');
      }
    } catch (error) {
      setStatus(conversationStatus, '读取会话失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function selectConversation(conversationID) {
    stopPolling();
    currentConversationID = conversationID;
    currentRequestID = null;
    currentInteraction = null;
    currentInteractionAttempt = null;
    attachmentIDs = [];
    handoffArtifactIDs = [];
    currentHandoffPreview = null;
    queueRequests = [];
    document.getElementById('chat-conversation-id').textContent = conversationID;
    document.getElementById('chat-handoff-source-runs').value = '';
    document.getElementById('chat-handoff-artifact-options').replaceChildren();
    document.getElementById('chat-handoff-preview').textContent = '';
    document.getElementById('chat-handoff-summary').textContent = '';
    document.getElementById('chat-handoff-scope').textContent = '';
    document.getElementById('chat-handoff-confirmed').checked = false;
    document.getElementById('request-panel').hidden = true;
    document.getElementById('chat-request-id').textContent = '';
    document.getElementById('chat-request-state').textContent = '';
    document.getElementById('chat-handling').textContent = '';
    document.getElementById('chat-run-link').replaceChildren();
    renderInteraction(null);
    panel.hidden = false;
    renderAttachmentLines();
    await loadMessages();
    await refreshQueue();
    // Post-review fix (#80): reopening a conversation restores the request that
    // still controls it — the interaction panel/candidates and polling come back,
    // and the queue below is clickable.
    const resumeID = resumableRequest(queueRequests, null);
    if (resumeID) {
      await showRequest(resumeID, true);
    }
  }

  async function loadMessages() {
    if (!currentConversationID) {
      return;
    }
    const container = document.getElementById('chat-messages');
    container.replaceChildren();
    try {
      const page = await api(conversationMessagesPath(currentConversationID));
      for (const message of (page.items || []).slice().reverse()) {
        const line = messageLine(message);
        const item = document.createElement('li');
        item.dataset.requestId = message.request_id || '';
        const head = document.createElement('span');
        head.className = 'message-label';
        head.textContent = line.label + ' · ' + String(message.created_at || '');
        item.appendChild(head);
        if (line.fetchBody) {
          const body = document.createElement('pre');
          body.textContent = '（读取封存正文…）';
          item.appendChild(body);
          fetchArtifactText(line.artifactId)
            .then((text) => {
              body.textContent = text;
            })
            .catch((error) => {
              body.textContent = '（正文不可读：' + (error.code || error.message) + '）';
            });
        }
        container.appendChild(item);
      }
    } catch (error) {
      setStatus(conversationStatus, '读取消息失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  function requestOrderFromMessages() {
    const seen = [];
    const items = document.querySelectorAll('#chat-messages li');
    for (const item of items) {
      const requestID = item.dataset.requestId;
      if (requestID && !seen.includes(requestID)) {
        seen.push(requestID);
      }
    }
    return seen;
  }

  async function refreshQueue() {
    if (!currentConversationID) {
      return;
    }
    const queueList = document.getElementById('queue-list');
    queueList.replaceChildren();
    const ids = requestOrderFromMessages();
    const requests = [];
    for (const id of ids) {
      try {
        requests.push(await api(requestPath(id)));
      } catch (error) {
        // A request whose conversation was deleted is not rendered as live.
        if (error.status !== 404) {
          throw error;
        }
      }
    }
    queueRequests = requests;
    const queue = describeQueue(
      requests.map((request) => ({ id: request.id, state: request.state })),
      currentRequestID,
    );
    document.getElementById('chat-queue-status').textContent = queue.text;
    for (const row of queue.rows) {
      const item = document.createElement('li');
      item.dataset.requestId = row.id;
      const show = document.createElement('button');
      show.type = 'button';
      show.textContent = row.position + '. ' + row.label + ' · ' + row.id.slice(0, 8);
      show.addEventListener('click', () => {
        showRequest(row.id, true);
      });
      item.appendChild(show);
      // Post-review fix (#80): a queued/awaiting request is visible and cancellable
      // from the queue itself, not only while it happens to be the selected one.
      // C6.01: the affected cancel action is paused when Run state is unavailable
      // (a cancel is accepted as intent and needs the same state interpretation).
      if (row.cancelable && OrcharaRunStateGuard.ready) {
        const cancel = document.createElement('button');
        cancel.type = 'button';
        cancel.textContent = '取消';
        cancel.addEventListener('click', () => {
          cancelRequest(row.id);
        });
        item.appendChild(cancel);
      }
      queueList.appendChild(item);
    }
  }

  /** Load and render one request; `resume` continues its polling. */
  async function showRequest(requestID, resume) {
    if (!requestID) {
      return;
    }
    stopPolling();
    currentRequestID = requestID;
    currentInteraction = null;
    currentInteractionAttempt = null;
    renderInteraction(null);
    const generation = ++selectionGeneration;
    try {
      const result = await fetchRequest(requestID);
      if (generation !== selectionGeneration) {
        // The user selected another request while this read was in flight: the
        // late answer belongs to the old selection and is dropped whole.
        return;
      }
      renderRequest(result.request, result.facts, result.run);
      if (resume && OrcharaRunStateGuard.ready) {
        poll(0);
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      if (generation !== selectionGeneration) {
        return;
      }
      setStatus(submitStatus, '查询请求失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  /** The one cancel path for the selected request and for a queue row. */
  async function cancelRequest(requestID) {
    if (!requestID) {
      return;
    }
    if (!OrcharaRunStateGuard.ready) {
      setStatus(
        submitStatus,
        '运行状态暂不可用：已暂停取消请求与运行状态观察；请刷新页面重试。',
        'bad',
      );
      return;
    }
    try {
      const csrfToken = await csrf();
      await api(requestCancelPath(requestID), { method: 'POST', csrf: csrfToken });
      // F12/T096: cancel stops controlled execution; it never rolls back an
      // external write that already happened. The Run state is rendered by the
      // handling line once the poll reads it.
      setStatus(
        submitStatus,
        '已接受取消意图：取消只停止受控执行，不会撤回已经发生的外部写入；正在刷新状态…',
        'pending',
      );
      await loadMessages();
      await refreshQueue();
      if (requestID === currentRequestID) {
        poll(0);
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus(submitStatus, '取消失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  function renderInteraction(interaction) {
    if (!currentInteraction ||
        (interaction && currentInteraction.interaction_id !== interaction.interaction_id)) {
      currentInteractionAttempt = null;
    }
    currentInteraction = interaction || null;
    const described = describeInteraction(interaction);
    const interactionPanel = document.getElementById('interaction-panel');
    if (!interactionPanel) {
      return;
    }
    interactionPanel.hidden = !described.visible;
    if (!described.visible) {
      currentInteractionAttempt = null;
      return;
    }
    document.getElementById('interaction-prompt').textContent = described.prompt ||
      (described.kind === 'selection' ? '请选择一个应用继续。' : '请补充缺少的信息。');
    const missing = described.missingFields.length > 0
      ? '缺少：' + described.missingFields.join('、')
      : '';
    document.getElementById('interaction-missing').textContent = missing;
    document.getElementById('interaction-expires').textContent = described.expiresAt
      ? '请在 ' + described.expiresAt + ' 前答复；过期后需要重新提交需求。'
      : '过期后需要重新提交需求，旧答复不会复活任务。';
    const list = document.getElementById('interaction-candidates');
    list.replaceChildren();
    for (const line of candidateLines(interaction)) {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'interaction-candidate';
      radio.value = line.id;
      label.appendChild(radio);
      label.appendChild(document.createTextNode(' ' + line.text));
      item.appendChild(label);
      list.appendChild(item);
    }
    const mode = interactionMode(interaction);
    document.getElementById('interaction-candidates').hidden = mode !== 'selection';
    document.getElementById('interaction-select').hidden = mode !== 'selection';
  }

  function chosenCandidateID() {
    const checked = document.querySelector('input[name="interaction-candidate"]:checked');
    return checked ? checked.value : '';
  }

  function renderRequest(request, facts, run) {
    currentRequestID = request.id;
    document.getElementById('request-panel').hidden = false;
    document.getElementById('chat-request-id').textContent = request.id;
    document.getElementById('chat-request-state').textContent = describeRequestState(request.state);
    // F08/T084: a failed request explains the stable error_code; the state label
    // above keeps its existing seven-state meaning.
    applyRequestFailure(document, request);
    renderInteraction(request.interaction);
    const handling = describeHandling(
      request,
      run || null,
      facts && facts.versionNo ? { version_no: facts.versionNo } : null,
    );
    const handlingLine = document.getElementById('chat-handling');
    handlingLine.textContent = handling.text;
    // F12/T096: keep the observed Run state/phase on the line so a page test (and
    // a reader) can tell finalizing/cancelling from a committed outcome without
    // parsing prose.
    handlingLine.dataset.runState = run && run.state ? String(run.state) : '';
    handlingLine.dataset.runPhase = run && run.phase ? String(run.phase) : '';
    const runLink = document.getElementById('chat-run-link');
    runLink.replaceChildren();
    if (request.run_id) {
      const link = document.createElement('a');
      link.href = '/runs?run=' + encodeURIComponent(request.run_id);
      link.textContent = '查看运行 ' + request.run_id.slice(0, 8);
      runLink.appendChild(link);
    }
    const queueStatus = document.getElementById('chat-queue-status');
    if (facts && facts.queuePosition > 0) {
      queueStatus.textContent = '排队位置：第 ' + facts.queuePosition + ' 位';
    }
  }

  async function fetchRun(runID) {
    if (!runID) {
      return null;
    }
    try {
      return await api('/api/v1/runs/' + encodeURIComponent(runID));
    } catch (error) {
      if (error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  async function fetchRequest(requestID) {
    const result = await apiFull(requestPath(requestID));
    // C6.01: in dependency-failure mode the page must not read a Run snapshot
    // and present its raw state; only the Request (its own state machine) is
    // rendered, and the handling line says Run state is unavailable.
    const run =
      result.payload.run_id && OrcharaRunStateGuard.ready
        ? await fetchRun(result.payload.run_id)
        : null;
    return { request: result.payload, facts: requestFacts(result.headers), run: run };
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function poll(attempts) {
    if (!currentRequestID) {
      return;
    }
    if (!OrcharaRunStateGuard.ready) {
      // C6.01: pause the realtime request/Run observation instead of polling
      // with incomplete state rules.
      stopPolling();
      return;
    }
    const requestID = currentRequestID;
    const generation = selectionGeneration;
    try {
      const result = await fetchRequest(requestID);
      if (generation !== selectionGeneration) {
        // A newer selection owns the panel and its own poll; this late read is
        // not rendered and must not stop the newer poll.
        return;
      }
      renderRequest(result.request, result.facts, result.run);
      await refreshQueue();
      if (generation !== selectionGeneration) {
        return;
      }
      const runState = result.run && typeof result.run.state === 'string' ? result.run.state : '';
      if (!shouldPollHandling(result.request.state, runState, attempts, 100)) {
        stopPolling();
        if (requestIsTerminal(result.request.state)) {
          setStatus(submitStatus, '请求已' + describeRequestState(result.request.state), 'ok');
        } else if (OrcharaRunState.isTerminal(runState)) {
          // F12/T096: the dispatched Request is not "done"; the Run outcome is.
          setStatus(submitStatus, 'Run 已进入终态 ' + runState + '。', 'ok');
        }
        return;
      }
      pollTimer = setTimeout(() => poll(attempts + 1), 800);
    } catch (error) {
      if (generation !== selectionGeneration) {
        return;
      }
      stopPolling();
      setStatus(submitStatus, '查询请求失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  function renderAttachmentLines() {
    const list = document.getElementById('chat-attachment-lines');
    list.replaceChildren();
    for (const line of attachmentPickerLines(attachmentIDs)) {
      const item = document.createElement('li');
      item.textContent = line.label;
      list.appendChild(item);
    }
  }

  // --- F08/T082: preview → select → confirm ----------------------------------
  function invalidateHandoffPreview() {
    currentHandoffPreview = null;
    document.getElementById('chat-handoff-preview').textContent = '';
    document.getElementById('chat-handoff-summary').textContent = '';
    document.getElementById('chat-handoff-scope').textContent = '';
  }

  function selectedHandoffArtifactIDs() {
    const checked = document.querySelectorAll('#chat-handoff-artifact-options input:checked');
    return Array.from(checked).map((input) => input.value);
  }

  function renderHandoffArtifactOptions(options) {
    const list = document.getElementById('chat-handoff-artifact-options');
    list.replaceChildren();
    if (options.length === 0) {
      const item = document.createElement('li');
      item.textContent = '来源 Run 没有可交接的产出附件。';
      list.appendChild(item);
      return;
    }
    for (const option of options) {
      const item = document.createElement('li');
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = option.id;
      checkbox.checked = handoffArtifactIDs.includes(option.id);
      checkbox.addEventListener('change', () => {
        handoffArtifactIDs = selectedHandoffArtifactIDs();
        invalidateHandoffPreview();
      });
      label.appendChild(checkbox);
      label.appendChild(document.createTextNode(' ' + option.label));
      item.appendChild(label);
      list.appendChild(item);
    }
  }

  async function loadHandoffArtifacts() {
    const status = document.getElementById('chat-handoff-artifact-status');
    setStatus(status, '', '');
    if (!currentConversationID) {
      setStatus(status, '请先选择一个会话。', 'bad');
      return;
    }
    let body;
    try {
      // Reuse the preview body validator: it also enforces the source-Run bounds.
      body = handoffPreviewBody(
        document.getElementById('chat-handoff-source-runs').value, []);
    } catch (error) {
      setStatus(status, '无法读取 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    const options = [];
    try {
      for (const runID of body.source_run_ids) {
        const page = await api(runArtifactsPath(runID));
        options.push(...handoffArtifactOptions(page.items));
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus(status, '读取来源附件失败 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    handoffArtifactIDs = [];
    renderHandoffArtifactOptions(options);
    invalidateHandoffPreview();
    setStatus(status, '来源 Run 共 ' + options.length + ' 个可交接附件。', 'ok');
  }

  async function previewHandoff() {
    if (!currentConversationID) {
      setStatus(handoffStatus, '请先选择一个会话。', 'bad');
      return;
    }
    let body;
    try {
      body = handoffPreviewBody(
        document.getElementById('chat-handoff-source-runs').value,
        selectedHandoffArtifactIDs(),
      );
    } catch (error) {
      setStatus(handoffStatus, '无法生成预览 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    try {
      const csrfToken = await csrf();
      setStatus(handoffStatus, '正在生成服务端预览…', 'pending');
      const preview = await api(handoffPreviewPath(currentConversationID), {
        method: 'POST',
        body: body,
        csrf: csrfToken,
      });
      currentHandoffPreview = preview;
      const described = describeHandoffPreview(preview);
      document.getElementById('chat-handoff-preview').textContent = described.summary;
      document.getElementById('chat-handoff-summary').textContent = described.summary;
      document.getElementById('chat-handoff-scope').textContent =
        '来源 Run ' + described.sourceRunCount + ' 个，附件 ' + described.artifactCount +
        ' 个，摘要 ' + Array.from(described.summary).length +
        ' 字（上限 2000）。确认后才会提交这份最小范围；旧应用内部历史与个人记忆不在其中。';
      setStatus(handoffStatus, '预览已生成，请确认范围。', 'ok');
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      invalidateHandoffPreview();
      setStatus(handoffStatus, '生成预览失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function uploadAttachment() {
    const input = document.getElementById('chat-attachment-input');
    const file = input.files && input.files[0];
    let payload;
    try {
      payload = uploadInputForFile(file, 'text/plain', 'task_input');
    } catch (error) {
      setStatus(attachmentStatus, '无法上传 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    try {
      const csrfToken = await csrf();
      setStatus(attachmentStatus, '正在计算 SHA-256…', 'pending');
      const buffer = await file.arrayBuffer();
      const sha = await sha256Hex(buffer);
      const ticket = await createUploadRequest(payload, csrfToken);
      await putUploadBytes(ticket.upload_url, buffer);
      const artifact = await completeUploadRequest(ticket.artifact_id, sha, csrfToken);
      attachmentIDs.push(artifact.id);
      renderAttachmentLines();
      setStatus(attachmentStatus, '附件已提交后台校验；稍后才会变为 ready。', 'pending');
      await waitForReady(artifact.id, 0);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus(attachmentStatus, '上传失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function waitForReady(artifactID, attempts) {
    if (!shouldPollArtifact('scanning', attempts, 100)) {
      setStatus(attachmentStatus, '附件仍在扫描，请稍后刷新。', 'pending');
      return;
    }
    const found = await fetchArtifact(artifactID);
    if (found.artifact.state === 'ready') {
      setStatus(attachmentStatus, '附件已就绪。', 'ok');
      return;
    }
    if (found.artifact.state === 'quarantined') {
      setStatus(attachmentStatus, '附件被隔离：' + quarantineMessage(found.quarantineReason), 'bad');
      return;
    }
    setTimeout(() => waitForReady(artifactID, attempts + 1), 800);
  }

  document.getElementById('conversation-create').addEventListener('click', async () => {
    try {
      const csrfToken = await csrf();
      setStatus(conversationStatus, '正在创建会话…', 'pending');
      const created = await api('/api/v1/conversations', { method: 'POST', csrf: csrfToken });
      setStatus(conversationStatus, '已创建会话 ' + created.id.slice(0, 8), 'ok');
      await loadConversations();
      await selectConversation(created.id);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus(conversationStatus, '创建会话失败 [' + error.code + '] ' + error.message, 'bad');
    }
  });

  document.getElementById('chat-attachment-upload').addEventListener('click', uploadAttachment);
  document.getElementById('chat-handoff-load-artifacts').addEventListener(
    'click', loadHandoffArtifacts);
  document.getElementById('chat-handoff-preview-button').addEventListener(
    'click', previewHandoff);
  document.getElementById('chat-handoff-source-runs').addEventListener(
    'input', invalidateHandoffPreview);
  document.getElementById('chat-handoff-confirmed').addEventListener(
    'change', () => {
      if (!document.getElementById('chat-handoff-confirmed').checked) {
        setStatus(handoffStatus, '未确认时不会提交交接范围。', 'pending');
      }
    });

  document.getElementById('chat-message-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    stopPolling();
    const generation = selectionGeneration;
    if (!OrcharaRunStateGuard.ready) {
      // C6.01: a new request dispatches a Run whose state this page cannot
      // interpret, so the related operation is paused with an explicit message.
      // Independent functions (conversation, attachments, handoff preview) stay.
      setStatus(
        submitStatus,
        '运行状态暂不可用：已暂停提交需求与运行状态观察；请刷新页面重试。',
        'bad',
      );
      return;
    }
    let body;
    try {
      const handoffConfirmed = document.getElementById('chat-handoff-confirmed').checked;
      const sourceRunText = document.getElementById('chat-handoff-source-runs').value.trim();
      let handoff = null;
      if (currentHandoffPreview) {
        // The submitted handoff is the confirmed server preview, never an edited
        // summary: the summary element is display-only.
        handoff = handoffFromPreview(currentHandoffPreview, handoffConfirmed);
      } else if (sourceRunText || handoffArtifactIDs.length > 0 || handoffConfirmed) {
        throw pageFailure('handoff_preview_required', '请先生成服务端交接预览并确认范围');
      }
      body = submitBody(
        currentConversationID,
        document.getElementById('chat-message').value,
        attachmentIDs,
        document.getElementById('chat-version-id').value,
        handoff,
      );
    } catch (error) {
      setStatus(submitStatus, '无法提交 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    if (!pendingAttempt || JSON.stringify(pendingAttempt.body) !== JSON.stringify(body)) {
      pendingAttempt = newSubmissionAttempt(body);
    }
    try {
      const csrfToken = await csrf();
      setStatus(submitStatus, '正在提交需求…', 'pending');
      const result = await apiFull('/api/v1/requests', {
        method: 'POST',
        body: pendingAttempt.body,
        csrf: csrfToken,
        headers: submitRequestHeaders(pendingAttempt),
      });
      const submittedRun = result.payload.run_id ? await fetchRun(result.payload.run_id) : null;
      // C6.02: a selection switch while the submit was in flight keeps the newer
      // panel; the submitted request still exists in the refreshed queue.
      const stale = generation !== selectionGeneration;
      if (!stale) {
        renderRequest(result.payload, requestFacts(result.headers), submittedRun);
      }
      pendingAttempt = null;
      setStatus(submitStatus, '请求已进入会话队列。', 'ok');
      await loadMessages();
      await refreshQueue();
      if (!stale && generation === selectionGeneration) {
        poll(0);
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      // Keep pendingAttempt only when the retry is safe: a network failure never
      // reached the server, but a 409 idempotency_conflict means the key now
      // belongs to another body and must not be replayed silently.
      const failure = describeSubmitFailure(error);
      if (!failure.reuseKey) {
        pendingAttempt = null;
      }
      setStatus(submitStatus, failure.text, 'bad');
    }
  });

  document.getElementById('chat-cancel').addEventListener('click', () => {
    cancelRequest(currentRequestID);
  });

  document.getElementById('interaction-reply').addEventListener('click', async () => {
    const interactionStatus = document.getElementById('interaction-status');
    if (!currentRequestID || !currentInteraction) {
      return;
    }
    if (!OrcharaRunStateGuard.ready) {
      setStatus(
        interactionStatus,
        '运行状态暂不可用：已暂停提交答复与运行状态观察；请刷新页面重试。',
        'bad',
      );
      return;
    }
    const mode = interactionMode(currentInteraction);
    const candidate = mode === 'selection' ? chosenCandidateID() : '';
    if (mode === 'selection' && !candidate) {
      setStatus(interactionStatus, '请先选择一个候选应用。', 'bad');
      return;
    }
    let body;
    try {
      // Post-review fix (#80): the missing-input reply carries the artifacts the
      // user uploaded for it; a hard-coded [] could never complete a CSV request.
      body = interactionReplyBody(
        currentInteraction.interaction_id,
        document.getElementById('interaction-message').value,
        candidate,
        replyArtifactIDs(attachmentIDs),
      );
    } catch (error) {
      setStatus(interactionStatus, '无法答复 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    if (!currentInteractionAttempt ||
        JSON.stringify(currentInteractionAttempt.body) !== JSON.stringify(body)) {
      currentInteractionAttempt = newInteractionAttempt(body);
    }
    const requestID = currentRequestID;
    const generation = selectionGeneration;
    try {
      const csrfToken = await csrf();
      setStatus(interactionStatus, '正在提交答复…', 'pending');
      const result = await apiFull(requestReplyPath(requestID), {
        method: 'POST',
        body: currentInteractionAttempt.body,
        csrf: csrfToken,
        headers: interactionReplyHeaders(currentInteractionAttempt),
      });
      currentInteractionAttempt = null;
      if (generation !== selectionGeneration || requestID !== currentRequestID) {
        // A different request is selected now; the late reply must not render
        // into (or poll for) the newer selection.
        return;
      }
      renderRequest(result.payload, requestFacts(result.headers), null);
      setStatus(interactionStatus, '答复已提交，正在继续选择应用。', 'ok');
      await loadMessages();
      await refreshQueue();
      if (generation === selectionGeneration) {
        poll(0);
      }
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      if (generation !== selectionGeneration || requestID !== currentRequestID) {
        return;
      }
      // Keep currentInteractionAttempt: a safe retry reuses the same
      // Idempotency-Key, while a closed interaction is explained instead of
      // being retried behind the user's back.
      const failure = describeReplyFailure(error);
      setStatus(interactionStatus, failure.text, 'bad');
      poll(0);
    }
  });

  (async () => {
    try {
      await requireLogin();
      await loadConversations();
    } catch (error) {
      setStatus(conversationStatus, '需要登录：' + error.message, 'bad');
    }
  })();
}

if (typeof document !== 'undefined' && document.getElementById) {
  initChat();
}
