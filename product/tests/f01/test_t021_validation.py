"""T021 acceptance 5: idempotency, validation and exactly-once epoch bumps.

Every assertion is behavioural: the response, the real row, the audit trail and
the policy epoch. A refusal must leave all four untouched.
"""

from __future__ import annotations

from conftest import assert_error
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_server,
    audit_events,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    epoch_revision,
    fresh_database,
    go_binaries,
    invite_and_activate,
    new_uuid,
    principal_row,
    sessions_for,
    set_disabled,
    set_disabled_or_fail,
    stack,
)


def test_disabling_an_already_disabled_principal_is_a_no_op(stack: Stack) -> None:
    admin = admin_session(stack)
    victim = invite_and_activate(stack, admin, "t021.idempotent", roles=("user",))
    set_disabled_or_fail(admin, victim.principal_id, disabled=True)

    revision_after_change = epoch_revision(stack.dbname)
    audit_after_change = len(audit_events(stack.dbname, "principal.disabled"))
    sessions_after_change = sessions_for(stack.dbname, victim.principal_id)

    repeat = set_disabled(admin, victim.principal_id, disabled=True, reason="再次请求")
    assert repeat.status == 200, repeat.text
    assert repeat.json == {"id": victim.principal_id, "state": "disabled"}, repeat.text
    # No second audit event, no second epoch bump, no second revocation pass.
    events = audit_events(stack.dbname, "principal.disabled")
    assert len(events) == audit_after_change
    assert epoch_revision(stack.dbname) == revision_after_change
    assert sessions_for(stack.dbname, victim.principal_id) == sessions_after_change
    # The stored reason still describes the real change, not the no-op request.
    assert "离职交接" in events[0]["metadata"], events[0]
    assert "再次请求" not in events[0]["metadata"], events[0]


def test_enabling_an_already_enabled_principal_is_a_no_op(stack: Stack) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "t021.still-enabled", roles=("user",))
    revision_before = epoch_revision(stack.dbname)
    audit_before = len(audit_events(stack.dbname, "principal.enabled"))

    response = set_disabled(admin, member.principal_id, disabled=False)
    assert response.status == 200, response.text
    assert response.json == {"id": member.principal_id, "state": "enabled"}, response.text
    assert epoch_revision(stack.dbname) == revision_before
    assert len(audit_events(stack.dbname, "principal.enabled")) == audit_before
    assert principal_row(stack.dbname, member.principal_id)["disabled_at"] is None


def test_each_real_state_change_bumps_the_epoch_exactly_once(stack: Stack) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "t021.epoch", roles=("user",))
    revision_before = epoch_revision(stack.dbname)

    set_disabled_or_fail(admin, member.principal_id, disabled=True)
    assert epoch_revision(stack.dbname) == revision_before + 1
    # A repeat is not a state change.
    set_disabled_or_fail(admin, member.principal_id, disabled=True)
    assert epoch_revision(stack.dbname) == revision_before + 1

    set_disabled_or_fail(admin, member.principal_id, disabled=False)
    assert epoch_revision(stack.dbname) == revision_before + 2
    set_disabled_or_fail(admin, member.principal_id, disabled=False)
    assert epoch_revision(stack.dbname) == revision_before + 2


def test_validation_and_authorization_refusals_change_nothing(stack: Stack) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "t021.member", roles=("creator",))
    victim = invite_and_activate(stack, admin, "t021.target", roles=("user",))
    service = create_service_account_or_fail(admin, "t021-validation-svc", admin.principal_id)
    _, secret = create_api_key_or_fail(admin, service)

    revision_before = epoch_revision(stack.dbname)
    audit_before = len(audit_events(stack.dbname, "principal.disabled"))
    disabled_at_before = principal_row(stack.dbname, victim.principal_id)["disabled_at"]

    # Empty / whitespace-only reason: 422 naming the field.
    for body_reason in ("", "   "):
        error = assert_error(
            set_disabled(admin, victim.principal_id, disabled=True, reason=body_reason),
            422,
            "validation_failed",
        )
        assert error.get("details", {}).get("field") == "reason", error

    # Missing reason entirely.
    missing_reason = admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{victim.principal_id}",
        body={"disabled": True},
        csrf=admin.csrf,
    )
    error = assert_error(missing_reason, 422, "validation_failed")
    assert error.get("details", {}).get("field") == "reason", error

    # Missing disabled field: a body without it must not silently enable anyone.
    missing_disabled = admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{victim.principal_id}",
        body={"reason": "why"},
        csrf=admin.csrf,
    )
    error = assert_error(missing_disabled, 422, "validation_failed")
    assert error.get("details", {}).get("field") == "disabled", error

    # Unknown and malformed ids are one 404 (no enumeration).
    assert_error(set_disabled(admin, new_uuid(), disabled=True), 404, "principal_not_found")
    assert_error(set_disabled(admin, "not-a-uuid", disabled=True), 404, "principal_not_found")

    # A non-admin session is refused.
    assert_error(set_disabled(member, victim.principal_id, disabled=True), 403, "forbidden")

    # A service key has no roles, so it is refused too (and needs no CSRF).
    assert_error(
        bearer_client(stack, secret).request(
            "PATCH",
            f"/api/v1/admin/principals/{victim.principal_id}",
            body={"disabled": True, "reason": "service key attempt"},
        ),
        403,
        "forbidden",
    )

    # Missing CSRF is refused before any state change.
    assert_error(
        set_disabled(admin, victim.principal_id, disabled=True, csrf=None),
        403,
        "csrf_invalid",
    )

    # Nothing above changed anything.
    assert principal_row(stack.dbname, victim.principal_id)["disabled_at"] == disabled_at_before
    assert epoch_revision(stack.dbname) == revision_before
    assert len(audit_events(stack.dbname, "principal.disabled")) == audit_before
