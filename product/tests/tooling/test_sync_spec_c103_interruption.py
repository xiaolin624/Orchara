"""C1.03 / #140 acceptance re-verification: process interruption and explicit recovery.

Scope: the already-implemented ``product/tools/sync_spec.py``. This module does not
change ``check``/``sync`` or the command interface. It adds the interruption
coverage that the historical #137 suite and the C1.02 suite did not observe at the
command layer:

* a real independent child process terminating with ``os._exit(73)`` at each of
  the four directory-switch positions (and at the journal/staging boundaries);
* the existing ``python tools/sync_spec.py --check`` command reporting
  ``incomplete_sync`` with a non-zero status, and a later explicit sync repairing
  the identifiable previous state before publishing the current source;
* an interruption during recovery itself, repeated for every recovery move, so a
  later explicit sync can still recover from the fixed slots without losing the
  last complete backup;
* an interrupted first sync with no old generation, returning to the prior
  non-existence before re-initialising;
* an invalid recovery record, a corrupt backup and an unexplained external
  modification of a managed copy, all preserved and failing closed;
* an invalid source starting no recovery mutation or cleanup, and check never
  repairing a managed copy.

Evidence categories stay separate. Every interruption here is a real process
death (``os._exit``), never an ordinary exception raised inside the pytest
process. Ordinary I/O failures remain covered by the historical #137 suite and
the C1.02 suite through the private file-operation seams. Physical power loss is
**not** verified: the two managed directories still do not share one atomic
filesystem commit. The transaction journal makes the in-between state detectable
and recoverable after a process exit; it is not a claim of power-fail durability.

All observations go through the real command seam (a real subprocess with real
temporary directories and real exit status) or the same module's
``check``/``sync`` entry, and are asserted against actual file bytes, the actual
``SYNC-MANIFEST.json`` baseline and the actual transaction record.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS = PRODUCT_ROOT / "tools"
TOOL = TOOLS / "sync_spec.py"
sys.path.insert(0, str(TOOLS))

import sync_spec as sync  # noqa: E402

SPEC_SUBDIR = Path("docs") / "spec"
SLOT_NAMES = ("stage-spec", "stage-contracts", "old-spec", "old-contracts",
              "discard-spec", "discard-contracts")

# Files outside the two managed copies. Interruption handling must not touch them.
UNMANAGED_FILES = {
    "src/app.py": b"print('product code')\n",
    "reports/architecture/sentinel.txt": b"# report sentinel\n",
    ".scratch/notes/keep.txt": b"# unrelated private state\n",
}

CRASH_SCRIPT = """
import os, sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import sync_spec as sync

mode, count = sys.argv[4], int(sys.argv[5])

if mode == "move":
    original = sync._move
    moves = 0

    def crash_after_move(layout, from_path, to_path):
        global moves
        original(layout, from_path, to_path)
        moves += 1
        if moves == count:
            os._exit(73)

    sync._move = crash_after_move
elif mode == "copy":
    original_copy = sync.shutil.copy2
    copies = 0

    def crash_after_copy(src, dst, *args, **kwargs):
        global copies
        result = original_copy(src, dst, *args, **kwargs)
        copies += 1
        if copies == count:
            os._exit(73)
        return result

    sync.shutil.copy2 = crash_after_copy
elif mode == "journal":
    original_write = sync._write_json

    def crash_after_journal(path, value):
        original_write(path, value)
        if Path(path).name == "transaction.json":
            os._exit(73)

    sync._write_json = crash_after_journal
elif mode == "cleanup":
    original_rmtree = sync.shutil.rmtree

    def crash_after_cleanup(path, *args, **kwargs):
        original_rmtree(path, *args, **kwargs)
        os._exit(73)

    sync.shutil.rmtree = crash_after_cleanup
else:
    raise SystemExit("unknown crash mode: " + mode)

raise SystemExit(sync.main(["--source", sys.argv[2], "--dest", sys.argv[3]]))
"""


def run_cli(source: Path, dest: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the real command and return its real exit status and diagnostics."""
    command = [sys.executable, "-X", "utf8", str(TOOL),
               "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def run_crashing_sync(source: Path, dest: Path, *, mode: str, count: int = 1
                      ) -> subprocess.CompletedProcess[str]:
    """Terminate an independent command process with os._exit(73) at one seam.

    ``mode`` names the real position: ``move`` exits after the Nth directory
    switch/recovery move, ``copy`` during staging, ``journal`` right after the
    pending record is written and ``cleanup`` during post-commit housekeeping.
    A non-73 exit means the child never reached the requested point; that is a
    test failure, not a process-exit observation.
    """
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", textwrap.dedent(CRASH_SCRIPT),
         str(TOOLS), str(source), str(dest), mode, str(count)],
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 73, (
        f"child never reached the {mode} crash point {count} "
        f"(exit {result.returncode})\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    assert "Traceback" not in result.stderr, result.stderr
    return result


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def spec_copy(dest: Path) -> Path:
    return dest / SPEC_SUBDIR


def contract_copy(dest: Path) -> Path:
    return dest / "contracts"


def state_path(dest: Path) -> Path:
    return dest / ".scratch" / "spec-sync"


def journal_path(dest: Path) -> Path:
    return state_path(dest) / "transaction.json"


def finding_kinds(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.split(None, 1)[0] for line in result.stdout.splitlines()
            if line.startswith("  ") and not line.strip().startswith("...")}


def unmanaged_bytes(dest: Path) -> dict[str, bytes]:
    return {name: (dest / name).read_bytes() for name in UNMANAGED_FILES}


def build_source(root: Path) -> Path:
    (root / "contracts").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "README.md").write_bytes(b"# spec\n")
    (root / "docs" / "HANDOFF.md").write_bytes(b"# handoff\n")
    (root / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0"}\n')
    (root / "contracts" / "schema.json").write_bytes(b'{"type": "object"}\n')
    return root


def seed_unmanaged(dest: Path) -> None:
    for name, data in UNMANAGED_FILES.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def assert_copies_match_source(source: Path, dest: Path) -> None:
    expected_spec = {path.relative_to(source).as_posix(): path.read_bytes()
                     for path in source.rglob("*") if path.is_file()}
    actual_spec = {name: data for name, data in tree_bytes(spec_copy(dest)).items()
                   if name != sync.MANIFEST_NAME}
    assert actual_spec == expected_spec, "spec copy is not the exact source projection"
    expected_contracts = {path.relative_to(source / "contracts").as_posix(): path.read_bytes()
                          for path in (source / "contracts").rglob("*") if path.is_file()}
    assert tree_bytes(contract_copy(dest)) == expected_contracts, \
        "runtime contract copy is not the exact source projection"
    assert (spec_copy(dest) / sync.MANIFEST_NAME).is_file()


def assert_last_complete_old_generation(dest: Path, old_spec: dict[str, bytes],
                                        old_contracts: dict[str, bytes]) -> None:
    """The last complete generation must stay in an active copy or its backup slot."""
    state = state_path(dest)
    for prefix, expected in (("spec", old_spec), ("contracts", old_contracts)):
        active = spec_copy(dest) if prefix == "spec" else contract_copy(dest)
        backup = state / ("old-" + prefix)
        observed = [tree_bytes(path) for path in (active, backup) if path.is_dir()]
        assert expected in observed, (
            f"the last complete {prefix} generation is neither active nor in old-{prefix}; "
            f"observed slots: {observed}")


def assert_no_slots(dest: Path) -> None:
    state = state_path(dest)
    assert not any((state / name).exists() for name in SLOT_NAMES)


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A real source synchronized by the real command into a real product dir."""
    source = build_source(tmp_path / "repo")
    dest = tmp_path / "product"
    seed_unmanaged(dest)
    result = run_cli(source, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    return source, dest


# ---------------------------------------------------------------------------
# AC1: after a real child exits at any of the four switch positions, the
# existing check command exits non-zero and reports the unfinished transaction;
# the checking command itself never repairs or cleans it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("switch_position", [1, 2, 3, 4])
def test_child_exit_at_each_switch_position_is_an_incomplete_transaction(workspace, switch_position):
    source, dest = workspace
    old_spec = tree_bytes(spec_copy(dest))
    old_contracts = tree_bytes(contract_copy(dest))
    (source / "README.md").write_bytes(b"# next generation\n")

    run_crashing_sync(source, dest, mode="move", count=switch_position)

    journal = journal_path(dest)
    assert journal.is_file(), "a real switch interruption did not leave its pending record"
    assert json.loads(journal.read_text(encoding="utf-8"))["schema"] == "orchara.spec-sync-tx/v1"
    assert_last_complete_old_generation(dest, old_spec, old_contracts)

    observed = tree_bytes(dest)
    for attempt in range(2):
        checked = run_cli(source, dest, check=True)
        assert checked.returncode == 1, checked.stdout + checked.stderr
        assert "drift or verification failure" in checked.stdout
        assert "incomplete_sync" in checked.stdout
        assert "previous sync was interrupted; run sync to recover" in checked.stdout
        assert tree_bytes(dest) == observed, (
            f"check attempt {attempt + 1} repaired, cleaned or rewrote an interrupted transaction")
        assert journal.is_file(), "check removed the pending record"
    assert sync.check(source, dest)[0]["kind"] == "incomplete_sync"
    assert unmanaged_bytes(dest) == UNMANAGED_FILES


# ---------------------------------------------------------------------------
# AC2: with a valid (and changed) source, the second explicit sync first
# recovers the identifiable previous state and then publishes the current
# source; both copies and the baseline pass check afterwards.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("switch_position", [1, 2, 3, 4])
def test_second_explicit_sync_recovers_before_publishing_the_current_source(workspace, switch_position):
    source, dest = workspace
    (source / "README.md").write_bytes(b"# current source\n")
    (source / "docs" / "NEW.md").write_bytes(b"# added while the sync was interrupted\n")

    run_crashing_sync(source, dest, mode="move", count=switch_position)
    interrupted = run_cli(source, dest, check=True)
    assert interrupted.returncode == 1
    assert "incomplete_sync" in interrupted.stdout

    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert "warning:" not in recovered.stdout
    assert not journal_path(dest).exists(), "the recovered transaction was not committed"
    assert_no_slots(dest)

    assert_copies_match_source(source, dest)
    assert (spec_copy(dest) / "README.md").read_bytes() == b"# current source\n"
    assert (spec_copy(dest) / "docs" / "NEW.md").read_bytes() == \
        b"# added while the sync was interrupted\n"

    final = run_cli(source, dest, check=True)
    assert final.returncode == 0, final.stdout + final.stderr
    assert "no drift" in final.stdout
    assert sync.check(source, dest) == []
    assert unmanaged_bytes(dest) == UNMANAGED_FILES


# ---------------------------------------------------------------------------
# Recovery ordering: after recovery commits, the current generation is still
# being staged. Observing that death point shows the previous complete state on
# disk and no pending record, i.e. recovery really happened first.
# ---------------------------------------------------------------------------


def test_recovery_is_committed_before_the_new_generation_is_installed(workspace):
    source, dest = workspace
    old_spec = tree_bytes(spec_copy(dest))
    old_contracts = tree_bytes(contract_copy(dest))
    (source / "README.md").write_bytes(b"# changed after the interruption\n")

    run_crashing_sync(source, dest, mode="move", count=2)
    second = run_crashing_sync(source, dest, mode="copy", count=1)
    assert second.returncode == 73

    assert not journal_path(dest).exists(), "recovery had not committed before staging began"
    assert tree_bytes(spec_copy(dest)) == old_spec, "the previous spec copy was not restored"
    assert tree_bytes(contract_copy(dest)) == old_contracts, "the previous contract copy was not restored"

    observed = run_cli(source, dest, check=True)
    assert observed.returncode == 1
    assert finding_kinds(observed) == {"source_modified"}, observed.stdout
    assert "incomplete_sync" not in observed.stdout

    final = run_cli(source, dest, check=False)
    assert final.returncode == 0, final.stdout + final.stderr
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []
    assert (spec_copy(dest) / "README.md").read_bytes() == b"# changed after the interruption\n"


# ---------------------------------------------------------------------------
# AC3: interrupting recovery itself is repeatable. Every recovery move is
# exercised after every switch position; a later explicit sync still recovers
# from the fixed slots and never loses the last complete backup.
# ---------------------------------------------------------------------------

RECOVERY_INTERRUPTION_CASES = [
    (1, 1),
    (2, 1), (2, 2),
    (3, 1), (3, 2), (3, 3),
    (4, 1), (4, 2), (4, 3), (4, 4),
]


@pytest.mark.parametrize(("switch_position", "recovery_position"), RECOVERY_INTERRUPTION_CASES)
def test_interrupted_recovery_stays_repeatable_without_losing_the_backup(
        workspace, switch_position, recovery_position):
    source, dest = workspace
    old_spec = tree_bytes(spec_copy(dest))
    old_contracts = tree_bytes(contract_copy(dest))
    (source / "README.md").write_bytes(b"# next generation\n")

    run_crashing_sync(source, dest, mode="move", count=switch_position)
    assert_last_complete_old_generation(dest, old_spec, old_contracts)

    run_crashing_sync(source, dest, mode="move", count=recovery_position)
    assert journal_path(dest).is_file(), "the interrupted recovery dropped its record"
    assert_last_complete_old_generation(dest, old_spec, old_contracts)

    observed = tree_bytes(dest)
    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "incomplete_sync" in checked.stdout
    assert tree_bytes(dest) == observed, "check repaired an interrupted recovery"

    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert not journal_path(dest).exists()
    assert_no_slots(dest)
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []
    assert (spec_copy(dest) / "README.md").read_bytes() == b"# next generation\n"


# ---------------------------------------------------------------------------
# AC4: an interrupted first sync has no old generation. Recovery returns both
# managed locations to their prior non-existence before re-initialising.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("switch_position", [1, 2])
def test_interrupted_first_sync_recovers_to_absence_then_reinitializes(tmp_path, switch_position):
    source = build_source(tmp_path / "repo")
    dest = tmp_path / "product"
    seed_unmanaged(dest)

    run_crashing_sync(source, dest, mode="move", count=switch_position)
    assert journal_path(dest).is_file()
    assert spec_copy(dest).exists() or contract_copy(dest).exists(), \
        "the first sync did not install anything before the process exit"

    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "incomplete_sync" in checked.stdout

    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert not journal_path(dest).exists()
    assert_no_slots(dest)
    assert_copies_match_source(source, dest)
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []
    assert unmanaged_bytes(dest) == UNMANAGED_FILES


@pytest.mark.parametrize("switch_position", [1, 2])
def test_first_sync_recovery_restores_nonexistence_before_reinitializing(tmp_path, switch_position):
    source = build_source(tmp_path / "repo")
    dest = tmp_path / "product"
    seed_unmanaged(dest)

    run_crashing_sync(source, dest, mode="move", count=switch_position)
    # Recover, then die while staging the re-initialisation.
    run_crashing_sync(source, dest, mode="copy", count=1)

    assert not journal_path(dest).exists(), "the first-sync recovery had not committed"
    assert not spec_copy(dest).exists(), "the prior non-existence of the spec copy was not restored"
    assert not contract_copy(dest).exists(), "the prior non-existence of the contract copy was not restored"

    observed = run_cli(source, dest, check=True)
    assert observed.returncode == 1
    assert finding_kinds(observed) == {"missing_manifest"}, observed.stdout
    assert "incomplete_sync" not in observed.stdout

    final = run_cli(source, dest, check=False)
    assert final.returncode == 0, final.stdout + final.stderr
    assert run_cli(source, dest, check=True).returncode == 0
    assert_copies_match_source(source, dest)
    assert unmanaged_bytes(dest) == UNMANAGED_FILES


# ---------------------------------------------------------------------------
# AC5: invalid recovery record, corrupt backup and unexplained external
# modification of a managed copy keep their evidence and fail closed. Nothing
# is guessed, no cleanup path is widened, no outside file is touched.
# ---------------------------------------------------------------------------


def test_invalid_recovery_record_is_preserved_and_fails_closed(workspace, tmp_path):
    source, dest = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")

    run_crashing_sync(source, dest, mode="move", count=2)
    journal = journal_path(dest)
    valid = journal.read_bytes()
    record = json.loads(valid)
    record["layout"]["spec"] = "../outside"
    journal.write_text(json.dumps(record), encoding="utf-8")
    invalid = journal.read_bytes()
    observed = tree_bytes(dest)

    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "incomplete_sync" in checked.stdout

    failed = run_cli(source, dest, check=False)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "recovery_required" in failed.stdout
    assert "Traceback" not in failed.stderr
    assert journal.read_bytes() == invalid, "the invalid record was rewritten or removed"
    assert tree_bytes(dest) == observed, "an invalid record caused a guess or a cleanup"
    assert sentinel.read_bytes() == b"keep\n"
    assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]

    with pytest.raises(sync.SyncError) as failure:
        sync.sync(source, dest)
    assert failure.value.finding["kind"] == "recovery_required"
    assert journal.read_bytes() == invalid

    journal.write_bytes(valid)
    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []


def test_corrupt_backup_is_preserved_and_fails_closed(workspace, tmp_path):
    source, dest = workspace
    old_contracts = tree_bytes(contract_copy(dest))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")

    run_crashing_sync(source, dest, mode="move", count=3)
    backup = state_path(dest) / "old-contracts"
    assert tree_bytes(backup) == old_contracts
    (backup / "openapi.json").write_bytes(b'{"corrupted": true}\n')
    corrupt = tree_bytes(backup)
    journal = journal_path(dest)
    journal_before = journal.read_bytes()

    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "incomplete_sync" in checked.stdout

    failed = run_cli(source, dest, check=False)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "recovery_required" in failed.stdout
    assert "backup does not match previous state" in failed.stdout
    assert journal.read_bytes() == journal_before
    assert tree_bytes(backup) == corrupt, "the corrupt backup evidence was modified or deleted"
    assert sentinel.read_bytes() == b"keep\n"
    assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]
    assert unmanaged_bytes(dest) == UNMANAGED_FILES

    (backup / "openapi.json").write_bytes(old_contracts["openapi.json"])
    assert tree_bytes(backup) == old_contracts
    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert run_cli(source, dest, check=True).returncode == 0
    assert_copies_match_source(source, dest)


def test_unexplained_external_target_change_is_preserved_and_fails_closed(workspace, tmp_path):
    source, dest = workspace
    old_spec = tree_bytes(spec_copy(dest))
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")

    run_crashing_sync(source, dest, mode="move", count=3)
    assert tree_bytes(state_path(dest) / "old-spec") == old_spec
    stray = spec_copy(dest) / "EXTERNAL.md"
    stray.write_bytes(b"external edit while a transaction was pending\n")
    journal = journal_path(dest)
    journal_before = journal.read_bytes()

    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "incomplete_sync" in checked.stdout

    failed = run_cli(source, dest, check=False)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "recovery_required" in failed.stdout
    assert "target changed outside this transaction" in failed.stdout
    assert journal.read_bytes() == journal_before
    assert stray.read_bytes() == b"external edit while a transaction was pending\n"
    assert tree_bytes(state_path(dest) / "old-spec") == old_spec
    assert not (state_path(dest) / "discard-spec").exists(), \
        "recovery moved a target it could not prove before failing"
    assert sentinel.read_bytes() == b"keep\n"
    assert unmanaged_bytes(dest) == UNMANAGED_FILES

    stray.unlink()
    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert run_cli(source, dest, check=True).returncode == 0
    assert_copies_match_source(source, dest)


# ---------------------------------------------------------------------------
# AC6: an invalid source starts no recovery, no target mutation and no cleanup;
# the check command never repairs a managed copy.
# ---------------------------------------------------------------------------


def test_invalid_source_does_not_start_recovery_or_cleanup(workspace):
    source, dest = workspace
    run_crashing_sync(source, dest, mode="move", count=2)
    journal = journal_path(dest)
    journal_before = journal.read_bytes()
    observed = tree_bytes(dest)

    (source / "contracts").rename(source / "contracts-withheld")
    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 1
    assert "source_incomplete" in checked.stdout
    assert "incomplete_sync" not in checked.stdout
    assert tree_bytes(dest) == observed, "checking an invalid source changed the pending state"

    failed = run_cli(source, dest, check=False)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "source_incomplete" in failed.stdout
    assert "Traceback" not in failed.stderr
    assert tree_bytes(dest) == observed, "an invalid source started recovery or cleanup"
    assert journal.read_bytes() == journal_before

    (source / "contracts-withheld").rename(source / "contracts")
    recovered = run_cli(source, dest, check=False)
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert "restored previous interrupted transaction before synchronizing" in recovered.stdout
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []


# ---------------------------------------------------------------------------
# The commit point is the removal of the pending record. A process exit after
# that point is not an incomplete transaction: the verified generation stays
# valid and a later sync only finishes housekeeping.
# ---------------------------------------------------------------------------


def test_process_exit_after_the_commit_point_keeps_the_new_generation_valid(workspace):
    source, dest = workspace
    (source / "README.md").write_bytes(b"# next generation\n")

    run_crashing_sync(source, dest, mode="cleanup", count=1)

    assert not journal_path(dest).exists(), "the child died before the commit point"
    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert "no drift" in checked.stdout
    assert (spec_copy(dest) / "README.md").read_bytes() == b"# next generation\n"

    retry = run_cli(source, dest, check=False)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "warning:" not in retry.stdout
    assert_no_slots(dest)
    assert run_cli(source, dest, check=True).returncode == 0
    assert sync.check(source, dest) == []
