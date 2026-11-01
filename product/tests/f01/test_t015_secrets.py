"""T015 acceptance 6: passwords, tokens and cookies never reach logs or URLs.

The API's own stdout/stderr is captured to a file for the whole flow, including
one deliberately failed login, a request whose *header* carries a marker, and a
request whose query string carries a marker. The log must contain the request
line (method, path, status, duration, request id) and none of the secrets.
"""

from __future__ import annotations

import re
import uuid

import pytest

from conftest import (
    HttpClient,
    api_server,  # noqa: F401 - pytest fixture
    bootstrap_or_fail,
    fresh_database,  # noqa: F401 - pytest fixture
    go_binaries,  # noqa: F401 - pytest fixture
    migrate_or_fail,
)

ADMIN_PASSWORD = "t015-log-admin-password"
USER_PASSWORD = "t015-log-user-password"
WRONG_PASSWORD = "t015-log-wrong-password"


@pytest.fixture
def live_server(fresh_database, go_binaries, api_server):
    dsn, _dbname = fresh_database
    migrate_or_fail(go_binaries["admin"], dsn)
    bootstrap_or_fail(go_binaries["admin"], dsn, ADMIN_PASSWORD)
    return api_server(dsn)


def test_no_secret_reaches_the_api_log_or_a_request_url(live_server) -> None:
    base = live_server.base_url
    admin = HttpClient(base)
    admin_login = admin.post(
        "/api/v1/auth/login", body={"login": "admin", "password": ADMIN_PASSWORD}
    )
    assert admin_login.status == 200, admin_login.text
    admin_csrf = admin_login.json["csrf_token"]
    admin_cookie = admin.cookie()
    assert admin_cookie

    invitation = admin.post(
        "/api/v1/admin/users",
        csrf=admin_csrf,
        body={"login": "kate", "display_name": "Kate", "roles": ["user"]},
    )
    assert invitation.status == 201, invitation.text
    activation_token = invitation.json["activation_token"]

    activation_client = HttpClient(base)
    activation = activation_client.post(
        "/api/v1/auth/activate",
        body={"activation_token": activation_token, "password": USER_PASSWORD},
    )
    assert activation.status == 200, activation.text

    user = HttpClient(base)
    user_login = user.post(
        "/api/v1/auth/login", body={"login": "kate", "password": USER_PASSWORD}
    )
    assert user_login.status == 200, user_login.text
    user_csrf = user_login.json["csrf_token"]
    user_cookie = user.cookie()
    assert user_cookie

    failed_login = HttpClient(base)
    rejected = failed_login.post(
        "/api/v1/auth/login", body={"login": "admin", "password": WRONG_PASSWORD}
    )
    assert rejected.status == 401, rejected.text

    # Markers that must never be logged: one in a header, one in a query string.
    header_marker = "t015-header-marker-" + uuid.uuid4().hex
    query_marker = "t015-query-marker-" + uuid.uuid4().hex
    trace_id = "t015-trace-" + uuid.uuid4().hex[:12]
    status_client = HttpClient(base)
    traced = status_client.post(
        "/api/v1/auth/login",
        body={"login": "admin", "password": WRONG_PASSWORD},
        headers={"Authorization": "Bearer " + header_marker, "X-Request-ID": trace_id},
    )
    assert traced.status == 401
    assert traced.header("X-Request-ID") == trace_id

    query_response = status_client.get(f"/api/status?probe={query_marker}")
    assert query_response.status == 200
    assert query_marker not in query_response.text

    log = live_server.wait_for_log(f"request_id={trace_id}")

    secrets = {
        "admin password": ADMIN_PASSWORD,
        "user password": USER_PASSWORD,
        "wrong password": WRONG_PASSWORD,
        "activation token": activation_token,
        "admin session cookie": admin_cookie,
        "user session cookie": user_cookie,
        "admin csrf token": admin_csrf,
        "user csrf token": user_csrf,
        "header marker": header_marker,
        "query marker": query_marker,
    }
    for label, secret in secrets.items():
        assert secret not in log, f"the API log leaked the {label}"

    # The log does carry the observability fields the design fixes.
    for expected in (
        "POST /api/v1/auth/login 200",
        "POST /api/v1/admin/users 201",
        "POST /api/v1/auth/activate 200",
        "GET /api/status 200",
        f"request_id={trace_id}",
    ):
        assert expected in log, f"the log is missing {expected!r}:\n{log[-3000:]}"
    assert re.search(r"POST /api/v1/auth/login 200 \d+(?:\.\d+)?(?:µs|ms|s) request_id=", log)
    # Method, path, status, duration, request id: nothing else per request line.
    request_line = re.compile(
        r"^orchara-api \d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2} "
        r"(?:GET|POST|PUT|PATCH|DELETE) /\S* \d{3} \S+ request_id=[A-Za-z0-9_.:-]+$"
    )
    for line in log.splitlines():
        if " request_id=" in line:
            assert request_line.match(line), line

    # No request the flow made carried a secret in its URL, and none had a query.
    auth_clients = [admin, activation_client, user, failed_login]
    for client in auth_clients:
        for method, path in client.requests:
            for label, secret in secrets.items():
                assert secret not in path, f"{method} {path} leaked the {label}"
            assert "?" not in path, f"{method} {path} carried a query string"


def test_the_log_file_does_not_contain_the_database_password(live_server) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
    import pg_dev  # noqa: PLC0415 - matches the rest of the suite

    assert pg_dev.load_secret() not in live_server.logs()
