#!/usr/bin/env python3
"""Regenerate the F15A/T053 evidence files by executing the real commands.

This driver is not part of the probe's verdict path: it starts real fixture
processes, runs the real probe CLI against them (and against an absent candidate
and the real local-filesystem analogue), and records what happened. The pytest
suite asserts the same discriminations independently; this script only makes the
checked-in evidence reproducible in one command.

Usage (from ``product``, Go available on PATH or ORCHARA_GO set)::

    python probes/f15a_objectstore/run_evidence.py --out reports/f15a
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

PROBE_ROOT = Path(__file__).resolve().parent
PRODUCT_ROOT = PROBE_ROOT.parents[1]
PROBE = PROBE_ROOT / "object_store_probe.py"
FIXTURES = PROBE_ROOT / "fixtures"
REPORTS = PRODUCT_ROOT / "reports" / "f15a"

sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import toolchain  # noqa: E402

RUNTIME_ACCESS = "probe-runtime-credential"
RUNTIME_SECRET = "fixture-runtime-secret-not-a-credential"
PROVISION_ACCESS = "probe-provision-credential"
PROVISION_SECRET = "fixture-provision-secret-not-a-credential"
BUCKET = "probe-bucket"
TENANT_PREFIX = "orchara-probe"

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


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_port(port: int, process: subprocess.Popen, timeout: float = 15.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit(f"fixture exited before listening (rc={process.returncode})")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    process.kill()
    raise SystemExit("fixture did not listen")


def probe_env(*, endpoint: str | None, provisioning: bool = True) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("F15A_")}
    if endpoint is None:
        return env
    env.update(
        {
            "F15A_S3_ENDPOINT": endpoint,
            "F15A_S3_BUCKET": BUCKET,
            "F15A_S3_REGION": "us-east-1",
            "F15A_PREFIX": TENANT_PREFIX,
            "F15A_S3_ACCESS_KEY": RUNTIME_ACCESS,
            "F15A_S3_SECRET_KEY": RUNTIME_SECRET,
        }
    )
    if provisioning:
        env["F15A_S3_PROVISION_ACCESS_KEY"] = PROVISION_ACCESS
        env["F15A_S3_PROVISION_SECRET_KEY"] = PROVISION_SECRET
    return env


def run_probe(
    name: str,
    *,
    endpoint: str | None,
    out_dir: Path,
    provisioning: bool = True,
    extra_args: tuple[str, ...] = (),
) -> dict:
    report_path = out_dir / f"{name}.json"
    profile = "candidate" if name.startswith("candidate") else "fixture"
    argv = [
        sys.executable,
        str(PROBE),
        "--out",
        str(report_path),
        "--short-ttl",
        "1",
        "--timeout",
        "10",
        "--retries",
        "2",
        "--profile",
        profile,
        *extra_args,
    ]
    completed = subprocess.run(
        argv,
        cwd=PRODUCT_ROOT,
        env=probe_env(endpoint=endpoint, provisioning=provisioning),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    entry = {
        "name": name,
        "command": " ".join(
            ["python", str(PROBE.relative_to(PRODUCT_ROOT))] + argv[2:]
        ),
        "exit_code": completed.returncode,
        "status": report["status"],
        "report": report_path.name,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "verdicts": {item["id"]: item["status"] for item in report["assertions"]},
        "stdout_tail": completed.stdout.strip().splitlines()[-4:],
    }
    return entry


class Fixture:
    def __init__(self, launcher: str, violations: tuple[str, ...], transient: int = 0):
        self.port = free_port()
        self.argv = [
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
            self.argv.extend(["--violate", violation])
        if transient:
            self.argv.extend(["--transient-failures", str(transient)])
        self.process: subprocess.Popen | None = None

    def __enter__(self) -> "Fixture":
        self.process = subprocess.Popen(
            self.argv,
            cwd=PRODUCT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        wait_for_port(self.port, self.process)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def build_local_fs_probe(workdir: Path) -> Path:
    binary = workdir / ("localfs_probe.exe" if sys.platform == "win32" else "localfs_probe")
    env = dict(os.environ)
    env.update(toolchain.go_caches())
    completed = subprocess.run(
        [
            toolchain.go_binary(),
            "build",
            "-o",
            str(binary),
            "./probes/f15a_objectstore/localfs_probe",
        ],
        cwd=PRODUCT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr)
    return binary


def run_local_fs(name: str, out_dir: Path, workdir: Path) -> dict:
    binary = build_local_fs_probe(workdir)
    root = workdir / "local-fs-store"
    report_path = out_dir / f"{name}.json"
    completed = subprocess.run(
        [str(binary), "-root", str(root), "-out", str(report_path)],
        cwd=PRODUCT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return {
        "name": name,
        "command": (
            "go build -o <tmp>/localfs_probe ./probes/f15a_objectstore/localfs_probe && "
            "<tmp>/localfs_probe -root <tmp>/local-fs-store -out "
            f"reports/f15a/{name}.json"
        ),
        "exit_code": completed.returncode,
        "status": report["status"],
        "report": report_path.name,
        "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "verdicts": {item["id"]: item["status"] for item in report["assertions"]},
        "environment": report["environment"],
        "store": report["store"],
        "stdout_tail": completed.stdout.strip().splitlines()[-4:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="regenerate F15A/T053 evidence")
    parser.add_argument("--out", type=Path, default=REPORTS)
    args = parser.parse_args(argv)
    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="f15a-evidence-"))

    runs: list[dict] = []
    try:
        with Fixture("compliant_server.py", ()) as fixture:
            runs.append(
                run_probe(
                    "fixture-compliant",
                    endpoint=f"http://127.0.0.1:{fixture.port}",
                    out_dir=out_dir,
                )
            )
        with Fixture("violating_server.py", ALL_VIOLATIONS) as fixture:
            runs.append(
                run_probe(
                    "fixture-violating-all",
                    endpoint=f"http://127.0.0.1:{fixture.port}",
                    out_dir=out_dir,
                )
            )
        for violation in ALL_VIOLATIONS:
            with Fixture("violating_server.py", (violation,)) as fixture:
                runs.append(
                    run_probe(
                        f"fixture-violation-{violation}",
                        endpoint=f"http://127.0.0.1:{fixture.port}",
                        out_dir=out_dir,
                    )
                )
        with Fixture("compliant_server.py", (), transient=1) as fixture:
            runs.append(
                run_probe(
                    "fixture-transient-retry",
                    endpoint=f"http://127.0.0.1:{fixture.port}",
                    out_dir=out_dir,
                )
            )

        # The real candidate run: no endpoint exists on this machine, so the
        # probe must produce a blocked report (exit 2), not a green one.
        runs.append(
            run_probe(
                "candidate-absent",
                endpoint="http://127.0.0.1:9000",
                out_dir=out_dir,
            )
        )
        runs.append(
            run_probe(
                "candidate-not-configured",
                endpoint=None,
                out_dir=out_dir,
            )
        )
        runs.append(run_local_fs("local-fs-analogue", out_dir, workdir))

        index = {
            "probe": "f15a-s3-compat",
            "ticket": "T053/F15A",
            "generated_at": now(),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "note": (
                "fixture runs are discrimination evidence, not candidate acceptance; "
                "candidate-absent is the real environment result (blocked, exit 2); "
                "local-fs-analogue is not S3 evidence"
            ),
            "runs": runs,
        }
        index_path = out_dir / "runs.json"
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(json.dumps({"runs": len(runs), "index": str(index_path)}, indent=2))
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
