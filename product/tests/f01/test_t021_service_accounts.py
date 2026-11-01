"""T021 acceptance 7: disabling a service principal kills its keys, and enabling
it never brings them back.

A service principal has no login and no sessions; its only credential is a
scoped API key. The responsible user stays untouched by this operation.
"""

from __future__ import annotations

from conftest import assert_error
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_keys,
    api_server,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    epoch_revision,
    fresh_database,
    go_binaries,
    invite_and_activate,
    principal_row,
    seed_api_key_row,
    sessions_for,
    set_disabled,
    set_disabled_or_fail,
    stack,
)


def test_disabling_a_service_account_revokes_its_keys_and_enabling_does_not_restore_them(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t021.sa-owner", roles=("creator",))
    service = create_service_account_or_fail(admin, "t021-service-target", responsible.principal_id)
    first_key_id, first_secret = create_api_key_or_fail(admin, service)
    second_key_id, second_secret = create_api_key_or_fail(admin, service)
    bearer = bearer_client(stack, first_secret)
    assert bearer.get("/api/v1/auth/me").status == 200

    revision_before = epoch_revision(stack.dbname)
    set_disabled_or_fail(admin, service, disabled=True)
    assert epoch_revision(stack.dbname) == revision_before + 1

    # Both keys are revoked in the same transaction, so they are invalid keys
    # (not "principal disabled": the key row itself no longer works).
    assert_error(bearer.get("/api/v1/auth/me"), 401, "invalid_key")
    assert_error(bearer_client(stack, second_secret).get("/api/v1/auth/me"), 401, "invalid_key")
    keys = api_keys(stack.dbname, principal_id=service)
    assert {key["id"] for key in keys} == {first_key_id, second_key_id}, keys
    assert all(key["revoked_at"] for key in keys), keys
    service_row = principal_row(stack.dbname, service)
    assert service_row is not None
    assert service_row["kind"] == "service"
    assert service_row["disabled_at"], service_row
    # The responsible human was not touched, and a service principal has no
    # sessions at all.
    assert principal_row(stack.dbname, responsible.principal_id)["disabled_at"] is None
    assert sessions_for(stack.dbname, service) == []

    # Re-enabling is allowed (the responsible user is still enabled) but the
    # revoked keys stay revoked; a new key is required.
    set_disabled_or_fail(admin, service, disabled=False)
    assert principal_row(stack.dbname, service)["disabled_at"] is None
    assert all(key["revoked_at"] for key in api_keys(stack.dbname, principal_id=service))
    assert_error(bearer.get("/api/v1/auth/me"), 401, "invalid_key")
    new_key_id, new_secret = create_api_key_or_fail(admin, service)
    assert new_key_id not in (first_key_id, second_key_id)
    assert bearer_client(stack, new_secret).get("/api/v1/auth/me").status == 200


def test_enabling_a_service_account_whose_responsible_user_is_disabled_is_allowed_but_unusable(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    responsible = invite_and_activate(stack, admin, "t021.gone-owner", roles=("user",))
    service = create_service_account_or_fail(admin, "t021-orphan-svc", responsible.principal_id)
    create_api_key_or_fail(admin, service)

    set_disabled_or_fail(admin, service, disabled=True)
    set_disabled_or_fail(admin, responsible.principal_id, disabled=True)

    # Enabling the service principal is allowed even though its responsible user
    # is disabled: the state is consistent, the account simply remains unusable.
    set_disabled_or_fail(admin, service, disabled=False)
    assert principal_row(stack.dbname, service)["disabled_at"] is None

    # A key cannot be issued (responsible user disabled), and a crafted live key
    # is refused at authentication time: enabling did not bypass the responsible
    # user rule.
    response = admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": service,
            "scopes": ["application:read"],
            "expires_at": "2030-01-01T00:00:00Z",
        },
        csrf=admin.csrf,
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "validation_failed"
    assert response.json["error"].get("details", {}).get("field") == "principal_id"

    _, crafted_secret = seed_api_key_row(stack.dbname, service)
    assert_error(
        bearer_client(stack, crafted_secret).get("/api/v1/auth/me"),
        403,
        "responsible_disabled",
    )
