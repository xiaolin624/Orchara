"""T021 acceptance 1 and 6: disabling kills old credentials immediately, and
enabling never resurrects them.

Everything is a real throwaway PostgreSQL database, the compiled binaries and
real HTTP. The victim is a human with two live browser sessions; the service
account they are responsible for holds a live API key. Disabling the person must
revoke both sessions and the service key in the same transaction.
"""

from __future__ import annotations

from conftest import assert_error
from t021_stack import (  # noqa: F401 - fixtures are used through their names
    USER_PASSWORD,
    Stack,
    admin_session,
    api_keys,
    api_server,
    audit_metadata,
    bearer_client,
    client_with_cookie,
    create_api_key_or_fail,
    create_service_account_or_fail,
    epoch_revision,
    fresh_database,
    go_binaries,
    invite_and_activate,
    invite_body,
    principal_row,
    session_for,
    sessions_for,
    set_disabled,
    set_disabled_or_fail,
    sign_in,
    stack,
)


def test_disabling_a_person_kills_the_session_and_the_service_key_immediately(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    victim = invite_and_activate(stack, admin, "t021.victim", roles=("creator",))
    second_session = session_for(stack, "t021.victim", USER_PASSWORD)
    service = create_service_account_or_fail(admin, "t021-svc", victim.principal_id)
    key_id, secret = create_api_key_or_fail(admin, service)
    bearer = bearer_client(stack, secret)

    # Both credentials work before the change; this is the current-authorization
    # baseline the refusal is compared against.
    assert victim.client.get("/api/v1/auth/me").status == 200
    assert second_session.client.get("/api/v1/auth/me").status == 200
    assert bearer.get("/api/v1/auth/me").status == 200

    revision_before = epoch_revision(stack.dbname)
    response = set_disabled(admin, victim.principal_id, disabled=True, reason=" 离职交接 ")
    assert response.status == 200, response.text
    assert response.json == {"id": victim.principal_id, "state": "disabled"}, response.text
    # Exactly one real state change, exactly one epoch bump.
    assert epoch_revision(stack.dbname) == revision_before + 1

    # Every live browser session of the disabled person is dead on the next call.
    assert_error(victim.client.get("/api/v1/auth/me"), 401, "session_revoked")
    assert_error(second_session.client.get("/api/v1/auth/me"), 401, "session_revoked")

    # A write is refused for the same reason, before any body is read.
    write = victim.client.post(
        "/api/v1/admin/users",
        body=invite_body("t021.never-created"),
        csrf=victim.csrf,
    )
    assert_error(write, 401, "session_revoked")

    # Signing in again is a 403, not a new session: a disabled account cannot
    # obtain a fresh credential.
    _, login = sign_in(stack, "t021.victim", USER_PASSWORD)
    assert_error(login, 403, "principal_disabled")

    # The service account's key was revoked in the same transaction, so the
    # next bearer call is an invalid key (not merely "responsible disabled").
    assert_error(bearer.get("/api/v1/auth/me"), 401, "invalid_key")

    # The rows prove the revocations really happened, and that the principal row
    # still exists (audit references it; nothing is deleted).
    sessions = sessions_for(stack.dbname, victim.principal_id)
    assert len(sessions) == 2, sessions
    assert all(session["revoked_at"] for session in sessions), sessions
    keys = api_keys(stack.dbname, principal_id=service)
    assert len(keys) == 1, keys
    assert keys[0]["id"] == key_id
    assert keys[0]["revoked_at"], keys
    victim_row = principal_row(stack.dbname, victim.principal_id)
    assert victim_row is not None, "the principal row must never be deleted"
    assert victim_row["kind"] == "user"
    assert victim_row["disabled_at"], victim_row

    # The audit event names the actor, the subject, the reason and the counts —
    # and never a credential.
    events = audit_metadata(stack.dbname, "principal.disabled")
    assert len(events) == 1, events
    event = events[0]
    assert event["actor_id"] == admin.principal_id
    assert event["resource_id"] == victim.principal_id
    assert event["reason"] == "离职交接"
    assert event["sessions_revoked"] == 2
    assert event["keys_revoked"] == 1


def test_enabling_restores_login_with_a_new_session_but_not_the_old_credentials(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    victim = invite_and_activate(stack, admin, "t021.rehire", roles=("user",))
    service = create_service_account_or_fail(admin, "t021-rehire-svc", victim.principal_id)
    key_id, old_secret = create_api_key_or_fail(admin, service)
    old_cookie = victim.cookie

    set_disabled_or_fail(admin, victim.principal_id, disabled=True)

    revision_before = epoch_revision(stack.dbname)
    enabled = set_disabled(admin, victim.principal_id, disabled=False, reason="返聘")
    assert enabled.status == 200, enabled.text
    assert enabled.json == {"id": victim.principal_id, "state": "enabled"}, enabled.text
    assert epoch_revision(stack.dbname) == revision_before + 1

    # The same password works again and issues a new session...
    client, login = sign_in(stack, "t021.rehire", USER_PASSWORD)
    assert login.status == 200, login.text
    new_cookie = client.cookie("platform_session")
    assert new_cookie and new_cookie != old_cookie
    assert client.get("/api/v1/auth/me").status == 200

    # ...while the old cookie stays revoked: enabling does not restore
    # credentials that were already revoked.
    old_client = client_with_cookie(stack, old_cookie)
    assert_error(old_client.get("/api/v1/auth/me"), 401, "session_revoked")

    # The old key stays revoked; a new key has to be issued explicitly.
    keys = api_keys(stack.dbname, principal_id=service)
    assert keys[0]["id"] == key_id
    assert keys[0]["revoked_at"], keys
    assert_error(bearer_client(stack, old_secret).get("/api/v1/auth/me"), 401, "invalid_key")
    new_key_id, new_secret = create_api_key_or_fail(admin, service)
    assert new_key_id != key_id
    assert bearer_client(stack, new_secret).get("/api/v1/auth/me").status == 200

    row = principal_row(stack.dbname, victim.principal_id)
    assert row["disabled_at"] is None, row

    events = audit_metadata(stack.dbname, "principal.enabled")
    assert len(events) == 1, events
    assert events[0]["actor_id"] == admin.principal_id
    assert events[0]["resource_id"] == victim.principal_id
    assert events[0]["reason"] == "返聘"
