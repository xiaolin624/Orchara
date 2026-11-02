"""T020 acceptance 4: scope and resource ACL are an intersection, scope first.

A key must carry ``application:read`` AND the service principal must hold an
unexpired ``read`` grant (ownership is never inherited from the responsible
human). Scope is checked before the ACL, so a write-scoped key is refused with
403 ``insufficient_scope`` even when a read grant exists, and an ACL miss stays
404 so ids cannot be enumerated.
"""

from __future__ import annotations

from conftest import assert_error
from t020_stack import (  # noqa: F401 - fixtures and helpers
    APPLICATION,
    SCOPE_APPLICATION_READ,
    admin_session,
    api_server,
    application_ids,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    get_application,
    go_binaries,
    grant_read_or_fail,
    invite_and_activate,
    revoke_grant,
    seed_api_key_row,
    seed_application,
    stack,
)


def _setup_owner_and_service(stack):
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "t020-acl-owner", roles=("creator",))
    application_id = seed_application(stack, owner.principal_id, "owned-by-the-owner")
    service_id = create_service_account_or_fail(admin, "svc-acl-reader", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    return admin, owner, application_id, service_id, secret


def test_a_read_key_needs_both_scope_and_a_grant_and_a_revoke_is_immediate(stack) -> None:
    admin, _owner, application_id, service_id, secret = _setup_owner_and_service(stack)
    bearer = bearer_client(stack, secret)

    # No grant: default deny. The list is empty and the detail is a 404 that is
    # indistinguishable from "does not exist".
    listing = bearer.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert listing.json == {"items": [], "next_cursor": None}
    response = bearer.get(f"/api/v1/applications/{application_id}")
    assert_error(response, 404, "resource_not_found")

    # Grant read on somebody else's application: the next list and detail see it.
    grant_id = grant_read_or_fail(admin, service_id, application_id)
    listing = bearer.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert application_ids(listing) == [application_id]
    detail = bearer.get(f"/api/v1/applications/{application_id}")
    assert detail.status == 200, detail.text
    assert detail.json["name"] == "owned-by-the-owner"

    # Revoke: the same process, no restart, next call is denied.
    assert revoke_grant(admin, grant_id).status == 204
    listing = bearer.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert listing.json == {"items": [], "next_cursor": None}
    assert_error(bearer.get(f"/api/v1/applications/{application_id}"), 404, "resource_not_found")


def test_scope_is_checked_before_the_acl(stack) -> None:
    admin, _owner, application_id, service_id, secret = _setup_owner_and_service(stack)
    # The service principal really holds a read grant on the application.
    grant_read_or_fail(admin, service_id, application_id)

    # A write-scoped key is a genuine credential (F02A/T023 added the scope to
    # the vocabulary); this test proves a key that is not read-scoped is refused
    # by a read endpoint before the catalog is touched.
    _write_key_id, write_secret = seed_api_key_row(
        stack.dbname, service_id, scopes=["application:write"]
    )
    write_bearer = bearer_client(stack, write_secret)

    # Scope is checked before the catalog: not even 404 can be observed, so the
    # response cannot reveal whether the application exists.
    assert_error(write_bearer.get("/api/v1/applications"), 403, "insufficient_scope")
    assert_error(
        write_bearer.get(f"/api/v1/applications/{application_id}"), 403, "insufficient_scope"
    )

    # Control: the read-scoped key still reads the same grant.
    read_bearer = bearer_client(stack, secret)
    listing = read_bearer.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert application_ids(listing) == [application_id]
    assert read_bearer.get(f"/api/v1/applications/{application_id}").status == 200


def test_a_read_key_without_an_acl_grant_gets_404(stack) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "t020-no-grant-owner", roles=("creator",))
    application_id = seed_application(stack, owner.principal_id, "not-granted")
    service_id = create_service_account_or_fail(admin, "svc-no-grant", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id, scopes=[SCOPE_APPLICATION_READ])
    bearer = bearer_client(stack, secret)

    assert bearer.get("/api/v1/applications").json == {"items": [], "next_cursor": None}
    assert_error(bearer.get(f"/api/v1/applications/{application_id}"), 404, "resource_not_found")


def test_a_service_account_never_inherits_its_responsible_persons_apps(stack) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t020-responsible-owner", roles=("creator",))
    responsible_application = seed_application(stack, responsible.principal_id, "responsible-owns")
    # The responsible human can read their own application...
    assert get_application(responsible, responsible_application).status == 200

    service_id = create_service_account_or_fail(admin, "svc-no-borrowing", responsible.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    # ...but the service account does not: responsibility is governance, not a
    # grant of the responsible person's content.
    assert bearer.get("/api/v1/applications").json == {"items": [], "next_cursor": None}
    assert_error(
        bearer.get(f"/api/v1/applications/{responsible_application}"),
        404,
        "resource_not_found",
    )

    # Only an explicit grant to the service principal changes that.
    grant_read_or_fail(admin, service_id, responsible_application)
    assert application_ids(bearer.get("/api/v1/applications")) == [responsible_application]


def test_a_human_session_is_not_scope_limited(stack) -> None:
    _admin, owner, application_id, _service_id, _secret = _setup_owner_and_service(stack)
    # A session cookie authenticates a human; F01 scopes describe API keys, so
    # the owner still lists their own application without any scope machinery.
    listing = owner.client.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert application_ids(listing) == [application_id]
    me = owner.client.get("/api/v1/auth/me")
    assert me.status == 200
    assert me.json["roles"] == ["creator"]
    assert me.json["csrf_token"]
