"""T017 acceptance: the login/logout pages actually drive the contract endpoints.

These assertions are behavioural in the sense the ticket asks for: the served
HTML and the served JavaScript must contain the forms, the contract paths, the
visible status elements and the redirect logic. No screenshot is involved, and
no token may appear in a URL, in the address bar or in browser storage.
"""

from __future__ import annotations

import pytest

from conftest import (
    api_server,
    fresh_database,
    go_binaries,
)


@pytest.fixture
def pages_server(fresh_database, api_server):
    # The pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def assert_no_token_in_url_or_storage(body: str, page: str) -> None:
    for forbidden in ("localStorage", "sessionStorage", "location.search", "URLSearchParams"):
        assert forbidden not in body, f"{page} uses {forbidden}"
    assert "?token=" not in body, f"{page} puts a token in a URL"
    assert "?csrf" not in body, f"{page} puts a CSRF token in a URL"


def test_login_page_is_served_and_posts_the_contract_path(pages_server) -> None:
    response = pages_server.http.get("/login")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="login-form"',
        'id="login"',
        'id="password"',
        'type="password"',
        "/assets/app.js",
        "/api/v1/auth/login",
        'id="login-status"',
        'role="status"',
        'href="/activate"',
        "window.location.assign('/admin')",
    ):
        assert marker in body, f"login.html is missing {marker!r}"

    # The two failures the page exists to explain are visible text, not a
    # console message.
    assert "invalid_credentials" in body
    assert "rate_limited" in body
    assert_no_token_in_url_or_storage(body, "login.html")
    # No credentials are remembered client-side.
    assert "autocomplete=\"current-password\"" in body


def test_admin_page_restores_identity_and_wires_logout(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    # T017 behaviour: identity is read back from /auth/me on every load, a 401
    # redirects to /login, writes take the CSRF token from that identity, and the
    # logout button posts /auth/logout.
    for marker in (
        'id="auth-status"',
        'id="identity-principal"',
        'id="identity-roles"',
        'id="logout-button"',
        "requireLogin(",
        "renderIdentity(",
        # The button uses the shared helper; the endpoint itself is asserted on
        # the served app.js below, so the path is not duplicated in every page.
        "await logout()",
        "location.assign('/login')",
        "error.status === 401",
        "csrf: await csrf()",
        'href="/login"',
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # T015 behaviour must survive: the invite form and the "shown once" token.
    for marker in (
        'id="signin-form"',
        'id="invite-form"',
        'id="invite-display-name"',
        'name="roles"',
        "/api/v1/admin/users",
        'id="invite-status"',
        'id="invite-result"',
        'id="activation-token"',
        "只显示一次",
    ):
        assert marker in body, f"admin.html lost the T015 marker {marker!r}"

    assert_no_token_in_url_or_storage(body, "admin.html")


def test_app_js_exposes_the_session_helpers(pages_server) -> None:
    response = pages_server.http.get("/assets/app.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text

    for marker in (
        "async function api(",
        "async function identity(",
        "async function csrf(",
        "async function requireLogin(",
        "async function logout(",
        "function escapeHTML(",
        "/api/v1/auth/me",
        "/api/v1/auth/logout",
        "X-CSRF-Token",
        "credentials: 'same-origin'",
        "redirect: 'error'",
    ):
        assert marker in body, f"app.js is missing {marker!r}"

    assert_no_token_in_url_or_storage(body, "app.js")
    # The identity lookup is the only source of the CSRF token.
    assert "current.csrf_token" in body


def test_activate_page_and_index_link_to_the_login_page(pages_server) -> None:
    for path in ("/activate", "/"):
        response = pages_server.http.get(path)
        assert response.status == 200, response.text
        assert 'href="/login"' in response.text, f"{path} has no link to /login"

    # The activation page keeps its own behaviour and never reads a URL token.
    activate = pages_server.http.get("/activate").text
    for marker in ('id="activate-form"', 'id="activation-token"', "/api/v1/auth/activate"):
        assert marker in activate, f"activate.html is missing {marker!r}"
    assert_no_token_in_url_or_storage(activate, "activate.html")


def test_only_named_pages_are_served(pages_server) -> None:
    for path in ("/login.html", "/admin.html"):
        response = pages_server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"
