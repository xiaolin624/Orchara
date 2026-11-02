"""T023: scoped API keys and the write path.

F02A extends T020's scope vocabulary with ``application:write`` because the
contract lists ``apiKey`` security on createApplication/saveDraft and no existing
scope could satisfy it. The tests prove the two scopes are enforced
independently: a read key can read a granted draft but never save, a write key
can create and save its own application but cannot read without the read scope.
"""

from __future__ import annotations

from support import f02a as f
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _bearer(stack, secret):
    return f.bearer_client(stack, secret)


def test_a_read_key_can_read_a_granted_draft_but_never_save_it(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    owner = f.invite_and_activate(f02a_stack, admin, "t023-scope-owner")
    application = f.create_application_or_fail(owner, "granted to a service account")
    service_id = f.create_service_account(admin, "svc-read", admin.principal_id)
    # Give the service principal the read grant, not the human.
    f.grant_read(admin, service_id, application["id"])
    _key_id, secret = f.create_api_key(admin, service_id, ["application:read"])
    bearer = _bearer(f02a_stack, secret)

    readable = bearer.get(f"/api/v1/applications/{application['id']}/draft")
    assert readable.status == 200, readable.text
    assert readable.json["capability_card"] == {}

    refused = bearer.request(
        "PUT",
        f"/api/v1/applications/{application['id']}/draft",
        body={"definition": {}, "layout": {}, "capability_card": {}},
        headers={"If-Match": readable.header("ETag")},
    )
    assert refused.status == 403, refused.text
    error = refused.json["error"]
    assert error["code"] == "insufficient_scope", error
    assert "application:write" in error["message"]

    # The contract also lets a key create applications only with the write scope.
    create = bearer.post("/api/v1/applications", body={"name": "should not exist"})
    assert create.status == 403, create.text
    assert create.json["error"]["code"] == "insufficient_scope"


def test_a_write_key_creates_and_saves_its_own_application(f02a_stack) -> None:
    admin = f.admin_session(f02a_stack)
    service_id = f.create_service_account(admin, "svc-write", admin.principal_id)
    _key_id, secret = f.create_api_key(admin, service_id, ["application:write"])
    bearer = _bearer(f02a_stack, secret)

    created = bearer.post("/api/v1/applications", body={"name": "service app"})
    assert created.status == 201, created.text
    application_id = created.json["id"]

    # Saving needs the read ETag to echo, and the write key is not read-scoped:
    # the GET refuses before the catalog, so the key cannot bootstrap an ETag.
    assert bearer.get(f"/api/v1/applications/{application_id}/draft").status == 403

    # Both scopes together are the editor credential: read the ETag, save with it.
    _both_key_id, both_secret = f.create_api_key(
        admin, service_id, ["application:read", "application:write"]
    )
    both = _bearer(f02a_stack, both_secret)
    draft = both.get(f"/api/v1/applications/{application_id}/draft")
    assert draft.status == 200, draft.text
    saved = both.request(
        "PUT",
        f"/api/v1/applications/{application_id}/draft",
        body={
            "definition": {"dsl_version": "1.0", "nodes": []},
            "layout": {},
            "capability_card": {},
        },
        headers={"If-Match": draft.header("ETag")},
    )
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'
