/*
 * F09/T054: personal long-term memory page.
 *
 * The page shows two server-owned read models and one decision action:
 *
 *   GET  /api/v1/memory-candidates   the owner's candidates (source, application
 *                                    scope, real server deadline, state)
 *   POST /api/v1/memory-candidates/{id}/decision
 *                                    confirm or reject, with a required
 *                                    Idempotency-Key and the session CSRF token
 *   GET  /api/v1/memories            the owner's confirmed memories
 *
 * Every text node is assigned through textContent; the page never builds markup
 * from a server value and keeps no credential or memory text in the browser.
 * The helpers below are pure so the page logic can be executed under Node, which
 * is how the acceptance test observes it.
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
  return {
    id: String(entry.id || ''),
    idShort: shortId(entry.id),
    text: String(entry.text || ''),
    applicationText: String(entry.application_id || ''),
    state,
    stateText: candidateStateText(state),
    isPending: state === 'pending',
    expiresAt: String(entry.expires_at || ''),
    sourceText: '来源：Run 内提出的记忆候选（服务端封存；确认前不可召回）',
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
  const token = await csrf();
  const result = await api(decisionPath(candidate.id), {
    method: 'POST',
    csrf: token,
    headers: { 'Idempotency-Key': newDecisionKey() },
    body: decisionPayload(decision),
  });
  setStatus('decision-status', decisionResultText(result), 'ok');
  return result;
}

function renderCandidate(item, candidate) {
  const view = describeCandidate(candidate);
  addFieldRow(item, '来源', view.sourceText);
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
