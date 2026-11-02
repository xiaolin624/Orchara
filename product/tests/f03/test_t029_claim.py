"""T029 acceptance: a supervisor claims exactly one compatible ready job.

The job is created by the real T027 ``debugDraft`` endpoint over a real golden
draft; the claim is a real mTLS request; the sealed manifest/input bytes are read
back through the real signed GET links. Every lease field is checked against both
the response and PostgreSQL.

``X-Run-Lease-Deadline`` on GET /api/v1/runs/{id} is the non-contract extension
that lets an owner see the real lease (the Run schema has no field for it; ADR
0004 §42 documents the same precedent for X-Manifest-SHA256).
"""

from __future__ import annotations

import hashlib
import json

from support import stack as st
from support import t025, t027, t029
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)

GOLDEN_JCS = (t025.SNAPSHOTS / "sales" / "manifest.jcs").read_bytes()


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def epoch(value: str) -> float:
    return t029.lease_deadline_epoch(value)


def test_a_claim_returns_the_real_lease_and_starts_the_run(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)
    job = t029.jobs_for_run(t029_stack.dbname, run["id"])
    assert len(job) == 1 and job[0]["state"] == "ready", job

    response = t029.execution_client(t029_stack, "supervisor").claim_job(
        worker_id="t029-supervisor-1", execution_unit_id="unit-alpha"
    )
    assert response.status == 200, response.text
    lease = response.json
    t029.assert_lease(lease)

    # --- the contract Lease, field by field --------------------------------
    assert lease["job_id"] == job[0]["id"], lease
    assert lease["run_id"] == run["id"], lease
    assert lease["job_kind"] == "start", lease
    assert lease["execution_snapshot_id"] == run["execution_snapshot_id"], lease
    assert lease["manifest_sha256"] == t029.GOLDEN_SHA256, lease
    assert lease["manifest"] == t029.snapshot_row(
        t029_stack.dbname, run["execution_snapshot_id"]
    )["manifest"], "the inline manifest must be the frozen JSONB projection"
    assert lease["resume_artifact_id"] is None, lease
    assert lease["resume_url"] is None, lease

    # --- the attempt row ----------------------------------------------------
    attempt = t029.attempt_row(t029_stack.dbname, lease["attempt_token"])
    assert attempt["job_id"] == job[0]["id"]
    assert attempt["run_id"] == run["id"]
    assert attempt["worker_id"] == "t029-supervisor-1"
    assert attempt["execution_unit_id"] == "unit-alpha"
    assert attempt["exited_at"] is None and attempt["revoked_at"] is None
    assert json.loads(attempt["checkpoint_connections"]) == []
    assert abs(epoch(attempt["lease_deadline"]) - epoch(lease["lease_deadline"])) < 0.001, (
        attempt["lease_deadline"],
        lease["lease_deadline"],
    )
    assert epoch(attempt["heartbeat_at"]) > 0
    assert epoch(attempt["started_at"]) > 0

    # --- job and run state --------------------------------------------------
    stored_job = t029.job_row(t029_stack.dbname, job[0]["id"])
    assert stored_job["state"] == "leased", stored_job
    assert stored_job["current_attempt_token"] == lease["attempt_token"], stored_job

    stored_run = t029.run_state_row(t029_stack.dbname, run["id"])
    assert stored_run["state"] == "running", stored_run
    assert stored_run["phase"] == "executing", stored_run
    # Claiming is exactly one aggregate transition: revision 1 -> 2.
    assert stored_run["revision"] == 2, stored_run
    assert stored_run["started_at"] is not None, stored_run

    # --- quota: one allocation, one used slot ------------------------------
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1
    allocations = t029.quota_allocations(t029_stack.dbname)
    assert len(allocations) == 1, allocations
    assert allocations[0]["holder_key"] == "job:" + job[0]["id"], allocations
    assert allocations[0]["released_at"] is None, allocations

    # --- real sealed links --------------------------------------------------
    manifest_bytes = t029.download(lease["manifest_url"])
    assert manifest_bytes == GOLDEN_JCS, "manifest_url did not serve the sealed golden bytes"
    assert sha256_hex(manifest_bytes) == lease["manifest_sha256"]

    snapshot = t029.snapshot_row(t029_stack.dbname, run["execution_snapshot_id"])
    manifest_artifact = t029.artifact_row(t029_stack.dbname, snapshot["manifest_artifact_id"])
    assert manifest_bytes == t029.object_bytes(t029_stack.object_dir, manifest_artifact["object_key"])

    input_artifact = t029.artifact_row(t029_stack.dbname, lease["input_artifact_id"])
    input_bytes = t029.download(lease["input_url"])
    assert input_bytes == t029.object_bytes(t029_stack.object_dir, input_artifact["object_key"])
    assert sha256_hex(input_bytes) == input_artifact["sha256"]
    assert input_bytes == t027.canonical_json({"text": "hello"})

    # --- the owner sees the real lease, not a fake progress bar -------------
    detail = owner.creator.client.get(t029.run_path(run["id"]))
    assert detail.status == 200, detail.text
    assert detail.json["state"] == "running" and detail.json["phase"] == "executing", detail.json
    deadline = detail.header("X-Run-Lease-Deadline")
    assert deadline, dict(detail.headers)
    assert abs(epoch(deadline) - epoch(attempt["lease_deadline"])) < 0.001, deadline


def test_a_claim_without_a_compatible_compiler_is_a_204_and_changes_nothing(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)

    response = t029.execution_client(t029_stack).claim_job(
        supported_compilers=["orchara-compiler/9.9.9"]
    )
    assert response.status == 204, response.text
    assert response.raw == b"", response.raw

    assert t029.attempt_count(t029_stack.dbname) == 0
    job = t029.job_row(t029_stack.dbname, t029.jobs_for_run(t029_stack.dbname, run["id"])[0]["id"])
    assert job["state"] == "ready" and job["current_attempt_token"] is None, job
    stored_run = t029.run_state_row(t029_stack.dbname, run["id"])
    assert stored_run["state"] == "queued" and stored_run["revision"] == 1, stored_run
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0
    assert t029.quota_allocations(t029_stack.dbname) == []


def test_an_already_leased_job_is_never_handed_out_twice(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    first = t029.claim_lease(t029_stack, owner, execution_unit_id="unit-1")

    second = t029.execution_client(t029_stack, "supervisor").claim_job(
        worker_id="t029-supervisor-2", execution_unit_id="unit-2"
    )
    assert second.status == 204, second.text
    assert t029.attempt_count(t029_stack.dbname) == 1
    stored = t029.attempt_row(t029_stack.dbname, first["attempt_token"])
    assert stored["execution_unit_id"] == "unit-1", stored
    assert t029.quota_counter(t029_stack.dbname)["used"] == 1


def test_a_worker_or_maintenance_certificate_cannot_claim(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    run = t029.queued_run(t029_stack, owner)

    for role in ("worker", "maintenance"):
        response = t029.execution_client(t029_stack, role).claim_job()
        assert response.status == 403, (role, response.text)
        assert response.json["error"]["code"] == "forbidden", response.text
        # Even with a perfectly lease-able job nothing changed.
        assert t029.attempt_count(t029_stack.dbname) == 0
        job = t029.job_row(
            t029_stack.dbname, t029.jobs_for_run(t029_stack.dbname, run["id"])[0]["id"]
        )
        assert job["state"] == "ready", job
        assert t029.quota_counter(t029_stack.dbname)["used"] == 0


def test_the_claim_body_is_validated_before_any_row_changes(t029_stack) -> None:
    owner = t029.golden_owner(t029_stack)
    _run = t029.queued_run(t029_stack, owner)
    client = t029.execution_client(t029_stack)

    valid = {
        "worker_id": "w",
        "supported_compilers": [t029.COMPILER_VERSION],
        "execution_unit_id": "u",
    }
    cases = [
        ("worker_id", {**valid, "worker_id": "   "}),
        ("worker_id", {**valid, "worker_id": ""}),
        ("supported_compilers", {**valid, "supported_compilers": []}),
        ("supported_compilers", {**valid, "supported_compilers": ["", ""]}),
        ("supported_compilers", {**valid, "supported_compilers": ["a", "a"]}),
        ("execution_unit_id", {**valid, "execution_unit_id": " "}),
        ("execution_unit_id", {k: v for k, v in valid.items() if k != "execution_unit_id"}),
    ]
    for field, body in cases:
        response = client.claim_job(body=body)
        assert response.status == 422, (field, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", (field, error)
        assert error.get("details", {}).get("field") == field, (field, error)

    unknown = client.claim_job(body={**valid, "unexpected": True})
    assert unknown.status == 400, unknown.text
    assert unknown.json["error"]["code"] == "invalid_json", unknown.text

    assert t029.attempt_count(t029_stack.dbname) == 0
    assert t029.quota_counter(t029_stack.dbname)["used"] == 0


def test_the_public_listener_does_not_serve_the_internal_execution_paths(t029_stack) -> None:
    public = t029_stack.client()
    for path, body in (
        (
            "/internal/v1/jobs/claim",
            {
                "worker_id": "w",
                "supported_compilers": [t029.COMPILER_VERSION],
                "execution_unit_id": "u",
            },
        ),
        ("/internal/v1/runs/" + t029.new_uuid() + "/heartbeat", {"attempt_token": t029.new_uuid()}),
        ("/internal/v1/attempts/" + t029.new_uuid() + "/exited", {}),
    ):
        response = public.post(path, body=body, headers={"X-Internal-Role": "supervisor"})
        assert response.status == 404, (path, response.text)
