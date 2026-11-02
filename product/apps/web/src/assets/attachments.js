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

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('upload-form')) {
  initAttachments();
}
