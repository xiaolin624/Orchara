"""T021 acceptance 3: the audit trail keeps locating the original subject.

Nothing is deleted when a principal is disabled, so a join from audit_events to
principals/users must still resolve the login and display name of every row that
references the disabled person. No audit row may contain a credential.
"""

from __future__ import annotations

from conftest import sql_rows
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_server,
    audit_rows,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    go_binaries,
    grant_read_or_fail,
    invite_and_activate,
    seed_application,
    set_disabled_or_fail,
    stack,
    stored_secrets,
)

VICTIM_LOGIN = "t021.audited"
VICTIM_DISPLAY_NAME = "T021 Audited"


def test_audit_keeps_resolving_the_original_subject_after_disable(stack: Stack) -> None:
    admin = admin_session(stack)
    victim = invite_and_activate(
        stack, admin, VICTIM_LOGIN, roles=("creator",), display_name=VICTIM_DISPLAY_NAME
    )
    service = create_service_account_or_fail(admin, "t021-audit-svc", victim.principal_id)
    application = seed_application(stack, victim.principal_id, "t021-audit-app")
    grant_read_or_fail(admin, service, application)
    _, secret = create_api_key_or_fail(admin, service)

    # Rows written before the disable, by the victim (auth.login).
    set_disabled_or_fail(admin, victim.principal_id, disabled=True, reason="audit 仍可定位")

    rows = sql_rows(
        stack.dbname,
        f"""
        SELECT a.action,
               a.actor_id::text AS actor_id,
               a.resource_id::text AS resource_id,
               actor.display_name AS actor_display_name,
               actor_user.login AS actor_login,
               resource.display_name AS resource_display_name,
               resource_user.login AS resource_login
          FROM platform.audit_events AS a
          LEFT JOIN platform.principals AS actor ON actor.id = a.actor_id
          LEFT JOIN platform.users AS actor_user ON actor_user.principal_id = actor.id
          LEFT JOIN platform.principals AS resource ON resource.id = a.resource_id
          LEFT JOIN platform.users AS resource_user ON resource_user.principal_id = resource.id
         WHERE a.actor_id = '{victim.principal_id}'::uuid
            OR a.resource_id = '{victim.principal_id}'::uuid
         ORDER BY a.created_at, a.action
        """,
    )
    assert rows, "there must be audit rows that reference the disabled principal"

    # Every reference still resolves: the principal row was not deleted and its
    # users row still carries the login.
    for row in rows:
        if row["actor_id"] == victim.principal_id:
            assert row["actor_display_name"] == VICTIM_DISPLAY_NAME, row
            assert row["actor_login"] == VICTIM_LOGIN, row
        if row["resource_id"] == victim.principal_id:
            assert row["resource_display_name"] == VICTIM_DISPLAY_NAME, row
            assert row["resource_login"] == VICTIM_LOGIN, row

    # The rows written before the change kept their original actor/resource ids.
    login_events = [row for row in rows if row["action"] == "auth.login"]
    assert login_events, rows
    assert all(row["actor_id"] == victim.principal_id for row in login_events)
    assert all(row["resource_id"] == victim.principal_id for row in login_events)

    # The disable event itself is written by the acting admin against the victim.
    disable_events = [
        row
        for row in sql_rows(
            stack.dbname,
            "SELECT actor_id::text AS actor_id, resource_id::text AS resource_id "
            "FROM platform.audit_events WHERE action = 'principal.disabled'",
        )
    ]
    assert len(disable_events) == 1, disable_events
    assert disable_events[0]["actor_id"] == admin.principal_id
    assert disable_events[0]["resource_id"] == victim.principal_id

    # No audit row (metadata included) contains a password, session hash or key
    # hash — and neither does the API's own log.
    hashes = [value.split(":", 1)[1] for value in stored_secrets(stack.dbname)]
    for row in audit_rows(stack.dbname):
        metadata = row["metadata"]
        for value in hashes:
            assert value not in metadata, f"audit metadata leaked a credential: {row}"
        for marker in ("orchara_", "$argon2id$"):
            assert marker not in metadata, f"audit metadata leaked a credential: {row}"
    log = stack.server.logs()
    for value in hashes:
        assert value not in log, "the API log leaked a credential"
    assert secret not in log
