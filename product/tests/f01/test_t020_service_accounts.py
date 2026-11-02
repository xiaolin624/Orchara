"""T020 acceptance 1: a named, responsible service account is a separate principal.

Every assertion runs the compiled ``cmd/api`` / ``cmd/admin`` binaries against a
real throwaway PostgreSQL database over a real socket. The API refuses to create
a service principal without a named, enabled human owner, and the created
principal has no users row, so it can never sign in.
"""

from __future__ import annotations

import secrets
import uuid

from conftest import assert_error, sql_rows
from t020_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    admin_session,
    api_server,
    audit_metadata,
    create_service_account,
    create_service_account_or_fail,
    disable_principal,
    fresh_database,
    go_binaries,
    invite_and_activate,
    sign_in,
    sql,
    stack,
)


def _principal_rows(dbname: str, principal_id: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT id::text AS id, kind, display_name, disabled_at::text AS disabled_at, "
        "responsible_user_id::text AS responsible_user_id "
        f"FROM platform.principals WHERE id = '{principal_id}'::uuid",
    )


def test_an_admin_creates_a_named_service_account(stack) -> None:
    admin = admin_session(stack)
    response = create_service_account(admin, "svc-orders", admin.principal_id)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "state"}, body
    assert body["state"] == "active"
    principal_id = body["id"]
    uuid.UUID(principal_id)  # the contract says UUID, so the server must mint one

    rows = _principal_rows(stack.dbname, principal_id)
    assert len(rows) == 1, rows
    assert rows[0]["kind"] == "service"
    assert rows[0]["display_name"] == "svc-orders"
    assert rows[0]["responsible_user_id"] == admin.principal_id
    assert rows[0]["disabled_at"] is None

    # A service principal has no users row: it is not a login.
    assert sql(
        stack.dbname,
        f"SELECT count(*) FROM platform.users WHERE principal_id = '{principal_id}'::uuid",
    ) == "0"

    events = audit_metadata(stack.dbname, "service_account.created")
    assert len(events) == 1, events
    event = events[0]
    assert event["actor_id"] == admin.principal_id
    assert event["resource_id"] == principal_id
    assert event["display_name"] == "svc-orders"
    assert event["responsible_user_id"] == admin.principal_id
    assert "secret" not in str(event).lower()


def test_creating_a_service_account_requires_an_admin_session_and_csrf(stack) -> None:
    admin = admin_session(stack)
    non_admin = invite_and_activate(stack, admin, "t020-not-admin", roles=("user",))
    principals_before = stack.count("platform.principals")
    audit_before = len(audit_metadata(stack.dbname, "service_account.created"))

    # An authenticated human without the admin role: 403 forbidden.
    response = non_admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": "svc-forbidden", "responsible_user_id": admin.principal_id},
        csrf=non_admin.csrf,
    )
    assert_error(response, 403, "forbidden")

    # An admin without the CSRF proof: 403 csrf_invalid, and no state change.
    response = admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": "svc-no-csrf", "responsible_user_id": admin.principal_id},
    )
    assert_error(response, 403, "csrf_invalid")

    assert stack.count("platform.principals") == principals_before
    assert len(audit_metadata(stack.dbname, "service_account.created")) == audit_before


def test_service_account_validation_is_typed_and_creates_nothing(stack) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t020-responsible", roles=("user",))
    disable_principal(stack.dbname, responsible.principal_id)
    first = create_service_account_or_fail(admin, "svc-owner", admin.principal_id)

    cases = [
        {
            "name": "unknown responsible",
            "display_name": "svc-unknown-owner",
            "responsible": str(uuid.uuid4()),
            "status": 404,
            "code": "principal_not_found",
            "field": None,
        },
        {
            "name": "disabled responsible",
            "display_name": "svc-disabled-owner",
            "responsible": responsible.principal_id,
            "status": 422,
            "code": "validation_failed",
            "field": "responsible_user_id",
        },
        {
            "name": "service account as responsible",
            "display_name": "svc-service-owner",
            "responsible": first,
            "status": 422,
            "code": "validation_failed",
            "field": "responsible_user_id",
        },
        {
            "name": "blank display name",
            "display_name": "   ",
            "responsible": admin.principal_id,
            "status": 422,
            "code": "validation_failed",
            "field": "display_name",
        },
        {
            "name": "malformed responsible id",
            "display_name": "svc-bad-owner",
            "responsible": "not-a-uuid",
            "status": 422,
            "code": "validation_failed",
            "field": "responsible_user_id",
        },
    ]
    principals_before = stack.count("platform.principals")
    audit_before = len(audit_metadata(stack.dbname, "service_account.created"))
    for case in cases:
        response = create_service_account(admin, case["display_name"], case["responsible"])
        error = assert_error(response, case["status"], case["code"])
        if case["field"]:
            assert error.get("details", {}).get("field") == case["field"], (case, error)
        assert stack.count("platform.principals") == principals_before, case
        assert len(audit_metadata(stack.dbname, "service_account.created")) == audit_before, case


def test_a_service_account_cannot_sign_in(stack) -> None:
    admin = admin_session(stack)
    create_service_account_or_fail(admin, "svc-login-probe", admin.principal_id)

    probe_password = "t020-probe-" + secrets.token_urlsafe(18)
    client, response = sign_in(stack, "svc-login-probe", probe_password)
    # Same invalid_credentials answer as every unknown login; no new principal.
    assert_error(response, 401, "invalid_credentials")
    assert client.cookie() is None
    assert sql(
        stack.dbname,
        "SELECT count(*) FROM platform.users WHERE login = 'svc-login-probe'",
    ) == "0"
