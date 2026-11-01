"""T015 acceptance 7: the admin and activation pages actually drive the API.

The assertions are behavioural where possible: the page must contain the form
fields, post to the contract endpoints, and expose visible status elements for
success and failure. The shared helper must exist and must never place a token in
a URL.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import (
    WEB_DIR,
    api_server,  # noqa: F401 - pytest fixture
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
)


@pytest.fixture
def pages_server(fresh_database, api_server):
    # Pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def test_admin_page_serves_signin_and_invite_forms(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="signin-form"',
        'id="invite-form"',
        'id="login"',
        'id="password"',
        'id="invite-display-name"',
        'name="roles"',
        "/assets/app.js",
        "/api/v1/auth/login",
        "/api/v1/admin/users",
        'id="auth-status"',
        'id="invite-status"',
        'id="invite-result"',
        'id="activation-token"',
        "只显示一次",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # The token is rendered into a textarea and never into a link or storage.
    assert "location.search" not in body
    assert "localStorage" not in body
    assert "sessionStorage" not in body
    assert "?token" not in body


def test_activate_page_serves_token_and_password_fields(pages_server) -> None:
    response = pages_server.http.get("/activate")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    body = response.text

    for marker in (
        'id="activate-form"',
        'id="activation-token"',
        "<textarea",
        'id="password"',
        'id="confirm-password"',
        "/assets/app.js",
        "/api/v1/auth/activate",
        'id="activate-status"',
        'id="activate-success"',
        "body: { activation_token: activationToken, password }",
    ):
        assert marker in body, f"activate.html is missing {marker!r}"

    # A one-time token must never be read from the URL or persisted.
    assert "location.search" not in body
    assert "URLSearchParams" not in body
    assert "?token=" not in body
    assert "localStorage" not in body
    assert ".innerHTML" not in body


def test_app_js_is_the_shared_helper(pages_server) -> None:
    response = pages_server.http.get("/assets/app.js")
    assert response.status == 200, response.text
    assert "javascript" in response.header("Content-Type")
    body = response.text

    assert "async function api(" in body
    assert "function escapeHTML(" in body
    assert "JSON.stringify(options.body)" in body
    assert "X-CSRF-Token" in body
    assert "payload.error" in body
    assert "failure.code" in body
    assert "redirect: 'error'" in body


def test_root_page_keeps_its_strings_and_links(pages_server) -> None:
    """Regression: the F00 page content asserted by tests/f00 must survive."""
    response = pages_server.http.get("/")
    assert response.status == 200, response.text
    body = response.text
    for marker in ("Orchara 平台状态", "/health/live", "/api/status", "运行中", "status === 'alive'"):
        assert marker in body, f"/ is missing {marker!r}"
    for link in ('href="/admin"', 'href="/activate"', 'href="/apps"'):
        assert link in body, f"/ is missing the navigation link {link!r}"


def test_pages_are_served_from_orchara_web_dir(fresh_database, go_binaries, api_server, tmp_path: Path) -> None:
    dsn, _dbname = fresh_database
    web_dir = tmp_path / "web"
    (web_dir / "assets").mkdir(parents=True)
    for name in ("index.html", "admin.html", "activate.html"):
        shutil.copyfile(WEB_DIR / name, web_dir / name)
    shutil.copyfile(WEB_DIR / "assets" / "app.js", web_dir / "assets" / "app.js")

    marker = "served-from-a-temporary-web-dir"
    admin_path = web_dir / "admin.html"
    admin_path.write_text(
        admin_path.read_text(encoding="utf-8").replace(
            "<title>Orchara 管理台（F01 身份）</title>", f"<title>{marker}</title>"
        ),
        encoding="utf-8",
    )
    server = api_server(dsn, web_dir=web_dir)

    admin = server.http.get("/admin")
    assert admin.status == 200, admin.text
    assert marker in admin.text, "the API served a page from somewhere other than ORCHARA_WEB_DIR"

    app_js = server.http.get("/assets/app.js")
    assert app_js.status == 200
    assert "async function api(" in app_js.text


def test_unknown_page_is_not_served(pages_server) -> None:
    """Only the named pages are exposed; there is no directory listing."""
    for path in ("/admin.html", "/assets/"):
        response = pages_server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"
