"""Shared plumbing for the F15A discrimination tests: real fixture subprocesses.

The fixture server is started as a real OS process on a free loopback port, the
probe is started as a real OS process against it, and the JSON evidence file is
read back. No in-process HTTP monkeypatching and no mocked S3 client.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "object_store_probe.py"
FIXTURES = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "fixtures"

BUCKET = "probe-bucket"
TENANT_PREFIX = "orchara-probe"
RUNTIME_ACCESS = "probe-runtime-credential"
RUNTIME_SECRET = "fixture-runtime-secret-not-a-credential"
PROVISION_ACCESS = "probe-provision-credential"
PROVISION_SECRET = "fixture-provision-secret-not-a-credential"
SECRET_VALUES = (RUNTIME_SECRET, PROVISION_SECRET)

ALL_VIOLATIONS = (
    "anonymous_read_denied",
    "anonymous_put_denied",
    "anonymous_list_denied",
    "out_of_range_key_denied",
    "sealed_put_denied",
    "staging_overwrite_preserves_sealed",
    "short_lived_signature_expiry",
    "proxy_download_boundary",
    "delete_semantics",
)

# Assertions that must fail on a violating fixture (versioning is observational).
ENFORCEABLE = ALL_VIOLATIONS


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"fixture exited before listening (rc={process.returncode}):\n"
                f"stdout={stdout}\nstderr={stderr}"
            )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    process.kill()
    raise AssertionError(f"fixture did not listen on 127.0.0.1:{port}")


class FixtureProcess:
    def __init__(
        self,
        launcher: str,
        *,
        violations: tuple[str, ...] = (),
        transient_failures: int = 0,
    ) -> None:
        self.port = free_port()
        args = [
            sys.executable,
            str(FIXTURES / launcher),
            "--port",
            str(self.port),
            "--bucket",
            BUCKET,
            "--tenant-prefix",
            TENANT_PREFIX,
            "--runtime-access-key",
            RUNTIME_ACCESS,
            "--runtime-secret-key",
            RUNTIME_SECRET,
            "--provision-access-key",
            PROVISION_ACCESS,
            "--provision-secret-key",
            PROVISION_SECRET,
        ]
        for violation in violations:
            args.extend(["--violate", violation])
        if transient_failures:
            args.extend(["--transient-failures", str(transient_failures)])
        self._args = args
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> "FixtureProcess":
        self.process = subprocess.Popen(
            self._args,
            cwd=PRODUCT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        wait_for_port(self.port, self.process)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.communicate()


def probe_env(port: int) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("F15A_")}
    env.update(
        {
            "F15A_S3_ENDPOINT": f"http://127.0.0.1:{port}",
            "F15A_S3_BUCKET": BUCKET,
            "F15A_S3_REGION": "us-east-1",
            "F15A_PREFIX": TENANT_PREFIX,
            "F15A_S3_ACCESS_KEY": RUNTIME_ACCESS,
            "F15A_S3_SECRET_KEY": RUNTIME_SECRET,
            "F15A_S3_PROVISION_ACCESS_KEY": PROVISION_ACCESS,
            "F15A_S3_PROVISION_SECRET_KEY": PROVISION_SECRET,
            "F15A_PROFILE": "fixture",
        }
    )
    return env


def run_probe(port: int, report_path: Path, *, extra_args: tuple[str, ...] = ()) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(PROBE),
        "--out",
        str(report_path),
        "--short-ttl",
        "5",
        "--timeout",
        "10",
        "--retries",
        "2",
        *extra_args,
    ]
    return subprocess.run(
        args,
        cwd=PRODUCT_ROOT,
        env=probe_env(port),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def load_report(report_path: Path) -> dict:
    return json.loads(report_path.read_text(encoding="utf-8"))


def statuses(report: dict) -> dict[str, str]:
    return {item["id"]: item["status"] for item in report["assertions"]}


def assert_no_secret_values(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for secret in SECRET_VALUES:
        assert secret not in text, f"fixture credential leaked into evidence: {path}"
    assert RUNTIME_ACCESS not in text
    assert PROVISION_ACCESS not in text
