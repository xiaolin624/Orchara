#!/usr/bin/env python3
"""P02/T009: produce machine-readable evidence for artifact pinning.

The report prose is not evidence on its own, so this script drives the probe CLI
and writes the raw results next to it:

    python probes/p02_checkpoint/artifact_evidence.py --out reports/p02/T009-artifact-pinning.json

It performs three real steps against the development cluster: publish v1, start a
run pinned to v1, publish v2, resume (must still load v1), then delete a pinned
artifact and show the resume fails with ``artifact_unavailable``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE = Path(__file__).with_name("checkpoint_probe.py")
VENV_PYTHON = PRODUCT_ROOT / "probes" / "agent_min" / ".venv" / (
    "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
)


def _run(*args: str, expect_success: bool = True) -> dict[str, Any]:
    completed = subprocess.run(
        [str(VENV_PYTHON), str(PROBE), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
    )
    if expect_success and completed.returncode != 0:
        raise SystemExit(f"probe {' '.join(args)} failed: {completed.stderr}")
    lines = [line for line in completed.stdout.strip().splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else {"exit_code": completed.returncode}


def _next_version() -> int:
    sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
    import pg_dev

    current = pg_dev.run_sql("SELECT coalesce(max(graph_version), 0) FROM app.artifacts")
    return int(current or 0) + 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="artifact pinning evidence")
    parser.add_argument("--out", type=Path, default=PRODUCT_ROOT / "reports" / "p02" / "T009-artifact-pinning.json")
    args = parser.parse_args(argv)

    first = _next_version()
    published_v1 = _run("publish", "--version", str(first))

    thread = f"t-{uuid.uuid4().hex[:10]}"
    started = _run("run", "--thread", thread, "--owner", "alice")

    published_v2 = _run("publish", "--version", str(first + 1))
    resumed = _run("resume", "--thread", thread, "--owner", "alice", "--decision", "approve")

    second = _next_version()
    _run("publish", "--version", str(second))
    thread2 = f"t-{uuid.uuid4().hex[:10]}"
    _run("run", "--thread", thread2, "--owner", "alice")
    _run("delete-artifact", "--version", str(second))
    unavailable = _run(
        "resume", "--thread", thread2, "--owner", "alice", "--decision", "approve",
        expect_success=False,
    )

    report = {
        "probe": "p02-artifact-pinning",
        "task": "P02 / T009",
        "published_before_run": published_v1,
        "run": started,
        "published_after_run": published_v2,
        "resume": resumed,
        "deleted_artifact_resume": unavailable,
        "assertions": {
            "resumed_on_the_pinned_version": resumed.get("loaded_version") == first,
            "digest_is_the_pinned_one": resumed.get("artifact_digest") == published_v1.get("digest"),
            "deleted_artifact_reports_unavailable": unavailable.get("error") == "artifact_unavailable",
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(json.dumps(report["assertions"], indent=2))
    print(f"wrote {args.out}")
    return 0 if all(report["assertions"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
