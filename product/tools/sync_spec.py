#!/usr/bin/env python3
"""Safely copy specifications and check live source, both copies and baseline.

check(source, dest) and sync(source, dest) are the module interface. Inventories,
staging, process locking and repeatable rollback are internal. Checking never
heals managed copies; explicit sync recovers an interrupted sync first.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterator

MANIFEST_NAME = "SYNC-MANIFEST.json"
MANIFEST_SCHEMA = "orchara.spec-sync/v2"
DEFAULT_EXCLUDES = (
    ".git", ".venv", ".deps", "__pycache__", ".pytest_cache",
    ".toolchains", ".scratch", "product", "node_modules", "*.pyc",
)


class SyncError(Exception):
    """Safe structured failure, never file contents or credentials."""

    def __init__(self, kind: str, path: Path | str, detail: str):
        self.finding = {"path": str(path), "kind": kind, "detail": detail}
        super().__init__(f"{kind}: {path}: {detail}")


def _is_excluded(relative: Path) -> bool:
    return any(relative.match(rule) if rule.startswith("*") else rule in relative.parts
               for rule in DEFAULT_EXCLUDES)


def _no_redirects(path: Path) -> None:
    """Reject symlinks and Windows junction/reparse points, including ancestors."""
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise SyncError("unsafe_path", part, "redirected paths are not managed")


def _relative(raw: str) -> Path:
    name = str(raw).replace("\\", "/")
    if (not name or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive
            or any(p in ("", ".", "..") for p in name.split("/")) or ":" in name):
        raise SyncError("invalid_layout", raw, "expected a nonempty relative path")
    return Path(*name.split("/"))


@dataclass(frozen=True)
class _Layout:
    source: Path
    dest: Path
    spec: Path
    contracts: Path
    state: Path

    @property
    def journal(self) -> Path:
        return self.state / "transaction.json"

    def identity(self) -> dict[str, str]:
        return {"spec": self.spec.relative_to(self.dest).as_posix(),
                "contracts": self.contracts.relative_to(self.dest).as_posix()}


def _layout(source: Path, dest: Path, spec_subdir: str, contracts_subdir: str) -> _Layout:
    source, dest = Path(source).absolute(), Path(dest).absolute()
    _no_redirects(source)
    _no_redirects(dest)
    source, dest = source.resolve(), dest.resolve()
    if (source == dest or source.is_relative_to(dest) or dest == dest.parent
            or dest.is_relative_to(source / "contracts")):
        raise SyncError("invalid_layout", dest, "destination cannot contain the source or be a volume root")
    paths = [dest / _relative(spec_subdir), dest / _relative(contracts_subdir),
             dest / ".scratch/spec-sync"]
    for path in paths:
        _no_redirects(path)
        if not path.resolve().is_relative_to(dest) or path.resolve() == dest:
            raise SyncError("invalid_layout", path, "managed path escapes destination")
    for i, path in enumerate(paths):
        if any(path.is_relative_to(other) or other.is_relative_to(path)
               for other in paths[i + 1:]):
            raise SyncError("invalid_layout", path, "managed directories overlap")
    return _Layout(source, dest, *paths)


def normalize_content(raw: bytes) -> bytes:
    """Preserve existing UTF-8 newline normalization; undecodable bytes are unchanged."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def _inventory(root: Path, *, source: bool = False, dest: Path | None = None,
               manifest: bool = True) -> dict[str, str]:
    _no_redirects(root)
    if not root.is_dir():
        raise SyncError("source_unavailable" if source else "copy_unavailable",
                        root, "directory is missing or not readable")
    result: dict[str, str] = {}

    def failed(error: OSError) -> None:
        raise error

    # Excluded trees are pruned before entering them, not after a recursive glob.
    for directory, dirs, files in os.walk(root, onerror=failed, followlinks=False):
        current = Path(directory)
        retained = []
        for name in sorted(dirs):
            path = current / name
            if source and (_is_excluded(path.relative_to(root)) or path == dest):
                continue
            _no_redirects(path)
            retained.append(name)
        dirs[:] = retained
        for name in sorted(files):
            path = current / name
            relative = path.relative_to(root)
            if source and _is_excluded(relative):
                continue
            if relative.as_posix() == MANIFEST_NAME:
                if source:
                    raise SyncError("source_incomplete", path, "baseline name is reserved")
                if not manifest:
                    continue
            _no_redirects(path)
            if not path.is_file():
                raise SyncError("unreadable_tree", path, "expected a regular file")
            result[relative.as_posix()] = hashlib.sha256(
                normalize_content(path.read_bytes())).hexdigest()
    return dict(sorted(result.items()))


def _source(layout: _Layout) -> dict[str, str]:
    if not layout.source.is_dir():
        raise SyncError("source_unavailable", layout.source,
                        "source directory is missing or not readable")
    _no_redirects(layout.source / "contracts")
    if not (layout.source / "contracts").is_dir():
        raise SyncError("source_incomplete", layout.source / "contracts",
                        "the source contracts directory is required")
    try:
        return _inventory(layout.source, source=True, dest=layout.dest)
    except OSError as error:
        raise SyncError("source_unavailable", layout.source,
                        "the source could not be read completely") from error


def _contracts(files: dict[str, str]) -> dict[str, str]:
    return {p.removeprefix("contracts/"): h for p, h in files.items()
            if p.startswith("contracts/")}


def _valid_map(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for name, digest in value.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            return False
        try:
            _relative(name)
        except SyncError:
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            return False
    return True


def _manifest(layout: _Layout) -> dict[str, Any]:
    path = layout.spec / MANIFEST_NAME
    _no_redirects(path)
    if not path.is_file():
        raise SyncError("missing_manifest", path, "run sync to create the baseline")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA
                or not _valid_map(value.get("files"))
                or not _valid_map(value.get("contracts"))
                or MANIFEST_NAME in value["files"]
                or value["contracts"] != _contracts(value["files"])
                or value.get("file_count") != len(value["files"])
                or value.get("contract_count") != len(value["contracts"])):
            raise ValueError("invalid baseline structure")
        return value
    except (ValueError, UnicodeError) as error:
        raise SyncError("invalid_manifest", path, "baseline structure is invalid") from error


def _compare(recorded: dict[str, str], actual: dict[str, str], prefix: str,
             kind_prefix: str = "") -> list[dict[str, str]]:
    issues = []
    for path in sorted(recorded.keys() | actual.keys()):
        kind = ("added" if path not in recorded else "removed" if path not in actual
                else "modified" if recorded[path] != actual[path] else "")
        if kind:
            issues.append({"path": prefix + path, "kind": kind_prefix + kind,
                           "detail": "content differs from the synchronized baseline"})
    return issues


def _audit(layout: _Layout, source: dict[str, str]) -> list[dict[str, str]]:
    baseline = _manifest(layout)
    return (_compare(baseline["files"], source, "source/", "source_")
            + _compare(baseline["files"], _inventory(layout.spec, manifest=False), "")
            + _compare(baseline["contracts"], _inventory(layout.contracts), "contracts/"))


@contextmanager
def _locked(layout: _Layout) -> Iterator[None]:
    _no_redirects(layout.state)
    layout.state.mkdir(parents=True, exist_ok=True)
    path = layout.state / "lock"
    _no_redirects(path)
    with path.open("a+b") as lock:
        if path.stat().st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SyncError("busy", layout.dest, "another check or sync holds the lock") from error
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == "nt":
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _guard(layout: _Layout, path: Path) -> None:
    """Recheck owned paths immediately before recursive deletion or moving."""
    _no_redirects(layout.dest)
    _no_redirects(path)
    allowed = (layout.spec, layout.contracts, *(layout.state / name for name in
               ("stage-spec", "stage-contracts", "old-spec", "old-contracts",
                "discard-spec", "discard-contracts")))
    if (path not in allowed or path.resolve() == layout.dest
            or not path.resolve().is_relative_to(layout.dest)):
        raise SyncError("unsafe_path", path, "not an owned destination slot")


def _remove(layout: _Layout, path: Path) -> None:
    _guard(layout, path)
    if path.exists():
        _inventory(path, manifest=True)  # Also reject redirected descendants.
        _guard(layout, path)
        shutil.rmtree(path)


def _move(layout: _Layout, source: Path, target: Path) -> None:
    _guard(layout, source)
    _guard(layout, target)
    if target.exists():
        raise SyncError("unsafe_path", target, "rename target must be empty")
    target.parent.mkdir(parents=True, exist_ok=True)
    _guard(layout, target)
    source.rename(target)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    _no_redirects(path)
    _no_redirects(temporary)
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _journal(layout: _Layout) -> dict[str, Any]:
    _no_redirects(layout.journal)
    try:
        value = json.loads(layout.journal.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("schema") != "orchara.spec-sync-tx/v1"
                or value.get("layout") != layout.identity()):
            raise ValueError("invalid transaction identity")
        for generation in ("old", "new"):
            maps = value.get(generation)
            if not isinstance(maps, dict) or set(maps) != {"spec", "contracts"}:
                raise ValueError("invalid transaction inventories")
            if any(not _valid_map(item) for item in maps.values() if item is not None):
                raise ValueError("invalid transaction inventory")
            if generation == "new" and any(item is None for item in maps.values()):
                raise ValueError("missing new inventory")
        return value
    except (ValueError, UnicodeError, OSError) as error:
        raise SyncError("recovery_required", layout.journal,
                        "transaction cannot be safely identified; preserve it for inspection") from error


def _old_trees(layout: _Layout) -> dict[str, dict[str, str] | None]:
    return {name: _inventory(path, manifest=True) if path.exists() else None
            for name, path in (("spec", layout.spec), ("contracts", layout.contracts))}


def _recover(layout: _Layout) -> None:
    """Repeatable rollback; another crash leaves the same journal for retry."""
    record = _journal(layout)
    for name, target in (("spec", layout.spec), ("contracts", layout.contracts)):
        _guard(layout, target)
        backup = layout.state / ("old-" + name)
        discarded = layout.state / ("discard-" + name)
        _guard(layout, backup)
        _guard(layout, discarded)
        old, new = record["old"][name], record["new"][name]
        if backup.exists():
            if old is None or _inventory(backup, manifest=True) != old:
                raise SyncError("recovery_required", backup, "backup does not match previous state")
            if target.exists() and _inventory(target, manifest=True) not in (old, new):
                raise SyncError("recovery_required", target, "target changed outside this transaction")
            if target.exists():
                # Rename the disposable tree away; never recursively delete a
                # live target before restoring its complete backup. A partial
                # housekeeping failure cannot strand the recovery path.
                _move(layout, target, discarded)
            _move(layout, backup, target)
        elif old is None:
            if target.exists():
                if _inventory(target, manifest=True) != new:
                    raise SyncError("recovery_required", target, "unexpected target content")
                _move(layout, target, discarded)
        elif not target.exists() or _inventory(target, manifest=True) != old:
            raise SyncError("recovery_required", target, "previous complete state cannot be proved")
    layout.journal.unlink()


def _clean_slots(layout: _Layout) -> None:
    for name in ("stage-spec", "stage-contracts", "old-spec", "old-contracts",
                 "discard-spec", "discard-contracts"):
        _remove(layout, layout.state / name)


def _source_commit(source: Path) -> str | None:
    try:
        result = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                capture_output=True, timeout=20, check=False)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace").strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _prepare(layout: _Layout, files: dict[str, str]) -> dict[str, Any]:
    spec, contracts = layout.state / "stage-spec", layout.state / "stage-contracts"
    spec.mkdir()
    contracts.mkdir()
    (spec / "contracts").mkdir()
    for name in files:
        original, target = layout.source / name, spec / name
        _no_redirects(original)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
    for name in _contracts(files):
        target = contracts / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(spec / "contracts" / name, target)
    if _inventory(spec) != files or _inventory(contracts) != _contracts(files):
        raise SyncError("source_changed", layout.source, "staged content differs from captured source")
    value = {"schema": MANIFEST_SCHEMA,
             "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "generation": uuid.uuid4().hex,
             "source_root": str(layout.source), "source_commit": _source_commit(layout.source),
             "spec_subdir": layout.identity()["spec"],
             "contracts_subdir": layout.identity()["contracts"],
             "file_count": len(files), "contract_count": len(_contracts(files)),
             "files": files, "contracts": _contracts(files)}
    _write_json(spec / MANIFEST_NAME, value)
    return value


def sync(source: Path, dest: Path, *, spec_subdir: str = "docs/spec",
         contracts_subdir: str = "contracts") -> dict[str, Any]:
    """Publish one verified generation, restoring an interrupted one first.

    The pending journal blocks checks until explicit sync recovers. Ordinary
    failures roll back. Failed recovery retains its journal and backups.
    This does not claim protection against physical power loss.
    """
    try:
        layout = _layout(source, dest, spec_subdir, contracts_subdir)
        files = _source(layout)  # Never mutate targets before source preflight.
        with _locked(layout):
            _no_redirects(layout.journal)
            recovered = layout.journal.exists()
            if recovered:
                _recover(layout)
            _clean_slots(layout)
            old = _old_trees(layout)
            try:
                manifest = _prepare(layout, files)
                if _source(layout) != files:
                    raise SyncError("source_changed", layout.source, "source changed while preparing copies")
                record = {"schema": "orchara.spec-sync-tx/v1", "layout": layout.identity(),
                          "old": old, "new": {
                              "spec": _inventory(layout.state / "stage-spec", manifest=True),
                              "contracts": _inventory(layout.state / "stage-contracts", manifest=True)}}
                _write_json(layout.journal, record)  # Before the first target move.
                for name, target in (("spec", layout.spec), ("contracts", layout.contracts)):
                    if target.exists():
                        _move(layout, target, layout.state / ("old-" + name))
                    _move(layout, layout.state / ("stage-" + name), target)
                if _audit(layout, _source(layout)):
                    raise SyncError("sync_failed", layout.dest, "installed generation failed verification")
                layout.journal.unlink()  # Commit point, after both trees are verified.
            except Exception as error:
                if layout.journal.exists():
                    try:
                        _recover(layout)
                    except Exception as recovery:
                        raise SyncError("recovery_required", layout.journal,
                                        "rollback incomplete; preserve transaction and retry sync") from recovery
                if isinstance(error, SyncError):
                    raise
                raise SyncError("sync_failed", layout.dest,
                                "synchronization failed; previous on-disk state preserved") from error
            warnings = []
            try:
                _clean_slots(layout)
            except (OSError, SyncError):
                warnings.append("committed generation is complete; temporary cleanup deferred")
            return {"copied": len(files), "manifest": layout.spec / MANIFEST_NAME,
                    "files": len(files), "contracts": manifest["contract_count"],
                    "recovered": recovered, "warnings": warnings}
    except SyncError:
        raise
    except OSError as error:
        raise SyncError("sync_failed", dest, "filesystem operation could not complete") from error


def check(source: Path, dest: Path, *, spec_subdir: str = "docs/spec",
          contracts_subdir: str = "contracts") -> list[dict[str, str]]:
    """Empty means current source, both copies and the baseline agree.

    Cooperative lock state may be created; managed copies are never modified.
    An interrupted synchronization requires an explicit sync call to recover.
    """
    try:
        layout = _layout(source, dest, spec_subdir, contracts_subdir)
        files = _source(layout)
        if not layout.dest.is_dir():
            raise SyncError("missing_manifest", layout.spec / MANIFEST_NAME,
                            "run sync to create the baseline")
        with _locked(layout):
            _no_redirects(layout.journal)
            if layout.journal.exists():
                raise SyncError("incomplete_sync", layout.journal,
                                "previous sync was interrupted; run sync to recover")
            return _audit(layout, files)
    except SyncError as error:
        return [error.finding]
    except OSError:
        return [{"path": str(dest), "kind": "unreadable_tree",
                 "detail": "could not verify all source and destination files"}]


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="Safely sync and check live specifications")
    parser.add_argument("--source", type=Path, default=repo)
    parser.add_argument("--dest", type=Path, default=repo / "product")
    parser.add_argument("--check", action="store_true", help="verify source, copies and baseline")
    args = parser.parse_args(argv)
    if args.check:
        findings = check(args.source, args.dest)
        if findings:
            print(f"drift or verification failure: {len(findings)} finding(s)")
            for finding in findings[:40]:
                print(f"  {finding['kind']:20} {finding['path']} ({finding['detail']})")
            if len(findings) > 40:
                print(f"  ... {len(findings) - 40} more")
            return 1
        print("no drift: current source, both copies and synchronized baseline agree")
        return 0
    try:
        result = sync(args.source, args.dest)
    except SyncError as error:
        print(str(error))
        return 1
    print(f"copied {result['copied']} files; manifest {result['manifest']} "
          f"({result['files']} spec + {result['contracts']} contract digests)")
    if result["recovered"]:
        print("restored previous interrupted transaction before synchronizing")
    for warning in result["warnings"]:
        print(f"warning: {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
