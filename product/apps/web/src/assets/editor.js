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
// F07/T075: fixed sub-application (call_app) authoring
// ---------------------------------------------------------------------------
//
// A call_app node's config is exactly {application_version: <published id>}; the
// fixed child version is the only thing that names what will run. Its `inputs`
// map child application input fields to bindings, and its `output_schema` is the
// refinement the node promises downstream. The panel built from these pure
// helpers reads the catalog/version endpoints (already ACL-filtered by the
// server) and only ever offers a version the creator can read; the server's
// validate endpoint remains the authority on whether the version is callable
// (a revoked version is refused there, because the contract version view has no
// revocation flag).
//
// The node id pattern and the binding rules mirror the P05 validator; the panel
// refuses a malformed node locally so the creator sees the reason next to the
// field, and the server still validates the saved document independently.

const CALL_APP_NODE_ID = /^[A-Za-z][A-Za-z0-9_-]{0,63}$/;
const CALL_APP_VERSION_ID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const CALL_APP_NODE_REF = /^[A-Za-z][A-Za-z0-9_-]*#\/.*$/;

function callAppFailure(code, message) {
  return editorFailure('call_app.' + code, message);
}

/** The applications a picker may offer: the server already applied the ACL. */
function callAppApplicationOptions(payload) {
  const items = payload && Array.isArray(payload.items) ? payload.items : [];
  return items
    .map((item) => ({ id: String(item.id), name: String(item.name || item.id) }))
    .sort((left, right) => (left.name < right.name ? -1 : left.name > right.name ? 1 : 0));
}

/** The published versions of one application, newest first, as the API lists them. */
function callAppVersionOptions(payload) {
  const items = payload && Array.isArray(payload.items) ? payload.items : [];
  return items.map((item) => ({
    id: String(item.id),
    applicationId: String(item.application_id || ''),
    versionNo: Number(item.version_no) || 0,
    manifestSha256: String(item.manifest_sha256 || ''),
    label: 'v' + String(item.version_no) + ' · ' + String(item.manifest_sha256 || '').slice(0, 12),
    version: item,
  }));
}

/**
 * The child version's frozen input/output schemas, or null when the row carries
 * no frozen definition (such a version cannot be called: the mapping would be
 * checked against nothing, and the server reports dependency_not_ready).
 */
function callAppChildSchemas(versionView) {
  const manifest = versionView && typeof versionView === 'object' ? versionView.manifest : null;
  const definition = manifest && typeof manifest === 'object' ? manifest.definition : null;
  if (!definition || typeof definition !== 'object' || Array.isArray(definition)) {
    return null;
  }
  const inputSchema = definition.input_schema;
  const outputSchema = definition.output_schema;
  if (!inputSchema || typeof inputSchema !== 'object' || Array.isArray(inputSchema)) {
    return null;
  }
  if (!outputSchema || typeof outputSchema !== 'object' || Array.isArray(outputSchema)) {
    return null;
  }
  return { inputSchema, outputSchema };
}

/** One editable row per declared child input field. */
function callAppInputRows(inputSchema, mapped) {
  const properties = inputSchema && typeof inputSchema.properties === 'object' && inputSchema.properties
    ? inputSchema.properties
    : {};
  const required = new Set(Array.isArray(inputSchema && inputSchema.required) ? inputSchema.required : []);
  const existing = mapped && typeof mapped === 'object' ? mapped : {};
  return Object.keys(properties).sort().map((field) => {
    const declared = properties[field] && typeof properties[field] === 'object' ? properties[field] : {};
    const binding = existing[field];
    let mode = 'none';
    let value = '';
    if (binding && typeof binding === 'object' && !Array.isArray(binding)) {
      if (Object.prototype.hasOwnProperty.call(binding, 'literal')) {
        mode = 'literal';
        value = JSON.stringify(binding.literal);
      } else if (typeof binding.ref === 'string') {
        if (binding.ref.startsWith('input#')) {
          mode = 'input';
          value = binding.ref.slice('input#'.length) || '/' + field;
        } else if (binding.ref.startsWith('node:')) {
          mode = 'node';
          value = binding.ref.slice('node:'.length);
        }
      }
    }
    return {
      field,
      type: typeof declared.type === 'string' ? declared.type : 'any',
      required: required.has(field),
      mode,
      value,
    };
  });
}

/** One editable row per declared child output field. */
function callAppOutputRows(outputSchema, declaredOutput) {
  const properties = outputSchema && typeof outputSchema.properties === 'object' && outputSchema.properties
    ? outputSchema.properties
    : {};
  const required = new Set(Array.isArray(outputSchema && outputSchema.required) ? outputSchema.required : []);
  const declaredProperties = declaredOutput && typeof declaredOutput.properties === 'object' && declaredOutput.properties
    ? declaredOutput.properties
    : {};
  return Object.keys(properties).sort().map((field) => {
    const declared = properties[field] && typeof properties[field] === 'object' ? properties[field] : {};
    const chosen = declaredProperties[field] && typeof declaredProperties[field] === 'object'
      ? declaredProperties[field]
      : null;
    return {
      field,
      type: typeof declared.type === 'string' ? declared.type : 'string',
      required: required.has(field),
      include: Boolean(chosen),
      declaredType: chosen && typeof chosen.type === 'string' ? chosen.type : '',
    };
  });
}

/**
 * One binding from the panel's mode/value pair, or null for "not mapped".
 *
 * Literal values are JSON (so a string needs quotes, exactly like the DSL); an
 * application-input pointer is normalised to a leading slash; a node reference is
 * `<node id>#<pointer>` and is prefixed with `node:`.
 */
function callAppBinding(mode, value) {
  const kind = String(mode || 'none');
  const text = String(value === undefined || value === null ? '' : value).trim();
  if (kind === 'none') {
    return null;
  }
  if (kind === 'input') {
    const pointer = text === '' ? '' : (text.startsWith('/') ? text : '/' + text);
    if (pointer === '' || pointer === '/') {
      throw callAppFailure('input_pointer', '应用输入引用需要一个 JSON Pointer，例如 /source');
    }
    return { ref: 'input#' + pointer };
  }
  if (kind === 'node') {
    if (!CALL_APP_NODE_REF.test(text)) {
      throw callAppFailure('node_ref', '节点输出引用必须写成 <节点 id>#/<字段>，例如 python1#/report');
    }
    return { ref: 'node:' + text };
  }
  if (kind === 'literal') {
    if (text === '') {
      throw callAppFailure('literal', '字面量需要一个 JSON 值（字符串请加引号，例如 "eu"）');
    }
    try {
      return { literal: JSON.parse(text) };
    } catch (error) {
      throw callAppFailure('literal', '字面量不是合法 JSON：' + error.message);
    }
  }
  throw callAppFailure('mode', '未知的映射方式：' + kind);
}

/** Build the call_app node from the panel form. Invalid fields throw located reasons. */
function callAppNodeFromForm(form) {
  const source = form || {};
  const nodeId = String(source.nodeId === undefined || source.nodeId === null ? '' : source.nodeId).trim();
  if (!CALL_APP_NODE_ID.test(nodeId)) {
    throw callAppFailure('node_id', '节点 id 必须匹配 [A-Za-z][A-Za-z0-9_-]{0,63}');
  }
  const versionId = String(source.versionId === undefined || source.versionId === null ? '' : source.versionId).trim();
  if (!CALL_APP_VERSION_ID.test(versionId)) {
    throw callAppFailure('version', '请选择一个已发布版本（固定版本，不接受 latest）');
  }
  const inputs = {};
  for (const row of source.inputRows || []) {
    const binding = callAppBinding(row.mode, row.value);
    if (binding !== null) {
      inputs[row.field] = binding;
    }
  }
  const properties = {};
  const required = [];
  for (const row of source.outputRows || []) {
    if (!row.include) {
      continue;
    }
    properties[row.field] = { type: row.type || 'string' };
    if (row.required) {
      required.push(row.field);
    }
  }
  const outputSchema = { type: 'object', properties };
  if (required.length > 0) {
    outputSchema.required = required.sort();
  }
  return {
    id: nodeId,
    type: 'call_app',
    inputs,
    output_schema: outputSchema,
    config: { application_version: versionId },
  };
}

/**
 * Insert or replace one call_app node in the definition and keep the chain
 * connected: a new node is placed before `end` and inherits its single incoming
 * edge (start -> ... -> call_app -> end). A node id already used by another type
 * is refused rather than silently converted.
 */
function upsertCallAppNode(definition, node) {
  const draft = definition && typeof definition === 'object' && !Array.isArray(definition)
    ? JSON.parse(JSON.stringify(definition))
    : {};
  draft.nodes = Array.isArray(draft.nodes) ? draft.nodes : [];
  draft.edges = Array.isArray(draft.edges) ? draft.edges : [];
  const existing = draft.nodes.find((candidate) => candidate && candidate.id === node.id);
  if (existing) {
    if (existing.type && existing.type !== 'call_app') {
      throw callAppFailure('node_id', '节点 id ' + node.id + ' 已被一个 ' + existing.type + ' 节点使用');
    }
    existing.type = 'call_app';
    existing.inputs = node.inputs;
    existing.output_schema = node.output_schema;
    existing.config = node.config;
    return draft;
  }
  const end = draft.nodes.find((candidate) => candidate && candidate.type === 'end');
  draft.nodes.splice(end ? draft.nodes.indexOf(end) : draft.nodes.length, 0, node);
  if (!end) {
    return draft;
  }
  const incoming = draft.edges.find((edge) => edge && edge.target === end.id);
  if (incoming) {
    incoming.target = node.id;
  } else {
    draft.edges.push({ source: node.id, target: end.id });
  }
  const outgoing = draft.edges.some((edge) => edge && edge.source === node.id && edge.target === end.id);
  if (!outgoing) {
    draft.edges.push({ source: node.id, target: end.id });
  }
  return draft;
}

/**
 * The editor's node strip plus the located errors per node. This is the
 * highlight surface the editor owns: every node is one chip, a chip whose node
 * is named by a located error is marked invalid, and each error keeps its exact
 * pointer so the panel can show which field to fix. Document-level errors
 * (for example /policy/max_child_calls) are returned separately.
 */
function callAppValidationModel(definition, errors) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const entries = nodes.map((node, index) => ({
    nodeId: node && typeof node.id === 'string' ? node.id : '',
    type: node && typeof node.type === 'string' ? node.type : '',
    index,
    callApp: Boolean(node && node.type === 'call_app'),
    errors: [],
  }));
  const documentLevel = [];
  for (const error of errors || []) {
    const match = /^\/nodes\/(\d+)(\/.*)?$/.exec(String(error && error.pointer ? error.pointer : ''));
    if (!match) {
      documentLevel.push({
        code: error.code,
        pointer: error.pointer,
        message: error.message,
      });
      continue;
    }
    const entry = entries[Number(match[1])];
    if (!entry) {
      documentLevel.push({ code: error.code, pointer: error.pointer, message: error.message });
      continue;
    }
    const suffix = match[2] ? match[2].replace(/^\//, '') : '';
    entry.errors.push({
      code: error.code,
      pointer: error.pointer,
      field: suffix,
      message: error.message,
    });
  }
  return { nodes: entries, documentLevel };
}

/** The call_app panel's view of one selected child version. */
function callAppPanelModel(versionView) {
  const schemas = callAppChildSchemas(versionView);
  if (!schemas) {
    return {
      callable: false,
      reason: '这个版本没有冻结的 definition，不能作为固定子应用调用（服务端会以 dependency_not_ready 拒绝）',
      inputRows: [],
      outputRows: [],
    };
  }
  return {
    callable: true,
    reason: '',
    inputRows: callAppInputRows(schemas.inputSchema),
    outputRows: callAppOutputRows(schemas.outputSchema),
  };
}

/** POST /validate. The contract answers 200 with {valid,errors}; only transport errors throw. */
async function validateDraftRequest(applicationId) {
  const result = await apiFull('/api/v1/applications/' + encodeURIComponent(applicationId) + '/validate', {
    method: 'POST',
    csrf: await csrf(),
  });
  const payload = result && result.payload && typeof result.payload === 'object'
    ? result.payload
    : { valid: false, errors: [] };
  return {
    valid: payload.valid === true,
    errors: Array.isArray(payload.errors) ? payload.errors : [],
  };
}

/** The panel state of one existing call_app node (null when the id is not one). */
function callAppPrefillFromNode(definition, nodeId, versionView) {
  const nodes = definition && Array.isArray(definition.nodes) ? definition.nodes : [];
  const node = nodes.find((candidate) => candidate && candidate.id === nodeId);
  if (!node || node.type !== 'call_app') {
    return null;
  }
  const versionId = node.config && typeof node.config.application_version === 'string'
    ? node.config.application_version
    : '';
  const schemas = callAppChildSchemas(versionView);
  return {
    nodeId: node.id,
    versionId,
    inputRows: schemas ? callAppInputRows(schemas.inputSchema, node.inputs) : [],
    outputRows: schemas ? callAppOutputRows(schemas.outputSchema, node.output_schema) : [],
  };
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

// The F04A canvas is mounted lazily after the draft is loaded; it shares the
// page's save path by mirroring its definition/layout into the JSON textareas,
// so the ETag/412/428 behaviour above is unchanged.
let canvasApi = null;

function editorMountCanvas(applicationId, draft) {
  const root = document.getElementById('canvas-root');
  if (!root || !window.OrcharaCanvas || typeof window.OrcharaCanvas.mount !== 'function') {
    return null;
  }
  const definition = draft && draft.definition && typeof draft.definition === 'object'
    ? draft.definition
    : {};
  const layout = draft && draft.layout && typeof draft.layout === 'object' ? draft.layout : {};
  if (canvasApi) {
    canvasApi.setDraft(definition, layout);
    return canvasApi;
  }
  canvasApi = window.OrcharaCanvas.mount(root, {
    applicationId,
    definition,
    layout,
    onChange(nextDefinition, nextLayout) {
      const definitionField = document.getElementById('draft-definition');
      const layoutField = document.getElementById('draft-layout');
      if (definitionField) {
        definitionField.value = JSON.stringify(nextDefinition, null, 2);
      }
      if (layoutField) {
        layoutField.value = JSON.stringify(nextLayout, null, 2);
      }
    },
  });
  return canvasApi;
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
  const applicationId = editorApplicationId(window.location.pathname);
  if (applicationId) {
    editorMountCanvas(applicationId, draft);
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

// ---------------------------------------------------------------------------
// F07/T075: call_app panel DOM wiring
// ---------------------------------------------------------------------------
//
// The F04A canvas bundle is a deliberately linear four-node authoring tool
// (start/python/agent/end); T075 adds the fixed sub-application node on the
// editor's own model/config surface instead of pretending the canvas renders it.
// The panel reads the same ACL-filtered catalog/version endpoints the server
// owns, writes the node into the definition textarea (the page's single save
// path, so ETag/412/428 are unchanged) and renders the located validate errors
// against the node chips.

const callAppPanelState = {
  applications: [],
  versions: [],
  version: null,
  inputRows: [],
  outputRows: [],
  lastErrors: [],
};

function callAppStatus(message, kind) {
  const element = document.getElementById('callapp-status');
  if (!element) {
    return;
  }
  element.textContent = message;
  element.className = 'status ' + (kind || '');
}

function callAppElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) {
    element.className = className;
  }
  if (text !== undefined) {
    element.textContent = text;
  }
  return element;
}

function callAppOption(value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  return option;
}

/** Parse the definition textarea; a JSON error is a located page failure. */
function callAppReadDefinition() {
  let parsed;
  try {
    parsed = JSON.parse(document.getElementById('draft-definition').value || '{}');
  } catch (error) {
    throw editorFailure('invalid_json', 'definition 不是合法 JSON：' + error.message);
  }
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw editorFailure('invalid_json', 'definition 必须是 JSON 对象');
  }
  return parsed;
}

async function callAppLoadApplications() {
  const select = document.getElementById('callapp-application');
  if (!select) {
    return;
  }
  const payload = await api('/api/v1/applications?limit=100');
  callAppPanelState.applications = callAppApplicationOptions(payload);
  select.replaceChildren(callAppOption('', '选择可调用的应用…'));
  for (const entry of callAppPanelState.applications) {
    select.appendChild(callAppOption(entry.id, entry.name + ' · ' + entry.id.slice(0, 8)));
  }
}

async function callAppLoadVersions(applicationId) {
  const select = document.getElementById('callapp-version');
  if (!select) {
    return;
  }
  callAppPanelState.versions = [];
  callAppPanelState.version = null;
  callAppPanelState.inputRows = [];
  callAppPanelState.outputRows = [];
  select.replaceChildren(callAppOption('', '选择固定版本…'));
  callAppRenderInputRows();
  callAppRenderOutputRows();
  if (!applicationId) {
    callAppStatus('先选择一个应用；只列出服务端允许当前用户读取的应用与版本。', '');
    return;
  }
  const payload = await api(
    '/api/v1/applications/' + encodeURIComponent(applicationId) + '/versions?limit=100',
  );
  callAppPanelState.versions = callAppVersionOptions(payload);
  for (const entry of callAppPanelState.versions) {
    select.appendChild(callAppOption(entry.id, entry.label));
  }
  if (callAppPanelState.versions.length === 0) {
    callAppStatus('这个应用还没有已发布版本，不能作为固定子应用。', 'bad');
  }
}

function callAppSelectVersion(versionId) {
  callAppPanelState.version = callAppPanelState.versions.find((entry) => entry.id === versionId) || null;
  const model = callAppPanelState.version
    ? callAppPanelModel(callAppPanelState.version.version)
    : { callable: false, reason: '', inputRows: [], outputRows: [] };
  callAppPanelState.inputRows = model.inputRows;
  callAppPanelState.outputRows = model.outputRows;
  callAppStatus(
    model.callable
      ? '已选择固定版本；输入/输出映射以该版本冻结的 Schema 为准。'
      : model.reason,
    model.callable ? 'ok' : 'bad',
  );
  callAppRenderInputRows();
  callAppRenderOutputRows();
}

function callAppRenderInputRows() {
  const root = document.getElementById('callapp-input-rows');
  if (!root) {
    return;
  }
  root.replaceChildren();
  if (callAppPanelState.inputRows.length === 0) {
    root.appendChild(callAppElement('p', 'hint', '尚未选择版本，或该版本没有声明输入字段。'));
    return;
  }
  for (const row of callAppPanelState.inputRows) {
    const container = callAppElement('div', 'callapp-row');
    container.appendChild(callAppElement(
      'span',
      'callapp-field',
      row.field + ' · ' + row.type + (row.required ? ' · 必填' : ''),
    ));
    const mode = document.createElement('select');
    for (const [value, label] of [
      ['none', '不映射'],
      ['input', '应用输入'],
      ['literal', '字面量 JSON'],
      ['node', '节点输出'],
    ]) {
      mode.appendChild(callAppOption(value, label));
    }
    mode.value = row.mode;
    mode.addEventListener('change', () => {
      row.mode = mode.value;
    });
    container.appendChild(mode);
    const value = document.createElement('input');
    value.type = 'text';
    value.value = row.value;
    value.placeholder = row.mode === 'literal' ? '"eu"' : '/source 或 python1#/report';
    value.addEventListener('input', () => {
      row.value = value.value;
    });
    container.appendChild(value);
    root.appendChild(container);
  }
}

function callAppRenderOutputRows() {
  const root = document.getElementById('callapp-output-rows');
  if (!root) {
    return;
  }
  root.replaceChildren();
  if (callAppPanelState.outputRows.length === 0) {
    root.appendChild(callAppElement('p', 'hint', '尚未选择版本，或该版本没有声明输出字段。'));
    return;
  }
  const types = ['string', 'integer', 'number', 'boolean', 'object', 'array'];
  for (const row of callAppPanelState.outputRows) {
    const container = callAppElement('div', 'callapp-row');
    const include = document.createElement('input');
    include.type = 'checkbox';
    include.checked = row.include;
    include.addEventListener('change', () => {
      row.include = include.checked;
    });
    container.appendChild(include);
    container.appendChild(callAppElement(
      'span',
      'callapp-field',
      row.field + ' · ' + row.type + (row.required ? ' · 子应用必填' : ''),
    ));
    const type = document.createElement('select');
    for (const name of types) {
      type.appendChild(callAppOption(name, name));
    }
    type.value = row.declaredType || row.type;
    type.addEventListener('change', () => {
      row.declaredType = type.value;
    });
    container.appendChild(type);
    root.appendChild(container);
  }
}

function callAppRenderNodes(definition, errors) {
  const root = document.getElementById('editor-node-list');
  if (!root) {
    return;
  }
  const model = callAppValidationModel(definition, errors);
  root.replaceChildren();
  if (model.nodes.length === 0) {
    root.appendChild(callAppElement('p', 'hint', 'definition 还没有节点。'));
  }
  for (const entry of model.nodes) {
    const chip = callAppElement(
      'button',
      'node-chip' + (entry.callApp ? ' call-app' : '') + (entry.errors.length > 0 ? ' invalid' : ''),
    );
    chip.type = 'button';
    chip.setAttribute('data-node-id', entry.nodeId);
    chip.appendChild(callAppElement('span', 'node-chip-id', entry.nodeId));
    chip.appendChild(callAppElement('span', 'node-chip-type', entry.type));
    if (entry.errors.length > 0) {
      chip.appendChild(callAppElement('span', 'node-chip-errors', entry.errors.length + ' 个错误'));
    }
    chip.addEventListener('click', () => {
      callAppPickNode(entry.nodeId);
    });
    root.appendChild(chip);
    for (const error of entry.errors) {
      root.appendChild(callAppElement(
        'div',
        'node-error',
        error.code + ' · ' + error.pointer + ' · ' + error.message,
      ));
    }
  }
  const documentRoot = document.getElementById('callapp-errors');
  if (documentRoot) {
    documentRoot.replaceChildren();
    for (const error of model.documentLevel) {
      documentRoot.appendChild(callAppElement(
        'div',
        'node-error',
        error.code + ' · ' + (error.pointer || '(document)') + ' · ' + error.message,
      ));
    }
  }
}

/** Load one existing call_app node back into the panel (node chip click). */
function callAppPickNode(nodeId) {
  const nodeField = document.getElementById('callapp-node-id');
  if (nodeField) {
    nodeField.value = nodeId;
  }
  let definition;
  try {
    definition = callAppReadDefinition();
  } catch (error) {
    callAppStatus('无法读取 definition [' + error.code + '] ' + error.message, 'bad');
    return;
  }
  const node = (definition.nodes || []).find((candidate) => candidate && candidate.id === nodeId);
  if (!node || node.type !== 'call_app') {
    callAppStatus('选中节点 ' + nodeId + '（' + (node ? node.type : '未知') + '）；只有 call_app 节点能在这里编辑。', '');
    return;
  }
  const versionId = node.config && typeof node.config.application_version === 'string'
    ? node.config.application_version
    : '';
  const entry = callAppPanelState.versions.find((candidate) => candidate.id === versionId);
  if (!entry) {
    callAppStatus(
      '节点 ' + nodeId + ' 引用的版本 ' + versionId +
      ' 不在当前可读版本列表里：请重新选择应用/版本，或确认该应用仍对当前用户可读。',
      'bad',
    );
    return;
  }
  const versionSelect = document.getElementById('callapp-version');
  if (versionSelect) {
    versionSelect.value = entry.id;
  }
  const prefill = callAppPrefillFromNode(definition, nodeId, entry.version);
  callAppSelectVersion(entry.id);
  if (prefill) {
    callAppPanelState.inputRows = prefill.inputRows;
    callAppPanelState.outputRows = prefill.outputRows;
    callAppRenderInputRows();
    callAppRenderOutputRows();
  }
  callAppStatus('已载入 call_app 节点 ' + nodeId + ' 的固定版本与映射。', 'ok');
}

function callAppApplyNode() {
  try {
    const node = callAppNodeFromForm({
      nodeId: document.getElementById('callapp-node-id').value,
      versionId: document.getElementById('callapp-version').value,
      inputRows: callAppPanelState.inputRows,
      outputRows: callAppPanelState.outputRows,
    });
    const definition = callAppReadDefinition();
    const next = upsertCallAppNode(definition, node);
    document.getElementById('draft-definition').value = JSON.stringify(next, null, 2);
    callAppRenderNodes(next, callAppPanelState.lastErrors);
    callAppStatus('已写入表单；保存/校验后服务端才会冻结这个映射。', 'ok');
  } catch (error) {
    callAppStatus('无法写入 call_app 节点 [' + error.code + '] ' + error.message, 'bad');
  }
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
      callAppPanelState.lastErrors = [];
      callAppRenderNodes(loaded.draft.definition || {}, []);
      await editorInitCallAppPanel();
      editorStatus('已加载服务器上的草稿（revision ' + draftRevisionLabel(etag) + '）。', 'ok');
    } catch (error) {
      if (error.status === 401) {
        window.location.assign('/login');
        return;
      }
      editorStatus('加载失败 [' + error.code + '] ' + error.message, 'bad');
    }
  }

  async function persistDraft() {
    let payload;
    try {
      payload = draftPayloadFromFields(editorReadFields());
    } catch (error) {
      editorStatus('无法保存 [' + error.code + '] ' + error.message, 'bad');
      return false;
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
      return true;
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
        return false;
      }
      if (error.status === 428) {
        editorShowConflict(true, '保存请求缺少 If-Match；请重新加载草稿后再保存。');
        editorStatus('保存被拒绝 [precondition_required]。', 'bad');
        return false;
      }
      if (error.status === 401) {
        window.location.assign('/login');
        return false;
      }
      editorStatus('保存失败 [' + error.code + '] ' + error.message, 'bad');
      return false;
    }
  }

  async function save(event) {
    event.preventDefault();
    await persistDraft();
  }

  async function editorInitCallAppPanel() {
    const applicationSelect = document.getElementById('callapp-application');
    if (!applicationSelect) {
      return;
    }
    applicationSelect.addEventListener('change', async () => {
      try {
        await callAppLoadVersions(applicationSelect.value);
      } catch (error) {
        callAppStatus('无法加载版本 [' + error.code + '] ' + error.message, 'bad');
      }
    });
    const versionSelect = document.getElementById('callapp-version');
    if (versionSelect) {
      versionSelect.addEventListener('change', (event) => {
        callAppSelectVersion(event.target.value);
      });
    }
    const addButton = document.getElementById('callapp-add');
    if (addButton) {
      addButton.addEventListener('click', callAppApplyNode);
    }
    const validateButton = document.getElementById('callapp-validate');
    if (validateButton) {
      validateButton.addEventListener('click', async () => {
        callAppStatus('正在保存并校验…', 'pending');
        if (!(await persistDraft())) {
          callAppStatus('保存未完成，未发起校验。', 'bad');
          return;
        }
        try {
          const result = await validateDraftRequest(applicationId);
          callAppPanelState.lastErrors = result.errors;
          callAppRenderNodes(callAppReadDefinition(), result.errors);
          if (result.valid) {
            callAppStatus('服务端校验通过：固定版本与 I/O 映射可发布，没有定位错误。', 'ok');
            return;
          }
          const first = result.errors[0] || {};
          callAppStatus(
            '服务端校验发现 ' + result.errors.length + ' 个问题；第一个：' +
              (first.code || '未知') + ' @ ' + (first.pointer || '(document)'),
            'bad',
          );
        } catch (error) {
          callAppStatus('校验失败 [' + error.code + '] ' + error.message, 'bad');
        }
      });
    }
    try {
      await callAppLoadApplications();
    } catch (error) {
      callAppStatus('无法加载可调用应用 [' + error.code + '] ' + error.message, 'bad');
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
