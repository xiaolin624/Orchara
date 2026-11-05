"""F09/T058 acceptance: revising or deleting a memory stops old recall immediately.

Every test drives the real stack: PostgreSQL migrated through the T058 migration,
the compiled API, real cookie+CSRF writes and real sealed object bytes on disk.
A memory is always created through the real contract entry
(``POST /api/v1/memories``); its text, revision, artifact and cache epoch are
read from PostgreSQL or from the sealed file, never from the API echo alone.

The detail read is the strong-ETag seam: a memory is addressed by id and owned by
one human, so a foreign id, an unknown id, a malformed id and a deleted memory all
answer the same 404, and an administrator who does not own the row is not exempt.
"""

from __future__ import annotations

import datetime
import json
import threading
import uuid

import pytest

from support import f00b, t041, t054, t058
from support import stack as st

TEXT = "The user prefers concise weekly summaries in Chinese."
REVISED = "The user prefers a short English summary every Friday."


# ---------------------------------------------------------------------------
# setup helpers
# ---------------------------------------------------------------------------
def owned_memory(stack, owner, *, text: str = TEXT, application_id: str | None = None) -> dict:
    application = application_id or owner.application_id
    return t058.create_memory_ok(owner.creator, application, text)


# ---------------------------------------------------------------------------
# GET /api/v1/memories/{memory_id}: strong ETag, owner-only, no existence leak
# ---------------------------------------------------------------------------
def test_the_detail_read_is_owner_only_and_carries_the_strong_etag(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)

    response = t058.detail(owner.creator, created["id"])
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == t058.MEMORY_FIELDS, body
    assert body["id"] == created["id"]
    assert body["application_id"] == owner.application_id
    assert body["revision"] == 1
    assert body["text"] == TEXT
    # The contract's strong ETag is the revision as a quoted opaque token.
    assert t058.etag_of(response) == t058.expected_etag(1)
    assert response.header("Cache-Control") == "no-store"

    # The server text is the sealed object, not a second copy in a column.
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert row["deleted_at"] is None
    assert t058.text_of_memory(t058_stack, row) == TEXT


def test_the_detail_answers_the_same_404_for_foreign_unknown_and_deleted_rows(
    t058_stack,
) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)

    # Another real human with their own application: 404, never 403 (no existence
    # leak). The other human cannot even create a memory in this application.
    other = t058.other_owner(t058_stack)
    forbidden_scope = t054.create_memory(
        other.creator, application_id=owner.application_id, text="not mine"
    )
    st.assert_error(forbidden_scope, 404, t058.RESOURCE_NOT_FOUND)
    st.assert_error(t058.detail(other.creator, created["id"]), 404, t058.RESOURCE_NOT_FOUND)

    # An administrator is not exempt: a different principal sees the same 404.
    st.assert_error(t058.detail(owner.admin, created["id"]), 404, t058.RESOURCE_NOT_FOUND)

    # Unknown and malformed (non-empty) ids are the same located 404. An empty
    # path segment is not this route at all (the mux answers its own plain 404),
    # so it is not a contract case.
    for memory_id in (str(uuid.uuid4()), "not-a-uuid"):
        st.assert_error(t058.detail(owner.creator, memory_id), 404, t058.RESOURCE_NOT_FOUND)

    # The endpoint is not anonymous.
    anonymous = t058_stack.client().get(t058.memory_path(created["id"]))
    st.assert_error(anonymous, 401, "unauthenticated")


def test_a_service_credential_is_refused_on_the_detail_read(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    service = t058.service_client(t058_stack)

    st.assert_error(
        service.get(t058.memory_path(created["id"])), 403, t058.PERSONAL_SCOPE_FORBIDDEN
    )
    # The credential itself is valid: /auth/me still answers, so the 403 is an
    # authorization decision, not a malformed key.
    assert service.get("/api/v1/auth/me").status == 200
    # And the refusal wrote nothing.
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert row["deleted_at"] is None


def test_memories_are_isolated_by_owner_and_application(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    second_application = t058.new_application(owner.creator)
    first = owned_memory(t058_stack, owner)
    second = owned_memory(t058_stack, owner, text="The user works in Beijing.",
                          application_id=second_application)

    # The same owner's list is scoped by application; detail is owner-addressed
    # (the contract has no application dimension in the path).
    for application_id, expected in (
        (owner.application_id, {first["id"]}),
        (second_application, {second["id"]}),
    ):
        response = t058.list_memories(owner.creator, application_id=application_id)
        assert response.status == 200, response.text
        assert {item["id"] for item in response.json["items"]} == expected
    both = t058.list_memories(owner.creator)
    assert both.status == 200 and len(both.json["items"]) == 2, both.text
    assert t058.detail(owner.creator, second["id"]).json["text"] == "The user works in Beijing."

    # The cache epoch is per (owner, application): the second application's
    # creation did not touch the first application's epoch beyond its own write.
    assert t058.epoch_of(t058_stack.dbname, owner.creator.principal_id, owner.application_id) >= 1
    assert (
        t058.epoch_of(t058_stack.dbname, owner.creator.principal_id, second_application) >= 1
    )

    # A different human sees neither memory, and cannot address the scope.
    other = t058.other_owner(t058_stack)
    assert t058.list_memories(other.creator).json["items"] == []
    for memory_id in (first["id"], second["id"]):
        st.assert_error(t058.detail(other.creator, memory_id), 404, t058.RESOURCE_NOT_FOUND)


# ---------------------------------------------------------------------------
# PATCH /api/v1/memories/{memory_id}: strong If-Match, never last-writer-wins
# ---------------------------------------------------------------------------
def test_a_missing_if_match_is_refused_and_never_overwrites(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    artifacts_before = len(t058.memory_body_artifacts(t058_stack.dbname))

    for if_match in (None, ""):
        response = t058.update(owner.creator, created["id"], text=REVISED, if_match=if_match)
        error = st.assert_error(response, 428, t058.PRECONDITION_REQUIRED)
        assert error["details"]["header"] == "If-Match", error

    # Nothing moved: no revision bump, no new object, the same text.
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert t058.text_of_memory(t058_stack, row) == TEXT
    assert len(t058.memory_body_artifacts(t058_stack.dbname)) == artifacts_before
    assert t058.detail(owner.creator, created["id"]).json["text"] == TEXT


def test_a_stale_or_garbled_etag_is_a_412_that_reports_the_current_etag(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    artifacts_before = len(t058.memory_body_artifacts(t058_stack.dbname))

    # The only accepted token is this server's strong ETag for the current
    # revision. Weak validators, lists, "*", unquoted numbers and an earlier or
    # future revision are all a stale precondition, never a write.
    for if_match in ('"0"', '"2"', "1", 'W/"1"', "*", '"1", "2"', "multiple-values"):
        response = t058.update(owner.creator, created["id"], text=REVISED, if_match=if_match)
        error = st.assert_error(response, 412, t058.PRECONDITION_FAILED)
        assert error["details"]["current_etag"] == t058.expected_etag(1), (if_match, error)
        assert error["details"]["current_revision"] == 1, (if_match, error)

    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert t058.text_of_memory(t058_stack, row) == TEXT
    assert len(t058.memory_body_artifacts(t058_stack.dbname)) == artifacts_before


def test_a_matching_etag_writes_a_new_body_and_bumps_revision_and_epoch(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    before = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert before is not None
    epoch_before = t058.epoch_of(
        t058_stack.dbname, owner.creator.principal_id, owner.application_id
    )

    response = t058.update(owner.creator, created["id"], text=REVISED,
                           if_match=t058.expected_etag(1))
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == t058.MEMORY_FIELDS, body
    assert body["id"] == created["id"]
    assert body["revision"] == 2
    assert body["text"] == REVISED
    assert t058.etag_of(response) == t058.expected_etag(2)

    # The new content is a new sealed artifact; the row points at it.
    after = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert after is not None and after["revision"] == 2
    assert after["body_artifact_id"] != before["body_artifact_id"]
    new_artifact = t058.artifact_of(t058_stack.dbname, after["body_artifact_id"])
    assert new_artifact["purpose"] == "memory_body", new_artifact
    assert new_artifact["sha256"] == st.sha256_hex(REVISED), new_artifact
    assert t054.memory_text(t058_stack, after) == REVISED
    # The superseded artifact is intentionally left for F07 physical cleanup; the
    # row (the recall authority) no longer references it.
    assert t058.artifact_of(t058_stack.dbname, before["body_artifact_id"])["state"] == "ready"

    # Delete/update invalidation is synchronous: the memory-scope epoch moved in
    # the same transaction as the revision.
    epoch_after = t058.epoch_of(
        t058_stack.dbname, owner.creator.principal_id, owner.application_id
    )
    assert epoch_after > epoch_before, (epoch_before, epoch_after)

    # The old text is no longer served as current, through detail or list.
    detail = t058.detail(owner.creator, created["id"])
    assert detail.json["text"] == REVISED
    assert t058.etag_of(detail) == t058.expected_etag(2)
    assert t058.list_memories(owner.creator).json["items"][0]["text"] == REVISED

    # A recall-shaped read of the authoritative row finds no current row at the
    # old revision: an embedding or cache entry keyed to revision 1 can never be
    # served as current after the update.
    old_revision_is_current = st.sql_rows(
        t058_stack.dbname,
        "SELECT revision FROM platform.memories "
        f"WHERE id = '{created['id']}'::uuid AND deleted_at IS NULL AND revision = 1",
    )
    assert old_revision_is_current == []
    assert st.sql_rows(
        t058_stack.dbname,
        "SELECT revision FROM platform.memories "
        f"WHERE id = '{created['id']}'::uuid AND deleted_at IS NULL AND revision = 2",
    ) == [{"revision": 2}]

    # A retry holding the old ETag must not overwrite the new revision.
    stale = t058.update(owner.creator, created["id"], text="late write",
                        if_match=t058.expected_etag(1))
    error = st.assert_error(stale, 412, t058.PRECONDITION_FAILED)
    assert error["details"]["current_etag"] == t058.expected_etag(2), error
    assert t058.row_of_memory(t058_stack.dbname, created["id"])["revision"] == 2

    # The new ETag is the one that writes revision 3.
    accepted = t058.update(owner.creator, created["id"], text="The user moved to Shanghai.",
                           if_match=t058.expected_etag(2))
    assert accepted.status == 200, accepted.text
    assert accepted.json["revision"] == 3
    assert t058.etag_of(accepted) == t058.expected_etag(3)


def test_concurrent_updates_with_one_etag_have_exactly_one_winner(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    texts = ["first concurrent revision", "second concurrent revision"]
    responses: dict[int, object] = {}
    barrier = threading.Barrier(2)

    def push(index: int) -> None:
        barrier.wait(timeout=30)
        responses[index] = t058.update(
            owner.creator, created["id"], text=texts[index],
            if_match=t058.expected_etag(1),
        )

    threads = [threading.Thread(target=push, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    statuses = sorted(response.status for response in responses.values())
    assert statuses == [200, 412], [response.text for response in responses.values()]
    winner = next(response for response in responses.values() if response.status == 200)
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None
    assert row["revision"] == 2
    # One winner wrote revision 2; the loser's text was never committed, and no
    # third revision exists.
    assert t054.memory_text(t058_stack, row) == winner.json["text"]
    assert winner.json["text"] in texts


def test_the_update_body_is_validated_after_the_precondition(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    artifacts_before = len(t058.memory_body_artifacts(t058_stack.dbname))

    good = t058.expected_etag(1)
    for kwargs, field in (
        ({"text": REVISED, "confirmed": None}, "confirmed"),
        ({"text": REVISED, "confirmed": False}, "confirmed"),
        ({"text": "   ", "confirmed": True}, "text"),
        ({"text": "a" * 4001, "confirmed": True}, "text"),
        ({"text": None, "confirmed": True}, "text"),
    ):
        response = t058.update(owner.creator, created["id"], if_match=good, **kwargs)
        error = st.assert_error(response, 422, t058.VALIDATION_FAILED)
        assert error["details"]["field"] == field, (kwargs, error)

    # Unknown top-level keys are refused by the shared decoder (the contract body
    # has additionalProperties:false).
    malicious = owner.creator.client.request(
        "PATCH",
        t058.memory_path(created["id"]),
        body={"text": REVISED, "confirmed": True, "application_id": owner.application_id},
        csrf=owner.creator.csrf,
        headers={"If-Match": good},
    )
    st.assert_error(malicious, 400, "invalid_json")

    # The precondition runs before the body: a missing If-Match with an invalid
    # body is still a 428, and nothing was written by any refused call.
    first = t058.update(owner.creator, created["id"], text="", confirmed=False, if_match=None)
    st.assert_error(first, 428, t058.PRECONDITION_REQUIRED)
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert t058.text_of_memory(t058_stack, row) == TEXT
    assert len(t058.memory_body_artifacts(t058_stack.dbname)) == artifacts_before


def test_a_foreign_or_unknown_memory_cannot_be_updated(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    other = t058.other_owner(t058_stack)
    artifacts_before = len(t058.memory_body_artifacts(t058_stack.dbname))

    for memory_id in (created["id"], str(uuid.uuid4()), "not-a-uuid"):
        response = t058.update(other.creator, memory_id, text=REVISED,
                               if_match=t058.expected_etag(1))
        st.assert_error(response, 404, t058.RESOURCE_NOT_FOUND)
    # The administrator is a different principal too.
    admin = t058.update(owner.admin, created["id"], text=REVISED,
                        if_match=t058.expected_etag(1))
    st.assert_error(admin, 404, t058.RESOURCE_NOT_FOUND)

    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert len(t058.memory_body_artifacts(t058_stack.dbname)) == artifacts_before


def test_a_service_credential_is_refused_on_the_write_paths(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    service = t058.service_client(t058_stack)

    st.assert_error(
        service.request(
            "PATCH", t058.memory_path(created["id"]),
            body={"text": REVISED, "confirmed": True},
            headers={"If-Match": t058.expected_etag(1)},
        ),
        403, t058.PERSONAL_SCOPE_FORBIDDEN,
    )
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1
    assert t058.text_of_memory(t058_stack, row) == TEXT


# ---------------------------------------------------------------------------
# DELETE /api/v1/memories/{memory_id}: tombstone + epoch before any cleanup
# ---------------------------------------------------------------------------
def test_delete_answers_a_deletion_job_and_stops_old_recall_immediately(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    before = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert before is not None
    epoch_before = t058.epoch_of(
        t058_stack.dbname, owner.creator.principal_id, owner.application_id
    )

    response = t058.delete(owner.creator, created["id"])
    assert response.status == 202, response.text
    job = response.json
    assert set(job) == t058.DELETION_FIELDS, job
    assert job["resource_id"] == created["id"]
    assert job["state"] == "pending"
    job_id = job["id"]
    assert uuid.UUID(job_id)
    assert job_id != created["id"]

    # The authoritative tables moved synchronously, before any cleanup: the
    # tombstone, the revision and the memory-scope cache epoch are all facts of
    # the same transaction that answered the request.
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None
    assert row["revision"] == 2, row
    assert row["deleted_at"] is not None, row
    epoch_after = t058.epoch_of(
        t058_stack.dbname, owner.creator.principal_id, owner.application_id
    )
    assert epoch_after > epoch_before, (epoch_before, epoch_after)

    jobs = t058.deletion_jobs(t058_stack.dbname, resource_id=created["id"])
    assert len(jobs) == 1, jobs
    stored = jobs[0]
    assert stored["id"] == job_id
    assert stored["owner_id"] == owner.creator.principal_id
    assert stored["resource_type"] == "memory"
    assert stored["deletion_revision"] == 2
    assert stored["state"] == "pending"
    assert stored["completed_at"] is None
    scopes = t058.deletion_scopes(stored)
    artifact = t058.artifact_of(t058_stack.dbname, before["body_artifact_id"])
    # F11/T069: remaining_scopes is now the contract's closed scope vocabulary
    # (the T058 copy descriptors moved to scope_details). A memory delete has to
    # hand T071/T072 every class of physical copy, and T069 cannot prove any gone.
    assert set(scopes) == set(t058.DELETION_SCOPES), scopes
    details = t058.deletion_scope_details(stored)
    object_items = details["object_store"]["items"]
    object_scope = next(item for item in object_items if item["kind"] == "object")
    assert object_scope["object_key"] == artifact["object_key"], scopes
    assert object_scope["sha256"] == artifact["sha256"], scopes
    embedding_scope = details["search_index"]
    assert embedding_scope["memory_revisions"] == [1], embedding_scope

    # The next read refuses the old content: detail 404, list empty. A stale ETag
    # is not a 412 on a tombstone: the memory does not exist any more.
    st.assert_error(t058.detail(owner.creator, created["id"]), 404, t058.RESOURCE_NOT_FOUND)
    assert t058.list_memories(owner.creator).json["items"] == []
    stale = t058.update(owner.creator, created["id"], text=REVISED,
                        if_match=t058.expected_etag(1))
    st.assert_error(stale, 404, t058.RESOURCE_NOT_FOUND)

    # A recall-shaped read of the authoritative row finds nothing current: an
    # embedding/cache entry keyed to revision 1 can never be served as current.
    current = st.sql_rows(
        t058_stack.dbname,
        "SELECT revision FROM platform.memories "
        f"WHERE id = '{created['id']}'::uuid AND deleted_at IS NULL AND revision = 1",
    )
    assert current == []

    # The bytes are still on disk: T058 is honest about not doing F07's physical
    # purge, and the job it returned says pending.
    assert t054.artifact_bytes(t058_stack, artifact) == TEXT.encode("utf-8")

    # A repeated delete is idempotent: the same job, one row, no second bump.
    again = t058.delete(owner.creator, created["id"])
    assert again.status == 202, again.text
    assert again.json == job, (again.json, job)
    assert len(t058.deletion_jobs(t058_stack.dbname, resource_id=created["id"])) == 1
    assert t058.row_of_memory(t058_stack.dbname, created["id"])["revision"] == 2
    assert (
        t058.epoch_of(t058_stack.dbname, owner.creator.principal_id, owner.application_id)
        == epoch_after
    )


def test_delete_only_touches_the_owner_and_the_one_application_scope(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    other_application = t058.new_application(owner.creator)
    doomed = owned_memory(t058_stack, owner)
    kept = owned_memory(t058_stack, owner, text="The user works in Beijing.",
                        application_id=other_application)
    kept_epoch = t058.epoch_of(
        t058_stack.dbname, owner.creator.principal_id, other_application
    )

    # A foreign human, the administrator and a service credential all write
    # nothing: 404 / 404 / 403 and no job row.
    other = t058.other_owner(t058_stack)
    st.assert_error(t058.delete(other.creator, doomed["id"]), 404, t058.RESOURCE_NOT_FOUND)
    st.assert_error(t058.delete(owner.admin, doomed["id"]), 404, t058.RESOURCE_NOT_FOUND)
    service = t058.service_client(t058_stack)
    st.assert_error(
        service.request("DELETE", t058.memory_path(doomed["id"])),
        403, t058.PERSONAL_SCOPE_FORBIDDEN,
    )
    assert t058.deletion_jobs(t058_stack.dbname) == []

    # Deleting one memory bumps only its own (owner, application) epoch. The
    # memory in the other application and its epoch are untouched.
    accepted = t058.delete(owner.creator, doomed["id"])
    assert accepted.status == 202, accepted.text
    assert (
        t058.epoch_of(t058_stack.dbname, owner.creator.principal_id, other_application)
        == kept_epoch
    )
    kept_row = t058.row_of_memory(t058_stack.dbname, kept["id"])
    assert kept_row is not None and kept_row["revision"] == 1
    assert kept_row["deleted_at"] is None
    assert t058.detail(owner.creator, kept["id"]).status == 200
    assert t058.deletion_jobs(t058_stack.dbname, resource_id=kept["id"]) == []

    # An unknown or malformed memory id is the same located 404.
    for memory_id in (str(uuid.uuid4()), "not-a-uuid"):
        st.assert_error(t058.delete(owner.creator, memory_id), 404, t058.RESOURCE_NOT_FOUND)


def test_a_concurrent_delete_returns_one_job_and_bumps_the_revision_once(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    responses: dict[int, object] = {}
    barrier = threading.Barrier(2)

    def remove(index: int) -> None:
        barrier.wait(timeout=30)
        responses[index] = t058.delete(owner.creator, created["id"])

    threads = [threading.Thread(target=remove, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert [response.status for response in responses.values()] == [202, 202]
    jobs = {response.json["id"] for response in responses.values()}
    assert len(jobs) == 1, jobs
    assert len(t058.deletion_jobs(t058_stack.dbname, resource_id=created["id"])) == 1
    # Exactly one tombstone bump: two concurrent deletes are not revision 3.
    assert t058.row_of_memory(t058_stack.dbname, created["id"])["revision"] == 2


def test_a_service_credential_is_refused_on_delete(t058_stack) -> None:
    owner = t058.owner(t058_stack)
    created = owned_memory(t058_stack, owner)
    service = t058.service_client(t058_stack)

    st.assert_error(
        service.request("DELETE", t058.memory_path(created["id"])),
        403, t058.PERSONAL_SCOPE_FORBIDDEN,
    )
    row = t058.row_of_memory(t058_stack.dbname, created["id"])
    assert row is not None and row["revision"] == 1 and row["deleted_at"] is None
    assert t058.deletion_jobs(t058_stack.dbname) == []
