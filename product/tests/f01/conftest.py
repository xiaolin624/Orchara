"""Real-PostgreSQL, real-binary fixtures for the F01 identity tickets.

Nothing here is mocked:

* every migration run executes the compiled ``cmd/admin`` binary against a
  throwaway database created with ``CREATE DATABASE`` on the isolated
  development cluster (127.0.0.1:55432);
* every HTTP assertion talks to a compiled ``cmd/api`` process over a real
  socket, with its stdout+stderr captured to a file so secret-leak assertions
  can inspect the actual log;
* SQL is executed through the real ``psql`` client.

Seams under test (fixed by DESIGN.md §7, so they are not re-negotiated per
ticket): the ``cmd/admin`` CLI, the HTTP contract in ``contracts/openapi.json``,
the PostgreSQL schema/privileges, and the static pages served from
``ORCHARA_WEB_DIR``.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PRODUCT_ROOT.parent
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402
import toolchain  # noqa: E402

GO = toolchain.go_binary()
WEB_DIR = PRODUCT_ROOT / "apps" / "web" / "src"
SESSION_COOKIE = "platform_session"

# ---------------------------------------------------------------------------
# page credential guard: fail closed, never "zero keys extracted so pass"
# ---------------------------------------------------------------------------
_QUERY_PARAMETER_NAMES = {"error", "run", "version"}
_GET_CALL_RE = re.compile(r"""\.get\(\s*(['"])(?P<key>[^'"\s]+)\1\s*\)""")
_URLSEARCH_CALL_RE = re.compile(r"new\s+URLSearchParams\s*\((?P<argument>[^)]*)\)")
_URLSEARCH_OBJECT_RE = re.compile(r"\{(?P<body>[^}]*)\}")
_URLSEARCH_STRING_RE = re.compile(r"""^\s*(['"])(?P<query>[^'"]*)\1\s*$""")
_OBJECT_PROPERTY_RE = re.compile(r"""(?:^|,)\s*['"]?(?P<key>[A-Za-z0-9_]+)['"]?\s*:""")


def query_parameter_keys(body: str) -> tuple[set[str], bool]:
    """Return (keys read from a URL query, whether an unparsed query read exists).

    The guard accepts both quote styles and both idioms
    (``new URLSearchParams(location.search).get('x')`` and
    ``new URL(...).searchParams.get("x")``). A query read whose key cannot be
    extracted (a variable key, an unknown argument) is reported as unparsed so
    the caller fails closed instead of silently checking an empty set.
    """
    keys = {match.group("key") for match in _GET_CALL_RE.finditer(body)}
    unparsed = False

    for match in _URLSEARCH_CALL_RE.finditer(body):
        argument = match.group("argument").strip()
        if not argument:
            # A local builder, e.g. ``const query = new URLSearchParams();
            # query.set('limit', ...)``: it does not read the address bar.
            continue
        string_literal = _URLSEARCH_STRING_RE.match(argument)
        if string_literal is not None:
            query = string_literal.group("query")
            keys.update(
                part.split("=", 1)[0]
                for part in query.split("&")
                if part and "=" in part
            )
            continue
        object_literal = _URLSEARCH_OBJECT_RE.fullmatch(argument)
        if object_literal is not None:
            keys.update(
                key
                for key in _OBJECT_PROPERTY_RE.findall(object_literal.group("body"))
            )
            continue
        if "location.search" in argument or ".searchParams" in argument:
            # The address-bar read; its keys come from the matching .get(...).
            continue
        # An argument we cannot parse. ``location.search`` / ``.searchParams``
        # are already covered by the caller; any other expression may still be a
        # query read, so fail closed.
        unparsed = True

    if "location.search" in body or ".searchParams" in body:
        # The read is definitely from the address bar. If no key came out, the
        # guard cannot prove which parameters are allowed.
        if not keys:
            unparsed = True
    return keys, unparsed


def assert_no_token_in_url_or_storage(body: str, page: str) -> None:
    """No credential may ride in a URL or sit in client storage.

    The pages do read a few *non-secret* query parameters: a run id (the chat
    page's run deep link), a fixed application version, and the stable code that
    tells the login page why the previous session ended. The allowlist is
    checked fail-closed: a query read with no extractable key (double quotes, a
    ``searchParams`` accessor, or a key held in a variable) fails instead of
    passing over an empty set.
    """
    for forbidden in ("localStorage", "sessionStorage"):
        assert forbidden not in body, f"{page} uses {forbidden}"
    queried, unparsed = query_parameter_keys(body)
    assert not unparsed, (
        f"{page} reads a URL query parameter but the guard could not extract its key; "
        "fail closed rather than admit an unrecognised parameter"
    )
    assert queried <= _QUERY_PARAMETER_NAMES, (
        f"{page} reads unexpected query parameters: "
        f"{sorted(queried - _QUERY_PARAMETER_NAMES)}"
    )
    assert "?token=" not in body, f"{page} puts a token in a URL"
    assert "?csrf" not in body, f"{page} puts a CSRF token in a URL"


# ---------------------------------------------------------------------------
# psql helpers: never a password on argv, always a real client
# ---------------------------------------------------------------------------
class PsqlResult(NamedTuple):
    code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()


def psql(
    database: str,
    query: str,
    *,
    user: str | None = None,
    password: str | None = None,
    timeout: int = 120,
) -> PsqlResult:
    """Run one SQL string through psql and return the raw result.

    The password travels in ``PGPASSWORD`` (environment), never in argv, so a
    failing assertion can print the command line without leaking a credential.
    """
    user = user or pg_dev.DEFAULT_USER
    password = password if password is not None else pg_dev.load_secret()
    env = dict(os.environ)
    env["PGPASSWORD"] = password
    completed = subprocess.run(
        [
            pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(pg_dev.DEFAULT_PORT),
            "-U",
            user,
            "-d",
            database,
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            query,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )
    return PsqlResult(completed.returncode, completed.stdout, completed.stderr)


def sql(database: str, query: str) -> str:
    """Run SQL as the owner and return trimmed stdout, failing the test on error."""
    result = psql(database, query)
    if result.code != 0:
        raise AssertionError(f"SQL failed: {query}\n{result.output}")
    return result.stdout.strip()


def sql_rows(database: str, query: str) -> list[dict]:
    """Run a query and return its rows as JSON objects (column order preserved)."""
    payload = sql(database, f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({query}) AS t")
    return json.loads(payload)


def sql_scalar(database: str, query: str) -> str:
    return sql(database, query)


def worker_password() -> str:
    """The restricted runtime role's password (derived locally, never committed)."""
    return pg_dev.load_worker_secret()


def set_worker_password(database: str) -> None:
    """Give ``orchara_worker`` a login password so denial tests can connect as it."""
    sql(database, f"ALTER ROLE orchara_worker PASSWORD {pg_dev.quote_literal(worker_password())}")


# ---------------------------------------------------------------------------
# throwaway databases
# ---------------------------------------------------------------------------
class FreshDatabase(NamedTuple):
    dsn: str
    name: str


def _drop_database(name: str) -> None:
    if not pg_dev.is_running():
        return
    try:
        # FORCE terminates leftover sessions (the API process is stopped by its
        # fixture first, but a failed test must not poison teardown).
        pg_dev.run_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', database="postgres")
    except SystemExit:  # pragma: no cover - teardown must not mask the real failure
        pass


@pytest.fixture
def fresh_database() -> Iterator[FreshDatabase]:
    """An empty database created with ``CREATE DATABASE``; dropped afterwards."""
    if not pg_dev.is_running():
        pytest.skip("isolated development cluster is not running (run tools/pg_dev.py init)")
    name = "orchara_f01_" + uuid.uuid4().hex[:12]
    pg_dev.run_sql(f'CREATE DATABASE "{name}"', database="postgres")
    try:
        yield FreshDatabase(dsn=pg_dev.dsn(name), name=name)
    finally:
        _drop_database(name)


# ---------------------------------------------------------------------------
# compiled Go binaries (built once per session)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def go_binaries(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    if not Path(GO).exists():
        pytest.skip("Go toolchain not available")
    out_dir = tmp_path_factory.mktemp("f01-go-bin")
    binaries: dict[str, Path] = {}
    for name, package in (("api", "./cmd/api"), ("admin", "./cmd/admin")):
        suffix = ".exe" if sys.platform == "win32" else ""
        binary = out_dir / f"orchara-{name}{suffix}"
        completed = subprocess.run(
            [GO, "build", "-o", str(binary), package],
            cwd=PRODUCT_ROOT,
            env=toolchain.go_env(),
            capture_output=True,
            text=True,
            timeout=600,
        )
        if completed.returncode != 0:
            pytest.fail(f"go build {package} failed:\n{completed.stdout}\n{completed.stderr}")
        binaries[name] = binary
    return binaries


def _admin_env(dsn: str | None, profile: str = "dev-stub") -> dict[str, str]:
    env = dict(os.environ)
    # A leftover ORCHARA_* from the host must not change what is exercised here.
    for key in ("ORCHARA_DB_DSN", "ORCHARA_HTTP_ADDR", "ORCHARA_WEB_DIR"):
        env.pop(key, None)
    env["ORCHARA_PROFILE"] = profile
    if dsn is not None:
        env["ORCHARA_DB_DSN"] = dsn
    return env


def run_admin(
    admin_binary: Path,
    args: list[str],
    *,
    dsn: str | None = None,
    profile: str = "dev-stub",
    stdin_text: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    """Run cmd/admin. The DSN is passed in the environment, never on argv."""
    return subprocess.run(
        [str(admin_binary), *args],
        cwd=PRODUCT_ROOT,
        env=_admin_env(dsn, profile),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_migrate(
    admin_binary: Path,
    dsn: str,
    *,
    migrations_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args = ["migrate"]
    if migrations_dir is not None:
        args.extend(["--migrations-dir", str(migrations_dir)])
    return run_admin(admin_binary, args, dsn=dsn)


def migrate_or_fail(admin_binary: Path, dsn: str, *, migrations_dir: Path | None = None) -> str:
    """Apply migrations and return the raw output; a non-zero exit fails the test."""
    completed = run_migrate(admin_binary, dsn, migrations_dir=migrations_dir)
    if completed.returncode != 0:
        raise AssertionError(
            f"admin migrate failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    return (completed.stdout + completed.stderr).strip()


def run_bootstrap(
    admin_binary: Path,
    dsn: str,
    password: str,
    *,
    login: str = "admin",
    display_name: str = "Platform Admin",
) -> subprocess.CompletedProcess[str]:
    return run_admin(
        admin_binary,
        ["bootstrap", "--login", login, "--display-name", display_name],
        dsn=dsn,
        stdin_text=password + "\n",
    )


def popen_admin(
    admin_binary: Path,
    args: list[str],
    *,
    dsn: str,
    profile: str = "dev-stub",
) -> subprocess.Popen:
    """Start cmd/admin without waiting: used to prove concurrent-runner behaviour."""
    return subprocess.Popen(
        [str(admin_binary), *args],
        cwd=PRODUCT_ROOT,
        env=_admin_env(dsn, profile),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def bootstrap_or_fail(admin_binary: Path, dsn: str, password: str, **kwargs) -> str:
    completed = run_bootstrap(admin_binary, dsn, password, **kwargs)
    if completed.returncode != 0:
        raise AssertionError(
            f"admin bootstrap failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    return (completed.stdout + completed.stderr).strip()


# ---------------------------------------------------------------------------
# HTTP: cookie aware, redirects are never followed
# ---------------------------------------------------------------------------
class Response:
    def __init__(self, status: int, headers, raw: bytes) -> None:
        self.status = status
        self.headers = headers
        self.raw = raw

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", errors="replace")

    @property
    def json(self):
        if not self.text.strip():
            return None
        return json.loads(self.text)

    def header(self, name: str) -> str | None:
        return self.headers.get(name)

    def header_values(self, name: str) -> list[str]:
        return self.headers.get_all(name) or []


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        # A redirect on an auth endpoint is a bug; surface the 3xx instead.
        return None


class HttpClient:
    """Cookie-aware HTTP client that returns every response, including errors."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )
        # Audit trail of (method, path): proves no request carried a secret in
        # its URL and lets tests assert what the API's own log should show.
        self.requests: list[tuple[str, str]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        raw_body: str | bytes | None = None,
        csrf: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        if not path.startswith("/"):
            path = "/" + path
        self.requests.append((method, path))
        request_headers = dict(headers or {})
        data = None
        if raw_body is not None:
            data = raw_body.encode("utf-8") if isinstance(raw_body, str) else raw_body
            request_headers.setdefault("Content-Type", "application/json")
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        if csrf is not None:
            request_headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=request_headers, method=method
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                return Response(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return Response(exc.code, exc.headers, exc.read())

    def get(self, path: str, **kwargs) -> Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> Response:
        return self.request("POST", path, **kwargs)

    def cookie(self, name: str = SESSION_COOKIE) -> str | None:
        for cookie in self.jar:
            if cookie.name == name:
                return cookie.value
        return None


# ---------------------------------------------------------------------------
# API process
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return
        except Exception as exc:  # noqa: BLE001 - retried until the deadline
            last = exc
            time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {url}; last error: {last}")


class ApiServer:
    def __init__(self, process: subprocess.Popen, base_url: str, log_path: Path, dsn: str) -> None:
        self.process = process
        self.base_url = base_url
        self.log_path = log_path
        self.dsn = dsn
        self.http = HttpClient(base_url)

    def logs(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:  # pragma: no cover - the file is created before spawn
            return ""

    def wait_for_log(self, needle: str, timeout: float = 10.0) -> str:
        """Wait until the captured log contains ``needle`` and return the log."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            text = self.logs()
            if needle in text:
                return text
            time.sleep(0.1)
        raise AssertionError(f"log never contained {needle!r}; log tail:\n{self.logs()[-2000:]}")

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self.process.kill()
                self.process.wait(timeout=5)


@pytest.fixture
def api_server(tmp_path: Path, go_binaries: dict[str, Path]):
    """Factory fixture: ``api_server(dsn)`` starts a real API and cleans it up."""
    started: list[ApiServer] = []

    def _start(
        dsn: str,
        *,
        web_dir: Path = WEB_DIR,
        profile: str = "dev-stub",
        extra_env: dict[str, str] | None = None,
    ) -> ApiServer:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        log_path = tmp_path / f"api-{len(started)}.log"
        log_handle = log_path.open("w", encoding="utf-8", newline="\n")
        env = _admin_env(dsn, profile)
        env["ORCHARA_HTTP_ADDR"] = f"127.0.0.1:{port}"
        env["ORCHARA_WEB_DIR"] = str(web_dir)
        if extra_env:
            env.update(extra_env)
        process = subprocess.Popen(
            [str(go_binaries["api"])],
            cwd=PRODUCT_ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        server = ApiServer(process, base_url, log_path, dsn)
        started.append(server)
        try:
            _wait_for_http(f"{base_url}/health/live")
        except Exception:
            log_handle.flush()
            server.stop()
            raise
        finally:
            # The child keeps its own descriptor; closing ours does not affect it.
            log_handle.close()
        return server

    yield _start

    for server in reversed(started):
        server.stop()


# ---------------------------------------------------------------------------
# small assertions helpers used by several tests
# ---------------------------------------------------------------------------
def assert_error(response: Response, status: int, code: str) -> dict:
    assert response.status == status, f"expected {status}, got {response.status}: {response.text}"
    body = response.json
    assert isinstance(body, dict) and "error" in body, response.text
    error = body["error"]
    assert set(error) >= {"code", "message", "request_id"}, error
    assert error["code"] == code, error
    assert isinstance(error["request_id"], str) and error["request_id"], error
    return error


def cookie_attributes(set_cookie: str) -> dict[str, str]:
    """Parse one Set-Cookie header into {lowercased attribute: value}."""
    parts = [part.strip() for part in set_cookie.split(";")]
    attributes: dict[str, str] = {}
    name, _, value = parts[0].partition("=")
    attributes["name"] = name
    attributes["value"] = value
    for part in parts[1:]:
        key, _, attr_value = part.partition("=")
        attributes[key.strip().lower()] = attr_value
    return attributes


def sha256_hex(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()
