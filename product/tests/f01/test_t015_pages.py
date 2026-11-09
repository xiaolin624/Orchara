"""T015 acceptance 7: the admin and activation pages actually drive the API.

The assertions are behavioural where possible: the page must contain the form
fields, post to the contract endpoints, and expose visible status elements for
success and failure. The shared helper must exist and must never place a token in
a URL.
"""

from __future__ import annotations

import re
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
    """The root enters chat; the real process-status page remains available."""
    root = pages_server.http.get("/")
    assert root.status == 303 and root.header("Location") == "/chat"
    response = pages_server.http.get("/status")
    assert response.status == 200, response.text
    body = response.text
    for marker in ("平台状态", "/health/live", "/api/status", "运行中", "status === 'alive'"):
        assert marker in body, f"/ is missing {marker!r}"
    for link in ('href="/login"', 'src="/assets/nav.js"'):
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
            "<title>管理台 · Orchara</title>", f"<title>{marker}</title>"
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


# A form element opens with <form ...> and closes with </form>; no page nests
# forms, so a non-greedy block match gives one block per real form.
_FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_FORM_BLOCK = re.compile(r"<form\b.*?</form>", re.IGNORECASE | re.DOTALL)
_METHOD_POST = re.compile(r"\bmethod\s*=\s*[\"']?post[\"']?", re.IGNORECASE)
_ACTION = re.compile(r"\baction\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)


def test_every_form_posts_and_no_action_carries_a_query_string(pages_server) -> None:
    """With JavaScript disabled or not yet loaded, a browser must not GET a form.

    A form without ``method`` defaults to GET, which puts the password, the
    activation token and the CSRF-bearing fields into the query string and the
    browser history -- exactly what "tokens and passwords never appear in a URL"
    forbids. The JS handlers remove the submit event, but that is not the only
    way a form can be submitted; ``method="post"`` is the markup-level guard.

    The page paths are the real served pages, so the assertion covers what the
    browser receives, not only the files on disk.
    """
    expected_forms = {"/login": 1, "/activate": 1, "/admin": 9}
    total = 0
    for path, expected_count in expected_forms.items():
        response = pages_server.http.get(path)
        assert response.status == 200, response.text
        body = response.text
        forms = _FORM_TAG.findall(body)
        assert len(forms) == expected_count, f"{path} has {len(forms)} forms: {forms}"
        total += len(forms)

        for form in forms:
            assert _METHOD_POST.search(form), (
                f"{path} serves a form without method=post, so a no-JS submit "
                f"would leak its fields into the query string: {form}"
            )
            action = _ACTION.search(form)
            if action is not None:
                assert "?" not in action.group(1), (
                    f"{path} puts a query string in a form action: {form}"
                )

        # The sensitive fields live inside a form (and therefore inside the
        # method=post guard), not in a bare element outside it.
        blocks = _FORM_BLOCK.findall(body)
        sensitive = {
            "/login": ('type="password"',),
            "/activate": ('id="activation-token"', 'type="password"'),
            # The admin page's one-time token display is a readonly textarea
            # outside any form (it is never submitted); its submitted sensitive
            # field is the sign-in password.
            "/admin": ('type="password"',),
        }[path]
        for marker in sensitive:
            assert any(marker in block for block in blocks), (
                f"{path} has a sensitive field {marker!r} outside every form"
            )
    # 1 login + 1 activation + 9 admin forms; a new form must be covered too.
    assert total == 11, f"expected the three pages to contain 11 forms, found {total}"
