"""F12/T093 external-client API conformance gate.

This is the replayable entry point the T093 acceptance criterion asks for: every
operation in ``contracts/openapi.json`` is probed by a *real HTTP client* against
a *running* API process (never an in-process handler), and the run produces one
machine-readable record per operation plus a verdict.

The gate is deliberately a judge, not a feature:

* it reads the operation list from the contract (no hand-written 106-line list);
* it refuses a contract whose operation count differs from the expected 106, so
  a silently dropped operation cannot turn the gate green;
* it probes every operation for three things - the unauthenticated boundary, the
  wrong-role / cross-principal boundary, and (where a resource can be built
  through the public API) the normal path;
* it checks the cross-endpoint invariants API.md fixes: the error envelope,
  pagination, ETag/If-Match, Idempotency-Key, SSE frames, UTC/UUID/Decimal and
  the absence of a generic CRUD fallback;
* it exits non-zero when any operation is unimplemented or any invariant fails.

Usage (from ``product/``):

    python tools/f12_api_gate.py \
        --base-url http://127.0.0.1:PORT \
        --admin-login admin --admin-password <generated> \
        --internal-url https://127.0.0.1:PORT \
        --internal-ca <tmp>/certs/ca/orchara-internal-test-ca.pem \
        --internal-cert-dir <tmp>/certs/clients \
        --out reports/f12/T093-api-conformance.json

The gate mutates the target: it invites a creator and a member, creates a
throwaway application/draft/run/conversation/memory/knowledge base/artifact and
deletes some of them again. Run it against a disposable test stack, never a
production deployment.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import http.cookiejar
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from evidence_redaction import redact_value

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
# The F12 baseline: contracts/openapi.json lists 93 paths / 106 operations. A
# gate that silently accepts 105 would be a false-green, so the number is fixed.
DEFAULT_EXPECTED_OPERATIONS = 106

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
# RFC3339 with an explicit UTC designator/offset. Both time.RFC3339 and
# time.RFC3339Nano are contract-legal, so the gate parses instead of comparing
# strings (the product mixes the two precisions).
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"
)
DECIMAL_RE = re.compile(r"^-?\d+(?:\.\d+)?$")
MONEY_KEYS = frozenset(
    {
        "charged",
        "reserved",
        "cost",
        "amount",
        "total",
        "budget_limit",
        "input_per_million",
        "output_per_million",
        "price_per_million",
        "unit_price",
    }
)
TIME_KEYS = frozenset(
    {
        "created_at",
        "updated_at",
        "expires_at",
        "activated_at",
        "decided_at",
        "lease_deadline",
        "started_at",
        "finished_at",
        "deleted_at",
        "heartbeat_at",
        "published_at",
        "submitted_at",
        "revoked_at",
        "due_at",
        "not_before",
        "not_after",
    }
)
ID_KEYS = frozenset(
    {
        "id",
        "application_id",
        "run_id",
        "parent_run_id",
        "child_run_id",
        "artifact_id",
        "output_artifact_id",
        "input_artifact_id",
        "source_artifact_id",
        "result_artifact_id",
        "dataset_artifact_id",
        "evidence_artifact_id",
        "proposal_artifact_id",
        "body_artifact_id",
        "conversation_id",
        "request_id",
        "interaction_id",
        "memory_id",
        "candidate_id",
        "knowledge_base_id",
        "document_id",
        "chunk_id",
        "model_version_id",
        "model_profile_version_id",
        "version_id",
        "application_version_id",
        "candidate_version_id",
        "review_id",
        "approval_id",
        "deletion_id",
        "grant_id",
        "grants_id",
        "job_id",
        "operation_id",
        "execution_snapshot_id",
        "attempt_id",
        "principal_id",
        "grantee_id",
        "responsible_user_id",
        "connector_version_id",
        "evaluation_id",
        "report_id",
        "key_id",
        "service_account_id",
        "event_id",
        "manifest_id",
    }
)

# Epics are a report grouping, not part of the contract. The mapping is derived
# from the owner documented in PROGRESS.md / the route comments, with explicit
# exceptions for paths that several epics contributed to.
EPIC_RULES: tuple[tuple[str, str], ...] = (
    ("/health/", "F00/D00"),
    ("/api/v1/auth/", "F01"),
    ("/api/v1/admin/users", "F01"),
    ("/api/v1/admin/principals", "F01"),
    ("/api/v1/admin/grants", "F01"),
    ("/api/v1/admin/service-accounts", "F01"),
    ("/api/v1/admin/api-keys", "F01"),
    ("/api/v1/admin/credential-refs", "F06"),
    ("/api/v1/admin/model-endpoints", "F06"),
    ("/api/v1/applications/{application_id}/submit", "F02B"),
    ("/api/v1/applications/{application_id}/evaluations", "F13A"),
    ("/api/v1/applications", "F02A"),
    ("/api/v1/reviews", "F02B"),
    ("/api/v1/versions", "F02B"),
    ("/api/v1/conversations", "F08"),
    ("/api/v1/requests", "F08"),
    ("/api/v1/runs", "F03"),
    ("/api/v1/artifacts", "F05"),
    ("/api/v1/tools", "F05"),
    ("/api/v1/models", "F06"),
    ("/api/v1/approvals", "F06"),
    ("/api/v1/operations", "F06"),
    ("/api/v1/memories", "F09"),
    ("/api/v1/memory-candidates", "F09"),
    ("/api/v1/knowledge-bases", "F10"),
    ("/api/v1/knowledge-documents", "F10"),
    ("/api/v1/deletions", "F11"),
    ("/api/v1/diagnostic-grants", "F11"),
    ("/api/v1/evaluations", "F13A"),
    ("/api/v1/evaluation-reports", "F13A"),
    ("/api/v1/background-jobs", "F00B"),
    ("/internal/v1/background/", "F00B"),
    ("/internal/v1/jobs", "F03"),
    ("/internal/v1/attempts", "F03"),
    ("/internal/v1/runs/{run_id}/heartbeat", "F03"),
    ("/internal/v1/runs/{run_id}/events", "F03"),
    ("/internal/v1/runs/{run_id}/result", "F03"),
    ("/internal/v1/runs/{run_id}/capabilities", "F06"),
    ("/internal/v1/runs/{run_id}/model-stream", "F06"),
    ("/internal/v1/runs/{run_id}/approvals", "F06"),
    ("/internal/v1/runs/{run_id}/children", "F07"),
    ("/internal/v1/runs/{run_id}/memory-candidates", "F09"),
    ("/internal/v1/runs/{run_id}/artifact", "F05"),
)


class ManifestError(RuntimeError):
    """The contract itself is not the one this gate was built to judge."""


class GateTransportError(RuntimeError):
    """A request never produced an HTTP response (connection/TLS failure)."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_uuid() -> str:
    return str(uuid.uuid4())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# contract -> operation list
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Operation:
    operation_id: str
    method: str
    path: str
    security: str  # "public" | "session" | "session_only" | "internal"
    internal_role: str | None
    path_params: tuple[str, ...]
    header_params: tuple[str, ...]
    success_statuses: tuple[int, ...]
    declared_statuses: tuple[int, ...]
    paginated: bool
    # Object properties named ``id`` that the operation's success schema declares
    # as plain strings rather than UUIDs (the deployer-keyed configuration
    # catalogs). Empty for every platform resource.
    non_uuid_id_fields: tuple[str, ...] = ()

    @property
    def is_internal(self) -> bool:
        return self.security == "internal"


def _security_kind(operation: dict) -> str:
    security = operation.get("security")
    if security == []:
        return "public"
    schemes: set[str] = set()
    for requirement in security or []:
        schemes.update(requirement)
    if schemes == {"workerMTLS"}:
        return "internal"
    if schemes == {"sessionCookie"}:
        return "session_only"
    return "session"


def _statuses(operation: dict) -> tuple[int, ...]:
    statuses: list[int] = []
    for raw in operation.get("responses", {}):
        try:
            statuses.append(int(raw))
        except ValueError:
            continue
    return tuple(sorted(statuses))


def _plain_string_id_fields(operation: dict) -> tuple[str, ...]:
    """``id`` properties the success schema types as a plain string, not a UUID.

    The UUID invariant judges the contract as written. Two deployer-keyed
    configuration catalogs (credential references, model endpoints) declare
    ``id: {type: string}``; every platform resource declares/expects a UUID. The
    whitelist is derived from the contract, never from the observed values.
    """
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                return
            if node.get("type") == "object":
                for name, prop in (node.get("properties") or {}).items():
                    if (
                        name == "id"
                        and isinstance(prop, dict)
                        and prop.get("type") == "string"
                        and prop.get("format") != "uuid"
                    ):
                        found.add(name)
                    walk(prop)
            elif node.get("type") == "array":
                walk(node.get("items"))

    for status, response in operation.get("responses", {}).items():
        try:
            code = int(status)
        except ValueError:
            continue
        if not 200 <= code < 300:
            continue
        walk(response.get("content", {}).get("application/json", {}).get("schema", {}))
    return tuple(sorted(found))


def load_operations(
    contract_path: Path, expected_count: int = DEFAULT_EXPECTED_OPERATIONS
) -> list[Operation]:
    """Read the authoritative operation list from the OpenAPI document.

    Raises ManifestError on a duplicate operationId or a count mismatch: those
    are the two ways a manifest mutation could otherwise hide a missing endpoint.
    """
    try:
        document = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - caller error
        raise ManifestError(f"cannot read the contract at {contract_path}: {exc}") from exc

    operations: list[Operation] = []
    seen: set[str] = set()
    for path, item in document.get("paths", {}).items():
        for method in HTTP_METHODS:
            operation = item.get(method)
            if operation is None:
                continue
            operation_id = operation.get("operationId")
            if not operation_id:
                raise ManifestError(f"{method.upper()} {path} has no operationId")
            if operation_id in seen:
                raise ManifestError(f"duplicate operationId {operation_id!r}")
            seen.add(operation_id)
            parameters = operation.get("parameters", [])
            path_params = tuple(
                parameter["name"] for parameter in parameters if parameter.get("in") == "path"
            )
            header_params = tuple(
                parameter["name"] for parameter in parameters if parameter.get("in") == "header"
            )
            statuses = _statuses(operation)
            operations.append(
                Operation(
                    operation_id=operation_id,
                    method=method.upper(),
                    path=path,
                    security=_security_kind(operation),
                    internal_role=operation.get("x-internal-role"),
                    path_params=path_params,
                    header_params=header_params,
                    success_statuses=tuple(status for status in statuses if 200 <= status < 300),
                    declared_statuses=statuses,
                    paginated=any(
                        parameter.get("name") in {"cursor", "limit"}
                        for parameter in parameters
                        if parameter.get("in") == "query"
                    ),
                    non_uuid_id_fields=_plain_string_id_fields(operation),
                )
            )

    if expected_count is not None and len(operations) != expected_count:
        raise ManifestError(
            f"the contract lists {len(operations)} operations, the F12 baseline is "
            f"{expected_count}: a dropped or added operation is a manifest change, not a pass"
        )
    if not operations:
        raise ManifestError("the contract lists no operations")
    operations.sort(key=lambda operation: operation.operation_id)
    return operations


def epic_for(operation: Operation) -> str:
    for prefix, epic in EPIC_RULES:
        if operation.path == prefix or operation.path.startswith(prefix.rstrip("/") + "/"):
            return epic
    if operation.path.startswith("/internal/v1/runs/{run_id}/"):
        return "F03/F04A"
    return "F12-unmapped"


# ---------------------------------------------------------------------------
# HTTP clients (real sockets; never the in-process handler)
# ---------------------------------------------------------------------------
class Response:
    def __init__(self, status: int, headers: Any, raw: bytes, elapsed_ms: float) -> None:
        self.status = status
        self.headers = headers
        self.raw = raw
        self.elapsed_ms = elapsed_ms

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="replace")

    @property
    def content_type(self) -> str:
        return (self.headers.get("Content-Type") or "") if self.headers else ""

    @property
    def json(self) -> Any:
        if not self.text.strip():
            return None
        try:
            return json.loads(self.text)
        except json.JSONDecodeError:
            return None

    def header(self, name: str) -> str | None:
        return self.headers.get(name) if self.headers else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class HttpClient:
    """Cookie-aware HTTP client for the public API."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        csrf: str | None = None,
        timeout: float | None = None,
    ) -> Response:
        if not path.startswith("/"):
            path = "/" + path
        request_headers = dict(headers or {})
        data: bytes | None = None
        if raw_body is not None:
            data = raw_body
            request_headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if csrf is not None:
            request_headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=request_headers, method=method
        )
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=timeout or self.timeout) as response:
                return Response(
                    response.status,
                    response.headers,
                    response.read(),
                    (time.monotonic() - started) * 1000,
                )
        except urllib.error.HTTPError as exc:
            return Response(
                exc.code, exc.headers, exc.read(), (time.monotonic() - started) * 1000
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GateTransportError(f"{method} {path}: {exc}") from exc

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def cookie(self, name: str) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None


@dataclass(frozen=True)
class InternalOptions:
    url: str
    ca_path: Path
    cert_dir: Path

    def cert_paths(self, role: str) -> tuple[Path, Path]:
        return (
            self.cert_dir / f"client-{role}.pem",
            self.cert_dir / f"client-{role}-key.pem",
        )


class InternalClient:
    """A real mTLS client for the internal listener.

    ``role`` selects both the client certificate and the ``X-Internal-Role``
    header. ``header_role`` can override the header independently, which is how
    the "certificate says worker, header says maintenance" refusal is written.
    """

    def __init__(
        self,
        options: InternalOptions,
        role: str,
        *,
        with_certificate: bool = True,
        timeout: float = 30.0,
    ) -> None:
        parsed = urllib.parse.urlsplit(options.url)
        self.host = parsed.hostname or ""
        self.port = parsed.port or 443
        self.role = role
        self.timeout = timeout
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(options.ca_path))
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        if with_certificate:
            cert, key = options.cert_paths(role)
            context.load_cert_chain(certfile=str(cert), keyfile=str(key))
        self.context = context

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        header_role: str | None = None,
        omit_role_header: bool = False,
        timeout: float | None = None,
    ) -> Response:
        headers = {"Accept": "application/json"}
        if not omit_role_header:
            headers["X-Internal-Role"] = self.role if header_role is None else header_role
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        started = time.monotonic()
        try:
            connection = http.client.HTTPSConnection(
                self.host, self.port, context=self.context, timeout=timeout or self.timeout
            )
            try:
                connection.request(method, path, body=data, headers=headers)
                response = connection.getresponse()
                raw = response.read()
                return Response(
                    response.status,
                    response.headers,
                    raw,
                    (time.monotonic() - started) * 1000,
                )
            finally:
                connection.close()
        except (ssl.SSLError, OSError, http.client.HTTPException) as exc:
            raise GateTransportError(f"{method} {path}: {exc}") from exc


class Session:
    def __init__(self, client: HttpClient, csrf: str, principal_id: str, login: str) -> None:
        self.client = client
        self.csrf = csrf
        self.principal_id = principal_id
        self.login = login

    def cookie(self, name: str) -> str | None:
        return self.client.cookie(name)


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------
@dataclass
class GateOptions:
    base_url: str
    admin_login: str
    admin_password: str
    contract_path: Path
    internal: InternalOptions | None = None
    expected_operations: int = DEFAULT_EXPECTED_OPERATIONS
    profile: str = "dev-stub"
    product_root: Path | None = None
    timeout: float = 30.0


class Gate:
    def __init__(self, options: GateOptions) -> None:
        self.options = options
        self.base_url = options.base_url.rstrip("/")
        self.contract_path = Path(options.contract_path)
        self.operations = load_operations(self.contract_path, options.expected_operations)
        self.operations_by_id = {operation.operation_id: operation for operation in self.operations}
        self.idempotency_required = tuple(
            operation.operation_id
            for operation in self.operations
            if "Idempotency-Key" in operation.header_params
        )
        self.records: dict[str, dict] = {}
        # Filled by run() from the product's literal route registrations. It is
        # the authority for "does this operation exist at all"; the runtime probe
        # alone cannot tell an unmatched route from a handler's resource-level
        # 404, because both arrive as the same contract error envelope.
        self.route_registry: dict[str, Any] | None = None
        self.route_missing_ids: frozenset[str] = frozenset()
        self.fixtures: dict[str, Any] = {}
        self.fixture_notes: list[dict] = []
        self.invariants: dict[str, dict] = {}
        self.invariant_results: list[dict] = []
        self.anonymous = HttpClient(self.base_url, timeout=options.timeout)
        self.admin: Session | None = None
        self.creator: Session | None = None
        self.member: Session | None = None
        self.internal: dict[str, InternalClient] = {}
        self._all_responses: list[tuple[str, Response]] = []
        self._success_bodies: list[tuple[str, Any]] = []
        self._created_ids: dict[str, str] = {}
        self._run_ids_with_events: list[str] = []
        self.cross_principal_failures: list[str] = []
        self.errors: list[str] = []
        self.started_at = utc_now()
        self._request_count = 0

    # -- infrastructure -----------------------------------------------------
    def _record_response(self, label: str, response: Response) -> Response:
        self._request_count += 1
        self._all_responses.append((label, response))
        if 200 <= response.status < 300 and response.json is not None:
            self._success_bodies.append((label, response.json))
            self._scan_ids(label, response.json)
        return response

    def _scan_ids(self, label: str, payload: Any) -> None:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key == "id" and isinstance(value, str) and "creation:" in label:
                    self._created_ids[label] = value
                if key in {"id", "application_id", "run_id", "conversation_id",
                           "memory_id", "artifact_id", "knowledge_base_id",
                           "deletion_id", "grant_id", "job_id", "model_version_id"} \
                        and isinstance(value, str) and UUID_RE.match(value):
                    self.fixtures.setdefault(key, value)
                self._scan_ids(label, value)
        elif isinstance(payload, list):
            for value in payload:
                self._scan_ids(label, value)

    def _login(self, login: str, password: str) -> Session:
        client = HttpClient(self.base_url, timeout=self.options.timeout)
        response = self._record_response(
            f"setup:login:{login}", client.post("/api/v1/auth/login", body={"login": login, "password": password})
        )
        if response.status != 200:
            raise RuntimeError(f"login as {login} failed: {response.status} {response.text[:300]}")
        payload = response.json or {}
        if not payload.get("csrf_token"):
            raise RuntimeError(f"login as {login} returned no csrf_token")
        return Session(client, payload["csrf_token"], payload["principal_id"], login)

    def _invite(self, login: str, roles: list[str]) -> Session:
        assert self.admin is not None
        invitation = self._record_response(
            f"setup:invite:{login}",
            self.admin.client.post(
                "/api/v1/admin/users",
                body={"login": login, "display_name": login.replace("-", " ").title(), "roles": roles},
                csrf=self.admin.csrf,
            ),
        )
        if invitation.status != 201:
            raise RuntimeError(f"invite {login} failed: {invitation.status} {invitation.text[:300]}")
        token = (invitation.json or {}).get("activation_token")
        activation = self._record_response(
            f"setup:activate:{login}",
            HttpClient(self.base_url, timeout=self.options.timeout).post(
                "/api/v1/auth/activate",
                body={"activation_token": token, "password": self._password_for(login)},
            ),
        )
        if activation.status != 200:
            raise RuntimeError(f"activate {login} failed: {activation.status} {activation.text[:300]}")
        return self._login(login, self._password_for(login))

    def _password_for(self, login: str) -> str:
        return "f12-gate-" + hashlib.sha256((login + self.started_at).encode()).hexdigest()[:24]

    def _internal_client(self, role: str) -> InternalClient:
        if role not in self.internal:
            if self.options.internal is None:
                raise RuntimeError("internal listener options are required for /internal/v1 probes")
            self.internal[role] = InternalClient(
                self.options.internal, role, timeout=self.options.timeout
            )
        return self.internal[role]

    # -- fixtures -----------------------------------------------------------
    def _build_fixtures(self) -> None:
        self.admin = self._login(self.options.admin_login, self.options.admin_password)
        self.creator = self._invite("f12-creator-" + uuid.uuid4().hex[:8], ["creator"])
        self.member = self._invite("f12-member-" + uuid.uuid4().hex[:8], ["user"])

        self._fixture("application", self._create_application)
        self._fixture("draft", self._save_draft)
        self._fixture("run", self._create_debug_run)
        self._fixture("conversation", self._create_conversation)
        self._fixture("memory", self._create_memory)
        self._fixture("knowledge_base", self._create_knowledge_base)
        self._fixture("artifact", self._create_artifact)
        self._fixture("model", self._register_model)
        self._fixture("service_account", self._create_service_account)
        self._fixture("api_key", self._create_api_key)
        self._fixture("diagnostic_grant", self._create_diagnostic_grant)

    def _fixture(self, name: str, builder: Callable[[], Any]) -> None:
        try:
            builder()
        except Exception as exc:  # noqa: BLE001 - recorded, never hidden
            self.fixture_notes.append({"name": name, "built": False, "detail": str(exc)})
        else:
            self.fixture_notes.append({"name": name, "built": True, "detail": ""})

    def _creator_post(self, path: str, body: Any = None, **kwargs) -> Response:
        assert self.creator is not None
        response = self._record_response(
            "fixture:" + path,
            self.creator.client.post(path, body=body, csrf=self.creator.csrf, **kwargs),
        )
        return response

    def _create_application(self) -> None:
        response = self._creator_post("/api/v1/applications", {"name": "F12 conformance application"})
        if response.status != 201:
            raise RuntimeError(f"createApplication: {response.status} {response.text[:300]}")
        self.fixtures["application_id"] = response.json["id"]

    def _draft_body(self) -> dict:
        return {
            "definition": {
                "dsl_version": "1.0",
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "nodes": [
                    {"id": "start", "type": "start", "inputs": {},
                     "output_schema": {"type": "object"}, "config": {}},
                    {"id": "end", "type": "end", "inputs": {},
                     "output_schema": {"type": "object"}, "config": {}},
                ],
                "edges": [{"source": "start", "target": "end"}],
                "policy": {
                    "max_active_seconds": 900,
                    "max_child_depth": 3,
                    "max_child_calls": 12,
                    "budget_limit": "1.00",
                    "currency": "USD",
                },
            },
            "layout": {"viewport": {"x": 0, "y": 0, "zoom": 1}},
            "capability_card": {
                "purpose": "F12 conformance",
                "includes": ["api"],
                "excludes": ["writes"],
                "required_inputs": ["text"],
                "expected_outputs": ["text"],
                "examples": ["example one", "example two"],
            },
        }

    def _save_draft(self) -> None:
        application_id = self.fixtures.get("application_id")
        if not application_id:
            raise RuntimeError("application fixture is missing")
        draft = self._record_response(
            "fixture:draft:get",
            self.creator.client.get(f"/api/v1/applications/{application_id}/draft"),
        )
        if draft.status != 200:
            raise RuntimeError(f"getDraft: {draft.status} {draft.text[:300]}")
        etag = draft.header("ETag")
        if not etag:
            raise RuntimeError("getDraft returned no ETag")
        self.fixtures["draft_etag"] = etag
        saved = self._record_response(
            "fixture:draft:put",
            self.creator.client.request(
                "PUT",
                f"/api/v1/applications/{application_id}/draft",
                body=self._draft_body(),
                csrf=self.creator.csrf,
                headers={"If-Match": etag},
            ),
        )
        if saved.status != 200:
            raise RuntimeError(f"saveDraft: {saved.status} {saved.text[:300]}")
        self.fixtures["draft_etag"] = saved.header("ETag") or etag
        self.fixtures["draft_revision"] = int((saved.header("ETag") or etag).strip('"'))

    def _debug_run(self) -> tuple[Response, dict, str]:
        application_id = self.fixtures.get("application_id")
        if not application_id:
            raise RuntimeError("application fixture is missing")
        revision = self.fixtures.get("draft_revision", 1)
        key = "f12-gate-run-" + uuid.uuid4().hex
        body = {"draft_revision": revision, "input": {"text": "hello"}, "artifact_ids": []}
        response = self._record_response(
            "fixture:debug-run",
            self.creator.client.post(
                f"/api/v1/applications/{application_id}/debug-runs",
                body=body,
                csrf=self.creator.csrf,
                headers={"Idempotency-Key": key},
            ),
        )
        return response, body, key

    def _create_debug_run(self) -> None:
        response, _body, _key = self._debug_run()
        if response.status != 202:
            raise RuntimeError(f"debugDraft: {response.status} {response.text[:300]}")
        self.fixtures["run_id"] = response.json["id"]
        self._run_ids_with_events.append(response.json["id"])

    def _create_conversation(self) -> None:
        response = self._record_response(
            "fixture:conversation",
            self.creator.client.post("/api/v1/conversations", csrf=self.creator.csrf),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createConversation: {response.status} {response.text[:300]}")
        self.fixtures["conversation_id"] = response.json["id"]

    def _create_memory(self) -> None:
        application_id = self.fixtures.get("application_id")
        response = self._record_response(
            "fixture:memory",
            self.creator.client.post(
                "/api/v1/memories",
                body={"application_id": application_id, "text": "f12 gate memory", "confirmed": True},
                csrf=self.creator.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createMemory: {response.status} {response.text[:300]}")
        self.fixtures["memory_id"] = response.json["id"]
        # A second memory makes the pagination round-trip (limit=1 + cursor)
        # meaningful.
        self._record_response(
            "fixture:memory:second",
            self.creator.client.post(
                "/api/v1/memories",
                body={"application_id": application_id, "text": "f12 gate memory two", "confirmed": True},
                csrf=self.creator.csrf,
            ),
        )

    def _create_knowledge_base(self) -> None:
        response = self._record_response(
            "fixture:knowledge-base",
            self.creator.client.post(
                "/api/v1/knowledge-bases", body={"name": "F12 conformance KB"}, csrf=self.creator.csrf
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createKnowledgeBase: {response.status} {response.text[:300]}")
        payload = response.json or {}
        identifier = payload.get("id")
        if not identifier:
            raise RuntimeError(f"createKnowledgeBase returned no id: {response.text[:300]}")
        self.fixtures["knowledge_base_id"] = identifier

    def _create_artifact(self) -> None:
        content = b"f12-conformance-artifact"
        response = self._record_response(
            "fixture:artifact:create",
            self.creator.client.post(
                "/api/v1/artifacts/uploads",
                body={
                    "media_type": "text/plain",
                    "size_bytes": len(content),
                    "purpose": "task_input",
                },
                csrf=self.creator.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createUpload: {response.status} {response.text[:300]}")
        payload = response.json or {}
        artifact_id = payload.get("id") or payload.get("artifact_id")
        if not artifact_id:
            raise RuntimeError(f"createUpload returned no id: {response.text[:300]}")
        self.fixtures["artifact_id"] = artifact_id
        upload_url = payload.get("upload_url") or payload.get("upload")
        if upload_url:
            absolute = urllib.parse.urljoin(self.base_url + "/", upload_url)
            put = urllib.request.Request(absolute, data=content, method="PUT")
            try:
                with urllib.request.urlopen(put, timeout=self.options.timeout) as uploaded:
                    uploaded.read()
            except urllib.error.HTTPError:
                pass
        complete = self._record_response(
            "fixture:artifact:complete",
            self.creator.client.post(
                f"/api/v1/artifacts/{artifact_id}/complete",
                body={"sha256": hashlib.sha256(content).hexdigest()},
                csrf=self.creator.csrf,
            ),
        )
        if complete.status not in (200, 202):
            raise RuntimeError(f"completeUpload: {complete.status} {complete.text[:300]}")

    def _register_model(self) -> None:
        assert self.admin is not None
        response = self._record_response(
            "fixture:model",
            self.admin.client.post(
                "/api/v1/models",
                body={
                    "name": "F12 stub model",
                    "provider": "stub",
                    "model_id": "f12-stub-model",
                    "endpoint_ref": "stub-endpoint",
                    "secret_ref": "stub-credential",
                    "config": {
                        "kind": "generation",
                        "protocol": "chat_compatible",
                        "context_window": 8192,
                        "max_output_tokens": 1024,
                        "timeout_ms": 30000,
                        "price": {
                            "currency": "USD",
                            "input_per_million": "0.10",
                            "output_per_million": "0.20",
                        },
                    },
                    "data_destination": "local-stub",
                },
                csrf=self.admin.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"registerModel: {response.status} {response.text[:300]}")
        self.fixtures["model_version_id"] = response.json["id"]

    def _create_service_account(self) -> None:
        assert self.admin is not None
        response = self._record_response(
            "fixture:service-account",
            self.admin.client.post(
                "/api/v1/admin/service-accounts",
                body={
                    "display_name": "F12 conformance service",
                    "responsible_user_id": self.admin.principal_id,
                },
                csrf=self.admin.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createServiceAccount: {response.status} {response.text[:300]}")
        self.fixtures["service_account_id"] = response.json["id"]

    def _create_api_key(self) -> None:
        assert self.admin is not None
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        response = self._record_response(
            "fixture:api-key",
            self.admin.client.post(
                "/api/v1/admin/api-keys",
                body={
                    "principal_id": self.fixtures["service_account_id"],
                    "scopes": ["application:read"],
                    "expires_at": expires,
                },
                csrf=self.admin.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"createApiKey: {response.status} {response.text[:300]}")
        self.fixtures["key_id"] = response.json["id"]

    def _create_diagnostic_grant(self) -> None:
        assert self.creator is not None and self.admin is not None
        expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        response = self._record_response(
            "fixture:diagnostic-grant",
            self.creator.client.post(
                "/api/v1/diagnostic-grants",
                body={
                    "run_id": self.fixtures["run_id"],
                    # The grantee of a diagnostic grant must be an administrator;
                    # the grant is the owner's explicit door for support access.
                    "grantee_id": self.admin.principal_id,
                    "purposes": ["support"],
                    "expires_at": expires,
                },
                csrf=self.creator.csrf,
            ),
        )
        if response.status not in (200, 201):
            raise RuntimeError(f"grantDiagnostics: {response.status} {response.text[:300]}")
        self.fixtures["grant_id"] = response.json["id"]

    # -- path / body construction ------------------------------------------
    def _fixture_value(self, name: str) -> str:
        value = self.fixtures.get(name)
        if isinstance(value, str) and UUID_RE.match(value):
            return value
        return new_uuid()

    def _render_path(self, operation: Operation) -> str:
        path = operation.path
        for parameter in operation.path_params:
            path = path.replace("{" + parameter + "}", self._fixture_value(parameter))
        return path

    def _body_for(self, operation: Operation) -> Any:
        operation_id = operation.operation_id
        if operation_id in BODIES:
            return BODIES[operation_id](self)
        if operation.method in ("GET", "DELETE"):
            return None
        # The contract declares a JSON body for every remaining POST/PUT/PATCH.
        return {}

    def _headers_for(self, operation: Operation, *, normal: bool) -> dict[str, str]:
        headers: dict[str, str] = {}
        if "Idempotency-Key" in operation.header_params:
            headers["Idempotency-Key"] = "f12-gate-" + uuid.uuid4().hex
        if "If-Match" in operation.header_params:
            headers["If-Match"] = self.fixtures.get("draft_etag", '"1"')
        return headers

    # -- probes --------------------------------------------------------------
    def _probe(
        self,
        operation: Operation,
        kind: str,
        *,
        session: Session | None = None,
        internal_role: str | None = None,
        internal_header_role: str | None = None,
        with_certificate: bool = True,
        expected: set[int] | None = None,
        expected_codes: set[str] | None = None,
        note: str = "",
    ) -> dict:
        path = self._render_path(operation)
        body = self._body_for(operation)
        headers = self._headers_for(operation, normal=(kind == "normal_path"))
        label = f"{operation.operation_id}:{kind}"
        transport_error: str | None = None
        response: Response | None = None
        if operation.is_internal:
            if self.options.internal is None:
                transport_error = "the gate was started without --internal-url/--internal-ca/--internal-cert-dir"
            else:
                if internal_role is None:
                    raise AssertionError("internal probe without a role")
                if with_certificate:
                    client = self._internal_client(internal_role)
                else:
                    client = InternalClient(
                        self.options.internal, internal_role, with_certificate=False,
                        timeout=self.options.timeout,
                    )
                try:
                    response = client.call(
                        operation.method,
                        path,
                        body=body,
                        header_role=internal_header_role,
                        omit_role_header=internal_header_role == "__omit__",
                    )
                except GateTransportError as exc:
                    transport_error = str(exc)
        else:
            client = session.client if session is not None else self.anonymous
            csrf = session.csrf if session is not None else None
            try:
                response = client.request(
                    operation.method, path, body=body, csrf=csrf, headers=headers
                )
            except GateTransportError as exc:
                transport_error = str(exc)
        if response is not None:
            self._record_response(label, response)

        record: dict[str, Any] = {
            "kind": kind,
            "request": {"method": operation.method, "path": path},
            "status": response.status if response is not None else None,
            "content_type": response.content_type if response is not None else None,
            "error_code": None,
            "transport_error": transport_error,
            "expected": {
                "statuses": sorted(expected) if expected is not None else None,
                "codes": sorted(expected_codes) if expected_codes else None,
            },
            "ok": False,
            "note": note,
        }
        if response is not None and response.status >= 400:
            payload = response.json
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                record["error_code"] = payload["error"].get("code")
        if transport_error is not None:
            record["ok"] = kind == "unauthenticated" and operation.is_internal
            record["note"] = (record["note"] + " " if record["note"] else "") + (
                "no HTTP response (expected for a client-certificate boundary)"
                if record["ok"]
                else "transport failure"
            )
            return record
        assert response is not None
        if expected is not None:
            status_ok = response.status in expected
            code_ok = not expected_codes or record["error_code"] in expected_codes
            record["ok"] = status_ok and code_ok
        if response.status >= 400:
            envelope_ok, detail = check_error_envelope(response)
            record["error_envelope_ok"] = envelope_ok
            if not envelope_ok:
                record["note"] = (record["note"] + " " if record["note"] else "") + detail
        else:
            record["error_envelope_ok"] = None
        if response.status in (404, 405) and is_plain_go_error(response):
            record["route_missing"] = True
            if kind in ("anonymous", "correct_internal_role"):
                record["ok"] = False
                record["note"] = (
                    record["note"] + " " if record["note"] else ""
                ) + "the route is not registered (Go's plain-text 404/405)"
        if response.status in (404, 405) and not is_plain_go_error(response):
            record["route_missing"] = False
        return record

    def _unknown_route_probe(self) -> dict:
        path = "/api/v1/f12-nonexistent-" + uuid.uuid4().hex
        response = self._record_response("invariant:unknown-path", self.anonymous.get(path))
        envelope_ok, detail = check_error_envelope(response) if response.status >= 400 else (False, "no error")
        return {
            "request": {"method": "GET", "path": path},
            "status": response.status,
            "content_type": response.content_type,
            "error_code": (response.json or {}).get("error", {}).get("code") if response.json else None,
            "envelope_ok": envelope_ok,
            "detail": detail,
        }

    def _unknown_method_probe(self) -> dict:
        assert self.admin is not None
        response = self._record_response(
            "invariant:unknown-method",
            self.admin.client.request(
                "PATCH", "/api/v1/applications", body={}, csrf=self.admin.csrf
            ),
        )
        envelope_ok, detail = check_error_envelope(response) if response.status >= 400 else (False, "no error")
        return {
            "request": {"method": "PATCH", "path": "/api/v1/applications"},
            "status": response.status,
            "allow": response.header("Allow"),
            "content_type": response.content_type,
            "error_code": (response.json or {}).get("error", {}).get("code") if response.json else None,
            "envelope_ok": envelope_ok,
            "detail": detail,
        }

    # -- per-operation probes ----------------------------------------------
    def _run_operation_probes(self) -> None:
        for operation in self.operations:
            probes: list[dict] = []
            if operation.security == "public":
                probes.append(self._public_probe(operation))
            elif operation.security == "internal":
                probes.append(
                    self._probe(
                        operation,
                        "unauthenticated",
                        internal_role=operation.internal_role or "worker",
                        with_certificate=False,
                        expected=None,
                        note="client certificate omitted; the listener must refuse the TLS handshake",
                    )
                )
                probes.append(
                    self._probe(
                        operation,
                        "correct_internal_role",
                        internal_role=operation.internal_role or "worker",
                        expected=set(operation.declared_statuses),
                        note=f"certificate organization {operation.internal_role}",
                    )
                )
                probes.append(
                    self._probe(
                        operation,
                        "wrong_internal_role",
                        internal_role="maintenance"
                        if (operation.internal_role or "worker") != "maintenance"
                        else "worker",
                        expected={403},
                        note="a certificate of another capacity must be refused",
                    )
                )
            else:
                probes.append(self._probe(operation, "anonymous", expected={401}))
                probes.append(self._wrong_principal_probe(operation))
            record = self._operation_record(operation, probes)
            self.records[operation.operation_id] = record

    def _public_probe(self, operation: Operation) -> dict:
        if operation.operation_id == "liveness" or operation.operation_id == "readiness":
            return self._probe(operation, "anonymous", expected={200})
        if operation.operation_id == "login":
            path = operation.path
            response = self._record_response(
                "login:anonymous",
                self.anonymous.post(
                    path,
                    body={"login": "f12-no-such-user", "password": "f12-no-such-password"},
                ),
            )
            return self._response_probe(
                operation, "anonymous", response, expected={401}, expected_codes={"invalid_credentials"}
            )
        if operation.operation_id == "activateUser":
            response = self._record_response(
                "activate:anonymous",
                self.anonymous.post(
                    operation.path,
                    body={"activation_token": "f12-no-such-token", "password": "f12-long-password"},
                ),
            )
            return self._response_probe(
                operation, "anonymous", response, expected={409}, expected_codes={"activation_invalid"}
            )
        raise AssertionError(f"unhandled public operation {operation.operation_id}")

    def _response_probe(
        self,
        operation: Operation,
        kind: str,
        response: Response,
        *,
        expected: set[int] | None,
        expected_codes: set[str] | None = None,
        note: str = "",
    ) -> dict:
        record: dict[str, Any] = {
            "kind": kind,
            "request": {"method": operation.method, "path": operation.path},
            "status": response.status,
            "content_type": response.content_type,
            "error_code": None,
            "transport_error": None,
            "expected": {
                "statuses": sorted(expected) if expected is not None else None,
                "codes": sorted(expected_codes) if expected_codes else None,
            },
            "ok": False,
            "note": note,
        }
        payload = response.json
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            record["error_code"] = payload["error"].get("code")
        if expected is not None:
            record["ok"] = response.status in expected and (
                not expected_codes or record["error_code"] in expected_codes
            )
        if response.status >= 400:
            envelope_ok, detail = check_error_envelope(response)
            record["error_envelope_ok"] = envelope_ok
            if not envelope_ok:
                record["note"] = (record["note"] + " " if record["note"] else "") + detail
        else:
            record["error_envelope_ok"] = None
        return record

    def _wrong_principal_probe(self, operation: Operation) -> dict:
        assert self.member is not None
        if operation.operation_id == "logout":
            # Logout is self-scoped: the meaningful probe is the same principal
            # ending its own session, not another user's session.
            response = self._record_response(
                "logout:self-scoped",
                self.admin.client.post("/api/v1/auth/logout", csrf=self.admin.csrf),
            )
            # Sign the administrator back in: the logout above ended that
            # session, and later probes need it.
            self.admin = self._login(self.options.admin_login, self.options.admin_password)
            return self._response_probe(
                operation, "wrong_principal", response, expected={204},
                note="self-scoped: the same principal signs out (the cross-principal case does not exist)",
            )
        if operation.operation_id in ADMIN_OPS:
            return self._probe(
                operation,
                "wrong_principal",
                session=self.member,
                expected={403},
                expected_codes={"forbidden"},
                note="a plain member must not reach an administrator operation",
            )
        if operation.operation_id in CREATOR_OPS:
            return self._probe(
                operation,
                "wrong_principal",
                session=self.member,
                expected={403},
                expected_codes={"creator_role_required"},
                note="a plain member must not reach a creator-only operation",
            )
        if operation.path_params:
            return self._probe(
                operation,
                "wrong_principal",
                session=self.member,
                expected={403, 404},
                note="another principal must not observe the creator's resource",
            )
        expected = set(operation.success_statuses)
        if operation.operation_id == "submitRequest":
            expected = {202, 404, 422}
        if operation.operation_id in {"createMemory", "grantDiagnostics"}:
            # The body names a resource owned by the creator; a member cannot see
            # it, so the service answers the same 404 as a missing row.
            expected = set(operation.success_statuses) | {404}
        return self._probe(
            operation,
            "wrong_principal",
            session=self.member,
            expected=expected,
            note="self-service operation: a member may succeed; refusals must be located",
        )

    def _operation_record(self, operation: Operation, probes: list[dict]) -> dict:
        failures: list[str] = []
        for probe in probes:
            if not probe.get("ok"):
                failures.append(
                    f"{probe['kind']}: expected {probe['expected']}, got "
                    f"{probe['status']} {probe.get('error_code') or probe.get('note') or ''}".strip()
                )
        implemented_probe = None
        for probe in probes:
            if probe["kind"] in ("anonymous", "correct_internal_role"):
                implemented_probe = probe
                break
        # Two independent signals, both required for the *route* to count:
        # * the literal route registry must register this exact method+path. This
        #   is what separates "the operation was never implemented" from "the
        #   handler answered 404 for a resource"; once ContractErrors normalises
        #   the mux 404 into the envelope, the runtime probe can no longer tell
        #   them apart.
        # * the runtime probe must actually reach the handler (no plain-text mux
        #   404/405, no transport failure), so a registration that exists only in
        #   the source scan cannot pass.
        #
        # Reaching a handler is NOT "implemented" any more (T093 review): an
        # always-401 handler also reaches the handler. `functional_probe` is
        # filled from the operation's own normal-path probe below; until then the
        # honest status is route_only (not implemented).
        route_registered = not (
            self.route_registry is not None
            and operation.operation_id in self.route_missing_ids
        )
        runtime_reached = bool(
            implemented_probe
            and not implemented_probe.get("route_missing")
            and not implemented_probe.get("transport_error")
        )
        route_only = route_registered and runtime_reached
        if operation.security == "public":
            authorization_ok = True
        elif operation.security == "internal":
            authorization_ok = all(
                probe.get("ok") for probe in probes if probe["kind"] != "correct_internal_role"
            )
        else:
            authorization_ok = all(probe.get("ok") for probe in probes)
        failures_list = failures
        if not route_registered:
            verdict = "fail"
        elif not authorization_ok or failures_list:
            verdict = "fail"
        else:
            verdict = "route_only"
        return {
            "operation_id": operation.operation_id,
            "method": operation.method,
            "path": operation.path,
            "epic": epic_for(operation),
            "security": operation.security,
            "internal_role": operation.internal_role,
            "implemented": False,
            "route_registered": route_registered,
            "runtime_reached": runtime_reached,
            "route_only": route_only,
            "functional_probe": None,
            "implementation_status": (
                "missing" if not route_registered else "route_only"
            ),
            "implementation_evidence": {
                "probe": implemented_probe["kind"] if implemented_probe else None,
                "status": implemented_probe["status"] if implemented_probe else None,
                "error_code": implemented_probe.get("error_code") if implemented_probe else None,
            }
            if implemented_probe
            else None,
            "authorization_ok": authorization_ok,
            "probes": probes,
            "normal_path": None,
            "cross_principal_isolation": None,
            "failures": failures,
            "verdict": verdict,
        }

    def _finalize_implementation(self) -> None:
        """Separate route existence from a real functional probe (T093 review).

        ``implemented`` is true only when the operation's own normal-path probe
        returned one of its contract-declared success statuses. Everything else
        is ``route_only`` (registered and reachable, but the functional path was
        never demonstrated) or ``probe_failed``; neither counts as implemented.
        """
        for operation in self.operations:
            record = self.records.get(operation.operation_id)
            if record is None:
                continue
            normal = record.get("normal_path")
            functional = bool(
                normal
                and normal.get("ok")
                and normal.get("status") in operation.success_statuses
            )
            record["functional_probe"] = (
                {
                    "kind": "normal_path",
                    "status": normal.get("status"),
                    "ok": bool(normal.get("ok")),
                    "expected_statuses": sorted(operation.success_statuses),
                    "checked": True,
                }
                if normal
                else {
                    "kind": "normal_path",
                    "status": None,
                    "ok": False,
                    "expected_statuses": sorted(operation.success_statuses),
                    "checked": False,
                }
            )
            if not record["route_registered"]:
                status = "missing"
            elif functional:
                status = "functional"
            elif normal is not None:
                status = "probe_failed"
            else:
                status = "route_only"
            record["implementation_status"] = status
            record["implemented"] = functional
            if status in ("missing", "probe_failed") or not record["authorization_ok"]:
                record["verdict"] = "fail"
            elif functional:
                record["verdict"] = "pass"
            else:
                record["verdict"] = "route_only"

    # -- normal paths -------------------------------------------------------
    def _run_normal_probes(self) -> None:
        for operation_id in NORMAL_ORDER:
            action = NORMAL_ACTIONS.get(operation_id)
            if action is None:
                continue
            operation = self.operations_by_id[operation_id]
            try:
                response = action(self)
            except Exception as exc:  # noqa: BLE001 - recorded as evidence
                self.records[operation_id]["normal_path"] = {
                    "kind": "normal_path",
                    "status": None,
                    "ok": False,
                    "detail": f"the fixture could not be built: {exc}",
                }
                self.records[operation_id]["failures"].append(f"normal_path: {exc}")
                self.records[operation_id]["verdict"] = "fail"
                continue
            self._record_response("normal:" + operation_id, response)
            probe = self._response_probe(
                operation, "normal_path", response, expected=set(operation.success_statuses)
            )
            self.records[operation_id]["normal_path"] = probe
            if not probe["ok"]:
                self.records[operation_id]["failures"].append(
                    f"normal_path: expected {sorted(operation.success_statuses)}, got {response.status}"
                )
                self.records[operation_id]["verdict"] = "fail"

    # -- cross-principal content isolation ----------------------------------
    def _run_cross_principal_isolation(self) -> None:
        """Assert that another principal cannot see the creator's resources.

        The review found that ``wrong_principal`` on a self-service list expected
        200 and only checked the status code: a member could receive the same
        200 without the body ever being inspected. For every list/get operation
        with a fixture, this pass calls the same route as the plain member and
        asserts the creator's id is absent (list) or the detail read is 403/404.
        """
        assert self.member is not None and self.creator is not None
        for operation in self.operations:
            record = self.records.get(operation.operation_id)
            if record is None:
                continue
            record["cross_principal_isolation"] = self._cross_principal_for(operation)

    def _cross_principal_for(self, operation: Operation) -> dict:
        operation_id = operation.operation_id
        list_fixture = CROSS_PRINCIPAL_LIST_FIXTURES.get(operation_id)
        if list_fixture is not None:
            resource_key = list_fixture
            if not self.fixtures.get(resource_key):
                return {
                    "applicable": True,
                    "checked": False,
                    "kind": "list",
                    "resource": resource_key,
                    "reason": f"no {resource_key} fixture was created",
                }
            response = self._record_response(
                "isolation:" + operation_id, self.member.client.get(operation.path + "?limit=100")
            )
            payload = response.json or {}
            items = payload.get("items") if isinstance(payload, dict) else None
            items = items if isinstance(items, list) else []
            foreign_id = self.fixtures[resource_key]
            item_ids = {str(item.get("id")) for item in items if isinstance(item, dict)}
            text = response.text
            visible = str(foreign_id) in text or str(foreign_id) in item_ids
            result = {
                "applicable": True,
                "checked": True,
                "kind": "list",
                "resource": resource_key,
                "member_status": response.status,
                "items_returned": len(items),
                "foreign_id": foreign_id,
                "foreign_id_visible": visible,
                "isolation_ok": response.status == 200 and not visible,
            }
            if not result["isolation_ok"]:
                self.cross_principal_failures.append(
                    f"{operation_id}: member list status={response.status} "
                    f"foreign_id_visible={visible}"
                )
            return result

        detail_fixture = CROSS_PRINCIPAL_DETAIL_FIXTURES.get(operation_id)
        if detail_fixture is not None:
            if not self.fixtures.get(detail_fixture):
                return {
                    "applicable": True,
                    "checked": False,
                    "kind": "detail",
                    "resource": detail_fixture,
                    "reason": f"no {detail_fixture} fixture was created",
                }
            path = self._render_path(operation)
            response = self._record_response(
                "isolation:" + operation_id, self.member.client.get(path)
            )
            foreign_id = self.fixtures[detail_fixture]
            visible = response.status == 200 or str(foreign_id) in response.text
            result = {
                "applicable": True,
                "checked": True,
                "kind": "detail",
                "resource": detail_fixture,
                "member_status": response.status,
                "foreign_id": foreign_id,
                "foreign_id_visible": visible,
                "isolation_ok": response.status in (403, 404) and not visible,
            }
            if not result["isolation_ok"]:
                self.cross_principal_failures.append(
                    f"{operation_id}: member detail status={response.status} "
                    f"foreign_id_visible={visible}"
                )
            return result

        if operation_id in CROSS_PRINCIPAL_SHARED:
            return {
                "applicable": False,
                "checked": False,
                "reason": CROSS_PRINCIPAL_SHARED[operation_id],
            }
        if operation_id in CROSS_PRINCIPAL_NOT_RUN:
            return {
                "applicable": True,
                "checked": False,
                "reason": CROSS_PRINCIPAL_NOT_RUN[operation_id],
            }
        if operation.method == "GET" and operation.security in ("session", "session_only"):
            return {
                "applicable": True,
                "checked": False,
                "reason": "no second-principal fixture exists for this read",
            }
        return {
            "applicable": False,
            "checked": False,
            "reason": "not a list/get read operation",
        }

    # -- invariant checks ---------------------------------------------------
    def _invariant(self, name: str, checked: bool, observations: list[dict], failures: list[str], note: str = "") -> None:
        entry = {
            "checked": checked,
            "passed": checked and not failures,
            "observations": observations,
            "failures": failures,
            "note": note,
        }
        self.invariants[name] = entry
        if failures or not checked:
            self.invariant_results.append(
                {"invariant": name, "passed": entry["passed"], "failures": failures, "note": note}
            )

    def _inv_error_envelope(self) -> None:
        observations: list[dict] = []
        failures: list[str] = []
        seen = 0
        for label, response in self._all_responses:
            if response.status < 400:
                continue
            seen += 1
            ok, detail = check_error_envelope(response)
            if not ok:
                failures.append(f"{label}: HTTP {response.status} {detail}")
            observations.append(
                {
                    "case": label,
                    "status": response.status,
                    "content_type": response.content_type,
                    "envelope_ok": ok,
                    "detail": detail,
                }
            )
        unknown_path = self._unknown_route_probe()
        unknown_method = self._unknown_method_probe()
        if unknown_path["status"] != 404:
            failures.append(f"unknown path answered {unknown_path['status']}, expected 404")
        if not unknown_path["envelope_ok"]:
            failures.append(
                "unknown path is not the contract error envelope: "
                f"{unknown_path['content_type']} {unknown_path['detail']}"
            )
        if unknown_method["status"] != 405:
            failures.append(f"unknown method answered {unknown_method['status']}, expected 405")
        if not unknown_method["envelope_ok"]:
            failures.append(
                "unknown method is not the contract error envelope: "
                f"{unknown_method['content_type']} {unknown_method['detail']}"
            )
        self._invariant(
            "error_envelope",
            checked=True,
            observations=[
                {"error_responses": seen, "unknown_path": unknown_path, "unknown_method": unknown_method}
            ],
            failures=failures,
            note="every error response, including the mux's own 404/405, must use {error:{code,message,request_id[,details]}}",
        )

    def _inv_pagination(self) -> None:
        assert self.creator is not None
        observations: list[dict] = []
        failures: list[str] = []
        for operation in self.operations:
            if not operation.paginated:
                continue
            path = self._render_path(operation)
            first = self._record_response(
                "pagination:" + operation.operation_id,
                self.creator.client.get(f"{path}?limit=1"),
            )
            row: dict[str, Any] = {
                "operation_id": operation.operation_id,
                "limit_1": {"status": first.status},
            }
            if first.status != 200:
                failures.append(f"{operation.operation_id}: limit=1 answered {first.status}")
                observations.append(row)
                continue
            payload = first.json or {}
            row["limit_1"]["has_items"] = isinstance(payload.get("items"), list)
            row["limit_1"]["has_next_cursor"] = "next_cursor" in payload
            if not row["limit_1"]["has_items"]:
                failures.append(f"{operation.operation_id}: the 200 body has no items array")
            if not row["limit_1"]["has_next_cursor"]:
                failures.append(f"{operation.operation_id}: the 200 body has no next_cursor member")
            if operation.operation_id == "listMemories":
                cursor = payload.get("next_cursor")
                row["cursor_round_trip"] = {"next_cursor": cursor}
                if cb := cursor:
                    second = self._record_response(
                        "pagination:listMemories:second",
                        self.creator.client.get(f"{path}?limit=1&cursor={urllib.parse.quote(cb)}"),
                    )
                    second_payload = second.json or {}
                    row["cursor_round_trip"]["status"] = second.status
                    row["cursor_round_trip"]["items"] = len(second_payload.get("items") or [])
                    if second.status != 200 or len(second_payload.get("items") or []) != 1:
                        failures.append("listMemories: the cursor did not return the second page")
                else:
                    failures.append("listMemories: two memories exist but next_cursor is null")
            for bad in ("101", "0", "abc"):
                response = self._record_response(
                    f"pagination:{operation.operation_id}:limit={bad}",
                    self.creator.client.get(f"{path}?limit={bad}"),
                )
                key = f"limit_{bad}"
                body = response.json or {}
                details = ((body.get("error") or {}).get("details") or {})
                row[key] = {"status": response.status, "code": (body.get("error") or {}).get("code"),
                            "field": details.get("field")}
                if response.status != 422:
                    failures.append(
                        f"{operation.operation_id}: limit={bad} answered {response.status}, expected 422 (no silent clamp)"
                    )
                elif details.get("field") != "limit":
                    failures.append(
                        f"{operation.operation_id}: limit={bad} 422 has no details.field=limit"
                    )
            observations.append(row)
        self._invariant(
            "pagination",
            checked=True,
            observations=observations,
            failures=failures,
            note="items + next_cursor, cursor/limit round-trip and a located 422 for an out-of-range limit (never a clamp)",
        )

    def _draft_put(self, if_match: str | None, body: dict | None = None) -> Response:
        assert self.creator is not None
        headers = {} if if_match is None else {"If-Match": if_match}
        return self.creator.client.request(
            "PUT",
            f"/api/v1/applications/{self.fixtures['application_id']}/draft",
            body=body or self._draft_body(),
            csrf=self.creator.csrf,
            headers=headers,
        )

    def _inv_etag(self) -> None:
        observations: list[dict] = []
        failures: list[str] = []
        assert self.creator is not None
        if not self.fixtures.get("application_id"):
            self._invariant("etag_if_match", checked=False, observations=[], failures=[],
                            note="no application draft fixture exists")
            return
        draft_path = f"/api/v1/applications/{self.fixtures['application_id']}/draft"
        get = self._record_response("etag:draft:get", self.creator.client.get(draft_path))
        etag = get.header("ETag")
        observations.append({"resource": "draft", "etag": etag, "get_status": get.status})
        if get.status != 200 or not etag:
            failures.append("draft GET has no ETag")
        else:
            missing = self._record_response("etag:draft:missing", self._draft_put(None))
            stale = self._record_response('etag:draft:stale', self._draft_put('"999999"'))
            observations.append(
                {
                    "resource": "draft",
                    "missing_if_match": {"status": missing.status,
                                         "code": ((missing.json or {}).get("error") or {}).get("code")},
                    "stale_if_match": {"status": stale.status,
                                       "code": ((stale.json or {}).get("error") or {}).get("code")},
                }
            )
            if missing.status != 428:
                failures.append(f"draft PUT without If-Match answered {missing.status}, expected 428")
            if stale.status != 412:
                failures.append(f"draft PUT with a stale If-Match answered {stale.status}, expected 412")
            fresh = self._record_response("etag:draft:current", self._draft_put(etag))
            observations.append({"resource": "draft", "current_if_match": {"status": fresh.status}})
            if fresh.status != 200:
                failures.append(f"draft PUT with the current ETag answered {fresh.status}, expected 200")
            else:
                refreshed = fresh.header("ETag")
                if refreshed:
                    self.fixtures["draft_etag"] = refreshed
                    try:
                        self.fixtures["draft_revision"] = int(refreshed.strip('"'))
                    except ValueError:
                        failures.append(f"the refreshed draft ETag is not an integer revision: {refreshed!r}")

        memory_path = f"/api/v1/memories/{self.fixtures['memory_id']}"
        if not self.fixtures.get("memory_id"):
            self._invariant("etag_if_match", checked=True, observations=observations,
                            failures=["no memory fixture exists for the ETag check"])
            return
        get_memory = self._record_response("etag:memory:get", self.creator.client.get(memory_path))
        memory_etag = get_memory.header("ETag")
        observations.append({"resource": "memory", "etag": memory_etag, "get_status": get_memory.status})
        if get_memory.status != 200 or not memory_etag:
            failures.append("memory GET has no ETag")
        else:
            body = {"text": "f12 gate memory updated", "confirmed": True}
            missing = self._record_response(
                "etag:memory:missing",
                self.creator.client.request("PATCH", memory_path, body=body, csrf=self.creator.csrf),
            )
            stale = self._record_response(
                "etag:memory:stale",
                self.creator.client.request(
                    "PATCH", memory_path, body=body, csrf=self.creator.csrf,
                    headers={"If-Match": '"999999"'},
                ),
            )
            observations.append(
                {
                    "resource": "memory",
                    "missing_if_match": {"status": missing.status,
                                         "code": ((missing.json or {}).get("error") or {}).get("code")},
                    "stale_if_match": {"status": stale.status,
                                       "code": ((stale.json or {}).get("error") or {}).get("code")},
                }
            )
            if missing.status != 428:
                failures.append(f"memory PATCH without If-Match answered {missing.status}, expected 428")
            if stale.status != 412:
                failures.append(f"memory PATCH with a stale If-Match answered {stale.status}, expected 412")
        self._invariant(
            "etag_if_match",
            checked=True,
            observations=observations,
            failures=failures,
            note="draft and memory: strong ETag on read, 428 for a missing If-Match, 412 for a stale one; Run cancel is deliberately ETag-free",
        )

    def _inv_idempotency(self) -> None:
        observations: list[dict] = []
        failures: list[str] = []
        assert self.creator is not None
        application_id = self.fixtures.get("application_id")
        if not application_id:
            self._invariant("idempotency", checked=False, observations=[], failures=[],
                            note="no application fixture exists, so debugDraft idempotency was not observed")
            return
        revision = self.fixtures.get("draft_revision", 1)
        key = "f12-idem-" + uuid.uuid4().hex
        body = {"draft_revision": revision, "input": {"text": "idempotent hello"}, "artifact_ids": []}
        path = f"/api/v1/applications/{application_id}/debug-runs"
        first = self._record_response(
            "idem:debug:first",
            self.creator.client.post(path, body=body, csrf=self.creator.csrf,
                                     headers={"Idempotency-Key": key}),
        )
        second = self._record_response(
            "idem:debug:replay",
            self.creator.client.post(path, body=body, csrf=self.creator.csrf,
                                     headers={"Idempotency-Key": key}),
        )
        conflict = self._record_response(
            "idem:debug:conflict",
            self.creator.client.post(
                path,
                body={"draft_revision": revision, "input": {"text": "different body"}, "artifact_ids": []},
                csrf=self.creator.csrf,
                headers={"Idempotency-Key": key},
            ),
        )
        observations.append(
            {
                "case": "debugDraft",
                "first": {"status": first.status, "run_id": (first.json or {}).get("id")},
                "replay": {"status": second.status, "run_id": (second.json or {}).get("id")},
                "same_key_different_body": {
                    "status": conflict.status,
                    "code": ((conflict.json or {}).get("error") or {}).get("code"),
                },
            }
        )
        first_id = (first.json or {}).get("id")
        second_id = (second.json or {}).get("id")
        if first.status != 202:
            failures.append(f"debugDraft first submission answered {first.status}, expected 202")
        if first_id != second_id:
            failures.append("the same Idempotency-Key and body produced a different Run")
        if conflict.status != 409:
            failures.append(
                f"the same Idempotency-Key with a different body answered {conflict.status}, expected 409"
            )

        missing_header: dict[str, dict] = {}
        for operation_id in self.idempotency_required:
            operation = self.operations_by_id[operation_id]
            session = self.admin if operation_id in ADMIN_OPS else self.creator
            path_value = self._render_path(operation)
            response = self._record_response(
                "idem:missing:" + operation_id,
                session.client.request(
                    operation.method, path_value, body=self._body_for(operation), csrf=session.csrf
                ),
            )
            payload = response.json or {}
            error = payload.get("error") or {}
            details = error.get("details") or {}
            identifies = details.get("header") == "Idempotency-Key" or details.get("field") == "Idempotency-Key"
            missing_header[operation_id] = {
                "status": response.status,
                "code": error.get("code"),
                "details": details,
            }
            if response.status not in (400, 422) or not identifies:
                failures.append(
                    f"{operation_id}: a missing Idempotency-Key answered {response.status} "
                    f"{error.get('code')} details={details}"
                )
        observations.append({"missing_idempotency_key": missing_header})
        shapes = {json.dumps(row.get("details"), sort_keys=True) for row in missing_header.values()}
        observations.append(
            {"missing_key_details_shapes": sorted(shapes),
             "note": "two documented shapes exist (decision endpoints use details.field; the rest details.header); "
                     "each is located and names the header, so the gate checks identification rather than one spelling"}
        )
        self._invariant(
            "idempotency",
            checked=True,
            observations=observations,
            failures=failures,
            note="same key + same body replays the resource, same key + different body is 409, a missing key is a located 4xx that names the header",
        )

    def _inv_sse(self) -> None:
        observations: list[dict] = []
        failures: list[str] = []
        assert self.creator is not None
        run_id = self.fixtures.get("run_id")
        if not run_id:
            self._invariant("sse_frames", checked=False, observations=[], failures=[],
                            note="no Run fixture exists, so the durable SSE stream was not observed")
            return
        cookie = self.creator.cookie("platform_session")
        headers = {"Accept": "text/event-stream", "Cookie": f"platform_session={cookie}"}
        result = self._read_sse(f"/api/v1/runs/{run_id}/events", headers, timeout=6.0)
        observations.append(result)
        if not result["content_type"].startswith("text/event-stream"):
            failures.append(f"the events response is {result['content_type']!r}, not text/event-stream")
        if not result["frames"]:
            failures.append("the events stream produced no frame within the read window")
        for frame in result["frames"]:
            if frame["id"] is None or frame["event"] is None or frame["data"] is None:
                failures.append(f"malformed SSE frame: {frame}")
            elif not frame["schema_ok"]:
                failures.append(f"event data failed event.schema.json: {frame['schema_error']}")
        if result["frames"]:
            last = str(result["frames"][-1]["id"])
            replay = self._read_sse(
                f"/api/v1/runs/{run_id}/events",
                {**headers, "Last-Event-ID": last},
                timeout=4.0,
            )
            observations.append({"last_event_id_reconnect": replay})
            if not replay["content_type"].startswith("text/event-stream"):
                failures.append("the Last-Event-ID reconnect is not an SSE response")
            if replay["frames"] and replay["frames"][0]["id"] is not None and replay["frames"][0]["id"] < int(last):
                failures.append("the Last-Event-ID reconnect replayed a seq older than the cursor")
        self._invariant(
            "sse_frames",
            checked=True,
            observations=observations,
            failures=failures,
            note="id:/event:/data: frames whose data is the event.schema envelope; Last-Event-ID replays this Run's seq. "
                 "The 410 cursor-expired branch needs a retention-window fixture and is recorded as not_run.",
        )
        self.invariants["sse_frames"]["not_run"] = [
            "410 event_cursor_expired + snapshot_url: requires an event older than ORCHARA_RUN_EVENT_RETENTION; "
            "the development stack keeps 24h, so this branch was not exercised"
        ]

    def _read_sse(self, path: str, headers: dict[str, str], *, timeout: float) -> dict:
        parsed = urllib.parse.urlsplit(self.base_url)
        connection = http.client.HTTPConnection(
            parsed.hostname or "127.0.0.1", parsed.port or 80, timeout=2.0
        )
        frames: list[dict] = []
        content_type = ""
        current: dict[str, Any] = {}
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type") or ""
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    line = response.readline()
                except TimeoutError:
                    break
                except OSError:
                    break
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text == "":
                    if current:
                        frames.append(self._finish_frame(current))
                        current = {}
                    continue
                if text.startswith(":"):
                    continue
                name, _, value = text.partition(":")
                current[name] = value[1:] if value.startswith(" ") else value
                if len(frames) >= 20:
                    break
        except (OSError, http.client.HTTPException) as exc:
            return {"content_type": content_type, "frames": [], "error": str(exc)}
        finally:
            connection.close()
        if current:
            frames.append(self._finish_frame(current))
        # Drop incomplete keep-alive comment-only frames.
        frames = [frame for frame in frames if frame["event"] is not None or frame["data"] is not None]
        return {"content_type": content_type, "frames": frames}

    def _finish_frame(self, current: dict[str, Any]) -> dict:
        frame = {
            "id": int(current["id"]) if current.get("id", "").isdigit() else None,
            "event": current.get("event"),
            "data": None,
            "schema_ok": False,
            "schema_error": None,
        }
        if current.get("data") is not None:
            try:
                frame["data"] = json.loads(current["data"])
            except json.JSONDecodeError as exc:
                frame["schema_error"] = f"data is not JSON: {exc}"
                return frame
            ok, error = validate_event_envelope(frame["data"])
            frame["schema_ok"] = ok
            frame["schema_error"] = error
        return frame

    def _non_uuid_id_fields_for(self, label: str) -> frozenset[str]:
        """The contract-declared plain-string ``id`` fields for one probe label.

        ``label`` is ``normal:<operationId>`` or ``<operationId>:<kind>`` (plus
        setup labels that match no operation). This keeps the UUID invariant
        honest: it applies to every platform resource id and skips exactly the
        catalog fields whose OpenAPI schema says ``type: string``.
        """
        for operation in self.operations:
            if not operation.non_uuid_id_fields:
                continue
            if label == f"normal:{operation.operation_id}" or label.startswith(
                operation.operation_id + ":"
            ):
                return frozenset(operation.non_uuid_id_fields)
        return frozenset()

    def _inv_shapes(self) -> None:
        uuid_failures: list[str] = []
        time_failures: list[str] = []
        decimal_failures: list[str] = []
        nostore_failures: list[str] = []
        uuid_seen = 0
        time_seen = 0
        decimal_seen = 0
        for label, payload in self._success_bodies:
            allowed_non_uuid = self._non_uuid_id_fields_for(label)
            checked, failures = scan_uuid_fields(
                label, payload, allowed_non_uuid=allowed_non_uuid
            )
            uuid_seen += checked
            uuid_failures.extend(failures)
            checked, failures = scan_time_fields(label, payload)
            time_seen += checked
            time_failures.extend(failures)
            checked, failures = scan_decimal_fields(label, payload)
            decimal_seen += checked
            decimal_failures.extend(failures)
        for label, response in self._all_responses:
            if "json" not in response.content_type:
                continue
            cache = response.header("Cache-Control") or ""
            if "no-store" not in cache:
                nostore_failures.append(f"{label}: Cache-Control={cache!r}")
        self._invariant(
            "uuid_server_generated",
            checked=True,
            observations=[{"id_fields_checked": uuid_seen}],
            failures=uuid_failures[:20],
            note="every observed resource id is a server-generated UUID; the only exception is contract-declared plain-string ids of the deployer configuration catalogs (listCredentialRefs/listModelEndpoints), derived from the OpenAPI success schema",
        )
        self._invariant(
            "utc_rfc3339",
            checked=True,
            observations=[{"timestamp_fields_checked": time_seen}],
            failures=time_failures[:20],
            note="timestamps parse as RFC3339 with a UTC designator; the gate parses instead of comparing strings because RFC3339 and RFC3339Nano both occur",
        )
        self._invariant(
            "decimal_strings",
            checked=True,
            observations=[{"money_fields_checked": decimal_seen}],
            failures=decimal_failures[:20],
            note="money/usage fields are decimal strings, never binary floats",
        )
        self._invariant(
            "no_store",
            checked=True,
            observations=[{"json_responses_checked": sum(1 for _l, r in self._all_responses if 'json' in r.content_type)}],
            failures=nostore_failures[:20],
            note="every JSON response (including successful reads) is Cache-Control: no-store",
        )

    def _inv_no_generic_crud(self) -> None:
        observations: list[dict] = []
        failures: list[str] = []
        unknown_path = self._unknown_route_probe()
        unknown_method = self._unknown_method_probe()
        assert self.admin is not None
        random_id = new_uuid()
        hidden = self._record_response(
            "crud:unknown-resource",
            self.admin.client.get(f"/api/v1/applications/{random_id}"),
        )
        hidden_ok, hidden_detail = (
            check_error_envelope(hidden) if hidden.status >= 400 else (False, "the API returned a body")
        )
        observations.append(
            {
                "runtime": {
                    "unknown_path": unknown_path,
                    "unknown_method": unknown_method,
                    "unowned_resource": {
                        "status": hidden.status,
                        "envelope_ok": hidden_ok,
                        "detail": hidden_detail,
                    },
                }
            }
        )
        if hidden.status != 404 or not hidden_ok:
            failures.append(
                f"GET a random application id answered {hidden.status} (envelope={hidden_ok}); "
                "there must be no enumerable generic resource route"
            )
        static = self.route_registry or scan_route_registry(self.options.product_root, self.operations)
        observations.append({"static_registry": static})
        failures.extend(static["failures"])
        self._invariant(
            "no_generic_crud",
            checked=True,
            observations=observations,
            failures=failures,
            note="no wildcard CRUD handler: an unknown path/method is refused, a random id is invisible, "
                 "and the compiled route registry only contains the explicit patterns",
        )

    # -- report -------------------------------------------------------------
    def run(self) -> dict:
        # The route registry is scanned before the first probe: it is the
        # authority for operation existence, and the runtime probe cannot infer it
        # once a missing route and a missing resource share the error envelope.
        self.route_registry = scan_route_registry(self.options.product_root, self.operations)
        self.route_missing_ids = frozenset(self.route_registry.get("missing_operation_ids", ()))
        try:
            self._build_fixtures()
        except Exception as exc:  # noqa: BLE001 - a broken setup is the verdict
            self.errors.append(f"fixture setup failed: {exc}")
        if self.admin is None:
            return self._report()

        self._run_operation_probes()
        # Content isolation runs against the fixtures the setup built, before
        # the normal probes start deleting them.
        self._run_cross_principal_isolation()
        self._inv_error_envelope()
        self._inv_pagination()
        self._inv_etag()
        self._inv_idempotency()
        self._inv_sse()
        self._inv_no_generic_crud()
        self._run_normal_probes()
        # `implemented` needs the normal-path results; classify route existence
        # separately from a real functional probe after they have run.
        self._finalize_implementation()
        # Shape invariants run last: they scan every recorded success body, and
        # the normal-path responses (a real run, lease, artifact, approval, ...)
        # are the ones that actually carry ids, timestamps and money strings.
        # Running this before the normal probes used to report "checked" over an
        # empty field set.
        self._inv_shapes()
        return self._report()

    def _report(self) -> dict:
        records = [self.records.get(operation.operation_id) for operation in self.operations]
        records = [record for record in records if record is not None]
        missing = [
            record["operation_id"]
            for record in records
            if record["implementation_status"] == "missing"
        ]
        functional = [
            record["operation_id"]
            for record in records
            if record["implementation_status"] == "functional"
        ]
        route_only = [
            record["operation_id"]
            for record in records
            if record["implementation_status"] == "route_only"
        ]
        probe_failed = [
            record["operation_id"]
            for record in records
            if record["implementation_status"] == "probe_failed"
        ]
        authorization_failed = [
            record["operation_id"] for record in records if not record["authorization_ok"]
        ]
        per_operation_failed = [record["operation_id"] for record in records if record["verdict"] == "fail"]
        invariant_failures = [entry for entry in self.invariant_results if not entry["passed"]]
        not_run = {
            name: entry.get("not_run", [])
            for name, entry in self.invariants.items()
            if entry.get("not_run")
        }
        cross_checked = [
            record["operation_id"]
            for record in records
            if (record.get("cross_principal_isolation") or {}).get("checked")
        ]
        cross_not_run = [
            record["operation_id"]
            for record in records
            if (record.get("cross_principal_isolation") or {}).get("applicable")
            and not (record.get("cross_principal_isolation") or {}).get("checked")
        ]
        by_epic: dict[str, dict] = {}
        for record in records:
            bucket = by_epic.setdefault(
                record["epic"],
                {
                    "operations": 0,
                    "implemented": 0,
                    "route_only": 0,
                    "failed": 0,
                    "missing": [],
                    "route_only_operations": [],
                },
            )
            bucket["operations"] += 1
            bucket["implemented"] += int(record["implementation_status"] == "functional")
            bucket["route_only"] += int(record["implementation_status"] == "route_only")
            bucket["failed"] += int(record["verdict"] == "fail")
            if record["implementation_status"] == "missing":
                bucket["missing"].append(record["operation_id"])
            elif record["implementation_status"] == "route_only":
                bucket["route_only_operations"].append(record["operation_id"])
        verdict_failed = bool(
            self.errors
            or missing
            or probe_failed
            or authorization_failed
            or invariant_failures
            or self.cross_principal_failures
            or any(record["verdict"] == "fail" for record in records)
        )
        verdict = "failed" if verdict_failed else ("route_only" if route_only else "passed")
        report = {
            "ticket": "T093",
            "issue": 94,
            "title": "外部客户端验证全量 REST/SSE 共同契约",
            "generated_at": utc_now(),
            "generated_by": "product/tools/f12_api_gate.py",
            "environment": {
                "base_url": self.base_url,
                "profile": self.options.profile,
                "transport": "real HTTP over a socket against a running cmd/api process (no in-process handler calls)",
                "internal_listener": bool(self.options.internal),
                "requests": self._request_count,
            },
            "contract": {
                "path": str(self.contract_path),
                "sha256": sha256_file(self.contract_path),
                "expected_operations": self.options.expected_operations,
                "operations": len(self.operations),
            },
            "fixtures": self.fixture_notes,
            "fixture_ids": {
                key: value
                for key, value in self.fixtures.items()
                if isinstance(value, str) and UUID_RE.match(value)
            },
            "operations": records,
            "summary": {
                "operations": len(self.operations),
                # `implemented` now counts functional probes only; the separate
                # route counts are the honest evidence the review asked for.
                "implemented": len(functional),
                "route_registered": sum(
                    1 for record in records if record["route_registered"]
                ),
                "functional_probe": len(functional),
                "functional_coverage": f"{len(functional)}/{len(records)}",
                "route_only": len(route_only),
                "route_only_operations": route_only,
                "probe_failed": len(probe_failed),
                "probe_failed_operations": probe_failed,
                "missing_operations": missing,
                "authorization_failed": authorization_failed,
                "per_operation_failed": per_operation_failed,
                "invariant_failures": invariant_failures,
                "cross_principal_isolation": {
                    "checked": len(cross_checked),
                    "checked_operations": cross_checked,
                    "not_run": cross_not_run,
                    "failures": self.cross_principal_failures,
                },
                "not_run": not_run,
                "errors": self.errors,
                "by_epic": by_epic,
                "verdict": verdict,
            },
            "invariants": self.invariants,
        }
        return redact_value(report)


def check_error_envelope(response: Response) -> tuple[bool, str]:
    if "json" not in response.content_type:
        return False, f"content-type is {response.content_type!r}, not JSON"
    payload = response.json
    if not isinstance(payload, dict):
        return False, "the body is not a JSON object"
    if set(payload) != {"error"}:
        return False, f"top-level members are {sorted(payload)}"
    error = payload.get("error")
    if not isinstance(error, dict):
        return False, "error is not an object"
    missing = {"code", "message", "request_id"} - set(error)
    if missing:
        return False, f"error is missing {sorted(missing)}"
    allowed = {"code", "message", "request_id", "details"}
    extra = set(error) - allowed
    if extra:
        return False, f"error has unexpected members {sorted(extra)}"
    if not isinstance(error["code"], str) or not error["code"]:
        return False, "error.code is not a non-empty string"
    if not isinstance(error["message"], str):
        return False, "error.message is not a string"
    if not isinstance(error["request_id"], str) or not error["request_id"]:
        return False, "error.request_id is not a non-empty string"
    if "details" in error and not isinstance(error["details"], dict):
        return False, "error.details is not an object"
    return True, ""


def is_plain_go_error(response: Response) -> bool:
    if response.status not in (404, 405):
        return False
    if "json" in response.content_type:
        return False
    text = response.text
    return "page not found" in text or "Method Not Allowed" in text


def validate_event_envelope(payload: Any) -> tuple[bool, str | None]:
    if not isinstance(payload, dict):
        return False, "event data is not a JSON object"
    try:
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts" / "event.schema.json").read_text(
                encoding="utf-8"
            )
        )
    except OSError as exc:  # pragma: no cover - checkout layout
        return False, f"cannot read event.schema.json: {exc}"
    try:
        import jsonschema  # type: ignore
    except ImportError:  # pragma: no cover - not the normal environment
        required = set(schema.get("required", []))
        missing = required - set(payload)
        return (not missing, f"missing {sorted(missing)}" if missing else None)
    try:
        jsonschema.validate(payload, schema)
    except jsonschema.ValidationError as exc:  # type: ignore[attr-defined]
        return False, exc.message
    return True, None


def _is_workflow_graph_object(payload: dict) -> bool:
    keys = set(payload)
    return (
        "type" in keys and ({"inputs", "config", "output_schema"} & keys)
    ) or {"source", "target"} <= keys


def scan_uuid_fields(
    label: str, payload: Any, *, allowed_non_uuid: frozenset[str] = frozenset()
) -> tuple[int, list[str]]:
    """Return (id fields examined, violations) for one success body."""
    checked = 0
    failures: list[str] = []
    if isinstance(payload, dict):
        graph_object = _is_workflow_graph_object(payload)
        for key, value in payload.items():
            if key in ID_KEYS and isinstance(value, str) and value:
                checked += 1
                # The contract types this catalog's id as a plain string.
                if key in allowed_non_uuid:
                    pass
                # Workflow node/edge ids are graph-local names ("start", "end",
                # "node-1"), not server-generated resource ids.
                elif key == "id" and graph_object:
                    pass
                elif not UUID_RE.match(value):
                    failures.append(f"{label}: {key}={value!r} is not a UUID")
            sub_checked, sub_failures = scan_uuid_fields(
                label, value, allowed_non_uuid=allowed_non_uuid
            )
            checked += sub_checked
            failures.extend(sub_failures)
    elif isinstance(payload, list):
        for value in payload:
            sub_checked, sub_failures = scan_uuid_fields(
                label, value, allowed_non_uuid=allowed_non_uuid
            )
            checked += sub_checked
            failures.extend(sub_failures)
    return checked, failures


def scan_time_fields(label: str, payload: Any) -> tuple[int, list[str]]:
    checked = 0
    failures: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            is_time = key in TIME_KEYS or key.endswith("_at") or key.endswith("_deadline")
            if is_time and isinstance(value, str) and value:
                checked += 1
                if not RFC3339_RE.match(value):
                    failures.append(f"{label}: {key}={value!r} is not RFC3339")
                else:
                    try:
                        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
                    except ValueError:
                        failures.append(f"{label}: {key}={value!r} does not parse")
                    else:
                        if parsed.utcoffset() != dt.timedelta(0):
                            failures.append(f"{label}: {key}={value!r} is not UTC")
            sub_checked, sub_failures = scan_time_fields(label, value)
            checked += sub_checked
            failures.extend(sub_failures)
    elif isinstance(payload, list):
        for value in payload:
            sub_checked, sub_failures = scan_time_fields(label, value)
            checked += sub_checked
            failures.extend(sub_failures)
    return checked, failures


def scan_decimal_fields(label: str, payload: Any) -> tuple[int, list[str]]:
    checked = 0
    failures: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in MONEY_KEYS and value is not None:
                checked += 1
                if not isinstance(value, str) or not DECIMAL_RE.match(value):
                    failures.append(f"{label}: {key}={value!r} is not a decimal string")
            sub_checked, sub_failures = scan_decimal_fields(label, value)
            checked += sub_checked
            failures.extend(sub_failures)
    elif isinstance(payload, list):
        for value in payload:
            sub_checked, sub_failures = scan_decimal_fields(label, value)
            checked += sub_checked
            failures.extend(sub_failures)
    return checked, failures


def scan_route_registry(product_root: Path | None, operations: list[Operation]) -> dict:
    """Static cross-check of the compiled route registry.

    This scan is the **authority for operation existence**: the runtime probe
    cannot distinguish "no route matches this path" from "the handler looked the
    resource up and found nothing", because both arrive as the same contract
    error envelope. It also produces the second signal for the generic-CRUD
    invariant: the registry contains only explicit patterns and every contract
    operation has a literal registration. The runtime probe remains the
    authority for the authorization boundary and the response shape.
    """
    failures: list[str] = []
    if product_root is None:
        product_root = Path(__file__).resolve().parents[1]
    sources = [
        product_root / "internal" / "api" / "server.go",
        product_root / "internal" / "api" / "internal.go",
        product_root / "cmd" / "api" / "main.go",
    ]
    registered: set[tuple[str, str]] = set()
    generic: list[str] = []
    pattern_re = re.compile(r'HandleFunc\(\s*"([^"]+)"')
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"cannot read {source}: {exc}")
            continue
        for match in pattern_re.finditer(text):
            pattern = match.group(1)
            if " " in pattern:
                method, path = pattern.split(" ", 1)
            else:
                method, path = "*", pattern
            registered.add((method, path))
            segments = [segment for segment in path.strip("/").split("/") if segment]
            for index, segment in enumerate(segments):
                if segment == "{$}":
                    # The exact-root anchor is a literal, not a wildcard.
                    continue
                if "{...}" in segment or (segment.startswith("{") and segment.endswith("}")):
                    # A wildcard is normal only after a literal collection
                    # prefix; a wildcard as the first API segment is a generic
                    # resource router.
                    first_api_segment = 1 if segments[0] in ("api", "internal") else 0
                    if index <= first_api_segment:
                        generic.append(f"{method} {path}")
                    if segment.startswith("{") and "..." in segment:
                        generic.append(f"{method} {path} (trailing wildcard)")
    missing: list[str] = []
    missing_operation_ids: list[str] = []
    for operation in operations:
        if (operation.method, operation.path) in registered:
            continue
        if ("*", operation.path) in registered:
            continue
        missing.append(f"{operation.method} {operation.path} ({operation.operation_id})")
        missing_operation_ids.append(operation.operation_id)
    if missing:
        failures.append(f"{len(missing)} contract operations have no literal route registration: {missing}")
    if generic:
        failures.append(f"wildcard/generic route handlers exist: {generic}")
    return {
        "sources": [str(source.relative_to(product_root)) for source in sources if source.exists()],
        "registered_patterns": len(registered),
        "contract_operations": len(operations),
        "missing_registrations": missing,
        "missing_operation_ids": sorted(missing_operation_ids),
        "generic_patterns": generic,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# Probe tables
# ---------------------------------------------------------------------------
# Operations whose handler calls authorize(w, r, "admin") or the identity
# equivalent: a plain member must be refused with 403 forbidden.
ADMIN_OPS = frozenset(
    {
        "inviteUser",
        "updatePrincipal",
        "setUserRoles",
        "grantResource",
        "revokeResourceGrant",
        "createServiceAccount",
        "createApiKey",
        "revokeApiKey",
        "listCredentialRefs",
        "listModelEndpoints",
        "registerModel",
        "validateModel",
        "reviewPublication",
        "revokeVersion",
        "reviewTool",
    }
)

# Operations gated by requireApplicationWrite: a browser session must hold the
# creator role (an administrator without it is refused too).
CREATOR_OPS = frozenset(
    {
        "createApplication",
        "saveDraft",
        "validateApplication",
        "debugDraft",
        "evaluateDraft",
        "submitApplication",
        "submitTool",
    }
)

# Cross-principal content isolation: which fixture id must be invisible to the
# plain member on a list / detail read (T093 review, H).
CROSS_PRINCIPAL_LIST_FIXTURES: dict[str, str] = {
    "listApplications": "application_id",
    "listRuns": "run_id",
    "listConversations": "conversation_id",
    "listMemories": "memory_id",
    "listKnowledgeBases": "knowledge_base_id",
}

CROSS_PRINCIPAL_DETAIL_FIXTURES: dict[str, str] = {
    "getApplication": "application_id",
    "getDraft": "application_id",
    "listVersions": "application_id",
    "getRun": "run_id",
    "listRunArtifacts": "run_id",
    "getConversation": "conversation_id",
    "listMessages": "conversation_id",
    "getMemory": "memory_id",
    "getKnowledgeBase": "knowledge_base_id",
    "listKnowledgeDocuments": "knowledge_base_id",
    "getArtifact": "artifact_id",
    "readArtifactContent": "artifact_id",
    "downloadArtifact": "artifact_id",
}

CROSS_PRINCIPAL_SHARED: dict[str, str] = {
    "getIdentity": "self-scoped identity read; there is no foreign body to hide",
    "listModels": "shared deployer catalog; the response is not scoped to one principal",
    "listTools": "shared published catalog; the response is not scoped to one principal",
    "getModel": "shared model catalog; the model version is deployer-registered, not per-principal",
    "listPublicationReviews": "administrator review queue; a plain member is refused before any body is built",
    "listCredentialRefs": "administrator-only deployment configuration; not a per-principal body",
    "listModelEndpoints": "administrator-only deployment configuration; not a per-principal body",
}

CROSS_PRINCIPAL_NOT_RUN: dict[str, str] = {
    "listApprovals": "no approval fixture is created by the gate; the member's empty list is the only observation",
    "listMemoryCandidates": "no memory-candidate fixture is created by the gate",
    "getRequest": "the request fixture is created by the request normal-path probe after this pass",
    "getVersion": "no published version fixture exists in the gate",
    "getTool": "no tool-version fixture is selected by the gate",
    "getPublicationReview": "no publication-review fixture exists in the gate",
    "getEvaluation": "no evaluation fixture exists in the gate",
    "getEvaluationReport": "no evaluation-report fixture exists in the gate",
    "getKnowledgeDocument": "no ingested knowledge-document fixture exists in the gate",
    "getBackgroundJob": "no background-job fixture is selected by the gate",
    "getDeletion": "no deletion fixture exists before the destructive normal probes",
    "getApproval": "no approval fixture exists in the gate",
}


def _future(hours: int = 1) -> str:
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


BODIES: dict[str, Callable[[Gate], Any]] = {
    "createApplication": lambda gate: {"name": "F12 gate application"},
    "saveDraft": lambda gate: gate._draft_body(),
    "debugDraft": lambda gate: {
        "draft_revision": int(gate.fixtures.get("draft_revision", 1)),
        "input": {"text": "hello"},
        "artifact_ids": [],
    },
    "evaluateDraft": lambda gate: {
        "draft_revision": 1,
        "dataset_artifact_id": new_uuid(),
    },
    "submitApplication": lambda gate: {
        "expected_manifest_sha256": "0" * 64,
        "evaluation_report_id": new_uuid(),
    },
    "reviewPublication": lambda gate: {
        "decision": "approve",
        "manifest_sha256": "0" * 64,
        "reason": "f12 gate",
    },
    "revokeVersion": lambda gate: {"reason": "f12 gate", "stop_existing": False},
    "submitRequest": lambda gate: {
        "conversation_id": gate.fixtures.get("conversation_id") or new_uuid(),
        "message": "f12 gate request",
        "artifact_ids": [],
    },
    "replyInteraction": lambda gate: {"interaction_id": new_uuid(), "message": "f12 gate reply"},
    "createUpload": lambda gate: {
        "media_type": "text/plain",
        "size_bytes": 24,
        "purpose": "task_input",
    },
    "completeUpload": lambda gate: {"sha256": "0" * 64},
    "decideApproval": lambda gate: {"decision": "approve", "params_sha256": "0" * 64},
    "reconcileOperation": lambda gate: {
        "conclusion": "confirmed_not_sent",
        "evidence_artifact_id": new_uuid(),
    },
    "createMemory": lambda gate: {
        "application_id": gate.fixtures.get("application_id") or new_uuid(),
        "text": "f12 gate memory",
        "confirmed": True,
    },
    "updateMemory": lambda gate: {"text": "f12 gate memory", "confirmed": True},
    "submitTool": lambda gate: {
        "source_artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "dependency_lock": {},
        "risk_class": "pure",
    },
    "reviewTool": lambda gate: {
        "decision": "approve",
        "manifest_sha256": "0" * 64,
        "reason": "f12 gate",
    },
    "createKnowledgeBase": lambda gate: {"name": "F12 gate KB"},
    "ingestDocument": lambda gate: {
        "source_artifact_id": gate.fixtures.get("artifact_id") or new_uuid()
    },
    "registerModel": lambda gate: {
        "name": "F12 gate model",
        "provider": "stub",
        "model_id": "f12-gate-model",
        "endpoint_ref": "stub-endpoint",
        "secret_ref": "stub-credential",
        "config": {
            "kind": "generation",
            "protocol": "chat_compatible",
            "timeout_ms": 30000,
            "price": {"currency": "USD", "input_per_million": "0.10", "output_per_million": "0.20"},
        },
        "data_destination": "local-stub",
    },
    "grantDiagnostics": lambda gate: {
        "run_id": gate.fixtures.get("run_id") or new_uuid(),
        "grantee_id": gate.member.principal_id if gate.member else new_uuid(),
        "purposes": ["support"],
        "expires_at": _future(),
    },
    "inviteUser": lambda gate: {
        "login": "f12-invited-" + uuid.uuid4().hex[:8],
        "display_name": "F12 Invited",
        "roles": ["user"],
    },
    "updatePrincipal": lambda gate: {"disabled": False, "reason": "f12 gate"},
    "setUserRoles": lambda gate: {"roles": ["user"]},
    "grantResource": lambda gate: {
        "principal_id": gate.member.principal_id if gate.member else new_uuid(),
        "resource_type": "application",
        "resource_id": gate.fixtures.get("application_id") or new_uuid(),
        "action": "read",
    },
    "createServiceAccount": lambda gate: {
        "display_name": "F12 gate service",
        "responsible_user_id": gate.admin.principal_id if gate.admin else new_uuid(),
    },
    "createApiKey": lambda gate: {
        "principal_id": gate.fixtures.get("service_account_id") or new_uuid(),
        "scopes": ["application:read"],
        "expires_at": _future(),
    },
    "decideMemoryCandidate": lambda gate: {"decision": "confirm"},
    "claimJob": lambda gate: {
        "worker_id": "f12-gate-supervisor",
        "supported_compilers": ["orchara-compiler/1.0.0"],
        "execution_unit_id": "f12-gate-unit",
    },
    "heartbeat": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "execution_unit_id": "f12-gate-unit",
    },
    "submitWorkerEvents": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "events": [
            {
                "event_id": new_uuid(),
                "local_sequence": 1,
                "type": "node.status",
                "payload": {"node_id": "start", "state": "running", "attempt_no": 1},
            }
        ],
    },
    "submitWorkerResult": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "event_id": new_uuid(),
        "suggested_state": "failed",
        "error_code": "f12_gate_probe",
    },
    "invokeCapability": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "operation_id": new_uuid(),
        "logical_key": "f12-gate-capability",
        "capability_version": "1.0",
        "params": {},
        "capability_kind": "tool",
    },
    "spawnChild": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "logical_key": "f12-gate-child",
        "application_version_id": new_uuid(),
        "input": {},
    },
    "confirmAttemptExit": lambda gate: {
        "execution_unit_id": "f12-gate-unit",
        "evidence": {"started": False},
    },
    "registerRunArtifact": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
        "relation": "output",
    },
    "createWorkerUpload": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "media_type": "text/plain",
        "size_bytes": 24,
        "purpose": "task_output",
    },
    "completeWorkerUpload": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "sha256": "0" * 64,
    },
    "prepareApproval": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "logical_key": "f12-gate-approval",
        "operation_id": new_uuid(),
        "proposal": {"summary": "f12 gate"},
    },
    "createWorkerDownload": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
    },
    "getWorkerArtifactStatus": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
    },
    "proposeMemoryCandidate": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "logical_key": "f12-gate-memory",
        "text": "f12 gate candidate",
    },
    "claimBackgroundJob": lambda gate: {
        "worker_id": "f12-gate-maintenance",
        "supported_kinds": ["artifact_finalize"],
    },
    "heartbeatBackgroundJob": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid()
    },
    "completeBackgroundJob": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "event_id": new_uuid(),
        "state": "succeeded",
    },
    "invokeBackgroundModel": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "logical_key": "f12-gate-model-call",
        "model_profile_version_id": new_uuid(),
        "params": {},
    },
    "createBackgroundUpload": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "media_type": "text/plain",
        "size_bytes": 24,
        "purpose": "task_output",
    },
    "completeBackgroundUpload": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "sha256": "0" * 64,
    },
    "createBackgroundDownload": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
    },
    "getBackgroundArtifactStatus": lambda gate: {
        "attempt_token": gate.fixtures.get("background_attempt_token") or new_uuid(),
        "artifact_id": gate.fixtures.get("artifact_id") or new_uuid(),
    },
    "streamModelCall": lambda gate: {
        "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
        "operation_id": new_uuid(),
        "logical_key": "f12-gate-stream",
        "capability_version": "1.0",
        "params": {},
        "capability_kind": "model",
    },
}


# ---------------------------------------------------------------------------
# Normal-path probes (only for resources the gate can construct over the API)
# ---------------------------------------------------------------------------
def _normal_liveness(gate: Gate) -> Response:
    return gate.anonymous.get("/health/live")


def _normal_readiness(gate: Gate) -> Response:
    return gate.anonymous.get("/health/ready")


def _normal_login(gate: Gate) -> Response:
    return HttpClient(gate.base_url, timeout=gate.options.timeout).post(
        "/api/v1/auth/login",
        body={"login": gate.options.admin_login, "password": gate.options.admin_password},
    )


def _normal_logout(gate: Gate) -> Response:
    session = gate._login(gate.options.admin_login, gate.options.admin_password)
    return session.client.post("/api/v1/auth/logout", csrf=session.csrf)


def _normal_get_identity(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.get("/api/v1/auth/me")


def _normal_list_applications(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/applications?limit=1")


def _normal_create_application(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        "/api/v1/applications", body={"name": "F12 gate normal application"}, csrf=gate.creator.csrf
    )


def _normal_get_application(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/applications/{gate.fixtures['application_id']}")


def _normal_get_draft(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/applications/{gate.fixtures['application_id']}/draft")


def _normal_save_draft(gate: Gate) -> Response:
    assert gate.creator is not None
    response = gate.creator.client.request(
        "PUT",
        f"/api/v1/applications/{gate.fixtures['application_id']}/draft",
        body=gate._draft_body(),
        csrf=gate.creator.csrf,
        headers={"If-Match": gate.fixtures["draft_etag"]},
    )
    etag = response.header("ETag")
    if response.status == 200 and etag:
        gate.fixtures["draft_etag"] = etag
        try:
            gate.fixtures["draft_revision"] = int(etag.strip('"'))
        except ValueError:
            pass
    return response


def _normal_validate_application(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        f"/api/v1/applications/{gate.fixtures['application_id']}/validate", csrf=gate.creator.csrf
    )


def _normal_debug_draft(gate: Gate) -> Response:
    response, _body, _key = gate._debug_run()
    return response


def _normal_list_runs(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/runs?limit=1")


def _normal_get_run(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/runs/{gate.fixtures['run_id']}")


def _normal_list_run_artifacts(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/runs/{gate.fixtures['run_id']}/artifacts?limit=1")


def _normal_create_conversation(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post("/api/v1/conversations", csrf=gate.creator.csrf)


def _normal_list_conversations(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/conversations?limit=1")


def _normal_get_conversation(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/conversations/{gate.fixtures['conversation_id']}")


def _normal_list_messages(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(
        f"/api/v1/conversations/{gate.fixtures['conversation_id']}/messages?limit=1"
    )


def _normal_submit_request(gate: Gate) -> Response:
    assert gate.creator is not None
    response = gate.creator.client.post(
        "/api/v1/requests",
        body={
            "conversation_id": gate.fixtures.get("conversation_id") or new_uuid(),
            "message": "f12 gate request",
            "artifact_ids": [],
        },
        csrf=gate.creator.csrf,
        headers={"Idempotency-Key": "f12-gate-request-" + uuid.uuid4().hex},
    )
    if response.status == 202:
        gate.fixtures["request_id"] = (response.json or {}).get("id")
    return response


def _normal_get_request(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/requests/{gate.fixtures['request_id']}")


def _normal_cancel_request(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        f"/api/v1/requests/{gate.fixtures['request_id']}/cancel", csrf=gate.creator.csrf
    )


def _normal_create_upload(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        "/api/v1/artifacts/uploads",
        body={"media_type": "text/plain", "size_bytes": 24, "purpose": "task_input"},
        csrf=gate.creator.csrf,
    )


def _normal_complete_upload(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        f"/api/v1/artifacts/{gate.fixtures['artifact_id']}/complete",
        body={"sha256": hashlib.sha256(b"f12-conformance-artifact").hexdigest()},
        csrf=gate.creator.csrf,
    )


def _normal_get_artifact(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/artifacts/{gate.fixtures['artifact_id']}")


def _normal_create_memory(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        "/api/v1/memories",
        body={
            "application_id": gate.fixtures.get("application_id") or new_uuid(),
            "text": "f12 gate normal memory",
            "confirmed": True,
        },
        csrf=gate.creator.csrf,
    )


def _normal_list_memories(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/memories?limit=1")


def _normal_get_memory(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/memories/{gate.fixtures['memory_id']}")


def _normal_update_memory(gate: Gate) -> Response:
    assert gate.creator is not None
    path = f"/api/v1/memories/{gate.fixtures['memory_id']}"
    current = gate.creator.client.get(path)
    etag = current.header("ETag")
    return gate.creator.client.request(
        "PATCH",
        path,
        body={"text": "f12 gate memory updated", "confirmed": True},
        csrf=gate.creator.csrf,
        headers={"If-Match": etag} if etag else {},
    )


def _normal_list_memory_candidates(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/memory-candidates?limit=1")


def _normal_create_knowledge_base(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.post(
        "/api/v1/knowledge-bases", body={"name": "F12 gate normal KB"}, csrf=gate.creator.csrf
    )


def _normal_list_knowledge_bases(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/knowledge-bases?limit=1")


def _normal_get_knowledge_base(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(
        f"/api/v1/knowledge-bases/{gate.fixtures['knowledge_base_id']}"
    )


def _normal_list_knowledge_documents(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(
        f"/api/v1/knowledge-bases/{gate.fixtures['knowledge_base_id']}/documents?limit=1"
    )


def _normal_list_approvals(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/approvals?limit=1")


def _normal_register_model(gate: Gate) -> Response:
    assert gate.admin is not None
    suffix = uuid.uuid4().hex[:8]
    return gate.admin.client.post(
        "/api/v1/models",
        body={
            "name": "F12 gate normal model " + suffix,
            "provider": "stub",
            "model_id": "f12-gate-normal-" + suffix,
            "endpoint_ref": "stub-endpoint",
            "secret_ref": "stub-credential",
            "config": {
                "kind": "generation",
                "protocol": "chat_compatible",
                "context_window": 8192,
                "max_output_tokens": 1024,
                "timeout_ms": 30000,
                "price": {"currency": "USD", "input_per_million": "0.10", "output_per_million": "0.20"},
            },
            "data_destination": "local-stub",
        },
        csrf=gate.admin.csrf,
    )


def _normal_list_models(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.get("/api/v1/models?limit=1")


def _normal_get_model(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.get(f"/api/v1/models/{gate.fixtures['model_version_id']}")


def _normal_validate_model(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.post(
        f"/api/v1/models/{gate.fixtures['model_version_id']}/validate",
        csrf=gate.admin.csrf,
        headers={"Idempotency-Key": "f12-gate-validate-" + uuid.uuid4().hex},
    )


def _normal_list_credential_refs(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.get("/api/v1/admin/credential-refs")


def _normal_list_model_endpoints(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.get("/api/v1/admin/model-endpoints")


def _normal_invite_user(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.post(
        "/api/v1/admin/users",
        body={
            "login": "f12-normal-" + uuid.uuid4().hex[:8],
            "display_name": "F12 Normal",
            "roles": ["user"],
        },
        csrf=gate.admin.csrf,
    )


def _normal_grant_resource(gate: Gate) -> Response:
    assert gate.admin is not None
    response = gate.admin.client.post(
        "/api/v1/admin/grants",
        body={
            "principal_id": gate.member.principal_id if gate.member else new_uuid(),
            "resource_type": "application",
            "resource_id": gate.fixtures["application_id"],
            "action": "read",
        },
        csrf=gate.admin.csrf,
    )
    if response.status in (200, 201) and isinstance(response.json, dict):
        gate.fixtures["resource_grant_id"] = response.json.get("id")
    return response


def _normal_create_service_account(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.post(
        "/api/v1/admin/service-accounts",
        body={
            "display_name": "F12 gate normal service",
            "responsible_user_id": gate.admin.principal_id,
        },
        csrf=gate.admin.csrf,
    )


def _normal_create_api_key(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.post(
        "/api/v1/admin/api-keys",
        body={
            "principal_id": gate.fixtures["service_account_id"],
            "scopes": ["application:read"],
            "expires_at": _future(),
        },
        csrf=gate.admin.csrf,
    )


def _normal_list_tools(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/tools?limit=1")


def _normal_list_reviews(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get("/api/v1/reviews?limit=1")


def _normal_list_versions(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(
        f"/api/v1/applications/{gate.fixtures['application_id']}/versions?limit=1"
    )


def _normal_cancel_run(gate: Gate) -> Response:
    assert gate.creator is not None
    response, _body, _key = gate._debug_run()
    if response.status != 202:
        return response
    run_id = response.json["id"]
    return gate.creator.client.post(f"/api/v1/runs/{run_id}/cancel", csrf=gate.creator.csrf)


def _normal_delete_memory(gate: Gate) -> Response:
    assert gate.creator is not None
    response = gate.creator.client.request(
        "DELETE", f"/api/v1/memories/{gate.fixtures['memory_id']}", csrf=gate.creator.csrf
    )
    if response.status == 202 and (response.json or {}).get("id"):
        gate.fixtures["deletion_id"] = response.json["id"]
    return response


def _normal_get_deletion(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.get(f"/api/v1/deletions/{gate.fixtures['deletion_id']}")


def _normal_delete_conversation(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.request(
        "DELETE", f"/api/v1/conversations/{gate.fixtures['conversation_id']}", csrf=gate.creator.csrf
    )


def _normal_revoke_api_key(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.request(
        "DELETE", f"/api/v1/admin/api-keys/{gate.fixtures['key_id']}", csrf=gate.admin.csrf
    )


def _normal_revoke_grant(gate: Gate) -> Response:
    assert gate.admin is not None
    return gate.admin.client.request(
        "DELETE", f"/api/v1/admin/grants/{gate.fixtures.get('resource_grant_id', new_uuid())}",
        csrf=gate.admin.csrf,
    )


def _normal_revoke_diagnostics(gate: Gate) -> Response:
    assert gate.creator is not None
    return gate.creator.client.request(
        "DELETE", f"/api/v1/diagnostic-grants/{gate.fixtures['grant_id']}", csrf=gate.creator.csrf
    )


def _normal_claim_job(gate: Gate) -> Response:
    _response, _body, _key = gate._debug_run()
    client = gate._internal_client("supervisor")
    response = client.call("POST", "/internal/v1/jobs/claim", body=BODIES["claimJob"](gate))
    if response.status == 200 and isinstance(response.json, dict):
        gate.fixtures["attempt_token"] = response.json.get("attempt_token")
        gate.fixtures["claimed_run_id"] = response.json.get("run_id")
    return response


def _lease_run_id(gate: Gate) -> str:
    return gate.fixtures.get("claimed_run_id") or gate.fixtures["run_id"]


def _normal_heartbeat(gate: Gate) -> Response:
    client = gate._internal_client("worker")
    return client.call(
        "POST",
        f"/internal/v1/runs/{_lease_run_id(gate)}/heartbeat",
        body={
            "attempt_token": gate.fixtures.get("attempt_token") or new_uuid(),
            "execution_unit_id": "f12-gate-unit",
        },
    )


def _normal_submit_worker_events(gate: Gate) -> Response:
    client = gate._internal_client("worker")
    return client.call(
        "POST",
        f"/internal/v1/runs/{_lease_run_id(gate)}/events",
        body=BODIES["submitWorkerEvents"](gate),
    )


def _normal_submit_worker_result(gate: Gate) -> Response:
    client = gate._internal_client("worker")
    return client.call(
        "POST",
        f"/internal/v1/runs/{_lease_run_id(gate)}/result",
        body=BODIES["submitWorkerResult"](gate),
    )


def _normal_claim_background_job(gate: Gate) -> Response:
    client = gate._internal_client("maintenance")
    response = client.call(
        "POST", "/internal/v1/background/jobs/claim", body=BODIES["claimBackgroundJob"](gate)
    )
    if response.status == 200 and isinstance(response.json, dict):
        gate.fixtures["background_attempt_token"] = response.json.get("attempt_token")
    return response


NORMAL_ACTIONS: dict[str, Callable[[Gate], Response]] = {
    "liveness": _normal_liveness,
    "readiness": _normal_readiness,
    "login": _normal_login,
    "logout": _normal_logout,
    "getIdentity": _normal_get_identity,
    "listApplications": _normal_list_applications,
    "createApplication": _normal_create_application,
    "getApplication": _normal_get_application,
    "getDraft": _normal_get_draft,
    "saveDraft": _normal_save_draft,
    "validateApplication": _normal_validate_application,
    "debugDraft": _normal_debug_draft,
    "listRuns": _normal_list_runs,
    "getRun": _normal_get_run,
    "listRunArtifacts": _normal_list_run_artifacts,
    "createConversation": _normal_create_conversation,
    "listConversations": _normal_list_conversations,
    "getConversation": _normal_get_conversation,
    "listMessages": _normal_list_messages,
    "submitRequest": _normal_submit_request,
    "getRequest": _normal_get_request,
    "cancelRequest": _normal_cancel_request,
    "createUpload": _normal_create_upload,
    "completeUpload": _normal_complete_upload,
    "getArtifact": _normal_get_artifact,
    "createMemory": _normal_create_memory,
    "listMemories": _normal_list_memories,
    "getMemory": _normal_get_memory,
    "updateMemory": _normal_update_memory,
    "listMemoryCandidates": _normal_list_memory_candidates,
    "createKnowledgeBase": _normal_create_knowledge_base,
    "listKnowledgeBases": _normal_list_knowledge_bases,
    "getKnowledgeBase": _normal_get_knowledge_base,
    "listKnowledgeDocuments": _normal_list_knowledge_documents,
    "listApprovals": _normal_list_approvals,
    "registerModel": _normal_register_model,
    "listModels": _normal_list_models,
    "getModel": _normal_get_model,
    "validateModel": _normal_validate_model,
    "listCredentialRefs": _normal_list_credential_refs,
    "listModelEndpoints": _normal_list_model_endpoints,
    "inviteUser": _normal_invite_user,
    "grantResource": _normal_grant_resource,
    "createServiceAccount": _normal_create_service_account,
    "createApiKey": _normal_create_api_key,
    "listTools": _normal_list_tools,
    "listPublicationReviews": _normal_list_reviews,
    "listVersions": _normal_list_versions,
    # Ordered after the read probes: these change or destroy the fixtures.
    "claimJob": _normal_claim_job,
    "heartbeat": _normal_heartbeat,
    "submitWorkerEvents": _normal_submit_worker_events,
    "submitWorkerResult": _normal_submit_worker_result,
    "claimBackgroundJob": _normal_claim_background_job,
    "cancelRun": _normal_cancel_run,
    "deleteMemory": _normal_delete_memory,
    "getDeletion": _normal_get_deletion,
    "deleteConversation": _normal_delete_conversation,
    "revokeApiKey": _normal_revoke_api_key,
    "revokeResourceGrant": _normal_revoke_grant,
    "revokeDiagnostics": _normal_revoke_diagnostics,
}

# The execution journey must run in order: claim -> heartbeat -> events -> result.
NORMAL_ORDER: tuple[str, ...] = (
    "liveness",
    "readiness",
    "login",
    "logout",
    "getIdentity",
    "listApplications",
    "createApplication",
    "getApplication",
    "getDraft",
    "saveDraft",
    "validateApplication",
    "debugDraft",
    "listRuns",
    "getRun",
    "listRunArtifacts",
    "createConversation",
    "listConversations",
    "getConversation",
    "listMessages",
    "submitRequest",
    "getRequest",
    "cancelRequest",
    "createUpload",
    "completeUpload",
    "getArtifact",
    "createMemory",
    "listMemories",
    "getMemory",
    "updateMemory",
    "listMemoryCandidates",
    "createKnowledgeBase",
    "listKnowledgeBases",
    "getKnowledgeBase",
    "listKnowledgeDocuments",
    "listApprovals",
    "registerModel",
    "listModels",
    "getModel",
    "validateModel",
    "listCredentialRefs",
    "listModelEndpoints",
    "inviteUser",
    "grantResource",
    "createServiceAccount",
    "createApiKey",
    "listTools",
    "listPublicationReviews",
    "listVersions",
    "claimJob",
    "heartbeat",
    "submitWorkerEvents",
    "submitWorkerResult",
    "claimBackgroundJob",
    "cancelRun",
    "deleteMemory",
    "getDeletion",
    "deleteConversation",
    "revokeApiKey",
    "revokeResourceGrant",
    "revokeDiagnostics",
)

IDEMPOTENCY_REQUIRED: tuple[str, ...] = ()  # derived per Gate from the contract


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _build_options(args: argparse.Namespace) -> GateOptions:
    internal = None
    if args.internal_url:
        if not args.internal_ca or not args.internal_cert_dir:
            raise SystemExit(
                "--internal-url requires --internal-ca and --internal-cert-dir "
                "(the worker/supervisor/maintenance client certificates)"
            )
        internal = InternalOptions(
            url=args.internal_url,
            ca_path=Path(args.internal_ca),
            cert_dir=Path(args.internal_cert_dir),
        )
    return GateOptions(
        base_url=args.base_url,
        admin_login=args.admin_login,
        admin_password=args.admin_password,
        contract_path=Path(args.contract),
        internal=internal,
        expected_operations=args.expected_operations,
        profile=args.profile,
        product_root=Path(args.product_root) if args.product_root else Path(__file__).resolve().parents[1],
        timeout=args.timeout,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True, help="public API base URL of a running cmd/api")
    parser.add_argument("--admin-login", default="admin")
    parser.add_argument("--admin-password", required=True)
    parser.add_argument("--contract", default="contracts/openapi.json")
    parser.add_argument("--expected-operations", type=int, default=DEFAULT_EXPECTED_OPERATIONS)
    parser.add_argument("--internal-url", help="https://host:port of the internal mTLS listener")
    parser.add_argument("--internal-ca", help="PEM CA that signed the internal server/client certs")
    parser.add_argument("--internal-cert-dir", help="directory holding client-<role>.pem / -key.pem")
    parser.add_argument("--profile", default="dev-stub")
    parser.add_argument("--product-root", help="product/ checkout root for the static registry cross-check")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default="reports/f12/T093-api-conformance.json")
    args = parser.parse_args(argv)

    global IDEMPOTENCY_REQUIRED
    options = _build_options(args)
    try:
        operations = load_operations(options.contract_path, options.expected_operations)
    except ManifestError as exc:
        print(f"F12 API GATE REFUSED THE MANIFEST: {exc}", file=sys.stderr)
        return 2
    IDEMPOTENCY_REQUIRED = tuple(
        operation.operation_id
        for operation in operations
        if "Idempotency-Key" in operation.header_params
    )
    options.expected_operations = args.expected_operations

    gate = Gate(options)
    report = gate.run()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = report["summary"]
    print(
        f"F12 API gate: route-registered {summary['route_registered']}/{summary['operations']}, "
        f"functional probe {summary['functional_probe']}/{summary['operations']}, "
        f"route_only {summary['route_only']}, "
        f"{len(summary['invariant_failures'])} invariant failure(s), "
        f"verdict={summary['verdict']}"
    )
    for operation_id in summary["missing_operations"]:
        print(f"  MISSING OPERATION: {operation_id}")
    if summary["route_only_operations"]:
        print(
            "  ROUTE-ONLY (registered but no functional normal-path probe): "
            + ", ".join(summary["route_only_operations"])
        )
    for failure in summary["cross_principal_isolation"]["failures"]:
        print(f"  CROSS-PRINCIPAL ISOLATION FAILED: {failure}")
    for failure in summary["invariant_failures"]:
        print(f"  INVARIANT FAILED: {failure['invariant']}: {failure['failures'][:3]}")
    for record in report["operations"]:
        if record["verdict"] == "fail":
            print(f"  AUTHORIZATION/PROBE FAILED: {record['operation_id']}: {record['failures'][:2]}")
    print(f"report: {out_path}")
    # 0 = every operation has a functional probe; 3 = the gate is honest but
    # functional coverage is incomplete (declared route_only); 1 = a real
    # failure; 2 = the manifest was refused before probing.
    if summary["verdict"] == "passed":
        return 0
    if summary["verdict"] == "route_only":
        return 3
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())


