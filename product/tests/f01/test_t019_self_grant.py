"""T019 acceptance 3: the ACL write path cannot manufacture the reader.

An administrator is not an automatic content reader. Before this file's fix,
``POST /api/v1/admin/grants`` accepted ``principal_id == caller``: the acting
administrator could grant themselves ``read`` on any application and then read
it, which is exactly the bypass the acceptance criterion forbids. The rule now
is two-person: only a *different* administrator can create that grant.

Everything here is real PostgreSQL + the compiled API over HTTP. The zero-write
assertions read the real ``resource_grants`` / ``audit_events`` tables and the
policy epoch before and after the refusal.
"""

from __future__ import annotations

from conftest import assert_error, sql, sql_rows
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    DEMO_VERSION_ID,
    Stack,
    admin_session,
    api_server,
    application_ids,
    audit_events,
    epoch_revision,
    fresh_database,
    get_application,
    go_binaries,
    grant_read,
    grant_read_or_fail,
    invite_and_activate,
    list_applications,
    seed_application,
    stack,
)


def _state(dbname: str) -> tuple[int, int, int]:
    """(grants, audit events, acl epoch) - the three things a refusal must not touch."""
    grants = int(sql(dbname, "SELECT count(*) FROM platform.resource_grants"))
    audits = int(sql(dbname, "SELECT count(*) FROM platform.audit_events"))
    return grants, audits, epoch_revision(dbname)


def test_an_administrator_cannot_grant_themselves_read(stack: Stack) -> None:
    admin = admin_session(stack)
    owner = invite_and_activate(stack, admin, "selfgrant.owner", roles=("creator",))
    application_id = seed_application(
        stack, owner.principal_id, "selfgrant-application", active_version_id=DEMO_VERSION_ID
    )
    before = _state(stack.dbname)

    denied = grant_read(admin, admin.principal_id, application_id)
    error = assert_error(denied, 422, "validation_failed")
    assert error.get("details") == {"field": "principal_id"}, error
    assert "another administrator" in error["message"].lower(), error

    # A UUID's textual form is case-insensitive. An upper-case spelling of the
    # caller's id must not become a grant row for the same principal either.
    upper = grant_read(admin, admin.principal_id.upper(), application_id)
    assert_error(upper, 422, "validation_failed")

    # Zero writes: no grant row, no epoch bump, no audit event.
    assert _state(stack.dbname) == before
    assert sql(stack.dbname, "SELECT count(*) FROM platform.resource_grants") == "0"

    # And the self-grant attempt did not create read access: the administrator
    # still sees exactly what any administrator without a grant sees - nothing.
    listed = list_applications(admin)
    assert listed.status == 200, listed.text
    assert listed.json == {"items": [], "next_cursor": None}
    assert_error(get_application(admin, application_id), 404, "resource_not_found")


def test_a_different_administrator_can_grant_the_read(stack: Stack) -> None:
    first = admin_session(stack)
    second = invite_and_activate(stack, first, "selfgrant.second.admin", roles=("admin",))
    owner = invite_and_activate(stack, first, "selfgrant.reader.owner", roles=("creator",))
    application_id = seed_application(
        stack, owner.principal_id, "two-person-application", active_version_id=DEMO_VERSION_ID
    )

    # No access before the second administrator acts.
    assert list_applications(first).json == {"items": [], "next_cursor": None}
    assert_error(get_application(first, application_id), 404, "resource_not_found")

    created = grant_read(second, first.principal_id, application_id)
    assert created.status == 201, created.text
    assert created.json["state"] == "granted"

    listed = list_applications(first)
    assert listed.status == 200, listed.text
    assert application_ids(listed) == [application_id]
    assert get_application(first, application_id).status == 200

    # The audit trail names the granting administrator, not the grantee.
    events = [event for event in audit_events(stack.dbname) if event["action"] == "grant.created"]
    assert len(events) == 1, events
    assert events[0]["actor_id"] == second.principal_id, events[0]
    metadata = sql_rows(
        stack.dbname,
        "SELECT principal_id::text AS principal_id FROM platform.resource_grants",
    )
    assert metadata == [{"principal_id": first.principal_id}], metadata
