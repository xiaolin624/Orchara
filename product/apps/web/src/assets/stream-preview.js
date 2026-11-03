/* F06/T044: the temporary owner model-stream preview.
 *
 * This file is loaded by the debug and runs pages, and its pure helpers are
 * executed directly by the Node page tests. The preview is temporary: it renders
 * streamed text/tool-call fragments for the *current* generation only, discards
 * the whole draft when a new generation id arrives, and closes on final/error.
 * Tool-call fragments are shown as pending and are never executed here: only the
 * validated final (built server-side) is authoritative, and the persistent Run
 * snapshot/events remain the authority for the outcome.
 */
'use strict';

var PREVIEW_TERMINAL_TYPES = ['final', 'error'];

function previewPath(runId) {
  return '/api/v1/runs/' + encodeURIComponent(String(runId || '')) + '/model-preview';
}

function emptyPreviewState() {
  return {
    generationID: null,
    operationID: null,
    text: '',
    pendingCalls: [],
    final: null,
    finalToolCalls: [],
    error: null,
    receiptArtifactID: null,
    status: 'idle',
    terminal: false,
    cleared: false,
    generations: 0,
  };
}

function parsePreviewEvent(eventName, rawData) {
  if (typeof rawData !== 'string' || rawData === '') {
    return null;
  }
  var frame;
  try {
    frame = JSON.parse(rawData);
  } catch (error) {
    return null;
  }
  if (!frame || typeof frame !== 'object') {
    return null;
  }
  if (PREVIEW_TERMINAL_TYPES.indexOf(eventName) === -1 &&
      ['delta', 'tool_delta'].indexOf(eventName) === -1) {
    return null;
  }
  if (typeof frame.operation_id !== 'string' || frame.operation_id === '') {
    return null;
  }
  if (typeof frame.generation_id !== 'string' || frame.generation_id === '') {
    return null;
  }
  frame.type = eventName;
  return frame;
}

function applyPreviewFrame(state, frame) {
  var next = emptyPreviewState();
  for (var key in state) {
    if (Object.prototype.hasOwnProperty.call(state, key)) {
      next[key] = state[key];
    }
  }
  next.pendingCalls = (state.pendingCalls || []).slice();
  next.finalToolCalls = (state.finalToolCalls || []).slice();
  if (!frame || !frame.generation_id || !frame.operation_id) {
    return next;
  }
  if (frame.generation_id !== next.generationID) {
    // A new physical generation replaces the old draft wholesale. Old text and
    // old tool-call fragments are never appended to the new generation.
    var replaced = next.generationID !== null;
    next.generationID = frame.generation_id;
    next.operationID = frame.operation_id;
    next.text = '';
    next.pendingCalls = [];
    next.final = null;
    next.finalToolCalls = [];
    next.error = null;
    next.receiptArtifactID = null;
    next.terminal = false;
    next.status = 'streaming';
    next.cleared = replaced;
    next.generations = next.generations + 1;
  }
  if (frame.type === 'delta') {
    next.text += typeof frame.text === 'string' ? frame.text : '';
    next.status = 'streaming';
    return next;
  }
  if (frame.type === 'tool_delta') {
    var call = frame.tool_call || {};
    next.pendingCalls.push({
      index: call.index,
      id: call.id || '',
      name: call.name || '',
      argumentsFragment: call.arguments_fragment || '',
      pending: true,
      executed: false,
    });
    next.status = 'streaming';
    return next;
  }
  if (frame.type === 'error') {
    var error = frame.error || {};
    next.error = {
      code: typeof error.code === 'string' ? error.code : 'stream_error',
      message: typeof error.message === 'string' ? error.message : '',
    };
    if (frame.retryable) {
      // The next generation replaces this draft; do not close the preview yet.
      next.status = 'retrying';
      next.terminal = false;
    } else {
      next.status = 'error';
      next.terminal = true;
    }
    return next;
  }
  if (frame.type === 'final') {
    var data = frame.data && typeof frame.data === 'object' ? frame.data : {};
    next.final = frame;
    // The final frame is the authority for the complete content; the preview is
    // replaced by it rather than trusted as a second source.
    if (typeof data.text === 'string') {
      next.text = data.text;
    }
    next.finalToolCalls = Array.isArray(data.tool_calls) ? data.tool_calls : [];
    next.pendingCalls = [];
    next.error = null;
    next.receiptArtifactID = frame.receipt_artifact_id || null;
    next.status = 'final';
    next.terminal = true;
    return next;
  }
  return next;
}

function previewStatusText(status) {
  var labels = {
    idle: '未连接',
    streaming: '临时预览（未提交）',
    retrying: '流已中断，等待平台开启新代次',
    final: '已收到完整校验结果（临时预览结束）',
    error: '流式调用失败（临时预览结束）',
  };
  return labels[status] || '未知状态';
}

function describePreview(state) {
  var pending = state.pendingCalls || [];
  var pendingText = pending.length === 0
    ? '无待定工具调用'
    : pending.map(function (call) {
        return (call.id || '?') + '(' + (call.name || '?') + ') 待定，未执行';
      }).join('；');
  return {
    generationID: state.generationID,
    operationID: state.operationID,
    text: state.text || '',
    pendingCount: pending.length,
    pendingText: pendingText,
    status: state.status,
    statusText: previewStatusText(state.status),
    terminal: state.terminal === true,
    cleared: state.cleared === true,
    generations: state.generations || 0,
    finalToolCallCount: (state.finalToolCalls || []).length,
    receiptArtifactID: state.receiptArtifactID,
    errorText: state.error ? (state.error.code + ': ' + state.error.message) : '',
  };
}

function renderPreviewPanel(panel, state) {
  if (!panel) {
    return;
  }
  var described = describePreview(state);
  panel.setAttribute('data-preview', described.status);
  var children = [];
  var title = document.createElement('strong');
  title.textContent = '临时模型流预览（非契约，不持久）';
  children.push(title);
  var status = document.createElement('span');
  status.textContent = ' · ' + described.statusText;
  children.push(status);
  if (described.generationID) {
    var generation = document.createElement('p');
    generation.textContent = 'generation: ' + described.generationID +
      (described.operationID ? ' · operation: ' + described.operationID : '') +
      (described.generations > 1 ? ' · 已替换 ' + (described.generations - 1) + ' 个旧代次' : '');
    children.push(generation);
  }
  var text = document.createElement('pre');
  text.className = 'preview-text';
  text.textContent = described.text;
  children.push(text);
  var pending = document.createElement('p');
  pending.textContent = described.pendingText;
  children.push(pending);
  if (described.errorText) {
    var error = document.createElement('p');
    error.textContent = '错误 ' + described.errorText;
    children.push(error);
  }
  if (described.terminal) {
    var authority = document.createElement('p');
    authority.textContent = described.status === 'final'
      ? '完整内容与 receipt 以持久 Run 快照/事件为准；本预览即将关闭。'
      : '失败以持久 Run 快照为准；本预览即将关闭。';
    children.push(authority);
  }
  panel.replaceChildren.apply(panel, children);
}

function beginModelPreview(runId, options) {
  var settings = options || {};
  var panelID = settings.panelID || 'run-preview';
  var panel = typeof document !== 'undefined' && document.getElementById
    ? document.getElementById(panelID)
    : null;
  if (!panel || typeof EventSource === 'undefined') {
    return null;
  }
  var state = emptyPreviewState();
  renderPreviewPanel(panel, state);
  var source = new EventSource(previewPath(runId));

  function handle(eventName, event) {
    var frame = parsePreviewEvent(eventName, event && event.data);
    if (!frame) {
      return;
    }
    state = applyPreviewFrame(state, frame);
    renderPreviewPanel(panel, state);
    if (state.terminal) {
      source.close();
    }
  }
  ['delta', 'tool_delta', 'final'].forEach(function (eventName) {
    source.addEventListener(eventName, function (event) {
      handle(eventName, event);
    });
  });
  // A server-sent "error" event is a normal MessageEvent with data; a transport
  // error has no data. Only the former ends the preview.
  source.addEventListener('error', function (event) {
    if (event && typeof event.data === 'string' && event.data !== '') {
      handle('error', event);
      return;
    }
    state.status = 'idle';
    renderPreviewPanel(panel, state);
  });
  return source;
}
