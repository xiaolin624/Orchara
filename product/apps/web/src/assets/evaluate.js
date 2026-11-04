/*
 * F13A/T056 evaluation page helpers.
 *
 * The page is a real client of the official paths:
 *
 *   GET  /api/v1/applications/{id}/draft                 -> current draft + revision
 *   POST /api/v1/artifacts/uploads                        -> 201 Upload
 *   PUT  <signed staging URL>                             -> 204
 *   POST /api/v1/artifacts/{id}/complete                  -> 202 Artifact
 *   GET  /api/v1/artifacts/{id}                           -> 200 Artifact
 *   POST /api/v1/applications/{id}/evaluations            -> 202 EvaluationJob (evaluateDraft)
 *   GET  /api/v1/evaluations/{id}                         -> 200 EvaluationJob
 *   GET  /api/v1/evaluation-reports/{id}/cases            -> 200 per-case results (ADR 0013)
 *   GET  /api/v1/artifacts/{id}/download                  -> 200 short-lived download link
 *
 * Everything below is pure so the Node page test executes it: paths are built
 * from ids with encodeURIComponent, request bodies are exactly the shapes the
 * server accepts, and a case row is derived from the sealed report's own
 * expected/actual evidence. Money is never converted to a binary float: the
 * page renders the report's decimal strings verbatim.
 */

const EVALUATION_DATASET_MEDIA_TYPE = 'application/json';
const CASE_INPUT_MEDIA_TYPES = Object.freeze({
  csv: 'text/csv',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
});
const CASE_FILE_EXTENSIONS = Object.freeze({ csv: 'csv', xlsx: 'xlsx' });

function evaluationDraftPath(applicationID) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationID)) + '/evaluations';
}

function evaluationDraftRevisionPath(applicationID) {
  return '/api/v1/applications/' + encodeURIComponent(String(applicationID)) + '/draft';
}

function evaluationPath(evaluationID) {
  return '/api/v1/evaluations/' + encodeURIComponent(String(evaluationID));
}

function evaluationReportPath(reportID) {
  return '/api/v1/evaluation-reports/' + encodeURIComponent(String(reportID));
}

function evaluationCasesPath(reportID) {
  return evaluationReportPath(reportID) + '/cases';
}

function caseArtifactPath(artifactID) {
  return '/api/v1/artifacts/' + encodeURIComponent(String(artifactID));
}

function caseArtifactDownloadPath(artifactID) {
  return caseArtifactPath(artifactID) + '/download';
}

function artifactUploadBody(sizeBytes) {
  const size = Number(sizeBytes);
  if (!Number.isInteger(size) || size < 1) {
    return { ok: false, error: '数据集文件必须至少 1 字节' };
  }
  if (size > 4 * 1024 * 1024) {
    return { ok: false, error: '评估数据集超过 4 MiB 上限（服务端会拒绝）' };
  }
  return {
    ok: true,
    body: {
      media_type: EVALUATION_DATASET_MEDIA_TYPE,
      size_bytes: size,
      purpose: 'evaluation_dataset',
    },
  };
}

/** The media type of one caller fixture from its extension (csv/xlsx only). */
function caseInputMediaType(fileName) {
  const name = String(fileName === undefined || fileName === null ? '' : fileName);
  const dot = name.lastIndexOf('.');
  if (dot < 0) {
    return { ok: false, error: '案例输入文件必须带 .csv 或 .xlsx 扩展名' };
  }
  const extension = name.slice(dot + 1).toLowerCase();
  const mediaType = CASE_INPUT_MEDIA_TYPES[extension];
  if (!mediaType) {
    return { ok: false, error: '案例输入只接受 .csv 或 .xlsx' };
  }
  return { ok: true, mediaType: mediaType };
}

function caseInputUploadBody(file) {
  if (!file || typeof file.size !== 'number' || file.size < 1) {
    return { ok: false, error: '请选择一个案例输入文件' };
  }
  const media = caseInputMediaType(file.name);
  if (!media.ok) {
    return media;
  }
  return {
    ok: true,
    body: {
      media_type: media.mediaType,
      size_bytes: file.size,
      purpose: 'task_input',
    },
  };
}

/** evaluateDraft's contract body is exactly these two members. */
function evaluateDraftBody(draftRevision, datasetArtifactID) {
  const revision = Number(draftRevision);
  const dataset = String(datasetArtifactID || '').trim();
  if (!Number.isInteger(revision) || revision < 1) {
    return { ok: false, error: '需要当前草稿 revision（整数，>=1）' };
  }
  if (!dataset) {
    return { ok: false, error: '请先上传数据集并等待封存完成' };
  }
  return { ok: true, body: { draft_revision: revision, dataset_artifact_id: dataset } };
}

function newEvaluationIdempotencyKey() {
  if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return 'evaluation-' + Date.now() + '-' + Math.random().toString(16).slice(2);
}

function evaluationStateLabel(state) {
  switch (state) {
    case 'queued':
      return '排队中';
    case 'running':
      return '运行中';
    case 'succeeded':
      return '已结束（报告已封存）';
    case 'failed':
      return '基础设施失败（无报告）';
    case 'cancelled':
      return '已取消';
    default:
      return '未知状态';
  }
}

function shouldPollEvaluation(state) {
  return state === 'queued' || state === 'running';
}

function caseVerdictLabel(verdict) {
  switch (verdict) {
    case 'passed':
      return '通过';
    case 'failed':
      return '失败';
    case 'not_run':
      return '未运行';
    case 'blocked':
      return '阻塞';
    default:
      return '未知判决';
  }
}

function assertionText(outcome) {
  const label = String(outcome.kind || '') === 'failure_reason'
    ? '失败原因'
    : String(outcome.kind || '');
  const target = outcome.pointer
    ? outcome.pointer
    : outcome.capability
      ? outcome.capability
      : '';
  const expected = String(outcome.expected === undefined ? '' : outcome.expected);
  const actual = String(outcome.actual === undefined ? '' : outcome.actual);
  return {
    label: label,
    target: target,
    expected: expected,
    actual: actual,
    passed: outcome.passed === true,
    detail: String(outcome.detail || ''),
  };
}

/**
 * One rendered case row: the expectation, the actual value/state, the failure
 * reason and the downloadable actual attachments (ids only; the page exchanges
 * them for a short-lived link on click, never storing a URL).
 */
function describeCase(caseResult) {
  const assertions = Array.isArray(caseResult.assertions) ? caseResult.assertions : [];
  const described = assertions.map(assertionText);
  const facts = caseResult.facts || {};
  const reasonParts = [];
  if (caseResult.reason_code) {
    reasonParts.push(String(caseResult.reason_code));
  }
  if (facts.run_error_code) {
    reasonParts.push('run_error_code=' + String(facts.run_error_code));
  }
  if (caseResult.detail) {
    reasonParts.push(String(caseResult.detail));
  }
  const attachments = (Array.isArray(caseResult.attachments) ? caseResult.attachments : [])
    .map((attachment) => ({
      artifactId: String(attachment.artifact_id || ''),
      label: String(attachment.label || 'attachment'),
      mediaType: String(attachment.media_type || ''),
      sizeBytes: Number(attachment.size_bytes || 0),
      sha256: String(attachment.sha256 || ''),
      downloadPath: caseArtifactDownloadPath(attachment.artifact_id),
    }))
    .filter((attachment) => attachment.artifactId);
  return {
    caseId: String(caseResult.case_id || caseResult.id || ''),
    verdict: String(caseResult.verdict || ''),
    verdictLabel: caseVerdictLabel(caseResult.verdict),
    reasons: reasonParts,
    expectation: described.map((item) =>
      item.label + (item.target ? ' ' + item.target : '') + ' = ' + item.expected + '（期望）'),
    actual: described.map((item) =>
      item.label + (item.target ? ' ' + item.target : '') + ' = ' + item.actual
        + (item.passed ? '（通过）' : '（不符）')),
    details: described.filter((item) => item.detail).map((item) => item.detail),
    attachments: attachments,
    runId: String(facts.run_id || ''),
  };
}

function describeCases(payload) {
  const cases = Array.isArray(payload && payload.cases) ? payload.cases : [];
  return {
    reportId: String((payload && payload.report_id) || ''),
    status: String((payload && payload.status) || ''),
    cases: cases.map(describeCase),
  };
}

/** Counts only: no percentage is shown unless every case is accounted for. */
function describeCaseSummary(payload) {
  const view = describeCases(payload);
  const summary = { total: view.cases.length, passed: 0, failed: 0, notRun: 0, blocked: 0 };
  view.cases.forEach((item) => {
    switch (item.verdict) {
      case 'passed':
        summary.passed += 1;
        break;
      case 'failed':
        summary.failed += 1;
        break;
      case 'blocked':
        summary.blocked += 1;
        break;
      default:
        summary.notRun += 1;
        break;
    }
  });
  summary.text = '共 ' + summary.total + ' 个案例：通过 ' + summary.passed + '，失败 '
    + summary.failed + '，未运行 ' + summary.notRun + '，阻塞 ' + summary.blocked;
  return summary;
}

function describeFailure(error) {
  if (!error) {
    return '未知错误';
  }
  const code = error.code || error.status || 'error';
  const message = error.message || String(error);
  return '[' + code + '] ' + message;
}
