#!/usr/bin/env python3
"""C1.04 / #141 POSIX acceptance leg, executed inside WSL Ubuntu-24.04.

The WSL distro has no pytest and dependencies must not be installed, so this is
a standard-library probe, not a pytest suite and not reported as one. It uses
only real temporary directories on the Linux filesystem, real source/copy
bytes, real child processes and the real command entry point
(``tools/sync_spec.py``). It proves source drift, sync, process mutual
exclusion, fail-fast busy diagnostics, lock-file deletion behaviour and normal
release/reuse.

Run from Windows:
  wsl.exe -d Ubuntu-24.04 -e python3 <this file>

Exit status 0 only when every check passes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS = PRODUCT_ROOT / "tools"
TOOL = TOOLS / "sync_spec.py"
sys.path.insert(0, str(TOOLS))

import sync_spec as sync  # noqa: E402

RESULTS: list[tuple[bool, str, str]] = []


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""), flush=True)


def run_cli(source: Path, dest: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-X", "utf8", str(TOOL),
               "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


def managed_bytes(dest: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for root, label in ((dest / "docs" / "spec", "spec"), (dest / "contracts", "contracts")):
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    result[f"{label}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
    return result


def content_bytes(dest: Path) -> dict[str, bytes]:
    """Managed file bytes, excluding the spec baseline's volatile metadata."""
    result: dict[str, bytes] = {}
    for root, label in ((dest / "docs" / "spec", "spec"), (dest / "contracts", "contracts")):
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            if label == "spec" and relative == sync.MANIFEST_NAME:
                continue
            result[f"{label}/{relative}"] = path.read_bytes()
    return result


def start_holder(source: Path, dest: Path) -> subprocess.Popen[str]:
    """Real sync process paused inside the lock, before it touches targets."""
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync
original = sync._prepare
def paused(layout, files):
    print("ready", flush=True)
    sys.stdin.readline()
    return original(layout, files)
sync._prepare = paused
sync.sync(Path(sys.argv[2]), Path(sys.argv[3]))
print("done", flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", script, str(TOOLS), str(source), str(dest)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    line = process.stdout.readline().strip()
    if line != "ready":
        stderr = process.stderr.read() if process.stderr else ""
        process.kill()
        raise RuntimeError(f"holder did not start: {line!r} {stderr}")
    return process


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="c104-posix-"))
    holder: subprocess.Popen[str] | None = None
    try:
        print("PLATFORM:", sys.platform, os.uname().release, flush=True)
        print("PYTHON:", sys.version.split()[0], flush=True)
        print("TEMP ROOT:", root, flush=True)
        print("PYTEST: not installed in this distro; pytest suite not_run", flush=True)

        source = root / "repo"
        (source / "contracts").mkdir(parents=True)
        (source / "docs").mkdir()
        (source / "README.md").write_bytes(b"# spec\n")
        (source / "docs" / "HANDOFF.md").write_bytes(b"# handoff\n")
        (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0"}\n')
        dest = root / "product"

        result = run_cli(source, dest, check=False)
        record(result.returncode == 0, "initial sync exits zero", result.stdout.strip())
        result = run_cli(source, dest, check=True)
        record(result.returncode == 0, "clean check exits zero", result.stdout.strip())

        (source / "README.md").write_bytes(b"# spec changed on POSIX\n")
        result = run_cli(source, dest, check=True)
        record(result.returncode != 0 and "source_modified" in result.stdout,
               "POSIX source change is drift with nonzero status", result.stdout.strip())
        result = run_cli(source, dest, check=False)
        record(result.returncode == 0, "POSIX sync publishes the change", result.stdout.strip())
        result = run_cli(source, dest, check=True)
        record(result.returncode == 0, "check passes after POSIX sync", result.stdout.strip())
        expected_content = content_bytes(dest)

        holder = start_holder(source, dest)
        before_hold = managed_bytes(dest)
        started = time.monotonic()
        competitor_check = run_cli(source, dest, check=True)
        check_elapsed = time.monotonic() - started
        record(competitor_check.returncode != 0 and "busy" in competitor_check.stdout
               and "another check or sync holds the lock" in competitor_check.stdout,
               "check competitor reports busy while sync holds the lock",
               competitor_check.stdout.strip())
        record(check_elapsed < 5.0, "check competitor fails fast", f"{check_elapsed:.3f}s")

        started = time.monotonic()
        competitor_sync = run_cli(source, dest, check=False)
        sync_elapsed = time.monotonic() - started
        record(competitor_sync.returncode != 0 and "busy" in competitor_sync.stdout
               and "another check or sync holds the lock" in competitor_sync.stdout,
               "sync competitor reports busy while sync holds the lock",
               competitor_sync.stdout.strip())
        record(sync_elapsed < 5.0, "sync competitor fails fast", f"{sync_elapsed:.3f}s")
        record(managed_bytes(dest) == before_hold,
               "busy competitors do not modify managed copies")
        record(holder.poll() is None, "busy competitors leave the holder alive")

        lock = dest / ".scratch" / "spec-sync" / "lock"
        lock_bytes = lock.read_bytes()
        record(str(holder.pid).encode() not in lock_bytes,
               "lock file carries no holder PID", repr(lock_bytes))

        # A POSIX process can unlink an open lock file. A cooperating tool
        # process that runs after this deletion must still observe the lock.
        lock.unlink()
        record(not lock.exists(), "POSIX unlink of the held lock file succeeds")
        unlinked_check = run_cli(source, dest, check=True)
        record(unlinked_check.returncode != 0 and "busy" in unlinked_check.stdout,
               "check after lock-file deletion still reports busy",
               unlinked_check.stdout.strip())
        unlinked_sync = run_cli(source, dest, check=False)
        record(unlinked_sync.returncode != 0 and "busy" in unlinked_sync.stdout,
               "sync after lock-file deletion still reports busy",
               unlinked_sync.stdout.strip())
        record(content_bytes(dest) == expected_content,
               "lock-file deletion competitors do not modify managed content")
        record(holder.poll() is None, "lock-file deletion competitors leave the holder alive")

        assert holder.stdin is not None
        holder.communicate("release\n", timeout=180)
        record(holder.returncode == 0, "lock holder exits normally", f"exit={holder.returncode}")
        result = run_cli(source, dest, check=True)
        record(result.returncode == 0, "check works after the holder exits", result.stdout.strip())
        result = run_cli(source, dest, check=False)
        record(result.returncode == 0, "sync works after the holder exits", result.stdout.strip())
        record(content_bytes(dest) == expected_content,
               "managed content is unchanged by the release/reuse cycle")
        record(lock.exists(), "the next successful operation restores the lock file")

        failures = [name for ok, name, _ in RESULTS if not ok]
        print(f"SUMMARY: {len(RESULTS) - len(failures)} passed, {len(failures)} failed", flush=True)
        if failures:
            print("FAILED:", ", ".join(failures), flush=True)
            return 1
        print("PASS POSIX C1.04 probe: source change, sync, shared process lock, "
              "busy diagnostics, lock-file deletion rejection, release/reuse", flush=True)
        return 0
    finally:
        if holder is not None and holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=30)
        resolved = root.resolve()
        base = Path(tempfile.gettempdir()).resolve()
        if resolved.is_relative_to(base) and resolved.name.startswith("c104-posix-"):
            shutil.rmtree(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
