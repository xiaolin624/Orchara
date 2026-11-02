"""T015 acceptance 5: invite -> one-time token -> activation -> login.

Every request goes to the real compiled API over HTTP; every stored value is
read back from real PostgreSQL. The login response's ``csrf_token`` is sent on
the admin write even though T015 does not enforce CSRF yet: T017 will, and these
tests must keep proving the same user path after that change.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

import pytest

from conftest import (
    ApiServer,
    HttpClient,
    Response,
    api_server,  # noqa: F401 - pytest fixture
    assert_error,
    bootstrap_or_fail,
    cookie_attributes,
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
    migrate_or_fail,
    sha256_hex,
    sql,
    sql_rows,
)

# Generated per test process: no fixed password string lives in the fixture.
ADMIN_PASSWORD = "t015-admin-" + secrets.token_urlsafe(18)
USER_PASSWORD = "t015-user-" + secrets.token_urlsafe(18)
WRONG_PASSWORD = "t015-wrong-" + secrets.token_urlsafe(18)
SECOND_PASSWORD = "t015-second-" + secrets.token_urlsafe(18)
# Deliberately below the 12-character minimum, generated so no fixed password
# string appears in the fixture.
WEAK_PASSWORD = "short-" + secrets.token_urlsafe(2)
COOKIE = "platform_session"


@dataclass
class Stack:
    server: ApiServer
    dsn: str
    dbname: str
    admin_binary: Path

    def client(self) -> HttpClient:
        return HttpClient(self.server.base_url)


class AdminSession(NamedTuple):
    client: HttpClient
    csrf: str


@pytest.fixture
def stack(fresh_database, go_binaries, api_server) -> Stack:
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    bootstrap_or_fail(go_binaries["admin"], dsn, ADMIN_PASSWORD)
    return Stack(server=api_server(dsn), dsn=dsn, dbname=dbname, admin_binary=go_binaries["admin"])


def login(stack: Stack, login_name: str, password: str, client: HttpClient | None = None):
    client = client or stack.client()
    response = client.post(
        "/api/v1/auth/login", body={"login": login_name, "password": password}
    )
    csrf = response.json.get("csrf_token") if response.status == 200 else None
    return client, response, csrf


def admin_session(stack: Stack) -> AdminSession:
    client, response, csrf = login(stack, "admin", ADMIN_PASSWORD)
    assert response.status == 200, response.text
    assert csrf
    return AdminSession(client, csrf)


def invite(
    stack: Stack,
    admin: AdminSession,
    login_name: str,
    *,
    display_name: str = "Invited User",
    roles: tuple[str, ...] = ("user",),
) -> Response:
    return admin.client.post(
        "/api/v1/admin/users",
        body={"login": login_name, "display_name": display_name, "roles": list(roles)},
        csrf=admin.csrf,
    )


def activate(stack: Stack, token: str, password: str) -> Response:
    return stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": token, "password": password},
    )


def invites_until_token(stack: Stack, admin: AdminSession, login_name: str) -> str:
    response = invite(stack, admin, login_name)
    assert response.status == 201, response.text
    return response.json["activation_token"]


def test_login_returns_a_session_cookie_and_a_hashed_csrf_token(stack: Stack) -> None:
    client, response, csrf = login(stack, "admin", ADMIN_PASSWORD)

    assert response.status == 200, response.text
    body = response.json
    assert set(body) == {"principal_id", "csrf_token", "expires_at"}, body
    assert body["principal_id"] == sql(stack.dbname, "SELECT id::text FROM platform.principals")
    assert body["csrf_token"] == csrf
    assert len(body["csrf_token"]) >= 40
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert abs(expires_at - (datetime.now(timezone.utc) + timedelta(hours=24))) < timedelta(minutes=5)
    assert response.header("Cache-Control") == "no-store"

    cookie_header = response.header_values("Set-Cookie")
    session_headers = [value for value in cookie_header if value.startswith(COOKIE + "=")]
    assert len(session_headers) == 1, cookie_header
    attributes = cookie_attributes(session_headers[0])
    assert attributes["path"] == "/"
    assert "httponly" in attributes
    assert attributes["samesite"].lower() == "lax"
    # dev-stub is plain HTTP on loopback; Secure would silently drop the cookie.
    assert "secure" not in attributes, attributes
    raw_cookie = attributes["value"]
    assert raw_cookie and len(raw_cookie) >= 40
    assert client.cookie(COOKIE) == raw_cookie

    stored = sql_rows(stack.dbname, "SELECT token_hash, csrf_hash, revoked_at::text AS revoked_at FROM platform.sessions")
    assert len(stored) == 1, stored
    assert stored[0]["token_hash"] == sha256_hex(raw_cookie), "the session cookie is not stored as SHA-256"
    assert stored[0]["csrf_hash"] == sha256_hex(csrf), "the CSRF token is not stored as SHA-256"
    assert stored[0]["revoked_at"] in (None, "")
    assert raw_cookie not in str(stored)


def test_login_does_not_reveal_whether_a_login_exists(stack: Stack) -> None:
    _client, unknown, _csrf = login(stack, "ghost", WRONG_PASSWORD)
    _client2, wrong, _csrf2 = login(stack, "admin", WRONG_PASSWORD)

    assert unknown.status == 401, unknown.text
    assert wrong.status == 401, wrong.text
    assert unknown.json["error"]["code"] == "invalid_credentials"
    assert wrong.json["error"]["code"] == "invalid_credentials"
    assert unknown.header("Cache-Control") == "no-store"
    assert sql(stack.dbname, "SELECT count(*) FROM platform.sessions") == "0"


def test_login_rejects_a_malformed_body(stack: Stack) -> None:
    response = stack.client().post("/api/v1/auth/login", raw_body="{not-json")
    assert_error(response, 400, "invalid_json")
    assert response.header("Cache-Control") == "no-store"


def test_disabled_principal_cannot_log_in(stack: Stack) -> None:
    sql(
        stack.dbname,
        "UPDATE platform.principals SET disabled_at = now() "
        "WHERE id = (SELECT id FROM platform.principals WHERE kind = 'user')",
    )
    _client, response, _csrf = login(stack, "admin", ADMIN_PASSWORD)
    assert_error(response, 403, "principal_disabled")
    assert sql(stack.dbname, "SELECT count(*) FROM platform.sessions") == "0"


def test_invite_requires_an_admin_session(stack: Stack) -> None:
    anonymous = stack.client()
    response = anonymous.post(
        "/api/v1/admin/users",
        body={"login": "eve", "display_name": "Eve", "roles": ["user"]},
    )
    assert_error(response, 401, "unauthenticated")

    admin = admin_session(stack)
    token = invites_until_token(stack, admin, "bob")
    assert activate(stack, token, USER_PASSWORD).status == 200
    bob_client, bob_login, bob_csrf = login(stack, "bob", USER_PASSWORD)
    assert bob_login.status == 200
    forbidden = bob_client.post(
        "/api/v1/admin/users",
        body={"login": "eve", "display_name": "Eve", "roles": ["user"]},
        csrf=bob_csrf,
    )
    assert_error(forbidden, 403, "forbidden")
    assert sql(stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'eve'") == "0"


def test_admin_invite_returns_a_one_time_token_that_is_only_hashed_at_rest(stack: Stack) -> None:
    admin = admin_session(stack)
    response = invite(stack, admin, "carol", display_name="Carol")

    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"principal_id", "activation_token", "expires_at"}, body
    assert response.header("Cache-Control") == "no-store"
    token = body["activation_token"]
    assert len(token) >= 40
    expires_at = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
    assert abs(expires_at - (datetime.now(timezone.utc) + timedelta(hours=24))) < timedelta(minutes=5)

    users = sql_rows(
        stack.dbname,
        f"SELECT principal_id::text AS principal_id, login, password_hash, must_change_password "
        f"FROM platform.users WHERE login = 'carol'",
    )
    assert len(users) == 1
    assert users[0]["principal_id"] == body["principal_id"]
    assert users[0]["password_hash"] == "!pending-activation"
    assert users[0]["must_change_password"] is True

    tokens = sql_rows(
        stack.dbname,
        "SELECT principal_id::text AS principal_id, token_hash, consumed_at::text AS consumed_at "
        "FROM platform.activation_tokens",
    )
    assert len(tokens) == 1
    assert tokens[0]["token_hash"] == sha256_hex(token)
    assert tokens[0]["token_hash"] != token
    assert tokens[0]["consumed_at"] in (None, "")
    assert token not in str(tokens)

    assert sql(
        stack.dbname,
        f"SELECT role FROM platform.role_bindings WHERE principal_id = '{body['principal_id']}'",
    ) == "user"
    # An invited user has no session and no password yet.
    assert sql(stack.dbname, "SELECT count(*) FROM platform.sessions") == "1"
    assert sql(
        stack.dbname,
        f"SELECT count(*) FROM platform.audit_events WHERE action = 'user.invited' "
        f"AND resource_id = '{body['principal_id']}'",
    ) == "1"


def test_duplicate_login_is_a_conflict(stack: Stack) -> None:
    admin = admin_session(stack)
    assert invite(stack, admin, "dave").status == 201

    duplicate = invite(stack, admin, "dave", display_name="Dave Again")
    assert_error(duplicate, 409, "login_taken")
    assert sql(stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'dave'") == "1"


@pytest.mark.parametrize("roles", [["superuser"], [], ["user", "user"]])
def test_invalid_roles_are_rejected(stack: Stack, roles: list[str]) -> None:
    admin = admin_session(stack)
    response = admin.client.post(
        "/api/v1/admin/users",
        body={"login": "frank", "display_name": "Frank", "roles": roles},
        csrf=admin.csrf,
    )
    assert_error(response, 422, "validation_failed")
    assert sql(stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'frank'") == "0"


def test_activation_succeeds_once_and_the_user_can_then_log_in(stack: Stack) -> None:
    admin = admin_session(stack)
    token = invites_until_token(stack, admin, "gina")

    activated = activate(stack, token, USER_PASSWORD)
    assert activated.status == 200, activated.text
    assert activated.json["state"] == "activated"
    assert activated.header("Cache-Control") == "no-store"
    principal_id = sql(stack.dbname, "SELECT principal_id::text FROM platform.users WHERE login='gina'")
    assert activated.json["id"] == principal_id

    user = sql_rows(
        stack.dbname,
        "SELECT password_hash, must_change_password FROM platform.users WHERE login = 'gina'",
    )[0]
    assert user["must_change_password"] is False
    assert user["password_hash"].startswith("$argon2id$v=19$m=65536,t=3,p=2$")
    assert sql(
        stack.dbname,
        "SELECT consumed_at IS NOT NULL FROM platform.activation_tokens",
    ) == "t"

    reuse = activate(stack, token, SECOND_PASSWORD)
    assert_error(reuse, 409, "activation_consumed")

    _client, login_response, _csrf = login(stack, "gina", USER_PASSWORD)
    assert login_response.status == 200, login_response.text


def test_expired_activation_is_rejected(stack: Stack) -> None:
    admin = admin_session(stack)
    token = invites_until_token(stack, admin, "henry")
    sql(
        stack.dbname,
        "UPDATE platform.activation_tokens SET expires_at = now() - interval '1 minute'",
    )

    response = activate(stack, token, USER_PASSWORD)
    assert_error(response, 409, "activation_expired")
    assert sql(
        stack.dbname,
        "SELECT consumed_at IS NULL FROM platform.activation_tokens",
    ) == "t"


def test_unknown_activation_token_is_rejected(stack: Stack) -> None:
    response = activate(stack, "not-a-real-activation-token", USER_PASSWORD)
    assert_error(response, 409, "activation_invalid")


def test_password_validation_does_not_consume_the_token(stack: Stack) -> None:
    admin = admin_session(stack)
    # The login itself is long enough to be equal to a candidate password.
    login_name = "iris.long.login"
    token = invites_until_token(stack, admin, login_name)

    weak = activate(stack, token, WEAK_PASSWORD)
    assert_error(weak, 422, "validation_failed")
    same_as_login = activate(stack, token, login_name)
    assert_error(same_as_login, 422, "validation_failed")

    # Neither rejection consumed the token or changed the sentinel.
    state = sql_rows(
        stack.dbname,
        "SELECT t.consumed_at::text AS consumed_at, u.password_hash "
        "FROM platform.activation_tokens t JOIN platform.users u ON u.principal_id = t.principal_id",
    )[0]
    assert state["consumed_at"] in (None, "")
    assert state["password_hash"] == "!pending-activation"

    accepted = activate(stack, token, USER_PASSWORD)
    assert accepted.status == 200, accepted.text
    _client, login_response, _csrf = login(stack, login_name, USER_PASSWORD)
    assert login_response.status == 200, login_response.text
