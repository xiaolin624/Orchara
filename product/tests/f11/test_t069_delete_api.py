"""F11/T069: the shared deletion foundation's public contract.

Real stack throughout: PostgreSQL 16, the compiled API behind HTTP with real
sealed object bytes, real cookie+CSRF sessions. A delete must tombstone the
resource in the same transaction that answers 202, and the answer must be a
queryable deletion job; physical cleanup is T071/T072 and is never claimed here.
"""

from __future__ import annotations

import uuid

from support import t028, t069
from support import stack as st


def test_delete_artifact_answers_202_and_tombstones_before_any_cleanup(
    t069_stack,
) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    content = b"t069 artifact body: marker-artifact-deletion\n"
    artifact_id = t069.upload_ready_artifact(stack, owner.creator, content)
    before = t069.artifact_row(stack.dbname, artifact_id)
    assert before["state"] == "ready" and before["deleted_at"] is None

    response = t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    job = t069.deletion_of(response)
    assert job["resource_id"] == artifact_id
    assert job["state"] == "pending"
    assert job["id"] != artifact_id

    # The tombstone is a fact of the transaction that answered the request: the
    # artifact row moved before any maintenance/cleanup process ran.
    after = t069.artifact_row(stack.dbname, artifact_id)
    assert after["deleted_at"] is not None, after
    assert after["state"] == "deleted", after
    assert after["revision"] == before["revision"] + 1, (before, after)
    # T069 does not purge: the sealed bytes are still real and purged_at is unset.
    assert after["purged_at"] is None
    sealed = stack.object_dir / after["object_key"]
    assert sealed.exists() and sealed.read_bytes() == content

    # The authoritative read endpoints refuse immediately, before any cleanup.
    st.assert_error(
        t028.get_artifact(owner.creator, artifact_id),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
    for path in ("/download", "/content"):
        refused = owner.creator.client.get(
            t069.resource_path(t069.RESOURCE_ARTIFACT, artifact_id) + path
        )
        st.assert_error(refused, 404, t069.ERROR_RESOURCE_NOT_FOUND)

    # The 202 answer is queryable and reports the real remaining scopes.
    fetched = t069.get_deletion(owner.creator, job["id"])
    assert fetched.status == 200, fetched.text
    assert set(fetched.json) == t069.DELETION_JOB_FIELDS, fetched.json
    assert fetched.json["id"] == job["id"]
    assert fetched.json["state"] == "pending"
    assert fetched.json["remaining_scopes"] == list(t069.SCOPES), fetched.json
    assert fetched.json["remaining_scopes"] == sorted(t069.SCOPES), fetched.json
    stored = t069.deletion_job(stack.dbname, job["id"])
    assert stored is not None
    assert stored["owner_id"] == owner.creator.principal_id
    assert stored["resource_type"] == t069.RESOURCE_ARTIFACT
    assert stored["resource_id"] == artifact_id
    assert stored["deletion_revision"] == after["revision"]
    assert stored["state"] == "pending"
    assert stored["completed_at"] is None
    assert t069.remaining_scopes(stored) == list(t069.SCOPES)
    details = t069.scope_details(stored)
    assert set(details) == set(t069.SCOPES), details
    object_items = details["object_store"]["items"]
    assert any(
        item.get("object_key") == after["object_key"] for item in object_items
    ), object_items

    # A repeated DELETE returns the same job, not a second one and not a second
    # revision bump.
    again = t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, artifact_id)
    assert again.status == 202, again.text
    assert again.json == job, (again.json, job)
    assert len(
        t069.deletion_jobs(
            stack.dbname, resource_type=t069.RESOURCE_ARTIFACT, resource_id=artifact_id
        )
    ) == 1
    assert t069.artifact_row(stack.dbname, artifact_id)["revision"] == after["revision"]

    # A foreign principal cannot read the deletion job; an unknown one is the
    # same located 404 (no enumeration).
    other = t069.other_owner(stack)
    st.assert_error(
        t069.get_deletion(other.creator, job["id"]), 404, t069.ERROR_RESOURCE_NOT_FOUND
    )
    st.assert_error(
        t069.get_deletion(owner.creator, str(uuid.uuid4())),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
    st.assert_error(
        t069.get_deletion(owner.creator, "not-a-uuid"),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )


def test_delete_of_an_unknown_or_foreign_artifact_is_the_same_404(t069_stack) -> None:
    stack = t069_stack
    owner = t069.owner(stack)
    other = t069.other_owner(stack)
    foreign = t069.upload_ready_artifact(stack, other.creator, b"not yours\n")

    st.assert_error(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, str(uuid.uuid4())),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
    st.assert_error(
        t069.delete_resource(owner.creator, t069.RESOURCE_ARTIFACT, foreign),
        404,
        t069.ERROR_RESOURCE_NOT_FOUND,
    )
    assert t069.deletion_jobs(stack.dbname) == []
    assert t069.artifact_row(stack.dbname, foreign)["deleted_at"] is None
