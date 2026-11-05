"""F11/T072: a failed copy cleanup is visible while the content stays unreadable.

The failure is real: the test holds a Windows file handle open inside the deleted
run's leftover sandbox workspace, so the cleanup process's ``os.RemoveAll`` fails
with a sharing violation. The job must record the failure, keep the uncleared
scope, and every real read endpoint must keep refusing the tombstoned content. A
retry after the handle is released must finish the scope.
"""

from __future__ import annotations

from support import t041, t069, t072
from support import stack as st


def test_a_failed_sandbox_cleanup_is_visible_while_the_content_stays_unreadable(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    sandbox_root = tmp_path / "sandbox"
    owner, run, _lease = t072.claimed_run_with_exited_attempt(stack)
    thread = t072.checkpoint_thread_id(stack.dbname, run["id"])
    t072.create_checkpoint_schema(stack.dbname)
    handle = None
    try:
        t072.insert_checkpoint_rows(
            stack.dbname, thread, label="delete-me", payload=b'{"private":"failure"}'
        )
        unit = t072.attempt_units(stack.dbname, run["id"])[0]
        leftover = t072.make_sandbox_leftover(
            sandbox_root, unit, b"T072-FAILURE-SENTINEL", label="open-body"
        )
        job = t072.delete_run_input_artifact(stack, owner, run["id"])

        # The real failure: an open handle inside the workspace blocks rmtree.
        handle = open(leftover / "open-body", "rb")
        completed, reports = t072.copies(stack, sandbox_root=sandbox_root)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        summary = t072.copies_summary(reports)
        assert summary["scopes_failed"] == 1, summary
        assert summary["scopes_cleared"] == 3, summary

        stored = t069.deletion_job(stack.dbname, job["id"])
        assert stored["state"] == "failed", stored
        assert stored["error_code"] == "copy_sandbox_remove_failed", stored
        remaining = t069.remaining_scopes(stored)
        completed_scopes = t069.completed_scopes(stored)
        assert "sandbox_temp" in remaining, remaining
        assert set(completed_scopes) == {"checkpoints", "third_party_history", "backup_catalog"}, (
            completed_scopes
        )
        assert leftover.exists(), "the blocked workspace must still be there"

        entries = t072.entries_for_job(stack.dbname, job["id"])
        assert t072.entry_of(entries, copy_class="checkpoint")["entry_kind"] == "scope_cleared"
        failed = t072.entry_of(entries, copy_class="sandbox_temp")
        assert failed["entry_kind"] == "scope_failed", failed
        failed_record = t072.ledger_record(failed)
        assert failed_record["error_code"] == "copy_sandbox_remove_failed", failed_record
        assert failed_record["entries"][0]["name"] == leftover.name, failed_record
        assert failed_record["entries"][0]["removed"] is False, failed_record

        # GET /deletions/{id} still shows the uncleared scope and the failed state.
        view = t069.get_deletion(owner.creator, job["id"])
        assert view.status == 200, view.text
        assert view.json["state"] == "failed", view.json
        assert "sandbox_temp" in view.json["remaining_scopes"], view.json
        assert "object_store" in view.json["remaining_scopes"], view.json

        # The content is still unreadable while the cleanup is incomplete.
        t072.assert_read_refuses_artifact(stack, owner, stored["resource_id"])

        # Release the handle: the retry clears the scope and the job stops failing.
        handle.close()
        handle = None
        retried, reports_again = t072.copies_ok(stack, sandbox_root=sandbox_root)
        assert retried.returncode == 0, retried.stdout + retried.stderr
        assert not leftover.exists(), leftover
        retried_job = t069.deletion_job(stack.dbname, job["id"])
        assert retried_job["state"] == "pending", retried_job
        assert retried_job["error_code"] is None, retried_job
        assert "sandbox_temp" not in t069.remaining_scopes(retried_job), retried_job
        assert set(t069.completed_scopes(retried_job)) == set(t072.COPY_SCOPES), retried_job
        cleared = t072.entry_of(
            t072.entries_for_job(stack.dbname, job["id"]), copy_class="sandbox_temp", entry_kind="scope_cleared"
        )
        record = t072.ledger_record(cleared)
        assert record["entries"][0]["removed"] is True, record
        assert t072.copies_summary(reports_again)["jobs"] == 1, reports_again
        # A third pass has nothing left to do.
        _, reports_third = t072.copies_ok(stack, sandbox_root=sandbox_root)
        assert t072.copies_summary(reports_third)["jobs"] == 0
    finally:
        if handle is not None:
            handle.close()
        t072.drop_checkpoint_schema(stack.dbname)


def test_a_live_attempt_workspace_is_never_deleted(t069_stack, tmp_path) -> None:
    stack = t069_stack
    sandbox_root = tmp_path / "sandbox"
    owner, run, _lease = t041.claimed_run(stack)  # the attempt never exits: it is live
    unit = t072.attempt_units(stack.dbname, run["id"])[0]
    assert unit == "t041-unit", unit
    leftover = t072.make_sandbox_leftover(sandbox_root, unit, b"live-body")
    job = t072.delete_run_input_artifact(stack, owner, run["id"])
    t072.copies_ok(stack, sandbox_root=sandbox_root, extra_args=["--sandbox-age", "0s"])

    assert leftover.exists(), "a live attempt's workspace must not be deleted"
    sandbox = t072.entry_of(
        t072.entries_for_job(stack.dbname, job["id"]), copy_class="sandbox_temp"
    )
    assert sandbox["entry_kind"] == "scope_failed", sandbox
    assert t072.ledger_record(sandbox)["error_code"] == "copy_sandbox_live_attempt", sandbox
    stored = t069.deletion_job(stack.dbname, job["id"])
    assert stored["state"] == "failed", stored
    assert "sandbox_temp" in t069.remaining_scopes(stored), stored
    assert "sandbox_temp" not in t069.completed_scopes(stored), stored
