"""T053/F15A: the probe must fail closed, never fake a candidate pass.

There is no candidate S3 endpoint on this machine. The probe's *configuration and
environment* path is therefore the one that actually runs here, and it must be
observably different from "all assertions passed": exit code 2 and ``blocked``,
with the concrete missing condition recorded. A probe that answered exit 0
because it had nothing to check would be the exact dishonesty this ticket
forbids.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = PRODUCT_ROOT / "probes" / "f15a_objectstore" / "object_store_probe.py"

FIXTURE_CREDENTIALS = {
    "F15A_S3_ACCESS_KEY": "probe-runtime-credential",
    "F15A_S3_SECRET_KEY": "fixture-runtime-secret-not-a-credential",
    "F15A_S3_PROVISION_ACCESS_KEY": "probe-provision-credential",
    "F15A_S3_PROVISION_SECRET_KEY": "fixture-provision-secret-not-a-credential",
}


def _clean_env(**extra: str) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("F15A_")
    }
    env.update(extra)
    return env


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE), *args],
        cwd=PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_missing_configuration_is_blocked_not_passed(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    completed = _run(["--out", str(out)], _clean_env())
    assert completed.returncode == 2, completed.stdout + completed.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["exit_code"] == 2
    assert report["blocked_reason"]["code"] == "config_missing"
    missing = set(report["blocked_reason"]["missing"])
    assert {"endpoint", "bucket", "access_key", "secret_key"} <= missing
    assert report["assertions"] == []
    assert all(item["status"] != "passed" for item in report["assertions"])
    # The CLI must say the same thing it wrote to disk.
    assert "blocked" in completed.stdout.lower()
    assert "fixture-runtime-secret" not in completed.stdout


def test_unreachable_endpoint_is_blocked_not_passed(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    port = _closed_port()
    env = _clean_env(**FIXTURE_CREDENTIALS)
    env.update(
        {
            "F15A_S3_ENDPOINT": f"http://127.0.0.1:{port}",
            "F15A_S3_BUCKET": "probe-bucket",
            "F15A_S3_REGION": "us-east-1",
            "F15A_PREFIX": "orchara-probe",
        }
    )
    completed = _run(["--out", str(out), "--timeout", "3"], env)
    assert completed.returncode == 2, completed.stdout + completed.stderr
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["blocked_reason"]["code"] == "endpoint_unreachable"
    assert "127.0.0.1" in report["blocked_reason"]["message"]
    assert report["assertions"] == []


def test_invalid_endpoint_scheme_is_a_configuration_failure(tmp_path: Path) -> None:
    out = tmp_path / "report.json"
    env = _clean_env(**FIXTURE_CREDENTIALS)
    env.update(
        {
            "F15A_S3_ENDPOINT": "ftp://127.0.0.1:9000",
            "F15A_S3_BUCKET": "probe-bucket",
        }
    )
    completed = _run(["--out", str(out)], env)
    assert completed.returncode == 2
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["blocked_reason"]["code"] == "config_invalid"


def test_secrets_are_environment_only_and_never_appear_in_evidence(tmp_path: Path) -> None:
    """A command line is readable by every local user; the probe must not take secrets there."""
    help_text = subprocess.run(
        [sys.executable, str(PROBE), "--help"],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert help_text.returncode == 0
    lowered = help_text.stdout.lower()
    assert "--secret" not in lowered
    assert "--access-key" not in lowered
    assert "F15A_S3_SECRET_KEY" in help_text.stdout

    out = tmp_path / "blocked.json"
    env = _clean_env(**FIXTURE_CREDENTIALS)
    env.update(
        {
            "F15A_S3_ENDPOINT": f"http://127.0.0.1:{_closed_port()}",
            "F15A_S3_BUCKET": "probe-bucket",
        }
    )
    _run(["--out", str(out)], env)
    written = out.read_text(encoding="utf-8")
    assert FIXTURE_CREDENTIALS["F15A_S3_SECRET_KEY"] not in written
    assert FIXTURE_CREDENTIALS["F15A_S3_PROVISION_SECRET_KEY"] not in written
    assert FIXTURE_CREDENTIALS["F15A_S3_ACCESS_KEY"] not in written
