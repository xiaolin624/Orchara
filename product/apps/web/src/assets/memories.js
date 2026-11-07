/*
 * F09/T054: personal long-term memory page.
 * F09/T058: revision updates with a strong ETag, conflict recovery and deletion.
 *
 * The page shows two server-owned read models and three actions:
 *
 *   GET  /api/v1/memory-candidates   the owner's candidates (source, application
 *                                    scope, real server deadline, state)
 *   POST /api/v1/memory-candidates/{id}/decision
 *                                    confirm or reject, with a required
 *                                    Idempotency-Key and the session CSRF token
 *   GET  /api/v1/memories            the owner's confirmed memories
 *   GET  /api/v1/memories/{id}       one memory plus its strong ETag
 *   PATCH /api/v1/memories/{id}      revise the text with If-Match
 *   DELETE /api/v1/memories/{id}     tombstone; answers a deletion job
 *
 * Every text node is assigned through textContent; the page never builds markup
 * from a server value and keeps no credential or memory text in the browser.
 * The helpers below are pure so the page logic can be executed under Node, which
 * is how the acceptance test observes it.
 *
 * Two guarantees are worded carefully because the ticket only owns one of them:
 * the delete stops recall synchronously (revision + cache epoch moved before the
 * 202 answer), while the physical copy purge is asynchronous F07 work. The page
 * says exactly that and never claims the bytes are gone.
 */

const CANDIDATE_STATES = {
  pending: '待确认',
  confirmed: '已确认',
  rejected: '已拒绝',
  expired: '已过期',
};

/** The Chinese label for one candidate state; an unknown value is not guessed. */
function candidateStateText(state) {
  return CANDIDATE_STATES[String(state === undefined || state === null ? '' : state)] || '未知状态';
}

function shortId(value) {
  const text = String(value === undefined || value === null ? '' : value);
  return text.length > 8 ? text.slice(0, 8) : text;
}

/**
 * One contract MemoryCandidate projected for display.
 *
 * `sourceText` names the provenance class: the candidate was proposed from
 * inside a Run and sealed server-side. The contract MemoryCandidate schema has no
 * run id field (additionalProperties:false), so the page does not invent one; the
 * stored source run is a platform fact for runtime recall (F09 follow-ups).
 */
function describeCandidate(candidate) {
  const entry = candidate || {};
  const state = String(entry.state || '');
  const applicationID = String(entry.application_id || '');
  return {
    id: String(entry.id || ''),
    idShort: shortId(entry.id),
    text: String(entry.text || ''),
    applicationText: applicationID,
    state,
    stateText: candidateStateText(state),
    isPending: state === 'pending',
    expiresAt: String(entry.expires_at || ''),
    sourceText: '来源：Run 内自动抽取的记忆候选（交互上下文由服务端封存；确认前不可召回）',
    // The contract MemoryCandidate schema has no source_run_id and
    // additionalProperties:false, so the page says the interaction is sealed
    // instead of inventing an id the server never sent.
    interactionText: '交互（Run）：服务端封存，确认前不会进入任何应用的上下文；契约不含 source_run_id，页面不编造具体 Run id。',
    saveScopeText: '保存范围：仅当前使用者 + 应用 ' + (applicationID || '（未知应用）')
      + '；换到另一个应用不会带走，管理员也看不到。',
    defaultDecision: 'none',
  };
}

/** One contract Memory projected for display. Memories have no state or expiry. */
function describeMemory(memory) {
  const entry = memory || {};
  const revision = Number(entry.revision || 0);
  return {
    id: String(entry.id || ''),
    idShort: shortId(entry.id),
    text: String(entry.text || ''),
    applicationText: String(entry.application_id || ''),
    revision,
    revisionText: 'revision ' + revision,
    sourceText: '来源：已确认的个人记忆（可按 revision 更新）',
  };
}

// ---------------------------------------------------------------------------
// F09/T058: revision updates with a strong ETag
// ---------------------------------------------------------------------------
/** The contract detail path. A raw id always goes through encodeURIComponent. */
function memoryPath(memoryId) {
  return '/api/v1/memories/' + encodeURIComponent(String(memoryId === undefined || memoryId === null ? '' : memoryId));
}

function deletionPath(memoryId) {
  return memoryPath(memoryId);
}

/**
 * The strong ETag from a response.
 *
 * `apiFull` exposes the real Headers object; the Node harness passes a plain
 * object. Both shapes are read, case-insensitively, and a missing header is an
 * empty string rather than a guess (a write must never be attempted without it).
 */
function etagFromHeaders(headers) {
  if (!headers) {
    return '';
  }
  if (typeof headers.get === 'function') {
    const value = headers.get('ETag');
    return value === null || value === undefined ? '' : String(value);
  }
  for (const key of Object.keys(headers)) {
    if (String(key).toLowerCase() === 'etag') {
      const value = headers[key];
      return value === null || value === undefined ? '' : String(value);
    }
  }
  return '';
}

/**
 * The exact contract updateMemory body: {text, confirmed:true}, and nothing else.
 * A blank or oversized body never leaves the page; the server would refuse it,
 * but a client-side refusal keeps the user's text and avoids a pointless write.
 */
function updatePayload(text) {
  const value = String(text === undefined || text === null ? '' : text);
  if (value.trim() === '') {
    throw new Error('记忆内容不能为空');
  }
  if (Array.from(value).length > 4000) {
    throw new Error('记忆内容最多 4000 个字符');
  }
  return { text: value, confirmed: true };
}

/** The write headers: the ETag just read, as the required If-Match token. */
function updateHeaders(etag) {
  const token = String(etag === undefined || etag === null ? '' : etag).trim();
  if (token === '') {
    throw new Error('缺少 ETag：必须先重新读取这条记忆，服务端不会静默覆盖');
  }
  return { 'If-Match': token };
}

/** The 412 explanation: what the server now has, and what the page will do. */
function conflictText(details) {
  const entry = details || {};
  const etag = String(entry.current_etag || '');
  const revision = Number(entry.current_revision || 0);
  return '版本冲突：服务器上这条记忆已经是 revision ' + revision + '（ETag ' + etag +
    '）。页面已取得最新 ETag；用“用最新版本重试”会以该版本为基准保存你上面的正文' +
    '（不会再静默覆盖，也不会丢掉你输入的内容）。';
}

/**
 * Classify a failed write so the page can recover instead of showing a generic
 * error: a 412 is a conflict with the current ETag to adopt, a 428 is a missing
 * precondition (a page bug: it must read the detail first), anything else keeps
 * the server's own code and message.
 */
function preconditionPlan(failure) {
  const entry = failure || {};
  const details = entry.details || {};
  const status = Number(entry.status || 0);
  if (status === 412) {
    return {
      kind: 'conflict',
      isConflict: true,
      currentEtag: String(details.current_etag || ''),
      currentRevision: Number(details.current_revision || 0),
      message: conflictText(details),
    };
  }
  if (status === 428) {
    return {
      kind: 'precondition_required',
      isConflict: false,
      currentEtag: '',
      currentRevision: 0,
      message: '缺少 If-Match：请先重新读取这条记忆取得 ETag 再提交修改；服务端不会静默覆盖。',
    };
  }
  return {
    kind: 'failed',
    isConflict: false,
    currentEtag: '',
    currentRevision: 0,
    message: errorText(entry),
  };
}

/** A successful save states the server's revision, never an optimistic claim. */
function updateResultText(memory) {
  const view = describeMemory(memory);
  return '已保存：记忆 ' + view.idShort + ' 现在是 revision ' + view.revision + '。';
}

/** After a delete the memory is gone from the recall set; the bytes are not. */
function memoryDeletedText(memoryId) {
  return '记忆 ' + shortId(memoryId) + ' 已删除：它已从读取与召回集合移除。';
}

// ---------------------------------------------------------------------------
// F09/T058: deletion state
// ---------------------------------------------------------------------------
const DELETION_STATES = {
  pending: '待处理',
  running: '处理中',
  completed: '已完成',
  failed: '失败',
};

/** The Chinese label for one deletion state; an unknown value is not guessed. */
function deletionStateText(state) {
  const key = String(state === undefined || state === null ? '' : state);
  return DELETION_STATES[key] || '未知删除状态';
}

/**
 * One contract DeletionAccepted projected for display.
 *
 * `id` is the deletion_job id, not the memory id (the memory is `resource_id`).
 * The recall sentence is this ticket's synchronous guarantee; the cleanup
 * sentence reports the job's real state and never claims the bytes are gone.
 */
function describeDeletion(job) {
  const entry = job || {};
  const state = String(entry.state || '');
  const stateText = deletionStateText(state);
  return {
    id: String(entry.id || ''),
    idShort: shortId(entry.id),
    state,
    stateText,
    resourceId: String(entry.resource_id || ''),
    recallText: '旧内容已停止召回：删除时服务端已同步提升 revision 与缓存 epoch，'
      + '读取和后续模型输入不再返回旧正文。',
    cleanupText: '物理副本清理：' + stateText
      + '（由删除治理异步执行；本页不声称副本已删除）。',
  };
}

/** What the human is about to do, stated before the delete request is sent. */
function deletionConfirmationText(memory) {
  const view = describeMemory(memory);
  return '删除后这条记忆（' + view.idShort + '）会立即停止召回：读取、列表和后续模型输入'
    + '都不再返回旧正文。物理副本清理由删除治理异步执行。继续删除？';
}

/** The server's own deletion answer, with the job id and its real state. */
function deletionResultText(job) {
  const view = describeDeletion(job);
  return '已接受删除：删除任务 ' + view.idShort + '（' + view.stateText + '）。' + view.recallText;
}

/** The exact contract decision body; there is no default choice. */
function decisionPayload(decision) {
  const value = String(decision === undefined || decision === null ? '' : decision).trim();
  if (value !== 'confirm' && value !== 'reject') {
    throw new Error('decision must be confirm or reject');
  }
  return { decision: value };
}

/** What the human is about to do, stated before the request is sent. */
function decisionConfirmationText(decision) {
  if (decision === 'confirm') {
    return '确认后这条候选才会保存为你的个人记忆；确认前不可召回，也不会自动保存。';
  }
  return '拒绝后不会生成记忆，这条候选不再可确认。';
}

/** A fresh Idempotency-Key per decision click, so a retry is never a second write. */
function newDecisionKey() {
  if (typeof globalThis !== 'undefined' && globalThis.crypto
      && typeof globalThis.crypto.randomUUID === 'function') {
    return globalThis.crypto.randomUUID();
  }
  return 'decision-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2);
}

function decisionPath(candidateId) {
  return '/api/v1/memory-candidates/' + encodeURIComponent(String(candidateId)) + '/decision';
}

function listPath(base, options) {
  const settings = options || {};
  const params = [];
  if (settings.cursor) {
    params.push('cursor=' + encodeURIComponent(String(settings.cursor)));
  }
  if (settings.limit !== undefined && settings.limit !== null) {
    params.push('limit=' + encodeURIComponent(String(settings.limit)));
  }
  if (settings.applicationId) {
    params.push('application_id=' + encodeURIComponent(String(settings.applicationId)));
  }
  return params.length ? base + '?' + params.join('&') : base;
}

function candidatesListPath(options) {
  return listPath('/api/v1/memory-candidates', options);
}

function memoriesListPath(options) {
  return listPath('/api/v1/memories', options);
}

/** The server's own decision answer, never an optimistic client claim. */
function decisionResultText(result) {
  const entry = result || {};
  if (entry.state === 'confirmed') {
    return '已确认：记忆 ' + shortId(entry.memory_id) + ' 已保存。';
  }
  if (entry.state === 'rejected') {
    return '已拒绝：未保存任何记忆。';
  }
  return '未知状态：' + String(entry.state || '');
}

/*
 * F12/T098: a refused candidate decision is a state, not a generic error.
 *
 * A candidate can expire while the page is open (the server enforces the
 * deadline), and a repeated decision can come back as a conflict. The page must
 * say that nothing was saved and that the old candidate cannot be revived; it
 * never retries the decision behind the user's back.
 */
function decisionFailurePlan(error) {
  const status = Number((error && error.status) || 0);
  const code = String((error && error.code) || '');
  const message = String((error && error.message) || '决定提交失败');
  const table = {
    memory_candidate_expired:
      '候选已过期（memory_candidate_expired）：没有保存任何记忆，这条候选也不能复活。'
      + '下一步：重新运行产生新的候选；页面不会自动重试。',
    memory_candidate_rejected:
      '候选此前已被拒绝（memory_candidate_rejected）：不会再生成记忆；'
      + '如果需要保存，请重新产生新候选。',
    memory_candidate_confirmed:
      '候选此前已被确认（memory_candidate_confirmed）：记忆已经存在，不会写第二条。'
      + '请刷新候选/记忆列表查看。',
    idempotency_conflict:
      '这次决定与同一个幂等键的旧内容不同（idempotency_conflict）：服务端保留了第一次决定。'
      + '请刷新列表核对，本页不会用旧键覆盖。',
    resource_not_found:
      '候选不存在或不属于你（resource_not_found）：不会显示他人的候选，也不会自动重试。',
  };
  return {
    code: code,
    status: status,
    expired: code === 'memory_candidate_expired',
    refresh: Boolean(code),
    text: table[code] || ('决定提交失败 [' + (code || ('HTTP ' + status)) + '] ' + message
      + '；页面没有保存任何记忆，也不会自动重试。'),
  };
}

/** A contract error rendered with its own code, so a refusal is not generic. */
function errorText(error) {
  const detail = error || {};
  if (detail.code) {
    return String(detail.code) + '：' + String(detail.message || '');
  }
  return String(detail.message || '请求失败');
}

// ---------------------------------------------------------------------------
// browser wiring (skipped when the helper is executed without a page)
// ---------------------------------------------------------------------------
const PAGE_STATE = {
  candidates: { cursor: null },
  memories: { cursor: null },
  // Set when a save hits a 412: the user's text and the server's current ETag,
  // kept only in this page so an explicit retry can adopt the new revision.
  pendingConflict: null,
};

function memoryPageReady() {
  return typeof document !== 'undefined'
    && typeof document.getElementById === 'function'
    && document.getElementById('candidates-list') !== null;
}

function setStatus(elementId, text, kind) {
  const element = document.getElementById(elementId);
  if (!element) {
    return;
  }
  element.textContent = text;
  element.className = 'status' + (kind ? ' ' + kind : '');
}

function addFieldRow(item, label, value) {
  const row = document.createElement('div');
  row.className = 'row';
  const name = document.createElement('span');
  name.className = 'label';
  name.textContent = label;
  const text = document.createElement('span');
  text.className = 'value';
  text.textContent = value;
  row.appendChild(name);
  row.appendChild(text);
  item.appendChild(row);
}

function decisionButtons(candidate) {
  const actions = document.createElement('div');
  actions.className = 'decision-buttons';
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.className = 'confirm-button';
  confirm.textContent = '确认并保存为记忆';
  confirm.addEventListener('click', () => {
    decideCandidate(candidate, 'confirm').then(loadCandidates).catch(showError);
  });
  const reject = document.createElement('button');
  reject.type = 'button';
  reject.className = 'reject-button';
  reject.textContent = '拒绝';
  reject.addEventListener('click', () => {
    decideCandidate(candidate, 'reject').then(loadCandidates).catch(showError);
  });
  actions.appendChild(confirm);
  actions.appendChild(reject);
  return actions;
}

async function decideCandidate(candidate, decision) {
  if (!window.confirm(decisionConfirmationText(decision))) {
    return null;
  }
  try {
    const token = await csrf();
    const result = await api(decisionPath(candidate.id), {
      method: 'POST',
      csrf: token,
      headers: { 'Idempotency-Key': newDecisionKey() },
      body: decisionPayload(decision),
    });
    setStatus('decision-status', decisionResultText(result), 'ok');
    return result;
  } catch (error) {
    const plan = decisionFailurePlan(error);
    setStatus('decision-status', plan.text, 'bad');
    if (plan.refresh) {
      try {
        // Re-read the real candidate state (a 409 expired row is persisted by
        // the server); the refusal text is kept on screen afterwards.
        await loadCandidates();
        setStatus('decision-status', plan.text, 'bad');
      } catch (reloadError) {
        // The refusal is already displayed; a failed re-read is not a decision.
      }
    }
    return null;
  }
}

// ---------------------------------------------------------------------------
// F09/T058: edit (strong ETag) and delete (deletion job) wiring
// ---------------------------------------------------------------------------
function memoryEditor(memory) {
  const editor = document.createElement('div');
  editor.className = 'memory-editor';
  const textarea = document.createElement('textarea');
  textarea.className = 'memory-text';
  textarea.rows = 3;
  textarea.value = String(memory.text || '');
  const actions = document.createElement('div');
  actions.className = 'decision-buttons';
  const save = document.createElement('button');
  save.type = 'button';
  save.className = 'save-button';
  save.textContent = '保存修改';
  save.addEventListener('click', () => {
    saveMemory(memory, textarea.value).catch(showError);
  });
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'delete-button';
  remove.textContent = '删除这条记忆';
  remove.addEventListener('click', () => {
    deleteMemory(memory).catch(showError);
  });
  actions.appendChild(save);
  actions.appendChild(remove);
  editor.appendChild(textarea);
  editor.appendChild(actions);
  return editor;
}

function showConflictRetry() {
  const retry = document.getElementById('memory-conflict-retry');
  if (!retry) {
    return;
  }
  retry.hidden = false;
  retry.onclick = () => {
    retryConflict().catch(showError);
  };
}

function hideConflictRetry() {
  const retry = document.getElementById('memory-conflict-retry');
  if (retry) {
    retry.hidden = true;
    retry.onclick = null;
  }
}

/** One PATCH with the ETag the page has; the server decides if it is current. */
async function writeMemory(memoryId, text, etag) {
  const token = await csrf();
  const response = await apiFull(memoryPath(memoryId), {
    method: 'PATCH',
    csrf: token,
    headers: updateHeaders(etag),
    body: updatePayload(text),
  });
  return response.payload;
}

/**
 * Save one edit. The detail read is deliberate: the list response carries no
 * ETag, so the page asks for the current revision first and echoes exactly that
 * token. A 412 is shown as a recoverable conflict, never retried silently.
 */
async function saveMemory(memory, text) {
  updatePayload(text); // refuse a blank/oversized body before any request
  const detail = await apiFull(memoryPath(memory.id));
  const etag = etagFromHeaders(detail.headers);
  return submitMemory(memory.id, text, etag);
}

async function submitMemory(memoryId, text, etag) {
  try {
    const saved = await writeMemory(memoryId, text, etag);
    PAGE_STATE.pendingConflict = null;
    hideConflictRetry();
    setStatus('memory-action-status', updateResultText(saved), 'ok');
    await loadMemories();
    return saved;
  } catch (error) {
    const plan = preconditionPlan(error);
    if (plan.kind === 'conflict') {
      // Keep the user's text and adopt the server's current ETag; only an
      // explicit click on "用最新版本重试" writes again, now on a revision the
      // user has been told about.
      PAGE_STATE.pendingConflict = { memoryId, text, etag: plan.currentEtag };
      setStatus('memory-action-status', plan.message, 'bad');
      showConflictRetry();
      return null;
    }
    throw error;
  }
}

async function retryConflict() {
  const pending = PAGE_STATE.pendingConflict;
  if (!pending || !pending.etag) {
    throw new Error('没有待重试的版本冲突');
  }
  return submitMemory(pending.memoryId, pending.text, pending.etag);
}

async function deleteMemory(memory) {
  if (!window.confirm(deletionConfirmationText(memory))) {
    return null;
  }
  const token = await csrf();
  const job = await api(deletionPath(memory.id), { method: 'DELETE', csrf: token });
  setStatus(
    'memory-action-status',
    deletionResultText(job) + ' ' + memoryDeletedText(memory.id),
    'ok',
  );
  renderDeletion(job);
  await loadMemories();
  return job;
}

/** The deletion state the server reported, including the honest cleanup state. */
function renderDeletion(job) {
  const panel = document.getElementById('deletion-status');
  const card = document.getElementById('deletion-card');
  if (!panel) {
    return;
  }
  const view = describeDeletion(job);
  panel.replaceChildren();
  addFieldRow(panel, '删除状态', view.stateText);
  addFieldRow(panel, '删除任务', view.id);
  addFieldRow(panel, '记忆 id', view.resourceId);
  addFieldRow(panel, '召回', view.recallText);
  addFieldRow(panel, '物理副本', view.cleanupText);
  if (card) {
    card.hidden = false;
  }
}

function renderCandidate(item, candidate) {
  const view = describeCandidate(candidate);
  // F12/T098: the candidate is never pre-decided. The card carries no checked
  // control and no batch "select all"; the user confirms/rejects one candidate.
  item.dataset.selection = view.defaultDecision;
  addFieldRow(item, '来源', view.sourceText);
  addFieldRow(item, '交互（Run）', view.interactionText);
  addFieldRow(item, '保存范围', view.saveScopeText);
  addFieldRow(item, '应用范围', view.applicationText);
  addFieldRow(item, '状态', view.stateText);
  addFieldRow(item, '过期时间', view.expiresAt);
  addFieldRow(item, '候选内容', view.text);
  addFieldRow(item, '候选 id', view.id);
  if (view.isPending) {
    item.appendChild(decisionButtons(candidate));
  }
}

function renderMemory(item, memory) {
  const view = describeMemory(memory);
  addFieldRow(item, '来源', view.sourceText);
  addFieldRow(item, '应用范围', view.applicationText);
  addFieldRow(item, '版本', view.revisionText);
  addFieldRow(item, '内容', view.text);
  addFieldRow(item, '记忆 id', view.id);
  item.appendChild(memoryEditor(memory));
}

async function loadCandidates() {
  const list = document.getElementById('candidates-list');
  setStatus('candidates-status', '正在加载候选…');
  const page = await api(candidatesListPath({ cursor: PAGE_STATE.candidates.cursor }));
  list.replaceChildren();
  for (const candidate of page.items) {
    const item = document.createElement('li');
    renderCandidate(item, candidate);
    list.appendChild(item);
  }
  document.getElementById('candidates-empty').hidden = page.items.length !== 0;
  const next = document.getElementById('candidates-next');
  next.hidden = !page.next_cursor;
  next.onclick = () => {
    PAGE_STATE.candidates.cursor = page.next_cursor;
    loadCandidates().catch(showError);
  };
  setStatus(
    'candidates-status',
    page.items.length === 0 ? '当前没有候选（确认前不会成为记忆）。' : '本页 ' + page.items.length + ' 条候选。',
    page.items.length === 0 ? '' : 'ok',
  );
}

async function loadMemories() {
  const list = document.getElementById('memories-list');
  setStatus('memories-status', '正在加载记忆…');
  const page = await api(memoriesListPath({ cursor: PAGE_STATE.memories.cursor }));
  list.replaceChildren();
  for (const memory of page.items) {
    const item = document.createElement('li');
    renderMemory(item, memory);
    list.appendChild(item);
  }
  document.getElementById('memories-empty').hidden = page.items.length !== 0;
  const next = document.getElementById('memories-next');
  next.hidden = !page.next_cursor;
  next.onclick = () => {
    PAGE_STATE.memories.cursor = page.next_cursor;
    loadMemories().catch(showError);
  };
  setStatus(
    'memories-status',
    page.items.length === 0 ? '还没有已确认的个人记忆。' : '本页 ' + page.items.length + ' 条记忆。',
    page.items.length === 0 ? '' : 'ok',
  );
}

async function refresh() {
  await requireLogin();
  await loadCandidates();
  await loadMemories();
}

function showError(error) {
  setStatus('candidates-status', errorText(error), 'bad');
  setStatus('memories-status', errorText(error), 'bad');
}

if (memoryPageReady()) {
  refresh().catch(showError);
}
