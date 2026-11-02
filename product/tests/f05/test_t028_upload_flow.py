"""T028 acceptance: the user upload path from reservation to sealed bytes.

Every request is real HTTP against the compiled API; the bytes travel over the
real HMAC-signed staging URL; the finalize job is executed by the real
``cmd/maintenance`` process; and the downloaded bytes are compared with the
bytes that were uploaded. The core acceptance criterion is here: overwriting the
staging object after validation must never change the sealed bytes.
"""

from __future__ import annotations

import secrets

from support import f00b, t028
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t028_stack,
)


def uploader(stack: t028.T028Stack, *, roles: tuple[str, ...] = ("user",)) -> t028.Session:
    """A real non-admin user; uploads are a normal-user capability, not creator."""
    admin = t028.admin_session(stack)
    return t028.invite_and_activate(
        stack, admin, "t028-" + secrets.token_hex(4), roles=roles
    )


def create_and_put(
    stack: t028.T028Stack,
    session: t028.Session,
    content: bytes,
    *,
    media_type: str = t028.TEXT,
    purpose: str = "task_input",
    size_bytes: int | None = None,
) -> tuple[dict, str]:
    response = t028.create_upload(
        session,
        media_type=media_type,
        size_bytes=len(content) if size_bytes is None else size_bytes,
        purpose=purpose,
    )
    assert response.status == 201, response.text
    ticket = response.json
    t028.assert_upload(ticket)
    put = t028.put_link(ticket["upload_url"], content)
    assert put.status == 204, (put.status, put.raw)
    return ticket, t028.sha256_hex(content)


def test_create_upload_reserves_staging_and_sealed_keys(t028_stack) -> None:
    session = uploader(t028_stack)
    response = t028.create_upload(
        session, media_type=t028.TEXT, size_bytes=11, purpose="task_input"
    )
    assert response.status == 201, response.text
    ticket = response.json
    t028.assert_upload(ticket)

    row = t028.artifact_row(t028_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "uploading"
    assert int(row["revision"]) == 1
    assert row["owner_id"] == session.principal_id
    assert row["purpose"] == "task_input"
    assert row["media_type"] == "text/plain"
    assert int(row["size_bytes"]) == 11
    assert row["expected_sha256"] is None
    assert row["sha256"] is None
    assert row["object_key"].startswith("sealed/")
    assert row["staging_object_key"].startswith("staging/")
    assert row["object_key"] != row["staging_object_key"]

    # The signed PUT is for the staging key with the declared size ceiling; the
    # sealed key is never a PUT target.
    assert ticket["upload_url"].startswith(t028_stack.server.base_url + "/object-links/")
    put = t028.put_link(ticket["upload_url"], b"hello world")
    assert put.status == 204, (put.status, put.raw)

    # The contract's Upload view is exactly three fields.
    assert set(ticket) == {"artifact_id", "upload_url", "expires_at"}


def test_upload_complete_scan_finalize_ready_and_read_exact_bytes(t028_stack) -> None:
    session = uploader(t028_stack)
    content = b"region,total\nEU,10\nUS,12\n"
    ticket, sha = create_and_put(t028_stack, session, content, media_type=t028.CSV, purpose="task_input")

    completed = t028.complete_upload(session, ticket["artifact_id"], sha)
    assert completed.status == 202, completed.text
    t028.assert_artifact_view(completed.json)
    assert completed.json["state"] == "scanning"
    assert completed.json["media_type"] == t028.CSV
    assert completed.json["size_bytes"] == len(content)

    # One finalize job exists, created in the same transaction as the CAS.
    jobs = t028.artifact_jobs(t028_stack.dbname, ticket["artifact_id"])
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "ready"
    assert jobs[0]["expected_sha256"] == sha
    assert int(jobs[0]["expected_revision"]) == 2

    # Before the handler runs the artifact is not readable.
    scanning_download = t028.download_artifact(session, ticket["artifact_id"])
    assert scanning_download.status == 409, scanning_download.text
    assert scanning_download.json["error"]["code"] == "artifact_not_ready"
    scanning_content = t028.read_content(session, ticket["artifact_id"])
    assert scanning_content.status == 409, scanning_content.text
    assert scanning_content.json["error"]["code"] == "artifact_not_ready"

    reports = t028.finalize_once(t028_stack)
    report = f00b.job_reports(reports)[0]
    assert report["job_id"] == jobs[0]["id"], report
    assert report["result"] == "succeeded", report

    ready = t028.get_artifact(session, ticket["artifact_id"])
    assert ready.status == 200, ready.text
    t028.assert_artifact_view(ready.json)
    assert ready.json["state"] == "ready"
    assert ready.json["media_type"] == t028.CSV
    assert ready.json["size_bytes"] == len(content)

    row = t028.artifact_row(t028_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "ready"
    assert row["sha256"] == sha
    assert row["expected_sha256"] == sha
    assert t028.object_bytes(t028_stack.object_dir, row["object_key"]) == content
    assert t028.object_keys(t028_stack.object_dir, "sealed/") == {row["object_key"]}

    admission = t028.admission_row(t028_stack.dbname, ticket["artifact_id"])
    assert admission is not None, "the finalize handler persisted no admission row"
    assert admission["verdict"] == "accepted"
    assert admission["declared_media_type"] == t028.CSV
    assert admission["detected_format"] == "csv"
    assert admission["sniffed_media_type"] == t028.CSV
    assert admission["reason"] is None

    download = t028.download_artifact(session, ticket["artifact_id"])
    assert download.status == 200, download.text
    t028.assert_download(download.json)
    fetched = t028.get_link(download.json["url"])
    assert fetched.status == 200, fetched.raw
    assert fetched.raw == content
    assert fetched.headers.get("Content-Disposition") == "attachment"

    proxied = t028.read_content(session, ticket["artifact_id"])
    assert proxied.status == 200, proxied.text
    assert proxied.raw == content
    assert proxied.header("Content-Type") == "application/octet-stream"
    assert proxied.header("Content-Disposition") == "attachment"
    assert proxied.header("Cache-Control") == "no-store"


def test_overwriting_staging_after_ready_never_changes_the_sealed_bytes(t028_stack) -> None:
    session = uploader(t028_stack)
    original = b'{"orders":[{"id":1,"total":10}]}'
    ticket, sha = create_and_put(
        t028_stack, session, original, media_type=t028.JSON, purpose="task_input"
    )
    assert t028.complete_upload(session, ticket["artifact_id"], sha).status == 202
    t028.finalize_once(t028_stack)
    row = t028.artifact_row(t028_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "ready"
    assert t028.object_bytes(t028_stack.object_dir, row["object_key"]) == original

    # The signed staging URL is still valid (it has minutes left) and the
    # uploader overwrites staging with different bytes. This must never reach the
    # sealed object or the verified hash.
    overwritten = b'{"orders":[{"id":1,"total":77}]}'
    assert len(overwritten) <= len(original)
    replace = t028.put_link(ticket["upload_url"], overwritten)
    assert replace.status == 204, (replace.status, replace.raw)
    assert t028.object_bytes(t028_stack.object_dir, row["staging_object_key"]) == overwritten
    assert t028.object_bytes(t028_stack.object_dir, row["object_key"]) == original

    after = t028.artifact_row(t028_stack.dbname, ticket["artifact_id"])
    assert after["state"] == "ready"
    assert after["sha256"] == sha
    assert int(after["revision"]) == int(row["revision"])

    fetched = t028.get_link(t028.download_artifact(session, ticket["artifact_id"]).json["url"])
    assert fetched.raw == original
    proxied = t028.read_content(session, ticket["artifact_id"])
    assert proxied.raw == original


def test_the_same_hash_is_idempotent_and_a_different_hash_conflicts(t028_stack) -> None:
    session = uploader(t028_stack)
    content = b"idempotent upload bytes\n"
    ticket, sha = create_and_put(t028_stack, session, content)
    artifact_id = ticket["artifact_id"]

    first = t028.complete_upload(session, artifact_id, sha)
    assert first.status == 202, first.text
    assert first.json["state"] == "scanning"
    jobs_after_first = t028.artifact_jobs(t028_stack.dbname, artifact_id)
    assert len(jobs_after_first) == 1, jobs_after_first

    # Same hash while scanning: same body shape, still exactly one job.
    replay = t028.complete_upload(session, artifact_id, sha)
    assert replay.status == 202, replay.text
    t028.assert_artifact_view(replay.json)
    assert replay.json == first.json
    assert len(t028.artifact_jobs(t028_stack.dbname, artifact_id)) == 1

    t028.finalize_once(t028_stack)
    ready = t028.get_artifact(session, artifact_id)
    assert ready.json["state"] == "ready"

    # Same hash after ready: idempotent, same four-field shape, no second job.
    after_ready = t028.complete_upload(session, artifact_id, sha)
    assert after_ready.status == 202, after_ready.text
    t028.assert_artifact_view(after_ready.json)
    assert after_ready.json["state"] == "ready"
    assert after_ready.json == ready.json
    jobs = t028.artifact_jobs(t028_stack.dbname, artifact_id)
    assert len(jobs) == 1, jobs

    # A different hash is a conflict and creates no job either.
    different = t028.sha256_hex(b"different bytes")
    conflict = t028.complete_upload(session, artifact_id, different)
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "artifact_hash_conflict", conflict.json
    assert len(t028.artifact_jobs(t028_stack.dbname, artifact_id)) == 1
    row = t028.artifact_row(t028_stack.dbname, artifact_id)
    assert row["expected_sha256"] == sha
    assert row["state"] == "ready"


def test_create_upload_validates_the_contract_fields(t028_stack) -> None:
    session = uploader(t028_stack)
    cases = [
        ({"media_type": "application/pdf", "size_bytes": 10, "purpose": "task_input"}, "media_type"),
        ({"media_type": "", "size_bytes": 10, "purpose": "task_input"}, "media_type"),
        ({"media_type": "text/plain", "size_bytes": 0, "purpose": "task_input"}, "size_bytes"),
        ({"media_type": "text/plain", "size_bytes": 20 * 1024 * 1024 + 1, "purpose": "task_input"}, "size_bytes"),
        ({"media_type": "text/plain", "size_bytes": 10, "purpose": "report"}, "purpose"),
        ({"media_type": "text/plain", "size_bytes": 10, "purpose": ""}, "purpose"),
    ]
    for body, field in cases:
        response = t028.create_upload(session, body=body)
        assert response.status == 422, (body, response.status, response.text)
        error = response.json["error"]
        assert error["code"] == "validation_failed", error
        assert error["details"]["field"] == field, (body, error)

    # Unknown top-level fields are refused by the contract decoder.
    unknown = t028.create_upload(
        session,
        body={"media_type": "text/plain", "size_bytes": 10, "purpose": "task_input", "extra": 1},
    )
    assert unknown.status == 400, unknown.text
    assert unknown.json["error"]["code"] == "invalid_json"

    assert t028.count_rows(t028_stack.dbname, "artifacts") == 0


def test_complete_validates_the_hash_and_needs_a_real_artifact(t028_stack) -> None:
    session = uploader(t028_stack)
    missing = t028.complete_upload(session, t028.new_uuid(), "a" * 64)
    assert missing.status == 404, missing.text
    assert missing.json["error"]["code"] == "resource_not_found"

    ticket = t028.create_upload(session, media_type=t028.TEXT, size_bytes=4, purpose="task_input").json
    for body in ({"sha256": "not-a-hash"}, {"sha256": "A" * 64}, {}):
        response = t028.complete_upload(session, ticket["artifact_id"], "", body=body)
        assert response.status == 422, (body, response.status, response.text)
        assert response.json["error"]["details"]["field"] == "sha256"
    row = t028.artifact_row(t028_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "uploading"
    assert t028.artifact_jobs(t028_stack.dbname, ticket["artifact_id"]) == []
