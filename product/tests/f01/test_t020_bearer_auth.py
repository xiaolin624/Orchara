"""T020 acceptance 3: a service key authenticates as itself and cannot administer.

The key is a bearer credential only: /auth/me reports kind "service", an empty
role list and a null CSRF token, and every role-gated admin endpoint refuses it
even though the same request is authenticated. No ``Set-Cookie`` is produced and
no session row is created.
"""

from __future__ import annotations

import uuid

from conftest import assert_error, sql
from t020_stack import (  # noqa: F401 - fixtures and helpers
    SCOPE_APPLICATION_READ,
    admin_session,
    api_server,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    future_rfc3339,
    go_binaries,
    revoke_api_key,
    stack,
)


def test_a_key_authenticates_without_a_cookie(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-me", admin.principal_id)
    expires_at = future_rfc3339(2)
    key_id, secret = create_api_key_or_fail(admin, service_id, expires_at=expires_at)
    sessions_before = stack.count("platform.sessions")

    bearer = bearer_client(stack, secret)
    response = bearer.get("/api/v1/auth/me")
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"principal_id", "kind", "roles", "csrf_token", "expires_at"}, body
    assert body["principal_id"] == service_id
    assert body["kind"] == "service"
    assert body["roles"] == []
    assert body["csrf_token"] is None
    assert body["expires_at"] == expires_at

    # A bearer credential is not a browser session: no cookie is set, and no
    # session row is created.
    assert response.header_values("Set-Cookie") == []
    assert stack.count("platform.sessions") == sessions_before

    # The same credential is accepted by the ACL-filtered read (empty here: no
    # grant) and the response names the service principal, never the human who
    # is responsible for it.
    listing = bearer.get("/api/v1/applications")
    assert listing.status == 200, listing.text
    assert listing.json == {"items": [], "next_cursor": None}

    # The key id is internal; it must not leak into the identity view.
    assert key_id not in response.text
    assert secret not in response.text


def test_admin_endpoints_refuse_a_service_key(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-admin-probe", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    random_id = str(uuid.uuid4())

    requests = [
        (
            "POST",
            "/api/v1/admin/service-accounts",
            {"display_name": "svc-forbidden", "responsible_user_id": admin.principal_id},
        ),
        (
            "POST",
            "/api/v1/admin/api-keys",
            {
                "principal_id": service_id,
                "scopes": [SCOPE_APPLICATION_READ],
                "expires_at": future_rfc3339(),
            },
        ),
        ("DELETE", f"/api/v1/admin/api-keys/{random_id}", None),
        (
            "POST",
            "/api/v1/admin/grants",
            {
                "principal_id": service_id,
                "resource_type": "application",
                "resource_id": random_id,
                "action": "read",
            },
        ),
        ("DELETE", f"/api/v1/admin/grants/{random_id}", None),
        ("PUT", f"/api/v1/admin/users/{random_id}/roles", {"roles": ["admin"]}),
    ]
    for method, path, body in requests:
        response = bearer.request(method, path, body=body)
        assert_error(response, 403, "forbidden")

    # The credential itself is still valid; only the role requirement failed.
    assert bearer.get("/api/v1/auth/me").status == 200
    # Nothing was created by the refused admin calls.
    assert sql(stack.dbname, "SELECT count(*) FROM platform.api_keys") == "1"


def test_a_malformed_bearer_header_never_falls_back_to_the_cookie(stack) -> None:
    admin = admin_session(stack)
    client = admin.client
    # The same client holds a valid session cookie. A present-but-malformed
    # Authorization header must be refused, not ignored in favour of the cookie.
    for header in (
        "Bearer",
        "Bearer ",
        "Bearer  two-spaces",
        "Basic " + admin.csrf,
        "Token something",
        admin.csrf,
    ):
        response = client.get("/api/v1/auth/me", headers={"Authorization": header})
        assert_error(response, 401, "invalid_key")

    # A revoked but syntactically valid key is refused even with a live cookie.
    service_id = create_service_account_or_fail(admin, "svc-revoked-session", admin.principal_id)
    key_id, secret = create_api_key_or_fail(admin, service_id)
    assert revoke_api_key(admin, key_id).status == 204
    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {secret}"})
    assert_error(response, 401, "invalid_key")

    # Without any Authorization header the cookie path is unchanged.
    response = client.get("/api/v1/auth/me")
    assert response.status == 200, response.text
    assert response.json["principal_id"] == admin.principal_id


def test_logout_refuses_a_bearer_credential_and_the_key_survives(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-logout", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    sessions_before = stack.count("platform.sessions")

    response = bearer.post("/api/v1/auth/logout")
    assert_error(response, 401, "unauthenticated")
    assert response.header_values("Set-Cookie") == []
    assert stack.count("platform.sessions") == sessions_before

    # The key was not revoked by logout; it still authenticates.
    assert bearer.get("/api/v1/auth/me").status == 200
    # The admin operation is the only revocation path, and it works.
    key_id, _ = create_api_key_or_fail(admin, service_id)
    assert revoke_api_key(admin, key_id).status == 204
