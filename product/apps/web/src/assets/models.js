/*
 * F06/T039 models page helpers.
 *
 * Plain JavaScript, no framework and no build step (same style as app.js and
 * tools.js). Every function that takes server data returns text/ids; the page
 * builds DOM nodes with textContent, so a model name or a report reason can
 * never become markup.
 *
 * The catalog is the only source of endpoint/credential references. This file
 * never builds a secret, never reads one from the DOM, and never renders the
 * catalog's deployment environment name (the admin endpoints do not return it).
 */

function modelsPath() {
  return '/api/v1/models';
}

function modelPath(modelId) {
  return modelsPath() + '/' + encodeURIComponent(String(modelId || ''));
}

function modelValidatePath(modelId) {
  return modelPath(modelId) + '/validate';
}

function modelEndpointsPath() {
  return '/api/v1/admin/model-endpoints';
}

function credentialRefsPath() {
  return '/api/v1/admin/credential-refs';
}

function evaluationReportPath(reportId) {
  return '/api/v1/evaluation-reports/' + encodeURIComponent(String(reportId || ''));
}

function artifactContentPath(artifactId) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactId || '')) + '/content';
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Parse the config textarea into the ModelConfig object the contract declares.
 * Returns {ok:true, value} or {ok:false, error}; never throws for UI input.
 */
function parseConfigObject(label, text) {
  const source = String(text === undefined || text === null ? '' : text).trim();
  if (!source) {
    return { ok: false, error: label + ' 必须填写一个 JSON 对象' };
  }
  let value;
  try {
    value = JSON.parse(source);
  } catch (error) {
    return { ok: false, error: label + ' 不是合法 JSON' };
  }
  if (!isPlainObject(value)) {
    return { ok: false, error: label + ' 必须是 JSON 对象' };
  }
  return { ok: true, value };
}

/**
 * Build the exact ModelInput body. endpoint_ref/secret_ref/data_destination come
 * from the deployer-registered catalog selections, never free text.
 */
function buildRegisterBody(fields) {
  const values = fields || {};
  const name = String(values.name || '').trim();
  const provider = String(values.provider || '').trim();
  const modelId = String(values.modelId || '').trim();
  const endpointRef = String(values.endpointRef || '').trim();
  const secretRef = String(values.secretRef || '').trim();
  const dataDestination = String(values.dataDestination || '').trim();
  if (!name) {
    return { ok: false, error: 'name 不能为空' };
  }
  if (!provider) {
    return { ok: false, error: 'provider 不能为空' };
  }
  if (!modelId) {
    return { ok: false, error: 'model_id 不能为空' };
  }
  if (!endpointRef) {
    return { ok: false, error: '必须选择一个预登记 endpoint' };
  }
  if (!secretRef) {
    return { ok: false, error: '必须选择一个预登记 credential 引用' };
  }
  if (!dataDestination) {
    return { ok: false, error: '必须选择数据目的地' };
  }
  const config = parseConfigObject('config', values.config);
  if (!config.ok) {
    return config;
  }
  return {
    ok: true,
    body: {
      name: name,
      provider: provider,
      model_id: modelId,
      endpoint_ref: endpointRef,
      secret_ref: secretRef,
      config: config.value,
      data_destination: dataDestination,
    },
  };
}

/**
 * The idempotency key for one validate click. It is generated on the client and
 * kept for the retry of the same click; the server replays the recorded 202.
 */
function validateKey() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'validate-' + Date.now() + '-' + Math.random().toString(16).slice(2);
}

const MODEL_STATE_TEXT = {
  pending: '待验证',
  running: '验证中',
  passed: '已通过',
  failed: '验证失败',
};

/**
 * Project one ModelProfile for the list. Pending/running/failed models cannot be
 * selected by a shared application, and the row says so explicitly.
 */
function describeModel(item) {
  const value = item || {};
  const state = String(value.validation_state || '');
  const reportId = value.evaluation_report_id ? String(value.evaluation_report_id) : '';
  const usable = state === 'passed';
  const warning = usable
    ? ''
    : '此版本不能被共享应用引用（必须先通过兼容验证）。';
  return {
    id: String(value.id || ''),
    name: String(value.name || ''),
    modelId: String(value.model_id || ''),
    state: state,
    stateText: MODEL_STATE_TEXT[state] || ('未知状态 ' + state),
    dataDestination: String(value.data_destination || ''),
    reportId: reportId,
    reportPath: reportId ? evaluationReportPath(reportId) : '',
    usable: usable,
    warning: warning,
    validatePath: modelValidatePath(value.id),
    canValidate: !usable && state !== 'running',
  };
}

/**
 * Project the synthetic compatibility report (the sealed document, fetched with
 * the admin session through the report artifact). This page shows the verdict
 * and the bindings; it never invents a field.
 */
function describeReport(document) {
  if (!isPlainObject(document)) {
    return { ok: false, error: '报告内容不是 JSON 对象' };
  }
  const check = isPlainObject(document.check) ? document.check : {};
  const compatible = document.compatible === true;
  const checkName = String(check.name || '');
  const checkPassed = check.passed === true;
  const reason = String(check.reason || '');
  return {
    ok: true,
    kind: String(document.kind || ''),
    status: compatible ? 'passed' : 'failed',
    statusText: compatible ? '兼容验证通过' : '兼容验证失败',
    compatible: compatible,
    modelProfileVersionId: String(document.model_profile_version_id || ''),
    manifestSha256: String(document.manifest_sha256 || ''),
    callId: String(document.call_id || ''),
    fixtureArtifactId: String(document.fixture_artifact_id || ''),
    fixtureSha256: String(document.fixture_sha256 || ''),
    provider: String(document.provider || ''),
    stub: document.stub === true,
    checkName: checkName,
    checkPassed: checkPassed,
    checkReason: reason,
    checkText: (checkName || '检查') + (checkPassed ? ' 通过' : ' 未通过') + (reason ? '（' + reason + '）' : ''),
  };
}

/** Project the EvaluationJob response of a validate click. */
function describeValidation(evaluation) {
  const value = evaluation || {};
  const state = String(value.state || '');
  const reportId = value.report_id ? String(value.report_id) : '';
  return {
    id: String(value.id || ''),
    state: state,
    stateText: MODEL_STATE_TEXT[state] || state,
    reportId: reportId,
    reportPath: reportId ? evaluationReportPath(reportId) : '',
    errorCode: value.error_code ? String(value.error_code) : '',
  };
}

function canRegister(identity) {
  return Boolean(identity && Array.isArray(identity.roles) && identity.roles.includes('admin'));
}

function canValidate(identity) {
  return canRegister(identity);
}

function describeFailure(error) {
  if (!error) {
    return '未知错误';
  }
  return '[' + (error.code || 'error') + '] ' + (error.message || String(error));
}
