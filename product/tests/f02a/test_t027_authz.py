"""T027: debug submission and run reads are owner-only.

A read grant on an application is a read grant, not a debugging grant: it lets a
principal see the application and its draft while every debug/run path answers
404. An administrator without ownership is treated exactly like any other
non-owner. API keys additionally need the documented scope before the ACL is
consulted at all.
"""

from __future__ import annotations

import secrets

from support import f02a
from support import stack as st
from support import t027
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t027_stack,
)


def owner(t027_ctx):
    admin = t027.admin_session(t027_ctx)
    creator = t027.invite_and_activate(
        t027_ctx, admin, "t027-auth-" + secrets.token_hex(4), roles=("creator",)
    )
    application_id = t027.seed_golden_draft(t027_ctx.dbname, creator.principal_id)
    return admin, creator, application_id


def test_only_the_owner_can_submit_a_debug_run(t027_stack) -> None:
    admin, creator, application_id = owner(t027_stack)
    path = t027.debug_path(application_id)

    anonymous = t027_stack.client().post(path, body=t027.debug_body(), headers={
        "Idempotency-Key": t027.new_idempotency_key()
    })
    st.assert_error(anonymous, 401, "unauthenticated")

    # The administrator is not the owner and holds no grant. Debugging is a
    # creator write, so an administrator who is not also a creator is refused as
    # a capability problem (403 creator_role_required) before ownership is
    # consulted (SECURITY §1: "管理员=兼任创作者后").
    st.assert_error(
        admin.client.post(
            path,
            body=t027.debug_body(),
            csrf=admin.csrf,
            headers={"Idempotency-Key": t027.new_idempotency_key()},
        ),
        403,
        "creator_role_required",
    )

    # A read grant lets the reader see the draft and nothing more.
    reader = t027.invite_and_activate(
        t027_stack, admin, "t027-read-" + secrets.token_hex(4), roles=("creator",)
    )
    t027.grant_read(admin, reader.principal_id, application_id)
    assert t027.get_draft(reader, application_id).status == 200
    st.assert_error(t027.submit_debug(reader, application_id), 404, "resource_not_found")

    # A browser POST without the CSRF proof is refused before any work.
    st.assert_error(t027.submit_debug(creator, application_id, csrf=False), 403, "csrf_invalid")

    # A scoped API key is checked before the ACL: read scope cannot submit.
    service_account = f02a.create_service_account(admin, "t027-reader-api", creator.principal_id)
    _key_id, secret = f02a.create_api_key(admin, service_account, ["application:read"])
    bearer = f02a.bearer_client(t027_stack, secret)
    response = bearer.post(
        path, body=t027.debug_body(), headers={"Idempotency-Key": t027.new_idempotency_key()}
    )
    st.assert_error(response, 403, "insufficient_scope")

    # Write scope plus a read grant is still not ownership.
    t027.grant_read(admin, service_account, application_id)
    _write_key_id, write_secret = f02a.create_api_key(admin, service_account, ["application:write"])
    write_bearer = f02a.bearer_client(t027_stack, write_secret)
    response = write_bearer.post(
        path, body=t027.debug_body(), headers={"Idempotency-Key": t027.new_idempotency_key()}
    )
    st.assert_error(response, 404, "resource_not_found")

    # Nothing was created by any refusal.
    t027.no_execution_rows(t027_stack.dbname, application_id)

    # The owner's own session can submit. A service account's application:write
    # key is a different principal, so it still cannot debug the creator's
    # application: the owner rule is principal-level, not scope-level.
    submitted = t027.submit_debug(creator, application_id)
    assert submitted.status == 202, submitted.text
    run = submitted.json

    service_account_write = f02a.create_service_account(
        admin, "t027-writer-api", creator.principal_id
    )
    _owner_key_id, owner_secret = f02a.create_api_key(
        admin, service_account_write, ["application:write"]
    )
    owner_bearer = f02a.bearer_client(t027_stack, owner_secret)
    service_submission = owner_bearer.post(
        path, body=t027.debug_body(), headers={"Idempotency-Key": t027.new_idempotency_key()}
    )
    st.assert_error(service_submission, 404, "resource_not_found")
    assert t027.run_row(t027_stack.dbname, run["id"])["owner_id"] == creator.principal_id


def test_only_the_owner_can_read_a_debug_run(t027_stack) -> None:
    admin, creator, application_id = owner(t027_stack)
    submitted = t027.submit_debug(creator, application_id)
    assert submitted.status == 202, submitted.text
    run_id = submitted.json["id"]

    assert t027.get_run(creator, run_id).status == 200

    st.assert_error(t027_stack.client().get(t027.run_path(run_id)), 401, "unauthenticated")
    st.assert_error(t027.get_run(admin, run_id), 404, "resource_not_found")

    reader = t027.invite_and_activate(
        t027_stack, admin, "t027-run-read-" + secrets.token_hex(4), roles=("creator",)
    )
    t027.grant_read(admin, reader.principal_id, application_id)
    assert t027.get_draft(reader, application_id).status == 200
    st.assert_error(t027.get_run(reader, run_id), 404, "resource_not_found")

    # A run has no grant type: no grant turns a non-owner into a run reader.
    st.assert_error(t027.get_run(reader, t027.new_uuid()), 404, "resource_not_found")

    # A second administrator without ownership is also 404.
    other_admin = t027.invite_and_activate(
        t027_stack, admin, "t027-admin2-" + secrets.token_hex(4), roles=("admin",)
    )
    st.assert_error(t027.get_run(other_admin, run_id), 404, "resource_not_found")

    # API keys: read scope is required before the ownership rule.
    service_account = f02a.create_service_account(admin, "t027-run-api", creator.principal_id)
    _key_id, write_secret = f02a.create_api_key(admin, service_account, ["application:write"])
    write_bearer = f02a.bearer_client(t027_stack, write_secret)
    st.assert_error(write_bearer.get(t027.run_path(run_id)), 403, "insufficient_scope")
    _read_key_id, read_secret = f02a.create_api_key(admin, service_account, ["application:read"])
    read_bearer = f02a.bearer_client(t027_stack, read_secret)
    st.assert_error(read_bearer.get(t027.run_path(run_id)), 404, "resource_not_found")
