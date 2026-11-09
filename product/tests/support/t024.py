"""F00B/T024 stack helpers: signed links, job-scoped artifact data, model ledger.

The stack is the real one from T022/T023 (PostgreSQL, compiled binaries, real
HTTP/mTLS) with two T024 additions:

* ``ORCHARA_OBJECT_SIGNING_KEY`` (a per-test random secret, never committed or
  logged) so the API can mint short-lived PUT/GET links;
* helpers that seed a candidate model profile version and a synthetic fixture,
  and speak the five T024 internal endpoints through a real client certificate.

Nothing here fakes the endpoints: bytes move over real HTTP, the maintenance
handler runs as a real process, and the ledger rows are read from PostgreSQL.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from support.runtime_stack import JobCapabilityClient, Stack
from support import f00b
from support import stack as st
from support.certs import StackCertificates

PRODUCT_ROOT = st.PRODUCT_ROOT

MAX_OBJECT_BYTES = 20 * 1024 * 1024
KIND_ARTIFACT_FINALIZE = "artifact_finalize"
KIND_MODEL_VALIDATE = "model_validate"

ADMIN_LOGIN = f00b.ADMIN_LOGIN
ADMIN_PASSWORD = f00b.ADMIN_PASSWORD
USER_PASSWORD = f00b.USER_PASSWORD


def new_signing_key() -> str:
    """A per-process HMAC key, generated at test time and never logged."""
    return "t024-signing-" + secrets.token_urlsafe(48)


def start_second_api(
    api_server_factory,
    stack: "Stack",
    directory: Path,
    *,
    signing_key: str | None,
    link_ttl: str = "10m",
    extra: dict[str, str] | None = None,
):
    """Start another real API over the same database and object store.

    Used to exercise cross-process facts: a token signed with another key, an
    expired link, a server without a signing key, and a server whose environment
    holds a credential reference. Returns ``(server, certificates)``.
    """
    from support.certs import make_stack_certificates, unique_internal_addr

    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(directory, internal_addr)
    env = api_extra_env(
        stack.object_dir,
        certificates,
        signing_key=signing_key,
        link_ttl=link_ttl,
        extra=extra,
    )
    server = api_server_factory(stack.dsn, extra_env=env)
    f00b.wait_internal(
        certificates.internal_url,
        certificates.ca.cert,
        certificates.client("maintenance"),
        logs=server.logs,
    )
    return server, certificates


def api_extra_env(
    object_dir: Path,
    certificates: StackCertificates,
    *,
    signing_key: str | None,
    link_ttl: str = "10m",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = f00b.api_extra_env(object_dir, certificates)
    if signing_key is not None:
        env["ORCHARA_OBJECT_SIGNING_KEY"] = signing_key
    env["ORCHARA_OBJECT_LINK_TTL"] = link_ttl
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------------------
# internal client with the five T024 operations
# ---------------------------------------------------------------------------
T024InternalClient = JobCapabilityClient


# ---------------------------------------------------------------------------
# signed object links: real HTTP without any session
# ---------------------------------------------------------------------------
class LinkResponse:
    def __init__(self, status: int, headers, raw: bytes) -> None:
        self.status = status
        self.headers = headers
        self.raw = raw

    @property
    def json(self):
        return json.loads(self.raw.decode("utf-8")) if self.raw.strip() else None


def _open_absolute(method: str, url: str, body: bytes | None = None, timeout: float = 30.0) -> LinkResponse:
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return LinkResponse(response.status, response.headers, response.read())
    except urllib.error.HTTPError as exc:
        return LinkResponse(exc.code, exc.headers, exc.read())


def put_link(url: str, content: bytes) -> LinkResponse:
    return _open_absolute("PUT", url, content)


def get_link(url: str) -> LinkResponse:
    return _open_absolute("GET", url)


def tamper_token(url: str) -> str:
    """Flip one character of a signed URL's token, keeping it URL-shaped."""
    parsed = urllib.parse.urlsplit(url)
    token = parsed.path.rsplit("/", 1)[-1]
    replacement = "A" if token[-1] != "A" else "B"
    tampered = token[:-1] + replacement
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rsplit("/", 1)[0] + "/" + tampered, "", "")
    )


# ---------------------------------------------------------------------------
# database seeding
# ---------------------------------------------------------------------------
def sha256_hex(content: bytes | str) -> str:
    data = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(data).hexdigest()


def params_digest(params: dict) -> str:
    """The JCS SHA-256 the server records for a logical call's params.

    The params used in these tests are ASCII objects with scalar values, for
    which Python's sorted compact JSON is byte-identical to RFC 8785.
    """
    canonical = json.dumps(params, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256_hex(canonical)


def fixture_bytes(case: str = "ok") -> bytes:
    """A deterministic synthetic model-compatibility fixture (JSON, no user data)."""
    document = {
        "schema": "orchara.model-compat/1",
        "case": case,
        "probe": {"structured_output": True},
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seed_fixture(
    dbname: str,
    object_dir: Path,
    owner_id: str,
    *,
    content: bytes | None = None,
    state: str = "ready",
) -> f00b.SeededArtifact:
    """Register a synthetic model fixture the way this batch's admin path does.

    T024 has no fixture-registration UI (F06 owns it), so the sanctioned
    registration path is the same administrative SQL seeding used for the other
    rows the batch cannot create through a product endpoint. The purpose marker
    ``model_fixture`` is load-bearing: it is deliberately not an upload purpose,
    and both ``background-jobs enqueue`` and the model-calls endpoint refuse a
    business artifact as a model input.
    """
    content = content if content is not None else fixture_bytes()
    return f00b.seed_artifact(
        dbname,
        object_dir,
        owner_id,
        content=content,
        state=state,
        staging=False,
        sealed_content=content if state == "ready" else None,
        media_type="application/json",
        purpose="model_fixture",
    )


def seed_business_artifact(
    dbname: str,
    object_dir: Path,
    owner_id: str,
    *,
    purpose: str = "parsed_data",
    content: bytes | None = None,
) -> f00b.SeededArtifact:
    """A ready artifact whose purpose is a real user-upload purpose.

    It is the negative control for the synthetic-fixture rule: the bytes are
    perfectly readable, but they are business content and must never be sent to a
    model.
    """
    content = content if content is not None else b'{"business":"user data"}'
    return f00b.seed_artifact(
        dbname,
        object_dir,
        owner_id,
        content=content,
        state="ready",
        staging=False,
        sealed_content=content,
        media_type="application/json",
        purpose=purpose,
    )


def seed_candidate(
    dbname: str,
    object_dir: Path,
    owner_id: str,
    *,
    provider: str = "stub",
    model_id: str = "stub-model-1",
    endpoint_ref: str = "env:ORCHARA_MODEL_ENDPOINT",
    secret_ref: str = "env:ORCHARA_MODEL_SECRET",
    validation_state: str = "pending",
    revoked: bool = False,
    config: dict | None = None,
) -> dict:
    """Insert a real candidate row with a real manifest artifact."""
    manifest = f00b.seed_artifact(
        dbname,
        object_dir,
        owner_id,
        content=b'{"manifest":"candidate"}',
        state="ready",
        staging=False,
        sealed_content=b'{"manifest":"candidate"}',
        media_type="application/json",
        purpose="report",
    )
    candidate_id = f00b.new_uuid()
    manifest_sha = sha256_hex(candidate_id + manifest.expected_sha256)
    payload = json.dumps(config or {"context_tokens": 8192}).replace("'", "''")
    st.sql(
        dbname,
        "INSERT INTO platform.model_profile_versions "
        "(id, name, provider, model_id, endpoint_ref, secret_ref, config, data_destination, "
        " manifest_sha256, manifest_artifact_id, validation_state, revoked_at, created_at) VALUES ("
        f"{st.quote_literal(candidate_id)}::uuid, {st.quote_literal('candidate ' + candidate_id[:8])}, "
        f"{st.quote_literal(provider)}, {st.quote_literal(model_id)}, "
        f"{st.quote_literal(endpoint_ref)}, {st.quote_literal(secret_ref)}, "
        f"{st.quote_literal(payload)}::jsonb, 'local-only', "
        f"{st.quote_literal(manifest_sha)}, {st.quote_literal(manifest.id)}::uuid, "
        f"{st.quote_literal(validation_state)}, {'now()' if revoked else 'NULL'}, now())",
    )
    return {
        "id": candidate_id,
        "manifest_artifact_id": manifest.id,
        "manifest_sha256": manifest_sha,
        "provider": provider,
        "validation_state": validation_state,
    }


def owner_id(dbname: str, login: str = ADMIN_LOGIN) -> str:
    return f00b.principal_id_for_login(dbname, login)


@dataclass
class ModelValidateJob:
    job_id: str
    candidate: dict
    fixture: f00b.SeededArtifact
    owner_id: str
    lease: dict

    @property
    def attempt_token(self) -> str:
        return self.lease["attempt_token"]


def prepare_model_validate_job(
    stack: Stack,
    *,
    case: str = "ok",
    fixture_content: bytes | None = None,
    candidate: dict | None = None,
    dedup_key: str | None = None,
) -> ModelValidateJob:
    """Create and claim one real model_validate job for the admin owner."""
    owner = owner_id(stack.dbname)
    candidate = candidate or seed_candidate(stack.dbname, stack.object_dir, owner)
    if fixture_content is None:
        fixture_content = fixture_bytes(case)
    fixture = seed_fixture(stack.dbname, stack.object_dir, owner, content=fixture_content)
    # A case marker travels in the fixture bytes; callers pass content directly
    # when they need a non-default case.
    payload = f00b.enqueue_ok(
        stack,
        kind=KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key=dedup_key or f"model-validate:{fixture.id}",
        input_artifact_id=fixture.id,
    )
    return claim_model_validate(stack, payload["id"], candidate, fixture, owner)


def claim_model_validate(
    stack: Stack,
    job_id: str,
    candidate: dict,
    fixture: f00b.SeededArtifact,
    owner: str,
) -> ModelValidateJob:
    client = stack.internal_client()
    response = client.claim(kinds=[KIND_MODEL_VALIDATE])
    assert response.status == 200, response.text
    lease = response.json
    assert lease["job_id"] == job_id, lease
    assert lease["kind"] == KIND_MODEL_VALIDATE, lease
    return ModelValidateJob(job_id=job_id, candidate=candidate, fixture=fixture, owner_id=owner, lease=lease)


def enqueue_and_claim_kind(stack: Stack, kind: str, **enqueue_kwargs) -> dict:
    payload = f00b.enqueue_ok(stack, kind=kind, **enqueue_kwargs)
    response = stack.internal_client().claim(kinds=[kind])
    assert response.status == 200, response.text
    lease = response.json
    assert lease["job_id"] == payload["id"], lease
    return lease


# ---------------------------------------------------------------------------
# maintenance: two real processes for the two pools
# ---------------------------------------------------------------------------
def run_model_validate_once(
    stack: Stack,
    *,
    extra_env: dict[str, str] | None = None,
    finalizer_timeout: float = 90.0,
) -> tuple[subprocess.CompletedProcess[str], list[dict], str]:
    """Run one model_validate process while a real finalize process serves it.

    T022's maintenance loop is single-threaded, so a handler that waits for its
    own report to be finalized needs a second process. This deliberately mirrors
    the two-pool split the spec requires (artifact_finalize is system_io, not the
    work pool) without inventing a concurrency runtime in T024.
    """
    log_path = Path(stack.server.log_path).parent / "finalizer.log"
    env = f00b.maintenance_env(stack, extra=extra_env)
    log_handle = log_path.open("w", encoding="utf-8", newline="\n")
    finalizer = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            "--kinds",
            KIND_ARTIFACT_FINALIZE,
            "--poll-interval",
            "20ms",
        ],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    try:
        completed = f00b.run_maintenance(
            stack, "--once", "--kinds", KIND_MODEL_VALIDATE, env=env, timeout=int(finalizer_timeout)
        )
    finally:
        finalizer.terminate()
        try:
            finalizer.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - teardown must not mask the result
            finalizer.kill()
            finalizer.wait(timeout=5)
        log_handle.close()
    return completed, f00b.parse_json_lines(completed.stdout), log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# database read models
# ---------------------------------------------------------------------------
def model_calls(dbname: str, job_id: str | None = None) -> list[dict]:
    where = f"WHERE background_job_id = {st.quote_literal(job_id)}::uuid" if job_id else ""
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, owner_id::text AS owner_id, request_id::text AS request_id, "
        "background_job_id::text AS background_job_id, "
        "model_profile_version_id::text AS model_profile_version_id, scope_key, logical_key, "
        "physical_attempt, state, result_artifact_id::text AS result_artifact_id, currency, "
        "reserved_amount::text AS reserved_amount, charged_amount::text AS charged_amount, "
        "tokens_in, tokens_out, params_sha256, created_at::text AS created_at "
        "FROM platform.platform_model_calls " + where + " ORDER BY physical_attempt, created_at",
    )


def candidate_row(dbname: str, candidate_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, provider, model_id, endpoint_ref, secret_ref, config::text AS config, "
        "validation_state, revoked_at::text AS revoked_at "
        "FROM platform.model_profile_versions "
        f"WHERE id = {st.quote_literal(candidate_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def job_result_artifact_id(dbname: str, job_id: str) -> str | None:
    rows = st.sql_rows(
        dbname,
        "SELECT result_artifact_id::text AS result_artifact_id FROM platform.background_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]["result_artifact_id"]


def job_artifact_links(dbname: str, job_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT background_job_id::text AS background_job_id, artifact_id::text AS artifact_id, "
        "relation, purpose FROM platform.background_job_artifacts "
        f"WHERE background_job_id = {st.quote_literal(job_id)}::uuid ORDER BY artifact_id",
    )


def insert_ledger_attempt(
    dbname: str,
    *,
    job_id: str,
    owner_id: str,
    candidate_id: str,
    logical_key: str,
    params_sha256: str,
    physical_attempt: int = 1,
    state: str = "failed",
) -> str:
    """Seed one earlier attempt of the same logical call (a real prior failure)."""
    call_id = f00b.new_uuid()
    st.sql(
        dbname,
        "INSERT INTO platform.platform_model_calls "
        "(id, owner_id, background_job_id, model_profile_version_id, scope_key, logical_key, "
        " physical_attempt, state, currency, reserved_amount, charged_amount, params_sha256) VALUES ("
        f"{st.quote_literal(call_id)}::uuid, {st.quote_literal(owner_id)}::uuid, "
        f"{st.quote_literal(job_id)}::uuid, {st.quote_literal(candidate_id)}::uuid, "
        f"{st.quote_literal(job_id)}, {st.quote_literal(logical_key)}, {int(physical_attempt)}, "
        f"{st.quote_literal(state)}, 'USD', 0.00010000, 0, {st.quote_literal(params_sha256)})",
    )
    return call_id


def wait_for_job_state(dbname: str, job_id: str, states: set[str], *, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = f00b.job_row(dbname, job_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: {last}")


def sealed_path_for(dbname: str, object_dir: Path, artifact_id: str) -> Path:
    row = f00b.artifact_row(dbname, artifact_id)
    return object_dir / row["object_key"]
