"""T019 acceptance 5: role changes take effect immediately and keep an admin.

The last-administrator rule is enforced inside the role transaction, under an
advisory lock. The concurrency test holds that lock from a real psql session so
two real HTTP demotions are guaranteed to reach the check together instead of
racing the authentication step; exactly one may win.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

from conftest import assert_error, sql
from t019_stack import (  # noqa: F401 - fixtures are used through their names
    Stack,
    admin_session,
    api_server,
    audit_events,
    enabled_human_admins,
    epoch_revision,
    fresh_database,
    go_binaries,
    grant_read,
    hold_roles_lock,
    invite_and_activate,
    list_applications,
    role_bindings,
    seed_application,
    seed_service_principal,
    set_roles,
    stack,
    wait_for_advisory_lock,
)


def grant_body(principal_id: str, application_id: str) -> dict:
    return {
        "principal_id": principal_id,
        "resource_type": "application",
        "resource_id": application_id,
        "action": "read",
    }


def test_a_promoted_user_can_call_an_admin_endpoint_and_a_demoted_one_cannot(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "roles.member", roles=("user",))
    owner = invite_and_activate(stack, admin, "roles.owner", roles=("creator",))
    first_application = seed_application(stack, owner.principal_id, "roles-app-one", age_minutes=2)
    second_application = seed_application(stack, owner.principal_id, "roles-app-two", age_minutes=1)

    revision_before = epoch_revision(stack.dbname)
    assert_error(
        member.client.post(
            "/api/v1/admin/grants",
            body=grant_body(member.principal_id, first_application),
            csrf=member.csrf,
        ),
        403,
        "forbidden",
    )

    promoted = set_roles(admin, member.principal_id, ["admin"])
    assert promoted.status == 200, promoted.text
    assert promoted.json == {"id": member.principal_id, "state": "updated"}
    assert epoch_revision(stack.dbname) == revision_before + 1
    assert role_bindings(stack.dbname, member.principal_id) == ["admin"]
    # The effect is visible on the very next request: the identity's roles are
    # re-read per request, so no re-login is needed.
    identity = member.client.get("/api/v1/auth/me")
    assert identity.status == 200
    assert identity.json["roles"] == ["admin"]

    accepted = member.client.post(
        "/api/v1/admin/grants",
        body=grant_body(admin.principal_id, first_application),
        csrf=member.csrf,
    )
    assert accepted.status == 201, accepted.text
    # Two bumps so far: the role update and the grant the promoted member created.
    assert epoch_revision(stack.dbname) == revision_before + 2
    # The new administrator uses the endpoint to grant *another* principal (the
    # bootstrap admin), not themselves; the two-person rule leaves the endpoint
    # fully usable after promotion. The grant is immediately effective for the
    # grantee.
    assert first_application in [
        item["id"] for item in list_applications(admin).json["items"]
    ]

    demoted = set_roles(admin, member.principal_id, ["user"])
    assert demoted.status == 200, demoted.text
    assert epoch_revision(stack.dbname) == revision_before + 3
    assert role_bindings(stack.dbname, member.principal_id) == ["user"]
    assert member.client.get("/api/v1/auth/me").json["roles"] == ["user"]
    assert_error(
        member.client.post(
            "/api/v1/admin/grants",
            body=grant_body(admin.principal_id, second_application),
            csrf=member.csrf,
        ),
        403,
        "forbidden",
    )
    # The grant the promoted member created is still effective after the
    # demotion: losing the admin role does not cascade into grants the member
    # created, but the member is back to seeing only their own (none).
    assert first_application in [
        item["id"] for item in list_applications(admin).json["items"]
    ]
    assert list_applications(member).json == {"items": [], "next_cursor": None}
    assert second_application not in [
        item["id"] for item in list_applications(admin).json["items"]
    ]


def test_the_last_enabled_human_admin_cannot_be_demoted(stack: Stack) -> None:
    admin = admin_session(stack)
    revision_before = epoch_revision(stack.dbname)
    bindings_before = role_bindings(stack.dbname, admin.principal_id)
    audit_before = len(audit_events(stack.dbname, "roles.updated"))

    for roles in (["user"], ["creator", "user"], ["creator"]):
        error = assert_error(set_roles(admin, admin.principal_id, roles), 409, "last_admin")
        assert error["message"]
        assert enabled_human_admins(stack.dbname) == 1
        assert role_bindings(stack.dbname, admin.principal_id) == bindings_before
        assert epoch_revision(stack.dbname) == revision_before
        assert len(audit_events(stack.dbname, "roles.updated")) == audit_before

    # With a second administrator the demotion is allowed; once that one is
    # demoted, the remaining administrator again cannot demote itself.
    second = invite_and_activate(stack, admin, "roles.second", roles=("admin",))
    assert enabled_human_admins(stack.dbname) == 2
    allowed = set_roles(admin, second.principal_id, ["user"])
    assert allowed.status == 200, allowed.text
    assert role_bindings(stack.dbname, second.principal_id) == ["user"]
    assert_error(set_roles(admin, admin.principal_id, ["user"]), 409, "last_admin")
    assert enabled_human_admins(stack.dbname) == 1


def test_a_service_principal_has_no_role_bindings(stack: Stack) -> None:
    admin = admin_session(stack)
    service = seed_service_principal(stack.dbname, admin.principal_id, "roles-service")
    revision_before = epoch_revision(stack.dbname)
    audit_before = len(audit_events(stack.dbname, "roles.updated"))

    error = assert_error(set_roles(admin, service, ["user"]), 422, "validation_failed")
    assert error.get("details", {}).get("field") == "principal_id", error
    assert role_bindings(stack.dbname, service) == []
    assert epoch_revision(stack.dbname) == revision_before
    assert len(audit_events(stack.dbname, "roles.updated")) == audit_before


def test_a_role_update_audits_old_and_new_roles_and_bumps_the_epoch(stack: Stack) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "roles.audited", roles=("user",))
    revision_before = epoch_revision(stack.dbname)

    updated = set_roles(admin, member.principal_id, ["creator", "user"])
    assert updated.status == 200, updated.text
    assert epoch_revision(stack.dbname) == revision_before + 1
    assert role_bindings(stack.dbname, member.principal_id) == ["creator", "user"]

    events = audit_events(stack.dbname, "roles.updated")
    assert len(events) == 1, events
    event = events[0]
    assert event["actor_id"] == admin.principal_id
    assert event["resource_type"] == "principal"
    assert event["resource_id"] == member.principal_id
    metadata = json.loads(event["metadata"])
    assert metadata["principal_id"] == member.principal_id
    assert metadata["old_roles"] == ["user"]
    assert metadata["new_roles"] == ["creator", "user"]

    # The promoted user's own identity view shows the new bindings.
    me = member.client.get("/api/v1/auth/me")
    assert me.status == 200
    assert me.json["roles"] == ["creator", "user"]


def test_two_concurrent_demotions_leave_an_administrator(stack: Stack) -> None:
    admin = admin_session(stack)
    second = invite_and_activate(stack, admin, "roles.concurrent", roles=("admin",))
    assert enabled_human_admins(stack.dbname) == 2

    # Hold the role-update lock while both requests authenticate, so both are
    # still administrators when they queue inside SetRoles. Without this, one
    # request could authenticate after the other committed and be refused by the
    # role check before the last-admin rule is reached.
    holder = hold_roles_lock(stack.dbname, seconds=3.0)
    try:
        wait_for_advisory_lock(stack.dbname)
        barrier = threading.Barrier(2)

        def demote(session, target_id: str):
            barrier.wait(timeout=20)
            return set_roles(session, target_id, ["user"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(demote, admin, second.principal_id),
                pool.submit(demote, second, admin.principal_id),
            ]
            responses = [future.result(timeout=60) for future in futures]
    finally:
        holder.wait(timeout=60)

    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 409], [(response.status, response.text) for response in responses]
    refused = next(response for response in responses if response.status == 409)
    assert_error(refused, 409, "last_admin")
    assert enabled_human_admins(stack.dbname) == 1


def test_only_admins_can_change_roles_and_grant(stack: Stack) -> None:
    admin = admin_session(stack)
    member = invite_and_activate(stack, admin, "roles.outsider", roles=("creator",))
    target = invite_and_activate(stack, admin, "roles.target", roles=("user",))
    revision_before = epoch_revision(stack.dbname)
    bindings_before = role_bindings(stack.dbname, target.principal_id)

    assert_error(set_roles(member, target.principal_id, ["admin"]), 403, "forbidden")
    assert role_bindings(stack.dbname, target.principal_id) == bindings_before
    assert epoch_revision(stack.dbname) == revision_before

    # The same caller cannot write a grant either; nothing is created.
    application_id = seed_application(stack, target.principal_id, "roles-outsider-app")
    assert_error(
        member.client.post(
            "/api/v1/admin/grants",
            body=grant_body(target.principal_id, application_id),
            csrf=member.csrf,
        ),
        403,
        "forbidden",
    )
    assert sql(stack.dbname, "SELECT count(*) FROM platform.resource_grants") == "0"

    # The admin can still do it; the refusals were about the caller, not the API.
    assert grant_read(admin, target.principal_id, application_id).status == 201
    assert [item["id"] for item in list_applications(target).json["items"]] == [application_id]
