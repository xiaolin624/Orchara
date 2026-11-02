"""T022 acceptance: claim, heartbeat, stale tokens, lease recovery and retries.

All lease timing is judged by the database clock. The test moves a lease into the
past with SQL instead of sleeping; the only bounded waits are for the *configured*
50 ms retry backoff to elapse (the base is an environment knob for exactly this
reason).
"""

from __future__ import annotations

import datetime as dt
import time

from support import f00b
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    migrated_database,
    t022_stack,
)

# Bounded polls, never a sleep that encodes the backoff itself.
CLAIM_TIMEOUT = 5.0


def claim_until_leased(stack, client, timeout: float = CLAIM_TIMEOUT) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.claim()
        last = response
        if response.status == 200:
            return response.json
        assert response.status == 204, response.text
        time.sleep(0.02)
    raise AssertionError(f"no claim succeeded within {timeout}s; last status {last.status if last else None}")


def prepare_job(stack, *, content=b"lease payload\n") -> tuple[str, f00b.SeededArtifact, dict]:
    owner = f00b.principal_id_for_login(stack.dbname)
    artifact = f00b.seed_artifact(stack.dbname, stack.object_dir, owner, content=content)
    payload = f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="lease:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    return owner, artifact, payload


def test_claim_leases_one_job_and_heartbeat_renews_it(t022_stack) -> None:
    _owner, artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()

    response = client.claim(worker_id="t022-lease-test")
    assert response.status == 200, response.text
    lease = response.json
    assert set(lease) == {
        "job_id", "kind", "target_id", "attempt_token", "lease_deadline",
        "expected_revision", "input_artifact_id", "input_url",
    }, lease
    assert lease["job_id"] == payload["id"]
    assert lease["kind"] == "artifact_finalize"
    assert lease["target_id"] == artifact.id
    assert lease["expected_revision"] == 1
    assert lease["input_artifact_id"] is None
    # T022 has no signed artifact URLs; T024 adds them. A null is the honest hole.
    assert lease["input_url"] is None
    deadline = dt.datetime.fromisoformat(lease["lease_deadline"].replace("Z", "+00:00"))
    assert deadline > dt.datetime.now(dt.timezone.utc), lease

    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["state"] == "leased"
    assert row["attempt_count"] == 1
    assert row["lease_token"] == lease["attempt_token"]
    assert row["worker_id"] == "t022-lease-test"
    first_deadline = row["lease_deadline"]

    heartbeat = client.heartbeat(payload["id"], lease["attempt_token"])
    assert heartbeat.status == 200, heartbeat.text
    assert heartbeat.json == {"id": payload["id"], "state": "leased"}
    renewed = f00b.job_row(t022_stack.dbname, payload["id"])
    assert renewed["lease_deadline"] > first_deadline
    assert renewed["lease_token"] == lease["attempt_token"]
    assert renewed["state"] == "leased"

    # A second claim cannot take the same job.
    assert client.claim().status == 204


def test_the_old_token_is_refused_after_the_lease_was_recovered(t022_stack) -> None:
    _owner, _artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()
    first = client.claim()
    assert first.status == 200, first.text
    first_token = first.json["attempt_token"]
    first_event = f00b.new_uuid()

    f00b.force_lease_expiry(t022_stack.dbname, payload["id"])

    second = claim_until_leased(t022_stack, client)
    assert second["job_id"] == payload["id"]
    assert second["attempt_token"] != first_token
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["attempt_count"] == 2
    assert row["state"] == "leased"

    stale = client.result(
        payload["id"], attempt_token=first_token, state="succeeded", event_id=first_event,
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt"
    unchanged = f00b.job_row(t022_stack.dbname, payload["id"])
    assert unchanged["attempt_count"] == 2
    assert unchanged["state"] == "leased"
    assert unchanged["result_event_id"] is None
    assert unchanged["finished_at"] is None
    stale_heartbeat = client.heartbeat(payload["id"], first_token)
    assert stale_heartbeat.status == 409
    assert stale_heartbeat.json["error"]["code"] == "stale_attempt"


def test_a_result_after_the_lease_expired_changes_nothing(t022_stack) -> None:
    _owner, artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()
    lease = client.claim().json
    f00b.force_lease_expiry(t022_stack.dbname, payload["id"])

    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="succeeded",
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "stale_attempt"
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["state"] == "leased"
    assert row["attempt_count"] == 1
    assert row["result_event_id"] is None
    assert row["finished_at"] is None
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "scanning"
    heartbeat = client.heartbeat(payload["id"], lease["attempt_token"])
    assert heartbeat.status == 409


def test_an_expired_lease_is_recovered_and_the_retry_succeeds(t022_stack) -> None:
    _owner, artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()
    assert client.claim().status == 200
    f00b.force_lease_expiry(t022_stack.dbname, payload["id"])

    # The first run only recovers: the job becomes ready with backoff, so this
    # claim finds nothing to hand out. It is restricted to the artifact_finalize
    # pool on purpose. With two pools, the other slot runs a second claim
    # transaction; if more than the 50ms backoff elapses between the two
    # transactions (the machine is loaded), that later claim legitimately finds
    # the job available and hands it out, which is the flake the review saw
    # (claimed=1 where 0 was expected). One slot = one claim transaction, and a
    # recovery made inside that transaction sets available_at = now()+backoff with
    # now() being the transaction timestamp, so the same transaction cannot also
    # claim it.
    _completed, reports = f00b.maintenance_once(t022_stack, "--kinds", "artifact_finalize")
    assert f00b.summary_of(reports)["claimed"] == 0
    recovered = f00b.job_row(t022_stack.dbname, payload["id"])
    assert recovered["state"] == "ready"
    assert recovered["error_code"] == "lease_expired"
    assert recovered["failure_class"] == "infrastructure"
    assert recovered["attempt_count"] == 1
    assert recovered["lease_token"] is None
    assert recovered["finished_at"] is None

    reports = retry_until_claimed(t022_stack)
    summary = f00b.summary_of(reports)
    assert summary["claimed"] == 1
    assert summary["succeeded"] == 1
    final = f00b.job_row(t022_stack.dbname, payload["id"])
    assert final["state"] == "succeeded"
    assert final["attempt_count"] == 2
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "ready"


def retry_until_claimed(stack, timeout: float = CLAIM_TIMEOUT) -> list[dict]:
    """Run --once until the short configured backoff has elapsed and a job runs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        _completed, reports = f00b.maintenance_once(stack)
        if f00b.summary_of(reports)["claimed"] > 0:
            return reports
        time.sleep(0.02)
    raise AssertionError(f"maintenance never claimed the retried job within {timeout}s")


def test_a_transient_failure_is_retried_then_fails_on_the_third_attempt(t022_stack) -> None:
    _owner, _artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()

    for attempt in (1, 2):
        lease = claim_until_leased(t022_stack, client)
        response = client.result(
            payload["id"], attempt_token=lease["attempt_token"], state="failed",
            error_code="transient_storage_io",
        )
        assert response.status == 200, response.text
        body = response.json
        assert body["state"] == "ready", body
        assert body["error_code"] == "transient_storage_io"
        row = f00b.job_row(t022_stack.dbname, payload["id"])
        assert row["attempt_count"] == attempt
        assert row["state"] == "ready"
        assert row["failure_class"] == "infrastructure"
        assert row["finished_at"] is None
        assert row["lease_token"] is None

    lease = claim_until_leased(t022_stack, client)
    final = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="transient_storage_io",
    )
    assert final.status == 200, final.text
    assert final.json["state"] == "failed"
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["state"] == "failed"
    assert row["attempt_count"] == 3
    assert row["failure_class"] == "infrastructure"
    assert row["error_code"] == "transient_storage_io"
    assert row["finished_at"] is not None
    # No fourth attempt exists.
    assert client.claim().status == 204


def test_a_business_failure_is_never_retried(t022_stack) -> None:
    _owner, artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()
    lease = client.claim().json

    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="source_deleted",
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["state"] == "failed"
    assert row["attempt_count"] == 1
    assert row["failure_class"] == "business"
    assert row["finished_at"] is not None
    assert f00b.artifact_row(t022_stack.dbname, artifact.id)["state"] == "scanning"
    assert client.claim().status == 204


def test_an_unknown_error_code_is_treated_as_business(t022_stack) -> None:
    _owner, _artifact, payload = prepare_job(t022_stack)
    client = t022_stack.internal_client()
    lease = client.claim().json
    response = client.result(
        payload["id"], attempt_token=lease["attempt_token"], state="failed",
        error_code="something_a_handler_invented",
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    row = f00b.job_row(t022_stack.dbname, payload["id"])
    assert row["failure_class"] == "business"
    assert row["attempt_count"] == 1


def test_claim_and_result_validation(t022_stack) -> None:
    client = t022_stack.internal_client()
    unknown = client.claim(kinds=["sh -c id"])
    assert unknown.status == 422, unknown.text
    assert unknown.json["error"]["code"] == "validation_failed"
    assert unknown.json["error"]["details"]["field"] == "supported_kinds"

    duplicate = client.claim(kinds=["artifact_finalize", "artifact_finalize"])
    assert duplicate.status == 422
    assert duplicate.json["error"]["details"]["field"] == "supported_kinds"

    empty_worker = client.post(
        "/internal/v1/background/jobs/claim",
        {"worker_id": " ", "supported_kinds": ["artifact_finalize"]},
    )
    assert empty_worker.status == 422
    assert empty_worker.json["error"]["details"]["field"] == "worker_id"

    missing = client.result(
        f00b.new_uuid(), attempt_token=f00b.new_uuid(), state="succeeded",
    )
    assert missing.status == 404
    assert missing.json["error"]["code"] == "job_not_found"

    malformed_job = client.heartbeat("not-a-uuid", f00b.new_uuid())
    assert malformed_job.status == 404
    assert malformed_job.json["error"]["code"] == "job_not_found"

    # A result needs its event id and, for failed, its error code.
    _owner, _artifact, payload = prepare_job(t022_stack)
    lease = client.claim().json
    missing_code = client.post(
        f"/internal/v1/background/jobs/{payload['id']}/result",
        {"attempt_token": lease["attempt_token"], "event_id": f00b.new_uuid(), "state": "failed"},
    )
    assert missing_code.status == 422
    assert missing_code.json["error"]["details"]["field"] == "error_code"
    no_event = client.post(
        f"/internal/v1/background/jobs/{payload['id']}/result",
        {"attempt_token": lease["attempt_token"], "state": "succeeded"},
    )
    assert no_event.status == 422
    assert no_event.json["error"]["details"]["field"] == "event_id"
    bad_token = client.post(
        f"/internal/v1/background/jobs/{payload['id']}/result",
        {"attempt_token": "nope", "event_id": f00b.new_uuid(), "state": "succeeded"},
    )
    assert bad_token.status == 422
    assert bad_token.json["error"]["details"]["field"] == "attempt_token"
