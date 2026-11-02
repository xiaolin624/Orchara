"""T020 re-review: issuance must not outrun a disable or a responsibility handover.

Both issuance paths read the responsible user's ``disabled_at`` and then write.
Without the shared ``ROLESET1`` advisory lock a disable (or a handover) that
commits between that read and the INSERT leaves a credential / responsibility
that the disable was supposed to revoke:

* an API key issued across a disable is never revoked by it, so it works again
  after the responsible user is re-enabled;
* a service account created across a disable is born responsible to a disabled
  person, and a handover that already ran cannot move it.

The trigger in ``race_support.py`` holds the issuance transaction open inside a
real PostgreSQL ``BEFORE INSERT`` trigger so the concurrent disable/handover
runs in the only window that matters. Every assertion is on the real tables and
real HTTP, after both transactions committed.
"""

from __future__ import annotations

from conftest import assert_error
from race_support import (
    Issuance,
    drop_sleep_trigger,
    install_sleep_trigger,
    wait_for_sleeping_insert,
)
from t020_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    Stack,
    admin_session,
    api_keys,
    api_server,
    bearer_client,
    create_api_key,
    create_service_account,
    create_service_account_or_fail,
    fresh_database,
    go_binaries,
    invite_and_activate,
    stack,
)
from t021_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    principal_row,
    run_handover,
    set_disabled,
    set_disabled_or_fail,
)

API_KEYS_TRIGGER = "f01_race_api_keys_insert"
API_KEYS_FUNCTION = "f01_race_sleep_api_keys"
PRINCIPALS_TRIGGER = "f01_race_principals_insert"
PRINCIPALS_FUNCTION = "f01_race_sleep_principals"


def test_a_key_is_never_born_after_the_disable_that_should_have_revoked_it(
    stack: Stack,
) -> None:
    issuer = admin_session(stack)
    disabler = admin_session(stack)
    responsible = invite_and_activate(stack, issuer, "t020.race.owner", roles=("user",))
    service = create_service_account_or_fail(issuer, "t020-race-svc", responsible.principal_id)

    install_sleep_trigger(
        stack.dbname,
        table="platform.api_keys",
        trigger=API_KEYS_TRIGGER,
        function=API_KEYS_FUNCTION,
        seconds=0.8,
    )
    try:
        # The issuer is inside the INSERT (the trigger is sleeping) when the
        # disable starts. With the lock the disable queues behind the issuance;
        # without it the disable commits while the key row is still uncommitted
        # and therefore invisible to the revoke UPDATE.
        issuance = Issuance(lambda: create_api_key(issuer, service)).start()
        wait_for_sleeping_insert(stack.dbname)
        disable = set_disabled(
            disabler, responsible.principal_id, disabled=True, reason="并发禁用"
        )
        assert disable.status == 200, disable.text
        issuance.join()

        created = issuance.value
        assert created.status == 201, created.text
        key_id, secret = created.json["id"], created.json["secret"]

        rows = api_keys(stack.dbname, principal_id=service)
        assert [row["id"] for row in rows] == [key_id], rows
        assert rows[0]["revoked_at"] is not None, (
            "the key was issued after the disable's revoke UPDATE and survived it: "
            f"{rows}"
        )

        # Re-enabling must not resurrect it: the row is revoked, not merely
        # masked by the responsible-user check.
        set_disabled_or_fail(disabler, responsible.principal_id, disabled=False, reason="复职")
        assert_error(bearer_client(stack, secret).get("/api/v1/auth/me"), 401, "invalid_key")
    finally:
        drop_sleep_trigger(
            stack.dbname,
            table="platform.api_keys",
            trigger=API_KEYS_TRIGGER,
            function=API_KEYS_FUNCTION,
        )


def test_a_service_account_created_across_a_handover_is_moved_by_it(
    stack: Stack, go_binaries
) -> None:
    issuer = admin_session(stack)
    creator = admin_session(stack)
    disabler = admin_session(stack)
    successor = invite_and_activate(stack, issuer, "t020.race.successor", roles=("admin",))
    departing = invite_and_activate(stack, issuer, "t020.race.departing", roles=("user",))

    install_sleep_trigger(
        stack.dbname,
        table="platform.principals",
        trigger=PRINCIPALS_TRIGGER,
        function=PRINCIPALS_FUNCTION,
        seconds=0.8,
        only_kind="service",
    )
    try:
        issuance = Issuance(
            lambda: create_service_account(creator, "t020-race-created", departing.principal_id)
        ).start()
        wait_for_sleeping_insert(stack.dbname)

        # With the lock: creation commits first, the disable queues behind it and
        # then the handover moves the committed row. Without it: the disable and
        # the handover both commit first, and creation then inserts a row that is
        # still responsible to the disabled person.
        disable = set_disabled(disabler, departing.principal_id, disabled=True, reason="并发禁用")
        assert disable.status == 200, disable.text
        handover = run_handover(
            go_binaries["admin"],
            stack.dsn,
            from_id=departing.principal_id,
            to_id=successor.principal_id,
            by_id=successor.principal_id,
        )
        assert handover.returncode == 0, handover.stdout + handover.stderr
        issuance.join()

        created = issuance.value
        assert created.status == 201, created.text
        service_id = created.json["id"]
        row = principal_row(stack.dbname, service_id)
        assert row is not None, "the created service principal is missing"
        assert row["kind"] == "service"
        assert row["responsible_user_id"] == successor.principal_id, (
            "the handover ran before the service account was committed, so the new "
            f"row is still responsible to the disabled user: {row}"
        )

        # And the disabled person cannot be named as responsible again.
        refused = create_service_account(issuer, "t020-race-refused", departing.principal_id)
        error = assert_error(refused, 422, "validation_failed")
        assert error.get("details", {}).get("field") == "responsible_user_id", error
    finally:
        drop_sleep_trigger(
            stack.dbname,
            table="platform.principals",
            trigger=PRINCIPALS_TRIGGER,
            function=PRINCIPALS_FUNCTION,
        )
