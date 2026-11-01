"""F00/T008 acceptance: the product skeleton reports real health.

This is the end-to-end check the ticket asks for, not a mock:

1. start the Go API against the isolated development PostgreSQL cluster;
2. liveness answers while the database is up, and readiness answers 200;
3. stop PostgreSQL -> readiness must turn 503 while liveness stays 200;
4. start PostgreSQL again -> readiness must return to 200;
5. no response may contain the DSN or its password.

The cluster is restored in ``finally`` so a failing run does not leave the
machine without its development database.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402

GO = shutil.which("go") or r"E:\orchara-toolchains\go\bin\go.exe"
ADDR = "127.0.0.1:8099"
BASE = f"http://{ADDR}"


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _wait_for(predicate, timeout: float = 30.0, interval: float = 0.4, what: str = "condition"):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except Exception as exc:  # noqa: BLE001 - retried below
            last = exc
        time.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}; last error: {last}")


def _get(path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{BASE}{path}", timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


@pytest.fixture(scope="module")
def api_server(tmp_path_factory: pytest.TempPathFactory):
    if not Path(GO).exists():
        pytest.skip("Go toolchain not available")
    if not pg_dev.is_running():
        pytest.skip("isolated development cluster is not running (run tools/pg_dev.py init)")

    binary_dir = tmp_path_factory.mktemp("api")
    binary = binary_dir / ("orchara-api.exe" if sys.platform == "win32" else "orchara-api")
    build = subprocess.run(
        [GO, "build", "-o", str(binary), "./cmd/api"],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if build.returncode != 0:
        pytest.fail(f"go build failed:\n{build.stdout}\n{build.stderr}")

    env = dict(os.environ)
    env["ORCHARA_DB_DSN"] = pg_dev.dsn()
    env["ORCHARA_HTTP_ADDR"] = ADDR
    env["ORCHARA_WEB_DIR"] = str(PRODUCT_ROOT / "apps" / "web" / "src")

    process = subprocess.Popen(
        [str(binary)],
        cwd=PRODUCT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for(lambda: _port_open("127.0.0.1", 8099), what="api port")
        _wait_for(lambda: _get("/healthz")[0] == 200, what="liveness")
        yield process
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover
            process.kill()
        if not pg_dev.is_running():
            pg_dev.init_cluster()


def test_liveness_and_readiness_with_database_up(api_server) -> None:
    live_code, live_body = _get("/healthz")
    assert live_code == 200
    assert json.loads(live_body)["status"] == "alive"

    ready_code, ready_body = _get("/readyz")
    assert ready_code == 200, ready_body
    payload = json.loads(ready_body)
    assert payload["status"] == "ready"
    assert payload["checks"]["database"]["status"] == "ok"


def test_contract_paths_are_served(api_server) -> None:
    """contracts/openapi.json declares /health/live and /health/ready."""
    live_code, live_body = _get("/health/live")
    assert live_code == 200, live_body
    assert json.loads(live_body)["status"] == "alive"

    ready_code, ready_body = _get("/health/ready")
    assert ready_code == 200, ready_body
    assert json.loads(ready_body)["status"] == "ready"


def test_console_page_is_served(api_server) -> None:
    code, body = _get("/")
    assert code == 200
    assert "Orchara 平台状态" in body
    # Liveness must come from its own endpoint, not be assumed from a successful fetch.
    assert "/health/live" in body, "the page must read liveness from /health/live"
    assert "/api/status" in body, "the page must read dependency state from /api/status"
    assert "运行中" in body and "status === 'alive'" in body, (
        "liveness text must be derived from the endpoint response, not hardcoded"
    )


def test_readiness_fails_while_database_is_down_and_recovers(api_server) -> None:
    try:
        pg_dev.stop_cluster()
        _wait_for(lambda: not _port_open("127.0.0.1", 55432), what="postgres to stop")

        ready_code, ready_body = _get("/readyz")
        assert ready_code == 503, "readiness must fail closed when the database is unreachable"
        payload = json.loads(ready_body)
        assert payload["status"] == "not_ready"
        assert payload["checks"]["database"]["status"] == "unavailable"

        # liveness must stay independent of the database
        live_code, _ = _get("/healthz")
        assert live_code == 200

        # no credential may appear in the failure path
        secret = pg_dev.load_secret()
        for leaked in (secret, "postgresql://", "password"):
            assert leaked not in ready_body.lower(), f"response leaked {leaked!r}"
    finally:
        pg_dev.init_cluster()

    _wait_for(lambda: _port_open("127.0.0.1", 55432), what="postgres to restart")
    _wait_for(lambda: _get("/readyz")[0] == 200, what="readiness to recover")


def test_status_endpoint_never_includes_the_dsn(api_server) -> None:
    code, body = _get("/api/status")
    assert code == 200
    lowered = body.lower()
    secret = pg_dev.load_secret()
    for leaked in (secret, "sslmode", "postgresql://"):
        assert leaked not in lowered, f"/api/status leaked {leaked!r}"
