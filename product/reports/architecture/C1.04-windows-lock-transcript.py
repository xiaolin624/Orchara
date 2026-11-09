#!/usr/bin/env python3
"""C1.04 / #141 Windows two-process lock transcript (evidence, not a test).

Runs the real command in real subprocesses over real temporary directories and
prints exact commands, exit statuses and stdout so the report does not have to
restate pytest dot output. The pytest module is the regression; this transcript
is the human-readable raw evidence for Windows.

Run from the product directory:
  D:\\Python\\python.exe -X utf8 reports\\architecture\\C1.04-windows-lock-transcript.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS = PRODUCT_ROOT / "tools"
TOOL = TOOLS / "sync_spec.py"
PYTHON = sys.executable

HOLDER = """
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


def run_cli(source: Path, dest: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    command = [PYTHON, "-X", "utf8", str(TOOL), "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    print("COMMAND:", subprocess.list2cmdline(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    print("EXIT:", result.returncode, flush=True)
    print("STDOUT:", result.stdout.strip(), flush=True)
    if result.stderr.strip():
        print("STDERR:", result.stderr.strip(), flush=True)
    return result


def managed_digest(dest: Path) -> str:
    digest = hashlib.sha256()
    for root in (dest / "docs" / "spec", dest / "contracts"):
        for path in sorted(root.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(dest).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="c104-win-"))
    holder: subprocess.Popen[str] | None = None
    try:
        source = root / "repo"
        (source / "contracts").mkdir(parents=True)
        (source / "README.md").write_bytes(b"# spec\n")
        (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0"}\n')
        dest = root / "product"
        print("WINDOWS:", sys.getwindowsversion(), "PYTHON:", sys.version.split()[0], flush=True)
        print("TEMP ROOT:", root, flush=True)

        result = run_cli(source, dest, check=False)
        assert result.returncode == 0
        result = run_cli(source, dest, check=True)
        assert result.returncode == 0

        holder = subprocess.Popen(
            [PYTHON, "-X", "utf8", "-c", HOLDER, str(TOOLS), str(source), str(dest)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        assert holder.stdout is not None
        line = holder.stdout.readline().strip()
        print("HOLDER: pid", holder.pid, "said", repr(line), flush=True)
        before = managed_digest(dest)

        run_cli(source, dest, check=True)
        run_cli(source, dest, check=False)
        print("MANAGED DIGEST UNCHANGED BY COMPETITORS:",
              managed_digest(dest) == before, flush=True)

        lock = dest / ".scratch" / "spec-sync" / "lock"
        try:
            lock.read_bytes()
            print("LOCK READ: unexpectedly succeeded", flush=True)
        except OSError as error:
            print(f"LOCK READ: {type(error).__name__} winerror={getattr(error, 'winerror', None)}",
                  flush=True)
        try:
            lock.unlink()
            print("LOCK DELETE: unexpectedly succeeded", flush=True)
        except OSError as error:
            print(f"LOCK DELETE: {type(error).__name__} winerror={getattr(error, 'winerror', None)}",
                  flush=True)
        print("HOLDER ALIVE AFTER COMPETITORS:", holder.poll() is None, flush=True)
        print("MANAGED DIGEST AFTER DELETE ATTEMPT:",
              managed_digest(dest) == before, flush=True)

        assert holder.stdin is not None
        holder.communicate("release\n", timeout=180)
        print("HOLDER EXIT:", holder.returncode, flush=True)
        result = run_cli(source, dest, check=True)
        assert result.returncode == 0
        result = run_cli(source, dest, check=False)
        assert result.returncode == 0
        result = run_cli(source, dest, check=True)
        assert result.returncode == 0
        print("LOCK REUSABLE AFTER NORMAL EXIT: True", flush=True)
        print("PASS Windows C1.04 lock transcript", flush=True)
        return 0
    finally:
        if holder is not None and holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=30)
        resolved = root.resolve()
        base = Path(tempfile.gettempdir()).resolve()
        if resolved.is_relative_to(base) and resolved.name.startswith("c104-win-"):
            shutil.rmtree(resolved)


if __name__ == "__main__":
    raise SystemExit(main())
