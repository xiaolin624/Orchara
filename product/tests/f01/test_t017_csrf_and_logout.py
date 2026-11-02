"""T017 acceptance 2: a forged write is refused and changes nothing.

Every unsafe request that authenticates with the session cookie must present
``X-CSRF-Token`` whose SHA-256 equals ``sessions.csrf_hash``. A missing or wrong
token is 403 ``csrf_invalid`` and the handler never runs, so the "no state
change" assertions below are database facts, not opinions.

Logout is part of this file because it is the other state-changing endpoint that
must survive a forged request: 403 leaves the session alive, 204 revokes exactly
one session and clears the browser cookie.
"""

from __future__ import annotations

import pytest

from conftest import (
    SESSION_COOKIE,
    assert_error,
    cookie_attributes,
    sha256_hex,
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
    invite_body,
    session_for,
    stack,
)


def assert_no_principal_named(stack: Stack, login: str) -> None:
    assert sql(
        stack.dbname, f"SELECT count(*) FROM platform.users WHERE login = '{login}'"
    ) == "0", f"a refused request still created {login}"


def test_invite_without_a_csrf_header_is_refused_and_creates_nothing(stack: Stack) -> None:
    admin = admin_session(stack)
    principals_before = stack.count("platform.principals")
    users_before = stack.count("platform.users")
    sessions_before = stack.count("platform.sessions")

    refused = admin.client.post("/api/v1/admin/users", body=invite_body("csrf.missing"))

    error = assert_error(refused, 403, "csrf_invalid")
    assert error["message"]
    assert refused.header("Cache-Control") == "no-store"
    assert stack.count("platform.principals") == principals_before
    assert stack.count("platform.users") == users_before
    assert stack.count("platform.sessions") == sessions_before
    assert_no_principal_named(stack, "csrf.missing")

    # A forged write must not quietly end the session either.
    assert admin.client.get("/api/v1/auth/me").status == 200


@pytest.mark.parametrize(
    "presented",
    [
        "",
        "not-a-csrf-token",
        "A" * 43,
        # The stored hash is not the preimage; sending it must not pass.
        "REPLACE_WITH_STORED_HASH",
    ],
)
def test_invite_with_a_wrong_csrf_token_is_refused(stack: Stack, presented: str) -> None:
    admin = admin_session(stack)
    if presented == "REPLACE_WITH_STORED_HASH":
        presented = sql(
            stack.dbname,
            f"SELECT csrf_hash FROM platform.sessions WHERE token_hash = '{sha256_hex(admin.cookie)}'",
        )
    principals_before = stack.count("platform.principals")

    refused = admin.client.post(
        "/api/v1/admin/users", body=invite_body("csrf.wrong"), csrf=presented
    )

    assert_error(refused, 403, "csrf_invalid")
    assert stack.count("platform.principals") == principals_before
    assert_no_principal_named(stack, "csrf.wrong")


def test_a_csrf_token_from_another_session_is_refused(stack: Stack) -> None:
    admin = admin_session(stack)
    other = session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)
    assert other.csrf != admin.csrf

    principals_before = stack.count("platform.principals")

    # A's cookie + B's token: the hash of B's token is not A's stored hash.
    forged = admin.client.post(
        "/api/v1/admin/users", body=invite_body("csrf.crossed"), csrf=other.csrf
    )
    assert_error(forged, 403, "csrf_invalid")
    assert stack.count("platform.principals") == principals_before
    assert_no_principal_named(stack, "csrf.crossed")

    # Each session still works with its own token, so the refusal was the mix.
    accepted = admin.client.post(
        "/api/v1/admin/users", body=invite_body("csrf.accepted"), csrf=admin.csrf
    )
    assert accepted.status == 201, accepted.text
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'csrf.accepted'"
    ) == "1"


def test_logout_without_a_valid_csrf_token_leaves_the_session_alive(stack: Stack) -> None:
    first = admin_session(stack)
    second = session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)

    missing = first.client.post("/api/v1/auth/logout")
    assert_error(missing, 403, "csrf_invalid")
    assert first.client.get("/api/v1/auth/me").status == 200

    wrong = first.client.post("/api/v1/auth/logout", csrf=second.csrf)
    assert_error(wrong, 403, "csrf_invalid")
    assert first.client.get("/api/v1/auth/me").status == 200
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.sessions WHERE revoked_at IS NOT NULL"
    ) == "0"


def test_logout_revokes_only_that_session_and_clears_the_cookie(stack: Stack) -> None:
    first = admin_session(stack)
    second = session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)

    logged_out = first.client.post("/api/v1/auth/logout", csrf=first.csrf)

    assert logged_out.status == 204, logged_out.text
    assert logged_out.raw == b"", "204 must not carry a body"
    assert logged_out.header("Cache-Control") == "no-store"

    clearing = [
        value
        for value in logged_out.header_values("Set-Cookie")
        if value.startswith(SESSION_COOKIE + "=")
    ]
    assert len(clearing) == 1, logged_out.header_values("Set-Cookie")
    attributes = cookie_attributes(clearing[0])
    assert attributes["value"] == ""
    assert attributes["max-age"] == "0"
    assert attributes["path"] == "/"
    assert "expires" in attributes and "1970" in attributes["expires"], attributes

    # Exactly one row is revoked, and only the logout's own session.
    revoked = sql(
        stack.dbname,
        "SELECT count(*) FROM platform.sessions WHERE revoked_at IS NOT NULL",
    )
    assert revoked == "1"
    assert sql(
        stack.dbname,
        f"SELECT revoked_at IS NOT NULL FROM platform.sessions "
        f"WHERE token_hash = '{sha256_hex(first.cookie)}'",
    ) == "t"
    assert sql(
        stack.dbname,
        f"SELECT revoked_at IS NULL FROM platform.sessions "
        f"WHERE token_hash = '{sha256_hex(second.cookie)}'",
    ) == "t"

    # The browser dropped the cookie...
    assert first.client.cookie(SESSION_COOKIE) is None, "the clearing cookie was not honoured"
    # ...and a client that keeps sending the old value is rejected by the server.
    stale = client_with_cookie(stack, first.cookie)
    assert_error(stale.get("/api/v1/auth/me"), 401, "session_revoked")
    assert_error(
        stale.post("/api/v1/auth/logout", csrf=first.csrf), 401, "session_revoked"
    )

    # The other session is untouched.
    still_there = second.client.get("/api/v1/auth/me")
    assert still_there.status == 200, still_there.text
    assert still_there.json["principal_id"] == second.principal_id


def test_logout_without_a_session_is_unauthenticated(stack: Stack) -> None:
    anonymous = stack.client()
    assert_error(anonymous.post("/api/v1/auth/logout"), 401, "unauthenticated")
    assert_error(
        anonymous.post("/api/v1/auth/logout", csrf="any-token"),
        401,
        "unauthenticated",
    )
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.sessions WHERE revoked_at IS NOT NULL"
    ) == "0"
