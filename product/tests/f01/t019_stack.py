"""Shared real-stack helpers for the T019 (resource grants) tests.

The seams are the T017 ones: a throwaway PostgreSQL database created with
``CREATE DATABASE``, the compiled ``cmd/admin`` / ``cmd/api`` binaries and real
HTTP over a socket. Nothing here is mocked.

One deliberate exception is documented in the T019 report: F02A owns
``createApplication``, so there is no HTTP way to create an application yet.
These tests seed ``platform.applications`` rows directly through psql, which is
the real table the API reads; the rows are real, only their creation is out of
band for F01.
"""

from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
import uuid

from conftest import pg_dev, sql, sql_rows
from t017_stack import (  # noqa: F401 - fixtures are re-exported for test modules
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    USER_PASSWORD,
    CookieOnlyClient,
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

APPLICATION = "application"
READ = "read"

DEMO_VERSION_ID = "9b2f8f0a-0000-4000-8000-000000000001"


def new_uuid() -> str:
    return str(uuid.uuid4())


def _literal(value: str) -> str:
    """Quote a test-controlled string as a SQL literal (no secrets here)."""
    return "'" + value.replace("'", "''") + "'"


def seed_application(
    stack: Stack,
    owner_id: str,
    name: str,
    *,
    age_minutes: int = 0,
    active_version_id: str | None = None,
    archived: bool = False,
) -> str:
    """Insert one real ``platform.applications`` row and return its id.

    ``age_minutes`` backdates ``created_at`` so a test can make ordering
    deterministic without sleeping. Larger age means older, so the list order
    (created_at DESC) is the reverse of the insertion order when ages increase.
    """
    application_id = new_uuid()
    created_at = f"now() - interval '{age_minutes} minutes'" if age_minutes else "now()"
    archived_at = "now()" if archived else "NULL"
    active = f"{_literal(active_version_id)}::uuid" if active_version_id else "NULL"
    sql(
        stack.dbname,
        "INSERT INTO platform.applications "
        "(id, owner_id, name, active_version_id, archived_at, created_at) VALUES "
        f"({_literal(application_id)}::uuid, {_literal(owner_id)}::uuid, "
        f"{_literal(name)}, {active}, {archived_at}, {created_at})",
    )
    return application_id


def seed_applications_for_series(
    stack: Stack,
    owner_id: str,
    count: int,
    *,
    prefix: str = "series",
) -> None:
    """Insert ``count`` applications owned by owner_id, one per minute in the past."""
    sql(
        stack.dbname,
        "INSERT INTO platform.applications (id, owner_id, name, created_at) "
        "SELECT gen_random_uuid(), "
        f"{_literal(owner_id)}::uuid, {_literal(prefix)} || '-' || i, "
        "now() - (i || ' minutes')::interval "
        f"FROM generate_series(1, {int(count)}) AS i",
    )


def grant_read(
    admin: Session,
    principal_id: str,
    application_id: str,
    *,
    expires_at: str | None = None,
):
    body = {
        "principal_id": principal_id,
        "resource_type": APPLICATION,
        "resource_id": application_id,
        "action": READ,
    }
    if expires_at is not None:
        body["expires_at"] = expires_at
    return admin.client.post("/api/v1/admin/grants", body=body, csrf=admin.csrf)


def grant_read_or_fail(
    admin: Session,
    principal_id: str,
    application_id: str,
    *,
    expires_at: str | None = None,
) -> str:
    response = grant_read(admin, principal_id, application_id, expires_at=expires_at)
    assert response.status == 201, response.text
    return response.json["id"]


def revoke_grant(admin: Session, grant_id: str):
    return admin.client.request(
        "DELETE", f"/api/v1/admin/grants/{grant_id}", csrf=admin.csrf
    )


def set_roles(admin: Session, principal_id: str, roles: list[str]):
    return admin.client.request(
        "PUT",
        f"/api/v1/admin/users/{principal_id}/roles",
        body={"roles": roles},
        csrf=admin.csrf,
    )


def list_applications(
    session: Session,
    *,
    cursor: str | None = None,
    limit: int | str | None = None,
) -> object:
    parts = []
    if cursor is not None:
        parts.append("cursor=" + urllib.parse.quote(cursor, safe=""))
    if limit is not None:
        parts.append("limit=" + urllib.parse.quote(str(limit), safe=""))
    path = "/api/v1/applications"
    if parts:
        path += "?" + "&".join(parts)
    return session.client.get(path)


def get_application(session: Session, application_id: str):
    return session.client.get(f"/api/v1/applications/{application_id}")


def application_ids(response) -> list[str]:
    payload = response.json
    return [item["id"] for item in payload["items"]]


def epoch_revision(dbname: str) -> int:
    return int(sql(dbname, "SELECT revision FROM platform.policy_epochs WHERE scope = 'acl'"))


def audit_events(dbname: str, action: str | None = None) -> list[dict]:
    where = f"WHERE action = {_literal(action)}" if action else ""
    return sql_rows(
        dbname,
        "SELECT id::text AS id, actor_id::text AS actor_id, action, "
        "resource_type, resource_id::text AS resource_id, metadata::text AS metadata, "
        "created_at::text AS created_at FROM platform.audit_events "
        f"{where} ORDER BY created_at, action",
    )


def disable_principal(dbname: str, principal_id: str) -> None:
    sql(
        dbname,
        "UPDATE platform.principals SET disabled_at = now() "
        f"WHERE id = {_literal(principal_id)}::uuid",
    )


def viewer_with_cookie(base_url: str, raw_cookie: str) -> CookieOnlyClient:
    """A cookie-only client for another API process on the same database."""
    return CookieOnlyClient(base_url, raw_cookie)


# The advisory lock key used by identity.SetRoles, derived from the same ASCII
# bytes ("ROLESET1") so the test can hold the lock while two real HTTP requests
# authenticate, then release it and let both queue for the role transaction.
ROLES_LOCK_KEY = int.from_bytes(b"ROLESET1", "big")


def hold_roles_lock(database: str, seconds: float = 3.0) -> subprocess.Popen:
    """Hold the role-update advisory lock from a real psql session, then sleep.

    The point is determinism: while the lock is held, two concurrent HTTP
    demotions both pass authentication and block inside SetRoles, so when the
    lock is released they are guaranteed to queue on the last-admin check instead
    of racing the authentication step.
    """
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    return subprocess.Popen(
        [
            pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            pg_dev.DEFAULT_USER,
            "-d",
            database,
            "-tA",
            "-c",
            f"SELECT pg_advisory_lock({ROLES_LOCK_KEY})",
            "-c",
            f"SELECT pg_sleep({float(seconds)})",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def wait_for_advisory_lock(database: str, timeout: float = 15.0) -> None:
    """Wait until a session-level advisory lock is visible in pg_locks."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        held = int(
            sql(database, "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted")
        )
        if held > 0:
            return
        time.sleep(0.05)
    raise AssertionError("the test could not acquire an advisory lock to synchronise on")


def enabled_human_admins(dbname: str) -> int:
    return int(
        sql(
            dbname,
            "SELECT count(*) FROM platform.principals AS p "
            "JOIN platform.users AS u ON u.principal_id = p.id "
            "JOIN platform.role_bindings AS r ON r.principal_id = p.id AND r.role = 'admin' "
            "WHERE p.kind = 'user' AND p.disabled_at IS NULL",
        )
    )


def role_bindings(dbname: str, principal_id: str) -> list[str]:
    return [
        row["role"]
        for row in sql_rows(
            dbname,
            "SELECT role FROM platform.role_bindings "
            f"WHERE principal_id = {_literal(principal_id)}::uuid ORDER BY role",
        )
    ]


def seed_service_principal(
    dbname: str, responsible_user_id: str, display_name: str = "svc-account"
) -> str:
    """Create a service principal (T020 owns the endpoint; T019 needs a row).

    ``principals_responsible_matches_kind`` requires the responsible user to be a
    local user, so the caller must pass a human principal id that has a users row.
    """
    principal_id = new_uuid()
    sql(
        dbname,
        "INSERT INTO platform.principals "
        "(id, kind, display_name, responsible_user_id) VALUES "
        f"({_literal(principal_id)}::uuid, 'service', {_literal(display_name)}, "
        f"{_literal(responsible_user_id)}::uuid)",
    )
    return principal_id
