"""T027: debugDraft creates a private snapshot, a queued run and a ready job.

The application is the T025 golden draft (fixed application id, sales definition,
full capability card, a real approved tool and a passed model candidate), so the
manifest the submission freezes is the checked-in canonical byte sequence: the
run's hash must equal the golden sha and the sealed bytes must equal
``manifest.jcs``. Nothing here calls the compiler in-process; every assertion
goes through the contract endpoint and the real object store.
"""

from __future__ import annotations

import decimal
import json
import secrets

from support import stack as st
from support import t025, t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
)


def owner(t027_ctx):
    """A creator who owns the T025 golden draft (fixed application id)."""
    admin = t027.admin_session(t027_ctx)
    creator = t027.invite_and_activate(
        t027_ctx, admin, "t027-owner-" + secrets.token_hex(4), roles=("creator",)
    )
    application_id = t027.seed_golden_draft(t027_ctx.dbname, creator.principal_id)
    return admin, creator, application_id


def test_debug_creates_a_private_snapshot_run_and_ready_job(t027_stack) -> None:
    _admin, creator, application_id = owner(t027_stack)
    response = t027.submit_debug(creator, application_id, input_value={"text": "hello"})

    assert response.status == 202, response.text
    run = response.json
    t027.run_fields(run)
    assert run["state"] == "queued"
    assert run["revision"] == 1
    assert response.header("X-Manifest-SHA256") == t027.GOLDEN_SHA256

    snapshot = t027.snapshot_row(t027_stack.dbname, run["execution_snapshot_id"])
    assert snapshot["kind"] == "debug"
    assert snapshot["application_id"] == application_id
    assert snapshot["application_version_id"] is None
    assert snapshot["draft_revision"] == 1
    assert snapshot["owner_id"] == creator.principal_id
    assert snapshot["manifest_sha256"] == t027.GOLDEN_SHA256
    assert snapshot["compiler_version"] == "orchara-compiler/1.0.0"
    assert snapshot["worker_image_digest"] == t027.WORKER_IMAGE_DIGEST
    assert snapshot["state_schema_version"] == "orchara-state/1.0"
    golden_manifest = json.loads(
        (t025.SNAPSHOTS / "sales" / "manifest.json").read_text(encoding="utf-8")
    )
    assert snapshot["manifest"] == golden_manifest

    # The manifest artifact the snapshot references is T025's exact byte
    # sequence: same hash, same size, same sealed file.
    manifest_artifact = t027.artifact_row(t027_stack.dbname, snapshot["manifest_artifact_id"])
    jcs_bytes, golden_sha = t025.golden_bytes()
    assert manifest_artifact["purpose"] == "execution_manifest"
    assert manifest_artifact["state"] == "ready"
    assert manifest_artifact["sha256"] == golden_sha == t027.GOLDEN_SHA256
    assert manifest_artifact["size_bytes"] == len(jcs_bytes)
    assert manifest_artifact["media_type"] == "application/json"
    sealed = t027.object_bytes(t027_stack.object_dir, manifest_artifact["object_key"])
    assert sealed == jcs_bytes
    assert t027.sha256_hex(sealed) == t027.GOLDEN_SHA256

    # The run row: private debug identity, queued, idle, budget from the policy.
    run_row = t027.run_row(t027_stack.dbname, run["id"])
    assert run_row["owner_id"] == creator.principal_id
    assert run_row["kind"] == "debug"
    assert run_row["request_id"] is None
    assert run_row["conversation_id"] is None
    assert run_row["parent_run_id"] is None
    assert run_row["application_version_id"] is None
    assert run_row["execution_snapshot_id"] == run["execution_snapshot_id"]
    assert run_row["manifest_sha256"] == t027.GOLDEN_SHA256
    assert run_row["state"] == "queued"
    assert run_row["phase"] == "idle"
    assert run_row["revision"] == 1
    # T032 writes the queued run.status event (seq 1) in the same transaction, so
    # the next free seq is 2. T034 freezes the policy's 900s active-time limit on
    # the Run at creation (milliseconds; 900 * 1000).
    assert run_row["next_event_seq"] == 2
    assert run_row["max_active_ms"] == 900_000
    assert decimal.Decimal(run_row["budget_limit"]) == decimal.Decimal("1.00")
    assert run_row["currency"] == "USD"
    assert run_row["active_elapsed_ms"] == 0
    assert run_row["output_artifact_id"] is None
    assert run_row["input_artifact_id"]
    assert run_row["checkpoint_thread_id"]

    jobs = t027.jobs_for_run(t027_stack.dbname, run["id"])
    assert len(jobs) == 1, jobs
    assert jobs[0]["kind"] == "start"
    assert jobs[0]["state"] == "ready"
    assert jobs[0]["current_attempt_token"] is None
    assert jobs[0]["available_at"]

    # The canonical test input is a ready, sealed artifact: JCS bytes, real hash.
    input_artifact = t027.artifact_row(t027_stack.dbname, run_row["input_artifact_id"])
    canonical = t027.canonical_json({"text": "hello"})
    assert input_artifact["purpose"] == "run_input"
    assert input_artifact["owner_id"] == creator.principal_id
    assert input_artifact["state"] == "ready"
    assert input_artifact["revision"] == 1
    assert input_artifact["sha256"] == t027.sha256_hex(canonical)
    assert input_artifact["size_bytes"] == len(canonical)
    assert input_artifact["media_type"] == "application/json"
    assert input_artifact["deleted_at"] is None
    assert t027.object_bytes(t027_stack.object_dir, input_artifact["object_key"]) == canonical

    # The run links its inputs; the canonical input is the first link.
    links = t027.run_artifact_rows(t027_stack.dbname, run["id"])
    assert [link["artifact_id"] for link in links] == [run_row["input_artifact_id"]]
    assert links[0]["relation"] == "input"
    assert links[0]["attempt_token"] is None

    # Debug is not publication and not a request: no version, review, request,
    # conversation, background job or evaluation row was created.
    counts = t027.execution_rows(t027_stack.dbname, application_id)
    assert counts == {
        "snapshots": 1,
        "runs": 1,
        "jobs": 1,
        "versions": 0,
        "reviews": 0,
        "background_jobs": 0,
        "evaluations": 0,
    }, counts

    # The idempotency receipt stores the exact 202 body, same transaction.
    receipts = t027.idempotency_rows(t027_stack.dbname, t027.debug_path(application_id))
    assert len(receipts) == 1, receipts
    assert receipts[0]["method"] == "POST"
    assert receipts[0]["principal_id"] == creator.principal_id
    assert receipts[0]["response_code"] == 202
    assert len(receipts[0]["request_sha256"]) == 64
    stored = json.loads(receipts[0]["response_json"])
    assert stored == run, (stored, run)


def test_the_owner_can_read_the_queued_run(t027_stack) -> None:
    _admin, creator, application_id = owner(t027_stack)
    submitted = t027.submit_debug(creator, application_id)
    assert submitted.status == 202, submitted.text
    run = submitted.json

    read = t027.get_run(creator, run["id"])
    assert read.status == 200, read.text
    assert read.json == run
    assert read.header("X-Manifest-SHA256") == t027.GOLDEN_SHA256

    other = t027.invite_and_activate(
        t027_stack, t027.admin_session(t027_stack), "t027-reader-" + secrets.token_hex(4),
        roles=("creator",),
    )
    st.assert_error(t027.get_run(other, run["id"]), 404, "resource_not_found")


def test_extra_input_artifacts_are_linked_as_run_inputs(t027_stack) -> None:
    _admin, creator, application_id = owner(t027_stack)
    first = t027.seed_artifact(t027_stack.dbname, creator.principal_id, content=b'{"a":1}')
    second = t027.seed_artifact(t027_stack.dbname, creator.principal_id, content=b'{"b":2}')

    response = t027.submit_debug(creator, application_id, artifact_ids=[second, first])
    assert response.status == 202, response.text
    run_row = t027.run_row(t027_stack.dbname, response.json["id"])
    links = t027.run_artifact_rows(t027_stack.dbname, response.json["id"])
    linked = [link["artifact_id"] for link in links]
    assert run_row["input_artifact_id"] in linked
    assert first in linked and second in linked
    assert len(linked) == 3, linked
    assert {link["relation"] for link in links} == {"input"}
    assert {link["attempt_token"] for link in links} == {None}


def test_the_run_read_is_not_available_for_unknown_or_malformed_ids(t027_stack) -> None:
    _admin, creator, _application_id = owner(t027_stack)
    for run_id in ("not-a-uuid", t027.new_uuid()):
        st.assert_error(t027.get_run(creator, run_id), 404, "resource_not_found")
