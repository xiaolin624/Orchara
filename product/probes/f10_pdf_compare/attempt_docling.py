#!/usr/bin/env python3
"""F10/T060 candidate A: one reproducible Docling installation/run attempt.

Docling is not part of the platform dependency set. This script is the re-runnable
entry point for the ticket's required attempt: it creates an isolated venv, tries
to install Docling with plain pip (no product lock is touched), and then runs the
Docling Native + HierarchicalChunker worker in that environment. The raw pip output
is written verbatim to a log file, and the outcome (``blocked`` or ``measured``) is
written to ``docling_attempt.json`` for the comparison harness and the report.

Usage:
    python probes/f10_pdf_compare/attempt_docling.py \
        --venv ..\\..\\.scratch\\docling-venv \
        --pip-log reports/f10/t060-docling-pip.log \
        --out probes/f10_pdf_compare/docling_attempt.json --timeout 900

    # reuse an environment prepared by an earlier attempt:
    python probes/f10_pdf_compare/attempt_docling.py --no-install ...
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRODUCT_ROOT = HERE.parents[1]
DEFAULT_VENV = PRODUCT_ROOT.parent / ".scratch" / "docling-venv"


def venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def sanitized_env() -> dict:
    """The host env with a valid NO_PROXY for httpx/huggingface_hub.

    This Windows host carries both ``NO_PROXY`` and ``no_proxy`` with the value
    ``...,::1,[::1]``. httpx (used by huggingface_hub) rejects the bracketed
    ``[::1]`` entry with ``Invalid port: ':1]'`` before any model download starts.
    The attempt therefore runs the worker with a syntactically valid NO_PROXY; the
    adaptation is recorded in the attempt JSON, not hidden.
    """
    env = {key: value for key, value in os.environ.items() if key.upper() != "NO_PROXY"}
    env["NO_PROXY"] = "localhost,127.0.0.1"
    return env


def run_logged(command: list[str], log_path: Path, *, env: dict | None = None) -> tuple[int, float]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with log_path.open("w", encoding="utf-8") as log:
        log.write("command: " + " ".join(command) + "\n")
        log.write(f"started: {time.strftime('%Y-%m-%dT%H:%M:%S%z')}\n=== output ===\n")
        log.flush()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            log.write(line)
        code = process.wait()
        elapsed = time.perf_counter() - started
        log.write(f"\n=== exit={code} elapsed={elapsed:.1f}s ===\n")
    return code, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="F10/T060 Docling isolated attempt")
    parser.add_argument("--venv", type=Path, default=DEFAULT_VENV)
    parser.add_argument("--pip-log", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=HERE / "docling_attempt.json")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--no-install", action="store_true")
    parser.add_argument(
        "--pip-exit",
        type=int,
        default=None,
        help="exit code of an install performed by an earlier attempt (with --no-install)",
    )
    parser.add_argument(
        "--pip-elapsed",
        type=float,
        default=None,
        help="elapsed seconds of that earlier install",
    )
    args = parser.parse_args()

    python = venv_python(args.venv)
    attempt: dict = {
        "ticket": "T060 (#61)",
        "candidate": "docling-native",
        "status": "blocked",
        "reason": "",
        "command": "",
        "venv": str(args.venv),
        "pip_log": str(args.pip_log),
        "installed": False,
        "note": "",
    }
    if args.pip_exit is not None:
        attempt["pip_exit"] = args.pip_exit
    if args.pip_elapsed is not None:
        attempt["pip_elapsed_s"] = round(args.pip_elapsed, 1)

    if not python.exists():
        if args.no_install:
            attempt["reason"] = f"isolated interpreter not found: {python}"
            attempt["note"] = "run without --no-install to create the venv and try pip install"
            args.out.write_text(json.dumps(attempt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(attempt["reason"])
            return 0
        create = [sys.executable, "-m", "venv", "--system-site-packages", str(args.venv)]
        attempt["venv_command"] = " ".join(create)
        code, elapsed = run_logged(create, args.pip_log)
        attempt["venv_exit"] = code
        attempt["venv_elapsed_s"] = round(elapsed, 1)
        if code != 0:
            attempt["reason"] = "creating the isolated venv failed"
            args.out.write_text(json.dumps(attempt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            return 0

    install = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "docling"]
    attempt["command"] = " ".join(install)
    if not args.no_install:
        code, elapsed = run_logged(install, args.pip_log)
        attempt["pip_exit"] = code
        attempt["pip_elapsed_s"] = round(elapsed, 1)
        if code != 0:
            attempt["reason"] = "pip install docling failed (see the raw pip log)"
            args.out.write_text(json.dumps(attempt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(attempt["reason"])
            return 0

    # Installed or reused: run the Docling worker in that environment. The worker
    # itself records a blocked status when an import or a conversion fails.
    worker_out = args.out.with_suffix(".worker.json")
    worker = [str(python), str(HERE / "docling_worker.py"), "--out", str(worker_out)]
    code, elapsed = run_logged(
        worker, args.out.with_suffix(".worker.log"), env=sanitized_env()
    )
    attempt["env_adaptation"] = "NO_PROXY=localhost,127.0.0.1 (host value contains '[::1]', which httpx rejects)"
    attempt["worker_exit"] = code
    attempt["worker_elapsed_s"] = round(elapsed, 1)
    if worker_out.exists():
        payload = json.loads(worker_out.read_text(encoding="utf-8"))
        attempt.update(payload)
        attempt["installed"] = payload.get("status") == "measured"
    else:
        attempt["reason"] = "the Docling worker produced no result file"
    args.out.write_text(json.dumps(attempt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"docling attempt: {attempt['status']} {attempt.get('reason', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
