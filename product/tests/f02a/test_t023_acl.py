"""T023 acceptance: draft/version ACLs are default-deny, and read is not write.

The chosen rule (recorded in ADR 0004 §13): the draft is readable by the owner or
an unexpired ``read`` grant; it is writable by the owner only. A caller who
cannot read gets 404 on every draft/version path (no existence oracle). A caller
who can read but does not own gets 403 on a save, because they already know the
draft exists and the refusal is about the missing write right.
"""

from __future__ import annotations

from support import f02a as f
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    f02a_stack,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _setup(stack):
    admin = f.admin_session(stack)
    owner = f.invite_and_activate(stack, admin, "t023-acl-owner")
    reader = f.invite_and_activate(stack, admin, "t023-acl-reader")
    outsider = f.invite_and_activate(stack, admin, "t023-acl-outsider")
    application = f.create_application_or_fail(owner, "visible only to its owner")
    versions = f.seed_published_versions(
        stack.dbname, application["id"], owner_id=owner.principal_id, count=2
    )
    return admin, owner, reader, outsider, application, versions


def test_an_unrelated_user_and_an_admin_without_a_grant_get_404_everywhere(f02a_stack) -> None:
    admin, _owner, _reader, outsider, application, versions = _setup(f02a_stack)
    for caller in (outsider, admin):
        assert f.get_draft(caller, application["id"]).status == 404
        assert f.list_versions(caller, application["id"]).status == 404
        assert f.get_version(caller, versions[0]["id"]).status == 404
        # The application read itself is the same default-deny boundary.
        assert f.get_application(caller, application["id"]).status == 404

    # Saving is a creator write: the outsider holds the role but not ownership
    # (404, no existence oracle), while the bootstrap administrator does not hold
    # the creator role at all and is refused before visibility is consulted (403
    # creator_role_required, SECURITY §1: an administrator must be a creator too).
    assert f.save_draft(outsider, application["id"], '"1"').status == 404
    st.assert_error(
        f.save_draft(admin, application["id"], '"1"'), 403, "creator_role_required"
    )


def test_a_read_grant_can_read_but_cannot_save(f02a_stack) -> None:
    admin, owner, reader, _outsider, application, versions = _setup(f02a_stack)
    f.grant_read(admin, reader.principal_id, application["id"])

    readable = f.get_draft(reader, application["id"])
    assert readable.status == 200, readable.text
    assert readable.json["definition"] == {}
    assert f.list_versions(reader, application["id"]).status == 200
    assert f.get_version(reader, versions[0]["id"]).status == 200

    before = f.draft_row(f02a_stack.dbname, application["id"])
    refused = f.save_draft(
        reader, application["id"], readable.header("ETag"),
        definition={"dsl_version": "1.0", "nodes": [{"id": "x", "type": "start"}]},
    )
    assert refused.status == 403, refused.text
    error = refused.json["error"]
    assert error["code"] == "forbidden", error
    assert "owner" in error["message"]
    assert f.draft_row(f02a_stack.dbname, application["id"]) == before

    # The owner keeps the write right all along.
    owner_save = f.save_draft(owner, application["id"], readable.header("ETag"))
    assert owner_save.status == 200, owner_save.text


def test_revoking_the_grant_removes_read_access_immediately(f02a_stack) -> None:
    admin, _owner, reader, _outsider, application, versions = _setup(f02a_stack)
    grant_id = f.grant_read(admin, reader.principal_id, application["id"])
    assert f.get_draft(reader, application["id"]).status == 200

    assert f.revoke_grant(admin, grant_id).status == 204
    assert f.get_draft(reader, application["id"]).status == 404
    assert f.list_versions(reader, application["id"]).status == 404
    assert f.get_version(reader, versions[0]["id"]).status == 404


def test_an_expired_grant_does_not_grant_read_access(f02a_stack) -> None:
    admin, _owner, reader, _outsider, application, _versions = _setup(f02a_stack)
    grant = admin.client.post(
        "/api/v1/admin/grants",
        body={
            "principal_id": reader.principal_id,
            "resource_type": "application",
            "resource_id": application["id"],
            "action": "read",
            # Expiring in the past is refused by the API; seed a near-future one
            # and move it into the past through SQL to exercise the decision,
            # not the request validation (T019 owns that).
            "expires_at": f.future_rfc3339(1),
        },
        csrf=admin.csrf,
    )
    assert grant.status == 201, grant.text
    st.sql(
        f02a_stack.dbname,
        "UPDATE platform.resource_grants SET expires_at = now() - interval '1 minute' "
        f"WHERE id = {st.quote_literal(grant.json['id'])}::uuid",
    )
    assert f.get_draft(reader, application["id"]).status == 404
