"""T026 acceptance: artifact_finalize keeps progressing while the work pool waits.

The ticket exists for one regression: a single maintenance loop that runs both a
waiting evaluation and the finalize job that would unblock it can deadlock. T026
splits the process into a dedicated `system_io` pool (artifact_finalize, default
2 slots) and a `work` pool (everything else, default 1). These tests start the
real binary with both pools and observe real database transitions.

The first test saturates the work pool with two evaluations whose datasets are
still uploading, then enqueues a real finalize job: it must complete while both
evaluation jobs are still leased. Only then are the datasets completed; both
evaluations must reach their correct end state.

The second test proves `--once` is a true drain across pools: a work handler's
completeUpload creates the system_io job that finalizes its own report, and the
process must not exit before that job has run.
"""

from __future__ import annotations

from support import f00b, t024, t026
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)


def _enqueue_uploading_evaluation(stack, owner):
    candidate = t024.seed_candidate(stack.dbname, stack.object_dir, owner)
    dataset = t026.seed_dataset(stack, owner, state="uploading")
    payload = t026.enqueue_evaluation_ok(
        stack,
        kind="model",
        dataset_artifact_id=dataset.id,
        model_profile_version_id=candidate["id"],
    )
    return payload, dataset, candidate


def test_finalize_progresses_while_both_work_slots_wait(t026_stack, tmp_path) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    first_payload, first_dataset, _candidate = _enqueue_uploading_evaluation(t026_stack, owner)
    second_payload, second_dataset, _candidate = _enqueue_uploading_evaluation(t026_stack, owner)

    log_path = tmp_path / "pools.log"
    process, handle = t026.start_pools(
        t026_stack, work_slots=2, system_io_slots=1, log_path=log_path
    )
    try:
        # Both work slots are occupied by handlers waiting for their dataset.
        for payload in (first_payload, second_payload):
            t026.wait_for_leased(t026_stack.dbname, payload["background_job_id"], timeout=30)
        for payload in (first_payload, second_payload):
            row = t026.wait_for_evaluation(t026_stack.dbname, payload["id"], {"running"}, timeout=10)
            assert row["state"] == "running", row
            job = f00b.job_row(t026_stack.dbname, payload["background_job_id"])
            assert job["state"] == "leased", job

        # A real artifact_finalize job enqueued *after* the work pool is
        # saturated must still complete in the system_io pool.
        finalize_artifact = f00b.seed_artifact(
            t026_stack.dbname,
            t026_stack.object_dir,
            owner,
            content=b"finalize while evaluations wait\n",
        )
        finalize_job = f00b.enqueue_ok(
            t026_stack,
            kind="artifact_finalize",
            target_id=finalize_artifact.id,
            owner_id=owner,
            dedup_key="t026-saturation:" + finalize_artifact.id,
            expected_revision=1,
            expected_sha256=finalize_artifact.expected_sha256,
        )
        t026.wait_until(
            lambda: f00b.job_row(t026_stack.dbname, finalize_job["id"])["state"] == "succeeded",
            timeout=30,
            what="the finalize job to complete while both work slots wait",
        )
        assert f00b.artifact_row(t026_stack.dbname, finalize_artifact.id)["state"] == "ready"

        # Ordering evidence: the finalize job is terminal while both evaluations
        # are still leased/running (the waiting handlers are heartbeating).
        for payload in (first_payload, second_payload):
            assert f00b.job_row(t026_stack.dbname, payload["background_job_id"])["state"] == "leased"
            assert t026.evaluation_row(t026_stack.dbname, payload["id"])["state"] == "running"

        # Now complete both datasets through the real finalize path. The waiting
        # handlers see ready, run, upload their reports, and succeed.
        for dataset in (first_dataset, second_dataset):
            t026.make_dataset_ready(t026_stack, owner, dataset)

        for payload in (first_payload, second_payload):
            evaluation = t026.wait_for_evaluation(
                t026_stack.dbname, payload["id"], {"succeeded", "failed"}, timeout=60
            )
            assert evaluation["state"] == "succeeded", evaluation
            assert evaluation["error_code"] is None, evaluation
            assert evaluation["report_id"] is not None
            report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
            assert report["status"] == "passed", report
            job = f00b.job_row(t026_stack.dbname, payload["background_job_id"])
            assert job["state"] == "succeeded", job
            assert job["error_code"] is None
    finally:
        log_text = t026.stop_process(process, handle, log_path)

    reports = f00b.parse_json_lines(log_text)
    kinds = [report.get("kind") for report in f00b.job_reports(reports)]
    assert kinds.count("application_evaluate") == 2, reports
    assert kinds.count("artifact_finalize") >= 3, reports
    assert "run: pools=system_io" in log_text and "work" in log_text, log_text[-2000:]


def test_once_drains_both_pools_through_a_report_finalize(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(
        t026_stack,
        cases=[
            t026.dataset_case(case_id="case-1", input_value={"q": "alpha"}),
            t026.dataset_case(case_id="case-2", input_value={"q": "beta"}),
        ],
    )
    completed, reports = t026.run_pools_once(t026_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    summary = f00b.summary_of(reports)
    assert summary["status"] == "ok"
    # Two claims at least: the evaluation (work pool) and the report finalize
    # (system_io pool) that its completeUpload created. A process that exited on
    # the work pool's first empty claim would leave the report scanning and the
    # evaluation unfinished.
    assert summary["claimed"] >= 2, summary
    assert summary["succeeded"] >= 2, summary

    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=10
    )
    assert evaluation["report_id"] is not None
    report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
    assert report["status"] == "passed", report
    assert report["manifest_sha256"] == prepared["candidate"]["manifest_sha256"]
    assert report["dataset_sha256"] == t026.sha256_hex(prepared["dataset"].content)

    job = f00b.job_row(t026_stack.dbname, prepared["background_job_id"])
    assert job["state"] == "succeeded"
    # The report artifact itself was finalized by the system_io pool.
    assert f00b.artifact_row(t026_stack.dbname, report["result_artifact_id"])["state"] == "ready"
