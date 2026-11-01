#!/usr/bin/env python3
"""Cross-language JCS agreement evidence for P01/T003.

Runs the Python canonicalizer and the Go CLI over every vector in
``vectors.json`` and records, per vector, both canonical strings, both digests
and whether they agree. The Go binary is built from ``go/`` (stdlib only).

Usage::

    python run_agreement.py --out ../../reports/p01/jcs-agreement.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "python"))
import jcs  # noqa: E402

GO = shutil.which("go") or r"E:\orchara-toolchains\go\bin\go.exe"


def build_go(destination: Path) -> Path:
    binary = destination / ("jcs.exe" if sys.platform == "win32" else "jcs")
    completed = subprocess.run(
        [GO, "build", "-o", str(binary), "."],
        cwd=HERE / "go",
        capture_output=True,
        text=True,
        timeout=300,
    )
    if completed.returncode != 0:
        raise SystemExit(f"go build failed: {completed.stderr}")
    return binary


def go_version() -> str:
    completed = subprocess.run([GO, "version"], capture_output=True, text=True, timeout=60)
    return completed.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="JCS Go/Python agreement probe")
    parser.add_argument("--out", type=Path, default=HERE.parent.parent / "reports" / "p01" / "jcs-agreement.json")
    args = parser.parse_args(argv)

    vectors = json.loads((HERE / "vectors.json").read_text(encoding="utf-8"))
    records: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_go(Path(tmp))
        for case in vectors:
            input_path = Path(tmp) / "input.json"
            input_path.write_text(case["input_json"], encoding="utf-8")
            completed = subprocess.run(
                [str(binary), str(input_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=120,
            )
            go_payload = json.loads(completed.stdout)

            try:
                python_canonical = jcs.canonicalize_text(case["input_json"])
                python_error = None
            except jcs.JCSError as exc:
                python_canonical = None
                python_error = exc.code

            if python_canonical is not None:
                python_sha = jcs.sha256_hex(python_canonical.encode("utf-8"))
            else:
                python_sha = None

            if "error" in go_payload:
                agrees = go_payload["error"] == python_error
            else:
                agrees = (
                    python_canonical is not None
                    and go_payload["canonical"] == python_canonical
                    and go_payload["sha256"] == python_sha
                )

            records.append(
                {
                    "name": case["name"],
                    "go_canonical": go_payload.get("canonical"),
                    "go_sha256": go_payload.get("sha256"),
                    "go_error": go_payload.get("error"),
                    "python_canonical": python_canonical,
                    "python_sha256": python_sha,
                    "python_error": python_error,
                    "agrees": agrees,
                }
            )

    mismatches = [record["name"] for record in records if not record["agrees"]]
    report = {
        "probe": "p01-jcs-agreement",
        "task": "P01 / T003",
        "spec": "RFC 8785 (JCS)",
        "go_version": go_version(),
        "vector_count": len(records),
        "mismatches": mismatches,
        "all_agree": not mismatches,
        "records": records,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(f"{len(records)} vectors, mismatches: {mismatches or 'none'} -> {args.out}")
    return 0 if not mismatches else 1


if __name__ == "__main__":
    raise SystemExit(main())
