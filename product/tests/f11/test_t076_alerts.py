"""F11/T076 (#77): a cleanup job that passes its deadline raises a real alert.

The alert surface is the read-only operator command ``orchara-admin deletion
alerts``. It reports, from PostgreSQL, a deletion job that is still owed work past
the documented 24h cleanup target and a ``delete_content`` purge attempt that
reported the retryable ``delete_timeout`` code. Nothing here mocks a clock: the
deadline is evaluated by the database clock, and the timeout is the handler's own
bounded attempt (the real maintenance ``--delete-content-timeout`` flag) expiring
before it could read its scope set.

The command writes nothing: the tombstoned artifact still refuses every read
before, during and after the alert pass, and once the full cleanup runs the same
command reports ``ok`` again (the job is completed, so it is no longer owed).
"""

from __future__ import annotations

from support import f00b, t028, t069, t071, t072, t076
from support import stack as st


def test_a_timed_out_purge_and_an_overdue_deletion_raise_alerts_that_clear(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t076 alert marker: sentinel-alert-body must never be printed\n"
    artifact_id = t071.ready_artifact(stack, owner.creator, content)
    row = t071.artifact_row(stack.dbname, artifact_id)
    object_key = row["object_key"]
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )
    purge = t071.delete_content_job(stack.dbname, job["id"])
    tombstoned = t071.artifact_row(stack.dbname, artifact_id)

    # Healthy baseline: the job is owed but inside its deadline.
    completed = t076.run_alerts(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    clean = t076.alert_report(completed)
    assert clean["status"] == "ok", clean
    assert clean["alerts"] == [], clean
    assert clean["deadline"] == "24h0m0s", clean
    assert "cannot erase" in clean["boundary"], clean

    # A real bounded purge attempt that expires before it can read its scope set.
    completed, reports = t076.purge_attempt_with_deadline(stack, "1ns")
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    attempts = f00b.job_reports(reports)
    assert attempts, reports
    # The drain may claim the retryable job again almost immediately; every attempt
    # in this pass reported the same bounded-timeout code.
    for attempt in attempts:
        assert attempt["result"] == "retried", attempt
        assert attempt["error_code"] == t076.CODE_DELETE_TIMEOUT, attempt
    background = f00b.job_row(stack.dbname, purge["id"])
    assert background["error_code"] == t076.CODE_DELETE_TIMEOUT, background
    assert background["state"] == "ready", background  # retryable, not burned
    timed_out_deletion = t071.deletion_job(stack.dbname, job["id"])
    assert timed_out_deletion["state"] == "failed", timed_out_deletion
    assert "object_store" in t071.remaining(timed_out_deletion), timed_out_deletion

    # Age the deletion and purge rows past the documented deadline. The clock that
    # decides is the database's, so nothing sleeps for 24 hours.
    t076.age_deletion_job(stack.dbname, job["id"], hours=48)
    t076.age_background_job(stack.dbname, purge["id"], hours=48)

    completed = t076.run_alerts(stack, "--deadline", "24h")
    assert completed.returncode == t076.ALERTS_EXIT_ALERT, (
        completed.stdout,
        completed.stderr,
    )
    report = t076.alert_report(completed)
    assert report["status"] == "alert", report
    codes = t076.alert_codes(report)
    assert t076.ALERT_JOB_OVERDUE in codes, report
    assert t076.ALERT_CLEANUP_TIMEOUT in codes, report
    assert report["deletion_jobs"] >= 1 and report["cleanup_jobs"] >= 1, report
    overdue = next(
        alert for alert in report["alerts"] if alert["code"] == t076.ALERT_JOB_OVERDUE
    )
    assert overdue["deletion_id"] == job["id"], overdue
    assert overdue["resource_id"] == artifact_id, overdue
    assert overdue["resource_type"] == t069.RESOURCE_ARTIFACT, overdue
    assert overdue["deadline_seconds"] == 24 * 3600, overdue
    assert overdue["age_seconds"] >= 24 * 3600, overdue
    assert "object_store" in overdue["remaining_scopes"], overdue
    timeout = next(
        alert for alert in report["alerts"] if alert["code"] == t076.ALERT_CLEANUP_TIMEOUT
    )
    assert timeout["background_job_id"] == purge["id"], timeout
    assert timeout["deletion_id"] == job["id"], timeout
    assert timeout["error_code"] == t076.CODE_DELETE_TIMEOUT, timeout
    # The failed deletion is reported as failed too (the purge did not finish).
    assert t076.ALERT_JOB_FAILED in codes, report

    # Redaction: no body, object key or content hash in the operator output, and
    # the alert pass wrote nothing.
    output = completed.stdout + completed.stderr
    assert "sentinel-alert-body" not in output
    assert object_key not in output
    assert row["sha256"] not in output
    unchanged = t071.artifact_row(stack.dbname, artifact_id)
    assert unchanged["purged_at"] is None, unchanged
    assert unchanged["revision"] == tombstoned["revision"], unchanged
    assert unchanged["deleted_at"] == tombstoned["deleted_at"], unchanged
    for suffix in ("", "/content"):
        st.assert_error(
            owner.creator.client.get(
                t069.resource_path(t069.RESOURCE_ARTIFACT, artifact_id) + suffix
            ),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )

    # Finish the real cleanup: the purge removes the bytes, the T072 copies pass
    # clears the four remaining scopes, the job completes.
    t071.wait_retry_available(stack.dbname, purge["id"])
    _completed, reports = t071.purge_once(stack)
    assert t071.purge_report(reports)["result"] == "succeeded", reports
    assert not t071.object_exists(stack, object_key)
    sandbox_root = tmp_path / "t076-sandbox"
    sandbox_root.mkdir()
    t072.copies_ok(stack, sandbox_root=sandbox_root)
    finished = t071.deletion_job(stack.dbname, job["id"])
    assert finished["state"] == "completed", finished
    assert t071.remaining(finished) == [], finished

    completed = t076.run_alerts(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    cleared = t076.alert_report(completed)
    assert cleared["status"] == "ok", cleared
    assert cleared["alerts"] == [], cleared
    # The completed deletion is still unreadable.
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )


def test_an_unclaimed_purge_job_is_a_stalled_cleanup_alert(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact_id = t071.ready_artifact(
        stack, owner.creator, b"t076 stalled cleanup marker\n"
    )
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    )
    purge = t071.delete_content_job(stack.dbname, job["id"])
    # No maintenance process ever claims it; age the unit of work past the target.
    assert f00b.job_row(stack.dbname, purge["id"])["state"] == "ready"
    t076.age_background_job(stack.dbname, purge["id"], hours=30)

    completed = t076.run_alerts(stack)
    assert completed.returncode == t076.ALERTS_EXIT_ALERT, completed.stdout
    report = t076.alert_report(completed)
    assert t076.ALERT_CLEANUP_STALLED in t076.alert_codes(report), report
    stalled = next(
        alert for alert in report["alerts"] if alert["code"] == t076.ALERT_CLEANUP_STALLED
    )
    assert stalled["background_job_id"] == purge["id"], stalled
    assert stalled["deletion_id"] == job["id"], stalled
    assert stalled["age_seconds"] >= 24 * 3600, stalled
    # Within a shorter deadline the alert fires too; the flag is real.
    completed = t076.run_alerts(stack, "--deadline", "1h")
    assert completed.returncode == t076.ALERTS_EXIT_ALERT

    # The unreadability does not depend on the cleanup: the tombstone already won.
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )

    # Usage validation: the deadline must be positive and the limit bounded.
    for args in (["--deadline", "0s"], ["--limit", "0"], ["--limit", "10000"]):
        refused = t076.run_alerts(stack, *args)
        assert refused.returncode == t076.ALERTS_EXIT_USAGE, (args, refused.stdout, refused.stderr)
