"""F11/T072: copy-class cleanup for one deletion job.

Every class the ticket names produces a recorded outcome per deletion job and
scope in the hash-chained ledger:

* ``checkpoint``  real rows in the out-of-band ``checkpoint`` schema (or an honest
  ``absent`` when the schema is not installed);
* ``summary``     there is no summary store in this product, so the class is
  ``absent`` after counting the metadata-only surfaces;
* ``sandbox_temp`` real leftover host workspaces named for the deleted run's
  attempt unit;
* ``third_party_history`` the deployed component boundary (empty by default: T066
  decided ``do_not_adopt``);
* ``backup_catalog`` the independent ledger registration plus the honest
  not-erasable declarations (vendor copy, downloaded copy, backup media).

The copy pass is the real compiled ``cmd/maintenance copies``; the CPython process
never removes a file itself. A second pass proves the repeated run deletes
nothing legitimate.
"""

from __future__ import annotations

import json
import uuid

from support import t069, t072
from support import stack as st


def test_every_copy_class_is_recorded_and_a_missing_checkpoint_schema_is_absent(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    sandbox_root = tmp_path / "sandbox"
    owner, run, _lease = t072.claimed_run_with_exited_attempt(stack)
    job = t072.delete_run_input_artifact(stack, owner, run["id"])
    stored = t069.deletion_job(stack.dbname, job["id"])
    assert not t072.checkpoint_schema_present(stack.dbname)

    completed, reports = t072.copies_ok(stack, sandbox_root=sandbox_root)
    summary = t072.copies_summary(reports)
    assert summary["jobs"] == 1, summary
    assert summary["job_ids"] == [job["id"]], summary

    entries = t072.entries_for_job(stack.dbname, job["id"])
    by_class = {entry["copy_class"]: entry for entry in entries if entry["copy_class"]}
    assert set(t072.COPY_CLASSES) <= set(by_class), entries

    checkpoint = by_class["checkpoint"]
    assert checkpoint["entry_kind"] == "scope_absent", checkpoint
    assert checkpoint["scope"] == "checkpoints", checkpoint
    checkpoint_record = t072.ledger_record(checkpoint)
    assert checkpoint_record["reason"] == "checkpoint_schema_absent", checkpoint_record
    assert checkpoint_record["thread_ids"] == [t072.checkpoint_thread_id(stack.dbname, run["id"])]

    summary_entry = by_class["summary"]
    assert summary_entry["entry_kind"] == "scope_absent", summary_entry
    summary_record = t072.ledger_record(summary_entry)
    assert summary_record["reason"] == "no_summary_store", summary_record
    assert set(summary_record["surfaces"]) == {
        "run_events",
        "run_attempts.reported_result",
        "run_node_outputs",
    }, summary_record

    sandbox = by_class["sandbox_temp"]
    assert sandbox["entry_kind"] == "scope_absent", sandbox
    assert t072.ledger_record(sandbox)["reason"] == "no_leftover_workspace", sandbox

    third_party = by_class["third_party_history"]
    assert third_party["entry_kind"] == "scope_absent", third_party
    assert t072.ledger_record(third_party)["reason"] == "no_third_party_component_deployed"

    catalog = by_class["backup_catalog"]
    assert catalog["entry_kind"] == "scope_cleared", catalog
    assert t072.ledger_record(catalog)["reason"] == "deletion_registered_in_ledger"

    # The copy pass declares, but never claims to have erased, the copies outside
    # platform control.
    assert by_class["downloaded_copy"]["entry_kind"] == "scope_external", by_class["downloaded_copy"]
    assert by_class["backup_media"]["entry_kind"] == "scope_external", by_class["backup_media"]
    assert by_class["vendor_copy"]["entry_kind"] == "scope_absent", by_class["vendor_copy"]
    assert t072.ledger_record(by_class["vendor_copy"])["reason"] == "no_external_model_path"

    # Four T072 scopes are cleared on the job; T071's three remain outstanding.
    job_after = t069.deletion_job(stack.dbname, job["id"])
    assert t069.completed_scopes(job_after) == sorted(t072.COPY_SCOPES), job_after
    assert t069.remaining_scopes(job_after) == sorted(t069.T071_SCOPES), job_after
    assert job_after["state"] == "pending", job_after

    view = t069.get_deletion(owner.creator, job["id"])
    assert view.status == 200, view.text
    assert view.json["remaining_scopes"] == sorted(t069.T071_SCOPES), view.json
    assert view.json["state"] == "pending", view.json

    # A second pass has no outstanding T072 scope: it deletes nothing and appends
    # nothing.
    ledger_before = t072.ledger_count(stack.dbname)
    completed_again, reports_again = t072.copies_ok(stack, sandbox_root=sandbox_root)
    again = t072.copies_summary(reports_again)
    assert again["jobs"] == 0, again
    assert t072.ledger_count(stack.dbname) == ledger_before


def test_named_checkpoint_and_sandbox_copies_are_cleared_and_a_repeat_run_keeps_their_neighbours(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    sandbox_root = tmp_path / "sandbox"
    owner, run, _lease = t072.claimed_run_with_exited_attempt(stack)
    thread = t072.checkpoint_thread_id(stack.dbname, run["id"])
    other_thread = str(uuid.uuid4())
    t072.create_checkpoint_schema(stack.dbname)
    try:
        t072.insert_checkpoint_rows(
            stack.dbname, thread, label="deleted-run-checkpoint",
            payload=b'{"private":"T072-CHECKPOINT-SENTINEL"}',
        )
        t072.insert_checkpoint_rows(
            stack.dbname, other_thread, label="legit-labelled-copy",
            payload=b'{"private":"T072-LEGIT-SENTINEL"}',
        )
        unit = t072.attempt_units(stack.dbname, run["id"])[0]
        leftover = t072.make_sandbox_leftover(
            sandbox_root, unit, b"T072-SANDBOX-SENTINEL", label="workspace-body"
        )
        unrelated = t072.make_unrelated_leftover(sandbox_root, b"T072-UNRELATED")

        job = t072.delete_run_input_artifact(stack, owner, run["id"])
        completed, reports = t072.copies_ok(
            stack, sandbox_root=sandbox_root, extra_args=["--sandbox-age", "0s"]
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        summary = t072.copies_summary(reports)
        assert summary["scopes_cleared"] == 4, summary

        # Real checkpoint rows of the deleted run are gone from all three tables;
        # the other thread's rows are untouched (the labelled legitimate copy).
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            assert t072.checkpoint_row_count(stack.dbname, table, thread_id=thread) == 0, table
            assert t072.checkpoint_row_count(stack.dbname, table, thread_id=other_thread) == 1, table

        assert not leftover.exists(), leftover
        assert unrelated.exists(), unrelated
        assert (unrelated / "input.json").read_bytes() == b"T072-UNRELATED"

        entries = t072.entries_for_job(stack.dbname, job["id"])
        checkpoint = t072.entry_of(entries, copy_class="checkpoint")
        assert checkpoint["entry_kind"] == "scope_cleared", checkpoint
        record = t072.ledger_record(checkpoint)
        assert record["thread_ids"] == [thread], record
        assert record["tables"]["checkpoints"]["deleted"] == 1, record
        assert record["tables"]["checkpoint_blobs"]["deleted"] == 1, record
        assert record["tables"]["checkpoint_writes"]["deleted"] == 1, record

        sandbox = t072.entry_of(entries, copy_class="sandbox_temp")
        assert sandbox["entry_kind"] == "scope_cleared", sandbox
        sandbox_record = t072.ledger_record(sandbox)
        assert sandbox_record["entries"][0]["name"] == leftover.name, sandbox_record
        assert t072.directory_bytes(unrelated) == len(b"T072-UNRELATED"), (
            "the unrelated leftover was touched"
        )

        job_after = t069.deletion_job(stack.dbname, job["id"])
        assert t069.completed_scopes(job_after) == sorted(t072.COPY_SCOPES), job_after

        # Repeat the pass: nothing named by the completed job is deleted again and
        # the legitimate neighbour survives both runs.
        ledger_before = t072.ledger_count(stack.dbname)
        _, reports_again = t072.copies_ok(
            stack, sandbox_root=sandbox_root, extra_args=["--sandbox-age", "0s"]
        )
        assert t072.copies_summary(reports_again)["jobs"] == 0
        assert t072.ledger_count(stack.dbname) == ledger_before
        assert unrelated.exists() and (unrelated / "input.json").read_bytes() == b"T072-UNRELATED"
        assert t072.checkpoint_row_count(stack.dbname, "checkpoints", thread_id=other_thread) == 1

        # The deleted checkpoint body is nowhere in the ledger or the report.
        exported, _ = t072.export_ledger(stack, tmp_path / "copies.jsonl")
        exported_text = (tmp_path / "copies.jsonl").read_text(encoding="utf-8")
        assert "T072-CHECKPOINT-SENTINEL" not in exported_text
        assert "T072-LEGIT-SENTINEL" not in exported_text
        assert "T072-SANDBOX-SENTINEL" not in exported_text
    finally:
        t072.drop_checkpoint_schema(stack.dbname)


def test_all_four_scopes_terminal_completes_the_job_and_records_non_erasable_copies(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"complete me")
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )
    # T071 is a different ticket on a parallel branch. Simulate exactly the row
    # transition T071 performs (scope removed, appended to completed) so the
    # *shared* clearing rule can be exercised end to end; the ledger rows of this
    # pass are the product's own.
    t072.t071_scopes_cleared(stack.dbname, job["id"])

    t072.copies_ok(stack, sandbox_root=tmp_path / "sandbox")
    job_after = t069.deletion_job(stack.dbname, job["id"])
    assert job_after["state"] == "completed", job_after
    assert job_after["completed_at"] is not None, job_after
    assert t069.remaining_scopes(job_after) == [], job_after
    assert set(t069.completed_scopes(job_after)) == set(t069.SCOPES), job_after

    entries = t072.entries_for_job(stack.dbname, job["id"])
    completed_entry = t072.entry_of(entries, entry_kind="job_completed")
    record = t072.ledger_record(completed_entry)
    assert record["reason"] == "all_scopes_terminal", record
    assert set(record["scopes"]) == set(t069.SCOPES), record
    non_erasable = {item["copy_class"] for item in record["non_erasable"]}
    assert non_erasable == {"vendor_copy", "downloaded_copy", "backup_media"}, record

    view = t069.get_deletion(owner.creator, job["id"])
    assert view.status == 200, view.text
    assert view.json["state"] == "completed", view.json
    assert view.json["remaining_scopes"] == [], view.json


def test_a_deployed_third_party_component_is_recorded_external_not_erasable(
    t069_stack, tmp_path
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"third party")
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )

    t072.copies_ok(
        stack,
        sandbox_root=tmp_path / "sandbox",
        extra_args=["--third-party-components", "mem0"],
    )
    entries = t072.entries_for_job(stack.dbname, job["id"])
    third_party = t072.entry_of(entries, copy_class="third_party_history")
    assert third_party["entry_kind"] == "scope_external", third_party
    record = t072.ledger_record(third_party)
    assert record["components"] == ["mem0"], record
    assert record["reason"] == "component_history_outside_platform_control", record
    assert "cannot be erased" in record["note"], record
    # The scope is terminal (recorded honestly) even though nothing was erased.
    job_after = t069.deletion_job(stack.dbname, job["id"])
    assert "third_party_history" in t069.completed_scopes(job_after), job_after
    assert "third_party_history" not in t069.remaining_scopes(job_after), job_after


def test_a_failed_model_call_count_is_recorded_as_a_failed_check(
    t069_stack, tmp_path
) -> None:
    """T072 AC1/AC4: a failed vendor-copy check is not "nothing to look for".

    The failure is real: ``platform.operations`` is renamed away for the pass, so
    the model-call count query fails. The vendor class must be recorded failed and
    the scope kept; before the fix the same failure was recorded ``absent`` with
    ``no_external_model_path``.
    """
    stack = t069_stack
    owner, run, _lease = t072.claimed_run_with_exited_attempt(
        stack, input_value={"text": "t072 failed count"}
    )
    job = t072.delete_run_input_artifact(stack, owner, run["id"])

    st.sql(stack.dbname, "ALTER TABLE platform.operations RENAME TO operations_t072_hidden")
    try:
        completed, reports = t072.copies(stack, sandbox_root=tmp_path / "sandbox")
    finally:
        st.sql(
            stack.dbname,
            "ALTER TABLE platform.operations_t072_hidden RENAME TO operations",
        )
    assert completed.returncode == 0, completed.stdout + completed.stderr

    entries = t072.entries_for_job(stack.dbname, job["id"])
    vendor = t072.entry_of(entries, copy_class="vendor_copy")
    assert vendor["entry_kind"] == "scope_failed", vendor
    record = t072.ledger_record(vendor)
    assert record["reason"] == "model_call_count_failed", record
    assert record["error_code"] == "copy_vendor_count_failed", record

    job_after = t069.deletion_job(stack.dbname, job["id"])
    assert job_after["state"] == "failed", job_after
    assert job_after["error_code"] == "copy_vendor_count_failed", job_after
    assert "third_party_history" in t069.remaining_scopes(job_after), job_after


def test_the_summary_surfaces_hold_no_body_copy(t069_stack, tmp_path) -> None:
    stack = t069_stack
    sentinel = "T072-SUMMARY-SENTINEL " + uuid.uuid4().hex
    owner, run, _lease = t072.claimed_run_with_exited_attempt(
        stack, input_value={"text": sentinel}
    )
    artifact = t069.run_rows(stack.dbname, run["id"])["input_artifact_id"]
    # Positive control: the deleted body really is served before the delete.
    before = owner.creator.client.get(f"/api/v1/artifacts/{artifact}/content")
    assert before.status == 200, before.text
    assert sentinel in before.text

    job = t072.delete_run_input_artifact(stack, owner, run["id"])
    completed, reports = t072.copies_ok(stack, sandbox_root=tmp_path / "sandbox")
    assert completed.returncode == 0, completed.stdout + completed.stderr

    # After the delete every real read refuses the body while the cleanup runs.
    t072.assert_read_refuses_artifact(stack, owner, artifact)

    surfaces = {
        "run_events": t069.run_events(stack.dbname, run["id"]),
        "audit": t069.audit_events(stack.dbname, "deletion.requested", resource_id=artifact),
        "ledger": t072.ledger_rows(stack.dbname, deletion_job_id=job["id"]),
        "maintenance_stdout": completed.stdout,
        "api_log": stack.server.logs(),
    }
    for name, payload in surfaces.items():
        text = json.dumps(payload) if not isinstance(payload, str) else payload
        assert sentinel not in text, f"the deleted body leaked into {name}"

    summary = t072.entry_of(
        t072.entries_for_job(stack.dbname, job["id"]), copy_class="summary"
    )
    assert summary["entry_kind"] == "scope_absent", summary
    assert t072.ledger_record(summary)["reason"] == "no_summary_store", summary

    t072.export_ledger(stack, tmp_path / "summary.jsonl")
    assert sentinel not in (tmp_path / "summary.jsonl").read_text(encoding="utf-8")
