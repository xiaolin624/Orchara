"""T065 acceptance: async evaluation progress and business-vs-infrastructure failure.

The ticket's user path is: a creator refreshes an evaluation's progress, gets an
immutable report, and can tell a *business* failure (a report says the evaluated
application failed while the evaluation job succeeded) from an *infrastructure*
failure (the job failed, so no report exists and ``report_id`` is empty).

Every assertion here reads a real interface: the compiled ``cmd/api`` over real
HTTP with session cookies or scoped API keys, the real two-pool
``cmd/maintenance run`` process, the real PostgreSQL rows, the real sealed report
bytes on disk. Nothing mocks the pools, the progress read or the completion
guard.
"""

from __future__ import annotations

from datetime import datetime

from support import t065
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _assert_rfc3339(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, value


def test_a_queued_evaluation_reports_only_honest_zero_counters(t052_stack) -> None:
    """Before any case completes, the read reports zero done and no report.

    It never turns ``state=queued`` into a percentage: the counters come from
    persisted case decisions, and the report id stays null until a report really
    exists. Authorization is owner-or-admin, exactly like the contract reads.
    """
    owner = t065.minimal_owner(t052_stack, login="t065-queued-owner")
    other = t065.other_creator(t052_stack, login="t065-queued-other")
    admin = t065.admin_session(t052_stack)
    evaluation_id = t065.submit_evaluation_for(t052_stack, owner, [t065.quiet_case()])

    response = t065.get_progress(owner.creator, evaluation_id)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == t065.PROGRESS_FIELDS, body
    assert body["evaluation_id"] == evaluation_id
    assert body["state"] == "queued"
    assert body["total_cases"] == 1
    assert body["completed_cases"] == 0
    assert body["failed_cases"] == 0
    assert body["report_id"] is None
    assert body["report_status"] is None
    assert body["error_code"] is None
    assert body["failure_class"] is None
    _assert_rfc3339(body["created_at"])
    assert body["finished_at"] is None

    # The database agrees: no report row and the coarse counters are still zero.
    row = t065.progress_row(t052_stack.dbname, evaluation_id)
    assert row["state"] == "queued", row
    assert row["report_id"] is None, row
    assert int(row["progress_total"]) == 1, row
    assert int(row["progress_done"]) == 0, row
    assert int(row["progress_failed"]) == 0, row

    # Another creator gets exactly the missing-resource answer (no probing).
    refused = t065.get_progress(other.creator, evaluation_id)
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found", refused.text
    missing = t065.get_progress(other.creator, t065.new_uuid())
    assert missing.status == 404, missing.text
    assert missing.json["error"]["message"] == refused.json["error"]["message"], missing.text
    malformed = t065.get_progress(owner.creator, "not-a-uuid")
    assert malformed.status == 404, malformed.text

    # The administrator may read it; an unauthenticated client may not.
    assert t065.get_progress(admin, evaluation_id).status == 200
    unauthenticated = t052_stack.client().get(t065.progress_path(evaluation_id))
    assert unauthenticated.status == 401, unauthenticated.text

    # API-key scopes are checked before the ACL: a write-only key is refused as a
    # credential; a read-scoped key for another principal learns nothing (404).
    write_key = t065.service_client(t052_stack, scopes=("application:write",))
    refused_key = write_key.get(t065.progress_path(evaluation_id))
    assert refused_key.status == 403, refused_key.text
    assert refused_key.json["error"]["code"] == "insufficient_scope", refused_key.text
    read_key = t065.service_client(t052_stack, scopes=("application:read",))
    foreign_key = read_key.get(t065.progress_path(evaluation_id))
    assert foreign_key.status == 404, foreign_key.text


def test_the_runner_persists_each_case_outcome_while_it_collects(t052_stack, tmp_path) -> None:
    """A case the runner decides without a run still moves the progress counters.

    An unsupported target is refused before any case run exists, so the coarse
    `progress_done` counter (which only sees run states) cannot move on its own.
    The runner's per-case progress report must persist the verdict. The sealed
    report is `not_run` while the background job and the evaluation both
    `succeeded`: `not_run` is a report status, never a job state.
    """
    owner = t065.minimal_owner(t052_stack, login="t065-blocked-owner")
    evaluation_id = t065.submit_evaluation_for(t052_stack, owner, [t065.blocked_case()])

    log = tmp_path / "pools.log"
    process, handle = t065.start_pools(t052_stack, log_path=log, case_timeout="10s")
    try:
        evaluation = t065.wait_for_evaluation(
            t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=120
        )
    finally:
        t065.stop_process(process, handle, log)

    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["error_code"] is None, evaluation
    assert evaluation["report_id"] is not None, evaluation
    report = t065.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report

    # The job really succeeded: `not_run` was never a job state.
    job = t065.get_background_job(owner.creator, evaluation["background_job_id"])
    assert job.status == 200, job.text
    assert job.json["state"] == "succeeded", job.json
    assert job.json["error_code"] is None, job.json

    # The case row carries the runner's verdict even though no run exists.
    rows = t065.case_progress_rows(t052_stack.dbname, evaluation_id)
    assert len(rows) == 1, rows
    assert rows[0]["case_id"] == "unsupported-target", rows[0]
    assert rows[0]["ordinal"] == 0, rows[0]
    assert rows[0]["run_id"] is None, rows[0]
    assert rows[0]["state"] == "blocked", rows[0]
    assert rows[0]["verdict"] == "blocked", rows[0]
    assert rows[0]["reason_code"] == "unsupported_target", rows[0]
    row = t065.progress_row(t052_stack.dbname, evaluation_id)
    assert int(row["progress_total"]) == 1, row
    assert int(row["progress_done"]) == 1, row
    assert int(row["progress_failed"]) == 0, row

    progress = t065.get_progress(owner.creator, evaluation_id).json
    assert set(progress) == t065.PROGRESS_FIELDS, progress
    assert progress["state"] == "succeeded", progress
    assert progress["total_cases"] == 1, progress
    assert progress["completed_cases"] == 1, progress
    assert progress["failed_cases"] == 0, progress
    assert progress["report_id"] == evaluation["report_id"], progress
    assert progress["report_status"] == "not_run", progress
    assert progress["error_code"] is None, progress
    # A report exists, so this is a business result even though nothing passed.
    assert progress["failure_class"] == "business", progress
    assert progress["finished_at"] is not None, progress


def test_a_failed_report_is_a_business_result_of_a_succeeded_job(
    t052_stack, tmp_path
) -> None:
    """The ticket's core distinction: report `failed` while job `succeeded`.

    A cancelled case's run is a real owner transition; the runner refuses to
    pass a cancelled case, so the sealed report is `failed`. The evaluation and
    its background job both `succeeded`: the report is a business verdict about
    the application, not a platform failure. The report bytes and sha256 never
    change after creation, and re-opening the report returns the same fields.
    """
    owner = t065.minimal_owner(t052_stack, login="t065-business-owner")
    evaluation_id = t065.submit_evaluation_for(t052_stack, owner, [t065.cancelled_case()])

    log = tmp_path / "pools.log"
    process, handle = t065.start_pools(t052_stack, log_path=log, case_timeout="30s")
    try:
        evaluation = t065.wait_for_evaluation(
            t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=120
        )
    finally:
        t065.stop_process(process, handle, log)

    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["error_code"] is None, evaluation
    assert evaluation["report_id"] is not None, evaluation
    report = t065.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "failed", report

    # AC1: the report failed, the job succeeded. Both reads are the contract's.
    job = t065.get_background_job(owner.creator, evaluation["background_job_id"])
    assert job.status == 200, job.text
    assert job.json["state"] == "succeeded", job.json
    assert job.json["error_code"] is None, job.json
    contract_evaluation = t065.get_evaluation(owner.creator, evaluation_id)
    assert contract_evaluation.json["state"] == "succeeded", contract_evaluation.text
    assert contract_evaluation.json["report_id"] == report["id"], contract_evaluation.text

    progress = t065.get_progress(owner.creator, evaluation_id).json
    assert set(progress) == t065.PROGRESS_FIELDS, progress
    assert progress["state"] == "succeeded", progress
    assert progress["report_id"] == report["id"], progress
    assert progress["report_status"] == "failed", progress
    assert progress["failure_class"] == "business", progress
    assert progress["total_cases"] == 1, progress
    assert progress["completed_cases"] == 1, progress
    assert progress["failed_cases"] == 1, progress
    # A business failure has no platform error code on the evaluation row.
    assert progress["error_code"] is None, progress

    # AC5: the sealed report is immutable. The bytes and their sha256 are the
    # same before and after re-reading it, and the row hash still matches.
    first_bytes, first_sha = t065.sealed_sha256(t052_stack, report["result_artifact_id"])
    first_report = t065.get_report(owner.creator, report["id"])
    assert first_report.status == 200, first_report.text
    first_row = t065.report_row(t052_stack.dbname, report["id"])
    second_bytes, second_sha = t065.sealed_sha256(t052_stack, report["result_artifact_id"])
    second_report = t065.get_report(owner.creator, report["id"])
    second_row = t065.report_row(t052_stack.dbname, report["id"])
    assert first_bytes == second_bytes, "the sealed report bytes changed after creation"
    assert first_sha == second_sha, "the sealed report sha256 changed after creation"
    assert first_sha == t065.artifact_row(
        t052_stack.dbname, report["result_artifact_id"]
    )["sha256"], "the recorded artifact hash disagrees with the sealed bytes"
    assert first_report.json == second_report.json, "re-opening the report changed its fields"
    assert first_row == second_row, "the immutable report row changed after creation"
    # The page's own re-open check: the same payload renders the same snapshot.
    assert first_row["manifest_sha256"] == second_row["manifest_sha256"]
    assert first_row["result_artifact_id"] == second_row["result_artifact_id"]


def test_an_infrastructure_failure_leaves_report_id_empty(t052_stack, tmp_path) -> None:
    """An infrastructure failure: job failed, evaluation failed, no report.

    The dataset's metadata is sound but its sealed bytes are really gone, so the
    trusted runner cannot read the input. The handler retries the transient
    storage failure to the ceiling and the job fails terminally; the evaluation
    fails with the same code and no report row is written. The progress read
    must say so with a null report id and the infrastructure class.
    """
    owner = t065.minimal_owner(t052_stack, login="t065-infra-owner")
    evaluation_id = t065.submit_evaluation_for(t052_stack, owner, [t065.quiet_case()])
    evaluation = t065.evaluation_row(t052_stack.dbname, evaluation_id)
    removed = t065.remove_sealed_object(t052_stack, evaluation["dataset_artifact_id"])
    assert removed.startswith("sealed/"), removed

    log = tmp_path / "pools.log"
    process, handle = t065.start_pools(t052_stack, log_path=log, case_timeout="30s")
    try:
        evaluation = t065.wait_for_evaluation(
            t052_stack.dbname, evaluation_id, {"failed", "cancelled"}, timeout=120
        )
    finally:
        t065.stop_process(process, handle, log)

    # AC2: no report was generated, so report_id is empty everywhere.
    assert evaluation["state"] == "failed", evaluation
    assert evaluation["report_id"] is None, evaluation
    assert evaluation["error_code"] == "transient_storage_io", evaluation
    assert t065.report_count(t052_stack.dbname) == 0

    row = t065.progress_row(t052_stack.dbname, evaluation_id)
    assert row["report_id"] is None, row
    assert row["finished_at"] is not None, row
    assert int(row["progress_done"]) == 0, row
    assert int(row["progress_failed"]) == 0, row
    job = t065.get_background_job(owner.creator, evaluation["background_job_id"])
    assert job.status == 200, job.text
    assert job.json["state"] == "failed", job.json
    assert job.json["error_code"] == "transient_storage_io", job.json

    contract_evaluation = t065.get_evaluation(owner.creator, evaluation_id)
    assert contract_evaluation.json["state"] == "failed", contract_evaluation.text
    assert contract_evaluation.json["report_id"] is None, contract_evaluation.text

    progress = t065.get_progress(owner.creator, evaluation_id).json
    assert set(progress) == t065.PROGRESS_FIELDS, progress
    assert progress["state"] == "failed", progress
    assert progress["report_id"] is None, progress
    assert progress["report_status"] is None, progress
    assert progress["error_code"] == "transient_storage_io", progress
    assert progress["failure_class"] == "infrastructure", progress
    assert progress["total_cases"] == 1, progress
    # No case decision was ever produced: the platform failed before a verdict.
    assert progress["completed_cases"] == 0, progress
    assert progress["failed_cases"] == 0, progress
    # Re-reading the progress is stable (nothing changes after termination).
    again = t065.get_progress(owner.creator, evaluation_id).json
    assert again == progress, "the terminal progress read changed between reads"


def test_work_pool_congestion_does_not_block_report_sealing(t052_stack, tmp_path) -> None:
    """A saturated work pool must not stop the report from being sealed.

    The real maintenance process runs the dual pools with exactly one work slot
    and one system_io slot. A real evaluation handler occupies the only work slot
    while it waits for its case run (no executor), and a second submitted
    evaluation is still queued; a real artifact_finalize job enqueued at that
    moment must still complete in the system_io pool. The evaluation's own sealed
    report must then become `ready` and the evaluation succeed, which is only
    possible because the finalize pool is independent.
    """
    owner = t065.minimal_owner(t052_stack, login="t065-congestion-owner")
    first_id = t065.submit_evaluation_for(t052_stack, owner, [t065.quiet_case("slow-case")])
    second_id = t065.submit_evaluation_for(t052_stack, owner, [t065.quiet_case("queued-case")])
    job_of = {
        row["id"]: row["background_job_id"]
        for row in (
            t065.evaluation_row(t052_stack.dbname, first_id),
            t065.evaluation_row(t052_stack.dbname, second_id),
        )
    }

    log = tmp_path / "pools.log"
    process, handle = t065.start_pools(
        t052_stack,
        log_path=log,
        work_slots=1,
        system_io_slots=1,
        case_timeout="15s",
    )
    try:
        # Exactly one of the two evaluations can occupy the single work slot;
        # the other must stay queued. That is the saturation the ticket names.
        t065.wait_until(
            lambda: any(
                t065.job_state(t052_stack.dbname, job_id) == "leased"
                for job_id in job_of.values()
            ),
            timeout=60,
            what="one evaluation to occupy the only work slot",
        )
        leased = {
            evaluation_id
            for evaluation_id in (first_id, second_id)
            if t065.job_state(t052_stack.dbname, job_of[evaluation_id]) == "leased"
        }
        assert len(leased) == 1, (leased, job_of)
        running_id = leased.pop()
        queued_id = first_id if running_id == second_id else second_id
        assert t065.job_state(t052_stack.dbname, job_of[running_id]) == "leased"
        assert t065.job_state(t052_stack.dbname, job_of[queued_id]) == "ready"
        assert t065.evaluation_row(t052_stack.dbname, queued_id)["state"] == "queued"

        # A real artifact_finalize job enqueued while the work pool is saturated
        # must complete in the dedicated system_io pool.
        artifact_id, finalize_job = t065.enqueue_standalone_finalize(
            t052_stack, owner.creator.principal_id
        )
        t065.wait_until(
            lambda: t065.job_state(t052_stack.dbname, finalize_job) == "succeeded",
            timeout=60,
            what="the standalone finalize to complete while the work pool is saturated",
        )
        assert t065.artifact_row(t052_stack.dbname, artifact_id)["state"] == "ready"
        # Ordering evidence: the finalize is terminal while the work slot is
        # still leased by the evaluation handler and the second job is queued.
        assert t065.job_state(t052_stack.dbname, job_of[running_id]) == "leased"
        assert t065.job_state(t052_stack.dbname, job_of[queued_id]) == "ready"

        evaluation = t065.wait_for_evaluation(
            t052_stack.dbname, running_id, {"succeeded", "failed"}, timeout=180
        )
    finally:
        log_text = t065.stop_process(process, handle, log)

    # The evaluation completed and its own report was sealed in the same
    # congested process.
    assert evaluation["state"] == "succeeded", evaluation
    assert evaluation["report_id"] is not None, evaluation
    report = t065.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report
    assert t065.artifact_row(t052_stack.dbname, report["result_artifact_id"])["state"] == "ready"

    progress = t065.get_progress(owner.creator, running_id).json
    assert progress["state"] == "succeeded", progress
    assert progress["completed_cases"] == 1, progress
    assert progress["report_status"] == "not_run", progress
    assert progress["failure_class"] == "business", progress

    # The log shows the real pool shape and that both pools claimed work: the
    # evaluation in `work`, the finalize in `system_io`.
    reports = t065.parse_json_lines(log_text)
    job_reports = [report for report in reports if "job_id" in report]
    completed = [report for report in job_reports if report.get("result") == "succeeded"]
    kinds = [(report.get("kind"), report.get("pool")) for report in completed]
    assert ("application_evaluate", "work") in kinds, kinds
    assert ("artifact_finalize", "system_io") in kinds, kinds
    assert "run: pools=" in log_text and "system_io" in log_text and "work" in log_text, (
        log_text[-2000:]
    )


EVALUATIONS_HARNESS = r"""
'use strict';

const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const webDir = __WEB_DIR__;
const appSource = fs.readFileSync(webDir + '/assets/app.js', 'utf8');
const pageSource = fs.readFileSync(webDir + '/assets/evaluations.js', 'utf8');

const context = { console };
vm.createContext(context);
vm.runInContext(appSource, context, { filename: 'app.js' });
vm.runInContext(pageSource, context, { filename: 'evaluations.js' });

// Paths are built from ids with encoding, never from caller-supplied URLs.
const id = '11111111-1111-4111-8111-111111111111';
assert.strictEqual(context.evaluationProgressPath(id),
  '/api/v1/evaluations/' + id + '/progress');
assert.strictEqual(context.evaluationReportDetailPath(id),
  '/api/v1/evaluation-reports/' + id);
assert.strictEqual(context.evaluationIDFromPathname('/evaluations/' + id), id);
assert.strictEqual(context.evaluationIDFromPathname('/evaluations/a%2Fb'), 'a/b');
assert.strictEqual(context.evaluationIDFromPathname('/apps/x/debug'), '');

// 1. A queued evaluation: honest zero counters, no report, no invented percent.
const queued = {
  evaluation_id: id, state: 'queued', report_status: null, report_id: null,
  total_cases: 2, completed_cases: 0, failed_cases: 0,
  error_code: null, failure_class: null,
  created_at: '2026-11-04T00:00:00Z', finished_at: null,
};
assert.strictEqual(context.shouldPollEvaluationProgress(queued), true);
assert.ok(context.evaluationPollDelayMS(queued) > 0);
const queuedView = context.describeProgress(queued);
assert.ok(queuedView.stateText.includes('排队'), queuedView.stateText);
assert.ok(queuedView.countsText.includes('0/2'), queuedView.countsText);
assert.ok(queuedView.countsText.includes('0 例失败'), queuedView.countsText);
assert.strictEqual(queuedView.failureKind, 'pending');
assert.ok(queuedView.reportIDText.includes('为空'), queuedView.reportIDText);

// 2. Business failure: report failed while the task succeeded. The sentence must
// say it is a business verdict, not an infrastructure failure.
const business = {
  ...queued, state: 'succeeded', report_status: 'failed',
  report_id: id, completed_cases: 2, failed_cases: 1, failure_class: 'business',
  finished_at: '2026-11-04T00:01:00Z',
};
const businessView = context.describeProgress(business);
assert.strictEqual(businessView.failureKind, 'business');
assert.ok(businessView.failureSentence.includes('业务'), businessView.failureSentence);
assert.ok(businessView.failureSentence.includes('succeeded'), businessView.failureSentence);
assert.ok(businessView.failureSentence.includes('不是平台'), businessView.failureSentence);
assert.strictEqual(context.shouldPollEvaluationProgress(business), false);

// 3. `not_run` is a report status, never a job state.
const notRun = {
  ...business, report_status: 'not_run', failed_cases: 0,
};
const notRunView = context.describeProgress(notRun);
assert.strictEqual(notRunView.failureKind, 'business_not_run');
assert.ok(notRunView.failureSentence.includes('报告状态'), notRunView.failureSentence);
assert.ok(notRunView.failureSentence.includes('不是任务状态'), notRunView.failureSentence);
assert.ok(notRunView.failureSentence.includes('not_run'), notRunView.failureSentence);

// 4. Infrastructure failure: job failed, no report, empty report id.
const infrastructure = {
  ...queued, state: 'failed', report_status: null, report_id: null,
  error_code: 'transient_storage_io', failure_class: 'infrastructure',
  completed_cases: 0, failed_cases: 0,
  finished_at: '2026-11-04T00:01:00Z',
};
const infrastructureView = context.describeProgress(infrastructure);
assert.strictEqual(infrastructureView.failureKind, 'infrastructure');
assert.ok(infrastructureView.failureSentence.includes('任务'), infrastructureView.failureSentence);
assert.ok(infrastructureView.failureSentence.includes('没有生成报告'),
  infrastructureView.failureSentence);
assert.ok(infrastructureView.failureSentence.includes('report_id 为空'),
  infrastructureView.failureSentence);
assert.ok(infrastructureView.failureSentence.includes('transient_storage_io'),
  infrastructureView.failureSentence);
assert.strictEqual(infrastructureView.reportStatusText, '无');

// 5. Cancelled is its own story, not a business verdict and not infra.
const cancelled = { ...queued, state: 'cancelled', finished_at: '2026-11-04T00:01:00Z' };
assert.strictEqual(context.describeProgress(cancelled).failureKind, 'cancelled');

// No sentence may invent a percentage or a progress bar.
for (const view of [queuedView, businessView, notRunView, infrastructureView,
  context.describeProgress(cancelled)]) {
  const text = view.stateText + view.countsText + view.failureSentence + view.reportIDText;
  assert.ok(!/%|进度条|预计完成/.test(text), text);
}

// 6. The immutable report: the same payload renders the same snapshot; a changed
// status is a different snapshot. Re-opening must not rewrite the display.
const report = {
  id: id, status: 'failed',
  manifest_sha256: 'a'.repeat(64),
  result_artifact_id: '22222222-2222-4222-8222-222222222222',
};
const snapshot = context.reportSnapshotText(report);
assert.strictEqual(context.reportSnapshotText({ ...report }), snapshot);
assert.strictEqual(context.reportHasChanged(report, { ...report }), false);
assert.ok(snapshot.includes('failed'), snapshot);
assert.ok(snapshot.includes('a'.repeat(64)), snapshot);
assert.strictEqual(context.reportHasChanged(report, { ...report, status: 'passed' }), true);
assert.strictEqual(context.reportSnapshotText(null), '');
assert.strictEqual(context.describeReport(null), '');

process.stdout.write('evaluations.js behaviour ok\n');
"""


def test_the_progress_page_and_its_logic_are_real(t052_stack, tmp_path) -> None:
    """The creator-facing section: refresh-safe poll, both failure stories, no report.

    The page is fetched from the running API; its pure logic is executed under
    Node (the same boundary every page test in this repository records: no
    browser DOM is driven). A refresh keeps the same URL because the evaluation
    id is in the path, and re-opening the report renders the same snapshot.
    """
    import json
    import re
    import shutil
    import subprocess

    from support import stack as st

    node = shutil.which("node")
    assert node is not None, "node must be on PATH to execute the page logic"

    page = t052_stack.server.http.get(f"/evaluations/{t065.new_uuid()}")
    assert page.status == 200, page.text
    assert "text/html" in page.header("Content-Type")
    assert page.header("Cache-Control") == "no-store"
    body = page.text
    for marker in (
        'id="evaluation-status"',
        'role="status"',
        'id="progress-panel"',
        'id="p-state"',
        'id="p-counts"',
        'id="p-total"',
        'id="p-completed"',
        'id="p-failed"',
        'id="p-report-id"',
        'id="p-report-status"',
        'id="p-failure-class"',
        'id="p-error-code"',
        'id="p-created-at"',
        'id="p-finished-at"',
        'id="failure-class-explanation"',
        'id="business-sentence"',
        'id="infrastructure-sentence"',
        'id="not-run-sentence"',
        'id="report-panel"',
        'id="report-empty"',
        'id="report-refresh"',
        'id="report-refresh-status"',
        "/api/v1/evaluations/{id}/progress",
        "/api/v1/evaluation-reports/{id}",
        "/assets/app.js",
        "/assets/evaluations.js",
    ):
        assert marker in body, f"evaluations.html is missing {marker!r}"
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "location.search" not in body

    asset = t052_stack.server.http.get("/assets/evaluations.js")
    assert asset.status == 200, asset.text
    assert "javascript" in asset.header("Content-Type")
    for marker in (
        "function evaluationProgressPath(",
        "function evaluationReportDetailPath(",
        "function evaluationIDFromPathname(",
        "function describeProgress(",
        "function describeFailureClass(",
        "function shouldPollEvaluationProgress(",
        "function reportSnapshotText(",
        "function reportHasChanged(",
        "/api/v1/evaluations/",
    ):
        assert marker in asset.text, f"evaluations.js is missing {marker!r}"

    # The evaluation entry point lives where a creator already works: the app's
    # private console links the shareable progress URL from an evaluation id.
    debug = t052_stack.server.http.get(f"/apps/{t065.new_uuid()}/debug")
    assert debug.status == 200, debug.text
    assert 'id="evaluation-progress-entry"' in debug.text, debug.text
    assert 'id="evaluation-id-input"' in debug.text, debug.text
    assert 'id="open-evaluation-progress"' in debug.text, debug.text
    assert "/evaluations/" in debug.text, debug.text

    # Every inline script and the asset parses with the real Node parser.
    inline = re.compile(r"<script(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL)
    for page_name in ("evaluations.html", "debug.html"):
        source = (st.WEB_DIR / page_name).read_text(encoding="utf-8")
        for index, block in enumerate(inline.findall(source)):
            script = tmp_path / f"{page_name}.{index}.js"
            script.write_text(block, encoding="utf-8")
            checked = subprocess.run(
                [node, "--check", str(script)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            assert checked.returncode == 0, (page_name, index, checked.stderr)
    checked = subprocess.run(
        [node, "--check", str(st.WEB_DIR / "assets" / "evaluations.js")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert checked.returncode == 0, checked.stderr

    harness = tmp_path / "evaluations-harness.js"
    harness.write_text(
        EVALUATIONS_HARNESS.replace("__WEB_DIR__", json.dumps(str(st.WEB_DIR))),
        encoding="utf-8",
    )
    completed = subprocess.run(
        [node, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    assert completed.returncode == 0, (
        "evaluations.js did not behave as the ticket requires:\n"
        f"--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    )
    assert "evaluations.js behaviour ok" in completed.stdout, completed.stdout
