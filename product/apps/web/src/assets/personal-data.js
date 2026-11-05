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

async function loadMemories() {
  setStatus('memories-status', '正在加载记忆…', '');
  try {
    var payload = await api('/api/v1/memories');
    renderItemList('memories-list', 'memories-empty', payload.items || [], function (memory, entry) {
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
    setStatus('memories-status', '已加载 ' + String((payload.items || []).length) + ' 条记忆。', 'ok');
  } catch (error) {
    setStatus('memories-status', error.message || String(error), 'bad');
  }
}

async function loadDocuments() {
  setStatus('documents-status', '正在加载知识文档…', '');
  try {
    var bases = await api('/api/v1/knowledge-bases');
    var documents = [];
    var baseItems = bases.items || [];
    for (var index = 0; index < baseItems.length; index += 1) {
      var base = baseItems[index];
      var payload = await api('/api/v1/knowledge-bases/'
        + encodeURIComponent(base.id) + '/documents');
      (payload.items || []).forEach(function (document) {
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
  requireLogin().then(function () {
    loadMemories();
    loadDocuments();
  }).catch(function (error) {
    setStatus('action-status', error.message || String(error), 'bad');
  });
});
