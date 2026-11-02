"""T017 re-review: a session must not be born after the disable that revokes it.

``Login`` used to check ``disabled_at`` before opening its transaction and then
insert the session without holding ``ROLESET1``. A disable committing in that
window revoked only the sessions visible at the time, so the just-inserted
session survived and worked again after the user was re-enabled.

The ``BEFORE INSERT`` trigger on ``platform.sessions`` holds the login
transaction open inside PostgreSQL; the disable then runs from another real
client while the login is exactly at the INSERT. Both orders are then checked on
the real session row and with the real cookie.
"""

from __future__ import annotations

from conftest import SESSION_COOKIE, assert_error, sha256_hex
from race_support import (
    Issuance,
    drop_sleep_trigger,
    install_sleep_trigger,
    wait_for_sleeping_insert,
)
from t017_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    USER_PASSWORD,
    Stack,
    admin_session,
    api_server,
    fresh_database,
    go_binaries,
    invite_and_activate,
    sign_in,
    stack,
)
from t021_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    sessions_for,
    set_disabled,
    set_disabled_or_fail,
)

SESSIONS_TRIGGER = "f01_race_sessions_insert"
SESSIONS_FUNCTION = "f01_race_sleep_sessions"


def test_a_session_is_never_born_after_the_disable_that_should_have_revoked_it(
    stack: Stack,
) -> None:
    admin = admin_session(stack)
    user = invite_and_activate(stack, admin, "t017.race.user", roles=("user",))

    install_sleep_trigger(
        stack.dbname,
        table="platform.sessions",
        trigger=SESSIONS_TRIGGER,
        function=SESSIONS_FUNCTION,
        seconds=0.8,
    )
    try:
        # The login is inside the session INSERT (the trigger is sleeping) when
        # the disable starts. With the lock the disable queues behind the login
        # and then revokes the committed row; without it the disable commits
        # while the session row is still uncommitted and therefore invisible.
        issuance = Issuance(lambda: sign_in(stack, "t017.race.user", USER_PASSWORD)).start()
        wait_for_sleeping_insert(stack.dbname)
        disable = set_disabled(admin, user.principal_id, disabled=True, reason="并发禁用")
        assert disable.status == 200, disable.text
        issuance.join()

        client, response = issuance.value
        assert response.status == 200, response.text
        cookie = client.cookie(SESSION_COOKIE)
        assert cookie, "the login did not set a session cookie"
        token_hash = sha256_hex(cookie)

        sessions = sessions_for(stack.dbname, user.principal_id)
        raced = [session for session in sessions if session["token_hash"] == token_hash]
        assert len(raced) == 1, (token_hash, sessions)
        assert raced[0]["revoked_at"] is not None, (
            "the session was issued after the disable's revoke UPDATE and survived "
            f"it: {raced}"
        )

        # The cookie is dead on the next call, and re-enabling the account does
        # not resurrect it: enabling clears disabled_at, never revoked_at.
        assert_error(client.get("/api/v1/auth/me"), 401, "session_revoked")
        set_disabled_or_fail(admin, user.principal_id, disabled=False, reason="复职")
        assert_error(client.get("/api/v1/auth/me"), 401, "session_revoked")
    finally:
        drop_sleep_trigger(
            stack.dbname,
            table="platform.sessions",
            trigger=SESSIONS_TRIGGER,
            function=SESSIONS_FUNCTION,
        )
