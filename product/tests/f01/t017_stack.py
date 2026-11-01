"""Shared real-stack fixture for the T017 (login / refresh / logout / CSRF) tests.

The seams are the same as T015's (``product/tests/f01/conftest.py``): a throwaway
PostgreSQL database created with ``CREATE DATABASE``, the compiled ``cmd/admin``
and ``cmd/api`` binaries, and real HTTP over a socket. This module only removes
the repetition of building that stack and signing in; nothing here is mocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

import pytest

from conftest import (  # noqa: F401 - the fixtures are re-exported for test modules
    SESSION_COOKIE,
    ApiServer,
    HttpClient,
    Response,
    api_server,
    bootstrap_or_fail,
    fresh_database,
    go_binaries,
    migrate_or_fail,
    sql,
)

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "t017-admin-password"
USER_PASSWORD = "t017-user-password"


@dataclass
class Stack:
    """One migrated + bootstrapped database behind one running API process."""

    server: ApiServer
    dsn: str
    dbname: str

    def client(self) -> HttpClient:
        return HttpClient(self.server.base_url)

    def count(self, table: str) -> int:
        return int(sql(self.dbname, f"SELECT count(*) FROM {table}"))


class Session(NamedTuple):
    """A signed-in browser: its cookie jar, its CSRF token and its principal."""

    client: HttpClient
    csrf: str
    principal_id: str
    cookie: str


@pytest.fixture
def stack(fresh_database, go_binaries, api_server) -> Stack:
    dsn, dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    bootstrap_or_fail(go_binaries["admin"], dsn, ADMIN_PASSWORD)
    return Stack(server=api_server(dsn), dsn=dsn, dbname=dbname)


def sign_in(stack: Stack, login: str, password: str, *, client: HttpClient | None = None):
    """POST /auth/login and return the client plus the raw response."""
    client = client or stack.client()
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    return client, response


def session_for(stack: Stack, login: str, password: str) -> Session:
    client, response = sign_in(stack, login, password)
    assert response.status == 200, response.text
    body = response.json
    assert body["csrf_token"], body
    cookie = client.cookie(SESSION_COOKIE)
    assert cookie, "login did not set the session cookie"
    return Session(client, body["csrf_token"], body["principal_id"], cookie)


def admin_session(stack: Stack) -> Session:
    return session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)


def invite(
    stack: Stack,
    admin: Session,
    login: str,
    *,
    roles: tuple[str, ...] = ("user",),
    display_name: str | None = None,
) -> Response:
    return admin.client.post(
        "/api/v1/admin/users",
        body={
            "login": login,
            "display_name": display_name or login.replace(".", " ").title(),
            "roles": list(roles),
        },
        csrf=admin.csrf,
    )


def invite_and_activate(
    stack: Stack,
    admin: Session,
    login: str,
    *,
    roles: tuple[str, ...] = ("user",),
    password: str = USER_PASSWORD,
    display_name: str | None = None,
) -> Session:
    """Invite, activate and sign in a new user through the real endpoints."""
    invitation = invite(stack, admin, login, roles=roles, display_name=display_name)
    assert invitation.status == 201, invitation.text
    activation = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": invitation.json["activation_token"], "password": password},
    )
    assert activation.status == 200, activation.text
    return session_for(stack, login, password)


def invite_body(login: str, roles: tuple[str, ...] = ("user",)) -> dict:
    """The request body POST /admin/users expects (used by negative tests too)."""
    return {
        "login": login,
        "display_name": login.replace(".", " ").title(),
        "roles": list(roles),
    }


class CookieOnlyClient(HttpClient):
    """A brand-new client that carries nothing but one session cookie.

    This is the page-refresh case in HTTP terms: no login response in memory, no
    CSRF token remembered anywhere, no jar entry — only the cookie the browser
    kept. It proves ``GET /auth/me`` can restore both the identity and the CSRF
    token from the presented cookie alone.
    """

    def __init__(self, base_url: str, raw_cookie: str) -> None:
        super().__init__(base_url)
        self.raw_cookie = raw_cookie

    def request(self, method: str, path: str, **kwargs):  # noqa: ANN001, ANN003
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Cookie", f"{SESSION_COOKIE}={self.raw_cookie}")
        return super().request(method, path, headers=headers, **kwargs)


def client_with_cookie(stack: Stack, raw_cookie: str) -> CookieOnlyClient:
    return CookieOnlyClient(stack.server.base_url, raw_cookie)
