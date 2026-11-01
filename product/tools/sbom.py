#!/usr/bin/env python3
"""Emit an SBOM / licence list for the locked probe environment.

T004 asks for the resolved set, its licences and checksums to be written down so
another deployer can reproduce and audit. Run it with the probe interpreter:

    product/probes/agent_min/.venv/Scripts/python.exe product/tools/sbom.py

An image digest is deliberately absent: no application image has been built in
this phase, and inventing one would be a false claim. The report says so
explicitly instead.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = PRODUCT_ROOT / "probes" / "agent_min" / "requirements.lock.txt"
DEFAULT_OUT = PRODUCT_ROOT / "reports" / "p01" / "sbom.json"

LOCK_PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", re.MULTILINE)
LOCK_HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")


def parse_lock(path: Path) -> dict[str, dict]:
    """Return {normalised name: {version, hashes}} from a hash-locked file."""
    text = path.read_text(encoding="utf-8")
    package: dict[str, dict] = {}
    current: str | None = None
    for line in text.splitlines():
        pin = LOCK_PIN.match(line)
        if pin:
            name, version = pin.group(1), pin.group(2)
            current = name.lower().replace("_", "-")
            package[current] = {"name": name, "version": version, "hashes": []}
            continue
        digest = LOCK_HASH.search(line)
        if digest and current:
            package[current]["hashes"].append(digest.group(1))
    return package


def installed_metadata() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if not name:
            continue
        key = name.lower().replace("_", "-")
        found[key] = {
            "version": dist.version,
            "license": (
                dist.metadata.get("License-Expression")
                or dist.metadata.get("License")
                or ""
            ).strip()
            or None,
            "classifiers": [
                value for value in dist.metadata.get_all("Classifier") or [] if "License" in value
            ],
        }
    return found


def build(lock_path: Path) -> dict:
    locked = parse_lock(lock_path)
    installed = installed_metadata()

    packages = []
    missing: list[str] = []
    mismatched: list[dict] = []
    unknown_license: list[str] = []
    for key, entry in sorted(locked.items()):
        record = installed.get(key)
        if record is None:
            missing.append(entry["name"])
            licence = None
        else:
            licence = record["license"] or (
                record["classifiers"][0].split("::")[-1].strip() if record["classifiers"] else None
            )
            if record["version"] != entry["version"]:
                mismatched.append(
                    {"name": entry["name"], "locked": entry["version"], "installed": record["version"]}
                )
        if not licence:
            unknown_license.append(entry["name"])
        packages.append(
            {
                "name": entry["name"],
                "version": entry["version"],
                "license": licence,
                "sha256": entry["hashes"],
                "platform_portable": len(entry["hashes"]) > 1,
            }
        )

    return {
        "sbom": "orchara.probe-sbom/v1",
        "task": "P04 evidence for T004",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "interpreter": sys.version.split()[0],
        "lock_file": str(lock_path.relative_to(PRODUCT_ROOT)),
        "package_count": len(packages),
        "packages": packages,
        "not_installed_in_this_environment": missing,
        "version_mismatches": mismatched,
        "licenses_not_declared": unknown_license,
        "image_digest": None,
        "image_digest_note": "no application image is built in this phase; not_run, not a placeholder digest",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SBOM/licence list for the locked probe env")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    report = build(args.lock)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(
        f"{report['package_count']} packages -> {args.out}; "
        f"missing={len(report['not_installed_in_this_environment'])}, "
        f"mismatched={len(report['version_mismatches'])}, "
        f"no-license={len(report['licenses_not_declared'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
