"""T027: editing the draft never changes an existing debug run or its bytes.

The snapshot, its canonical manifest and the canonical test input are frozen when
the submission commits. This file proves that a later draft save changes only
what a *new* debug submission freezes; the old run keeps its bytes, its hash and
its queued start job.
"""

from __future__ import annotations

import secrets

from support import stack as st
from support import t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
)


def owner(t027_ctx):
    admin = t027.admin_session(t027_ctx)
    creator = t027.invite_and_activate(
        t027_ctx, admin, "t027-frozen-" + secrets.token_hex(4), roles=("creator",)
    )
    application_id = t027.seed_golden_draft(t027_ctx.dbname, creator.principal_id)
    return admin, creator, application_id


def test_editing_the_draft_keeps_the_existing_debug_byte_identical(t027_stack) -> None:
    _admin, creator, application_id = owner(t027_stack)
    submitted = t027.submit_debug(creator, application_id)
    assert submitted.status == 202, submitted.text
    run = submitted.json
    run_row = t027.run_row(t027_stack.dbname, run["id"])
    snapshot = t027.snapshot_row(t027_stack.dbname, run["execution_snapshot_id"])
    input_artifact = t027.artifact_row(t027_stack.dbname, run_row["input_artifact_id"])
    manifest_artifact = t027.artifact_row(
        t027_stack.dbname, snapshot["manifest_artifact_id"]
    )
    input_bytes = t027.object_bytes(t027_stack.object_dir, input_artifact["object_key"])
    manifest_bytes = t027.object_bytes(t027_stack.object_dir, manifest_artifact["object_key"])

    # Save a new revision that changes executable content (not only layout).
    draft = t027.get_draft(creator, application_id)
    assert draft.header("ETag") == '"1"'
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("anchor-minimal-linear"),
        capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'
    assert t027.draft_row(t027_stack.dbname, application_id)["execution_revision"] == 2

    # The frozen execution identity is unchanged: same row, same hash, same bytes.
    assert t027.snapshot_row(t027_stack.dbname, run["execution_snapshot_id"]) == snapshot
    assert t027.run_row(t027_stack.dbname, run["id"]) == run_row
    assert t027.artifact_row(t027_stack.dbname, run_row["input_artifact_id"]) == input_artifact
    assert t027.artifact_row(t027_stack.dbname, snapshot["manifest_artifact_id"]) == manifest_artifact
    assert t027.object_bytes(t027_stack.object_dir, input_artifact["object_key"]) == input_bytes
    assert t027.object_bytes(t027_stack.object_dir, manifest_artifact["object_key"]) == manifest_bytes
    assert t027.sha256_hex(input_bytes) == input_artifact["sha256"]
    assert t027.sha256_hex(manifest_bytes) == snapshot["manifest_sha256"]

    read = t027.get_run(creator, run["id"])
    assert read.status == 200, read.text
    assert read.json == run
    assert read.header("X-Manifest-SHA256") == t027.GOLDEN_SHA256

    counts = t027.execution_rows(t027_stack.dbname, application_id)
    assert counts["snapshots"] == 1 and counts["runs"] == 1 and counts["jobs"] == 1, counts

    # A new submission naming the old revision is refused; it is not silently
    # debugged at revision 2.
    stale = t027.submit_debug(creator, application_id, draft_revision=1)
    error = st.assert_error(stale, 409, "revision_conflict")
    assert error["details"] == {"requested_revision": 1, "current_revision": 2}, error
    assert t027.execution_rows(t027_stack.dbname, application_id)["snapshots"] == 1


def test_a_new_debug_after_the_edit_freezes_the_new_revision(t027_stack) -> None:
    _admin, creator, application_id = owner(t027_stack)
    first = t027.submit_debug(creator, application_id)
    assert first.status == 202, first.text
    first_run = first.json
    first_hash = first.header("X-Manifest-SHA256")
    first_snapshot = t027.snapshot_row(t027_stack.dbname, first_run["execution_snapshot_id"])

    draft = t027.get_draft(creator, application_id)
    saved = t027.save_draft(
        creator,
        application_id,
        draft.header("ETag"),
        definition=t027.p05_case("anchor-minimal-linear"),
        capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text

    second = t027.submit_debug(creator, application_id, draft_revision=2, key=t027.new_idempotency_key())
    assert second.status == 202, second.text
    second_run = second.json
    assert second_run["id"] != first_run["id"]
    assert second_run["execution_snapshot_id"] != first_run["execution_snapshot_id"]
    second_hash = second.header("X-Manifest-SHA256")
    assert second_hash and second_hash != first_hash

    second_snapshot = t027.snapshot_row(t027_stack.dbname, second_run["execution_snapshot_id"])
    assert second_snapshot["draft_revision"] == 2
    assert second_snapshot["manifest_sha256"] == second_hash

    # The first snapshot still describes revision 1 and its original hash.
    assert t027.snapshot_row(t027_stack.dbname, first_run["execution_snapshot_id"]) == first_snapshot
    assert first_snapshot["draft_revision"] == 1
    assert first_snapshot["manifest_sha256"] == first_hash

    # Two independent private executions; the application still has no version.
    counts = t027.execution_rows(t027_stack.dbname, application_id)
    assert counts == {
        "snapshots": 2,
        "runs": 2,
        "jobs": 2,
        "versions": 0,
        "reviews": 0,
        "background_jobs": 0,
        "evaluations": 0,
    }, counts
    assert first_run["id"] != second_run["id"]
