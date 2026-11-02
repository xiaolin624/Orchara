"""T020 acceptance 5: service accounts cannot borrow personal data.

``GET /api/v1/memories`` is refused for a service credential before any storage
is touched. F09 owns the personal memory store, so until it lands a human gets an
honest empty authorized list (never fabricated rows), with only ``limit``
validated and every other contract query parameter ignored.
"""

from __future__ import annotations

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

    # And the credential is still a well-formed key: /auth/me works, so the 403
    # is an authorization decision rather than a broken request.
    assert bearer.get("/api/v1/auth/me").status == 200


def test_a_human_gets_an_honest_empty_memory_list(stack) -> None:
    admin = admin_session(stack)
    response = admin.client.get("/api/v1/memories")
    assert response.status == 200, response.text
    assert response.json == {"items": [], "next_cursor": None}
    assert response.header("Cache-Control") == "no-store"

    # limit is validated inside the contract range; cursor and application_id
    # are accepted by the contract and ignored until F09 owns the store.
    for query in ("?limit=1", "?limit=100", "?cursor=opaque&application_id=not-a-uuid"):
        response = admin.client.get("/api/v1/memories" + query)
        assert response.status == 200, (query, response.text)
        assert response.json == {"items": [], "next_cursor": None}, query

    for query in ("?limit=0", "?limit=101", "?limit=abc", "?limit=", "?limit=-1"):
        response = admin.client.get("/api/v1/memories" + query)
        error = assert_error(response, 422, "validation_failed")
        assert error.get("details", {}).get("field") == "limit", (query, error)

    # A human session is required; the endpoint is not anonymous.
    assert_error(stack.client().get("/api/v1/memories"), 401, "unauthenticated")
