#!/usr/bin/env python3
"""Reproduce the specification into the product workspace and detect drift.

T001: the specification directory is a read-only source; the product workspace
keeps its own copy under ``docs/spec`` plus a runtime contract snapshot under
``contracts``. F00 depends on being able to prove the two have not silently
diverged, so every copy writes a SHA-256 manifest and ``--check`` compares the
workspace against it.

Usage::

    python product/tools/sync_spec.py            # copy + write manifest
    python product/tools/sync_spec.py --check    # exit 1 if the copy drifted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MANIFEST_NAME = "SYNC-MANIFEST.json"
MANIFEST_SCHEMA = "orchara.spec-sync/v1"

# Paths that are workspace state, never specification content.
DEFAULT_EXCLUDES = (
    ".git",
    ".venv",
    ".deps",
    "__pycache__",
    ".pytest_cache",
    ".toolchains",
    ".scratch",
    "product",
    "node_modules",
    "*.pyc",
)


def _is_excluded(relative: Path) -> bool:
    parts = relative.parts
    for exclude in DEFAULT_EXCLUDES:
        if exclude.startswith("*"):
            if relative.match(exclude):
                return True
            continue
        if exclude in parts:
            return True
    return False


def _iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if _is_excluded(path.relative_to(root)):
            continue
        yield path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_commit(source: Path) -> str | None:
    try:
        completed = subprocess.run(  # noqa: S603
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip() or None


def _digest_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): _sha256(path)
        for path in _iter_files(root)
    }


def sync(
    source: Path,
    dest: Path,
    *,
    spec_subdir: str = "docs/spec",
    contracts_subdir: str = "contracts",
) -> dict[str, Any]:
    """Copy the specification into ``dest`` and write the drift manifest."""
    spec_root = dest / spec_subdir
    if spec_root.exists():
        shutil.rmtree(spec_root)
    spec_root.parent.mkdir(parents=True, exist_ok=True)

    copied = 0
    for path in _iter_files(source):
        relative = path.relative_to(source)
        target = spec_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied += 1

    # Runtime contract snapshot the product loads; must mirror the spec copy.
    contracts_source = source / "contracts"
    contracts_dest = dest / contracts_subdir
    if contracts_source.is_dir():
        if contracts_dest.exists():
            shutil.rmtree(contracts_dest)
        shutil.copytree(contracts_source, contracts_dest)

    files = _digest_tree(spec_root)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_root": str(source),
        "source_commit": _source_commit(source),
        "spec_subdir": spec_subdir,
        "contracts_subdir": contracts_subdir,
        "file_count": len(files),
        "files": files,
    }
    manifest_path = spec_root / MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"copied": copied, "manifest": manifest_path, "files": len(files)}


def check(
    source: Path,
    dest: Path,
    *,
    spec_subdir: str = "docs/spec",
    contracts_subdir: str = "contracts",
) -> list[dict[str, str]]:
    """Return drift entries between the spec copy and the manifest ([] if clean)."""
    manifest_path = dest / spec_subdir / MANIFEST_NAME
    if not manifest_path.exists():
        return [
            {
                "path": str(manifest_path),
                "kind": "missing_manifest",
                "detail": "run sync_spec.py to create the manifest",
            }
        ]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded: dict[str, str] = manifest.get("files", {})
    actual = _digest_tree(dest / spec_subdir)
    actual.pop(MANIFEST_NAME, None)
    recorded.pop(MANIFEST_NAME, None)

    drift: list[dict[str, str]] = []
    for path, digest in sorted(recorded.items()):
        if path not in actual:
            drift.append({"path": path, "kind": "removed", "detail": "in manifest, missing on disk"})
        elif actual[path] != digest:
            drift.append(
                {
                    "path": path,
                    "kind": "modified",
                    "detail": f"sha256 {digest[:12]} -> {actual[path][:12]}",
                }
            )
    for path in sorted(set(actual) - set(recorded)):
        drift.append({"path": path, "kind": "added", "detail": "present on disk, not in manifest"})

    if source.is_dir():
        for path in _iter_files(source):
            relative = path.relative_to(source).as_posix()
            if relative not in recorded and relative not in actual:
                drift.append(
                    {
                        "path": relative,
                        "kind": "upstream_new",
                        "detail": "spec source has a file the product copy lacks; re-run sync",
                    }
                )
    return drift


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Sync the spec into the product workspace")
    parser.add_argument("--source", type=Path, default=repo_root)
    parser.add_argument("--dest", type=Path, default=repo_root / "product")
    parser.add_argument("--check", action="store_true", help="only report drift; exit 1 on drift")
    args = parser.parse_args(argv)

    if args.check:
        drift = check(args.source, args.dest)
        if drift:
            print(f"drift detected: {len(drift)} entr{'y' if len(drift) == 1 else 'ies'}")
            for entry in drift[:40]:
                print(f"  {entry['kind']:14} {entry['path']} ({entry['detail']})")
            if len(drift) > 40:
                print(f"  ... {len(drift) - 40} more")
            return 1
        print("no drift: spec copy matches manifest")
        return 0

    result = sync(args.source, args.dest)
    print(f"copied {result['copied']} files; manifest {result['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
