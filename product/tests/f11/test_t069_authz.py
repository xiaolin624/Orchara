"""F11/T069: deletion authorization boundaries.

The DELETE routes are owner-only and follow the neighbouring endpoint rules: a
cookie session must present X-CSRF-Token (a forged request is refused before any
lookup), an API key needs the existing application:write scope for a delete and
application:read for the job read, and an administrator is not exempt from the
resource ACL (it gets the same located 404 as a stranger).
"""

from __future__ import annotations

import json
import uuid

from support import t028, t054, t069
from support import stack as st


def test_a_delete_without_the_csrf_token_changes_nothing(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    memory = t069.create_memory(
        stack, owner.creator, owner.application_id, "t069 csrf marker"
    )
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"t069 csrf artifact\n")
    before_memory = t069.memory_row(stack.dbname, memory["id"])
    before_artifact = t069.artifact_row(stack.dbname, artifact)

    for resource_type, resource_id in (
        (t069.RESOURCE_MEMORY, memory["id"]),
        (t069.RESOURCE_ARTIFACT, artifact),
        (t069.RESOURCE_DOCUMENT, str(uuid.uuid4())),
        (t069.RESOURCE_CONVERSATION, str(uuid.uuid4())),
    ):
        refused = t069.delete_resource(
            owner.creator, resource_type, resource_id, csrf=""
        )
        st.assert_error(refused, 403, "csrf_invalid")

    assert t069.memory_row(stack.dbname, memory["id"]) == before_memory
    assert t069.artifact_row(stack.dbname, artifact) == before_artifact
    assert t069.deletion_jobs(stack.dbname) == []


def test_anonymous_deletes_are_401_and_admin_is_not_exempt(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"t069 admin acl\n")
    admin = owner.admin
    anonymous = stack.client()

    for resource_type, resource_id in (
        (t069.RESOURCE_ARTIFACT, artifact),
        (t069.RESOURCE_MEMORY, str(uuid.uuid4())),
        (t069.RESOURCE_DOCUMENT, str(uuid.uuid4())),
        (t069.RESOURCE_CONVERSATION, str(uuid.uuid4())),
    ):
        st.assert_error(
            t069.delete_resource(anonymous, resource_type, resource_id),
            401,
            "unauthenticated",
        )
        # The administrator is a non-owner of user content: same 404.
        st.assert_error(
            t069.delete_resource(admin, resource_type, resource_id),
            404,
            t069.ERROR_RESOURCE_NOT_FOUND,
        )

    assert t069.artifact_row(stack.dbname, artifact)["deleted_at"] is None
    assert t069.deletion_jobs(stack.dbname) == []


def test_api_key_scopes_gate_delete_and_the_job_read(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    memory = t069.create_memory(
        stack, owner.creator, owner.application_id, "t069 scope marker"
    )
    artifact = t069.upload_ready_artifact(stack, owner.creator, b"t069 scope artifact\n")
    write_only = t054.service_key_client(
        stack, owner.admin, display_name="svc-t069-write", scopes=("application:write",)
    )
    read_only = t054.service_key_client(
        stack, owner.admin, display_name="svc-t069-read", scopes=("application:read",)
    )

    # A key without application:write is refused before the resource lookup.
    st.assert_error(
        t069.delete_resource(
            read_only, t069.RESOURCE_ARTIFACT, artifact, client=read_only
        ),
        403,
        "insufficient_scope",
    )
    assert t069.artifact_row(stack.dbname, artifact)["deleted_at"] is None

    # A service principal is never the owner of a human memory; the human-only
    # boundary is checked after the scope, so this is the memory-specific 403.
    st.assert_error(
        t069.delete_resource(
            write_only, t069.RESOURCE_MEMORY, memory["id"], client=write_only
        ),
        403,
        t069.ERROR_PERSONAL_SCOPE,
    )
    assert t069.memory_row(stack.dbname, memory["id"])["deleted_at"] is None
    # A scoped service key is also not the artifact owner: 404, never 403 leak.
    st.assert_error(
        t069.delete_resource(
            write_only, t069.RESOURCE_ARTIFACT, artifact, client=write_only
        ),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )

    # The job read needs application:read.
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_MEMORY, memory["id"])
    )
    st.assert_error(
        t069.get_deletion(write_only, job["id"], client=write_only),
        403,
        "insufficient_scope",
    )
    allowed = t069.get_deletion(owner.creator, job["id"])
    assert allowed.status == 200, allowed.text


def test_the_deletion_audit_keeps_ids_but_no_content(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    secret = "t069-audit-body-sentinel-do-not-copy"
    artifact = t069.upload_ready_artifact(stack, owner.creator, secret.encode())
    job = t069.deletion_of(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact)
    )

    events = t069.audit_events(
        stack.dbname, "deletion.requested", resource_id=artifact
    )
    assert len(events) == 1, events
    metadata = json.loads(events[0]["metadata"])
    assert set(metadata) == {
        "deletion_id",
        "resource_type",
        "resource_id",
        "deletion_revision",
        "remaining_scopes",
        "affected_run_ids",
    }, metadata
    assert metadata["deletion_id"] == job["id"]
    assert metadata["resource_type"] == t069.RESOURCE_ARTIFACT
    assert metadata["resource_id"] == artifact
    assert metadata["remaining_scopes"] == list(t069.SCOPES)
    assert metadata["affected_run_ids"] == []
    assert secret not in events[0]["metadata"], events[0]["metadata"]

    # The artifact row still names the sealed key (T071 owns the purge); the
    # audit does not duplicate the content or the key.
    row = t069.artifact_row(stack.dbname, artifact)
    assert row["object_key"] not in events[0]["metadata"]
    assert t028.get_artifact(owner.creator, artifact).status == 404
