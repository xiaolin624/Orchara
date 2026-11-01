"""T021 acceptance 4: the last enabled human administrator can never be disabled.

The count is read inside the disable transaction, under the same "ROLESET1"
advisory lock as role changes. The concurrency test holds that lock from a real
psql session so two real HTTP requests are guaranteed to reach the count
together; exactly one may win, which is what proves the lock is shared.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from conftest import assert_error
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    Stack,
    admin_session,
    api_server,
    audit_events,
    enabled_human_admins,
    epoch_revision,
    fresh_database,
    go_binaries,
    hold_roles_lock,
    invite_and_activate,
    principal_row,
    session_for,
    set_disabled,
    set_disabled_or_fail,
    sign_in,
    stack,
    wait_for_advisory_lock,
)


def test_the_only_administrator_cannot_disable_itself(stack: Stack) -> None:
    admin = admin_session(stack)
    revision_before = epoch_revision(stack.dbname)
    audit_before = len(audit_events(stack.dbname, "principal.disabled"))

    error = assert_error(set_disabled(admin, admin.principal_id, disabled=True), 409, "last_admin")
    assert error["message"]

    # A refusal changes nothing: still enabled, still signed in, same epoch, no
    # audit row.
    assert enabled_human_admins(stack.dbname) == 1
    assert principal_row(stack.dbname, admin.principal_id)["disabled_at"] is None
    assert epoch_revision(stack.dbname) == revision_before
    assert len(audit_events(stack.dbname, "principal.disabled")) == audit_before
    assert admin.client.get("/api/v1/auth/me").status == 200


def test_two_admins_one_can_be_disabled_but_never_the_last(stack: Stack) -> None:
    admin = admin_session(stack)
    second = invite_and_activate(stack, admin, "t021.admin-b", roles=("admin",))
    assert enabled_human_admins(stack.dbname) == 2

    # With two administrators, disabling one succeeds.
    set_disabled_or_fail(admin, second.principal_id, disabled=True)
    assert enabled_human_admins(stack.dbname) == 1
    assert_error(second.client.get("/api/v1/auth/me"), 401, "session_revoked")

    # The remaining one cannot disable itself; the attempt is a pure refusal.
    revision_before = epoch_revision(stack.dbname)
    audit_before = len(audit_events(stack.dbname, "principal.disabled"))
    assert_error(set_disabled(admin, admin.principal_id, disabled=True), 409, "last_admin")
    assert enabled_human_admins(stack.dbname) == 1
    assert principal_row(stack.dbname, admin.principal_id)["disabled_at"] is None
    assert admin.client.get("/api/v1/auth/me").status == 200
    assert epoch_revision(stack.dbname) == revision_before
    assert len(audit_events(stack.dbname, "principal.disabled")) == audit_before

    # Self-disable is allowed once another enabled administrator remains.
    set_disabled_or_fail(admin, second.principal_id, disabled=False)
    returning = session_for(stack, "t021.admin-b", USER_PASSWORD)
    set_disabled_or_fail(admin, admin.principal_id, disabled=True)
    assert enabled_human_admins(stack.dbname) == 1
    assert_error(admin.client.get("/api/v1/auth/me"), 401, "session_revoked")
    _, login = sign_in(stack, ADMIN_LOGIN, ADMIN_PASSWORD)
    assert_error(login, 403, "principal_disabled")

    # The remaining administrator can still enable the disabled one.
    set_disabled_or_fail(returning, admin.principal_id, disabled=False)
    assert enabled_human_admins(stack.dbname) == 2


def test_two_concurrent_self_disables_leave_exactly_one_administrator(stack: Stack) -> None:
    admin = admin_session(stack)
    second = invite_and_activate(stack, admin, "t021.concurrent-admin", roles=("admin",))
    assert enabled_human_admins(stack.dbname) == 2

    # Hold the shared role/disable lock while both requests authenticate, so both
    # are still enabled administrators when they queue inside SetDisabled. Without
    # the shared lock both could see the other administrator and both win.
    holder = hold_roles_lock(stack.dbname, seconds=3.0)
    try:
        wait_for_advisory_lock(stack.dbname)
        barrier = threading.Barrier(2)

        def disable_self(session):
            barrier.wait(timeout=20)
            return set_disabled(session, session.principal_id, disabled=True)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(disable_self, admin),
                pool.submit(disable_self, second),
            ]
            responses = [future.result(timeout=60) for future in futures]
    finally:
        holder.wait(timeout=60)

    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 409], [(response.status, response.text) for response in responses]
    refused = next(response for response in responses if response.status == 409)
    assert_error(refused, 409, "last_admin")
    assert enabled_human_admins(stack.dbname) == 1
