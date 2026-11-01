"""Shared real-stack helpers for the T021 (disable/enable and handover) tests.

The seams are the T017/T019/T020 ones: a throwaway PostgreSQL database created
with ``CREATE DATABASE``, the compiled ``cmd/admin`` / ``cmd/api`` binaries and
real HTTP over a socket. Nothing here is mocked.

The new surface is:

* ``PATCH /api/v1/admin/principals/{principal_id}`` with ``{disabled, reason}``
  (``set_disabled``);
* ``cmd/admin handover --from --to --by`` (``run_handover``), always with the DSN
  in the environment, never on argv.

Crafted states (a second session, a revoked key row) are produced through the
real endpoints or psql on the real tables; no fixture writes a secret into a
report or a URL.
"""

from __future__ import annotations

from conftest import Response, run_admin, sql_rows
from t020_stack import (  # noqa: F401 - fixtures and helpers are re-exported
    ADMIN_LOGIN,
    ADMIN_PASSWORD,
    SCOPE_APPLICATION_READ,
    USER_PASSWORD,
    Session,
    Stack,
    admin_session,
    api_keys,
    api_server,
    application_ids,
    audit_events,
    audit_metadata,
    bearer_client,
    bootstrap_or_fail,
    client_with_cookie,
    create_api_key,
    create_api_key_or_fail,
    create_service_account,
    create_service_account_or_fail,
    disable_principal,
    enabled_human_admins,
    epoch_revision,
    fresh_database,
    future_rfc3339,
    get_application,
    go_binaries,
    grant_read,
    grant_read_or_fail,
    invite,
    invite_and_activate,
    invite_body,
    list_applications,
    migrate_or_fail,
    new_uuid,
    past_rfc3339,
    revoke_api_key,
    role_bindings,
    seed_api_key_row,
    seed_application,
    seed_service_principal,
    session_for,
    set_roles,
    sign_in,
    stack,
)
from t019_stack import hold_roles_lock, wait_for_advisory_lock  # noqa: F401 - re-exported

SAFE_REASON = "离职交接"


def _literal(value: str) -> str:
    """Quote a test-controlled string as a SQL literal (no secrets here)."""
    return "'" + value.replace("'", "''") + "'"


# ---------------------------------------------------------------------------
# PATCH /api/v1/admin/principals/{principal_id}
# ---------------------------------------------------------------------------
_UNSET = object()


def set_disabled(
    admin: Session,
    principal_id: str,
    *,
    disabled: bool,
    reason: str = SAFE_REASON,
    csrf: object = _UNSET,
):
    """Send the real PATCH request. ``csrf=None`` sends no CSRF header at all."""
    kwargs: dict = {}
    if csrf is _UNSET:
        kwargs["csrf"] = admin.csrf
    elif csrf is not None:
        kwargs["csrf"] = csrf
    return admin.client.request(
        "PATCH",
        f"/api/v1/admin/principals/{principal_id}",
        body={"disabled": disabled, "reason": reason},
        **kwargs,
    )


def set_disabled_or_fail(
    admin: Session,
    principal_id: str,
    *,
    disabled: bool,
    reason: str = SAFE_REASON,
) -> Response:
    response = set_disabled(admin, principal_id, disabled=disabled, reason=reason)
    assert response.status == 200, response.text
    assert response.json == {
        "id": principal_id,
        "state": "disabled" if disabled else "enabled",
    }, response.text
    return response


# ---------------------------------------------------------------------------
# Database assertions (real tables)
# ---------------------------------------------------------------------------
def principal_row(dbname: str, principal_id: str) -> dict | None:
    rows = sql_rows(
        dbname,
        "SELECT id::text AS id, kind, display_name, disabled_at::text AS disabled_at, "
        "responsible_user_id::text AS responsible_user_id "
        f"FROM platform.principals WHERE id = {_literal(principal_id)}::uuid",
    )
    return rows[0] if rows else None


def sessions_for(dbname: str, principal_id: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT token_hash, principal_id::text AS principal_id, "
        "revoked_at::text AS revoked_at, expires_at::text AS expires_at "
        "FROM platform.sessions "
        f"WHERE principal_id = {_literal(principal_id)}::uuid ORDER BY expires_at, token_hash",
    )


def service_accounts_of(dbname: str, responsible_user_id: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT id::text AS id, display_name FROM platform.principals "
        "WHERE kind = 'service' "
        f"AND responsible_user_id = {_literal(responsible_user_id)}::uuid "
        "ORDER BY display_name, id",
    )


def resource_grants(dbname: str) -> list[dict]:
    return sql_rows(
        dbname,
        "SELECT id::text AS id, principal_id::text AS principal_id, resource_type, "
        "resource_id::text AS resource_id, action, expires_at::text AS expires_at "
        "FROM platform.resource_grants ORDER BY id",
    )


def authorization_snapshot(dbname: str) -> dict:
    """Roles and grants, byte-identical across a refused or harmless operation."""
    return {
        "role_bindings": sql_rows(
            dbname,
            "SELECT principal_id::text AS principal_id, role FROM platform.role_bindings "
            "ORDER BY principal_id, role",
        ),
        "resource_grants": resource_grants(dbname),
        "epoch": epoch_revision(dbname),
    }


# ---------------------------------------------------------------------------
# cmd/admin handover (DSN only in the environment)
# ---------------------------------------------------------------------------
def run_handover(
    admin_binary,
    dsn: str,
    *,
    from_id: str,
    to_id: str,
    by_id: str,
):
    return run_admin(
        admin_binary,
        ["handover", "--from", from_id, "--to", to_id, "--by", by_id],
        dsn=dsn,
    )


def handover_events(dbname: str) -> list[dict]:
    return audit_events(dbname, "service_account.handover")


def stored_secrets(dbname: str) -> list[str]:
    """Every hash-shaped value that must never appear in an audit row or log.

    Reads the real session token hashes, API key hashes and the user's password
    hash. Tests assert these strings are absent from audit metadata and process
    logs.
    """
    rows = sql_rows(
        dbname,
        "SELECT 'session:' || token_hash AS value FROM platform.sessions "
        "UNION ALL SELECT 'key:' || key_hash FROM platform.api_keys "
        "UNION ALL SELECT 'password:' || password_hash FROM platform.users",
    )
    return [row["value"] for row in rows]


def audit_rows(dbname: str) -> list[dict]:
    return audit_events(dbname)
