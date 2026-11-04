"""T020 acceptance 5: service accounts cannot borrow personal data.

``GET /api/v1/memories`` is refused for a service credential before any storage
is touched. F09/T054 replaced F01's honest empty list with the real read model;
this test keeps the credential boundary it was written for and otherwise checks
that a human gets an authorized, empty list on a fresh database, plus the
contract query validation the real store now performs (the F01 boundary used to
ignore ``cursor``/``application_id`` because there was nothing behind them).
"""

from __future__ import annotations

import uuid

from conftest import assert_error
from t020_stack import (  # noqa: F401 - fixtures and helpers
    admin_session,
    api_server,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    go_binaries,
    seed_api_key_row,
    stack,
)


def test_a_service_key_cannot_read_personal_memories(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-memory", admin.principal_id)
    _key_id, secret = create_api_key_or_fail(admin, service_id)
    bearer = bearer_client(stack, secret)

    # A normal application-scoped key is refused with the personal-data code,
    # not with insufficient_scope: the endpoint itself is human-only.
    assert_error(bearer.get("/api/v1/memories"), 403, "personal_scope_forbidden")

    # The refusal is about the credential, not the requested scope: a crafted
    # key that carries a personal scope is still refused.
    for scopes in (["personal:read"], ["memory:read"], ["application:read", "memory:read"]):
        _id, crafted_secret = seed_api_key_row(stack.dbname, service_id, scopes=scopes)
        crafted = bearer_client(stack, crafted_secret)
        assert_error(crafted.get("/api/v1/memories"), 403, "personal_scope_forbidden")

    # The write side of personal memory is refused by the same credential rule
    # (F09/T054 added the write operation).
    assert_error(
        bearer.post(
            "/api/v1/memories",
            body={"application_id": str(uuid.uuid4()), "text": "x", "confirmed": True},
        ),
        403,
        "personal_scope_forbidden",
    )

    # And the credential is still a well-formed key: /auth/me works, so the 403
    # is an authorization decision rather than a broken request.
    assert bearer.get("/api/v1/auth/me").status == 200


def test_a_human_gets_the_real_authorized_memory_list(stack) -> None:
    admin = admin_session(stack)
    response = admin.client.get("/api/v1/memories")
    assert response.status == 200, response.text
    assert response.json == {"items": [], "next_cursor": None}
    assert response.header("Cache-Control") == "no-store"

    # Valid query values address the real read model and return an empty page for
    # an owner with no memories.
    for query in ("?limit=1", "?limit=100", f"?application_id={uuid.uuid4()}"):
        response = admin.client.get("/api/v1/memories" + query)
        assert response.status == 200, (query, response.text)
        assert response.json == {"items": [], "next_cursor": None}, query

    # A malformed query value is a located 422 (F09 owns the store these values
    # address; F01 accepted and ignored them because there was no store).
    for query in ("?limit=0", "?limit=101", "?limit=abc", "?limit=", "?limit=-1"):
        response = admin.client.get("/api/v1/memories" + query)
        error = assert_error(response, 422, "validation_failed")
        assert error.get("details", {}).get("field") == "limit", (query, error)
    response = admin.client.get("/api/v1/memories?application_id=not-a-uuid")
    error = assert_error(response, 422, "validation_failed")
    assert error.get("details", {}).get("field") == "application_id", error
    # An opaque cursor that is not one of ours is a 400, not an empty page.
    assert_error(admin.client.get("/api/v1/memories?cursor=opaque"), 400, "invalid_cursor")

    # A human session is required; the endpoint is not anonymous.
    assert_error(stack.client().get("/api/v1/memories"), 401, "unauthenticated")
