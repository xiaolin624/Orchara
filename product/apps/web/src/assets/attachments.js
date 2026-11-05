/*
 * F05/T028 attachment page.
 *
 * The page is a real client of the upload contract:
 *
 *   POST /api/v1/artifacts/uploads                    -> 201 Upload
 *   PUT  <signed staging URL>                         -> 204 (no session)
 *   POST /api/v1/artifacts/{id}/complete              -> 202 Artifact
 *   GET  /api/v1/artifacts/{id}                       -> 200 Artifact
 *   GET  /api/v1/artifacts/{id}/download              -> 200 Download
 *
 * It computes SHA-256 in the browser, uploads to the one signed staging URL,
 * completes with the hash, then polls the four-field Artifact while the
 * background finalize handler inspects the real bytes. The sealed bytes are
 * only fetched through a fresh short-lived link when the user clicks download.
 *
 * Safety rules encoded here:
 *   - the signed URL is used verbatim and never composed from user input;
 *   - the PUT carries no cookie, CSRF token or Authorization header;
 *   - no credential and no signed URL is written into the address bar, browser
 *     storage or the DOM (the download link is assigned only at click time);
 *   - the quarantine reason and every artifact field are rendered with
 *     textContent, never parsed as markup.
 *
 * The pure helpers are at the top so the Node test executes them with stubbed
 * fetch; the DOM wiring is at the bottom.
 */

const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const MEDIA_TYPES = Object.freeze({
  'text/plain': 'text',
  'text/csv': 'csv',
  'application/json': 'json',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'xlsx',
});
const UPLOAD_PURPOSES = Object.freeze([
  'task_input',
  'tool_source',
  'knowledge',
  'evaluation_dataset',
]);

const QUARANTINE_MESSAGES = Object.freeze({
  artifact_hash_mismatch: '内容哈希与声明的 SHA-256 不一致',
  artifact_size_mismatch: '实际大小与声明的大小不一致',
  artifact_too_large: '内容超过单个对象 20 MiB 上限',
  artifact_upload_incomplete: '暂存对象不存在：上传字节没有到达服务器',
  artifact_format_rejected: '内容格式未通过准入',
  unsupported_media_type: '声明的媒体类型不在首版支持范围',
  content_type_mismatch: '真实内容与声明的媒体类型不一致',
  empty_content: '文件为空',
  invalid_text_encoding: '文本内容不是合法的 UTF-8',
  invalid_json: '声明为 JSON，但内容不是合法 JSON',
  invalid_csv: '声明为 CSV，但内容不是合法 CSV',
  xlsx_macro_present: 'XLSX 含宏，首版拒绝',
  xlsx_entry_limit_exceeded: 'XLSX 条目数超过准入上限',
  xlsx_uncompressed_limit_exceeded: 'XLSX 解压总量超过准入上限',
  xlsx_path_traversal: 'XLSX 含路径穿越条目',
  xlsx_missing_workbook: 'XLSX 缺少工作簿/内容类型元数据',
  xlsx_invalid_archive: 'XLSX 压缩包损坏',
});

function artifactPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID));
}

function artifactCompletePath(artifactID) {
  return artifactPath(artifactID) + '/complete';
}

function artifactDownloadPath(artifactID) {
  return artifactPath(artifactID) + '/download';
}

function artifactFailure(code, message, status) {
  const failure = new Error(message);
  failure.code = code;
  failure.status = status || 0;
  return failure;
}

/** Validate the browser-side half of UploadInput before touching the API. */
function uploadInputForFile(file, mediaType, purpose) {
  if (!file || typeof file.size !== 'number') {
    throw artifactFailure('file', '请选择一个文件');
  }
  if (!Number.isInteger(file.size) || file.size < 1) {
    throw artifactFailure('size_bytes', '文件必须至少 1 字节');
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    throw artifactFailure('size_bytes', '文件超过 20 MiB 上限');
  }
  if (!Object.prototype.hasOwnProperty.call(MEDIA_TYPES, mediaType)) {
    throw artifactFailure('media_type', '请选择受支持的媒体类型');
  }
  if (!UPLOAD_PURPOSES.includes(purpose)) {
    throw artifactFailure('purpose', '请选择受支持的用途');
  }
  return { media_type: mediaType, size_bytes: file.size, purpose };
}

/** Lowercase hex SHA-256 of an ArrayBuffer/Uint8Array, without base64. */
async function sha256Hex(buffer) {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, '0'))
    .join('');
}

/** Reserve one upload. The CSRF token is required for a cookie session. */
async function createUploadRequest(payload, csrfToken) {
  return api('/api/v1/artifacts/uploads', {
    method: 'POST',
    body: payload,
    csrf: csrfToken,
  });
}

/**
 * PUT the bytes to the signed staging URL.
 *
 * This is the one request that must not carry any ambient credential: the HMAC
 * token is the whole authority. It also never sends the CSRF header, because
 * the object-link route has no session and a CSRF token in that request would
 * be a credential leaked to a URL-scoped operation.
 */
async function putUploadBytes(uploadURL, bytes) {
  if (!uploadURL) {
    throw artifactFailure('upload_url', '服务器没有返回上传链接');
  }
  const response = await fetch(uploadURL, {
    method: 'PUT',
    body: bytes,
    credentials: 'omit',
    cache: 'no-store',
    redirect: 'error',
    headers: { 'Content-Type': 'application/octet-stream' },
  });
  if (response.status !== 204) {
    let detail = null;
    const text = await response.text();
    if (text) {
      try {
        detail = JSON.parse(text);
      } catch (parseError) {
        detail = null;
      }
    }
    const error = (detail && detail.error) || {};
    throw artifactFailure(
      error.code || 'upload_failed',
      error.message || '上传失败（HTTP ' + response.status + '）',
      response.status,
    );
  }
  return true;
}

async function completeUploadRequest(artifactID, sha256, csrfToken) {
  return api(artifactCompletePath(artifactID), {
    method: 'POST',
    body: { sha256: sha256 },
    csrf: csrfToken,
  });
}

/** Poll one artifact and keep the safe quarantine reason header. */
async function fetchArtifact(artifactID) {
  const result = await apiFull(artifactPath(artifactID));
  return {
    artifact: result.payload,
    quarantineReason: result.headers.get('X-Artifact-Quarantine-Reason'),
  };
}

async function fetchDownloadLink(artifactID) {
  return api(artifactDownloadPath(artifactID));
}

function artifactIsTerminal(state) {
  return state === 'ready' || state === 'quarantined';
}

function shouldPollArtifact(state, attempts, maxAttempts) {
  if (!state || artifactIsTerminal(state)) {
    return false;
  }
  return attempts < maxAttempts;
}

/** Fixed Chinese text for a known reason code; unknown codes say so. */
function quarantineMessage(reason) {
  if (!reason) {
    return '内容未通过准入，已被隔离';
  }
  return QUARANTINE_MESSAGES[reason] || '内容未通过准入（未知原因代码）';
}

function artifactSummary(artifact) {
  return {
    id: artifact.id,
    state: artifact.state,
    mediaType: artifact.media_type,
    sizeBytes: artifact.size_bytes,
  };
}

// ---------------------------------------------------------------------------
// F05/T030 preset sales tool
//
// The page is a real client of the non-contract bridge:
//
//   POST /api/v1/tool-runs          -> 202 {id, state, tool, artifact_id}
//   GET  /api/v1/tool-runs/{job_id} -> 200 {id, state, error_code, artifacts}
//   GET  /api/v1/artifacts/{id}/content -> summary.json / summary.csv bytes
//
// Money stays a Decimal string end to end: the helpers below never convert an
// amount to binary floating point, and the table is rendered from the verified
// summary.json artifact with textContent only.
// ---------------------------------------------------------------------------
const TOOL_NAME = 'sales_stats';
const TOOL_SUMMARY_PURPOSE = 'tool_summary';
const TOOL_ARTIFACT_PURPOSE = 'tool_artifact';
const TOOL_DIAGNOSTICS_PURPOSE = 'diagnostics';
const TOOL_LOG_PURPOSE = 'log';
const MONEY_PATTERN = /^(0|[1-9][0-9]*)\.[0-9]{2}$/;

function toolRunPath() {
  return '/api/v1/tool-runs';
}

function toolRunStatusPath(jobID) {
  return toolRunPath() + '/' + encodeURIComponent(String(jobID));
}

function toolArtifactContentPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID)) + '/content';
}

/** Validate the exact POST body before any request is sent. */
function toolRunBody(tool, artifactID) {
  if (tool !== TOOL_NAME) {
    throw artifactFailure('tool', '只支持预置工具 sales_stats');
  }
  const normalized = String(artifactID === undefined || artifactID === null ? '' : artifactID).trim();
  if (!normalized) {
    throw artifactFailure('artifact_id', '请填写要统计的附件 artifact id');
  }
  return { tool: TOOL_NAME, artifact_id: normalized };
}

function toolRunIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'tool-run-' + Date.now() + '-' + Math.random().toString(16).slice(2);
}

/** Create one run; the idempotency key is required by the bridge endpoint. */
async function createToolRunRequest(tool, artifactID, csrfToken, idempotencyKey) {
  const key = idempotencyKey || toolRunIdempotencyKey();
  return api(toolRunPath(), {
    method: 'POST',
    body: toolRunBody(tool, artifactID),
    csrf: csrfToken,
    headers: { 'Idempotency-Key': key },
  });
}

async function fetchToolRun(jobID) {
  return api(toolRunStatusPath(jobID));
}

/** Fetch a summary/diagnostics JSON artifact through the owner-only content endpoint. */
async function fetchToolArtifactJSON(artifactID) {
  const response = await fetch(toolArtifactContentPath(artifactID), {
    method: 'GET',
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
    headers: { Accept: 'application/json' },
  });
  const text = await response.text();
  if (!response.ok) {
    let detail = null;
    if (text) {
      try {
        detail = JSON.parse(text);
      } catch (parseError) {
        detail = null;
      }
    }
    const error = (detail && detail.error) || {};
    throw artifactFailure(
      error.code || 'artifact_content_failed',
      error.message || '无法读取结果附件',
      response.status,
    );
  }
  try {
    return JSON.parse(text);
  } catch (parseError) {
    throw artifactFailure('invalid_json', '结果附件不是合法 JSON');
  }
}

/** Map the background-job state (contract vocabulary) to page language. */
function toolRunStatusLabel(state) {
  switch (state) {
    case 'ready':
      return '排队中';
    case 'leased':
      return '运行中';
    case 'succeeded':
      return '成功';
    case 'failed':
      return '失败';
    case 'cancelled':
      return '已取消';
    default:
      return '未知状态';
  }
}

function toolRunIsActive(state) {
  return state === 'ready' || state === 'leased';
}

function toolRunIsTerminal(state) {
  return state === 'succeeded' || state === 'failed' || state === 'cancelled';
}

function shouldPollToolRun(state, attempts, maxAttempts) {
  if (!state || toolRunIsTerminal(state)) {
    return false;
  }
  return attempts < maxAttempts;
}

function toolRunArtifact(run, purpose) {
  if (!run || !Array.isArray(run.artifacts)) {
    return null;
  }
  return run.artifacts.find((artifact) => artifact.purpose === purpose) || null;
}

/**
 * F15A/T057: the tool_run failure path publishes its diagnostics *best effort*
 * and reports the failure immediately, so the artifact can still be `scanning`
 * when the run is already terminal. Wait (bounded) for the real artifact state
 * instead of reading the content at that instant and showing a 409 as the
 * "explanation". `artifactIsTerminal` covers the quarantined/deleted verdicts,
 * which no amount of waiting can turn into readable content.
 */
function shouldPollToolArtifact(artifact, attempts, maxAttempts) {
  const state = artifact && artifact.state ? String(artifact.state) : '';
  if (!state || state === 'ready' || artifactIsTerminal(state)) {
    return false;
  }
  return attempts < maxAttempts;
}

function toolDownloadLinks(run) {
  const links = [];
  const csv = toolRunArtifact(run, TOOL_ARTIFACT_PURPOSE);
  if (csv) {
    links.push({ label: '下载 summary.csv', url: toolArtifactContentPath(csv.id) });
  }
  const summary = toolRunArtifact(run, TOOL_SUMMARY_PURPOSE);
  if (summary) {
    links.push({ label: '下载 summary.json', url: toolArtifactContentPath(summary.id) });
  }
  return links;
}

function assertSummaryDocument(summary) {
  if (!summary || typeof summary !== 'object' || Array.isArray(summary)) {
    throw artifactFailure('summary_invalid', 'summary.json 不是对象');
  }
  if (typeof summary.total !== 'string' || !MONEY_PATTERN.test(summary.total)) {
    throw artifactFailure('summary_invalid', 'summary.json 的 total 不是两位小数金额字符串');
  }
  if (!summary.regions || typeof summary.regions !== 'object' || Array.isArray(summary.regions)) {
    throw artifactFailure('summary_invalid', 'summary.json 缺少 regions 对象');
  }
  if (!Number.isInteger(summary.order_count) || summary.order_count < 1) {
    throw artifactFailure('summary_invalid', 'summary.json 的 order_count 必须是正整数');
  }
  return summary;
}

/** Region rows for the Decimal table; amounts remain strings. */
function summaryRows(summary) {
  const document = assertSummaryDocument(summary);
  return Object.keys(document.regions).map((region) => {
    const total = document.regions[region];
    if (typeof total !== 'string' || !MONEY_PATTERN.test(total)) {
      throw artifactFailure('summary_invalid', 'summary.json 的地区金额不是两位小数金额字符串');
    }
    return { region: region, total: total };
  });
}

function summaryTotals(summary) {
  const document = assertSummaryDocument(summary);
  return { total: document.total, orderCount: String(document.order_count) };
}

/** Safe, plain-text fields from the diagnostics artifact (never HTML). */
function diagnosticsFields(document) {
  if (!document || typeof document !== 'object') {
    return null;
  }
  const location = document.location || {};
  const parts = [];
  if (Number.isInteger(location.row)) {
    parts.push('第 ' + location.row + ' 行');
  }
  if (typeof location.column === 'string' && location.column) {
    parts.push('列 ' + location.column);
  }
  if (typeof location.cell === 'string' && location.cell) {
    parts.push('单元格 ' + location.cell);
  }
  return {
    code: String(document.error_code || 'unknown'),
    message: String(document.message || '工具拒绝了该输入'),
    fix: String(document.fix || '请修正输入后重试'),
    location: parts.join('，'),
    retryable: document.retryable === true,
  };
}

function initTools() {
  const panel = document.getElementById('tool-panel');
  if (!panel) {
    return;
  }
  const status = document.getElementById('tool-run-status');
  const downloads = document.getElementById('tool-downloads');
  const logNote = document.getElementById('tool-log-note');
  let pollTimer = null;
  let currentJobID = null;

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = 'status ' + (kind || '');
  }

  function renderRun(run) {
    currentJobID = run.id;
    document.getElementById('tool-run-id').textContent = run.id;
    document.getElementById('tool-run-state').textContent = toolRunStatusLabel(run.state);
    document.getElementById('tool-run-error-code').textContent = run.error_code || '—';
    document.getElementById('tool-summary').hidden = true;
    document.getElementById('tool-diagnostics').hidden = true;
    downloads.replaceChildren();
    logNote.textContent = '';

    const logArtifact = toolRunArtifact(run, TOOL_LOG_PURPOSE);
    if (logArtifact) {
      logNote.textContent = '诊断日志只作为受控附件保存，页面不展示其正文。';
    }
    for (const link of toolDownloadLinks(run)) {
      const item = document.createElement('li');
      const anchor = document.createElement('a');
      anchor.href = link.url;
      anchor.textContent = link.label;
      anchor.setAttribute('download', '');
      item.appendChild(anchor);
      downloads.appendChild(item);
    }
    return run;
  }

  async function loadSummary(run) {
    const artifact = toolRunArtifact(run, TOOL_SUMMARY_PURPOSE);
    if (!artifact) {
      setStatus('运行成功但缺少 summary.json 附件，请查看诊断信息。', 'bad');
      return;
    }
    const document_ = await fetchToolArtifactJSON(artifact.id);
    const totals = summaryTotals(document_);
    const rows = summaryRows(document_);
    document.getElementById('tool-summary-total').textContent = totals.total;
    document.getElementById('tool-summary-count').textContent = totals.orderCount;
    const body = document.getElementById('tool-summary-region-rows');
    body.replaceChildren();
    for (const row of rows) {
      const tr = document.createElement('tr');
      const regionCell = document.createElement('td');
      regionCell.textContent = row.region;
      const totalCell = document.createElement('td');
      totalCell.textContent = row.total;
      tr.appendChild(regionCell);
      tr.appendChild(totalCell);
      body.appendChild(tr);
    }
    document.getElementById('tool-summary').hidden = false;
    setStatus('统计成功；结果金额是服务器校验过的 Decimal 字符串。', 'ok');
  }

  /**
   * Wait (bounded) for the diagnostics artifact's real state to become `ready`.
   * The failure is reported before the best-effort diagnostics upload has
   * necessarily been finalized, so reading the content immediately would show a
   * 409 artifact_not_ready instead of the rejection explanation.
   */
  async function waitForToolArtifactReady(artifact) {
    let current = artifact;
    let attempts = 0;
    while (current && current.state !== 'ready') {
      if (!shouldPollToolArtifact(current, attempts, 60)) {
        return null;
      }
      await new Promise((resolve) => setTimeout(resolve, 200));
      current = await fetchArtifact(current.id);
      attempts += 1;
    }
    return current;
  }

  async function loadDiagnostics(run) {
    const artifact = toolRunArtifact(run, TOOL_DIAGNOSTICS_PURPOSE);
    if (!artifact) {
      setStatus('运行失败 [' + (run.error_code || 'unknown') + ']，但没有诊断附件。', 'bad');
      return;
    }
    const ready = await waitForToolArtifactReady(artifact);
    if (!ready) {
      setStatus(
        '运行失败 [' + (run.error_code || 'unknown') + ']，诊断附件仍在封存或不可读，请稍后刷新。',
        'bad',
      );
      return;
    }
    const fields = diagnosticsFields(await fetchToolArtifactJSON(ready.id));
    if (!fields) {
      setStatus('诊断附件不是合法对象，已停止渲染。', 'bad');
      return;
    }
    document.getElementById('tool-diagnostics-code').textContent = fields.code;
    document.getElementById('tool-diagnostics-message').textContent = fields.message;
    document.getElementById('tool-diagnostics-fix').textContent = fields.fix;
    document.getElementById('tool-diagnostics-location').textContent = fields.location
      ? '位置：' + fields.location
      : fields.retryable
        ? '该错误可能暂时可重试。'
        : '';
    document.getElementById('tool-diagnostics').hidden = false;
    setStatus('运行失败 [' + fields.code + '] ' + fields.message, 'bad');
  }

  async function poll(jobID, attempts) {
    try {
      const run = renderRun(await fetchToolRun(jobID));
      if (run.state === 'succeeded') {
        await loadSummary(run);
        return;
      }
      if (run.state === 'failed') {
        await loadDiagnostics(run);
        return;
      }
      if (!shouldPollToolRun(run.state, attempts, 100)) {
        setStatus('轮询已停止，任务仍为 ' + toolRunStatusLabel(run.state) + '，可稍后刷新。', 'pending');
        return;
      }
      setStatus('销售统计' + toolRunStatusLabel(run.state) + '…', 'pending');
      pollTimer = setTimeout(() => poll(jobID, attempts + 1), 600);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus('查询销售统计失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  document.getElementById('tool-run-submit').addEventListener('click', async () => {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
    let body;
    try {
      body = toolRunBody(TOOL_NAME, document.getElementById('tool-artifact-id').value);
    } catch (error) {
      setStatus('无法运行 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    try {
      await requireLogin();
      const csrfToken = await csrf();
      setStatus('正在创建销售统计任务…', 'pending');
      const run = await createToolRunRequest(TOOL_NAME, body.artifact_id, csrfToken);
      renderRun(run);
      setStatus('任务已创建，等待后台执行…', 'pending');
      pollTimer = setTimeout(() => poll(run.id, 0), 400);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus('创建销售统计失败 [' + error.code + '] ' + error.message, 'bad');
    }
  });
}

function initAttachments() {
  const form = document.getElementById('upload-form');
  const status = document.getElementById('upload-status');
  const panel = document.getElementById('artifact-panel');
  const downloadButton = document.getElementById('artifact-download');
  const downloadStatus = document.getElementById('artifact-download-status');
  const quarantineBox = document.getElementById('artifact-quarantine');
  const quarantineReason = document.getElementById('artifact-quarantine-reason');
  let currentArtifactID = null;
  let pollTimer = null;

  function setStatus(message, kind) {
    status.textContent = message;
    status.className = 'status ' + (kind || '');
  }

  function setDownloadStatus(message, kind) {
    downloadStatus.textContent = message;
    downloadStatus.className = 'status ' + (kind || '');
  }

  function renderArtifact(artifact, reason) {
    currentArtifactID = artifact.id;
    const summary = artifactSummary(artifact);
    document.getElementById('artifact-id').textContent = summary.id;
    document.getElementById('artifact-state').textContent = summary.state;
    document.getElementById('artifact-media-type').textContent = summary.mediaType;
    document.getElementById('artifact-size').textContent = String(summary.sizeBytes);
    panel.hidden = false;
    const toolArtifactInput = document.getElementById('tool-artifact-id');
    if (toolArtifactInput) {
      // Convenience only: the value is user-visible text, never injected as
      // markup, and the tool-run request still re-validates it server-side.
      toolArtifactInput.value = summary.id;
    }

    const quarantined = summary.state === 'quarantined';
    quarantineBox.hidden = !quarantined;
    quarantineReason.textContent = quarantined ? reason || '' : '';
    downloadButton.hidden = summary.state !== 'ready';
    document.getElementById('artifact-note').textContent = quarantined
      ? '该附件已被隔离：不可下载，也不可作为任务输入。'
      : summary.state === 'ready'
        ? '校验通过。下载时才会向服务器获取新的短时签名链接。'
        : '后台正在扫描真实字节（uploading/scanning），页面会继续轮询。';
  }

  function stopPolling() {
    if (pollTimer !== null) {
      clearTimeout(pollTimer);
      pollTimer = null;
    }
  }

  async function poll(artifactID, attempts) {
    try {
      const result = await fetchArtifact(artifactID);
      renderArtifact(result.artifact, result.quarantineReason);
      if (result.artifact.state === 'ready') {
        setStatus('附件已就绪，可下载封存内容。', 'ok');
        return;
      }
      if (result.artifact.state === 'quarantined') {
        setStatus(
          '附件已隔离 [' + (result.quarantineReason || 'unknown') + '] ' +
            quarantineMessage(result.quarantineReason),
          'bad',
        );
        return;
      }
      if (!shouldPollArtifact(result.artifact.state, attempts, 100)) {
        setStatus('轮询已停止，附件仍为 ' + result.artifact.state + '，可稍后刷新重试。', 'pending');
        return;
      }
      pollTimer = setTimeout(() => poll(artifactID, attempts + 1), 600);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus('轮询失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    stopPolling();
    let payload;
    let file;
    try {
      file = document.getElementById('upload-file').files[0];
      payload = uploadInputForFile(
        file,
        document.getElementById('upload-media-type').value,
        document.getElementById('upload-purpose').value,
      );
    } catch (error) {
      setStatus('无法上传 [' + error.code + '] ' + error.message, 'bad');
      return;
    }

    try {
      await requireLogin();
      const csrfToken = await csrf();
      setStatus('正在计算 SHA-256…', 'pending');
      const buffer = await file.arrayBuffer();
      const sha256 = await sha256Hex(buffer);

      setStatus('正在预分配上传…', 'pending');
      const ticket = await createUploadRequest(payload, csrfToken);
      setStatus('正在上传字节…', 'pending');
      await putUploadBytes(ticket.upload_url, buffer);

      setStatus('正在提交校验…', 'pending');
      const artifact = await completeUploadRequest(ticket.artifact_id, sha256, csrfToken);
      renderArtifact(artifact, null);
      setStatus('已提交，等待后台扫描…', 'pending');
      pollTimer = setTimeout(() => poll(artifact.id, 0), 400);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setStatus('上传失败 [' + error.code + '] ' + error.message, 'bad');
    }
  });

  downloadButton.addEventListener('click', async () => {
    if (!currentArtifactID) {
      return;
    }
    setDownloadStatus('正在获取短时下载链接…', 'pending');
    try {
      const link = await fetchDownloadLink(currentArtifactID);
      setDownloadStatus('已获取链接，开始下载。', 'ok');
      window.location.assign(link.url);
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      setDownloadStatus('下载失败 [' + error.code + '] ' + error.message, 'bad');
    }
  });
}

if (typeof document !== 'undefined' && document.getElementById) {
  if (document.getElementById('upload-form')) {
    initAttachments();
  }
  if (document.getElementById('tool-panel')) {
    initTools();
  }
}
