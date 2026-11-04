"""T028 acceptance: refused uploads are quarantined and never readable.

Each case attacks one admission fact: the declared hash, the declared size, the
object ceiling, the declared media type, the JSON/CSV shape, and an XLSX zip
with macros or a non-XLSX body. A quarantine must leave the artifact visible to
its owner only as state=quarantined with a safe reason, and must make download,
signed links and proxied content unavailable. A missing staging object is
discovered by the handler and quarantines a user upload instead of leaving it
scanning forever.
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


def uploader(stack: t028.T028Stack) -> t028.Session:
    admin = t028.admin_session(stack)
    return t028.invite_and_activate(stack, admin, "t028-q-" + secrets.token_hex(4))


def upload_and_finalize(
    stack: t028.T028Stack,
    session: t028.Session,
    content: bytes,
    *,
    media_type: str,
    purpose: str = "task_input",
    size_bytes: int | None = None,
    complete_sha: str | None = None,
) -> tuple[str, dict]:
    created = t028.create_upload(
        session,
        media_type=media_type,
        size_bytes=len(content) if size_bytes is None else size_bytes,
        purpose=purpose,
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    put = t028.put_link(created.json["upload_url"], content)
    assert put.status == 204, (put.status, put.raw)
    sha = complete_sha if complete_sha is not None else t028.sha256_hex(content)
    completed = t028.complete_upload(session, artifact_id, sha)
    assert completed.status == 202, completed.text
    reports = t028.finalize_once(stack)
    report = f00b.job_reports(reports)[0]
    return artifact_id, report


def assert_quarantined(
    stack: t028.T028Stack,
    session: t028.Session,
    artifact_id: str,
    *,
    job_code: str,
    reason: str,
) -> dict:
    row = t028.artifact_row(stack.dbname, artifact_id)
    assert row["state"] == "quarantined", row
    assert row["sha256"] is None, row
    jobs = t028.artifact_jobs(stack.dbname, artifact_id)
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "failed", jobs
    assert jobs[0]["error_code"] == job_code, jobs
    assert jobs[0]["failure_class"] == "business", jobs
    assert int(jobs[0]["attempt_count"]) == 1, jobs

    admission = t028.admission_row(stack.dbname, artifact_id)
    assert admission is not None, "no admission row was recorded for the refused upload"
    assert admission["verdict"] == "rejected", admission
    assert admission["reason"] == reason, admission

    visible = t028.get_artifact(session, artifact_id)
    assert visible.status == 200, visible.text
    assert visible.json["state"] == "quarantined"
    assert t028.quarantine_reason(visible) == reason, visible.headers

    # Quarantined content is not readable through either path, and the sealed
    # active key was never written.
    assert t028.download_artifact(session, artifact_id).status == 404
    assert t028.read_content(session, artifact_id).status == 404
    assert not (stack.object_dir / row["object_key"]).exists()
    return row


def test_a_hash_mismatch_quarantines_and_hides_the_content(t028_stack) -> None:
    session = uploader(t028_stack)
    content = b"the bytes that actually arrived\n"
    wrong = t028.sha256_hex(b"what the uploader declared")
    artifact_id, report = upload_and_finalize(
        t028_stack, session, content, media_type=t028.TEXT, complete_sha=wrong
    )
    assert report["result"] == "failed", report
    assert report["error_code"] == "artifact_hash_mismatch", report
    row = assert_quarantined(
        t028_stack,
        session,
        artifact_id,
        job_code="artifact_hash_mismatch",
        reason="artifact_hash_mismatch",
    )
    # The handler removed only its fenced attempt object.
    assert t028.object_keys(t028_stack.object_dir, "sealed/") == set()
    assert (t028_stack.object_dir / row["staging_object_key"]).exists()


def test_a_declared_size_mismatch_quarantines(t028_stack) -> None:
    session = uploader(t028_stack)
    content = b"short body"
    artifact_id, report = upload_and_finalize(
        t028_stack, session, content, media_type=t028.TEXT, size_bytes=len(content) + 3
    )
    assert report["error_code"] == "artifact_size_mismatch", report
    assert_quarantined(
        t028_stack, session, artifact_id,
        job_code="artifact_size_mismatch", reason="artifact_size_mismatch",
    )


def test_an_oversized_staging_object_is_quarantined(t028_stack) -> None:
    # The signed PUT refuses more bytes than declared, so this state is seeded
    # directly: an artifact row whose staging object is over the platform
    # ceiling. The handler must refuse to copy it.
    session = uploader(t028_stack)
    artifact = t028.seed_user_artifact(
        t028_stack.dbname,
        t028_stack.object_dir,
        session.principal_id,
        content=t028.oversized_content(),
        size_bytes=t028.MAX_OBJECT_BYTES + 1,
        purpose="task_input",
    )
    t028.enqueue_finalize(t028_stack, artifact)
    reports = t028.finalize_once(t028_stack)
    report = f00b.job_reports(reports)[0]
    assert report["error_code"] == "artifact_too_large", report
    assert_quarantined(
        t028_stack, session, artifact.id,
        job_code="artifact_too_large", reason="artifact_too_large",
    )


def test_an_unsupported_declared_media_type_is_quarantined(t028_stack) -> None:
    # The public createUpload path refuses this declaration (422, covered in the
    # flow tests). A row that already exists with an unsupported declaration is
    # refused by the handler instead of trusting it. (application/pdf was the
    # example until F10/T060 admitted it for purpose=knowledge; a Word document
    # remains an unsupported knowledge source.)
    session = uploader(t028_stack)
    artifact = t028.seed_user_artifact(
        t028_stack.dbname,
        t028_stack.object_dir,
        session.principal_id,
        media_type="application/msword",
        content=b"\xd0\xcf\x11\xe0 not admitted\n",
        purpose="knowledge",
    )
    t028.enqueue_finalize(t028_stack, artifact)
    report = f00b.job_reports(t028.finalize_once(t028_stack))[0]
    assert report["error_code"] == "artifact_format_rejected", report
    row = assert_quarantined(
        t028_stack, session, artifact.id,
        job_code="artifact_format_rejected", reason="unsupported_media_type",
    )
    admission = t028.admission_row(t028_stack.dbname, artifact.id)
    assert admission["declared_media_type"] == "application/msword", admission
    assert admission["detected_format"] in {"text", "binary", "unknown"}, admission
    assert t028.object_keys(t028_stack.object_dir, "sealed/") == set()


def test_malformed_json_is_quarantined(t028_stack) -> None:
    session = uploader(t028_stack)
    artifact_id, report = upload_and_finalize(
        t028_stack, session, b'{"orders": [', media_type=t028.JSON
    )
    assert report["error_code"] == "artifact_format_rejected", report
    assert_quarantined(
        t028_stack, session, artifact_id,
        job_code="artifact_format_rejected", reason="invalid_json",
    )


def test_a_zip_with_macros_is_quarantined_even_with_a_valid_hash(t028_stack) -> None:
    session = uploader(t028_stack)
    macro = t028.macro_xlsx_bytes()
    artifact_id, report = upload_and_finalize(
        t028_stack, session, macro, media_type=t028.XLSX
    )
    assert report["error_code"] == "artifact_format_rejected", report
    assert_quarantined(
        t028_stack, session, artifact_id,
        job_code="artifact_format_rejected", reason="xlsx_macro_present",
    )


def test_an_inconsistent_declared_type_is_quarantined(t028_stack) -> None:
    session = uploader(t028_stack)
    zipped = t028.xlsx_bytes()
    artifact_id, report = upload_and_finalize(
        t028_stack, session, zipped, media_type=t028.TEXT
    )
    assert report["error_code"] == "artifact_format_rejected", report
    assert_quarantined(
        t028_stack, session, artifact_id,
        job_code="artifact_format_rejected", reason="content_type_mismatch",
    )


def test_a_valid_xlsx_is_admitted(t028_stack) -> None:
    session = uploader(t028_stack)
    content = t028.xlsx_bytes()
    artifact_id, report = upload_and_finalize(
        t028_stack, session, content, media_type=t028.XLSX, purpose="evaluation_dataset"
    )
    assert report["result"] == "succeeded", report
    row = t028.artifact_row(t028_stack.dbname, artifact_id)
    assert row["state"] == "ready"
    admission = t028.admission_row(t028_stack.dbname, artifact_id)
    assert admission["verdict"] == "accepted", admission
    assert admission["detected_format"] == "xlsx", admission
    proxied = t028.read_content(session, artifact_id)
    assert proxied.status == 200
    assert proxied.raw == content


def test_a_user_upload_without_staging_bytes_is_quarantined(t028_stack) -> None:
    # The client never PUT the bytes. completeUpload does not guess this (the
    # harness discovers it), and a user upload must not stay scanning forever.
    session = uploader(t028_stack)
    created = t028.create_upload(session, media_type=t028.TEXT, size_bytes=9, purpose="task_input")
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    completed = t028.complete_upload(session, artifact_id, t028.sha256_hex(b"never sent"))
    assert completed.status == 202, completed.text
    report = f00b.job_reports(t028.finalize_once(t028_stack))[0]
    assert report["error_code"] == "artifact_upload_incomplete", report
    row = assert_quarantined(
        t028_stack, session, artifact_id,
        job_code="artifact_upload_incomplete", reason="artifact_upload_incomplete",
    )
    assert row["expected_sha256"] == t028.sha256_hex(b"never sent")
    assert t028.object_keys(t028_stack.object_dir, "sealed/") == set()


def test_a_quarantined_upload_cannot_be_completed_into_a_job(t028_stack) -> None:
    session = uploader(t028_stack)
    artifact_id, _report = upload_and_finalize(
        t028_stack, session, b'{"broken":', media_type=t028.JSON
    )
    before = t028.artifact_jobs(t028_stack.dbname, artifact_id)
    replay = t028.complete_upload(session, artifact_id, t028.artifact_row(t028_stack.dbname, artifact_id)["expected_sha256"])
    assert replay.status == 409, replay.text
    assert replay.json["error"]["code"] == "artifact_conflict", replay.json
    assert t028.artifact_jobs(t028_stack.dbname, artifact_id) == before
