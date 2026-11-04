#!/usr/bin/env python3
"""Reproducible clean development-environment bring-up for Orchara (F15A/T057).

What this delivers
------------------
One deployer-facing entry that, on a machine with the user-space PostgreSQL
cluster, Go, Python and Node available, performs the documented sequence against
a **fresh database**:

    admin migrate -> admin bootstrap -> cmd/api (Web pages) -> cmd/maintenance run

It prints every command it executes (with credentials redacted), writes process
logs under the dev state root, and waits for ``/health/ready`` before it declares
the environment up. ``bringup.py smoke`` additionally runs the F15A sales /
failure / cancel / model-profile smoke through the public HTTP API and writes the
machine-readable run record to ``product/reports/f15a/``.

Honest boundaries (see ``deploy/dev/README.md``)
-----------------------------------------------
There is no Docker/Compose, Redis or S3 on the reference environment and no real
model credentials, so the deployment profile is ``dev-stub``. ``cmd/worker`` is a
liveness-only skeleton that claims no work: the real asynchronous loops are
``cmd/maintenance run`` (started here) and ``cmd/supervisor`` (only needed by the
gVisor debug path, which this smoke does not exercise). Container deployment,
formal isolation and a real model profile are blocked, not faked.

The object store is the platform's real local-FS implementation (not an S3
compatibility claim), object links are real HMAC-signed URLs, and the internal
listener really requires mutual TLS.
"""

from __future__ import annotations

import argparse
import atexit
import datetime as dt
import hashlib
import json
import os
import platform as platform_module
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PRODUCT_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
DEPLOY_DEV = Path(__file__).resolve().parent
for entry in (str(TOOLS_ROOT), str(DEPLOY_DEV)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import dev_http  # noqa: E402
import dev_pki  # noqa: E402
import pg_dev  # noqa: E402
import toolchain  # noqa: E402

PROFILE = "dev-stub"
SUPPORTED_PROFILES = ("dev-stub",)
ADMIN_LOGIN = "admin"
ADMIN_DISPLAY_NAME = "Platform Admin"
ADMIN_PASSWORD_ENV = "ORCHARA_DEV_ADMIN_PASSWORD"
DEFAULT_STATE_ROOT = PRODUCT_ROOT / ".devstack"
DEFAULT_DATABASE = "orchara_dev"
WEB_DIR = PRODUCT_ROOT / "apps" / "web" / "src"
RUNTIME_LOCK = PRODUCT_ROOT / "probes" / "agent_min" / "requirements.lock.txt"
EXAMPLES = REPO_ROOT / "examples" / "sales"

# No worker image has been built in this batch (F15A owns the clean-env smoke,
# not image construction). Debug runs still require the deployment input; the
# value is an explicit placeholder recorded as such in the evidence.
PLACEHOLDER_WORKER_IMAGE_DIGEST = "sha256:" + "a" * 64

PACKAGES = {
    "api": "./cmd/api",
    "admin": "./cmd/admin",
    "maintenance": "./cmd/maintenance",
    "supervisor": "./cmd/supervisor",
}

# Environment variables whose values are credentials or embed credentials.
SECRET_ENV_KEYS = {
    "ORCHARA_DB_DSN",
    "ORCHARA_MAINTENANCE_DSN",
    "ORCHARA_WORKER_DSN",
    "ORCHARA_OBJECT_SIGNING_KEY",
    "ORCHARA_MODEL_SECRET_REF",
}


class BringupError(RuntimeError):
    """A bring-up step failed; the message is safe to print (no credentials)."""


# ---------------------------------------------------------------------------
# command log
# ---------------------------------------------------------------------------
def _display(argv: list[str]) -> str:
    def quote(part: str) -> str:
        if not part or any(character in part for character in ' \t"'):
            return '"' + part.replace('"', '\\"') + '"'
        return part

    return " ".join(quote(str(part)) for part in argv)


class CommandLog:
    """Prints each command before running it and keeps a redacted record."""

    def __init__(self, out=print) -> None:
        self.out = out
        self.commands: list[dict] = []

    def record(
        self,
        step: str,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        note: str = "",
        cwd: Path | None = None,
    ) -> dict:
        public_env = {}
        for key, value in (env or {}).items():
            if not key.startswith("ORCHARA_"):
                continue
            if key in SECRET_ENV_KEYS:
                public_env[key] = "<redacted>"
            else:
                public_env[key] = value
        entry = {
            "step": step,
            "argv": [str(part) for part in argv],
            "cwd": str(cwd or PRODUCT_ROOT),
            "env": public_env,
            "note": note,
        }
        self.commands.append(entry)
        self.out(f"$ {_display([str(part) for part in argv])}")
        if cwd is not None and Path(cwd) != PRODUCT_ROOT:
            self.out(f"  # cwd: {cwd}")
        if note:
            self.out(f"  # {note}")
        for key, value in public_env.items():
            self.out(f"  # env {key}={value}")
        return entry


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def clean_env() -> dict[str, str]:
    """A process environment with every inherited ORCHARA_* variable removed."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("ORCHARA_")}
    return env


def _python() -> str:
    return sys.executable


def _exe_suffix() -> str:
    return ".exe" if sys.platform == "win32" else ""


def _run(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path = PRODUCT_ROOT,
    stdin_text: str | None = None,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=stdin_text,
        stdin=None if stdin_text is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _require_password(password: str | None, *, confirm: bool = False) -> str:
    if password:
        value = password
    else:
        value = os.environ.get(ADMIN_PASSWORD_ENV) or ""
        if not value:
            if not sys.stdin.isatty():
                raise BringupError(
                    f"no administrator password: set {ADMIN_PASSWORD_ENV} or pass "
                    "--admin-password-env; bootstrap reads it from hidden stdin, never argv"
                )
            import getpass

            value = getpass.getpass("administrator password (hidden): ")
            if confirm and getpass.getpass("confirm password: ") != value:
                raise BringupError("the administrator passwords do not match")
    if len(value) < 12:
        raise BringupError("the administrator password must be at least 12 characters (identity policy)")
    return value


def _log_tail(path: Path, lines: int = 40) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# the dev stack
# ---------------------------------------------------------------------------
class DevStack:
    """A real, running development deployment started from a clean database."""

    def __init__(self) -> None:
        self.state_root: Path
        self.logs_dir: Path
        self.bin_dir: Path
        self.certs_dir: Path
        self.object_dir: Path
        self.dbname = ""
        self.dsn = ""
        self.db_server_version = ""
        self.profile = PROFILE
        self.admin_login = ADMIN_LOGIN
        self.admin_password = ""
        self.bootstrapped = False
        self.http_addr = ""
        self.base_url = ""
        self.internal_addr = ""
        self.internal_url = ""
        self.certificates: dev_pki.DevCertificates | None = None
        self.signing_key = ""
        self.signing_key_path: Path
        self.binaries: dict[str, Path] = {}
        self.worker_image_digest = PLACEHOLDER_WORKER_IMAGE_DIGEST
        self.runtime_lock = RUNTIME_LOCK
        self.runtime_lock_sha256 = ""
        self.api: subprocess.Popen | None = None
        self.maintenance: subprocess.Popen | None = None
        self.commands = CommandLog()
        self.http = dev_http.HttpClient("http://127.0.0.1:1")
        self.started_at = ""
        self._stopped = False

    # -- lifecycle ----------------------------------------------------------
    @classmethod
    def start(
        cls,
        *,
        state_root: Path | str | None = None,
        database: str = DEFAULT_DATABASE,
        fresh: bool = False,
        admin_password: str | None = None,
        admin_login: str = ADMIN_LOGIN,
        profile: str = PROFILE,
        build: bool = True,
        public_port: int | None = None,
        out=print,
    ) -> "DevStack":
        if profile not in SUPPORTED_PROFILES:
            raise BringupError(
                f"profile {profile!r} is not usable on this deployment: only 'dev-stub' "
                "can be started without model credentials or an accepted Linux isolation host "
                f"(blocked: model_credentials, linux_isolation; requested {profile!r})"
            )
        if not database.replace("_", "").isalnum() or not database.islower():
            raise BringupError("--database must be lowercase letters, digits and underscores")
        stack = cls()
        stack.profile = profile
        stack.admin_login = admin_login
        stack.state_root = Path(state_root or DEFAULT_STATE_ROOT).resolve()
        stack.logs_dir = stack.state_root / "logs"
        stack.bin_dir = stack.state_root / "bin"
        stack.certs_dir = stack.state_root / "certs"
        stack.object_dir = stack.state_root / "objects"
        stack.signing_key_path = stack.state_root / "object-signing-key"
        for directory in (stack.logs_dir, stack.bin_dir, stack.certs_dir, stack.object_dir):
            directory.mkdir(parents=True, exist_ok=True)
        stack.commands = CommandLog(out)
        stack.started_at = dt.datetime.now(dt.timezone.utc).isoformat()

        stack.admin_password = _require_password(admin_password)
        try:
            stack._prepare_database(database, fresh)
            stack._build(build)
            stack._certificates()
            stack._signing_key()
            stack._runtime_lock()
            stack._migrate()
            stack._bootstrap()
            stack._start_api(public_port)
            stack._start_maintenance()
        except Exception:
            stack.stop()
            raise
        atexit.register(stack.stop)
        stack.http = dev_http.HttpClient(stack.base_url)
        return stack

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for name in ("maintenance", "api"):
            process = getattr(self, name, None)
            if process is None or process.poll() is not None:
                continue
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover - wedged process
                process.kill()
                process.wait(timeout=5)

    # -- maintenance process control (deployment/diagnostic checks) ---------
    def stop_maintenance(self) -> None:
        """Stop the background loop without touching the API or the database."""
        if self.maintenance is None or self.maintenance.poll() is not None:
            return
        self.maintenance.terminate()
        try:
            self.maintenance.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - wedged process
            self.maintenance.kill()
            self.maintenance.wait(timeout=5)

    def start_maintenance(
        self, *extra_args: str, extra_env: dict[str, str] | None = None
    ) -> None:
        """Start the documented `cmd/maintenance run` loop (optionally with overrides).

        The defaults are the documented deployment shape; a diagnostic check (for
        example one that proves a real ``tool_timeout``) starts it with its own
        bounded arguments and env.
        """
        self._start_maintenance(extra_args, extra_env)

    def run_maintenance(
        self, *args: str, extra_env: dict[str, str] | None = None, timeout: int = 300
    ) -> subprocess.CompletedProcess[str]:
        """Run one `cmd/maintenance run` invocation (for example `--once`)."""
        env = self._maintenance_env()
        env.update(extra_env or {})
        argv = [str(self.binaries["maintenance"]), "run", *args]
        self.commands.record("maintenance", argv, env=env, note="one diagnostic maintenance invocation")
        return _run(argv, env=env, timeout=timeout)

    # -- preparation --------------------------------------------------------
    def _prepare_database(self, database: str, fresh: bool) -> None:
        if not pg_dev.is_running():
            self.commands.record(
                "pg_dev.init", [_python(), "tools/pg_dev.py", "init"], note="isolated dev cluster (127.0.0.1:55432)"
            )
            pg_dev.init_cluster()
        self.dbname = database
        if fresh:
            argv = [
                pg_dev.psql_binary(), "-h", "127.0.0.1", "-p", str(pg_dev.DEFAULT_PORT),
                "-U", pg_dev.DEFAULT_USER, "-d", "postgres", "-c",
                f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)',
            ]
            self.commands.record(
                "drop_database",
                argv,
                note="the PostgreSQL password comes from the git-ignored product/.env.local (never printed)",
            )
            pg_dev.run_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)', database="postgres")
        exists = pg_dev.run_sql(
            f"SELECT 1 FROM pg_database WHERE datname = '{database}'", database="postgres"
        )
        if exists != "1":
            argv = [
                pg_dev.psql_binary(), "-h", "127.0.0.1", "-p", str(pg_dev.DEFAULT_PORT),
                "-U", pg_dev.DEFAULT_USER, "-d", "postgres", "-c", f'CREATE DATABASE "{database}"',
            ]
            self.commands.record("create_database", argv)
            pg_dev.run_sql(f'CREATE DATABASE "{database}"', database="postgres")
        self.dsn = pg_dev.dsn(database)
        try:
            self.db_server_version = pg_dev.run_sql("SHOW server_version", database=database)
        except SystemExit:  # pragma: no cover - the fresh database is normally reachable
            self.db_server_version = "unknown"

    def _build(self, build: bool) -> None:
        if not build:
            raise BringupError("--no-build is not supported yet; the bring-up always builds the real binaries")
        go = toolchain.go_binary()
        env = toolchain.go_env(clean_env())
        for name, package in PACKAGES.items():
            binary = self.bin_dir / f"orchara-{name}{_exe_suffix()}"
            argv = [go, "build", "-o", str(binary), package]
            self.commands.record("build", argv, note=f"real compiled {name} binary")
            completed = _run(argv, env=env)
            if completed.returncode != 0:
                raise BringupError(
                    f"go build {package} failed ({completed.returncode}):\n"
                    f"{completed.stdout}\n{completed.stderr}"
                )
            self.binaries[name] = binary

    def _certificates(self) -> None:
        certificates, created = dev_pki.ensure(self.certs_dir, host="127.0.0.1")
        self.commands.record(
            "dev_certificates",
            [_python(), "deploy/dev/dev_pki.py", "--dir", str(self.certs_dir)],
            note="dev-only internal mTLS material; " + ("generated now" if created else "reused"),
        )
        self.certificates = certificates

    def _signing_key(self) -> None:
        if not self.signing_key_path.is_file():
            code = (
                "import pathlib,secrets,sys; "
                "pathlib.Path(sys.argv[1]).write_text(secrets.token_urlsafe(48), encoding='utf-8')"
            )
            self.commands.record(
                "object_signing_key",
                [_python(), "-c", code, str(self.signing_key_path)],
                note="48-byte HMAC object-link key; written to the git-ignored dev root, never printed",
            )
            completed = _run([_python(), "-c", code, str(self.signing_key_path)])
            if completed.returncode != 0:
                raise BringupError(f"cannot generate the object signing key: {completed.stderr}")
        self.signing_key = self.signing_key_path.read_text(encoding="utf-8").strip()
        if len(self.signing_key) < 32:
            raise BringupError("the object signing key file is shorter than 32 bytes")

    def _runtime_lock(self) -> None:
        if not self.runtime_lock.is_file():
            raise BringupError(
                "the P01 runtime lock is missing; the manifest compiler input cannot be reproduced "
                f"({self.runtime_lock})"
            )
        self.runtime_lock_sha256 = _sha256_file(self.runtime_lock)

    # -- the documented command sequence ------------------------------------
    def _admin_env(self) -> dict[str, str]:
        env = clean_env()
        env["ORCHARA_PROFILE"] = self.profile
        env["ORCHARA_DB_DSN"] = self.dsn
        return env

    def _migrate(self) -> None:
        argv = [str(self.binaries["admin"]), "migrate"]
        self.commands.record("migrate", argv, env=self._admin_env(), note="apply the numbered SQL chain")
        completed = _run(argv, env=self._admin_env())
        if completed.returncode != 0:
            raise BringupError(
                f"admin migrate failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
            )
        self.migrate_output = (completed.stdout + completed.stderr).strip()

    def _bootstrap(self) -> None:
        users = pg_dev.run_sql(
            "SELECT count(*) FROM platform.users", database=self.dbname
        )
        if users != "0":
            self.bootstrapped = False
            self.commands.commands.append(
                {
                    "step": "bootstrap",
                    "argv": [],
                    "cwd": str(PRODUCT_ROOT),
                    "env": {},
                    "note": f"skipped: database already has {users} user row(s); "
                    "the smoke must use that administrator's existing password",
                }
            )
            return
        argv = [
            str(self.binaries["admin"]),
            "bootstrap",
            "--login",
            self.admin_login,
            "--display-name",
            ADMIN_DISPLAY_NAME,
        ]
        self.commands.record(
            "bootstrap", argv, env=self._admin_env(), note="password on hidden stdin (never argv/logs)"
        )
        completed = _run(argv, env=self._admin_env(), stdin_text=self.admin_password + "\n")
        if completed.returncode != 0:
            raise BringupError(
                f"admin bootstrap failed ({completed.returncode}):\n"
                f"{completed.stdout}\n{completed.stderr}"
            )
        self.bootstrapped = True
        self.bootstrap_output = (completed.stdout + completed.stderr).strip()

    def _api_env(self) -> dict[str, str]:
        certificates = self.certificates
        assert certificates is not None
        env = clean_env()
        env.update(
            {
                "ORCHARA_PROFILE": self.profile,
                "ORCHARA_DB_DSN": self.dsn,
                "ORCHARA_HTTP_ADDR": self.http_addr,
                "ORCHARA_PUBLIC_URL": self.base_url,
                "ORCHARA_WEB_DIR": str(WEB_DIR),
                "ORCHARA_OBJECT_DIR": str(self.object_dir),
                "ORCHARA_OBJECT_SIGNING_KEY": self.signing_key,
                "ORCHARA_INTERNAL_ADDR": self.internal_addr,
                "ORCHARA_INTERNAL_SERVER_CERT": str(certificates.server_cert),
                "ORCHARA_INTERNAL_SERVER_KEY": str(certificates.server_key),
                "ORCHARA_INTERNAL_CLIENT_CA": str(certificates.ca_cert),
                "ORCHARA_WORKER_IMAGE_DIGEST": self.worker_image_digest,
                "ORCHARA_RUNTIME_LOCK_PATH": str(self.runtime_lock),
                "ORCHARA_PYTHON": _python(),
                "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            }
        )
        return env

    def _start_api(self, public_port: int | None) -> None:
        port = public_port or dev_http.free_port()
        self.http_addr = f"127.0.0.1:{port}"
        self.base_url = f"http://{self.http_addr}"
        self.internal_addr = f"127.0.0.1:{dev_http.free_port()}"
        self.internal_url = f"https://{self.internal_addr}"
        argv = [str(self.binaries["api"])]
        self.commands.record(
            "api",
            argv,
            env=self._api_env(),
            note="serves the real Web pages and the internal mTLS listener",
        )
        self.api = self._spawn(self.logs_dir / "api.log", argv, self._api_env())

        deadline = time.time() + 60
        last = "no response"
        while time.time() < deadline:
            if self.api.poll() is not None:
                raise BringupError(
                    f"the API exited during startup:\n{_log_tail(self.logs_dir / 'api.log')}"
                )
            try:
                response = dev_http.HttpClient(self.base_url).get("/health/ready", timeout=3)
                if response.status == 200:
                    break
                last = f"HTTP {response.status}: {response.text[:300]}"
            except Exception as exc:  # noqa: BLE001 - retried until the deadline
                last = str(exc)
            time.sleep(0.2)
        else:
            raise BringupError(f"the API never became ready ({last}):\n{_log_tail(self.logs_dir / 'api.log')}")
        self._wait_internal()

    def _wait_internal(self) -> None:
        import http.client
        import ssl

        certificates = self.certificates
        assert certificates is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_verify_locations(cafile=str(certificates.ca_cert))
        maintenance_cert, maintenance_key = certificates.client("maintenance")
        context.load_cert_chain(certfile=str(maintenance_cert), keyfile=str(maintenance_key))
        context.check_hostname = True
        host, port = self.internal_addr.rsplit(":", 1)
        deadline = time.time() + 30
        last: Exception | None = None
        while time.time() < deadline:
            connection = None
            try:
                connection = http.client.HTTPSConnection(
                    host, int(port), context=context, timeout=3
                )
                connection.request(
                    "GET", "/health/live", headers={"X-Internal-Role": "maintenance"}
                )
                response = connection.getresponse()
                response.read()
                if response.status == 200:
                    return
                last = AssertionError(f"internal listener answered HTTP {response.status}")
            except Exception as exc:  # noqa: BLE001 - retried until the deadline
                last = exc
            finally:
                if connection is not None:
                    connection.close()
            time.sleep(0.1)
        raise BringupError(f"the internal mTLS listener never became ready: {last}")

    def _maintenance_env(self) -> dict[str, str]:
        certificates = self.certificates
        assert certificates is not None
        maintenance_cert, maintenance_key = certificates.client("maintenance")
        env = clean_env()
        env.update(
            {
                "ORCHARA_PROFILE": self.profile,
                "ORCHARA_MAINTENANCE_DSN": self.dsn,
                "ORCHARA_INTERNAL_URL": self.internal_url,
                "ORCHARA_INTERNAL_CLIENT_CERT": str(maintenance_cert),
                "ORCHARA_INTERNAL_CLIENT_KEY": str(maintenance_key),
                "ORCHARA_INTERNAL_CA": str(certificates.ca_cert),
                "ORCHARA_OBJECT_DIR": str(self.object_dir),
                "ORCHARA_PYTHON": _python(),
                "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            }
        )
        return env

    def _start_maintenance(
        self, extra_args: tuple[str, ...] = (), extra_env: dict[str, str] | None = None
    ) -> None:
        if extra_args:
            argv = [str(self.binaries["maintenance"]), "run", *extra_args]
        else:
            argv = [
                str(self.binaries["maintenance"]),
                "run",
                "--poll-interval",
                "200ms",
                "--worker-id",
                "dev-maintenance",
            ]
        env = self._maintenance_env()
        env.update(extra_env or {})
        self.commands.record(
            "maintenance",
            argv,
            env=env,
            note="real background pools (artifact_finalize system_io + tool_run/model_validate work)",
        )
        self.maintenance = self._spawn(self.logs_dir / "maintenance.log", argv, env)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.maintenance.poll() is not None:
                raise BringupError(
                    "the maintenance process exited during startup:\n"
                    f"{_log_tail(self.logs_dir / 'maintenance.log')}"
                )
            if "run: pools=" in self.maintenance_log():
                return
            time.sleep(0.1)
        raise BringupError(
            "the maintenance process never reported its pools:\n"
            f"{_log_tail(self.logs_dir / 'maintenance.log')}"
        )

    def _spawn(self, log_path: Path, argv: list[str], env: dict[str, str]) -> subprocess.Popen:
        handle = log_path.open("w", encoding="utf-8", newline="\n")
        try:
            return subprocess.Popen(
                argv,
                cwd=PRODUCT_ROOT,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        finally:
            handle.close()

    # -- reads --------------------------------------------------------------
    def maintenance_log(self) -> str:
        return _log_tail(self.logs_dir / "maintenance.log", lines=400)

    def api_log(self) -> str:
        return _log_tail(self.logs_dir / "api.log", lines=400)

    def facts(self) -> dict:
        return {
            "profile": self.profile,
            "stub_results": True,
            "database": {
                "engine": "PostgreSQL",
                "server_version": self.db_server_version,
                "host": "127.0.0.1",
                "port": pg_dev.DEFAULT_PORT,
                "name": self.dbname,
            },
            "public_base_url": self.base_url,
            "internal_url": self.internal_url,
            "object_store": {"kind": "local-filesystem", "root": str(self.object_dir)},
            "admin_login": self.admin_login,
            "bootstrapped_now": self.bootstrapped,
            "worker_image_digest": {
                "value": self.worker_image_digest,
                "kind": "deployer-placeholder",
                "image_built": False,
            },
            "runtime_lock": {
                "path": str(self.runtime_lock),
                "sha256": self.runtime_lock_sha256,
            },
            "binaries": {name: str(path) for name, path in self.binaries.items()},
            "logs": {
                "api": str(self.logs_dir / "api.log"),
                "maintenance": str(self.logs_dir / "maintenance.log"),
            },
            "host": {
                "os": platform_module.platform(),
                "python": platform_module.python_version(),
            },
            "commands": self.commands.commands,
            "started_at": self.started_at,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-root", default=str(DEFAULT_STATE_ROOT), help="git-ignored dev state root")
    parser.add_argument("--database", default=DEFAULT_DATABASE, help="development database name")
    parser.add_argument("--fresh", action="store_true", help="drop and recreate the database first")
    parser.add_argument("--admin-login", default=ADMIN_LOGIN)
    parser.add_argument(
        "--admin-password-env",
        default=ADMIN_PASSWORD_ENV,
        help="environment variable holding the administrator password (never argv)",
    )
    parser.add_argument("--public-port", type=int, default=None, help="fixed public port (tests use a free port)")
    parser.add_argument(
        "--run-seconds",
        type=float,
        default=0,
        help="stop automatically after this many seconds (0 = run until Ctrl-C; for CI checks)",
    )


def _resolve_password(args: argparse.Namespace) -> str:
    password = os.environ.get(args.admin_password_env)
    return _require_password(password)


def command_up(args: argparse.Namespace) -> int:
    stack = DevStack.start(
        state_root=Path(args.state_root),
        database=args.database,
        fresh=args.fresh,
        admin_password=_resolve_password(args),
        admin_login=args.admin_login,
        public_port=args.public_port,
    )
    print("")
    print(f"[bringup] ready: {stack.base_url}  (profile={stack.profile}, stub_results=true)")
    print(f"[bringup] internal mTLS: {stack.internal_url}")
    print(f"[bringup] database: {stack.dbname} on 127.0.0.1:{pg_dev.DEFAULT_PORT}")
    print(f"[bringup] admin login: {stack.admin_login} (password supplied out of band)")
    print(f"[bringup] logs: {stack.logs_dir}")
    if not stack.bootstrapped:
        print("[bringup] bootstrap skipped: the database already had users; resuming an existing environment")
    if args.run_seconds and args.run_seconds > 0:
        print(f"[bringup] API and maintenance are running; stopping automatically after {args.run_seconds}s")
    else:
        print("[bringup] API and maintenance are running; press Ctrl-C to stop both.")
    print("")
    stopped = False

    def _stop(_signum=None, _frame=None) -> None:  # noqa: ANN001
        nonlocal stopped
        stopped = True
        raise KeyboardInterrupt

    for name in ("SIGINT", "SIGTERM"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _stop)
            except (ValueError, OSError):  # pragma: no cover - non-main thread
                pass
    try:
        deadline = time.time() + args.run_seconds if args.run_seconds and args.run_seconds > 0 else 0
        while not stopped:
            if stack.api is not None and stack.api.poll() is not None:
                print(f"[bringup] the API exited; log tail:\n{stack.api_log()}", file=sys.stderr)
                return 1
            if stack.maintenance is not None and stack.maintenance.poll() is not None:
                print(
                    f"[bringup] the maintenance process exited; log tail:\n{stack.maintenance_log()}",
                    file=sys.stderr,
                )
                return 1
            if deadline and time.time() >= deadline:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        stack.stop()
    print("[bringup] stopped")
    return 0


def command_smoke(args: argparse.Namespace) -> int:
    import smoke  # local import: the smoke module is only needed for this subcommand

    stack = DevStack.start(
        state_root=Path(args.state_root),
        database=args.database,
        fresh=args.fresh,
        admin_password=_resolve_password(args),
        admin_login=args.admin_login,
        public_port=args.public_port,
    )
    try:
        certificates = stack.certificates
        assert certificates is not None
        supervisor_cert, supervisor_key = certificates.client("supervisor")
        internal = smoke.InternalAccess(
            url=stack.internal_url,
            ca=str(certificates.ca_cert),
            client_cert=str(supervisor_cert),
            client_key=str(supervisor_key),
            role="supervisor",
        )
        evidence = smoke.run(
            base_url=stack.base_url,
            admin_login=stack.admin_login,
            admin_password=stack.admin_password,
            web_dir=WEB_DIR,
            examples_dir=EXAMPLES,
            internal=internal,
            environment=stack.facts(),
            command=_display([_python(), "deploy/dev/bringup.py", "smoke", *sys.argv[2:]]),
        )
    finally:
        stack.stop()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    smoke.print_summary(evidence)
    print(f"[bringup] evidence written to {out_path}")
    return 0 if evidence.get("status") == "passed" else 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # a piped bring-up must still show its progress line by line
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):  # pragma: no cover - redirected stream
            pass
    parser = argparse.ArgumentParser(
        description="start the Orchara development deployment from a clean database (dev-stub)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    up = subparsers.add_parser("up", help="start API + maintenance and stay in the foreground")
    _common_arguments(up)
    up.set_defaults(handler=command_up)
    smoke_parser = subparsers.add_parser(
        "smoke", help="bring up, run the sales/failure/cancel/model-profile smoke, write evidence"
    )
    _common_arguments(smoke_parser)
    smoke_parser.add_argument(
        "--out",
        default=str(PRODUCT_ROOT / "reports" / "f15a" / "dev-smoke-run.json"),
        help="machine-readable run record path",
    )
    smoke_parser.set_defaults(handler=command_smoke)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except BringupError as exc:
        print(f"[bringup] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
