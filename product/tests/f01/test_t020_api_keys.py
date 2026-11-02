"""T020 acceptance 2: a scoped key whose secret is shown exactly once.

The database receives only the SHA-256 of the secret. Every refusal is typed and
leaves the ``api_keys`` and audit tables unchanged. All assertions run against a
real throwaway PostgreSQL database and the compiled API binary.
"""

from __future__ import annotations

import datetime as dt
import re

from conftest import assert_error, sha256_hex, sql, sql_rows
from t020_stack import (  # noqa: F401 - fixtures and helpers
    admin_session,
    api_server,
    audit_metadata,
    create_api_key,
    create_api_key_or_fail,
    create_service_account_or_fail,
    disable_principal,
    fresh_database,
    future_rfc3339,
    go_binaries,
    invite_and_activate,
    past_rfc3339,
    revoke_api_key,
    stack,
)

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _key_rows(dbname: str, principal_id: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT id::text AS id, principal_id::text AS principal_id, key_hash, scopes, "
        "expires_at::text AS expires_at, revoked_at::text AS revoked_at "
        f"FROM platform.api_keys WHERE principal_id = '{principal_id}'::uuid",
    )


def test_an_admin_issues_a_one_time_key_for_a_service_account(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-key-holder", admin.principal_id)
    expires_at = future_rfc3339(3)

    response = create_api_key(admin, service_id, expires_at=expires_at)
    assert response.status == 201, response.text
    assert response.header("Cache-Control") == "no-store"
    body = response.json
    assert set(body) == {"id", "secret"}, body
    key_id, secret = body["id"], body["secret"]
    assert secret.startswith("orchara_")
    # `orchara_` + 43 base64url characters of 32 random bytes.
    assert re.match(r"^orchara_[A-Za-z0-9_-]{43}$", secret), secret

    rows = _key_rows(stack.dbname, service_id)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["id"] == key_id
    assert row["principal_id"] == service_id
    assert row["scopes"] == ["application:read"]
    assert row["revoked_at"] is None
    # Only the hash is stored: 64 lowercase hex characters, and the raw secret is
    # not recoverable from any column.
    assert HEX64.match(row["key_hash"]), row["key_hash"]
    assert row["key_hash"] == sha256_hex(secret)
    assert secret not in str(row)
    stored_expiry = dt.datetime.fromisoformat(row["expires_at"])
    assert stored_expiry == dt.datetime.fromisoformat(expires_at.replace("Z", "+00:00"))

    events = audit_metadata(stack.dbname, "api_key.created")
    assert len(events) == 1, events
    event = events[0]
    assert event["actor_id"] == admin.principal_id
    assert event["resource_id"] == key_id
    assert event["principal_id"] == service_id
    assert event["scopes"] == ["application:read"]
    assert event["expires_at"] == expires_at
    # Auditing records the fact, never the credential or its hash.
    assert secret not in str(event)
    assert sha256_hex(secret) not in str(event)


def test_key_creation_validation_is_typed_and_creates_nothing(stack) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t020-key-responsible", roles=("user",))
    service_id = create_service_account_or_fail(admin, "svc-validation", responsible.principal_id)

    # Disabling the responsible human after the account exists makes key
    # issuance fail with a semantic 422; the admin session is unaffected because
    # that human is a different account.
    disable_principal(stack.dbname, responsible.principal_id)

    cases = [
        {
            "name": "human target",
            "principal_id": admin.principal_id,
            "scopes": ["application:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "principal_id",
        },
        {
            "name": "unknown target",
            "principal_id": "11111111-1111-4111-8111-111111111111",
            "scopes": ["application:read"],
            "expires_at": future_rfc3339(),
            "status": 404,
            "code": "principal_not_found",
            "field": None,
        },
        {
            "name": "malformed target",
            "principal_id": "not-a-uuid",
            "scopes": ["application:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "principal_id",
        },
        {
            "name": "personal scope",
            "principal_id": service_id,
            "scopes": ["personal:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "personal_scope_forbidden",
            "field": "scopes",
        },
        {
            "name": "memory scope",
            "principal_id": service_id,
            "scopes": ["memory:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "personal_scope_forbidden",
            "field": "scopes",
        },
        {
            "name": "unknown scope",
            "principal_id": service_id,
            # application:write is defined by F02A/T023; an out-of-vocabulary
            # scope must still be refused.
            "scopes": ["application:execute"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "scopes",
        },
        {
            "name": "empty scopes",
            "principal_id": service_id,
            "scopes": [],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "scopes",
        },
        {
            "name": "duplicate scopes",
            "principal_id": service_id,
            "scopes": ["application:read", "application:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "scopes",
        },
        {
            "name": "past expiry",
            "principal_id": service_id,
            "scopes": ["application:read"],
            "expires_at": past_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "expires_at",
        },
        {
            "name": "malformed expiry",
            "principal_id": service_id,
            "scopes": ["application:read"],
            "expires_at": "the day after tomorrow",
            "status": 422,
            "code": "validation_failed",
            "field": "expires_at",
        },
        {
            "name": "disabled responsible user",
            "principal_id": service_id,
            "scopes": ["application:read"],
            "expires_at": future_rfc3339(),
            "status": 422,
            "code": "validation_failed",
            "field": "principal_id",
        },
    ]
    keys_before = stack.count("platform.api_keys")
    audit_before = len(audit_metadata(stack.dbname, "api_key.created"))
    for case in cases:
        response = create_api_key(
            admin,
            case["principal_id"],
            scopes=case["scopes"],
            expires_at=case["expires_at"],
        )
        error = assert_error(response, case["status"], case["code"])
        if case["field"]:
            assert error.get("details", {}).get("field") == case["field"], (case, error)
        assert stack.count("platform.api_keys") == keys_before, case
        assert len(audit_metadata(stack.dbname, "api_key.created")) == audit_before, case


def test_key_creation_requires_an_admin_session_and_csrf(stack) -> None:
    admin = admin_session(stack)
    non_admin = invite_and_activate(stack, admin, "t020-key-nonadmin", roles=("user",))
    service_id = create_service_account_or_fail(admin, "svc-key-admin", admin.principal_id)
    keys_before = stack.count("platform.api_keys")
    audit_before = len(audit_metadata(stack.dbname, "api_key.created"))

    body = {
        "principal_id": service_id,
        "scopes": ["application:read"],
        "expires_at": future_rfc3339(),
    }
    response = non_admin.client.post("/api/v1/admin/api-keys", body=body, csrf=non_admin.csrf)
    assert_error(response, 403, "forbidden")

    response = admin.client.post("/api/v1/admin/api-keys", body=body)
    assert_error(response, 403, "csrf_invalid")

    assert stack.count("platform.api_keys") == keys_before
    assert len(audit_metadata(stack.dbname, "api_key.created")) == audit_before


def test_revocation_is_idempotent_audited_and_typed(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-revoke", admin.principal_id)
    key_id, _secret = create_api_key_or_fail(admin, service_id)

    response = revoke_api_key(admin, key_id)
    assert response.status == 204, response.text
    assert response.raw == b""
    assert response.header("Cache-Control") == "no-store"
    row = _key_rows(stack.dbname, service_id)[0]
    assert row["revoked_at"] is not None

    revoked = audit_metadata(stack.dbname, "api_key.revoked")
    assert len(revoked) == 1, revoked
    assert revoked[0]["actor_id"] == admin.principal_id
    assert revoked[0]["resource_id"] == key_id
    assert revoked[0]["principal_id"] == service_id

    # A second delete of the same key is success without a second audit row.
    assert revoke_api_key(admin, key_id).status == 204
    assert len(audit_metadata(stack.dbname, "api_key.revoked")) == 1

    # Unknown and malformed ids are the same typed 404.
    for unknown in ("11111111-1111-4111-8111-111111111111", "not-a-uuid"):
        response = revoke_api_key(admin, unknown)
        assert_error(response, 404, "key_not_found")
    assert sql(stack.dbname, "SELECT count(*) FROM platform.api_keys") == "1"
