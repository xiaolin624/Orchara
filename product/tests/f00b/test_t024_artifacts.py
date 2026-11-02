"""T024 acceptance: job-scoped artifact uploads, finalize and downloads.

Every byte here moves over real HTTP: the internal mTLS endpoint reserves the
artifact and mints a signed PUT link, a plain HTTP PUT fills the staging object,
``complete`` moves the row to scanning and creates the artifact_finalize job in
the same transaction, the real maintenance binary finalizes it, and a signed GET
returns exactly the uploaded bytes. Nothing is mocked and no row is written by
the test to fake a transition.
"""

from __future__ import annotations

import subprocess
import time

from support import f00b, t024
from support.fixtures import (  # noqa: F401 - pytest fixtures
    t024_stack,
)

CONTENT = b'{"report":"compatibility","ok":true}\n'


def upload(stack, lease: dict, content: bytes = CONTENT, **kwargs):
    return stack.internal_client().upload(
        lease["job_id"],
        lease["attempt_token"],
        media_type=kwargs.get("media_type", "application/json"),
        size_bytes=kwargs.get("size_bytes", len(content)),
        purpose=kwargs.get("purpose", "report"),
    )


def complete(stack, lease: dict, artifact_id: str, sha: str):
    return stack.internal_client().complete(
        lease["job_id"], artifact_id, lease["attempt_token"], sha
    )


def status(stack, lease: dict, artifact_id: str):
    return stack.internal_client().artifact_status(
        lease["job_id"], lease["attempt_token"], artifact_id
    )


def download(stack, lease: dict, artifact_id: str):
    return stack.internal_client().download(
        lease["job_id"], lease["attempt_token"], artifact_id
    )


def test_upload_complete_finalize_download_round_trip(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    sha = t024.sha256_hex(CONTENT)

    ticket_response = upload(t024_stack, job.lease)
    assert ticket_response.status == 201, ticket_response.text
    ticket = ticket_response.json
    assert set(ticket) == {"artifact_id", "upload_url", "expires_at"}, ticket
    assert ticket["upload_url"].startswith(t024_stack.server.base_url + "/object-links/"), ticket

    # The signed PUT needs no session: it is the token that authorizes the write.
    put = t024.put_link(ticket["upload_url"], CONTENT)
    assert put.status == 204, (put.status, put.raw)

    completed = complete(t024_stack, job.lease, ticket["artifact_id"], sha)
    assert completed.status == 202, completed.text
    assert completed.json == {
        "id": ticket["artifact_id"],
        "state": "scanning",
        "media_type": "application/json",
        "size_bytes": len(CONTENT),
    }, completed.json

    # The real finalize handler runs in a real process with a real lease.
    finalize_job = f00b.job_count(t024_stack.dbname, kind=t024.KIND_ARTIFACT_FINALIZE)
    assert finalize_job == 1
    _done, reports = f00b.maintenance_once(t024_stack, "--kinds", t024.KIND_ARTIFACT_FINALIZE)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "succeeded", report
    assert report["kind"] == t024.KIND_ARTIFACT_FINALIZE

    state = status(t024_stack, job.lease, ticket["artifact_id"])
    assert state.status == 200, state.text
    assert state.json["state"] == "ready", state.json
    assert state.json["size_bytes"] == len(CONTENT)

    row = f00b.artifact_row(t024_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "ready"
    assert row["sha256"] == sha
    assert int(row["size_bytes"]) == len(CONTENT)
    assert row["staging_object_key"].startswith("staging/")
    assert row["object_key"].startswith("sealed/")

    link = download(t024_stack, job.lease, ticket["artifact_id"])
    assert link.status == 200, link.text
    fetched = t024.get_link(link.json["url"])
    assert fetched.status == 200, (fetched.status, fetched.raw)
    assert fetched.raw == CONTENT
    assert fetched.headers["Content-Length"] == str(len(CONTENT))


def test_a_dropped_wakeup_never_loses_the_finalize_work(t024_stack) -> None:
    """The wakeup seam is advisory: the API drops every notification.

    The API installs an in-process notifier with no consumer, so the
    artifact_finalize job created by ``complete`` is announced to nobody (the
    real maintenance process is a different process). A maintenance process that
    was already polling PostgreSQL must still claim, finalize and activate the
    artifact, and the API log must show the seam ran and the wakeup was dropped.

    This is T022 acceptance 1 (``Redis 清空仍能从 PG 轮询推进``) exercised at the
    seam level: no Redis instance exists on this host, so the test does not claim
    one was cleared; it proves that losing every wakeup cannot lose the work.
    """
    job = t024.prepare_model_validate_job(t024_stack)
    finalizer = subprocess.Popen(
        [
            str(t024_stack.binaries["maintenance"]),
            "run",
            "--kinds",
            t024.KIND_ARTIFACT_FINALIZE,
            "--poll-interval",
            "20ms",
            "--worker-id",
            "t024-wakeup-dropped",
        ],
        cwd=f00b.PRODUCT_ROOT,
        env=f00b.maintenance_env(t024_stack),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        # The poller must already be alive before the job exists, otherwise
        # "polling claimed it" would be indistinguishable from a startup scan.
        time.sleep(0.2)
        assert finalizer.poll() is None, "the poller exited during startup"
        ticket = upload(t024_stack, job.lease).json
        assert t024.put_link(ticket["upload_url"], CONTENT).status == 204
        assert complete(t024_stack, job.lease, ticket["artifact_id"], t024.sha256_hex(CONTENT)).status == 202
        f00b.wait_until(
            lambda: f00b.artifact_row(t024_stack.dbname, ticket["artifact_id"])["state"] == "ready",
            timeout=20.0,
            what="the poller to finalize the artifact after a dropped wakeup",
        )
    finally:
        finalizer.terminate()
        try:
            _stdout, stderr = finalizer.communicate(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            finalizer.kill()
            _stdout, stderr = finalizer.communicate(timeout=5)

    assert "run: worker=t024-wakeup-dropped" in stderr, stderr
    logs = t024_stack.server.logs()
    assert "wakeup: no notification transport is configured" in logs, logs[-3000:]
    assert "the wakeup was dropped" in logs, logs[-3000:]

    row = f00b.artifact_row(t024_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "ready"
    assert row["sha256"] == t024.sha256_hex(CONTENT)


def test_upload_validation_rejects_bad_purpose_size_media_and_state(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)

    bad_purpose = upload(t024_stack, job.lease, purpose="secrets")
    assert bad_purpose.status == 422, bad_purpose.text
    assert bad_purpose.json["error"]["code"] == "validation_failed"
    assert bad_purpose.json["error"]["details"]["field"] == "purpose"

    bad_size = upload(t024_stack, job.lease, size_bytes=t024.MAX_OBJECT_BYTES + 1)
    assert bad_size.status == 422, bad_size.text
    assert bad_size.json["error"]["details"]["field"] == "size_bytes"

    bad_media = upload(t024_stack, job.lease, media_type="")
    assert bad_media.status == 422, bad_media.text
    assert bad_media.json["error"]["details"]["field"] == "media_type"

    stale = t024_stack.internal_client().upload(job.job_id, f00b.new_uuid(), size_bytes=8)
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "stale_attempt"

    # A kind that may not create artifacts is refused even with a live token.
    owner = t024.owner_id(t024_stack.dbname)
    artifact = f00b.seed_artifact(
        t024_stack.dbname, t024_stack.object_dir, owner, state="scanning"
    )
    lease = t024.enqueue_and_claim_kind(
        t024_stack,
        t024.KIND_ARTIFACT_FINALIZE,
        target_id=artifact.id,
        owner_id=owner,
        dedup_key="t024-upload-not-allowed:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    not_allowed = t024_stack.internal_client().upload(lease["job_id"], lease["attempt_token"], size_bytes=4)
    assert not_allowed.status == 422, not_allowed.text
    assert not_allowed.json["error"]["code"] == "validation_failed"
    assert not_allowed.json["error"]["details"]["field"] == "job_id"


def test_complete_is_idempotent_per_hash_and_conflicts_on_a_different_hash(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    sha = t024.sha256_hex(CONTENT)
    ticket = upload(t024_stack, job.lease).json
    assert t024.put_link(ticket["upload_url"], CONTENT).status == 204

    first = complete(t024_stack, job.lease, ticket["artifact_id"], sha)
    assert first.status == 202, first.text
    second = complete(t024_stack, job.lease, ticket["artifact_id"], sha)
    assert second.status == 202, second.text
    assert second.json["state"] == "scanning"
    # Same artifact + same hash is one intention: exactly one finalize job.
    assert f00b.job_count(t024_stack.dbname, kind=t024.KIND_ARTIFACT_FINALIZE) == 1

    different = complete(t024_stack, job.lease, ticket["artifact_id"], "0" * 64)
    assert different.status == 409, different.text
    assert different.json["error"]["code"] == "artifact_conflict"
    assert f00b.job_count(t024_stack.dbname, kind=t024.KIND_ARTIFACT_FINALIZE) == 1

    bad_sha = complete(t024_stack, job.lease, ticket["artifact_id"], "not-a-hash")
    assert bad_sha.status == 422, bad_sha.text
    assert bad_sha.json["error"]["details"]["field"] == "sha256"

    malformed = complete(t024_stack, job.lease, "not-a-uuid", sha)
    assert malformed.status == 404, malformed.text
    assert malformed.json["error"]["code"] == "artifact_not_found"

    # A different job's artifact is not in scope and is indistinguishable from a
    # missing one.
    other = f00b.seed_artifact(
        t024_stack.dbname,
        t024_stack.object_dir,
        job.owner_id,
        state="scanning",
        staging=False,
        sealed_content=b"other",
        content=b"other",
    )
    foreign = complete(t024_stack, job.lease, other.id, t024.sha256_hex(b"other"))
    assert foreign.status == 404, foreign.text
    assert foreign.json["error"]["code"] == "artifact_not_found"


def test_artifact_scope_is_job_scoped(t024_stack) -> None:
    first = t024.prepare_model_validate_job(t024_stack, dedup_key="t024-scope-a")
    second = t024.prepare_model_validate_job(t024_stack, dedup_key="t024-scope-b")

    first_ticket = upload(t024_stack, first.lease).json
    second_ticket = upload(t024_stack, second.lease).json

    # Each job sees its own reserved artifact.
    assert status(t024_stack, first.lease, first_ticket["artifact_id"]).status == 200
    cross = status(t024_stack, first.lease, second_ticket["artifact_id"])
    assert cross.status == 404, cross.text
    assert cross.json["error"]["code"] == "artifact_not_found"
    cross_download = download(t024_stack, first.lease, second_ticket["artifact_id"])
    assert cross_download.status == 404, cross_download.text

    # An unrelated user's ready artifact is equally invisible.
    stranger = f00b.seed_principal(t024_stack.dbname, "t024-stranger")
    unrelated = f00b.seed_artifact(
        t024_stack.dbname,
        t024_stack.object_dir,
        stranger,
        state="ready",
        staging=False,
        sealed_content=b"stranger",
        content=b"stranger",
    )
    assert status(t024_stack, first.lease, unrelated.id).status == 404
    assert download(t024_stack, first.lease, unrelated.id).status == 404

    # Not-ready artifacts are visible in scope but cannot be downloaded yet.
    not_ready = download(t024_stack, first.lease, first_ticket["artifact_id"])
    assert not_ready.status == 409, not_ready.text
    assert not_ready.json["error"]["code"] == "artifact_not_ready"

    # A signed token is bound to its own object: the second job's upload link
    # cannot fill the first job's staging key, and a token for one object cannot
    # be reused to read another.
    put = t024.put_link(second_ticket["upload_url"], b"second job bytes")
    assert put.status == 204
    assert t024.get_link(second_ticket["upload_url"]).status == 404


def test_a_leased_job_carries_a_real_input_url(t024_stack) -> None:
    job = t024.prepare_model_validate_job(t024_stack)
    input_url = job.lease["input_url"]
    assert isinstance(input_url, str) and input_url, job.lease
    assert job.lease["input_artifact_id"] == job.fixture.id
    fetched = t024.get_link(input_url)
    assert fetched.status == 200, (fetched.status, fetched.raw)
    assert fetched.raw == job.fixture.content

    # A job without an input artifact keeps null: no URL is invented.
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = f00b.enqueue_ok(
        t024_stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-no-fixture",
    )
    lease = t024_stack.internal_client().claim(kinds=[t024.KIND_MODEL_VALIDATE]).json
    assert lease["job_id"] == payload["id"]
    assert lease["input_artifact_id"] is None
    assert lease["input_url"] is None
