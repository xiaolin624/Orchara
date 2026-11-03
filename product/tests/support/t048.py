"""F06/T048 real-stack helpers: fixed HTTP connectors and controlled egress.

The stack is the F06/T041 one (throwaway PostgreSQL migrated through 0029, the
compiled ``cmd/api``, real mTLS capability endpoint, real object store, real
golden manifest). The *connector endpoint* is a real local HTTP server on
127.0.0.1 (the brief explicitly allows loopback for tests through
``allow_private=true``); nothing about the egress guard is mocked -- the HTTP
request is made by Go's client and observed by the Python server.

The tool->connector binding is a real database fact: ``platform.tool_versions
.connector_versions`` (migration 0012) is updated before the debug run compiles
its manifest, exactly the column the production closure reads.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from support import f00b, t025, t041
from support import stack as st

GOLDEN_TOOL_ID = t025.GOLDEN_TOOL_ID

# The test credential value. It exists only in this process's environment and
# in the test's assertion; it is never written to the repository or a fixture.
TEST_SECRET_ENV = "T048_TEST_SECRET"


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        record = {
            "method": self.command,
            "path": self.path,
            "headers": {name.lower(): value for name, value in self.headers.items()},
            "body": body.decode("utf-8", "replace"),
        }
        self.server.records.append(record)
        status, headers, payload = self.server.responder(record)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_PATCH = _handle
    do_DELETE = _handle

    def log_message(self, *_args) -> None:  # keep pytest output clean
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True


class LocalConnectorServer:
    """A real loopback HTTP server whose answer each test controls."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self.responder = lambda _record: (
            200,
            {"Content-Type": "application/json", "X-Request-Id": "t048-local"},
            json.dumps({"ok": True, "value": 42}).encode(),
        )
        self._httpd = _Server(("127.0.0.1", 0), _Handler)
        self._httpd.records = self.records
        self._httpd.responder = lambda record: self.responder(record)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    def respond_json(self, payload: dict | list, *, status: int = 200) -> None:
        self.responder = lambda _record: (
            status,
            {"Content-Type": "application/json"},
            json.dumps(payload).encode(),
        )

    def respond_redirect(self, location: str, *, status: int = 302) -> None:
        self.responder = lambda _record: (status, {"Location": location}, b"")

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


# ---------------------------------------------------------------------------
# admin registry calls
# ---------------------------------------------------------------------------
def registration_body(
    *,
    origin: str,
    method: str = "GET",
    path_prefix: str = "/v1",
    request_schema: dict | None = None,
    response_schema: dict | None = None,
    risk_class: str = "read",
    allow_private: bool = True,
    secret_ref: str = f"env:{TEST_SECRET_ENV}",
    secret_header: str = "X-Connector-Secret",
) -> dict:
    return {
        "origin": origin,
        "method": method,
        "path_prefix": path_prefix,
        # The schema describes the whole invocation params object ({path, query,
        # body}); additionalProperties=true keeps path/body legal while a query
        # member is only allowed when the administrator declares it.
        "request_schema": (
            {"type": "object", "additionalProperties": True}
            if request_schema is None
            else request_schema
        ),
        "response_schema": (
            {
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"type": "boolean"}},
            }
            if response_schema is None
            else response_schema
        ),
        "risk_class": risk_class,
        "allow_private": allow_private,
        "secret_ref": secret_ref,
        "secret_header": secret_header,
    }


def register(stack, admin: f00b.Session, **overrides) -> dict:
    body = registration_body(**overrides)
    response = admin.client.post("/api/v1/admin/connectors", body=body, csrf=admin.csrf)
    assert response.status == 201, response.text
    return response.json


def register_raw(stack, admin: f00b.Session, body: dict) -> st.Response:
    return admin.client.post("/api/v1/admin/connectors", body=body, csrf=admin.csrf)


def list_policies(admin: f00b.Session) -> st.Response:
    return admin.client.get("/api/v1/admin/connectors")


def revoke(admin: f00b.Session, policy_id: str) -> st.Response:
    return admin.client.post(f"/api/v1/admin/connectors/{policy_id}/revoke", body={}, csrf=admin.csrf)


# ---------------------------------------------------------------------------
# binding + invocation
# ---------------------------------------------------------------------------
def bind_tool_connectors(dbname: str, policy_ids: list[str], *, tool_id: str = GOLDEN_TOOL_ID) -> None:
    """Pin the tool to the connector policies; the manifest closure reads this."""
    for policy_id in policy_ids:
        assert uuid.UUID(policy_id)
    st.sql(
        dbname,
        "UPDATE platform.tool_versions SET connector_versions = "
        f"{st.quote_literal(json.dumps(policy_ids))}::jsonb "
        f"WHERE id = {st.quote_literal(tool_id)}::uuid",
    )


def invoke(
    stack,
    lease: dict,
    *,
    policy_id: str,
    params: dict,
    logical_key: str,
    operation_id: str | None = None,
    attempt_token: str | None = None,
) -> st.Response:
    return t041.invoke(
        t041.worker_client(stack),
        lease,
        operation_id=operation_id or str(uuid.uuid4()),
        logical_key=logical_key,
        capability_version=policy_id,
        capability_kind="connector",
        params=params,
        attempt_token=attempt_token,
    )


def manifest_connector_policies(lease: dict) -> list[dict]:
    return lease["manifest"]["connector_policies"]


# ---------------------------------------------------------------------------
# database / artifact reads
# ---------------------------------------------------------------------------
def policy_row(dbname: str, policy_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text, version, origin, method, path_prefix, request_schema::text, "
        "response_schema::text, secret_ref, secret_header, risk_class, allow_private, "
        "policy_artifact_id::text, policy_sha256, created_by::text, "
        "approved_at::text, revoked_at::text "
        "FROM platform.connector_policies "
        f"WHERE id = {st.quote_literal(policy_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def policy_artifact(stack, policy: dict) -> dict:
    return t041.artifact_row(stack.dbname, policy["policy_artifact_id"])


def params_artifact_bytes(stack, operation: dict) -> bytes:
    artifact = t041.artifact_row(stack.dbname, operation["params_artifact_id"])
    return (stack.object_dir / artifact["object_key"]).read_bytes()


def receipt(stack, receipt_artifact_id: str) -> tuple[dict, bytes]:
    artifact = t041.artifact_row(stack.dbname, receipt_artifact_id)
    payload = (stack.object_dir / artifact["object_key"]).read_bytes()
    return json.loads(payload), payload
