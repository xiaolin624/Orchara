"""T017 acceptance: five failed sign-ins throttle the key for 60 seconds.

The limiter is per process and in memory, so the observable contract is what the
HTTP surface does: after five failures for one (login, peer address) the sixth
request is refused with 429 ``rate_limited`` and ``Retry-After: 60`` even when
the password is finally correct, another login is unaffected, success clears the
counter, and the login is matched case-insensitively. Window expiry itself is
covered by the Go unit test with an injected clock
(``internal/api/loginlimit_test.go``), so nothing here sleeps for 60 seconds.
"""

from __future__ import annotations

import secrets

from conftest import assert_error, sql
from t017_stack import (  # noqa: F401 - fixtures are used through their names
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    Stack,
    admin_session,
    api_server,
    fresh_database,
    go_binaries,
    invite_and_activate,
    sign_in,
    stack,
)

# Generated per test process: the fixture contains no fixed password string.
WRONG_PASSWORD = "t017-wrong-" + secrets.token_urlsafe(18)


def fail_login(stack: Stack, client, login: str = ADMIN_LOGIN):
    client, response = sign_in(stack, login, WRONG_PASSWORD, client=client)
    return response


def test_five_failures_throttle_the_login_even_for_the_right_password(stack: Stack) -> None:
    admin = admin_session(stack)
    invite_and_activate(stack, admin, "rate.user")
    sessions_before = stack.count("platform.sessions")

    attacker = stack.client()
    for attempt in range(5):
        response = fail_login(stack, attacker)
        assert_error(response, 401, "invalid_credentials")

    blocked = attacker.post(
        "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
    )
    assert_error(blocked, 429, "rate_limited")
    assert blocked.header("Retry-After") == "60", blocked.header("Retry-After")
    assert blocked.header("Cache-Control") == "no-store"
    assert stack.count("platform.sessions") == sessions_before, "a throttled login created a session"

    # A different login is not affected by the throttled key.
    other = attacker.post(
        "/api/v1/auth/login", body={"login": "rate.user", "password": USER_PASSWORD}
    )
    assert other.status == 200, other.text

    # The throttled key is still throttled, and the window was not extended into
    # a lockout of the whole process: a fresh login of another user keeps working.
    again = attacker.post(
        "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
    )
    assert_error(again, 429, "rate_limited")


def test_a_successful_login_clears_its_own_failure_counter(stack: Stack) -> None:
    client = stack.client()
    for _ in range(4):
        assert_error(fail_login(stack, client), 401, "invalid_credentials")

    accepted = client.post(
        "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
    )
    assert accepted.status == 200, accepted.text

    # If the counter had not been cleared, the fourth or fifth new failure would
    # already be refused; instead every one is a plain 401.
    for _ in range(5):
        assert_error(fail_login(stack, client), 401, "invalid_credentials")

    # Five fresh failures do trip it again.
    assert_error(
        client.post(
            "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
        ),
        429,
        "rate_limited",
    )


def test_the_throttle_key_lowercases_the_login(stack: Stack) -> None:
    client = stack.client()
    for presented in ("ADMIN", "Admin", "admin", "aDmIn", " admin "):
        response = client.post(
            "/api/v1/auth/login", body={"login": presented, "password": WRONG_PASSWORD}
        )
        assert_error(response, 401, "invalid_credentials")

    blocked = client.post(
        "/api/v1/auth/login", body={"login": ADMIN_LOGIN, "password": ADMIN_PASSWORD}
    )
    assert_error(blocked, 429, "rate_limited")


def test_the_trip_is_per_peer_and_not_global(stack: Stack) -> None:
    """A different login keeps its own budget (the address is the same here)."""
    client = stack.client()
    for _ in range(5):
        assert_error(fail_login(stack, client, login="ghost"), 401, "invalid_credentials")

    # A login that exists still works from the same peer address.
    admin = admin_session(stack)
    assert admin.client.get("/api/v1/auth/me").status == 200
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.sessions WHERE revoked_at IS NULL"
    ) == "1"
