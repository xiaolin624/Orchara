"""F11/T073: user-scoped diagnostic grants over the real stack.

Cycle 1 (grant lifecycle): the contract's ``grantDiagnostics`` /
``revokeDiagnostics`` operations. Real PostgreSQL 16, the compiled API behind
HTTP with real cookie+CSRF sessions, and a real claimed run owned by a real
creator. The grant names the run, the administrator grantee, the purposes, the
expiry and (via the documented non-contract ``X-Diagnostic-Object-Scope`` header)
the explicit object snapshot the user chose.
"""

from __future__ import annotations

import datetime
import uuid

import pytest

from support import f00b, t073
from support import stack as st


def test_the_run_owner_grants_an_administrator_a_bounded_diagnostic_scope(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    artifact_id = t073.upload_output(stack, lease, b"t073 grant body\n")

    expires_at = t073.expires_in(3600)
    created = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        purposes=["troubleshooting", "quality_review"],
        expires_at=expires_at,
        object_scope=[t073.artifact_scope(artifact_id)],
    )

    # The stored grant is the ticket's scope: user/run/object/purpose/expiry.
    row = t073.grant_row(stack.dbname, created["id"])
    assert row is not None, created
    assert row["owner_id"] == owner.creator.principal_id
    assert row["grantee_id"] == admin.principal_id
    assert row["run_id"] == run["id"]
    assert t073.grant_purposes(row) == ["troubleshooting", "quality_review"]
    assert t073.grant_object_scope(row) == [t073.artifact_scope(artifact_id)]
    assert row["revoked_at"] is None
    assert row["expires_at"].startswith(expires_at[:19]), (row["expires_at"], expires_at)

    # One audit row names the ids only, never content.
    created_audit = t073.audit_rows(stack.dbname, t073.ACTION_GRANT_CREATED)
    assert len(created_audit) == 1, created_audit
    metadata = t073.audit_metadata(created_audit[0])
    assert metadata["grant_id"] == created["id"], metadata
    assert metadata["run_id"] == run["id"], metadata
    assert metadata["grantee_id"] == admin.principal_id, metadata
    assert metadata["object_count"] == 1, metadata
    assert set(metadata) == {
        "grant_id",
        "run_id",
        "grantee_id",
        "purposes",
        "expires_at",
        "object_count",
    }, metadata

    # Both sides can see the metadata: the owner as owner, the admin as grantee.
    owned = [item for item in t073.list_grant_items(owner.creator) if item["id"] == created["id"]]
    assert len(owned) == 1, owned
    assert owned[0]["state"] == t073.ACTIVE
    assert owned[0]["direction"] == "owner"
    assert owned[0]["object_scope"] == [t073.artifact_scope(artifact_id)]
    received = [item for item in t073.list_grant_items(admin) if item["id"] == created["id"]]
    assert len(received) == 1, received
    assert received[0]["direction"] == "grantee"
    assert received[0]["run_id"] == run["id"]

    # The scope header is a snapshot, not a run-wide permission: a second object of
    # the same run is absent from the stored grant.
    other_artifact = t073.upload_output(stack, lease, b"t073 second body\n")
    assert t073.artifact_scope(other_artifact) not in t073.grant_object_scope(row)


def test_only_the_run_owner_grants_and_the_grantee_must_be_another_administrator(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    stranger = t073.other_owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, _lease = t073.claimed_run(stack, owner)

    # A foreign owner cannot grant on this run: the same 404 as a missing run.
    st.assert_error(
        t073.create_grant(stranger.creator, run["id"], admin.principal_id),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )
    assert t073.grant_rows(stack.dbname) == []

    # The grantee must be an enabled administrator other than the run owner.
    non_admin = t073.create_grant(
        owner.creator, run["id"], stranger.creator.principal_id
    )
    error = st.assert_error(non_admin, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "grantee_id", error

    self_grant = t073.create_grant(
        owner.creator, run["id"], owner.creator.principal_id
    )
    error = st.assert_error(self_grant, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "grantee_id", error

    unknown_grantee = t073.create_grant(owner.creator, run["id"], str(uuid.uuid4()))
    st.assert_error(unknown_grantee, 404, "principal_not_found")

    # An unknown run is a located 404, not a validation leak.
    st.assert_error(
        t073.create_grant(owner.creator, str(uuid.uuid4()), admin.principal_id),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )

    # The request body is the contract's, field by field.
    malformed = t073.create_grant(
        owner.creator,
        run["id"],
        admin.principal_id,
        purposes=[],
        object_scope=[t073.artifact_scope(str(uuid.uuid4()))],
    )
    error = st.assert_error(malformed, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "purposes", error

    bad_purpose = t073.create_grant(
        owner.creator, run["id"], admin.principal_id, purposes=["Not A Token"]
    )
    error = st.assert_error(bad_purpose, 422, t073.CODE_VALIDATION)
    assert error["details"]["field"] == "purposes", error

    past_expiry = t073.expires_in(-60)
    error = st.assert_error(
        t073.create_grant(
            owner.creator, run["id"], admin.principal_id, expires_at=past_expiry
        ),
        422,
        t073.CODE_VALIDATION,
    )
    assert error["details"]["field"] == "expires_at", error

    # An object that does not belong to this run cannot be put in scope.
    foreign_artifact = t073.upload_input(stack, stranger.creator, b"not this run\n")
    error = st.assert_error(
        t073.create_grant(
            owner.creator,
            run["id"],
            admin.principal_id,
            object_scope=[t073.artifact_scope(foreign_artifact)],
        ),
        422,
        t073.CODE_VALIDATION,
    )
    assert error["details"]["field"] == "object_scope", error

    # CSRF is required on the cookie session, and a service credential has no
    # personal consent authority at all.
    st.assert_error(
        t073.create_grant(owner.creator, run["id"], admin.principal_id, csrf="wrong"),
        403,
        t073.CODE_CSRF,
    )
    anonymous = stack.client().post(
        t073.GRANTS_PATH,
        body={
            "run_id": run["id"],
            "grantee_id": admin.principal_id,
            "purposes": t073.GRANTED_PURPOSES,
            "expires_at": t073.expires_in(3600),
        },
    )
    st.assert_error(anonymous, 401, "unauthenticated")

    from support import t054

    key_client = t054.service_key_client(stack, admin, display_name="svc-t073")
    st.assert_error(
        t073.create_grant(
            key_client, run["id"], admin.principal_id, object_scope=[]
        ),
        403,
        t073.CODE_PERSONAL_SCOPE,
    )
    assert t073.grant_rows(stack.dbname) == []


def test_revocation_is_immediate_idempotent_and_owner_only(t069_stack) -> None:
    stack = t069_stack
    owner = t073.owner(stack)
    stranger = t073.other_owner(stack)
    admin = t073.admin_session(stack)
    _owner, run, lease = t073.claimed_run(stack, owner)
    artifact_id = t073.upload_output(stack, lease, b"t073 revoke body\n")
    grant = t073.create_grant_ok(
        owner.creator,
        run["id"],
        admin.principal_id,
        object_scope=[t073.artifact_scope(artifact_id)],
    )

    # A stranger cannot revoke it and learns nothing (same 404 as missing).
    st.assert_error(
        t073.revoke_grant(stranger.creator, grant["id"]),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )
    assert t073.grant_row(stack.dbname, grant["id"])["revoked_at"] is None

    revoked = t073.revoke_grant(owner.creator, grant["id"])
    assert revoked.status == 204, revoked.text
    row = t073.grant_row(stack.dbname, grant["id"])
    assert row["revoked_at"] is not None, row

    # Repeating the DELETE is idempotent: same terminal state, exactly one audit
    # row, and the first revocation timestamp is not overwritten.
    again = t073.revoke_grant(owner.creator, grant["id"])
    assert again.status == 204, again.text
    assert t073.grant_row(stack.dbname, grant["id"])["revoked_at"] == row["revoked_at"]
    revoked_audit = t073.audit_rows(stack.dbname, t073.ACTION_GRANT_REVOKED)
    assert len(revoked_audit) == 1, revoked_audit
    metadata = t073.audit_metadata(revoked_audit[0])
    assert metadata["grant_id"] == grant["id"], metadata
    assert metadata["run_id"] == run["id"], metadata
    assert metadata["grantee_id"] == admin.principal_id, metadata

    # The revoked grant is still readable as metadata, with its state derived.
    items = [item for item in t073.list_grant_items(owner.creator) if item["id"] == grant["id"]]
    assert len(items) == 1 and items[0]["state"] == t073.REVOKED, items

    st.assert_error(
        t073.revoke_grant(owner.creator, str(uuid.uuid4())),
        404,
        t073.CODE_RESOURCE_NOT_FOUND,
    )

    # A service credential can neither list nor revoke a personal grant.
    from support import t054

    key_client = t054.service_key_client(stack, admin, display_name="svc-t073-revoke")
    st.assert_error(
        t073.revoke_grant(key_client, grant["id"]), 403, t073.CODE_PERSONAL_SCOPE
    )
    st.assert_error(t073.list_grants(key_client), 403, t073.CODE_PERSONAL_SCOPE)
