#!/usr/bin/env python3
"""Create the locked probe environment from scratch and run the P01 probe.

T002 claimed "installs successfully in a clean environment", but the original
evidence lived only in a git-ignored virtualenv, so a fresh checkout could not
reproduce it. This script is the re-runnable entry point:

    python product/tools/bootstrap_probe.py            # create/reuse + install + run
    python product/tools/bootstrap_probe.py --recreate # delete the venv first

It never bypasses the lock: installation uses ``--require-hashes``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "agent_min"
VENV = PROBE_ROOT / ".venv"
LOCK = PROBE_ROOT / "requirements.lock.txt"
REPORT = PRODUCT_ROOT / "reports" / "p01" / "agent-min-probe.json"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def run(command: list[str], description: str, *, cwd: Path | None = None) -> None:
    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=1800, cwd=cwd
    )
    if completed.returncode != 0:
        raise SystemExit(f"{description} failed:\n{completed.stdout}\n{completed.stderr}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bootstrap the locked probe environment")
    parser.add_argument("--recreate", action="store_true", help="delete and rebuild the venv")
    args = parser.parse_args(argv)

    if not LOCK.exists():
        raise SystemExit(f"missing lock file: {LOCK}")

    if args.recreate and VENV.exists():
        shutil.rmtree(VENV)

    if not venv_python().exists():
        run([sys.executable, "-m", "venv", str(VENV)], "venv creation")

    python = str(venv_python())
    run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"], "pip upgrade")
    run(
        [python, "-m", "pip", "install", "--quiet", "--require-hashes", "-r", str(LOCK)],
        "locked install",
    )
    run([python, "-m", "pip", "check"], "dependency check")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    run(
        [python, "-m", "agent_min", "--quiet", "--out", str(REPORT)],
        "probe run",
        cwd=PROBE_ROOT,
    )

    payload = json.loads(REPORT.read_text(encoding="utf-8"))
    failed = [name for name, value in payload["assertions"].items() if value is False]
    print(json.dumps({"report": str(REPORT), "failed_assertions": failed}, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
