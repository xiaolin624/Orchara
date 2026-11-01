#!/usr/bin/env python3
"""Manage an isolated development PostgreSQL cluster for Orchara probes.

The machine already runs a `postgresql-x64-16` Windows service on 5432 whose
credentials are unknown and which does not belong to this project. Probes must
never touch it, so this tool creates a separate cluster from the user-space
PostgreSQL binaries on its own port.

Data lives outside the repository (default ``E:/orchara-pg``) so that contract
validation, which walks every Markdown file in the tree, is not polluted.

Usage::

    python tools/pg_dev.py init      # initdb + start + create role/db
    python tools/pg_dev.py start
    python tools/pg_dev.py stop
    python tools/pg_dev.py status
    python tools/pg_dev.py dsn
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PG_BIN = Path(os.environ.get("ORCHARA_PG_BIN", r"E:\orchara-toolchains\pgsql\bin"))
DEFAULT_DATA_DIR = Path(os.environ.get("ORCHARA_PG_DATA", r"E:\orchara-pg"))
DEFAULT_PORT = int(os.environ.get("ORCHARA_PG_PORT", "55432"))
DEFAULT_USER = "orchara_owner"
DEFAULT_DB = "orchara_probe"

# Where a developer keeps the local development secret. Git-ignored on purpose:
# product/AGENTS.md forbids credentials in the repo, tests, reports or fixtures.
ENV_FILE = PRODUCT_ROOT / ".env.local"
SECRET_ENV = "ORCHARA_PG_PASSWORD"
WORKER_SECRET_ENV = "ORCHARA_PG_WORKER_PASSWORD"

EXE = ".exe" if os.name == "nt" else ""


def _read_env_file(key: str) -> str | None:
    if not ENV_FILE.exists():
        return None
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def load_secret() -> str:
    """Return the development password, or fail closed with instructions.

    There is deliberately no default: a checked-in password is a credential in
    the repository, and a silently-generated one makes runs irreproducible.
    """
    value = os.environ.get(SECRET_ENV) or _read_env_file(SECRET_ENV)
    if not value:
        raise SystemExit(
            f"{SECRET_ENV} is not set. Create {ENV_FILE} containing\n"
            f"  {SECRET_ENV}=<your local development password>\n"
            "or export it in the environment. This file is git-ignored."
        )
    return value


def load_worker_secret() -> str:
    """Password for the restricted runtime role, derived unless overridden."""
    value = os.environ.get(WORKER_SECRET_ENV) or _read_env_file(WORKER_SECRET_ENV)
    return value or f"{load_secret()}-worker"



def _tool(name: str) -> str:
    candidate = DEFAULT_PG_BIN / f"{name}{EXE}"
    if candidate.exists():
        return str(candidate)
    resolved = shutil.which(name)
    if not resolved:
        raise SystemExit(f"cannot find PostgreSQL tool {name!r} in {DEFAULT_PG_BIN} or PATH")
    return resolved


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    # stdin must be closed: psql will otherwise block on an interactive password
    # prompt when it is spawned from a non-interactive agent session.
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL
    )
    if check and completed.returncode != 0:
        raise SystemExit(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout}\n{completed.stderr}"
        )
    return completed


def _run_detached(command: list[str]) -> None:
    """Run a command that spawns a long-lived child (the postmaster).

    Capturing output here would deadlock: the postmaster inherits the pipe and
    keeps it open, so the parent never observes EOF even after pg_ctl exits.
    """
    completed = subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        timeout=120,
    )
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode}): {' '.join(command)}")


def is_running() -> bool:
    if not (DEFAULT_DATA_DIR / "postmaster.pid").exists():
        return False
    completed = _run([_tool("pg_ctl"), "status", "-D", str(DEFAULT_DATA_DIR)], check=False)
    return completed.returncode == 0


def init_cluster() -> None:
    if (DEFAULT_DATA_DIR / "PG_VERSION").exists():
        print(f"cluster already initialised at {DEFAULT_DATA_DIR}")
    else:
        DEFAULT_DATA_DIR.parent.mkdir(parents=True, exist_ok=True)
        pwfile = DEFAULT_DATA_DIR.with_suffix(".pw")
        pwfile.write_text(load_secret(), encoding="utf-8", newline="\n")
        try:
            _run(
                [
                    _tool("initdb"),
                    "-D",
                    str(DEFAULT_DATA_DIR),
                    "-U",
                    DEFAULT_USER,
                    f"--pwfile={pwfile}",
                    "--auth-local=scram-sha-256",
                    "--auth-host=scram-sha-256",
                    "--encoding=UTF8",
                    "--no-locale",
                ]
            )
        finally:
            pwfile.unlink(missing_ok=True)
        print(f"initialised cluster at {DEFAULT_DATA_DIR}")

    if not is_running():
        _run_detached(
            [
                _tool("pg_ctl"),
                "start",
                "-D",
                str(DEFAULT_DATA_DIR),
                "-l",
                str(DEFAULT_DATA_DIR / "server.log"),
                "-o",
                f"-p {DEFAULT_PORT} -c listen_addresses=127.0.0.1",
                "-w",
            ]
        )
        print(f"started cluster on 127.0.0.1:{DEFAULT_PORT}", flush=True)

    _ensure_database()


def _psql(sql: str, database: str = "postgres") -> str:
    env = dict(os.environ)
    env["PGPASSWORD"] = load_secret()
    completed = subprocess.run(
        [
            _tool("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(DEFAULT_PORT),
            "-U",
            DEFAULT_USER,
            "-d",
            database,
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    if completed.returncode != 0:
        raise SystemExit(f"psql failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def _ensure_database() -> None:
    existing = _psql(f"SELECT 1 FROM pg_database WHERE datname = '{DEFAULT_DB}'")
    if existing != "1":
        _psql(f'CREATE DATABASE "{DEFAULT_DB}"')
        print(f"created database {DEFAULT_DB}")


def dsn(database: str = DEFAULT_DB) -> str:
    return (
        f"postgresql://{DEFAULT_USER}:{load_secret()}"
        f"@127.0.0.1:{DEFAULT_PORT}/{database}?sslmode=disable"
    )


def dsn_with_schema(schema: str | None, database: str = DEFAULT_DB) -> str:
    """Owner DSN with the session ``search_path`` pinned to ``schema``."""
    base = dsn(database)
    if not schema:
        return base
    from urllib.parse import quote

    return f"{base}&options={quote(f'-csearch_path={schema}')}"


def worker_dsn(schema: str | None = None) -> str:
    base = (
        f"postgresql://orchara_worker:{load_worker_secret()}"
        f"@127.0.0.1:{DEFAULT_PORT}/{DEFAULT_DB}?sslmode=disable"
    )
    if schema:
        from urllib.parse import quote

        base += f"&options={quote(f'-csearch_path={schema}')}"
    return base


def status() -> dict:
    running = is_running()
    payload = {
        "running": running,
        "data_dir": str(DEFAULT_DATA_DIR),
        "port": DEFAULT_PORT,
        "database": DEFAULT_DB,
        "dsn_redacted": dsn().replace(load_secret(), "<redacted>"),
    }
    if running:
        payload["server_version"] = _psql("SHOW server_version", "postgres")
        payload["current_database"] = _psql("SELECT current_database()", DEFAULT_DB)
    return payload


def stop_cluster() -> None:
    if is_running():
        _run([_tool("pg_ctl"), "stop", "-D", str(DEFAULT_DATA_DIR), "-m", "fast", "-w"])
        print("stopped")
    else:
        print("not running")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="isolated dev PostgreSQL cluster")
    parser.add_argument(
        "action", choices=("init", "start", "stop", "status", "dsn", "wait")
    )
    parser.add_argument("--timeout", type=float, default=30.0, help="seconds for 'wait'")
    args = parser.parse_args(argv)

    if args.action in {"init", "start"}:
        init_cluster()
        print(json.dumps(status(), indent=2))
    elif args.action == "stop":
        stop_cluster()
    elif args.action == "status":
        print(json.dumps(status(), indent=2))
    elif args.action == "dsn":
        print(dsn())
    elif args.action == "wait":
        deadline = time.time() + args.timeout
        while time.time() < deadline:
            if is_running():
                print("ready")
                return 0
            time.sleep(0.5)
        print("timeout waiting for cluster")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
