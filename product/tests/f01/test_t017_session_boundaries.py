"""T017 acceptance 2/3/4: session boundaries and identity isolation.

The states are crafted the way a real deployment would reach them — an expired
session, a revoked session, a disabled principal — with direct SQL against the
same rows the API reads, then observed through the real HTTP surface. The
isolation test signs two different users in at the same time and proves that a
cookie is only ever answered with its own identity and its own CSRF token.
"""

from __future__ import annotations

from conftest import (
    assert_error,
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
    invite_and_activate,
    invite_body,
    stack,
)


def expire_session(stack: Stack, raw_cookie: str) -> None:
    sql(
        stack.dbname,
        "UPDATE platform.sessions SET expires_at = now() - interval '1 minute' "
        f"WHERE token_hash = '{sha256_hex(raw_cookie)}'",
    )


def revoke_session(stack: Stack, raw_cookie: str) -> None:
    sql(
        stack.dbname,
        "UPDATE platform.sessions SET revoked_at = now() "
        f"WHERE token_hash = '{sha256_hex(raw_cookie)}'",
    )


def disable_admin(stack: Stack) -> None:
    sql(
        stack.dbname,
        "UPDATE platform.principals SET disabled_at = now() "
        "WHERE id = (SELECT principal_id FROM platform.users WHERE login = 'admin')",
    )


def test_an_expired_session_is_rejected_on_a_read_and_on_a_write(stack: Stack) -> None:
    admin = admin_session(stack)
    expire_session(stack, admin.cookie)
    users_before = stack.count("platform.users")

    read = client_with_cookie(stack, admin.cookie).get("/api/v1/auth/me")
    assert_error(read, 401, "session_expired")
    assert read.header("Cache-Control") == "no-store"

    write = admin.client.post(
        "/api/v1/admin/users", body=invite_body("expired.probe"), csrf=admin.csrf
    )
    assert_error(write, 401, "session_expired")
    assert stack.count("platform.users") == users_before, "an expired session still wrote data"
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'expired.probe'"
    ) == "0"


def test_a_revoked_session_is_rejected_on_a_read_and_on_a_write(stack: Stack) -> None:
    admin = admin_session(stack)
    revoke_session(stack, admin.cookie)
    users_before = stack.count("platform.users")

    read = client_with_cookie(stack, admin.cookie).get("/api/v1/auth/me")
    assert_error(read, 401, "session_revoked")

    write = admin.client.post(
        "/api/v1/admin/users", body=invite_body("revoked.probe"), csrf=admin.csrf
    )
    assert_error(write, 401, "session_revoked")
    assert stack.count("platform.users") == users_before
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'revoked.probe'"
    ) == "0"


def test_a_disabled_principal_is_rejected_on_a_read_and_on_a_write(stack: Stack) -> None:
    admin = admin_session(stack)
    disable_admin(stack)
    users_before = stack.count("platform.users")

    read = admin.client.get("/api/v1/auth/me")
    assert_error(read, 403, "principal_disabled")
    assert read.header("Cache-Control") == "no-store"

    write = admin.client.post(
        "/api/v1/admin/users", body=invite_body("disabled.probe"), csrf=admin.csrf
    )
    assert_error(write, 403, "principal_disabled")
    assert stack.count("platform.users") == users_before
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'disabled.probe'"
    ) == "0"

    # Disabling is not a logout: the row is live, the principal is what refuses.
    assert sql(
        stack.dbname,
        f"SELECT revoked_at IS NULL FROM platform.sessions "
        f"WHERE token_hash = '{sha256_hex(admin.cookie)}'",
    ) == "t"


def test_two_signed_in_users_never_see_each_others_identity(stack: Stack) -> None:
    admin = admin_session(stack)
    second = invite_and_activate(
        stack,
        admin,
        "second.admin",
        roles=("admin", "creator"),
        display_name="Second Admin",
    )
    assert second.principal_id != admin.principal_id
    assert second.cookie != admin.cookie
    assert second.csrf != admin.csrf

    a_client = client_with_cookie(stack, admin.cookie)
    b_client = client_with_cookie(stack, second.cookie)

    # Repeat: a cookie must always answer with the same identity, never with the
    # other user's, no matter what the previous request was.
    for _ in range(3):
        me_a = a_client.get("/api/v1/auth/me")
        me_b = b_client.get("/api/v1/auth/me")
        assert me_a.status == 200 and me_b.status == 200, (me_a.text, me_b.text)
        assert me_a.header("Cache-Control") == "no-store"
        assert me_b.header("Cache-Control") == "no-store"

        assert me_a.json["principal_id"] == admin.principal_id
        assert me_b.json["principal_id"] == second.principal_id
        assert me_a.json["roles"] == ["admin"]
        assert me_b.json["roles"] == ["admin", "creator"]
        assert me_a.json["csrf_token"] == admin.csrf
        assert me_b.json["csrf_token"] == second.csrf
        assert me_a.json["csrf_token"] != me_b.json["csrf_token"]

        assert second.principal_id not in me_a.text
        assert admin.principal_id not in me_b.text

    # A's CSRF token presented with B's cookie is a forged request: B may be an
    # admin, so the refusal can only come from the CSRF comparison.
    principals_before = stack.count("platform.principals")
    forged = b_client.post(
        "/api/v1/admin/users", body=invite_body("isolation.probe"), csrf=admin.csrf
    )
    assert_error(forged, 403, "csrf_invalid")
    assert stack.count("platform.principals") == principals_before
    assert sql(
        stack.dbname, "SELECT count(*) FROM platform.users WHERE login = 'isolation.probe'"
    ) == "0"

    # B with its own token still works, so the refusal was the token, not B.
    accepted = b_client.post(
        "/api/v1/admin/users", body=invite_body("isolation.ok"), csrf=second.csrf
    )
    assert accepted.status == 201, accepted.text

    # A's identity is unchanged by B's activity.
    me_a_again = a_client.get("/api/v1/auth/me")
    assert me_a_again.status == 200
    assert me_a_again.json["principal_id"] == admin.principal_id
    assert me_a_again.json["csrf_token"] == admin.csrf
