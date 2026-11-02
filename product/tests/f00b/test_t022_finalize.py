"""T022 acceptance: artifact_finalize handler half and server-side guard half.

The handler copies bytes and reports; only the server's completion guard can
activate an artifact. Each test attacks one of the guard's facts: hash, size,
tombstone, source revision, sealed-object presence and the "still scanning"
state. A claimed success that cannot be proven must never produce a ready
artifact.
"""

from __future__ import annotations

import hashlib

from support import f00b
from support.certs import make_stack_certificates, unique_internal_addr
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def prepare(stack, *, content=b"finalize bytes\n", **seed_kwargs):
    owner = f00b.principal_id_for_login(stack.dbname)
    artifact = f00b.seed_artifact(
        stack.dbname, stack.object_dir, owner, content=content, **seed_kwargs
    )
    payload = f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="guard:" + artifact.id,
        expected_revision=seed_kwargs.get("revision", 1),
        expected_sha256=artifact.expected_sha256,
    )
    return owner, artifact, payload


def test_a_hash_mismatch_is_quarantined_by_the_real_handler(t022_stack) -> None:
    content = b"the bytes that arrived"
    _owner, artifact, payload = prepare(
        t022_stack,
        content=content,
        expected_sha256=hashlib.sha256(b"what the uploader claimed").hexdigest(),
    )
    _completed, reports = f00b.maintenance_once(t022_stack)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed"
    assert report["error_code"] == "artifact_hash_mismatch"

    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    assert job["attempt_count"] == 1
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "quarantined"
    assert int(row["revision"]) == 2
    assert row["sha256"] is None
    # The unusable sealed copy is removed; the staging object is left for the
    # orphan sweep.
    assert not artifact.sealed_path(t022_stack.object_dir).exists()
    assert artifact.staging_path(t022_stack.object_dir).exists()


def test_a_size_mismatch_is_quarantined(t022_stack) -> None:
    content = b"size matters"
    _owner, artifact, payload = prepare(
        t022_stack, content=content, size_bytes=len(content) + 7
    )
    _completed, reports = f00b.maintenance_once(t022_stack)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed"
    assert report["error_code"] == "artifact_size_mismatch"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "quarantined"
    assert not artifact.sealed_path(t022_stack.object_dir).exists()


def test_a_deleted_artifact_can_never_be_activated(t022_stack) -> None:
    _owner, artifact, payload = prepare(t022_stack)
    f00b.delete_artifact_row(t022_stack.dbname, artifact.id)

    _completed, reports = f00b.maintenance_once(t022_stack)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed"
    assert report["error_code"] == "source_deleted"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "deleted"
    assert row["sha256"] is None
    assert not artifact.sealed_path(t022_stack.object_dir).exists()


def test_a_revision_change_after_the_claim_cannot_activate(t022_stack) -> None:
    _owner, artifact, payload = prepare(t022_stack)
    # The source moved on (another state change) after the job was enqueued.
    f00b.set_artifact_state(t022_stack.dbname, artifact.id, "scanning")

    _completed, reports = f00b.maintenance_once(t022_stack)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed"
    assert report["error_code"] == "source_changed"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "scanning"
    assert row["sha256"] is None
    assert not artifact.sealed_path(t022_stack.object_dir).exists()


def test_a_successful_claim_after_a_tombstone_is_refused_by_the_guard(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"claimed success after delete"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
        sealed_content=content,
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-tombstone:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    f00b.delete_artifact_row(t022_stack.dbname, artifact.id)

    response = client.result(payload["id"], attempt_token=lease["attempt_token"], state="succeeded")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "source_deleted"
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "deleted"
    assert row["sha256"] is None


def test_the_guard_rehashes_the_sealed_object_instead_of_trusting_the_worker(
    t022_stack,
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"good bytes"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
        sealed_content=content,
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-tamper:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    # A buggy or malicious worker overwrites the sealed object with different
    # bytes of the same length and still claims success (same length so the guard
    # must reach the hash comparison, not merely notice a size difference).
    tampered = bytes([content[0] ^ 0xFF]) + content[1:]
    assert len(tampered) == len(content)
    f00b.write_object(t022_stack.object_dir, artifact.object_key, tampered)

    response = client.result(payload["id"], attempt_token=lease["attempt_token"], state="succeeded")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "artifact_hash_mismatch"
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "quarantined"
    assert row["sha256"] is None
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["failure_class"] == "business"


def test_a_missing_sealed_object_is_quarantined(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    artifact = f00b.seed_artifact(t022_stack.dbname, t022_stack.object_dir, owner)
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-missing:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    response = client.result(payload["id"], attempt_token=lease["attempt_token"], state="succeeded")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "artifact_object_missing"
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "quarantined"


def test_an_already_ready_artifact_is_not_activated_twice(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"already ready"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
        state="ready", revision=1,
    )
    before = f00b.artifact_row(t022_stack.dbname, artifact.id)
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-ready:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    response = client.result(payload["id"], attempt_token=lease["attempt_token"], state="succeeded")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "artifact_state_not_scanning"
    after = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert after["state"] == "ready"
    assert after["revision"] == before["revision"]
    assert after["sha256"] == before["sha256"]


def test_a_quarantine_code_submitted_by_a_worker_quarantines_the_artifact(t022_stack) -> None:
    _owner, artifact, payload = prepare(t022_stack)
    client = t022_stack.internal_client()
    lease = client.claim().json
    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="artifact_hash_mismatch",
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "quarantined"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["failure_class"] == "business"
    assert job["attempt_count"] == 1


def test_a_duplicate_result_event_is_idempotent_and_a_different_one_conflicts(
    t022_stack,
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"duplicate event bytes"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
        sealed_content=content,
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-event:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    event = f00b.new_uuid()

    first = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded", event_id=event,
    )
    assert first.status == 200, first.text
    assert first.json["state"] == "succeeded"
    recorded = f00b.job_row(t022_stack.dbname, payload["id"])
    assert recorded["result_event_id"] == event
    assert recorded["result_event_digest"] is not None
    activated = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert activated["state"] == "ready"

    replay = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded", event_id=event,
    )
    assert replay.status == 200, replay.text
    assert replay.json == first.json
    unchanged = f00b.job_row(t022_stack.dbname, payload["id"])
    assert unchanged["finished_at"] == recorded["finished_at"]
    assert unchanged["attempt_count"] == recorded["attempt_count"]
    assert unchanged["result_event_digest"] == recorded["result_event_digest"]
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["revision"] == activated["revision"]

    # Same event id, different content: a conflict, not a replay.
    different_content = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="source_deleted", event_id=event,
    )
    assert different_content.status == 409, different_content.text
    assert different_content.json["error"]["code"] == "event_conflict"

    # A second, different terminal event: also a conflict.
    different_event = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded",
    )
    assert different_event.status == 409
    assert different_event.json["error"]["code"] == "event_conflict"
    final = f00b.job_row(t022_stack.dbname, payload["id"])
    assert final["result_event_id"] == event
    assert final["state"] == "succeeded"


def test_an_artifact_result_cannot_carry_a_worker_chosen_result_artifact(t022_stack) -> None:
    _owner, artifact, payload = prepare(t022_stack)
    client = t022_stack.internal_client()
    lease = client.claim().json
    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded",
        result_artifact_id=artifact.id,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "validation_failed"
    assert response.json["error"]["details"]["field"] == "result_artifact_id"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "leased"
    assert job["result_event_id"] is None
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "scanning"


def test_a_server_without_an_object_store_refuses_to_activate(
    t022_stack, tmp_path, api_server
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"no store here"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
        sealed_content=content,
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-nostore:" + artifact.id, expected_revision=1,
    )
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs-nostore", internal_addr)
    server = api_server(
        t022_stack.dsn,
        extra_env={
            "ORCHARA_INTERNAL_ADDR": internal_addr,
            "ORCHARA_INTERNAL_SERVER_CERT": str(certificates.server.cert),
            "ORCHARA_INTERNAL_SERVER_KEY": str(certificates.server.key),
            "ORCHARA_INTERNAL_CLIENT_CA": str(certificates.ca.cert),
        },
    )
    client = f00b.InternalClient(
        certificates.internal_url, certificates.ca.cert, certificates.client("maintenance")
    )
    f00b.wait_internal(
        certificates.internal_url,
        certificates.ca.cert,
        certificates.client("maintenance"),
        logs=lambda: server.logs(),
    )
    lease = client.claim().json
    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded",
    )
    assert response.status == 503, response.text
    assert response.json["error"]["code"] == "object_store_unavailable"
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["state"] == "leased"
    assert job["result_event_id"] is None
    assert job["finished_at"] is None
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "scanning"
