"""T026 acceptance: a failed report and a failed job are different facts.

* A business evaluation failure (a case fails, or the dataset is not a valid
  dataset) is expressed by the report's status=failed while the evaluation job
  succeeds and the evaluation row is succeeded.
* An infrastructure failure (the dataset is tombstoned, or its sealed bytes
  cannot be read) fails the background job and the evaluation row with a stable
  error code and creates no report row at all.

Both are observed through the real processes and the real public reads.
"""

from __future__ import annotations

from support import f00b, t026, t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)


def test_a_failed_case_is_a_successful_job_with_a_failed_report(t026_stack) -> None:
    good = t026.dataset_case(case_id="good", input_value={"q": "ok"})
    bad = t026.dataset_case(
        case_id="bad",
        input_value={"q": "wrong"},
        expected_sha256="0" * 64,
    )
    prepared = t026.prepare_model_evaluation(t026_stack, cases=[good, bad])
    completed, reports = t026.run_pools_once(t026_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=10
    )
    assert evaluation["error_code"] is None
    assert evaluation["report_id"] is not None
    report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
    assert report["status"] == "failed", report
    assert report["manifest_sha256"] == prepared["candidate"]["manifest_sha256"]

    job = f00b.job_row(t026_stack.dbname, prepared["background_job_id"])
    assert job["state"] == "succeeded", job
    assert job["error_code"] is None
    assert job["failure_class"] is None
    assert job["result_artifact_id"] == report["result_artifact_id"]

    session = f00b.admin_session(t026_stack)
    body = t026.get_evaluation(session, prepared["evaluation_id"])
    assert body.status == 200, body.text
    assert body.json["state"] == "succeeded"
    assert body.json["report_id"] == report["id"]
    assert body.json["error_code"] is None
    report_body = t026.get_report(session, report["id"])
    assert report_body.status == 200, report_body.text
    assert report_body.json["status"] == "failed"


def test_an_empty_case_set_is_not_run_never_passed(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack, cases=[])
    completed, reports = t026.run_pools_once(t026_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=10
    )
    assert evaluation["report_id"] is not None
    report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report


def test_a_malformed_dataset_is_a_failed_report_not_a_job_failure(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack, content=b'{"cases": "not-an-array"}')
    completed, reports = t026.run_pools_once(t026_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=10
    )
    assert evaluation["report_id"] is not None
    report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
    assert report["status"] == "failed", report
    assert f00b.job_row(t026_stack.dbname, prepared["background_job_id"])["state"] == "succeeded"


def test_a_tombstoned_dataset_fails_the_job_and_the_evaluation_without_a_report(
    t026_stack,
) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack)
    f00b.delete_artifact_row(t026_stack.dbname, prepared["dataset"].id)

    completed, reports = t026.run_pools_once(t026_stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    job = t026.wait_for_job_state(
        t026_stack.dbname, prepared["background_job_id"], {"failed"}, timeout=20
    )
    assert job["error_code"] == "source_deleted", job
    assert job["failure_class"] == "business", job
    assert job["result_artifact_id"] is None

    evaluation = t026.evaluation_row(t026_stack.dbname, prepared["evaluation_id"])
    assert evaluation["state"] == "failed", evaluation
    assert evaluation["error_code"] == "source_deleted", evaluation
    assert evaluation["report_id"] is None
    assert evaluation["finished_at"] is not None
    assert t026.report_count(t026_stack.dbname) == 0

    session = f00b.admin_session(t026_stack)
    body = t026.get_evaluation(session, prepared["evaluation_id"])
    assert body.status == 200, body.text
    assert body.json["state"] == "failed"
    assert body.json["report_id"] is None
    assert body.json["error_code"] == "source_deleted"


def test_unreadable_dataset_bytes_retry_then_fail_without_a_report(t026_stack, tmp_path) -> None:
    # Ready metadata, but no sealed object: the handler can download a URL and
    # the store returns 404. This is a retryable infrastructure failure.
    prepared = t026.prepare_model_evaluation(t026_stack, sealed=False)

    log_path = tmp_path / "retry.log"
    process, handle = t026.start_pools(
        t026_stack, work_slots=1, system_io_slots=1, log_path=log_path
    )
    try:
        job = t026.wait_for_job_state(
            t026_stack.dbname, prepared["background_job_id"], {"failed"}, timeout=30
        )
    finally:
        log_text = t026.stop_process(process, handle, log_path)

    assert job["error_code"] == "transient_storage_io", job
    assert job["failure_class"] == "infrastructure", job
    assert int(job["attempt_count"]) == 3, job

    evaluation = t026.evaluation_row(t026_stack.dbname, prepared["evaluation_id"])
    assert evaluation["state"] == "failed", evaluation
    assert evaluation["error_code"] == "transient_storage_io", evaluation
    assert evaluation["report_id"] is None
    assert t026.report_count(t026_stack.dbname) == 0
    assert "application_evaluate" in log_text


def test_exhausted_leases_fail_the_evaluation_without_a_report(t026_stack) -> None:
    """The third expired lease terminalizes the job AND its evaluation row.

    Recovery happens inside the next claim's transaction (there is no separate
    sweep process), so the same rule must hold on that path as on Complete: a
    retryable failure leaves the evaluation running (an attempt is still in
    flight), while the exhausted third attempt fails the job and the evaluation
    with the same code and no report row.
    """
    prepared = t026.prepare_model_evaluation(t026_stack)
    job_id = prepared["background_job_id"]
    evaluation_id = prepared["evaluation_id"]
    client = t026_stack.internal_client()

    # Attempt 1.
    first = client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE])
    assert first.status == 200, first.text
    assert first.json["job_id"] == job_id
    assert t026.evaluation_row(t026_stack.dbname, evaluation_id)["state"] == "running"
    f00b.force_lease_expiry(t026_stack.dbname, job_id)

    # The next claim recovers the expired lease (ready + backoff) and finds
    # nothing to hand out yet. The evaluation is still running: a retry is
    # pending, not a terminal failure.
    recovered = client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE])
    assert recovered.status == 204, recovered.text
    job = f00b.job_row(t026_stack.dbname, job_id)
    assert job["state"] == "ready", job
    assert job["error_code"] == "lease_expired"
    evaluation = t026.evaluation_row(t026_stack.dbname, evaluation_id)
    assert evaluation["state"] == "running", evaluation
    assert evaluation["report_id"] is None
    assert evaluation["finished_at"] is None

    # Attempt 2 and 3.
    for attempt in (2, 3):
        t026.wait_for_retry_available(t026_stack.dbname, job_id)
        lease = client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE])
        assert lease.status == 200, lease.text
        assert lease.json["job_id"] == job_id
        assert int(f00b.job_row(t026_stack.dbname, job_id)["attempt_count"]) == attempt
        assert t026.evaluation_row(t026_stack.dbname, evaluation_id)["state"] == "running"
        f00b.force_lease_expiry(t026_stack.dbname, job_id)
        recovered = client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE])
        assert recovered.status == 204, recovered.text
        if attempt == 2:
            row = f00b.job_row(t026_stack.dbname, job_id)
            assert row["state"] == "ready", row
            assert t026.evaluation_row(t026_stack.dbname, evaluation_id)["state"] == "running"

    # The third expired lease is not retried: the job fails, and the bound
    # evaluation fails with the same code and no report.
    job = f00b.job_row(t026_stack.dbname, job_id)
    assert job["state"] == "failed", job
    assert job["error_code"] == "lease_expired", job
    assert job["failure_class"] == "infrastructure", job
    assert int(job["attempt_count"]) == 3
    assert job["finished_at"] is not None

    evaluation = t026.evaluation_row(t026_stack.dbname, evaluation_id)
    assert evaluation["state"] == "failed", evaluation
    assert evaluation["error_code"] == "lease_expired", evaluation
    assert evaluation["report_id"] is None
    assert evaluation["finished_at"] is not None
    assert t026.report_count(t026_stack.dbname) == 0

    # No fourth attempt exists: the job is terminal.
    assert client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE]).status == 204


def test_an_application_evaluation_with_a_foreign_snapshot_fails_honestly(t026_stack) -> None:
    owner = f00b.principal_id_for_login(t026_stack.dbname)
    dataset = t026.seed_dataset(t026_stack, owner)
    # T027 makes the snapshot reference real, so the honest refusal is no longer
    # "the table is missing": it is that the snapshot belongs to another
    # principal, which is not a valid evaluation binding.
    foreign_owner = t027.seed_principal(t026_stack.dbname, "t026-foreign-snapshot")
    foreign_snapshot = t027.seed_execution_snapshot(t026_stack.dbname, foreign_owner)
    payload = t026.enqueue_evaluation_ok(
        t026_stack,
        kind="application",
        dataset_artifact_id=dataset.id,
        snapshot_id=foreign_snapshot,
    )
    completed, reports = t026.run_pools_once(t026_stack, work_slots=1)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    job = t026.wait_for_job_state(
        t026_stack.dbname, payload["background_job_id"], {"failed"}, timeout=20
    )
    assert job["error_code"] == "evaluation_binding_missing", job
    evaluation = t026.evaluation_row(t026_stack.dbname, payload["id"])
    assert evaluation["state"] == "failed", evaluation
    assert evaluation["error_code"] == "evaluation_binding_missing"
    assert evaluation["report_id"] is None
    assert t026.report_count(t026_stack.dbname) == 0
