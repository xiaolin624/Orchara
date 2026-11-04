"""Real PostgreSQL, real binaries and real HTTP primitives (shared support).

Adapted from ``tests/f01/conftest.py`` so the F00B tests can use the same seams
without importing F01's conftest module (pytest gives every ``conftest`` the same
top-level module name; importing across test directories is fragile).

* SQL runs through the real ``psql`` client against a throwaway database on the
  isolated development cluster (127.0.0.1:55432).
* Go binaries are built once per session and started as real processes.
* HTTP responses are returned for every status, with cookie support and no
  redirect following.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import NamedTuple

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PRODUCT_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

import pg_dev  # noqa: E402
import toolchain  # noqa: E402

GO = toolchain.go_binary()
WEB_DIR = PRODUCT_ROOT / "apps" / "web" / "src"
SESSION_COOKIE = "platform_session"


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
    """Run one SQL string through psql and return the raw result."""
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
    """Run a query and return its rows as JSON objects."""
    payload = sql(database, f"SELECT coalesce(json_agg(t), '[]'::json) FROM ({query}) AS t")
    return json.loads(payload)


def sql_scalar(database: str, query: str) -> str:
    return sql(database, query)


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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


def create_database(prefix: str) -> FreshDatabase:
    if not pg_dev.is_running():
        raise RuntimeError("isolated development cluster is not running (run tools/pg_dev.py init)")
    name = prefix + uuid.uuid4().hex[:12]
    pg_dev.run_sql(f'CREATE DATABASE "{name}"', database="postgres")
    return FreshDatabase(dsn=pg_dev.dsn(name), name=name)


def drop_database(name: str) -> None:
    if not pg_dev.is_running():
        return
    try:
        pg_dev.run_sql(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)', database="postgres")
    except SystemExit:  # pragma: no cover - teardown must not mask the real failure
        pass


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
        return None


class HttpClient:
    """Cookie-aware HTTP client that returns every response, including errors."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect()
        )
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


def assert_error(response: Response, status: int, code: str) -> dict:
    assert response.status == status, f"expected {status}, got {response.status}: {response.text}"
    body = response.json
    assert isinstance(body, dict) and "error" in body, response.text
    error = body["error"]
    assert set(error) >= {"code", "message", "request_id"}, error
    assert error["code"] == code, error
    assert isinstance(error["request_id"], str) and error["request_id"], error
    return error


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout: float = 30.0) -> None:
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


def sha256_hex(value: str | bytes) -> str:
    import hashlib

    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# compiled Go binaries
# ---------------------------------------------------------------------------
def build_go_binaries(out_dir: Path, packages: dict[str, str]) -> dict[str, Path]:
    """Build each ``{name: package}`` into out_dir; fail loudly on a build error."""
    if not Path(GO).exists():
        raise RuntimeError("Go toolchain not available")
    out_dir.mkdir(parents=True, exist_ok=True)
    binaries: dict[str, Path] = {}
    for name, package in packages.items():
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
            raise AssertionError(f"go build {package} failed:\n{completed.stdout}\n{completed.stderr}")
        binaries[name] = binary
    return binaries


def admin_env(dsn: str | None, profile: str = "dev-stub") -> dict[str, str]:
    """A clean environment for a product process: no host ORCHARA_* leaks in."""
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("ORCHARA_"):
            del env[key]
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
    return subprocess.run(
        [str(admin_binary), *args],
        cwd=PRODUCT_ROOT,
        env=admin_env(dsn, profile),
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def migrate_or_fail(admin_binary: Path, dsn: str) -> str:
    completed = run_admin(admin_binary, ["migrate"], dsn=dsn)
    if completed.returncode != 0:
        raise AssertionError(
            f"admin migrate failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    return (completed.stdout + completed.stderr).strip()


def migration_chain(dbname: str) -> dict[str, str]:
    """The applied migration ledger keyed by version.

    The only stable chain invariant is continuity: a fresh database applies
    0001, 0002, ... with no gap and exactly one row per version. A pinned
    ``ends at 00NN`` list rots the moment a later ticket appends a migration, so
    a ticket asserts its own migration name at its own version instead of the
    transient tail.
    """
    rows = sql_rows(
        dbname,
        "SELECT version, name FROM platform.schema_migrations ORDER BY version",
    )
    versions = [row["version"] for row in rows]
    assert versions == [
        f"{number:04d}" for number in range(1, len(versions) + 1)
    ], versions
    return {row["version"]: row["name"] for row in rows}


def bootstrap_or_fail(
    admin_binary: Path,
    dsn: str,
    password: str,
    *,
    login: str = "admin",
    display_name: str = "Platform Admin",
) -> str:
    completed = run_admin(
        admin_binary,
        ["bootstrap", "--login", login, "--display-name", display_name],
        dsn=dsn,
        stdin_text=password + "\n",
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"admin bootstrap failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
        )
    return (completed.stdout + completed.stderr).strip()


# ---------------------------------------------------------------------------
# API process
# ---------------------------------------------------------------------------
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
        except FileNotFoundError:  # pragma: no cover
            return ""

    def wait_for_log(self, needle: str, timeout: float = 10.0) -> str:
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


def start_api(
    api_binary: Path,
    dsn: str,
    *,
    web_dir: Path = WEB_DIR,
    profile: str = "dev-stub",
    extra_env: dict[str, str] | None = None,
    log_path: Path,
    port: int | None = None,
) -> ApiServer:
    port = port or free_port()
    base_url = f"http://127.0.0.1:{port}"
    log_handle = log_path.open("w", encoding="utf-8", newline="\n")
    env = admin_env(dsn, profile)
    env["ORCHARA_HTTP_ADDR"] = f"127.0.0.1:{port}"
    env["ORCHARA_WEB_DIR"] = str(web_dir)
    if extra_env:
        env.update(extra_env)
    process = subprocess.Popen(
        [str(api_binary)],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    server = ApiServer(process, base_url, log_path, dsn)
    try:
        wait_for_http(f"{base_url}/health/live")
    except Exception:
        log_handle.flush()
        server.stop()
        raise
    finally:
        # The child keeps its own descriptor; closing ours does not affect it.
        log_handle.close()
    return server
