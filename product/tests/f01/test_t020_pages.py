"""T020 acceptance 8: the admin page drives the service-account and key endpoints.

Like the T015/T017/T019 page tests, this is a behavioural assertion for a page
without a browser: the served HTML must contain the contract paths, the forms,
the one-time-secret rendering and visible feedback for success and failure. The
page must never put a secret in a URL, in browser storage or in a link.
"""

from __future__ import annotations

import pytest

from conftest import (
    api_server,
    assert_no_token_in_url_or_storage,
    fresh_database,
    go_binaries,
)


@pytest.fixture
def pages_server(fresh_database, api_server):
    # Pages do not read the database, but the API refuses to start without one.
    dsn, _dbname = fresh_database
    return api_server(dsn)


def test_admin_page_has_the_service_account_form(pages_server) -> None:
    response = pages_server.http.get("/admin")
    assert response.status == 200, response.text
    body = response.text

    for marker in (
        'id="service-accounts-panel"',
        'id="service-account-form"',
        'id="sa-display-name"',
        'id="sa-responsible"',
        'id="sa-status"',
        'id="sa-result"',
        'id="sa-principal-id"',
        "/api/v1/admin/service-accounts",
        "responsible_user_id",
        "display_name",
        "setStatus(serviceAccountStatus",
        "csrf: await csrf()",
        "error.status === 401",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # A service account is described as a separate, non-loginable principal.
    assert "没有登录密码" in body or "不能登录" in body


def test_admin_page_renders_the_one_time_key_and_a_revoke_form(pages_server) -> None:
    response = pages_server.http.get("/admin")
    body = response.text

    for marker in (
        'id="api-keys-panel"',
        'id="api-key-form"',
        'id="api-key-principal"',
        'id="api-key-scope-application-read"',
        'name="scopes"',
        'value="application:read"',
        'id="api-key-expires"',
        'id="api-key-status"',
        'id="api-key-result"',
        'id="api-key-secret"',
        "readonly",
        'id="api-key-id"',
        "/api/v1/admin/api-keys",
        "method: 'DELETE'",
        'id="api-key-revoke-form"',
        'id="revoke-api-key-id"',
        'id="api-key-revoke-status"',
        "明文只显示一次",
        "setStatus(apiKeyStatus",
        "setStatus(apiKeyRevokeStatus",
        "apiKeySecretField.value = created.secret",
        "csrf: await csrf()",
    ):
        assert marker in body, f"admin.html is missing {marker!r}"

    # The secret is never placed in a link, storage, or an address bar.
    assert_no_token_in_url_or_storage(body, "admin.html")


def test_the_existing_admin_sections_survive_the_t020_additions(pages_server) -> None:
    body = pages_server.http.get("/admin").text
    for marker in (
        'id="signin-form"',
        'id="invite-form"',
        'id="activation-token"',
        'id="auth-status"',
        'id="identity-principal"',
        "只显示一次",
        'id="grants-panel"',
        'id="grant-form"',
        'id="grant-status"',
        'id="revoke-form"',
        'id="roles-panel"',
        'id="roles-form"',
        "Orchara 管理台（F01 身份）",
    ):
        assert marker in body, f"admin.html lost {marker!r}"
