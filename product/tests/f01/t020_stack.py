"""Shared real-stack helpers for the T020 (service accounts / API keys) tests.

The seams are the T017/T019 ones: a throwaway PostgreSQL database created with
``CREATE DATABASE``, the compiled ``cmd/admin`` / ``cmd/api`` binaries and real
HTTP over a socket. Nothing here is mocked.

Two deliberate boundaries are documented in the T020 report:

* F02A owns ``createApplication`` and F09 owns the memory store, so the tests
  seed ``platform.applications`` rows and, for crafted credential states, a
  ``platform.api_keys`` row through psql. Those are the real tables the API
  reads; only their creation is out of band for F01.
* The responsible user's session is a real login/activation flow.
"""

from __future__ import annotations

import base64
import datetime as dt
import os
import uuid

from conftest import HttpClient, Response, sha256_hex, sql, sql_rows
from t017_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    Session,
    Stack,
    admin_session,
    api_server,
    bootstrap_or_fail,
    client_with_cookie,
    fresh_database,
    go_binaries,
    invite,
    invite_and_activate,
    invite_body,
    migrate_or_fail,
    session_for,
    sign_in,
    stack,
)
from t019_stack import (  # noqa: F401 - fixtures and helpers are re-exported
    APPLICATION,
    READ,
    application_ids,
    audit_events,
    disable_principal,
    enabled_human_admins,
    epoch_revision,
    get_application,
    grant_read,
    grant_read_or_fail,
    list_applications,
    new_uuid,
    revoke_grant,
    role_bindings,
    seed_application,
    seed_applications_for_series,
    seed_service_principal,
    set_roles,
)

SCOPE_APPLICATION_READ = "application:read"
API_KEY_PREFIX = "orchara_"

# A far-future timestamp is used where the test wants a key that outlives the
# test run; a near one is used where expiry is the subject.
def future_rfc3339(hours: float = 2) -> str:
    moment = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def past_rfc3339(hours: float = 1) -> str:
    return future_rfc3339(-hours)


# ---------------------------------------------------------------------------
# Service accounts: real HTTP helpers
# ---------------------------------------------------------------------------
def create_service_account(
    admin: Session,
    display_name: str,
    responsible_user_id: str,
) -> Response:
    return admin.client.post(
        "/api/v1/admin/service-accounts",
        body={"display_name": display_name, "responsible_user_id": responsible_user_id},
        csrf=admin.csrf,
    )


def create_service_account_or_fail(
    admin: Session,
    display_name: str,
    responsible_user_id: str,
) -> str:
    response = create_service_account(admin, display_name, responsible_user_id)
    assert response.status == 201, response.text
    return response.json["id"]


# ---------------------------------------------------------------------------
# API keys: real HTTP helpers
# ---------------------------------------------------------------------------
def create_api_key(
    admin: Session,
    principal_id: str,
    *,
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> Response:
    return admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": principal_id,
            "scopes": list(scopes if scopes is not None else [SCOPE_APPLICATION_READ]),
            "expires_at": expires_at or future_rfc3339(2),
        },
        csrf=admin.csrf,
    )


def create_api_key_or_fail(
    admin: Session,
    principal_id: str,
    *,
    scopes: list[str] | None = None,
    expires_at: str | None = None,
) -> tuple[str, str]:
    response = create_api_key(admin, principal_id, scopes=scopes, expires_at=expires_at)
    assert response.status == 201, response.text
    body = response.json
    assert set(body) == {"id", "secret"}, body
    return body["id"], body["secret"]


def revoke_api_key(admin: Session, key_id: str) -> Response:
    return admin.client.request(
        "DELETE", f"/api/v1/admin/api-keys/{key_id}", csrf=admin.csrf
    )


# ---------------------------------------------------------------------------
# Bearer HTTP client
# ---------------------------------------------------------------------------
class BearerClient(HttpClient):
    """A client that carries only an Authorization header (no cookie jar use)."""

    def __init__(self, base_url: str, secret: str) -> None:
        super().__init__(base_url)
        self.secret = secret

    def request(self, method: str, path: str, **kwargs):  # noqa: ANN001, ANN003
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Authorization", f"Bearer {self.secret}")
        return super().request(method, path, headers=headers, **kwargs)


def bearer_client(stack: Stack, secret: str) -> BearerClient:
    return BearerClient(stack.server.base_url, secret)


# ---------------------------------------------------------------------------
# Database assertions and crafted states
# ---------------------------------------------------------------------------
def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def api_keys(dbname: str, *, principal_id: str | None = None) -> list[dict]:
    where = f"WHERE principal_id = {_literal(principal_id)}::uuid" if principal_id else ""
    return sql_rows(
        dbname,
        "SELECT id::text AS id, principal_id::text AS principal_id, key_hash, "
        "scopes, expires_at::text AS expires_at, revoked_at::text AS revoked_at "
        f"FROM platform.api_keys {where} ORDER BY id",
    )


def audit_metadata(dbname: str, action: str) -> list[dict]:
    import json

    return [
        {"actor_id": row["actor_id"], "resource_id": row["resource_id"], **json.loads(row["metadata"])}
        for row in audit_events(dbname, action)
    ]


def seed_api_key_row(
    dbname: str,
    principal_id: str,
    *,
    scopes: list[str] | None = None,
    expires_in_hours: float = 2,
    secret: str | None = None,
    revoked: bool = False,
) -> tuple[str, str]:
    """Insert a real ``platform.api_keys`` row and return ``(key_id, secret)``.

    Creation refuses non-``application:read`` scopes, so a crafted "write key"
    can only be produced by seeding the row directly. The hash stored is the
    real SHA-256 of the returned secret, which the API then authenticates.
    """
    key_id = new_uuid()
    if secret is None:
        secret = API_KEY_PREFIX + base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    scopes = scopes or [SCOPE_APPLICATION_READ]
    array = "ARRAY[" + ", ".join(_literal(scope) for scope in scopes) + "]::text[]"
    revoked_at = "now()" if revoked else "NULL"
    sql(
        dbname,
        "INSERT INTO platform.api_keys (id, principal_id, key_hash, scopes, expires_at, revoked_at) "
        f"VALUES ({_literal(key_id)}::uuid, {_literal(principal_id)}::uuid, "
        f"{_literal(sha256_hex(secret))}, {array}, "
        f"now() + interval '{float(expires_in_hours)} hours', {revoked_at})",
    )
    return key_id, secret


def expire_api_key(dbname: str, key_id: str) -> None:
    sql(
        dbname,
        "UPDATE platform.api_keys SET expires_at = now() - interval '1 minute' "
        f"WHERE id = {_literal(key_id)}::uuid",
    )
