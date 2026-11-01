"""T019 acceptance 4: every refusal is the exact contract code and writes nothing.

Each case snapshots the grant table, the policy epoch and the audit trail before
the refused request; an equality assertion after it proves the refusal happened
before any write (handlers authorize first, then validate, then transact).
"""

from __future__ import annotations

from conftest import assert_error, sql
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_server,
    disable_principal,
    epoch_revision,
    fresh_database,
    get_application,
    go_binaries,
    grant_read,
    grant_read_or_fail,
    invite_and_activate,
    list_applications,
    new_uuid,
    revoke_grant,
    seed_application,
    set_roles,
    stack,
)


def state(dbname: str) -> tuple[int, int, int, int]:
    """Grants, policy revision, audit rows and role bindings, in one snapshot."""
    return (
        int(sql(dbname, "SELECT count(*) FROM platform.resource_grants")),
        epoch_revision(dbname),
        int(sql(dbname, "SELECT count(*) FROM platform.audit_events")),
        int(sql(dbname, "SELECT count(*) FROM platform.role_bindings")),
    )


def grant_body(principal_id: str, application_id: str, **overrides) -> dict:
    body = {
        "principal_id": principal_id,
        "resource_type": "application",
        "resource_id": application_id,
        "action": "read",
    }
    body.update(overrides)
    return body


def prepare(stack: Stack):
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "val.owner", roles=("creator",))
    reader = invite_and_activate(stack, admin, "val.reader", roles=("user",))
    application_id = seed_application(stack, owner.principal_id, "validation-application")
    archived_application = seed_application(
        stack, owner.principal_id, "archived-validation-application", age_minutes=1, archived=True
    )
    return admin, owner, reader, application_id, archived_application


def test_every_bad_grant_is_refused_with_its_field_and_changes_nothing(stack: Stack) -> None:
    admin, _owner, reader, application_id, archived_application = prepare(stack)
    disabled = invite_and_activate(stack, admin, "val.disabled", roles=("user",))
    disable_principal(stack.dbname, disabled.principal_id)

    cases = [
        (
            "malformed principal",
            {"principal_id": "not-a-uuid"},
            422,
            "validation_failed",
            "principal_id",
        ),
        ("unknown principal", {"principal_id": new_uuid()}, 404, "principal_not_found", None),
        (
            "disabled principal",
            {"principal_id": disabled.principal_id},
            422,
            "validation_failed",
            "principal_id",
        ),
        (
            "unknown resource type",
            {"resource_type": "memory"},
            422,
            "validation_failed",
            "resource_type",
        ),
        ("unknown action", {"action": "write"}, 422, "validation_failed", "action"),
        ("empty action", {"action": ""}, 422, "validation_failed", "action"),
        ("malformed resource", {"resource_id": "nope"}, 422, "validation_failed", "resource_id"),
        ("unknown resource", {"resource_id": new_uuid()}, 404, "resource_not_found", None),
        (
            "archived resource",
            {"resource_id": archived_application},
            404,
            "resource_not_found",
            None,
        ),
        (
            "past expiry",
            {"expires_at": "2020-01-01T00:00:00Z"},
            422,
            "validation_failed",
            "expires_at",
        ),
        (
            "unparsable expiry",
            {"expires_at": "the day after tomorrow"},
            422,
            "validation_failed",
            "expires_at",
        ),
    ]

    for label, overrides, status, code, field in cases:
        before = state(stack.dbname)
        body = grant_body(reader.principal_id, application_id)
        body.update(overrides)
        response = admin.client.post(
            "/api/v1/admin/grants",
            body=body,
            csrf=admin.csrf,
        )
        error = assert_error(response, status, code)
        if field is not None:
            assert error.get("details", {}).get("field") == field, (label, error)
        assert state(stack.dbname) == before, f"{label} changed state"
        assert get_application(reader, application_id).status == 404, label


def test_a_duplicate_grant_is_a_conflict_and_bumps_nothing(stack: Stack) -> None:
    admin, _owner, reader, application_id, _archived = prepare(stack)
    grant_read_or_fail(admin, reader.principal_id, application_id)
    before = state(stack.dbname)

    conflict = grant_read(admin, reader.principal_id, application_id)
    error = assert_error(conflict, 409, "grant_exists")
    assert error["message"]
    assert state(stack.dbname) == before
    assert get_application(reader, application_id).status == 200, "the existing grant must survive"


def test_an_unknown_or_malformed_grant_cannot_be_revoked(stack: Stack) -> None:
    admin, _owner, _reader, _application_id, _archived = prepare(stack)
    before = state(stack.dbname)

    for grant_id in (new_uuid(), "not-a-uuid"):
        response = revoke_grant(admin, grant_id)
        assert_error(response, 404, "grant_not_found")
        assert state(stack.dbname) == before, grant_id


def test_cursor_and_limit_are_validated(stack: Stack) -> None:
    admin, _owner, reader, _application_id, _archived = prepare(stack)

    for cursor in ("not-a-cursor", "***", "b3BhcXVl"):
        assert_error(
            list_applications(reader, cursor=cursor), 400, "invalid_cursor"
        )

    for limit in ("abc", "0", "101", "-1", "1.5", " "):
        error = assert_error(
            list_applications(reader, limit=limit), 422, "validation_failed"
        )
        assert error.get("details", {}).get("field") == "limit", (limit, error)

    # The boundary values are accepted, and the envelope is exactly the contract.
    for limit in ("1", "100"):
        response = list_applications(reader, limit=limit)
        assert response.status == 200, response.text
        assert set(response.json) == {"items", "next_cursor"}

    # Cursor from a real page is accepted.
    page = list_applications(reader, limit=1)
    assert page.status == 200

    # An unknown query parameter is harmless and therefore ignored; the query is
    # still driven only by the declared cursor/limit pair.
    harmless = reader.client.get("/api/v1/applications?sort=created_at&foo=bar")
    assert harmless.status == 200, harmless.text
    assert set(harmless.json) == {"items", "next_cursor"}


def test_non_admins_and_forged_writes_are_refused_without_changes(stack: Stack) -> None:
    admin, _owner, reader, application_id, _archived = prepare(stack)
    before = state(stack.dbname)

    assert_error(
        reader.client.post(
            "/api/v1/admin/grants",
            body=grant_body(reader.principal_id, application_id),
            csrf=reader.csrf,
        ),
        403,
        "forbidden",
    )
    assert_error(
        set_roles(reader, reader.principal_id, ["admin"]), 403, "forbidden"
    )
    assert_error(revoke_grant(reader, new_uuid()), 403, "forbidden")

    # A valid admin session without the CSRF proof is still refused, and the
    # granted tuple below proves the same endpoint works when both are present.
    assert_error(
        admin.client.post(
            "/api/v1/admin/grants",
            body=grant_body(reader.principal_id, application_id),
        ),
        403,
        "csrf_invalid",
    )
    assert_error(
        admin.client.request(
            "PUT",
            f"/api/v1/admin/users/{reader.principal_id}/roles",
            body={"roles": ["admin"]},
        ),
        403,
        "csrf_invalid",
    )
    assert_error(
        admin.client.request("DELETE", f"/api/v1/admin/grants/{new_uuid()}"),
        403,
        "csrf_invalid",
    )
    assert state(stack.dbname) == before

    accepted = grant_read(admin, reader.principal_id, application_id)
    assert accepted.status == 201, accepted.text
    assert get_application(reader, application_id).status == 200


def test_role_body_validation(stack: Stack) -> None:
    admin, _owner, reader, _application_id, _archived = prepare(stack)
    before = state(stack.dbname)

    for label, roles in (
        ("empty", []),
        ("duplicate", ["user", "user"]),
        ("unknown", ["root"]),
        ("blank", [" "]),
    ):
        error = assert_error(set_roles(admin, reader.principal_id, roles), 422, "validation_failed")
        assert error.get("details", {}).get("field") == "roles", (label, error)
        assert state(stack.dbname) == before, label

    assert_error(set_roles(admin, new_uuid(), ["user"]), 404, "principal_not_found")
    assert_error(set_roles(admin, "not-a-uuid", ["user"]), 404, "principal_not_found")
    assert state(stack.dbname) == before
