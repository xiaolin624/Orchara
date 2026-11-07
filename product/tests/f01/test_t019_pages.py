"""T019 acceptance 6: the pages are real clients of the contract endpoints.

Like the T015/T017 page tests, these assertions are behavioural for a page
without a browser: the served HTML must contain the contract paths, the form
fields, the visible status elements and the 401 redirect, and must not put a
credential in a URL or in browser storage. JavaScript execution itself is not
run here (recorded as a limitation in the report).
"""

from __future__ import annotations

import re

import pytest

from conftest import (
    api_server,  # noqa: F401 - pytest fixture
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
)


@pytest.fixture
def pages_server(fresh_database, api_server):
    # The pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def assert_no_token_in_url_or_storage(body: str, page: str) -> None:
    """No credential may ride in a URL or sit in client storage.

    The pages do read a few *non-secret* query parameters: a run id (the chat
    page's run deep link), a fixed application version, and the stable code that
    tells the login page why the previous session ended. A blanket ban on
    ``location.search`` / ``URLSearchParams`` contradicted those, so the reads are
    checked against a small allowlist instead - an unrecognised parameter still
    fails the guard.
    """
    for forbidden in ("localStorage", "sessionStorage"):
        assert forbidden not in body, f"{page} uses {forbidden}"
    queried = set(re.findall(r"URLSearchParams\([^)]*\)\.get\(\s*'([^']+)'", body))
    allowed = {"error", "run", "version"}
    assert queried <= allowed, f"{page} reads unexpected query parameters: {sorted(queried - allowed)}"
    assert "?token=" not in body, f"{page} puts a token in a URL"
    assert "?csrf" not in body, f"{page} puts a CSRF token in a URL"


def test_the_apps_page_lists_the_visible_applications(pages_server) -> None:
    response = pages_server.http.get("/apps")
    assert response.status == 200, response.text
    assert "text/html" in response.header("Content-Type")
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="apps-list"',
        'id="apps-empty"',
        'id="apps-next"',
        'id="apps-status"',
        'role="status"',
        "/assets/app.js",
        "/api/v1/applications",
        "next_cursor",
        "active_version_id",
        "下一页",
        "当前没有可见的应用",
        "requireLogin(",
        # T095 centralised the session-failure branch in app.js: every page now
        # calls the shared helper instead of repeating `error.status === 401` and
        # its redirect. The helper's own 401 -> /login branch and its "empty the
        # session regions" rule are asserted on the served asset just below, so
        # the behaviour stays pinned instead of only being renamed.
        "handleAuthFailure(error)",
    ):
        # The identity read is via requireLogin(); the page never renders an
        # application before that succeeds.
        assert marker in body, f"apps.html is missing {marker!r}"

    app_js = pages_server.http.get("/assets/app.js")
    assert app_js.status == 200, app_js.text
    assert "error.status === 401" in app_js.text
    assert "window.location.assign(loginURLFor(error))" in app_js.text
    assert "[data-auth-content]" in app_js.text

    # The ACL wording is honest about default deny and about admin not being a
    # content reader.
    assert "管理员" in body
    assert "只显示" in body
    assert_no_token_in_url_or_storage(body, "apps.html")


def test_only_the_named_apps_path_is_served(pages_server) -> None:
    for path in ("/apps.html", "/apps/"):
        response = pages_server.http.get(path)
        assert response.status == 404, f"{path} unexpectedly returned {response.status}"


def test_the_admin_page_has_the_grant_revoke_and_role_forms(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    body = response.text

    for marker in (
        'id="grants-panel"',
        'id="grant-form"',
        'id="grant-principal"',
        'id="grant-resource"',
        'id="grant-action"',
        'id="grant-action" name="action"',
        'value="read"',
        'id="grant-expires"',
        'id="grant-status"',
        'id="grant-result"',
        'id="grant-id"',
        'id="revoke-form"',
        'id="revoke-grant-id"',
        'id="revoke-status"',
        'id="roles-panel"',
        'id="roles-form"',
        'id="roles-principal"',
        'id="roles-status"',
        "/api/v1/admin/grants",
        "/api/v1/admin/users/",
        "/roles",
        "resource_type: 'application'",
        "method: 'DELETE'",
        "method: 'PUT'",
        "csrf: await csrf()",
        "error.status === 401",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # The visible feedback the ticket asks for: ok / bad feedback on every write.
    for marker in ("setStatus(grantStatus", "setStatus(revokeStatus", "setStatus(rolesStatus"):
        assert marker in body, f"admin.html has no feedback for {marker!r}"

    # T015/T017 behaviour must survive the additions.
    for marker in (
        'id="invite-form"',
        'id="auth-status"',
        'id="identity-principal"',
        "只显示一次",
    ):
        assert marker in body, f"admin.html lost {marker!r}"

    assert_no_token_in_url_or_storage(body, "admin.html")
