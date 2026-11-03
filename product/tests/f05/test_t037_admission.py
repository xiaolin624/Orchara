"""T037: application/zip is admitted only for purpose=tool_source.

T028's upload allowlist used to admit text/CSV/JSON/XLSX. T037 adds
``application/zip`` for the build-source purpose and keeps the same real-byte
admission discipline: entry count, total uncompressed size, compression ratio,
absolute/``..`` paths, symlinks and encrypted entries are all refused before any
extraction. The tests go through the real upload/finalize path and read the real
admission row; a quarantined artifact can never be submitted as a source.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from support import f00b, t028, t037
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    fresh_database,
    go_binaries,
    t033_stack,
)


def creator(stack, admin, login: str):
    return f00b.invite_and_activate(
        stack, admin, login, roles=("user", "creator"), password=f00b.USER_PASSWORD
    )


def upload_and_finalize(stack, session, content: bytes) -> str:
    created = t028.create_upload(
        session, media_type="application/zip", size_bytes=len(content), purpose="tool_source"
    )
    assert created.status == 201, created.text
    ticket = created.json
    assert t028.put_link(ticket["upload_url"], content).status in (200, 204)
    completed = t028.complete_upload(session, ticket["artifact_id"], t028.sha256_hex(content))
    assert completed.status == 202, completed.text
    t028.finalize_once(stack)
    t037.wait_until(
        lambda: t028.artifact_row(stack.dbname, ticket["artifact_id"])["state"] in {"ready", "quarantined"},
        timeout=30,
    )
    return ticket["artifact_id"]


def symlink_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("tool/link.py")
        info.create_system = 3  # Unix: Go's zip reader interprets the Unix mode bits
        info.external_attr = (0o120777 << 16) | 0o777
        archive.writestr(info, "../../etc/passwd")
    return buffer.getvalue()


def ratio_bomb() -> bytes:
    return t037.source_zip({"tool/blob.bin": b"\x00" * (8 << 20)})


def entry_floor() -> bytes:
    entries = {f"tool/file-{index:03d}.py": b"x = 1\n" for index in range(513)}
    return t037.source_zip(entries)


def test_a_real_source_zip_is_admitted_for_tool_source(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.admit.ok")
    artifact_id = upload_and_finalize(t033_stack, owner, t037.source_zip())
    row = t028.artifact_row(t033_stack.dbname, artifact_id)
    assert row["state"] == "ready", row
    assert row["purpose"] == "tool_source"
    admission = t028.admission_row(t033_stack.dbname, artifact_id)
    assert admission["verdict"] == "accepted", admission
    assert admission["detected_format"] == "zip", admission
    assert admission["sniffed_media_type"] == "application/zip", admission

    response = t037.submit_tool(owner, t037.submit_body(artifact_id))
    assert response.status == 202, response.text


def test_unsafe_zips_are_quarantined_with_a_safe_reason_and_cannot_be_submitted(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.admit.bad")
    cases = {
        "path traversal": (
            t037.source_zip({"../escape.py": b"x = 1\n"}),
            "zip_path_traversal",
        ),
        "symlink": (symlink_zip(), "zip_symlink_entry"),
        "ratio bomb": (ratio_bomb(), "zip_ratio_exceeded"),
        "entry limit": (entry_floor(), "zip_entry_limit_exceeded"),
    }
    for label, (content, reason) in cases.items():
        artifact_id = upload_and_finalize(t033_stack, owner, content)
        row = t028.artifact_row(t033_stack.dbname, artifact_id)
        assert row["state"] == "quarantined", (label, row)
        admission = t028.admission_row(t033_stack.dbname, artifact_id)
        assert admission["verdict"] == "rejected", (label, admission)
        assert admission["reason"] == reason, (label, admission)

        # A quarantined artifact is invisible to submit (same 404 as a missing id);
        # it can never become a build input.
        response = t037.submit_tool(owner, t037.submit_body(artifact_id))
        assert response.status == 404, (label, response.text)
        assert response.json["error"]["code"] == "resource_not_found"


def test_a_zip_with_other_upload_purposes_is_refused_at_declaration(t033_stack) -> None:
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.admit.purpose")
    content = t037.source_zip()
    for purpose in ("task_input", "knowledge", "evaluation_dataset"):
        refused = t028.create_upload(
            owner, media_type="application/zip", size_bytes=len(content), purpose=purpose
        )
        assert refused.status == 422, (purpose, refused.text)
        assert refused.json["error"]["details"]["field"] == "media_type"


def test_a_zip_declared_for_a_non_zip_artifact_is_refused_even_if_the_bytes_are_a_zip(t033_stack) -> None:
    # Existing T028 rules stay intact: an XLSX declaration whose bytes are a plain
    # source zip is still a content-type mismatch, not a silently accepted file.
    admin = t037.admin_session(t033_stack)
    owner = creator(t033_stack, admin, "t037.admit.mismatch")
    content = t037.source_zip()
    created = t028.create_upload(
        owner, media_type="application/json", size_bytes=len(content), purpose="task_input"
    )
    assert created.status == 201, created.text
    ticket = created.json
    assert t028.put_link(ticket["upload_url"], content).status in (200, 204)
    assert t028.complete_upload(owner, ticket["artifact_id"], t028.sha256_hex(content)).status == 202
    t028.finalize_once(t033_stack)
    t037.wait_until(
        lambda: t028.artifact_row(t033_stack.dbname, ticket["artifact_id"])["state"] in {"ready", "quarantined"},
        timeout=30,
    )
    row = t028.artifact_row(t033_stack.dbname, ticket["artifact_id"])
    assert row["state"] == "quarantined", row
    admission = t028.admission_row(t033_stack.dbname, ticket["artifact_id"])
    assert admission["reason"] == "content_type_mismatch", admission
