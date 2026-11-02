"""T021 acceptance 2: an unfinished-todo handover never expands authority.

The departing user X is responsible for a service account whose key has already
been revoked by X's disable. ``cmd/admin handover --from X --to Y --by Z`` moves
only that responsibility: no roles, no grants, no keys change, so Y gains nothing
they were not already authorized to do (Y is an enabled human admin). Y still
cannot read the application the service account was granted; a freshly issued key
for the service account still can, because the grant belongs to the account.

Every invocation uses the compiled binary, a throwaway PostgreSQL database and
the DSN only in the environment.
"""

from __future__ import annotations

from conftest import assert_error, sql_rows
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_keys,
    api_server,
    application_ids,
    audit_metadata,
    authorization_snapshot,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    get_application,
    go_binaries,
    grant_read_or_fail,
    handover_events,
    invite_and_activate,
    list_applications,
    new_uuid,
    principal_row,
    resource_grants,
    role_bindings,
    run_admin,
    run_handover,
    seed_application,
    set_disabled_or_fail,
    stack,
)

SERVICE_DISPLAY_NAME = "t021-handover-svc"


def _service_responsibilities(dbname: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT id::text AS id, display_name, responsible_user_id::text AS responsible_user_id "
        "FROM platform.principals WHERE kind = 'service' ORDER BY id",
    )


def _handover_state(dbname: str) -> dict:
    """Everything a refusal must leave byte-identical."""
    return {
        "responsibilities": _service_responsibilities(dbname),
        "handover_events": len(handover_events(dbname)),
        "authorization": authorization_snapshot(dbname),
    }


def test_a_handover_moves_responsibility_without_expanding_any_authority(stack: Stack, go_binaries) -> None:
    admin = admin_session(stack)  # Z, the acting administrator
    successor = invite_and_activate(stack, admin, "t021.successor-y", roles=("admin",))
    departing = invite_and_activate(stack, admin, "t021.departing-x", roles=("creator",))
    service = create_service_account_or_fail(admin, SERVICE_DISPLAY_NAME, departing.principal_id)
    application = seed_application(stack, departing.principal_id, "t021-handover-app")
    grant_read_or_fail(admin, service, application)
    retired_key_id, retired_secret = create_api_key_or_fail(admin, service)

    # X is disabled first: the service account's key dies with them, which is
    # exactly the state a handover starts from.
    set_disabled_or_fail(admin, departing.principal_id, disabled=True)
    assert_error(bearer_client(stack, retired_secret).get("/api/v1/auth/me"), 401, "invalid_key")
    assert principal_row(stack.dbname, service)["responsible_user_id"] == departing.principal_id

    # Everything the handover must leave byte-identical is snapshotted from the
    # real pre-handover state (after the disable, which legitimately revoked the
    # key).
    roles_before = role_bindings(stack.dbname, successor.principal_id)
    grants_before = resource_grants(stack.dbname)
    keys_before = api_keys(stack.dbname, principal_id=service)

    completed = run_handover(
        go_binaries["admin"],
        stack.dsn,
        from_id=departing.principal_id,
        to_id=successor.principal_id,
        by_id=admin.principal_id,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr.strip() == "", completed.stderr
    # Output: the count plus one line per moved account (id and display name).
    assert "1 service account" in completed.stdout, completed.stdout
    assert service in completed.stdout, completed.stdout
    assert SERVICE_DISPLAY_NAME in completed.stdout, completed.stdout
    assert retired_secret not in completed.stdout
    assert retired_secret not in completed.stderr

    # Responsibility moved; the audit event names the actor and both principals.
    assert principal_row(stack.dbname, service)["responsible_user_id"] == successor.principal_id
    events = audit_metadata(stack.dbname, "service_account.handover")
    assert len(events) == 1, events
    assert events[0]["actor_id"] == admin.principal_id
    assert events[0]["resource_id"] == service
    assert events[0]["from"] == departing.principal_id
    assert events[0]["to"] == successor.principal_id

    # Nothing else changed: same role bindings, same grants, same key rows
    # (the revoked key stays revoked).
    assert role_bindings(stack.dbname, successor.principal_id) == roles_before
    assert resource_grants(stack.dbname) == grants_before
    assert api_keys(stack.dbname, principal_id=service) == keys_before
    assert principal_row(stack.dbname, departing.principal_id)["disabled_at"] is not None
    assert principal_row(stack.dbname, successor.principal_id)["disabled_at"] is None

    # Y is an administrator but not an automatic content reader: the service
    # account's grant was not copied to Y.
    assert_error(get_application(successor, application), 404, "resource_not_found")
    assert application_ids(list_applications(successor)) == []

    # A freshly issued key for the service account still works because the ACL
    # grant belongs to the account, not to its responsible person.
    new_key_id, new_secret = create_api_key_or_fail(admin, service)
    assert new_key_id != retired_key_id
    fresh = bearer_client(stack, new_secret)
    assert application in application_ids(fresh.get("/api/v1/applications"))
    assert fresh.get(f"/api/v1/applications/{application}").status == 200


def test_handover_refusals_exit_one_and_change_nothing(stack: Stack, go_binaries) -> None:
    admin = admin_session(stack)  # Z
    successor = invite_and_activate(stack, admin, "t021.refuse-y", roles=("admin",))
    departing = invite_and_activate(stack, admin, "t021.refuse-x", roles=("creator",))
    outsider = invite_and_activate(stack, admin, "t021.refuse-u", roles=("user",))
    doomed_admin = invite_and_activate(stack, admin, "t021.refuse-d", roles=("admin",))
    create_service_account_or_fail(admin, "t021-refusal-svc", departing.principal_id)
    service_successor = create_service_account_or_fail(
        admin, "t021-refusal-sa", departing.principal_id
    )
    set_disabled_or_fail(admin, doomed_admin.principal_id, disabled=True)

    state_before = _handover_state(stack.dbname)
    cases = [
        ("successor is not an administrator", departing.principal_id, outsider.principal_id, admin.principal_id),
        ("successor is disabled", departing.principal_id, doomed_admin.principal_id, admin.principal_id),
        ("actor is not an administrator", departing.principal_id, successor.principal_id, outsider.principal_id),
        ("unknown successor", departing.principal_id, new_uuid(), admin.principal_id),
        ("unknown actor", departing.principal_id, successor.principal_id, new_uuid()),
        ("unknown departing principal", new_uuid(), successor.principal_id, admin.principal_id),
        ("from equals to", departing.principal_id, departing.principal_id, admin.principal_id),
        ("successor is a service principal", departing.principal_id, service_successor, admin.principal_id),
    ]
    for label, from_id, to_id, by_id in cases:
        completed = run_handover(
            go_binaries["admin"], stack.dsn, from_id=from_id, to_id=to_id, by_id=by_id
        )
        assert completed.returncode == 1, f"{label}: {completed.stdout}{completed.stderr}"
        assert completed.stdout.strip() == "", f"{label}: {completed.stdout}"
        assert completed.stderr.strip(), f"{label}: no stderr reason"
        assert "orchara-admin:" in completed.stderr, f"{label}: {completed.stderr}"
        assert _handover_state(stack.dbname) == state_before, f"{label}: the database changed"


def test_handover_usage_and_flag_errors_exit_two(stack: Stack, go_binaries) -> None:
    admin = admin_session(stack)
    successor = invite_and_activate(stack, admin, "t021.usage-y", roles=("admin",))
    departing = invite_and_activate(stack, admin, "t021.usage-x", roles=("user",))
    state_before = _handover_state(stack.dbname)

    cases = [
        ["handover"],
        ["handover", "--from", departing.principal_id],
        ["handover", "--from", departing.principal_id, "--to", successor.principal_id],
        ["handover", "--from", "not-a-uuid", "--to", successor.principal_id, "--by", admin.principal_id],
        ["handover", "--from", departing.principal_id, "--to", successor.principal_id,
         "--by", admin.principal_id, "--unknown-flag", "x"],
    ]
    for args in cases:
        completed = run_admin(go_binaries["admin"], args, dsn=stack.dsn)
        assert completed.returncode == 2, f"{args}: {completed.stdout}{completed.stderr}"
        assert completed.stderr.strip(), f"{args}: no stderr message"
        assert "usage" in completed.stderr.lower(), f"{args}: {completed.stderr}"
        assert _handover_state(stack.dbname) == state_before, f"{args}: the database changed"


def test_handover_with_no_service_accounts_succeeds_with_zero(stack: Stack, go_binaries) -> None:
    admin = admin_session(stack)
    successor = invite_and_activate(stack, admin, "t021.empty-y", roles=("admin",))
    departing = invite_and_activate(stack, admin, "t021.empty-x", roles=("user",))

    completed = run_handover(
        go_binaries["admin"],
        stack.dsn,
        from_id=departing.principal_id,
        to_id=successor.principal_id,
        by_id=admin.principal_id,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "0 service account" in completed.stdout, completed.stdout
    assert handover_events(stack.dbname) == []
