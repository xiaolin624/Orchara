"""T055 acceptance: knowledge-base/document HTTP surface and owner-only ACL.

Every request is real HTTP against the compiled API and real PostgreSQL. The ACL
is proven with two ordinary accounts **and** an administrator: a non-owner gets
exactly the same 404 as a missing row, never 403, so the surface cannot be used
to probe which knowledge bases exist. API keys use the existing
application:read/application:write scopes; cookie sessions need CSRF.
"""

from __future__ import annotations

import pytest

from support import f00b, t055
from support.fixtures import t055_stack  # noqa: F401 - pytest fixture

pytestmark = pytest.mark.usefixtures("t055_stack")


def make_user(stack, admin, login: str, *, roles: tuple[str, ...] = ("user",)):
    return f00b.invite_and_activate(stack, admin, login, roles=roles, password=f00b.USER_PASSWORD)


def create_base_ok(session, name: str = "t055-base") -> str:
    response = t055.create_knowledge_base(session, name)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "state"}, body
    assert body["state"] == "active"
    return body["id"]


def test_create_list_get_knowledge_base_has_exactly_the_contract_shape(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")

    created = t055.create_knowledge_base(alice, "  Sales knowledge  ")
    assert created.status == 201, created.text
    assert set(created.json) == {"id", "state"}
    assert created.json["state"] == "active"
    base_id = created.json["id"]

    listed = t055.list_knowledge_bases(alice)
    assert listed.status == 200, listed.text
    assert set(listed.json) == {"items", "next_cursor"}
    assert listed.json["next_cursor"] is None
    assert len(listed.json["items"]) == 1
    item = listed.json["items"][0]
    assert set(item) == {"id", "name", "revision"}
    assert item == {"id": base_id, "name": "Sales knowledge", "revision": 1}

    fetched = t055.get_knowledge_base(alice, base_id)
    assert fetched.status == 200, fetched.text
    assert fetched.json == {"id": base_id, "name": "Sales knowledge", "revision": 1}


def test_list_is_cursor_paginated_and_owner_scoped(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")
    bob = make_user(t055_stack, admin, "t055.bob")
    alice_ids = {create_base_ok(alice, f"a-{index}") for index in range(3)}

    first = t055.list_knowledge_bases(alice, limit=2)
    assert first.status == 200, first.text
    assert len(first.json["items"]) == 2
    assert first.json["next_cursor"]
    second = t055.list_knowledge_bases(alice, limit=2, cursor=first.json["next_cursor"])
    assert second.status == 200, second.text
    assert len(second.json["items"]) == 1
    assert second.json["next_cursor"] is None
    assert {entry["id"] for entry in first.json["items"]} | {
        entry["id"] for entry in second.json["items"]
    } == alice_ids

    bob_list = t055.list_knowledge_bases(bob)
    assert bob_list.status == 200, bob_list.text
    assert bob_list.json == {"items": [], "next_cursor": None}

    assert t055.list_knowledge_bases(alice, limit=0).status == 422
    assert t055.list_knowledge_bases(alice, cursor="not-a-cursor").status == 400


def test_a_foreign_principal_and_an_administrator_both_get_404(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")
    bob = make_user(t055_stack, admin, "t055.bob")
    base_id = create_base_ok(alice)

    for caller in (bob, admin):
        response = t055.get_knowledge_base(caller, base_id)
        f00b_assert_404(response)
        assert t055.list_documents(caller, base_id).status == 404
        assert t055.ingest_document(caller, base_id, f00b.new_uuid()).status == 404

    # A missing id and a foreign id are the same answer.
    missing = t055.get_knowledge_base(alice, f00b.new_uuid())
    f00b_assert_404(missing)
    assert missing.json["error"]["code"] == t055.get_knowledge_base(bob, base_id).json["error"]["code"]
    assert t055.get_knowledge_base(alice, "not-a-uuid").status == 404


def test_ingest_requires_a_ready_owner_artifact_and_a_registered_parser(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")
    bob = make_user(t055_stack, admin, "t055.bob")
    base_id = create_base_ok(alice)

    # Not completed yet: uploading is a 409 precondition, not a silent accept.
    reservation = t055.t028.create_upload(
        alice, media_type=t055.TEXT, size_bytes=16, purpose=t055.PURPOSE_KNOWLEDGE
    )
    assert reservation.status == 201, reservation.text
    pending = t055.ingest_document(alice, base_id, reservation.json["artifact_id"])
    assert pending.status == 409, pending.text
    assert pending.json["error"]["code"] == "precondition_failed"

    # Another principal's ready-looking artifact id must not be usable.
    bobs = t055.upload_text_artifact(
        bob, "bob's private text\n", media_type=t055.TEXT
    )
    t055.finalize_once(t055_stack)
    t055.wait_for_artifact_state(bob, bobs, {"ready"})
    foreign = t055.ingest_document(alice, base_id, bobs)
    f00b_assert_404(foreign)

    # CSV has no registered parser in this slice: a clear 422, not a failed job.
    csv_artifact = t055.upload_text_artifact(
        alice, "region,amount\nnorth,1\n", media_type=t055.CSV
    )
    t055.finalize_once(t055_stack)
    t055.wait_for_artifact_state(alice, csv_artifact, {"ready"})
    unsupported = t055.ingest_document(alice, base_id, csv_artifact)
    assert unsupported.status == 422, unsupported.text
    assert unsupported.json["error"]["code"] == "validation_failed"
    assert unsupported.json["error"]["details"]["field"] == "source_artifact_id"


def test_cookie_sessions_need_csrf_and_api_keys_need_the_existing_scopes(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")

    no_csrf = t055.create_knowledge_base(alice, "no csrf", csrf=False)
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid"

    service_id = t055.create_service_account(admin, "t055 service", alice.principal_id)
    _read_key_id, read_secret = t055.create_api_key(admin, service_id, ["application:read"])
    _write_key_id, write_secret = t055.create_api_key(admin, service_id, ["application:write"])
    http = t055_stack.client()
    read_headers = {"Authorization": f"Bearer {read_secret}"}

    listed = http.get("/api/v1/knowledge-bases", headers=read_headers)
    assert listed.status == 200, listed.text
    refused_write = http.post(
        "/api/v1/knowledge-bases", body={"name": "keyed"}, headers=read_headers
    )
    assert refused_write.status == 403, refused_write.text
    assert refused_write.json["error"]["code"] == "insufficient_scope"

    write_headers = {"Authorization": f"Bearer {write_secret}"}
    created = http.post(
        "/api/v1/knowledge-bases", body={"name": "keyed"}, headers=write_headers
    )
    assert created.status == 201, created.text
    refused_read = http.get("/api/v1/knowledge-bases", headers=write_headers)
    assert refused_read.status == 403, refused_read.text
    assert refused_read.json["error"]["code"] == "insufficient_scope"


def test_malformed_bodies_and_unknown_fields_are_refused(t055_stack) -> None:
    admin = f00b.admin_session(t055_stack)
    alice = make_user(t055_stack, admin, "t055.alice")

    empty_name = t055.create_knowledge_base(alice, "   ")
    assert empty_name.status == 422, empty_name.text
    assert empty_name.json["error"]["details"]["field"] == "name"

    unknown_field = alice.client.post(
        "/api/v1/knowledge-bases",
        body={"name": "x", "owner_id": "someone-else"},
        csrf=alice.csrf,
    )
    assert unknown_field.status == 400, unknown_field.text

    base_id = create_base_ok(alice)
    bad_body = t055.ingest_document(alice, base_id, f00b.new_uuid(), body={"artifact": "x"})
    assert bad_body.status == 400, bad_body.text


def f00b_assert_404(response) -> None:
    assert response.status == 404, response.text
    assert response.json["error"]["code"] == "resource_not_found"
    assert response.json["error"]["request_id"]
