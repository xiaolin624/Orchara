#!/usr/bin/env python3
"""One command that runs the basic acceptance checks (F00/T012).

The point is reproducibility: a clean environment should be able to run
``python product/tools/ci_checks.py`` and get the same verdict a developer got,
without remembering five separate commands.

``--quick`` skips the checks that need a database or the Go toolchain, so the
script is still useful on a machine that only has Python.

Exit code is 0 only when every selected check **ran and passed**. A skipped
check makes the verdict ``partial`` and the exit code non-zero, because a green
exit from a run that never executed the integration checks is exactly the
"not run recorded as passed" failure this project forbids. ``--allow-skipped``
exists for machines that legitimately cannot run them, and it marks the summary
``partial`` rather than ``ok``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PRODUCT_ROOT.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import toolchain  # noqa: E402

GO = toolchain.go_binary()


def _run(command: list[str], *, cwd: Path, timeout: int = 1800) -> tuple[int, str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    tail = ((completed.stdout or "") + (completed.stderr or "")).strip().splitlines()
    return completed.returncode, "\n".join(tail[-3:])


def _has_go() -> bool:
    return Path(GO).exists()


def _has_cluster() -> bool:
    sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
    try:
        import pg_dev
    except SystemExit:  # missing local secret
        return False
    return pg_dev.is_running()


def build_checks(quick: bool) -> list[dict]:
    checks: list[dict] = [
        {
            "name": "spec-drift",
            "command": [sys.executable, "product/tools/sync_spec.py", "--check"],
            "cwd": REPO_ROOT,
            "always": True,
        },
        {
            "name": "python-tests-fast",
            "command": [
                sys.executable,
                "-m",
                "pytest",
                "product/tests/tooling",
                "product/tests/p01",
                "-q",
            ],
            "cwd": REPO_ROOT,
            "always": True,
        },
    ]
    if not quick:
        checks.append(
            {
                "name": "go-vet",
                "command": [GO, "vet", "./..."],
                "cwd": PRODUCT_ROOT,
                "always": False,
                "requires": "go",
            }
        )
        checks.append(
            {
                "name": "go-test",
                "command": [GO, "test", "./..."],
                "cwd": PRODUCT_ROOT,
                "always": False,
                "requires": "go",
            }
        )
        checks.append(
            {
                "name": "integration-tests",
                "command": [sys.executable, "-m", "pytest", "product/tests/f00", "product/tests/p02", "-q"],
                "cwd": REPO_ROOT,
                "always": False,
                "requires": "cluster",
            }
        )
    return checks


def summarise(results: list[dict], duration: float) -> dict:
    """Build the summary. A skipped check makes the verdict ``partial``, not ``ok``."""
    failed = [item for item in results if item["status"] == "failed"]
    skipped = [item for item in results if item["status"] == "skipped"]
    verdict = "failed" if failed else ("partial" if skipped else "ok")
    return {
        "checks": results,
        "passed": sum(1 for item in results if item["status"] == "passed"),
        "skipped": len(skipped),
        "failed": [item["name"] for item in failed],
        "skipped_names": [item["name"] for item in skipped],
        "duration_seconds": round(duration, 1),
        "verdict": verdict,
        "ok": verdict == "ok",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the basic acceptance checks")
    parser.add_argument("--quick", action="store_true", help="skip checks needing Go or a database")
    parser.add_argument("--json", action="store_true", help="emit a JSON summary")
    parser.add_argument(
        "--allow-skipped",
        action="store_true",
        help="exit 0 for a partial run (checks that cannot run on this machine); the summary still says partial",
    )
    args = parser.parse_args(argv)

    have_go = _has_go()
    have_cluster = _has_cluster()
    results = []
    started = time.time()

    for check in build_checks(args.quick):
        requires = check.get("requires")
        if requires == "go" and not have_go:
            results.append({"name": check["name"], "status": "skipped", "detail": "no Go toolchain"})
            continue
        if requires == "cluster" and not have_cluster:
            results.append(
                {"name": check["name"], "status": "skipped", "detail": "development cluster not running"}
            )
            continue

        code, tail = _run(check["command"], cwd=check["cwd"])
        results.append(
            {
                "name": check["name"],
                "status": "passed" if code == 0 else "failed",
                "detail": tail if code != 0 else "",
            }
        )

    summary = summarise(results, time.time() - started)

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for item in results:
            marker = {"passed": "ok", "skipped": "skip", "failed": "FAIL"}[item["status"]]
            print(f"[{marker:4}] {item['name']}")
            if item["detail"]:
                print(f"        {item['detail']}")
        print(
            f"\n{summary['passed']} passed, {summary['skipped']} skipped, "
            f"{len(summary['failed'])} failed -> {summary['verdict']}"
        )
    if summary["verdict"] == "ok":
        return 0
    if summary["verdict"] == "partial" and args.allow_skipped:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
