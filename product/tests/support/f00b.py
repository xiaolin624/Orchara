"""F00B/T022 stack helpers: internal mTLS, artifacts, jobs and the maintenance CLI.

The stack is the real one: a throwaway PostgreSQL database, the compiled
``cmd/api`` listening on public HTTP and (optionally) internal HTTPS with client
certificates, the compiled ``cmd/admin`` and ``cmd/maintenance`` binaries, and an
object-store directory shared by the two processes.

Two boundaries are deliberate and documented in the T022 report/ADR:

* T022 has no upload producer (completeUpload is T024), so artifacts are seeded
  with SQL and their staging bytes are written into the real object store. The
  table, the bytes and the guard are real; only the absent HTTP entry point is.
* The artifact_finalize handler reads its artifact row through the maintenance
  DSN because the job-scoped artifact-status endpoint is T024's contract.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import ssl
import subprocess
import time
import urllib.parse
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from support import stack as st
from support.certs import ClientCertificate, StackCertificates

PRODUCT_ROOT = st.PRODUCT_ROOT

MAX_OBJECT_BYTES = 20 * 1024 * 1024
KIND_ARTIFACT_FINALIZE = "artifact_finalize"
ALL_KINDS = (
    "artifact_finalize",
    "route_request",
    "tool_build",
    "document_ingest",
    "application_evaluate",
    "model_validate",
    "delete_content",
    "catalog_index",
)

ADMIN_LOGIN = "admin"
# Generated per test process, never a fixed credential-shaped literal in the
# repository. The prefix keeps failure messages readable.
ADMIN_PASSWORD = "t022-admin-" + secrets.token_urlsafe(18)
USER_PASSWORD = "t022-user-" + secrets.token_urlsafe(18)

_UNSET = object()


# ---------------------------------------------------------------------------
# stack
# ---------------------------------------------------------------------------
@dataclass
class T022Stack:
    server: st.ApiServer
    dsn: str
    dbname: str
    object_dir: Path
    certificates: StackCertificates
    binaries: dict[str, Path]

    def client(self) -> st.HttpClient:
        return st.HttpClient(self.server.base_url)

    def internal_client(self, role: str = "maintenance") -> "InternalClient":
        return InternalClient(
            self.certificates.internal_url,
            self.certificates.ca.cert,
            self.certificates.client(role),
        )


class Session(NamedTuple):
    client: st.HttpClient
    csrf: str
    principal_id: str
    cookie: str


def api_extra_env(
    object_dir: Path,
    certificates: StackCertificates,
    *,
    retry_backoff: str = "50ms",
) -> dict[str, str]:
    return {
        "ORCHARA_OBJECT_DIR": str(object_dir),
        "ORCHARA_INTERNAL_ADDR": certificates.internal_addr,
        "ORCHARA_INTERNAL_SERVER_CERT": str(certificates.server.cert),
        "ORCHARA_INTERNAL_SERVER_KEY": str(certificates.server.key),
        "ORCHARA_INTERNAL_CLIENT_CA": str(certificates.ca.cert),
        "ORCHARA_BACKGROUND_RETRY_BACKOFF": retry_backoff,
    }


# ---------------------------------------------------------------------------
# identity: real login / invite / activate
# ---------------------------------------------------------------------------
def sign_in(stack: T022Stack, login: str, password: str) -> tuple[st.HttpClient, st.Response]:
    client = stack.client()
    response = client.post("/api/v1/auth/login", body={"login": login, "password": password})
    return client, response


def session_for(stack: T022Stack, login: str, password: str) -> Session:
    client, response = sign_in(stack, login, password)
    assert response.status == 200, response.text
    body = response.json
    assert body["csrf_token"], body
    cookie = client.cookie(st.SESSION_COOKIE)
    assert cookie, "login did not set the session cookie"
    return Session(client, body["csrf_token"], body["principal_id"], cookie)


def admin_session(stack: T022Stack) -> Session:
    return session_for(stack, ADMIN_LOGIN, ADMIN_PASSWORD)


def invite_and_activate(
    stack: T022Stack,
    admin: Session,
    login: str,
    *,
    roles: tuple[str, ...] = ("user",),
    password: str = USER_PASSWORD,
) -> Session:
    invitation = admin.client.post(
        "/api/v1/admin/users",
        body={
            "login": login,
            "display_name": login.replace(".", " ").title(),
            "roles": list(roles),
        },
        csrf=admin.csrf,
    )
    assert invitation.status == 201, invitation.text
    activation = stack.client().post(
        "/api/v1/auth/activate",
        body={"activation_token": invitation.json["activation_token"], "password": password},
    )
    assert activation.status == 200, activation.text
    return session_for(stack, login, password)


# ---------------------------------------------------------------------------
# internal mTLS client
# ---------------------------------------------------------------------------
class InternalClient:
    """A real TLS client with a real client certificate.

    ``client`` may be ``None`` to exercise the "no client certificate" handshake
    refusal. ``header_role`` can override the X-Internal-Role header independently
    of the certificate, which is exactly how the mismatch test is written;
    passing ``None`` omits the header.
    """

    def __init__(self, url: str, ca_path: Path, client: ClientCertificate | None) -> None:
        parsed = urllib.parse.urlsplit(url)
        assert parsed.scheme == "https" and parsed.hostname, url
        self.host = parsed.hostname
        self.port = parsed.port or 443
        self.certificate = client
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.context.load_verify_locations(cafile=str(ca_path))
        if client is not None:
            self.context.load_cert_chain(certfile=str(client.cert), keyfile=str(client.key))
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.check_hostname = True

    def request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        *,
        header_role=_UNSET,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> st.Response:
        request_headers = {"Accept": "application/json"}
        if header_role is _UNSET:
            role = self.certificate.role if self.certificate is not None else None
        else:
            role = header_role
        if role is not None:
            request_headers["X-Internal-Role"] = role
        request_headers.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        connection = http.client.HTTPSConnection(
            self.host, self.port, context=self.context, timeout=timeout
        )
        try:
            connection.request(method, path, body=data, headers=request_headers)
            response = connection.getresponse()
            raw = response.read()
            return st.Response(response.status, response.headers, raw)
        finally:
            connection.close()

    def get(self, path: str, **kwargs) -> st.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, body: dict | None = None, **kwargs) -> st.Response:
        return self.request("POST", path, body=body, **kwargs)

    # -- contract shortcuts -------------------------------------------------
    def claim(self, *, worker_id: str = "t022-maintenance", kinds: list[str] | None = None, **kwargs):
        return self.post(
            "/internal/v1/background/jobs/claim",
            {"worker_id": worker_id, "supported_kinds": list(kinds or [KIND_ARTIFACT_FINALIZE])},
            **kwargs,
        )

    def heartbeat(self, job_id: str, attempt_token: str, **kwargs):
        return self.post(
            f"/internal/v1/background/jobs/{job_id}/heartbeat",
            {"attempt_token": attempt_token},
            **kwargs,
        )

    def result(
        self,
        job_id: str,
        *,
        attempt_token: str,
        state: str,
        event_id: str | None = None,
        error_code: str | None = None,
        result_artifact_id: str | None = None,
        **kwargs,
    ):
        body: dict = {
            "attempt_token": attempt_token,
            "event_id": event_id or new_uuid(),
            "state": state,
        }
        if error_code is not None:
            body["error_code"] = error_code
        if result_artifact_id is not None:
            body["result_artifact_id"] = result_artifact_id
        return self.post(f"/internal/v1/background/jobs/{job_id}/result", body, **kwargs)


def wait_internal(
    url: str,
    ca_path: Path,
    client_cert: ClientCertificate,
    *,
    timeout: float = 20.0,
    logs=None,
) -> None:
    """Wait until the internal listener answers /health/live with a real cert."""
    client = InternalClient(url, ca_path, client_cert)
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            response = client.get("/health/live", timeout=2)
            if response.status == 200:
                return
            last = AssertionError(f"status {response.status}: {response.text}")
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(0.1)
    tail = f"\napi log tail:\n{logs()[-3000:]}" if callable(logs) else ""
    raise AssertionError(f"internal listener never became ready; last error: {last}{tail}")


def wait_for_internal(stack: T022Stack, timeout: float = 20.0) -> None:
    wait_internal(
        stack.certificates.internal_url,
        stack.certificates.ca.cert,
        stack.certificates.client("maintenance"),
        timeout=timeout,
        logs=stack.server.logs,
    )


def assert_handshake_refused(callable_, *, what: str) -> None:
    """A refused TLS handshake must not produce an HTTP response."""
    try:
        response = callable_()
    except (ssl.SSLError, ConnectionError, OSError, http.client.HTTPException):
        return
    raise AssertionError(f"{what} was not refused at the TLS layer; got HTTP {response.status}")


# ---------------------------------------------------------------------------
# cmd/admin background-jobs enqueue
# ---------------------------------------------------------------------------
def enqueue(
    stack: T022Stack,
    *,
    kind: str,
    target_id: str,
    owner_id: str,
    dedup_key: str,
    expected_revision: int | None = None,
    expected_sha256: str | None = None,
    input_artifact_id: str | None = None,
    extra_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [
        "background-jobs",
        "enqueue",
        "--kind",
        kind,
        "--target-id",
        target_id,
        "--owner-id",
        owner_id,
        "--dedup-key",
        dedup_key,
    ]
    if expected_revision is not None:
        args += ["--expected-revision", str(expected_revision)]
    if expected_sha256 is not None:
        args += ["--expected-sha256", expected_sha256]
    if input_artifact_id is not None:
        args += ["--input-artifact-id", input_artifact_id]
    args += list(extra_args or [])
    return st.run_admin(stack.binaries["admin"], args, dsn=stack.dsn)


def enqueue_ok(stack: T022Stack, **kwargs) -> dict:
    completed = enqueue(stack, **kwargs)
    assert completed.returncode == 0, (
        f"admin background-jobs enqueue failed ({completed.returncode}):\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert set(payload) == {"id", "state", "kind"}, payload
    return payload


# ---------------------------------------------------------------------------
# cmd/maintenance run
# ---------------------------------------------------------------------------
def maintenance_env(
    stack: T022Stack,
    *,
    retry_backoff: str = "50ms",
    heartbeat_interval: str = "50ms",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    certificates = stack.certificates
    client = certificates.client("maintenance")
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)  # run uses ORCHARA_MAINTENANCE_DSN only
    env["ORCHARA_MAINTENANCE_DSN"] = stack.dsn
    env["ORCHARA_INTERNAL_URL"] = certificates.internal_url
    env["ORCHARA_INTERNAL_CLIENT_CERT"] = str(client.cert)
    env["ORCHARA_INTERNAL_CLIENT_KEY"] = str(client.key)
    env["ORCHARA_INTERNAL_CA"] = str(certificates.ca.cert)
    env["ORCHARA_OBJECT_DIR"] = str(stack.object_dir)
    env["ORCHARA_BACKGROUND_RETRY_BACKOFF"] = retry_backoff
    env["ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL"] = heartbeat_interval
    if extra:
        env.update(extra)
    return env


def run_maintenance(
    stack: T022Stack,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(stack.binaries["maintenance"]), "run", *args],
        cwd=PRODUCT_ROOT,
        env=env or maintenance_env(stack),
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def parse_json_lines(output: str) -> list[dict]:
    lines = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            lines.append(json.loads(stripped))
    return lines


def maintenance_once(
    stack: T022Stack,
    *args: str,
    env: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    """One deterministic `--once` drain with a single slot per pool.

    T022's tests were written against the single-threaded loop (one claim at a
    time, "the first empty claim exits"). T026 keeps one slot per pool here so
    those ordering assumptions still describe a real process; the deployed
    defaults (system_io=2, work=1) and multi-slot behaviour are exercised by
    ``tests/f00b/test_t026_pool.py``.
    """
    completed = run_maintenance(
        stack,
        "--once",
        *args,
        "--system-io-slots",
        "1",
        "--work-slots",
        "1",
        env=env,
    )
    assert completed.returncode == 0, (
        f"maintenance run --once failed ({completed.returncode}):\n"
        f"{completed.stdout}\n{completed.stderr}"
    )
    return completed, parse_json_lines(completed.stdout)


def job_reports(reports: list[dict]) -> list[dict]:
    return [report for report in reports if "job_id" in report]


def summary_of(reports: list[dict]) -> dict:
    summaries = [report for report in reports if "status" in report]
    assert summaries, f"maintenance never printed a summary: {reports}"
    return summaries[-1]


# ---------------------------------------------------------------------------
# database seeding and assertions
# ---------------------------------------------------------------------------
def new_uuid() -> str:
    return str(uuid.uuid4())


def principal_id_for_login(dbname: str, login: str = ADMIN_LOGIN) -> str:
    return st.sql(
        dbname,
        "SELECT p.id::text FROM platform.principals AS p "
        "JOIN platform.users AS u ON u.principal_id = p.id "
        f"WHERE u.login = {st.quote_literal(login)}",
    )


def seed_principal(dbname: str, display_name: str = "t022-owner") -> str:
    # A CTE keeps the INSERT's command tag out of the returned value (psql prints
    # "INSERT 0 1" after a bare RETURNING statement).
    return st.sql(
        dbname,
        "WITH inserted AS ("
        "INSERT INTO platform.principals (id, kind, display_name) "
        f"VALUES (gen_random_uuid(), 'user', {st.quote_literal(display_name)}) RETURNING id"
        ") SELECT id FROM inserted",
    )


@dataclass(frozen=True)
class SeededArtifact:
    id: str
    object_key: str
    staging_key: str
    expected_sha256: str
    size_bytes: int
    content: bytes

    def sealed_path(self, object_dir: Path) -> Path:
        return object_dir / self.object_key

    def staging_path(self, object_dir: Path) -> Path:
        return object_dir / self.staging_key


def random_object_name() -> str:
    return os.urandom(32).hex()


# The derivation of a finalize attempt's own sealed key. It is the cross-language
# side of objectstore.AttemptSealedKey: the Go test
# TestAttemptSealedKeyIsStableFencedAndASealedKey pins this exact vector, so a
# change to the domain string fails that test instead of silently diverging from
# these helpers.
ATTEMPT_SEALED_DOMAIN = "orchara/objectstore/attempt-sealed/v1"


def attempt_sealed_key(job_id: str, attempt_token: str) -> str:
    """The sealed key one finalize attempt owns: sealed/<hex sha256(domain|job|token)>."""
    material = "\n".join(
        (ATTEMPT_SEALED_DOMAIN, job_id.strip().lower(), attempt_token.strip().lower())
    )
    return "sealed/" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def object_keys(object_dir: Path, prefix: str) -> set[str]:
    """Every real file under one typed store prefix (the test's own view)."""
    directory = object_dir / prefix.rstrip("/")
    if not directory.exists():
        return set()
    return {
        f"{prefix.rstrip('/')}/{entry.name}"
        for entry in directory.iterdir()
        if entry.is_file()
    }


def write_object(object_dir: Path, key: str, content: bytes) -> Path:
    path = object_dir / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def seed_artifact(
    dbname: str,
    object_dir: Path,
    owner_id: str,
    *,
    content: bytes = b"orchara finalize payload\n",
    state: str = "scanning",
    revision: int = 1,
    expected_sha256: str | None = None,
    size_bytes: int | None = None,
    staging: bool = True,
    sealed_content: bytes | None = None,
    deleted: bool = False,
    media_type: str = "text/plain",
    purpose: str = "artifact",
) -> SeededArtifact:
    """Write real staging/sealed bytes and insert a real artifacts row.

    ``expected_sha256``/``size_bytes`` default to the truth about ``content``;
    passing other values writes a row whose declared facts are wrong, which is how
    the mismatch tests are seeded.
    """
    artifact_id = new_uuid()
    staging_name = random_object_name()
    sealed_name = random_object_name()
    staging_key = f"staging/{staging_name}"
    object_key = f"sealed/{sealed_name}"
    actual_sha = hashlib.sha256(content).hexdigest()
    expected = expected_sha256 if expected_sha256 is not None else actual_sha
    size = size_bytes if size_bytes is not None else len(content)

    if staging:
        write_object(object_dir, staging_key, content)
    if sealed_content is not None:
        write_object(object_dir, object_key, sealed_content)

    stored_sha = actual_sha if state == "ready" else None
    st.sql(
        dbname,
        "INSERT INTO platform.artifacts "
        "(id, owner_id, object_key, staging_object_key, expected_sha256, purpose, sha256, "
        " media_type, size_bytes, state, revision, deleted_at) VALUES ("
        f"{st.quote_literal(artifact_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(object_key)}, {st.quote_literal(staging_key)}, "
        f"{st.quote_literal(expected)}, {st.quote_literal(purpose)}, "
        f"{st.quote_literal(stored_sha) if stored_sha else 'NULL'}, "
        f"{st.quote_literal(media_type)}, {int(size)}, {st.quote_literal(state)}, {int(revision)}, "
        f"{'now()' if deleted else 'NULL'})",
    )
    return SeededArtifact(
        id=artifact_id,
        object_key=object_key,
        staging_key=staging_key,
        expected_sha256=expected,
        size_bytes=int(size),
        content=content,
    )


def artifact_row(dbname: str, artifact_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, object_key, staging_object_key, "
        "expected_sha256, sha256, size_bytes, media_type, purpose, state, revision, "
        "deleted_at::text AS deleted_at "
        "FROM platform.artifacts "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def job_row(dbname: str, job_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, owner_id::text AS owner_id, "
        "state, error_code, failure_class, attempt_count, lease_token::text AS lease_token, "
        "lease_deadline::text AS lease_deadline, result_event_id::text AS result_event_id, "
        "result_event_digest, finished_at::text AS finished_at, available_at::text AS available_at, "
        "worker_id, dedup_key, expected_revision, expected_sha256, "
        "input_artifact_id::text AS input_artifact_id, "
        "result_artifact_id::text AS result_artifact_id "
        "FROM platform.background_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def job_count(dbname: str, *, kind: str | None = None) -> int:
    where = f"WHERE kind = {st.quote_literal(kind)}" if kind else ""
    return int(st.sql(dbname, f"SELECT count(*) FROM platform.background_jobs {where}"))


def force_lease_expiry(dbname: str, job_id: str) -> None:
    """Move the lease into the past so the next claim/sweep must recover it."""
    st.sql(
        dbname,
        "UPDATE platform.background_jobs SET lease_deadline = now() - interval '2 seconds' "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )


def set_artifact_state(dbname: str, artifact_id: str, state: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.artifacts SET state = "
        f"{st.quote_literal(state)}, revision = revision + 1 "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


def delete_artifact_row(dbname: str, artifact_id: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.artifacts SET deleted_at = now(), state = 'deleted', revision = revision + 1 "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


def wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02, what: str) -> None:
    """Bounded wait for a server-side transition (small, explicitly bounded).

    Lease expiry and retry delays are judged by the database clock; this helper
    only waits for the *consequence* of a short configured backoff (50ms in
    tests), never guesses the backoff itself.
    """
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            if predicate():
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last error: {last}")
