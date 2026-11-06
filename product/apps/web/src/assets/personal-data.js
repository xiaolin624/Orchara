'use strict';

/**
 * F11/T069: the personal-data page logic.
 *
 * The page is a thin, honest projection of the contract:
 *
 *   DELETE /api/v1/{memories|knowledge-documents|artifacts}/{id} -> 202 DeletionAccepted
 *   GET    /api/v1/deletions/{deletion_job_id}                   -> 200 DeletionJob
 *
 * The deletion answer names the job, not the resource. The tombstone has already
 * committed when the 202 arrives, so the page removes the item from its list
 * immediately; physical cleanup stays asynchronous (T071/T072). The page never
 * claims the bytes are gone and never polls forever: it stops after a bounded
 * number of progress reads and displays the remaining scopes as the server
 * reported them.
 *
 * No HTML-string injection, no browser storage, no credentials: every server
 * string is rendered with textContent, exactly like the other authenticated
 * pages.
 */

var DELETION_STATES = {
  pending: '待处理',
  running: '执行中',
  completed: '已完成',
  failed: '失败'
};

var SCOPE_TEXT = {
  object_store: '对象存储中的密封对象',
  search_index: '检索索引与向量',
  retrieval_cache: '检索/问答缓存',
  checkpoints: '运行 checkpoint 与摘要',
  sandbox_temp: '沙箱临时文件',
  third_party_history: '第三方历史副本',
  backup_catalog: '备份删除账本'
};

var SCOPE_ORDER = [
  'object_store',
  'search_index',
  'retrieval_cache',
  'checkpoints',
  'sandbox_temp',
  'third_party_history',
  'backup_catalog'
];

var DELETION_POLL_MAX_ATTEMPTS = 5;
var DELETION_POLL_INTERVAL_MS = 2000;

var RESOURCE_PATHS = {
  artifact: '/api/v1/artifacts/',
  memory: '/api/v1/memories/',
  knowledge_document: '/api/v1/knowledge-documents/',
  conversation: '/api/v1/conversations/'
};

var RESOURCE_TEXT = {
  artifact: '文件',
  memory: '记忆',
  knowledge_document: '知识文档',
  conversation: '会话'
};

var UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function resourcePath(resourceType, resourceId) {
  var prefix = RESOURCE_PATHS[resourceType];
  if (!prefix) {
    throw new Error('unknown deletion resource type');
  }
  return prefix + encodeURIComponent(String(resourceId));
}

function deletionPath(deletionId) {
  return '/api/v1/deletions/' + encodeURIComponent(String(deletionId));
}

function artifactDeletionPlan(rawId) {
  var artifactId = String(rawId == null ? '' : rawId).trim();
  if (!UUID_PATTERN.test(artifactId)) {
    throw new Error('artifact id 必须是 UUID');
  }
  return {
    artifactID: artifactId,
    path: resourcePath('artifact', artifactId)
  };
}

/** One deletion state; an unknown value is never guessed. */
function deletionStateText(state) {
  return DELETION_STATES[String(state || '')] || '未知删除状态';
}

function scopeText(scope) {
  return SCOPE_TEXT[String(scope || '')] || '未知清理范围';
}

/** The server's remaining scopes in the fixed vocabulary order. */
function remainingScopesText(scopes) {
  var items = Array.isArray(scopes) ? scopes.slice() : [];
  items.sort(function (left, right) {
    var leftIndex = SCOPE_ORDER.indexOf(left);
    var rightIndex = SCOPE_ORDER.indexOf(right);
    if (leftIndex < 0) { leftIndex = SCOPE_ORDER.length; }
    if (rightIndex < 0) { rightIndex = SCOPE_ORDER.length; }
    return leftIndex - rightIndex || String(left).localeCompare(String(right));
  });
  if (items.length === 0) {
    return '无（服务端报告所有清理范围已完成）';
  }
  return items.map(scopeText).join('、');
}

/** Project one contract DeletionJob for display. */
function describeDeletionJob(job) {
  if (!job || typeof job.id !== 'string' || typeof job.state !== 'string') {
    throw new Error('服务端没有返回有效的删除任务');
  }
  var scopes = Array.isArray(job.remaining_scopes) ? job.remaining_scopes : [];
  var stateText = deletionStateText(job.state);
  return {
    id: job.id,
    state: job.state,
    stateText: stateText,
    remaining: scopes,
    remainingText: remainingScopesText(scopes),
    isOpen: job.state === 'pending' || job.state === 'running',
    progressText: '删除任务 ' + job.id + '：' + stateText
      + '；剩余清理范围：' + remainingScopesText(scopes) + '。'
  };
}

/**
 * Poll only while the job is still open and the page has attempts left. T069
 * leaves a fresh job `pending` (purge is T071/T072), so an unbounded poll would
 * spin forever; the caller shows the last server answer instead.
 */
function shouldPollDeletion(job, attempts) {
  if (!job || typeof job.state !== 'string') {
    return false;
  }
  var used = Number(attempts || 0);
  if (!isFinite(used) || used >= DELETION_POLL_MAX_ATTEMPTS) {
    return false;
  }
  return job.state === 'pending' || job.state === 'running';
}

function deletionConfirmationText(resourceType, resourceID) {
  var label = RESOURCE_TEXT[resourceType] || '资源';
  return '删除这个' + label + '（' + resourceID + '）后，它会立即停止被读取、检索、缓存命中与恢复；'
    + '物理副本由后台清理任务完成。继续删除？';
}

function deletionResultText(job, resourceType) {
  var view = describeDeletionJob(job);
  var label = RESOURCE_TEXT[resourceType] || '资源';
  return '已接受删除' + label + '：删除任务 ' + view.id + '（' + view.stateText + '）。'
    + '旧内容已停止被读取；剩余清理范围：' + view.remainingText + '。';
}

// ---------------------------------------------------------------------------
// F11/T073: user-scoped diagnostic grants
//
// A grant names one run, one administrator grantee, the explicit object snapshot
// the user selected, bounded purposes and a hard expiry. The page never reads
// content: it creates/lists/revokes the consent record only. The server's content
// door is admin-only and audits every read.
// ---------------------------------------------------------------------------

var DIAGNOSTIC_GRANTS_PATH = '/api/v1/diagnostic-grants';
var DIAGNOSTIC_GRANT_STATES = {
  active: '有效',
  expired: '已过期',
  revoked: '已撤销'
};
var DIAGNOSTIC_REFUSAL_TEXT = {
  diagnostic_grant_revoked: '诊断授权已被撤销，正文不可再读。',
  diagnostic_grant_expired: '诊断授权已过期，正文不可再读。',
  diagnostic_run_deleted: '运行已删除；诊断授权不能越过删除记录读取正文。',
  diagnostic_purpose_denied: '该用途不在诊断授权范围内。',
  diagnostic_scope_denied: '该对象不在诊断授权范围内。',
  diagnostic_content_unavailable: '正文已不可用（已删除或已清理）。',
  diagnostic_content_not_ready: '正文尚未就绪。',
  personal_scope_forbidden: '服务账号不能管理诊断授权。'
};
var DIAGNOSTIC_PURPOSE_PATTERN = /^[a-z][a-z0-9_-]{0,63}$/;

function diagnosticGrantPath(grantId) {
  return DIAGNOSTIC_GRANTS_PATH + '/' + encodeURIComponent(String(grantId));
}

function diagnosticObjectEntry(artifactId) {
  var id = String(artifactId == null ? '' : artifactId).trim();
  if (!UUID_PATTERN.test(id)) {
    throw new Error('artifact id 必须是 UUID');
  }
  return 'artifact:' + id.toLowerCase();
}

function diagnosticObjectScopeHeader(entries) {
  var items = Array.isArray(entries) ? entries : [];
  return items.map(diagnosticObjectEntry).join(',');
}

/** The purpose tokens, lower-case and deduplicated; an invalid token is refused. */
function diagnosticPurposes(raw) {
  var values = Array.isArray(raw)
    ? raw
    : String(raw == null ? '' : raw).split(',');
  var seen = {};
  var purposes = [];
  values.forEach(function (value) {
    var purpose = String(value == null ? '' : value).trim();
    if (!purpose) {
      return;
    }
    if (!DIAGNOSTIC_PURPOSE_PATTERN.test(purpose)) {
      throw new Error('用途必须匹配 [a-z][a-z0-9_-]{0,63}：' + purpose);
    }
    if (!seen[purpose]) {
      seen[purpose] = true;
      purposes.push(purpose);
    }
  });
  if (purposes.length === 0) {
    throw new Error('至少需要一个用途');
  }
  return purposes;
}

/** A datetime-local value as the RFC3339 UTC instant the API expects. */
function diagnosticExpiryISO(localValue) {
  var raw = String(localValue == null ? '' : localValue).trim();
  if (!raw) {
    throw new Error('请选择到期时间');
  }
  var moment = new Date(raw);
  if (isNaN(moment.getTime())) {
    throw new Error('到期时间格式无效');
  }
  return moment.toISOString();
}

function diagnosticGrantStateText(state) {
  return DIAGNOSTIC_GRANT_STATES[String(state || '')] || '未知状态';
}

function diagnosticReadRefusalText(code) {
  return DIAGNOSTIC_REFUSAL_TEXT[String(code || '')] || '诊断读取被拒绝。';
}

/** The POST /api/v1/diagnostic-grants request the form produces. */
function diagnosticGrantCreateRequest(input) {
  var runID = String(input && input.runID ? input.runID : '').trim();
  var granteeID = String(input && input.granteeID ? input.granteeID : '').trim();
  if (!UUID_PATTERN.test(runID)) {
    throw new Error('运行 id 必须是 UUID');
  }
  if (!UUID_PATTERN.test(granteeID)) {
    throw new Error('管理员 principal id 必须是 UUID');
  }
  var purposes = diagnosticPurposes(input && input.purposes);
  var expiresAt = diagnosticExpiryISO(input && input.expiresAt);
  var objects = Array.isArray(input && input.objects) ? input.objects : [];
  var headers = {};
  if (objects.length > 0) {
    headers['X-Diagnostic-Object-Scope'] = diagnosticObjectScopeHeader(objects);
  }
  return {
    path: DIAGNOSTIC_GRANTS_PATH,
    method: 'POST',
    headers: headers,
    body: {
      run_id: runID.toLowerCase(),
      grantee_id: granteeID.toLowerCase(),
      purposes: purposes,
      expires_at: expiresAt
    }
  };
}

function diagnosticGrantRevokeRequest(grantId) {
  var id = String(grantId == null ? '' : grantId).trim();
  if (!UUID_PATTERN.test(id)) {
    throw new Error('授权 id 必须是 UUID');
  }
  return { path: diagnosticGrantPath(id), method: 'DELETE' };
}

/** Project one metadata grant for display. Never a content field. */
function describeDiagnosticGrant(grant) {
  if (!grant || typeof grant.id !== 'string' || typeof grant.state !== 'string') {
    throw new Error('服务端没有返回有效的诊断授权');
  }
  var purposes = Array.isArray(grant.purposes) ? grant.purposes.slice() : [];
  var objects = Array.isArray(grant.object_scope) ? grant.object_scope.slice() : [];
  return {
    id: grant.id,
    state: grant.state,
    stateText: diagnosticGrantStateText(grant.state),
    runID: typeof grant.run_id === 'string' ? grant.run_id : '',
    purposeText: purposes.length ? purposes.join('、') : '无用途',
    objectCount: objects.length,
    objectText: objects.length ? objects.join('、') : '未选择对象',
    expiresAt: typeof grant.expires_at === 'string' ? grant.expires_at : '',
    revokedAt: typeof grant.revoked_at === 'string' ? grant.revoked_at : '',
    isActive: grant.state === 'active'
  };
}

function diagnosticGrantSummaryText(grant) {
  var view = describeDiagnosticGrant(grant);
  return '诊断授权 ' + view.id + '：' + view.stateText
    + '；运行 ' + view.runID
    + '；用途 ' + view.purposeText
    + '；对象 ' + String(view.objectCount) + ' 个'
    + '；到期 ' + view.expiresAt + '。';
}

// ---------------------------------------------------------------------------
// DOM wiring (never executed by the Node helper test; the pure functions are)
// ---------------------------------------------------------------------------
function setText(id, value) {
  var element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function setStatus(id, message, kind) {
  var element = document.getElementById(id);
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status' + (kind ? ' ' + kind : '');
}

function addFieldRow(container, label, value) {
  var row = document.createElement('div');
  row.className = 'row';
  var name = document.createElement('span');
  name.className = 'label';
  name.textContent = label;
  var content = document.createElement('span');
  content.className = 'value';
  content.textContent = value;
  row.appendChild(name);
  row.appendChild(content);
  container.appendChild(row);
}

function renderDeletion(job) {
  var panel = document.getElementById('deletion-status');
  var card = document.getElementById('deletion-card');
  if (!panel || !card) {
    return;
  }
  card.hidden = false;
  panel.textContent = '';
  var view = describeDeletionJob(job);
  addFieldRow(panel, '删除任务', view.id);
  addFieldRow(panel, '状态', view.stateText);
  addFieldRow(panel, '剩余清理范围', view.remainingText);
  addFieldRow(panel, '说明',
    '删除已在响应前生效：旧内容不再被读取。物理副本由后续清理任务处理。');
}

var deletionPollTimer = null;
var deletionJobID = null;
var deletionAttempts = 0;

async function refreshDeletion() {
  if (!deletionJobID) {
    return;
  }
  try {
    var job = await api(deletionPath(deletionJobID));
    renderDeletion(job);
    deletionAttempts += 1;
    if (shouldPollDeletion(job, deletionAttempts)) {
      deletionPollTimer = setTimeout(refreshDeletion, DELETION_POLL_INTERVAL_MS);
    } else {
      setStatus('deletion-refresh-status',
        '已停止自动刷新：服务端报告的剩余清理范围如上。', '');
    }
  } catch (error) {
    setStatus('deletion-refresh-status', error.message || String(error), 'bad');
  }
}

function watchDeletion(job, resourceType) {
  deletionJobID = job.id;
  deletionAttempts = 0;
  if (deletionPollTimer) {
    clearTimeout(deletionPollTimer);
    deletionPollTimer = null;
  }
  renderDeletion(job);
  setStatus('action-status', deletionResultText(job, resourceType), 'ok');
  deletionPollTimer = setTimeout(refreshDeletion, DELETION_POLL_INTERVAL_MS);
}

async function deleteItem(resourceType, resourceID, afterDelete) {
  if (!window.confirm(deletionConfirmationText(resourceType, resourceID))) {
    return null;
  }
  try {
    var token = await csrf();
    var job = await api(resourcePath(resourceType, resourceID), {
      method: 'DELETE',
      csrf: token
    });
    watchDeletion(job, resourceType);
    if (afterDelete) {
      afterDelete();
    }
    return job;
  } catch (error) {
    var message = error && error.code === 'resource_in_use'
      ? '该文件仍被应用配置或可恢复的运行引用，服务端拒绝删除（resource_in_use）。'
      : (error.message || String(error));
    setStatus('action-status', message, 'bad');
    return null;
  }
}

function renderItemList(listId, emptyId, items, renderItem) {
  var list = document.getElementById(listId);
  var empty = document.getElementById(emptyId);
  if (!list) {
    return;
  }
  list.textContent = '';
  if (!items.length) {
    if (empty) {
      empty.hidden = false;
    }
    return;
  }
  if (empty) {
    empty.hidden = true;
  }
  items.forEach(function (item) {
    var entry = document.createElement('li');
    renderItem(item, entry);
    list.appendChild(entry);
  });
}

// The list contract is items + next_cursor (API.md "pagination"). The page used
// to read only the first page of each list, which silently hid memories,
// knowledge bases and documents beyond the server's default limit. This page is
// the "everything I may delete" inventory, so completeness is the point: follow
// the opaque cursor until the server says there is no next page.
async function collectAllPages(path) {
  var items = [];
  var cursor = null;
  for (var page = 0; page < 100; page += 1) {
    var query = '';
    if (cursor) {
      query = (path.indexOf('?') === -1 ? '?' : '&') + 'cursor=' + encodeURIComponent(cursor);
    }
    var payload = await api(path + query);
    items = items.concat(payload.items || []);
    cursor = payload.next_cursor || null;
    if (!cursor) {
      break;
    }
  }
  return items;
}

async function loadMemories() {
  setStatus('memories-status', '正在加载记忆…', '');
  try {
    var memories = await collectAllPages('/api/v1/memories');
    renderItemList('memories-list', 'memories-empty', memories, function (memory, entry) {
      addFieldRow(entry, '记忆 id', memory.id);
      addFieldRow(entry, '应用范围', memory.application_id);
      addFieldRow(entry, '版本', String(memory.revision));
      addFieldRow(entry, '正文', memory.text);
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = '删除这条记忆';
      button.addEventListener('click', function () {
        deleteItem('memory', memory.id, loadMemories);
      });
      entry.appendChild(button);
    });
    setStatus('memories-status', '已加载 ' + String(memories.length) + ' 条记忆。', 'ok');
  } catch (error) {
    setStatus('memories-status', error.message || String(error), 'bad');
  }
}

async function loadDocuments() {
  setStatus('documents-status', '正在加载知识文档…', '');
  try {
    var baseItems = await collectAllPages('/api/v1/knowledge-bases');
    var documents = [];
    for (var index = 0; index < baseItems.length; index += 1) {
      var base = baseItems[index];
      var baseDocuments = await collectAllPages('/api/v1/knowledge-bases/'
        + encodeURIComponent(base.id) + '/documents');
      baseDocuments.forEach(function (document) {
        document.knowledge_base_name = base.name;
        documents.push(document);
      });
    }
    renderItemList('documents-list', 'documents-empty', documents, function (document, entry) {
      addFieldRow(entry, '文档 id', document.id);
      addFieldRow(entry, '知识库', document.knowledge_base_name || document.knowledge_base_id);
      addFieldRow(entry, '状态', document.state);
      addFieldRow(entry, '版本', String(document.revision));
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = '删除这个文档';
      button.addEventListener('click', function () {
        deleteItem('knowledge_document', document.id, loadDocuments);
      });
      entry.appendChild(button);
    });
    setStatus('documents-status', '已加载 ' + String(documents.length) + ' 个文档。', 'ok');
  } catch (error) {
    setStatus('documents-status', error.message || String(error), 'bad');
  }
}

// ---------------------------------------------------------------------------
// F11/T073 grant management wiring (browser only; the pure helpers above are
// what the Node page test executes)
// ---------------------------------------------------------------------------
var grantObjectEntries = [];

function selectedGrantObjects() {
  var list = document.getElementById('grant-objects-list');
  if (!list || !list.querySelectorAll) {
    return [];
  }
  var selected = [];
  var inputs = list.querySelectorAll('input[type="checkbox"]');
  for (var index = 0; index < inputs.length; index += 1) {
    if (inputs[index].checked) {
      selected.push(inputs[index].value);
    }
  }
  return selected;
}

function renderGrantObjects(items) {
  var list = document.getElementById('grant-objects-list');
  if (!list) {
    return;
  }
  list.textContent = '';
  grantObjectEntries = [];
  items.forEach(function (item) {
    var entry = diagnosticObjectEntry(item.id);
    grantObjectEntries.push(entry);
    var row = document.createElement('li');
    var box = document.createElement('input');
    box.type = 'checkbox';
    box.value = entry;
    box.checked = true;
    var label = document.createElement('label');
    label.textContent = ' ' + entry + '（' + String(item.state || '未知状态') + '）';
    row.appendChild(box);
    row.appendChild(label);
    list.appendChild(row);
  });
}

async function loadGrantObjects() {
  var input = document.getElementById('grant-run-id');
  var raw = input ? input.value : '';
  try {
    var runID = String(raw == null ? '' : raw).trim();
    if (!UUID_PATTERN.test(runID)) {
      throw new Error('运行 id 必须是 UUID');
    }
    setStatus('grant-objects-status', '正在读取运行制品…', '');
    var payload = await api('/api/v1/runs/' + encodeURIComponent(runID) + '/artifacts?limit=100');
    var items = payload.items || [];
    renderGrantObjects(items);
    if (items.length === 0) {
      setStatus('grant-objects-status', '这个运行没有可授权的制品。', '');
    } else {
      setStatus('grant-objects-status', '已列出 ' + String(items.length) + ' 个对象，可取消勾选。', 'ok');
    }
  } catch (error) {
    setStatus('grant-objects-status', error.message || String(error), 'bad');
  }
}

function renderDiagnosticGrants(items) {
  renderItemList('grants-list', 'grants-empty', items, function (grant, entry) {
    var view = describeDiagnosticGrant(grant);
    addFieldRow(entry, '授权 id', view.id);
    addFieldRow(entry, '状态', view.stateText);
    addFieldRow(entry, '运行', view.runID);
    addFieldRow(entry, '用途', view.purposeText);
    addFieldRow(entry, '对象', view.objectText);
    addFieldRow(entry, '到期', view.expiresAt);
    if (view.revokedAt) {
      addFieldRow(entry, '撤销于', view.revokedAt);
    }
    if (view.isActive) {
      var button = document.createElement('button');
      button.type = 'button';
      button.textContent = '撤销这条授权';
      button.addEventListener('click', function () {
        revokeDiagnosticGrant(view.id);
      });
      entry.appendChild(button);
    }
  });
}

async function loadDiagnosticGrants() {
  try {
    var payload = await api(DIAGNOSTIC_GRANTS_PATH);
    var items = payload.items || [];
    renderDiagnosticGrants(items);
    if (items.length === 0) {
      setStatus('grant-status', '还没有诊断授权。', '');
    }
  } catch (error) {
    setStatus('grant-status', error.message || String(error), 'bad');
  }
}

async function createDiagnosticGrant() {
  var runInput = document.getElementById('grant-run-id');
  var granteeInput = document.getElementById('grant-grantee-id');
  var purposesInput = document.getElementById('grant-purposes');
  var expiresInput = document.getElementById('grant-expires');
  try {
    var request = diagnosticGrantCreateRequest({
      runID: runInput ? runInput.value : '',
      granteeID: granteeInput ? granteeInput.value : '',
      purposes: purposesInput ? purposesInput.value : '',
      expiresAt: expiresInput ? expiresInput.value : '',
      objects: selectedGrantObjects()
    });
    var token = await csrf();
    var accepted = await api(request.path, {
      method: request.method,
      body: request.body,
      headers: request.headers,
      csrf: token
    });
    setStatus('grant-status', '已创建诊断授权 ' + accepted.id + '（' + accepted.state + '）。', 'ok');
    await loadDiagnosticGrants();
  } catch (error) {
    setStatus('grant-status', error.message || String(error), 'bad');
  }
}

async function revokeDiagnosticGrant(grantId) {
  try {
    var request = diagnosticGrantRevokeRequest(grantId);
    var token = await csrf();
    await api(request.path, { method: request.method, csrf: token });
    setStatus('grant-status', '已撤销诊断授权 ' + grantId + '；后续读取会立即被拒绝。', 'ok');
    await loadDiagnosticGrants();
  } catch (error) {
    setStatus('grant-status', error.message || String(error), 'bad');
  }
}

document.addEventListener('DOMContentLoaded', function () {
  var button = document.getElementById('delete-artifact');
  if (button) {
    button.addEventListener('click', function () {
      var input = document.getElementById('artifact-id');
      try {
        var plan = artifactDeletionPlan(input ? input.value : '');
        deleteItem('artifact', plan.artifactID, null);
      } catch (error) {
        setStatus('action-status', error.message, 'bad');
      }
    });
  }
  var refresh = document.getElementById('deletion-refresh');
  if (refresh) {
    refresh.addEventListener('click', function () {
      deletionAttempts = 0;
      refreshDeletion();
    });
  }
  var loadObjects = document.getElementById('grant-load-objects');
  if (loadObjects) {
    loadObjects.addEventListener('click', loadGrantObjects);
  }
  var createGrant = document.getElementById('create-grant');
  if (createGrant) {
    createGrant.addEventListener('click', createDiagnosticGrant);
  }
  requireLogin().then(function () {
    loadMemories();
    loadDocuments();
    loadDiagnosticGrants();
  }).catch(function (error) {
    setStatus('action-status', error.message || String(error), 'bad');
  });
});
