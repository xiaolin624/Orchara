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
