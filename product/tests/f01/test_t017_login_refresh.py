"""T017 acceptance 1: sign in, refresh the page, still be identified.

The refresh case is expressed in HTTP terms: a brand-new client that carries
*nothing but* the session cookie (no login response in memory, no CSRF token
remembered) must get the same identity and the same CSRF token from
``GET /api/v1/auth/me``. That is only possible because the CSRF token is derived
from the session token instead of being a second random value.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import (
    SESSION_COOKIE,
    assert_error,
    cookie_attributes,
    sql,
)
from t017_stack import (  # noqa: F401 - fixtures are used through their names
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    Stack,
    admin_session,
    api_server,
    client_with_cookie,
    fresh_database,
    go_binaries,
    invite_and_activate,
    session_for,
    stack,
)


def test_login_sets_a_safe_cookie_and_a_refresh_restores_the_identity(stack: Stack) -> None:
    client = stack.client()
    response = client.post(
        "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
    )
    assert response.status == 200, response.text
    login_body = response.json
    assert set(login_body) == {"principal_id", "csrf_token", "expires_at"}, login_body
    assert response.header("Cache-Control") == "no-store"

    set_cookie = [
        value
        for value in response.header_values("Set-Cookie")
        if value.startswith(SESSION_COOKIE + "=")
    ]
    assert len(set_cookie) == 1, response.header_values("Set-Cookie")
    attributes = cookie_attributes(set_cookie[0])
    assert attributes["path"] == "/"
    assert "httponly" in attributes, "a session cookie readable by JavaScript can be exfiltrated"
    assert attributes["samesite"].lower() == "lax"
    # dev-stub speaks plain HTTP on loopback, so Secure would silently drop the
    # cookie instead of protecting it. The production profiles set it.
    assert "secure" not in attributes, attributes
    raw_cookie = attributes["value"]
    assert raw_cookie and len(raw_cookie) >= 40
    assert client.cookie(SESSION_COOKIE) == raw_cookie

    # --- the page is refreshed: only the cookie survives ---
    refreshed = client_with_cookie(stack, raw_cookie)
    assert refreshed.cookie(SESSION_COOKIE) is None, "this client must start with an empty jar"
    me = refreshed.get("/api/v1/auth/me")

    assert me.status == 200, me.text
    assert me.header("Cache-Control") == "no-store"
    assert me.header("Content-Type").startswith("application/json")
    identity = me.json
    assert set(identity) == {"principal_id", "kind", "roles", "csrf_token", "expires_at"}, identity
    assert identity["principal_id"] == login_body["principal_id"]
    assert identity["kind"] == "user"
    assert identity["roles"] == ["admin"]
    assert identity["csrf_token"] == login_body["csrf_token"], (
        "the refreshed page did not get back the CSRF token login returned"
    )
    expires_at = datetime.fromisoformat(identity["expires_at"].replace("Z", "+00:00"))
    assert abs(expires_at - (datetime.now(timezone.utc) + timedelta(hours=24))) < timedelta(minutes=5)
    assert identity["principal_id"] == sql(
        stack.dbname, "SELECT id::text FROM platform.principals WHERE kind = 'user'"
    )


def test_each_session_gets_its_own_csrf_token_and_each_is_restored(stack: Stack) -> None:
    first = session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)
    second = session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)
    assert first.cookie != second.cookie
    assert first.csrf != second.csrf, "two sessions share a CSRF token"

    for session in (first, second):
        refreshed = client_with_cookie(stack, session.cookie)
        identity = refreshed.get("/api/v1/auth/me").json
        assert identity["csrf_token"] == session.csrf
        assert identity["principal_id"] == session.principal_id


def test_me_rejects_a_missing_or_unknown_session_cookie(stack: Stack) -> None:
    anonymous = stack.client()
    missing = anonymous.get("/api/v1/auth/me")
    assert_error(missing, 401, "unauthenticated")
    assert missing.header("Cache-Control") == "no-store"

    unknown = client_with_cookie(stack, "this-is-not-a-session-token").get("/api/v1/auth/me")
    assert_error(unknown, 401, "unauthenticated")

    garbage = stack.client().get(
        "/api/v1/auth/me", headers={"Cookie": f"{SESSION_COOKIE}=%ZZ-not-base64"}
    )
    assert_error(garbage, 401, "unauthenticated")


def test_me_does_not_leak_a_login_a_display_name_or_a_password_hash(stack: Stack) -> None:
    admin = admin_session(stack)
    marker_login = "t017.marker.login"
    marker_display = "Marker Display Name"
    user = invite_and_activate(stack, admin, marker_login, display_name=marker_display)
    identity = user.client.get("/api/v1/auth/me")
    assert identity.status == 200, identity.text

    # The database holds this user's login, display name and password hash.
    # None of them is part of IdentityView, and none may appear in its body.
    own = sql(
        stack.dbname,
        "SELECT login || '|' || p.display_name || '|' || u.password_hash "
        "FROM platform.users AS u JOIN platform.principals AS p ON p.id = u.principal_id "
        "WHERE u.login = 't017.marker.login'",
    ).split("|")
    assert len(own) == 3 and all(own), own
    for value in own:
        assert value not in identity.text, f"/auth/me leaked {value!r}"

    # The administrator who invited them is not this user's data either.
    other = sql(
        stack.dbname,
        "SELECT p.display_name || '|' || u.password_hash "
        "FROM platform.users AS u JOIN platform.principals AS p ON p.id = u.principal_id "
        "WHERE u.login = 'admin'",
    ).split("|")
    assert len(other) == 2 and all(other), other
    for value in other:
        assert value not in identity.text, f"/auth/me leaked another principal's {value!r}"

    assert set(identity.json) == {"principal_id", "kind", "roles", "csrf_token", "expires_at"}
    assert "login" not in identity.json
    assert "display_name" not in identity.json
