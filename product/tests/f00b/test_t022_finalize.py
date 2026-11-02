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
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)


def wait_for_available_at(dbname: str, job_id: str) -> None:
    """Wait on the database clock until a retry's backoff has elapsed."""
    f00b.wait_until(
        lambda: st.sql(
            dbname,
            "SELECT available_at <= now() FROM platform.background_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == "t",
        timeout=5.0,
        interval=0.01,
        what=f"the retry of job {job_id} to become available",
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
    # The attempt object the handler wrote is removed by the handler itself; the
    # active object_key was never written.
    assert not artifact.sealed_path(t022_stack.object_dir).exists()
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == set()
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
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == set()


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


def test_the_guard_rehashes_the_attempt_object_instead_of_trusting_the_worker(
    t022_stack,
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"good bytes"
    artifact = f00b.seed_artifact(
        t022_stack.dbname, t022_stack.object_dir, owner, content=content,
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-tamper:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    # A buggy or malicious worker writes its own attempt object with different
    # bytes of the same length and still claims success (same length so the guard
    # must reach the hash comparison, not merely notice a size difference).
    attempt_key = f00b.attempt_sealed_key(payload["id"], lease["attempt_token"])
    tampered = bytes([content[0] ^ 0xFF]) + content[1:]
    assert len(tampered) == len(content)
    f00b.write_object(t022_stack.object_dir, attempt_key, tampered)

    response = client.result(payload["id"], attempt_token=lease["attempt_token"], state="succeeded")
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "artifact_hash_mismatch"
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "quarantined"
    assert row["sha256"] is None
    job = f00b.job_row(t022_stack.dbname, payload["id"])
    assert job["failure_class"] == "business"
    # The guard removed only the attempt object it rejected.
    assert not (t022_stack.object_dir / attempt_key).exists()
    assert not artifact.sealed_path(t022_stack.object_dir).exists()


def test_a_missing_attempt_object_is_quarantined(t022_stack) -> None:
    # A claimed success with no bytes at this attempt's own key cannot be proven:
    # the guard never falls back to the artifact's active key or to another
    # attempt's object.
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
    )
    payload = f00b.enqueue_ok(
        t022_stack, kind="artifact_finalize", target_id=artifact.id, owner_id=owner,
        dedup_key="guard-event:" + artifact.id, expected_revision=1,
    )
    client = t022_stack.internal_client()
    lease = client.claim().json
    # The worker half: the attempt writes its own object before reporting.
    attempt_key = f00b.attempt_sealed_key(payload["id"], lease["attempt_token"])
    f00b.write_object(t022_stack.object_dir, attempt_key, content)
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
    # Activation moved the verified attempt object to the active key: exactly one
    # sealed object exists and it is the artifact's own key.
    assert not (t022_stack.object_dir / attempt_key).exists()
    assert (t022_stack.object_dir / artifact.object_key).read_bytes() == content
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == {artifact.object_key}

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


def test_a_failed_attempt_never_deletes_an_object_already_sealed_at_the_active_key(
    t022_stack,
) -> None:
    """The fence: a resumed attempt can only remove its own object.

    This is the regression the second review found. The old handler wrote straight
    to ``artifact.object_key`` and, on a hash mismatch, deleted that key. Here an
    object is already sealed at the active key (the shape left by an activation
    that beat this attempt); a real finalize attempt then fails its hash check.
    The active bytes must survive byte for byte, and the attempt's own temporary
    object must be the only thing removed.
    """
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"staging bytes from the stale attempt"
    active_bytes = b"bytes sealed by the newer activation"
    wrong_hash = hashlib.sha256(b"what the uploader claimed").hexdigest()
    artifact = f00b.seed_artifact(
        t022_stack.dbname,
        t022_stack.object_dir,
        owner,
        content=content,
        sealed_content=active_bytes,
        expected_sha256=wrong_hash,
    )
    payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="fenced-active:" + artifact.id,
        expected_revision=1,
        expected_sha256=wrong_hash,
    )

    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed", report
    assert report["error_code"] == "artifact_hash_mismatch", report
    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "quarantined"
    assert row["sha256"] is None
    # The active key still holds the bytes written by the other activation.
    assert (t022_stack.object_dir / artifact.object_key).read_bytes() == active_bytes
    # Only the active key exists: the failed attempt removed only its own object.
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == {artifact.object_key}


def test_a_successful_completion_moves_the_attempt_object_and_leaves_no_attempt_key(
    t022_stack,
) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"bytes that must be moved into the active key"
    artifact = f00b.seed_artifact(t022_stack.dbname, t022_stack.object_dir, owner, content=content)
    payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="fenced-move:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )

    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    report = f00b.job_reports(reports)[0]
    assert report["job_id"] == payload["id"], report
    assert report["result"] == "succeeded", report

    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "ready"
    assert row["sha256"] == artifact.expected_sha256
    assert (t022_stack.object_dir / artifact.object_key).read_bytes() == content
    # The guard moved the attempt object: no per-attempt sealed key is left.
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == {artifact.object_key}


def test_a_resumed_old_attempt_cannot_corrupt_the_activated_object(t022_stack) -> None:
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"the retried attempt's bytes"
    artifact = f00b.seed_artifact(t022_stack.dbname, t022_stack.object_dir, owner, content=content)
    payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="fenced-stale:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    client = t022_stack.internal_client()
    first = client.claim()
    assert first.status == 200, first.text
    old_token = first.json["attempt_token"]

    # The old attempt is now paused. Its lease expires, the retry runs for real
    # and activates the artifact.
    f00b.force_lease_expiry(t022_stack.dbname, payload["id"])
    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    assert f00b.summary_of(reports)["claimed"] == 0, "the first drain only recovers"
    recovered = f00b.job_row(t022_stack.dbname, payload["id"])
    assert recovered["state"] == "ready"
    assert recovered["error_code"] == "lease_expired"
    wait_for_available_at(t022_stack.dbname, payload["id"])
    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    assert f00b.summary_of(reports)["succeeded"] == 1, reports

    activated = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert activated["state"] == "ready"
    active_bytes = (t022_stack.object_dir / artifact.object_key).read_bytes()
    assert active_bytes == content

    # The old attempt resumes and writes its own object, then reports failure
    # with its now-stale token. The job is already terminal, so the server refuses
    # the late event without reaching the guard; nothing it did may reach the
    # active object.
    old_key = f00b.attempt_sealed_key(payload["id"], old_token)
    stale_bytes = b"stale bytes written by the resumed attempt"
    f00b.write_object(t022_stack.object_dir, old_key, stale_bytes)
    response = client.result(
        payload["id"], attempt_token=old_token, state="failed", error_code="transient_storage_io",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "event_conflict"

    after = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert after["state"] == "ready"
    assert after["sha256"] == activated["sha256"]
    assert after["revision"] == activated["revision"]
    assert (t022_stack.object_dir / artifact.object_key).read_bytes() == active_bytes
    # The stale bytes exist only as that attempt's own fenced object.
    assert old_key != artifact.object_key
    assert (t022_stack.object_dir / old_key).read_bytes() == stale_bytes


def test_a_stale_completion_deletes_only_its_own_attempt_key(t022_stack) -> None:
    """Two live attempts on one artifact: the later activation wins.

    The first job's token is still a live lease, so its completion reaches the
    guard. The guard sees the artifact is no longer scanning, deletes only that
    attempt's own object and leaves the activated bytes untouched.
    """
    owner = f00b.principal_id_for_login(t022_stack.dbname)
    content = b"the attempt that wins"
    artifact = f00b.seed_artifact(t022_stack.dbname, t022_stack.object_dir, owner, content=content)
    first_payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="fenced-race-a:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    second_payload = f00b.enqueue_ok(
        t022_stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="fenced-race-b:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    client = t022_stack.internal_client()
    first = client.claim()
    second = client.claim()
    assert first.status == 200 and second.status == 200
    assert {first.json["job_id"], second.json["job_id"]} == {
        first_payload["id"],
        second_payload["id"],
    }
    winning, winning_token = first.json["job_id"], first.json["attempt_token"]
    losing, losing_token = second.json["job_id"], second.json["attempt_token"]
    if winning != first_payload["id"]:
        winning, losing = losing, winning
        winning_token, losing_token = losing_token, winning_token

    winning_key = f00b.attempt_sealed_key(winning, winning_token)
    losing_key = f00b.attempt_sealed_key(losing, losing_token)
    f00b.write_object(t022_stack.object_dir, winning_key, content)
    f00b.write_object(t022_stack.object_dir, losing_key, b"the losing attempt's bytes")

    activated = client.result(winning, attempt_token=winning_token, state="succeeded")
    assert activated.status == 200, activated.text
    assert activated.json["state"] == "succeeded", activated.json

    rejected = client.result(losing, attempt_token=losing_token, state="succeeded")
    assert rejected.status == 200, rejected.text
    assert rejected.json["state"] == "failed", rejected.json
    assert rejected.json["error_code"] == "artifact_state_not_scanning", rejected.json

    row = f00b.artifact_row(t022_stack.dbname, artifact.id)
    assert row["state"] == "ready"
    assert row["sha256"] == artifact.expected_sha256
    # The guard removed only the losing attempt's object; the active bytes are the
    # winner's verified bytes.
    assert not (t022_stack.object_dir / losing_key).exists()
    assert (t022_stack.object_dir / artifact.object_key).read_bytes() == content
    assert f00b.object_keys(t022_stack.object_dir, "sealed/") == {artifact.object_key}
