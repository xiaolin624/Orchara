"""T028 acceptance: artifact visibility and the write/read capability split.

Uploads are a normal-user capability (no creator role), but every artifact read
and the completion are owner-only: another human, an administrator and a
read-grant holder all get the same 404 as a missing row. Browser sessions need
CSRF on the two writes; API keys use the platform's existing two scopes as the
documented interim mapping (application:write for create/complete,
application:read for reads).
"""

from __future__ import annotations

import secrets

from support import f00b, f02a, t028
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t028_stack,
)


def users(stack: t028.T028Stack) -> tuple[t028.Session, t028.Session]:
    admin = t028.admin_session(stack)
    owner = t028.invite_and_activate(stack, admin, "t028-a-" + secrets.token_hex(3))
    other = t028.invite_and_activate(stack, admin, "t028-b-" + secrets.token_hex(3))
    return owner, other


def ready_artifact(stack: t028.T028Stack, session: t028.Session, content: bytes = b"a,b\n1,2\n"):
    created = t028.create_upload(
        session, media_type=t028.CSV, size_bytes=len(content), purpose="task_input"
    )
    assert created.status == 201, created.text
    artifact_id = created.json["artifact_id"]
    assert t028.put_link(created.json["upload_url"], content).status == 204
    assert t028.complete_upload(session, artifact_id, t028.sha256_hex(content)).status == 202
    t028.finalize_once(stack)
    assert t028.get_artifact(session, artifact_id).json["state"] == "ready"
    return artifact_id


def test_another_human_and_an_admin_get_404_everywhere(t028_stack) -> None:
    owner, other = users(t028_stack)
    artifact_id = ready_artifact(t028_stack, owner)
    admin = t028.admin_session(t028_stack)

    for actor in (other, admin):
        for response in (
            t028.get_artifact(actor, artifact_id),
            t028.download_artifact(actor, artifact_id),
            t028.read_content(actor, artifact_id),
            t028.complete_upload(actor, artifact_id, t028.sha256_hex(b"a,b\n1,2\n")),
            t028.complete_upload(actor, artifact_id, t028.sha256_hex(b"different")),
        ):
            assert response.status == 404, (actor.principal_id, response.status, response.text)
            assert response.json["error"]["code"] == "resource_not_found", response.json

    # A malformed or unknown id is the same 404 for the owner.
    for artifact in (t028.new_uuid(), "not-a-uuid"):
        response = t028.get_artifact(owner, artifact)
        assert response.status == 404, (artifact, response.status, response.text)
        assert t028.download_artifact(owner, artifact).status == 404
        assert t028.read_content(owner, artifact).status == 404

    # The owner still reads it: the refusals above are ACL, not a broken route.
    assert t028.get_artifact(owner, artifact_id).status == 200
    assert t028.download_artifact(owner, artifact_id).status == 200
    assert t028.read_content(owner, artifact_id).status == 200


def test_anonymous_requests_are_401(t028_stack) -> None:
    owner, _other = users(t028_stack)
    artifact_id = ready_artifact(t028_stack, owner)
    anonymous = t028_stack.client()

    for method, path in (
        ("POST", "/api/v1/artifacts/uploads"),
        ("POST", f"/api/v1/artifacts/{artifact_id}/complete"),
        ("GET", f"/api/v1/artifacts/{artifact_id}"),
        ("GET", f"/api/v1/artifacts/{artifact_id}/download"),
        ("GET", f"/api/v1/artifacts/{artifact_id}/content"),
    ):
        response = anonymous.request(method, path, body={} if method == "POST" else None)
        assert response.status == 401, (method, path, response.status, response.text)
        assert response.json["error"]["code"] in {"unauthenticated", "csrf_invalid"}, response.json


def test_a_missing_csrf_token_is_403_on_both_writes(t028_stack) -> None:
    owner, _other = users(t028_stack)
    create = t028.create_upload(owner, media_type=t028.TEXT, size_bytes=4, csrf=False)
    assert create.status == 403, create.text
    assert create.json["error"]["code"] == "csrf_invalid", create.json

    created = t028.create_upload(owner, media_type=t028.TEXT, size_bytes=4)
    assert created.status == 201
    artifact_id = created.json["artifact_id"]
    assert t028.put_link(created.json["upload_url"], b"four").status == 204
    complete = t028.complete_upload(owner, artifact_id, t028.sha256_hex(b"four"), csrf=False)
    assert complete.status == 403, complete.text
    assert complete.json["error"]["code"] == "csrf_invalid", complete.json
    assert t028.artifact_row(t028_stack.dbname, artifact_id)["state"] == "uploading"
    assert t028.artifact_jobs(t028_stack.dbname, artifact_id) == []


def test_api_key_scopes_are_the_documented_interim_mapping(t028_stack) -> None:
    owner, _other = users(t028_stack)
    admin = t028.admin_session(t028_stack)
    service_account = t028.create_service_account(admin, "t028 uploader", owner.principal_id)
    write_key_id, write_secret = t028.create_api_key(
        admin, service_account, ["application:write"]
    )
    read_key_id, read_secret = t028.create_api_key(admin, service_account, ["application:read"])
    assert write_key_id != read_key_id

    writer = f02a.bearer_client(t028_stack, write_secret)
    reader = f02a.bearer_client(t028_stack, read_secret)
    content = b"region,total\nEU,10\n"

    create = writer.post(
        "/api/v1/artifacts/uploads",
        body={"media_type": t028.CSV, "size_bytes": len(content), "purpose": "task_input"},
    )
    assert create.status == 201, create.text
    artifact_id = create.json["artifact_id"]
    assert t028.put_link(create.json["upload_url"], content).status == 204
    complete = writer.post(
        f"/api/v1/artifacts/{artifact_id}/complete", body={"sha256": t028.sha256_hex(content)}
    )
    assert complete.status == 202, complete.text
    t028.finalize_once(t028_stack)

    # The read key sees the service account's own artifact; the write key does
    # not carry the read scope (scope is checked before the owner ACL).
    assert reader.get(f"/api/v1/artifacts/{artifact_id}").status == 200
    assert reader.get(f"/api/v1/artifacts/{artifact_id}/download").status == 200
    assert reader.get(f"/api/v1/artifacts/{artifact_id}/content").raw == content

    refused_read = writer.get(f"/api/v1/artifacts/{artifact_id}")
    assert refused_read.status == 403, refused_read.text
    assert refused_read.json["error"]["code"] == "insufficient_scope", refused_read.json

    # A read-only key may not create or complete.
    refused_create = reader.post(
        "/api/v1/artifacts/uploads",
        body={"media_type": t028.TEXT, "size_bytes": 1, "purpose": "task_input"},
    )
    assert refused_create.status == 403, refused_create.text
    assert refused_create.json["error"]["code"] == "insufficient_scope", refused_create.json
    refused_complete = reader.post(
        f"/api/v1/artifacts/{artifact_id}/complete", body={"sha256": t028.sha256_hex(content)}
    )
    assert refused_complete.status == 403, refused_complete.text
    assert refused_complete.json["error"]["code"] == "insufficient_scope", refused_complete.json


def test_a_browser_session_does_not_need_the_creator_role(t028_stack) -> None:
    # The uploader created by users() has only the default user role. A creator
    # requirement here would be wrong: uploading a task input is a user action.
    owner, _other = users(t028_stack)
    created = t028.create_upload(owner, media_type=t028.TEXT, size_bytes=2, purpose="task_input")
    assert created.status == 201, created.text
    assert t028.put_link(created.json["upload_url"], b"hi").status == 204
    assert t028.complete_upload(owner, created.json["artifact_id"], t028.sha256_hex(b"hi")).status == 202
