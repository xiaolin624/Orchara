"""SECURITY §1: creating, saving, validating and debugging are creator actions.

The permission matrix fixes "建应用/脚本草稿与受限调试" as false for a plain user and
as "兼任创作者后" for an administrator: the server gate is the creator role, not the
administrator role and not ownership alone. API keys are service-account
credentials with scopes instead of roles, so a write-scoped key keeps working
(T023 §17); reads are unchanged.

Every refusal is checked against the real database: a refused write must create
zero applications, drafts or execution rows.
"""

from __future__ import annotations

from support import f02a as f
from support import stack as st
from support import t025, t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
    t025_stack,
    t027_stack,
)


def plain_user(stack) -> f.Session:
    admin = f.admin_session(stack)
    return f.invite_and_activate(stack, admin, "t023-role-user", roles=("user",))


def creator(stack) -> f.Session:
    admin = f.admin_session(stack)
    return f.invite_and_activate(stack, admin, "t023-role-creator", roles=("creator",))


def admin_only(stack) -> f.Session:
    admin = f.admin_session(stack)
    return f.invite_and_activate(stack, admin, "t023-role-admin", roles=("admin",))


def admin_creator(stack) -> f.Session:
    admin = f.admin_session(stack)
    return f.invite_and_activate(
        stack, admin, "t023-role-admin-creator", roles=("admin", "creator")
    )


def row_count(dbname: str, table: str) -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.{table}"))


def test_a_session_without_the_creator_role_cannot_create_save_validate_or_debug(
    f02a_stack,
) -> None:
    admin = f.admin_session(f02a_stack)
    user = plain_user(f02a_stack)

    refused_create = f.create_application(user, "a plain user's app")
    st.assert_error(refused_create, 403, "creator_role_required")
    assert row_count(f02a_stack.dbname, "applications") == 0
    assert row_count(f02a_stack.dbname, "application_drafts") == 0

    # The user owns a real application + draft (seeded, because the HTTP create
    # path is exactly what is being refused); ownership alone is not a write
    # capability for a non-creator.
    application_id = t025.seed_application(
        f02a_stack.dbname, user.principal_id, name="owned but not creatable"
    )
    before = f.draft_row(f02a_stack.dbname, application_id)
    st.assert_error(
        f.save_draft(user, application_id, '"1"', definition={"dsl_version": "1.0", "nodes": []}),
        403,
        "creator_role_required",
    )
    assert f.draft_row(f02a_stack.dbname, application_id) == before
    st.assert_error(t025.validate(user, application_id), 403, "creator_role_required")
    st.assert_error(t027.submit_debug(user, application_id), 403, "creator_role_required")
    t027.no_execution_rows(f02a_stack.dbname, application_id)

    # The bootstrap administrator has only `admin`: that is not a creator.
    st.assert_error(f.create_application(admin, "admin app"), 403, "creator_role_required")
    st.assert_error(f.save_draft(admin, application_id, '"1"'), 403, "creator_role_required")
    st.assert_error(t025.validate(admin, application_id), 403, "creator_role_required")

    # Reads stay as they were: ownership still means "can read the draft".
    owned_by_admin = t025.seed_application(
        f02a_stack.dbname, admin.principal_id, name="admin's own draft"
    )
    assert f.get_draft(admin, owned_by_admin).status == 200


def test_a_creator_succeeds_and_an_admin_who_is_also_a_creator_succeeds(f02a_stack) -> None:
    creator_session = creator(f02a_stack)
    created = f.create_application_or_fail(creator_session, "creator app")
    saved = f.save_draft(creator_session, created["id"], '"1"')
    assert saved.status == 200, saved.text
    assert saved.header("ETag") == '"2"'

    admin_creator_session = admin_creator(f02a_stack)
    admin_created = f.create_application_or_fail(admin_creator_session, "admin creator app")
    admin_saved = f.save_draft(admin_creator_session, admin_created["id"], '"1"')
    assert admin_saved.status == 200, admin_saved.text


def test_validate_requires_the_creator_role_before_any_compilation(t025_stack) -> None:
    creator_session = creator(t025_stack)
    application_id = t025.seed_application(
        t025_stack.dbname, creator_session.principal_id, name="validate gate"
    )
    # An empty draft is a located validation result, not a gate refusal: the
    # creator passed the capability check and reached the compiler.
    accepted = t025.validate(creator_session, application_id)
    assert accepted.status == 200, accepted.text
    assert accepted.json["valid"] is False, accepted.json

    admin_session = admin_only(t025_stack)
    st.assert_error(t025.validate(admin_session, application_id), 403, "creator_role_required")

    admin_creator_id = admin_creator(t025_stack)
    application_id = t025.seed_application(
        t025_stack.dbname, admin_creator_id.principal_id, name="admin creator validate"
    )
    accepted = t025.validate(admin_creator_id, application_id)
    assert accepted.status == 200, accepted.text
    assert accepted.json["valid"] is False, accepted.json


def test_debug_requires_the_creator_role_before_any_snapshot(t027_stack) -> None:
    creator_session = creator(t027_stack)
    application_id = t027.seed_golden_draft(t027_stack.dbname, creator_session.principal_id)
    accepted = t027.submit_debug(creator_session, application_id)
    assert accepted.status == 202, accepted.text
    assert t027.count_for_application(
        t027_stack.dbname, "execution_snapshots", application_id
    ) == 1

    admin_session = admin_only(t027_stack)
    admin_application = t025.seed_application(
        t027_stack.dbname, admin_session.principal_id, name="admin without creator"
    )
    st.assert_error(
        t027.submit_debug(admin_session, admin_application), 403, "creator_role_required"
    )
    t027.no_execution_rows(t027_stack.dbname, admin_application)

    # An administrator who is also a creator passes the gate and reaches the
    # compiler: the empty seeded draft is a located 422, not a role refusal.
    admin_creator_session = admin_creator(t027_stack)
    admin_creator_application = t025.seed_application(
        t027_stack.dbname, admin_creator_session.principal_id, name="admin creator debug"
    )
    reached = t027.submit_debug(admin_creator_session, admin_creator_application)
    st.assert_error(reached, 422, "validation_failed")
    t027.no_execution_rows(t027_stack.dbname, admin_creator_application)
