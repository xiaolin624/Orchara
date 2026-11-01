"""T020 acceptance 7: the key secret exists only in the one-time creation response.

The API's captured stdout/stderr, every request URL the clients made, the
``api_keys`` row and the audit trail are checked for the raw secret. Only the
SHA-256 lives in the database, and even that hash is never audited or logged.
"""

from __future__ import annotations

import hashlib
import json

from conftest import sql_rows
from t020_stack import (  # noqa: F401 - fixtures and helpers
    admin_session,
    api_server,
    audit_events,
    bearer_client,
    create_api_key_or_fail,
    create_service_account_or_fail,
    fresh_database,
    go_binaries,
    revoke_api_key,
    stack,
)


def test_the_secret_never_reaches_the_database_logs_audit_or_a_url(stack) -> None:
    admin = admin_session(stack)
    service_id = create_service_account_or_fail(admin, "svc-secret", admin.principal_id)
    key_id, secret = create_api_key_or_fail(admin, service_id)
    hash_hex = hashlib.sha256(secret.encode("utf-8")).hexdigest()

    bearer = bearer_client(stack, secret)
    assert bearer.get("/api/v1/auth/me").status == 200
    assert bearer.get("/api/v1/applications").status == 200
    assert bearer.get("/api/v1/memories").status == 403
    assert revoke_api_key(admin, key_id).status == 204

    # --- database -------------------------------------------------------
    row_payload = json.dumps(
        sql_rows(
            stack.dbname,
            "SELECT id::text AS id, principal_id::text AS principal_id, key_hash, "
            "scopes, expires_at::text AS expires_at, revoked_at::text AS revoked_at "
            "FROM platform.api_keys",
        )
    )
    assert secret not in row_payload
    assert hash_hex in row_payload  # only the hash is stored
    # The audit trail names the key, never its secret or hash.
    audit_payload = json.dumps(audit_events(stack.dbname), default=str)
    assert secret not in audit_payload
    assert hash_hex not in audit_payload
    assert key_id in audit_payload  # the event is locatable

    # --- API log --------------------------------------------------------
    log = stack.server.wait_for_log("DELETE /api/v1/admin/api-keys/")
    assert secret not in log, "the API log leaked the key secret"
    assert hash_hex not in log, "the API log leaked the stored key hash"
    # Log lines stay method/path/status/duration/request id and carry no query.
    for expected in (
        "POST /api/v1/admin/service-accounts 201",
        "POST /api/v1/admin/api-keys 201",
        "GET /api/v1/auth/me 200",
        "GET /api/v1/applications 200",
        "GET /api/v1/memories 403",
    ):
        assert expected in log, f"the log is missing {expected!r}:\n{log[-3000:]}"
    assert "?" not in "\n".join(
        line for line in log.splitlines() if " request_id=" in line
    ), "a request log line carried a query string"

    # --- request URLs ---------------------------------------------------
    for client in (admin.client, bearer):
        for method, path in client.requests:
            assert secret not in path, f"{method} {path} leaked the key secret"
            assert hash_hex not in path, f"{method} {path} leaked the key hash"
            assert "?" not in path, f"{method} {path} carried a query string"

    # The session cookie is a credential too: it was never placed in a URL.
    assert admin.cookie not in str(admin.client.requests)
