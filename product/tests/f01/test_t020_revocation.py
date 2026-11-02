"""T020 acceptance 6: revocation and disablement take effect on the next call.

The API reads the key row on every request, so there is no cache to wait out: a
revoked or expired key is refused immediately, and disabling the service
principal or its named responsible human denies the still-valid secret with the
contract's 403 codes.
"""

from __future__ import annotations

from conftest import assert_error, sql
from t020_stack import (  # noqa: F401 - fixtures and helpers
    admin_session,
    api_server,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    disable_principal,
    expire_api_key,
    fresh_database,
    go_binaries,
    invite_and_activate,
    revoke_api_key,
    seed_api_key_row,
    stack,
)


def test_a_revoked_key_is_refused_on_the_next_call(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-revoke-now", admin.principal_id)
    key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    assert bearer.get("/api/v1/auth/me").status == 200

    assert revoke_api_key(admin, key_id).status == 204

    assert_error(bearer.get("/api/v1/auth/me"), 401, "invalid_key")
    assert_error(bearer.get("/api/v1/applications"), 401, "invalid_key")


def test_an_expired_key_is_refused(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-expiry", admin.principal_id)
    key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    assert bearer.get("/api/v1/auth/me").status == 200

    # Crafted state: the expiry is in the past, exactly as time will make it.
    expire_api_key(stack.dbname, key_id)

    assert_error(bearer.get("/api/v1/auth/me"), 401, "invalid_key")
    assert_error(bearer.get("/api/v1/applications"), 401, "invalid_key")


def test_disabling_the_responsible_user_immediately_denies_a_valid_key(stack) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t020-disabled-owner", roles=("user",))
    service_id = create_service_account_or_fail(admin, "svc-orphaned", responsible.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    assert bearer.get("/api/v1/auth/me").status == 200

    # Only the responsible human is disabled; the key row stays untouched and
    # unrevoked, which is exactly the "takes effect immediately" rule.
    disable_principal(stack.dbname, responsible.principal_id)
    assert sql(
        stack.dbname,
        f"SELECT count(*) FROM platform.api_keys WHERE principal_id = '{service_id}'::uuid "
        "AND revoked_at IS NULL AND expires_at > now()",
    ) == "1"

    assert_error(bearer.get("/api/v1/auth/me"), 403, "responsible_disabled")
    assert_error(bearer.get("/api/v1/applications"), 403, "responsible_disabled")


def test_disabling_the_service_principal_rejects_the_key(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-disabled", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)
    assert bearer.get("/api/v1/auth/me").status == 200

    disable_principal(stack.dbname, service_id)
    assert_error(bearer.get("/api/v1/auth/me"), 403, "principal_disabled")
    assert_error(bearer.get("/api/v1/applications"), 403, "principal_disabled")


def test_a_seeded_revoked_key_is_indistinguishable_from_unknown(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-seeded-revoked", admin.principal_id)
    _key_id, secret = seed_api_key_row(stack.dbname, service_id, revoked=True)
    assert_error(bearer_client(stack, secret).get("/api/v1/auth/me"), 401, "invalid_key")

    # An unknown secret gets the same response as a revoked one.
    assert_error(
        bearer_client(stack, "orchara_" + "A" * 43).get("/api/v1/auth/me"), 401, "invalid_key"
    )
