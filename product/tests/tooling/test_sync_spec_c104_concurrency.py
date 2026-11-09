"""C1.04 / #141 multi-process and cross-platform acceptance of spec sync.

This module is intentionally C1.04-specific; the historical #137 suite,
the C1.01 re-verification and the C1.02 sync/recovery suite stay untouched so
each ticket's evidence remains separately labelled and parallel branches merge
cleanly.

Every concurrency assertion uses two or more *real* processes and the real
command entry point (``tools/sync_spec.py --source ... --dest ...``) with real
temporary directories, real file bytes and real exit statuses. Holder processes
run the actual ``sync()``/``check()`` operations and are paused only at a
private file-operation seam (the design's allowed injection seam); no production
fault-injection endpoint is added.

Covered here:

* check and sync share one workspace mutex; competitors fail fast with a
  ``busy`` diagnostic while a real operation holds the lock; a busy competitor
  cannot modify the managed copies, delete the lock file, guess a PID or kill
  the holder;
* after the holder releases normally, check and sync work again;
* success is zero and drift / unverifiable / busy failures are nonzero with
  distinguishable diagnostics, on both the CLI and the function entries;
* real directory junctions (Windows) / symlinks (POSIX) at the private lock
  state are rejected before any write;
* text newline equivalence, binary comparison, normal same-repo nesting and
  metadata fields are not treated as content drift.

The POSIX ``fcntl`` leg cannot run in this Windows pytest invocation; the
recorded WSL probe (``reports/architecture/C1.04-posix-probe.py``) exercises it
with real Linux processes. The platform-conditional branch below is what a
Linux pytest run would assert.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS = PRODUCT_ROOT / "tools"
TOOL = TOOLS / "sync_spec.py"
sys.path.insert(0, str(TOOLS))

import sync_spec as sync  # noqa: E402

HOLDER_SCRIPTS = {
    # Real sync paused inside the process lock, before it touches targets.
    "sync": """
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
""",
    # Real check paused inside the process lock, in its audit step.
    "check": """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync
original = sync._audit
def paused(layout, files):
    print("ready", flush=True)
    sys.stdin.readline()
    return original(layout, files)
sync._audit = paused
sync.check(Path(sys.argv[2]), Path(sys.argv[3]))
print("done", flush=True)
""",
    # Real sync paused after its verified commit, while it still holds the lock.
    "committed_sync": """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync
original = sync._clean_slots
calls = 0
def paused(layout):
    global calls
    calls += 1
    if calls == 2:
        print("ready", flush=True)
        sys.stdin.readline()
    return original(layout)
sync._clean_slots = paused
sync.sync(Path(sys.argv[2]), Path(sys.argv[3]))
print("done", flush=True)
""",
}


def run_cli(source: Path, dest: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    """Run the real command; the caller observes exit status and diagnostics."""
    command = [sys.executable, "-X", "utf8", str(TOOL),
               "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=180)


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A real source tree published by the real CLI to a real destination."""
    source = tmp_path / "repo"
    (source / "contracts").mkdir(parents=True)
    (source / "docs").mkdir()
    (source / "README.md").write_bytes(b"# spec\n")
    (source / "docs" / "HANDOFF.md").write_bytes(b"# handoff\n")
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0"}\n')
    dest = tmp_path / "product"
    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return source, dest


def managed_bytes(dest: Path) -> dict[str, bytes]:
    """Every file of both managed copies, including the spec baseline."""
    result: dict[str, bytes] = {}
    for root, label in ((dest / "docs" / "spec", "spec"), (dest / "contracts", "contracts")):
        if root.is_dir():
            for path in root.rglob("*"):
                if path.is_file():
                    result[f"{label}/{path.relative_to(root).as_posix()}"] = path.read_bytes()
    return result


def content_bytes(dest: Path) -> dict[str, bytes]:
    """Managed file bytes without the spec baseline's volatile metadata."""
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


def read_line(process: subprocess.Popen[str], timeout: float) -> str:
    """Read the holder's readiness line without hanging on a failed child."""
    value: dict[str, str] = {}

    def read() -> None:
        value["line"] = process.stdout.readline() if process.stdout else ""

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"holder did not report readiness within {timeout}s"
    return value.get("line", "").strip()


@contextmanager
def running_holder(source: Path, dest: Path, operation: str) -> Iterator[subprocess.Popen[str]]:
    """Start a real operation in a separate process and hold its workspace lock."""
    process = subprocess.Popen(
        [sys.executable, "-X", "utf8", "-c", HOLDER_SCRIPTS[operation],
         str(TOOLS), str(source), str(dest)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        line = read_line(process, 60)
        assert line == "ready", (
            f"{operation} holder did not reach its locked section: {line!r} "
            f"{process.stderr.read() if process.poll() is not None and process.stderr else ''}")
        yield process
    finally:
        if process.poll() is None:
            try:
                process.communicate("release\n", timeout=180)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=30)
        else:
            process.communicate(timeout=30)


def busy_failure(source: Path, dest: Path, *, check: bool) -> subprocess.CompletedProcess[str]:
    """Run a competing command and require a fast, non-modifying busy failure."""
    started = time.monotonic()
    result = run_cli(source, dest, check=check)
    elapsed = time.monotonic() - started
    assert result.returncode != 0, f"competitor was not rejected: {result.stdout}"
    combined = result.stdout + result.stderr
    assert "busy" in combined, combined
    assert "another check or sync holds the lock" in combined, combined
    assert elapsed < 10, f"competitor took {elapsed:.1f}s instead of failing fast"
    return result


def make_dir_link(link: Path, target: Path) -> None:
    """Create a real directory redirect; junctions keep Windows testable."""
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def remove_dir_link(link: Path) -> None:
    if sys.platform == "win32":
        link.rmdir()
    else:
        link.unlink()


def journal_path(dest: Path) -> Path:
    return dest / ".scratch" / "spec-sync" / "transaction.json"


@pytest.mark.parametrize("holder_operation", ["sync", "check"])
def test_check_and_sync_share_the_workspace_mutex(workspace, holder_operation):
    """Both real entry points take the same lock; competitors fail fast busy."""
    source, dest = workspace
    before = managed_bytes(dest)
    before_content = content_bytes(dest)

    with running_holder(source, dest, holder_operation) as holder:
        for check in (True, False):
            busy_failure(source, dest, check=check)
            assert managed_bytes(dest) == before, "a busy competitor modified managed copies"
            assert not journal_path(dest).exists(), "a busy competitor created transaction state"
        assert holder.poll() is None, "a competitor killed or reaped the lock holder"

    assert holder.returncode == 0, "the lock holder did not exit normally"
    result = run_cli(source, dest, check=True)
    assert result.returncode == 0, result.stdout
    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout
    result = run_cli(source, dest, check=True)
    assert result.returncode == 0, result.stdout
    assert content_bytes(dest) == before_content


def test_normal_exit_releases_the_lock_without_competitor_mutation(workspace):
    """After a normal exit, check and sync work; a busy competitor changed nothing."""
    source, dest = workspace
    source_a = content_bytes(dest)

    # The holder publishes generation B and pauses after its verified commit,
    # still holding the lock. The competitor wants generation C but is busy.
    (source / "README.md").write_bytes(b"# generation B\n")
    (source / "NEW.md").write_bytes(b"# new in B\n")
    with running_holder(source, dest, "committed_sync") as holder:
        committed = content_bytes(dest)
        assert committed["spec/README.md"] == b"# generation B\n"
        assert committed["spec/NEW.md"] == b"# new in B\n"
        (source / "README.md").write_bytes(b"# generation C\n")
        (source / "NEW.md").unlink()
        busy_failure(source, dest, check=False)
        busy_failure(source, dest, check=True)
        assert content_bytes(dest) == committed, (
            "the competitor changed the committed generation while busy")
        assert holder.poll() is None

    assert holder.returncode == 0
    result = run_cli(source, dest, check=True)
    assert result.returncode != 0
    assert "source_modified" in result.stdout
    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout
    result = run_cli(source, dest, check=True)
    assert result.returncode == 0, result.stdout
    assert content_bytes(dest) != source_a
    assert content_bytes(dest)["spec/README.md"] == b"# generation C\n"
    assert "spec/NEW.md" not in content_bytes(dest)


def test_busy_failure_does_not_delete_the_lock_file_or_use_a_pid(workspace):
    """The competitor cannot evict the holder via lock-file deletion or PID tricks."""
    source, dest = workspace
    lock = dest / ".scratch" / "spec-sync" / "lock"
    before = managed_bytes(dest)

    with running_holder(source, dest, "check") as holder:
        lock_mtime = lock.stat().st_mtime_ns
        if sys.platform == "win32":
            # The mandatory Windows byte-range lock makes even reading the
            # locked byte fail; the OS owns the file, not a PID record.
            with pytest.raises(PermissionError):
                lock.read_bytes()
            assert lock.stat().st_size == 1, "the lock payload could contain a PID"
        else:
            lock_bytes = lock.read_bytes()
            assert str(holder.pid).encode() not in lock_bytes, "the lock file stores a PID"
            assert len(lock_bytes) <= 16, f"unexpected lock payload: {lock_bytes!r}"

        for check in (True, False):
            result = busy_failure(source, dest, check=check)
            combined = (result.stdout + result.stderr).lower()
            assert str(holder.pid) not in combined, "diagnostics guess a holder PID"
            assert "pid=" not in combined, "diagnostics expose a PID-based takeover"
            assert "stale" not in combined and "timeout" not in combined
        assert lock.exists()
        if sys.platform == "win32":
            assert lock.stat().st_size == 1
        else:
            assert lock.read_bytes() == lock_bytes
        assert lock.stat().st_mtime_ns == lock_mtime, "a competitor rewrote the lock file"
        assert managed_bytes(dest) == before

        if sys.platform == "win32":
            # Windows keeps the open lock file undeletable; the OS enforces it.
            with pytest.raises(OSError) as failure:
                lock.unlink()
            assert isinstance(failure.value, PermissionError)
            assert getattr(failure.value, "winerror", None) == 32
            assert lock.exists()
        else:
            # POSIX allows unlinking an open file. The workspace directory lock
            # must still make a fresh tool process report busy, not steal.
            lock.unlink()
            assert not lock.exists()
            busy_failure(source, dest, check=True)
            busy_failure(source, dest, check=False)
            assert holder.poll() is None

        assert managed_bytes(dest) == before, "lock-file manipulation changed managed copies"
        assert holder.poll() is None, "the holder was killed instead of the lock being honored"

    assert holder.returncode == 0
    result = run_cli(source, dest, check=True)
    assert result.returncode == 0, result.stdout
    assert lock.exists(), "the next successful operation did not restore the lock file"
    final_bytes = lock.read_bytes()
    assert str(holder.pid).encode() not in final_bytes, "the lock file stores a PID"
    assert len(final_bytes) <= 16


def test_entry_points_keep_success_and_distinguishable_failure_statuses(workspace):
    """Success is zero; drift, unverifiable and busy are nonzero and distinct."""
    source, dest = workspace

    result = run_cli(source, dest, check=True)
    assert result.returncode == 0
    assert "no drift" in result.stdout
    result = run_cli(source, dest, check=False)
    assert result.returncode == 0
    assert "copied" in result.stdout

    assert sync.check(source, dest) == []
    succeeded = sync.sync(source, dest)
    assert set(("copied", "manifest", "files", "contracts", "recovered",
                "warnings")).issubset(succeeded)
    assert succeeded["copied"] > 0 and succeeded["manifest"].is_file()
    assert succeeded["recovered"] is False and succeeded["warnings"] == []

    (source / "README.md").write_bytes(b"# drift\n")
    drift = sync.check(source, dest)
    assert drift and drift[0]["kind"] == "source_modified"
    result = run_cli(source, dest, check=True)
    assert result.returncode != 0 and "source_modified" in result.stdout
    assert run_cli(source, dest, check=False).returncode == 0
    assert sync.check(source, dest) == []

    missing = source.with_name("repo-missing")
    source.rename(missing)
    # The original path is now absent: an unverifiable source, not a rename.
    unverifiable = sync.check(source, dest)
    assert unverifiable and unverifiable[0]["kind"] == "source_unavailable"
    result = run_cli(source, dest, check=True)
    assert result.returncode != 0 and "source_unavailable" in result.stdout
    result = run_cli(source, dest, check=False)
    assert result.returncode != 0 and "source_unavailable" in result.stdout
    with pytest.raises(sync.SyncError) as failure:
        sync.sync(source, dest)
    assert failure.value.finding["kind"] == "source_unavailable"
    missing.rename(source)

    with running_holder(source, dest, "sync"):
        result = run_cli(source, dest, check=True)
        assert result.returncode != 0 and "busy" in result.stdout
        assert "source_modified" not in result.stdout
        assert sync.check(source, dest)[0]["kind"] == "busy"
        result = run_cli(source, dest, check=False)
        assert result.returncode != 0 and "busy" in result.stdout
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "busy"

    assert run_cli(source, dest, check=True).returncode == 0


@pytest.mark.parametrize("redirected", ["state", "parent"])
def test_lock_state_directory_redirects_are_rejected(workspace, redirected):
    """A real junction/symlink at the private lock state is never followed."""
    source, dest = workspace
    outside = dest.parent / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")
    before = managed_bytes(dest)

    link = dest / ".scratch" / "spec-sync" if redirected == "state" else dest / ".scratch"
    if link.exists():
        shutil.rmtree(link)
    make_dir_link(link, outside)
    try:
        result = run_cli(source, dest, check=True)
        assert result.returncode != 0 and "unsafe_path" in result.stdout, result.stdout
        result = run_cli(source, dest, check=False)
        assert result.returncode != 0 and "unsafe_path" in result.stdout, result.stdout
        assert sentinel.read_bytes() == b"keep\n"
        assert managed_bytes(dest) == before
    finally:
        remove_dir_link(link)

    assert run_cli(source, dest, check=True).returncode == 0
    assert sentinel.read_bytes() == b"keep\n"


def test_newline_binary_nesting_and_metadata_semantics_are_preserved(tmp_path):
    """Text equivalence, binary comparison, nesting and metadata do not drift."""
    source = tmp_path / "repo"
    (source / "contracts").mkdir(parents=True)
    (source / "README.md").write_bytes(b"# spec\n")
    (source / "text.txt").write_bytes(b"line one\r\nline two\r\n")
    (source / "binary.bin").write_bytes(b"\xff\r\n")
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0"}\n')
    dest = source / "workspace"  # Normal same-repository nesting.

    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert run_cli(source, dest, check=True).returncode == 0
    assert not (dest / "docs" / "spec" / "workspace").exists(), (
        "the nested destination was copied into the specification copy")

    # Pure text newline changes are equivalent, not drift.
    (dest / "docs" / "spec" / "text.txt").write_bytes(b"line one\nline two\n")
    assert run_cli(source, dest, check=True).returncode == 0

    # Undecodable bytes are compared as-is, so CRLF normalization cannot hide them.
    (dest / "docs" / "spec" / "binary.bin").write_bytes(b"\xff\n")
    result = run_cli(source, dest, check=True)
    assert result.returncode != 0
    assert "modified" in result.stdout and "binary.bin" in result.stdout
    assert run_cli(source, dest, check=False).returncode == 0
    assert run_cli(source, dest, check=True).returncode == 0

    # Machine-specific metadata is diagnostic, never content identity.
    manifest_path = dest / "docs" / "spec" / sync.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "1999-01-01T00:00:00+00:00"
    manifest["source_root"] = "/some/other/machine/repo"
    manifest["source_commit"] = "0" * 40
    manifest["generation"] = "f" * 32
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = run_cli(source, dest, check=True)
    assert result.returncode == 0, result.stdout
