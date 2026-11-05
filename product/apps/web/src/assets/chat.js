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

/** A request is polled only while its state can still move (queued -> dispatched). */
function shouldPollRequest(state, attempts, maxAttempts) {
  if (!state || TERMINAL_REQUEST_STATES.includes(state)) {
    return false;
  }
  if (state === 'dispatched') {
    // The handling version is already visible; the request state is stable.
    return false;
  }
  return WAITING_STATES.includes(state) && attempts < Math.max(1, maxAttempts || 1);
}

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

/** Pending interaction projection; `visible:false` means there is nothing to answer. */
function describeInteraction(interaction) {
  if (!interaction) {
    return { visible: false, kind: '', prompt: '', candidates: [], expiresAt: '', closed: true };
  }
  const proposal = interaction.proposal || {};
  const candidates = Array.isArray(proposal.candidates)
    ? proposal.candidates.map((candidate) => ({
        id: candidate.application_version_id || candidate.id || '',
        label: candidate.label || candidate.name || candidate.version_no || '',
      }))
    : [];
  return {
    visible: true,
    kind: interaction.kind || '',
    prompt: proposal.question || proposal.prompt || '',
    candidates: candidates,
    expiresAt: interaction.expires_at || '',
    closed: false,
  };
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

/** The handling version sentence; never guesses a version that is not known. */
function describeHandling(request, run, version) {
  if (!request || !request.run_id) {
    return { text: '尚无处理应用', handled: false, versionNo: 0 };
  }
  const versionNo = version && Number.isInteger(version.version_no) ? version.version_no : 0;
  const prefix = versionNo > 0 ? 'v' + versionNo : '处理版本';
  const state = (run && run.state) || '';
  if (state === 'succeeded') {
    return { text: prefix + ' 已处理', handled: true, versionNo: versionNo };
  }
  if (state === 'failed') {
    return { text: prefix + ' 处理失败', handled: true, versionNo: versionNo };
  }
  if (state === 'cancelled') {
    return { text: prefix + ' 已取消', handled: true, versionNo: versionNo };
  }
  return { text: prefix + ' 正在处理', handled: true, versionNo: versionNo };
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
  let attachmentIDs = [];
  let pollTimer = null;
  let pendingAttempt = null;

  const conversationStatus = document.getElementById('conversation-status');
  const submitStatus = document.getElementById('chat-submit-status');
  const attachmentStatus = document.getElementById('chat-attachment-status');

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
    currentConversationID = conversationID;
    currentRequestID = null;
    attachmentIDs = [];
    document.getElementById('chat-conversation-id').textContent = conversationID;
    panel.hidden = false;
    renderAttachmentLines();
    await loadMessages();
    await refreshQueue();
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
    const queue = describeQueue(
      requests.map((request) => ({ id: request.id, state: request.state })),
      currentRequestID,
    );
    document.getElementById('chat-queue-status').textContent = queue.text;
    for (const row of queue.rows) {
      const item = document.createElement('li');
      item.textContent = row.position + '. ' + row.label + ' · ' + row.id.slice(0, 8);
      queueList.appendChild(item);
    }
  }

  function renderRequest(request, facts, run) {
    currentRequestID = request.id;
    document.getElementById('request-panel').hidden = false;
    document.getElementById('chat-request-id').textContent = request.id;
    document.getElementById('chat-request-state').textContent = describeRequestState(request.state);
    const handling = describeHandling(
      request,
      run || null,
      facts && facts.versionNo ? { version_no: facts.versionNo } : null,
    );
    document.getElementById('chat-handling').textContent = handling.text;
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
    const run = result.payload.run_id ? await fetchRun(result.payload.run_id) : null;
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
    try {
      const result = await fetchRequest(currentRequestID);
      renderRequest(result.request, result.facts, result.run);
      await refreshQueue();
      if (!shouldPollRequest(result.request.state, attempts, 100)) {
        stopPolling();
        if (requestIsTerminal(result.request.state)) {
          setStatus(submitStatus, '请求已' + describeRequestState(result.request.state), 'ok');
        }
        return;
      }
      pollTimer = setTimeout(() => poll(attempts + 1), 800);
    } catch (error) {
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

  document.getElementById('chat-message-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    stopPolling();
    let body;
    try {
      const handoffConfirmed = document.getElementById('chat-handoff-confirmed').checked;
      const handoff =
        handoffConfirmed ||
        document.getElementById('chat-handoff-source-runs').value.trim() ||
        document.getElementById('chat-handoff-summary').value.trim()
          ? handoffBody(
              document.getElementById('chat-handoff-source-runs').value,
              document.getElementById('chat-handoff-summary').value,
              handoffConfirmed,
              attachmentIDs,
            )
          : null;
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
      renderRequest(result.payload, requestFacts(result.headers), submittedRun);
      pendingAttempt = null;
      setStatus(submitStatus, '请求已进入会话队列。', 'ok');
      await loadMessages();
      await refreshQueue();
      poll(0);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      // Keep pendingAttempt: a network failure is retried with the same key.
      setStatus(submitStatus, '提交失败 [' + error.code + '] ' + error.message, 'bad');
    }
  });

  document.getElementById('chat-cancel').addEventListener('click', async () => {
    if (!currentRequestID) {
      return;
    }
    try {
      const csrfToken = await csrf();
      await api(requestCancelPath(currentRequestID), { method: 'POST', csrf: csrfToken });
      setStatus(submitStatus, '已接受取消意图，正在刷新状态…', 'pending');
      await loadMessages();
      await refreshQueue();
      poll(0);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus(submitStatus, '取消失败 [' + error.code + '] ' + error.message, 'bad');
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
