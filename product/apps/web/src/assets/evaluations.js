/*
 * F13A/T065 evaluation progress page helpers.
 *
 * The page is a real client of two read paths:
 *
 *   GET /api/v1/evaluations/{id}/progress    -> non-contract progress (ADR 0016)
 *   GET /api/v1/evaluation-reports/{id}      -> contract Report (four fields)
 *
 * Everything below is pure so the Node page test executes it. The rules the
 * page must express:
 *
 *   - a refresh is safe: the evaluation id is in the URL path, and the page polls
 *     the persisted counters while the evaluation is queued/running;
 *   - no percentage is ever derived from `state`; the numbers are the runner's
 *     persisted per-case decisions;
 *   - a report `failed` while the task `succeeded` is a *business* verdict, not
 *     a platform failure;
 *   - a failed task with no report is an *infrastructure* failure and its
 *     report id is explicitly empty;
 *   - `not_run` is a report status, never a job state;
 *   - the immutable report is rendered from its own four contract fields, so
 *     re-opening it shows the same content.
 */

function evaluationProgressPath(evaluationID) {
  return '/api/v1/evaluations/' + encodeURIComponent(String(evaluationID || '')) + '/progress';
}

function evaluationReportDetailPath(reportID) {
  return '/api/v1/evaluation-reports/' + encodeURIComponent(String(reportID || ''));
}

/** The shareable page URL: the id is in the path, so a refresh is the same view. */
function evaluationProgressPagePath(evaluationID) {
  return '/evaluations/' + encodeURIComponent(String(evaluationID || ''));
}

/** The evaluation id of `/evaluations/{id}`; '' when the path is not that page. */
function evaluationIDFromPathname(pathname) {
  const path = String(pathname || '');
  const prefix = '/evaluations/';
  if (path.indexOf(prefix) !== 0) {
    return '';
  }
  const rest = path.slice(prefix.length);
  if (!rest || rest.indexOf('/') >= 0) {
    return '';
  }
  try {
    return decodeURIComponent(rest);
  } catch (error) {
    return '';
  }
}

/** One honest sentence per evaluation state. No percentage lives here. */
function describeEvaluationState(state) {
  switch (String(state || '')) {
    case 'queued':
      return '排队中：评测任务已提交，尚未被领取';
    case 'running':
      return '运行中：任务已被领取，正在逐例采集';
    case 'succeeded':
      return '任务已成功完成（报告可能通过或不通过）';
    case 'failed':
      return '任务失败：没有产出报告';
    case 'cancelled':
      return '任务已取消：没有产出报告';
    default:
      return '未知状态：' + String(state || '');
  }
}

/** The persisted counters, verbatim. `completed` includes not_run/blocked cases. */
function describeCaseCounts(progress) {
  const value = progress || {};
  const total = Number(value.total_cases || 0);
  const completed = Number(value.completed_cases || 0);
  const failed = Number(value.failed_cases || 0);
  return '已处理 ' + completed + '/' + total + ' 例，其中 ' + failed + ' 例失败';
}

/** The report id: never omit the empty state. */
function describeReportID(progress) {
  const reportID = progress && progress.report_id ? String(progress.report_id) : '';
  return reportID ? reportID : '报告尚未生成（report_id 为空）';
}

/** The report status as a status; the task state is never reused for it. */
function describeReportStatus(progress) {
  const status = progress && progress.report_status ? String(progress.report_status) : '';
  return status ? status : '无';
}

/**
 * Which of the ticket's stories the progress tells.
 *
 * `business`      report failed/not_run while the task succeeded (a verdict)
 * `infrastructure` the task failed and no report exists (no verdict was made)
 * `cancelled`     the evaluation was cancelled (neither of the two failures)
 * `passed`        the report passed
 * `pending`       still queued/running (or no decisive outcome yet)
 */
function failureKind(progress) {
  const value = progress || {};
  const classification = String(value.failure_class || '');
  const reportStatus = String(value.report_status || '');
  if (classification === 'business') {
    return reportStatus === 'not_run' ? 'business_not_run' : 'business';
  }
  if (classification === 'infrastructure') {
    return 'infrastructure';
  }
  if (String(value.state || '') === 'cancelled') {
    return 'cancelled';
  }
  if (reportStatus === 'passed') {
    return 'passed';
  }
  return 'pending';
}

/**
 * The plain-language distinction the ticket exists for. The two failure stories
 * are never merged into "something failed": a report failed and a job failed are
 * different facts about the application and the platform.
 */
function describeFailureClass(progress) {
  const value = progress || {};
  const errorCode = value.error_code ? String(value.error_code) : '';
  switch (failureKind(value)) {
    case 'business':
      return {
        kind: 'business',
        sentence:
          '业务结论：密封报告为 failed，评测任务本身 succeeded。这是对被评测应用的业务判定，' +
          '不是平台故障；报告不可变，重新打开显示相同内容（report_id ' +
          String(value.report_id || '') + '）。',
      };
    case 'business_not_run':
      return {
        kind: 'business_not_run',
        sentence:
          '业务结论：报告状态为 not_run —— 没有任何一例产出可判定结论。not_run 是报告状态，' +
          '不是任务状态；评测任务本身 succeeded，所以这不是任务失败，也不是设施失败。',
      };
    case 'infrastructure':
      return {
        kind: 'infrastructure',
        sentence:
          '设施失败：评测任务 failed，没有生成报告（report_id 为空）。平台在执行阶段失败，' +
          '因此没有应用判定，也没有可重读的报告' +
          (errorCode ? '；原因码 ' + errorCode : '') + '。',
      };
    case 'cancelled':
      return {
        kind: 'cancelled',
        sentence: '任务已取消：没有生成报告（report_id 为空）。取消不是业务判定，也不是设施失败。',
      };
    case 'passed':
      return {
        kind: 'passed',
        sentence: '业务结论：报告为 passed。评测任务本身 succeeded，报告不可变。',
      };
    default:
      return {
        kind: 'pending',
        sentence: '评测尚未结束：当前状态 ' + String(value.state || '') + '，报告 id 仍为空。',
      };
  }
}

/** Everything the page renders, derived from one progress payload. */
function describeProgress(progress) {
  const value = progress || {};
  const failure = describeFailureClass(value);
  return {
    stateText: describeEvaluationState(value.state),
    countsText: describeCaseCounts(value),
    failureKind: failure.kind,
    failureSentence: failure.sentence,
    reportIDText: describeReportID(value),
    reportStatusText: describeReportStatus(value),
  };
}

/** Poll while the evaluation can still change; a terminal state stops the timer. */
function shouldPollEvaluationProgress(progress) {
  const state = String((progress || {}).state || '');
  return state === 'queued' || state === 'running';
}

/** Refresh cadence while running; 0 means "do not schedule another read". */
function evaluationPollDelayMS(progress) {
  return shouldPollEvaluationProgress(progress) ? 2000 : 0;
}

/** The immutable report's four contract fields as one stable sentence. */
function describeReport(report) {
  if (!report) {
    return '';
  }
  return (
    '报告 ' + String(report.id || '') +
    '：status=' + String(report.status || '') +
    '，manifest_sha256=' + String(report.manifest_sha256 || '') +
    '，result_artifact_id=' + String(report.result_artifact_id || '')
  );
}

/**
 * A deterministic snapshot of the report projection. Comparing two snapshots is
 * how the page proves re-opening the same report shows identical content.
 */
function reportSnapshotText(report) {
  if (!report) {
    return '';
  }
  return JSON.stringify({
    id: String(report.id || ''),
    status: String(report.status || ''),
    manifest_sha256: String(report.manifest_sha256 || ''),
    result_artifact_id: String(report.result_artifact_id || ''),
  });
}

/** True when a re-read report differs from the first read (it never should). */
function reportHasChanged(first, second) {
  return reportSnapshotText(first) !== reportSnapshotText(second);
}
