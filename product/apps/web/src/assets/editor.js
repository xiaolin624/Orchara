/*
 * F02A/T023 draft editor.
 *
 * The editor is a real client of the contract:
 *
 *   GET  /api/v1/applications/{id}/draft        -> DraftInput + ETag
 *   PUT  /api/v1/applications/{id}/draft        -> If-Match required, new ETag
 *
 * The ETag is the save's precondition. A 412 means somebody else saved first:
 * the page must not retry automatically and must not overwrite. It shows the
 * conflict panel with exactly two choices — reload the server version (discard
 * local edits) or keep a local copy (download the JSON) — and leaves the form
 * untouched until the user chooses. A 428 can only be reached by a bug in this
 * page (the ETag comes from the GET); it is displayed, never retried.
 *
 * The pure helpers at the top are exported on purpose: tests execute them under
 * Node with stubbed fetch/headers, which is the only way a page assertion can be
 * behavioural without a browser. The DOM wiring stays at the bottom.
 */

function editorFailure(code, message) {
  const failure = new Error(message);
  failure.code = code;
  failure.status = 0;
  return failure;
}

/** The application id from /apps/{id}/edit, or null for any other path. */
function editorApplicationId(pathname) {
  const match = /^\/apps\/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\/edit\/?$/.exec(
    String(pathname || ''),
  );
  return match ? match[1].toLowerCase() : null;
}

function draftPath(applicationId) {
  return '/api/v1/applications/' + encodeURIComponent(applicationId) + '/draft';
}

/** One value per line, blanks dropped; the card arrays are textarea lines. */
function splitLines(value) {
  return String(value === undefined || value === null ? '' : value)
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line !== '');
}

const CAPABILITY_CARD_FIELDS = [
  'purpose',
  'includes',
  'excludes',
  'required_inputs',
  'expected_outputs',
  'examples',
];

/**
 * Build the contract capability_card from the form fields.
 *
 * An untouched card is {} — the honest "not filled in yet" value a new draft
 * has. A touched card must be the full six-key shape (the contract marks every
 * key required and examples minItems=2); the server enforces the same rule, and
 * the page refuses first so the creator sees the reason next to the field.
 */
function capabilityCardFromFields(fields) {
  const source = fields || {};
  const card = {
    purpose: String(source.purpose === undefined || source.purpose === null ? '' : source.purpose).trim(),
    includes: splitLines(source.includes),
    excludes: splitLines(source.excludes),
    required_inputs: splitLines(source.required_inputs),
    expected_outputs: splitLines(source.expected_outputs),
    examples: splitLines(source.examples),
  };
  const touched = CAPABILITY_CARD_FIELDS.some((key) =>
    key === 'purpose' ? card.purpose !== '' : card[key].length > 0,
  );
  return touched ? card : {};
}

/** Parse one textarea as a JSON object; the message names the field. */
function parseJSONObject(text, label) {
  let value;
  try {
    value = JSON.parse(String(text === undefined || text === null ? '' : text));
  } catch (error) {
    throw editorFailure('invalid_json', label + ' 不是合法 JSON：' + error.message);
  }
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw editorFailure('invalid_json', label + ' 必须是 JSON 对象');
  }
  return value;
}

/**
 * The exact PUT body. Client-side validation mirrors the server's structural
 * rules only; the DSL itself is allowed to be invalid here (it may not be
 * debugged or submitted, which T025/T027 enforce, not the save path).
 */
function draftPayloadFromFields(fields) {
  const source = fields || {};
  const card = capabilityCardFromFields(source.capabilityCard || {});
  if (Object.keys(card).length > 0 && card.examples.length < 2) {
    throw editorFailure(
      'capability_card.examples',
      '能力卡填写后至少需要 2 个示例（契约 capability_card.examples minItems=2）',
    );
  }
  return {
    definition: parseJSONObject(source.definition, 'definition'),
    layout: parseJSONObject(source.layout, 'layout'),
    capability_card: card,
  };
}

/** Parse the strong ETag ("<revision>") the server issued. */
function revisionFromETag(etag) {
  const match = /^"(\d+)"$/.exec(String(etag || ''));
  return match ? Number(match[1]) : null;
}

function draftRevisionLabel(etag) {
  const revision = revisionFromETag(etag);
  return revision === null ? '未知' : String(revision);
}

/** GET the draft and the ETag a save must echo. */
async function loadDraftRequest(applicationId) {
  const result = await apiFull(draftPath(applicationId));
  const etag =
    result.headers && typeof result.headers.get === 'function' ? result.headers.get('ETag') : null;
  if (!etag) {
    throw editorFailure('missing_etag', '服务器没有返回 ETag，无法安全保存草稿');
  }
  return { draft: result.payload, etag: etag };
}

/** PUT one draft with If-Match. The caller decides how to show a 412. */
async function saveDraftRequest(applicationId, etag, payload) {
  if (!etag) {
    throw editorFailure('missing_etag', '缺少 ETag，无法保存草稿');
  }
  return apiFull(draftPath(applicationId), {
    method: 'PUT',
    body: payload,
    csrf: await csrf(),
    headers: { 'If-Match': etag },
  });
}

/** The conflict branch of a save: 412 is "reload or keep a local copy". */
function isPreconditionConflict(error) {
  return Boolean(error) && error.status === 412;
}

/** The downloadable local copy: the exact payload the creator tried to save. */
function localCopyJSON(payload) {
  return JSON.stringify(payload, null, 2);
}

// ---------------------------------------------------------------------------
// DOM wiring (not executed by the Node behaviour test)
// ---------------------------------------------------------------------------

function editorStatus(message, kind) {
  const element = document.getElementById('editor-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function editorSetRevision(etag) {
  const revision = document.getElementById('draft-revision');
  if (revision) {
    revision.textContent = draftRevisionLabel(etag);
  }
  const execution = document.getElementById('draft-execution-revision');
  if (execution) {
    execution.textContent = '保存后刷新可见';
  }
}

function editorFillForm(draft) {
  document.getElementById('draft-definition').value = JSON.stringify(draft.definition || {}, null, 2);
  document.getElementById('draft-layout').value = JSON.stringify(draft.layout || {}, null, 2);
  const card = draft.capability_card || {};
  document.getElementById('card-purpose').value = card.purpose || '';
  for (const key of ['includes', 'excludes', 'required_inputs', 'expected_outputs', 'examples']) {
    const element = document.getElementById('card-' + key.replace(/_/g, '-'));
    element.value = Array.isArray(card[key]) ? card[key].join('\n') : '';
  }
}

function editorReadFields() {
  return {
    definition: document.getElementById('draft-definition').value,
    layout: document.getElementById('draft-layout').value,
    capabilityCard: {
      purpose: document.getElementById('card-purpose').value,
      includes: document.getElementById('card-includes').value,
      excludes: document.getElementById('card-excludes').value,
      required_inputs: document.getElementById('card-required-inputs').value,
      expected_outputs: document.getElementById('card-expected-outputs').value,
      examples: document.getElementById('card-examples').value,
    },
  };
}

function editorShowConflict(visible, detail) {
  const panel = document.getElementById('conflict-panel');
  if (!panel) {
    return;
  }
  panel.hidden = !visible;
  const status = document.getElementById('conflict-status');
  if (status) {
    status.textContent = visible && detail ? detail : '';
  }
}

function editorDownloadLocalCopy(applicationId, payload) {
  const link = document.createElement('a');
  const blob = new Blob([localCopyJSON(payload)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  link.href = url;
  link.download = 'orchara-draft-' + applicationId + '.json';
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

function initEditor() {
  const applicationId = editorApplicationId(window.location.pathname);
  if (!applicationId) {
    editorStatus('这个地址没有指向一个应用编辑器（应为 /apps/<application id>/edit）。', 'bad');
    return;
  }
  // The debug entry is per application, so it is built from the path segment
  // instead of putting an id in a query string. The link is inert until the
  // page knows which application it edits.
  const debugEntry = document.getElementById('debug-entry');
  if (debugEntry) {
    debugEntry.href = '/apps/' + encodeURIComponent(applicationId) + '/debug';
    debugEntry.textContent = '打开私有调试页';
  }
  let etag = null;

  async function load() {
    editorStatus('正在加载草稿…', 'pending');
    editorShowConflict(false, '');
    try {
      await requireLogin();
      const loaded = await loadDraftRequest(applicationId);
      etag = loaded.etag;
      editorFillForm(loaded.draft);
      editorSetRevision(etag);
      editorStatus('已加载服务器上的草稿（revision ' + draftRevisionLabel(etag) + '）。', 'ok');
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      editorStatus('加载失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function save(event) {
    event.preventDefault();
    let payload;
    try {
      payload = draftPayloadFromFields(editorReadFields());
    } catch (error) {
      editorStatus('无法保存 [' + error.code + '] ' + error.message, 'bad');
      return;
    }
    editorStatus('正在保存…', 'pending');
    try {
      const result = await saveDraftRequest(applicationId, etag, payload);
      const next = result.headers && typeof result.headers.get === 'function' ? result.headers.get('ETag') : null;
      if (next) {
        etag = next;
        editorSetRevision(etag);
      }
      editorShowConflict(false, '');
      editorStatus('已保存（revision ' + draftRevisionLabel(etag) + '）。刷新页面会加载这份草稿。', 'ok');
    } catch (error) {
      if (isPreconditionConflict(error)) {
        const current = error.details && error.details.current_revision;
        editorShowConflict(
          true,
          '服务器上的草稿已被别人保存（当前 revision ' +
            (current === undefined || current === null ? '未知' : current) +
            '）。你的本地修改没有保存；请选择重载服务器版本，或先下载本地副本。',
        );
        editorStatus('保存冲突，未覆盖服务器内容。', 'bad');
        return;
      }
      if (error.status === 428) {
        editorShowConflict(true, '保存请求缺少 If-Match；请重新加载草稿后再保存。');
        editorStatus('保存被拒绝 [precondition_required]。', 'bad');
        return;
      }
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      editorStatus('保存失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  document.getElementById('draft-form').addEventListener('submit', save);
  document.getElementById('conflict-reload').addEventListener('click', () => {
    // Explicit user action: reloading discards local edits, which is why the
    // panel offers the download first and never reloads on its own.
    load();
  });
  document.getElementById('conflict-download').addEventListener('click', () => {
    try {
      editorDownloadLocalCopy(applicationId, draftPayloadFromFields(editorReadFields()));
    } catch (error) {
      editorStatus('无法生成本地副本 [' + error.code + '] ' + error.message, 'bad');
    }
  });
  load();
}

if (typeof document !== 'undefined' && document.getElementById && document.getElementById('draft-form')) {
  initEditor();
}
