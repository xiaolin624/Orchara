"""T021 acceptance 8: the admin page drives the disable/enable endpoint.

Like the T015/T017/T019/T020 page tests, this is a behavioural assertion for a
page without a browser: the served HTML must contain the form, the contract
path, the CSRF-protected write and visible feedback for success and for the
contract failures this ticket introduces. Existing ids, the page title and the
"只显示一次" strings must survive.
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
    # Pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def assert_no_secret_in_url_or_storage(body: str) -> None:
    for forbidden in ("localStorage", "sessionStorage", "location.search", "URLSearchParams"):
        assert forbidden not in body, f"admin.html uses {forbidden}"
    assert "?token=" not in body, "admin.html puts a token in a URL"
    assert "?csrf" not in body, "admin.html puts a CSRF token in a URL"


def test_admin_page_has_the_disable_form_and_feedback(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    assert response.header("Cache-Control") == "no-store"
    body = response.text

    for marker in (
        'id="principals-panel"',
        'id="disable-form"',
        'id="disable-principal"',
        'id="disable-state"',
        'id="disable-reason"',
        'id="disable-status"',
        "/api/v1/admin/principals/",
        "method: 'PATCH'",
        "csrf: await csrf()",
        "setStatus(disableStatus",
        "error.status === 401",
        "principalsPanel.hidden = !isAdmin",
        # The two contract failures this panel is expected to show.
        "last_admin",
        "principal_not_found",
        # Why re-enabling does not restore credentials.
        "不会恢复",
        # Why principal rows are never deleted.
        "永远不会被删除",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # The reason is required and is described as an audit field.
    assert 'required placeholder="例如：离职交接"' in body or "原因（必填" in body
    assert_no_secret_in_url_or_storage(body)


def test_the_existing_admin_sections_survive_the_t021_additions(pages_server) -> None:
    body = pages_server.http.get("/admin").text
    for marker in (
        "管理台 · Orchara",
        "只显示一次",
        'id="signin-form"',
        'id="invite-form"',
        'id="activation-token"',
        'id="auth-status"',
        'id="identity-principal"',
        'id="grants-panel"',
        'id="grant-form"',
        'id="grant-status"',
        'id="revoke-form"',
        'id="roles-panel"',
        'id="roles-form"',
        'id="service-accounts-panel"',
        'id="service-account-form"',
        'id="sa-status"',
        'id="api-keys-panel"',
        'id="api-key-form"',
        'id="api-key-secret"',
        'id="api-key-revoke-form"',
        "/api/v1/admin/service-accounts",
        "/api/v1/admin/api-keys",
        "/api/v1/admin/users",
    ):
        assert marker in body, f"admin.html lost {marker!r}"
