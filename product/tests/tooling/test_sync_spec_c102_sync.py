"""C1.02 / #139 acceptance re-verification of protected specification sync.

The ticket exercises the already-implemented ``sync()`` path and the protected
switch/recovery behaviour. Every assertion goes through the real command seam
(``tools/sync_spec.py`` as a real subprocess with real temporary directories and
real exit status) or through the same module's ``sync``/``check`` entry, and is
made against actual file bytes.

Fault injection only uses the private file-operation seams the design allows
(copy/rename/remove/write helpers); observed outcomes are the managed copies,
the synchronized baseline, the private transaction state, the outside
filesystem and the command's exit status and diagnostics. No production
fault-injection endpoint is added.

The historical #137 suite (``test_sync_spec.py``) and the C1.01 re-verification
(``test_sync_spec_c101_verification.py``) stay untouched so evidence stays
separately labelled and later C1 tickets merge cleanly. This module adds only
acceptance coverage that was not already observable on these seams: real CLI
propagation of additions/modifications/ordinary deletions, empty and missing
contracts sources, unsafe layouts and redirects through the command, staged
generation completeness before the first switch, source change during
preparation, post-install verification failure, the discard-before-restore
rollback order, cleanup-failure warnings through the command, and unmanaged
scope preservation.
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

# Unmanaged files that live next to (never inside) the two managed copies.
UNMANAGED_FILES = {
    "src/app.py": b"print('product code')\n",
    "docs/adr/ADR-0001.md": b"# decision one\n",
    "reports/architecture/sentinel.txt": b"# report sentinel\n",
    ".scratch/notes/keep.txt": b"# unrelated private state\n",
}

SLOT_NAMES = ("stage-spec", "stage-contracts", "old-spec", "old-contracts",
              "discard-spec", "discard-contracts")


def run_cli(source: Path, dest: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
    """Run the real command and return its real exit status and output."""
    command = [sys.executable, "-X", "utf8", str(TOOL),
               "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


def run_injected_cli(script: str, source: Path, dest: Path) -> subprocess.CompletedProcess[str]:
    """Run the real command entry in a subprocess with one private seam patched."""
    return subprocess.run([sys.executable, "-X", "utf8", "-c", textwrap.dedent(script),
                           str(TOOLS), "--source", str(source), "--dest", str(dest)],
                          capture_output=True, text=True, timeout=120)


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def managed_bytes(dest: Path) -> dict[str, bytes]:
    """Both managed copies plus the synchronized baseline, keyed by tree."""
    result: dict[str, bytes] = {}
    for prefix in ("spec", "contracts"):
        root = dest / "docs" / "spec" if prefix == "spec" else dest / "contracts"
        if root.exists():
            for name, data in tree_bytes(root).items():
                result[f"{prefix}/{name}"] = data
    return result


def manifest_bytes(dest: Path) -> bytes:
    return (dest / "docs" / "spec" / sync.MANIFEST_NAME).read_bytes()


def unmanaged_bytes(dest: Path, names=UNMANAGED_FILES) -> dict[str, bytes]:
    return {name: (dest / name).read_bytes() for name in names}


def state_path(dest: Path) -> Path:
    return dest / ".scratch" / "spec-sync"


def journal_path(dest: Path) -> Path:
    return state_path(dest) / "transaction.json"


def finding_lines(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines()
            if line.startswith("  ") and not line.strip().startswith("...")]


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


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A real source synchronized by the real command to a real product dir.

    The destination already contains product code, decisions, a report sentinel
    and unrelated private state, so protected sync must leave them untouched.
    """
    source = build_source(tmp_path / "repo")
    dest = tmp_path / "product"
    seed_unmanaged(dest)
    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return source, dest


def cli_finding_kinds(result: subprocess.CompletedProcess[str]) -> set[str]:
    return {line.split(None, 1)[0] for line in finding_lines(result.stdout)}


# ---------------------------------------------------------------------------
# AC1: additions, modifications and ordinary file deletions fully propagate.
# ---------------------------------------------------------------------------


def test_cli_sync_propagates_additions_modifications_and_ordinary_deletions(workspace):
    source, dest = workspace
    (source / "docs" / "NEW.md").write_bytes(b"# new document\n")
    (source / "contracts" / "extra.json").write_bytes(b'{"extra": true}\n')
    (source / "README.md").write_bytes(b"# spec v2\n")
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.1"}\n')
    (source / "docs" / "HANDOFF.md").unlink()
    (source / "contracts" / "schema.json").unlink()

    result = run_cli(source, dest, check=False)
    assert result.returncode == 0, result.stdout + result.stderr

    expected_spec = {path.relative_to(source).as_posix(): path.read_bytes()
                     for path in source.rglob("*") if path.is_file()}
    actual_spec = {name: data for name, data in tree_bytes(dest / "docs" / "spec").items()
                   if name != sync.MANIFEST_NAME}
    assert actual_spec == expected_spec, "spec copy is not the exact source projection"

    expected_contracts = {path.relative_to(source / "contracts").as_posix(): path.read_bytes()
                          for path in (source / "contracts").rglob("*") if path.is_file()}
    assert tree_bytes(dest / "contracts") == expected_contracts, \
        "runtime contract copy is not the exact source projection"

    # Ordinary deletion must remove the file from both managed copies.
    assert not (dest / "docs" / "spec" / "docs" / "HANDOFF.md").exists()
    assert not (dest / "contracts" / "schema.json").exists()

    after = run_cli(source, dest, check=True)
    assert after.returncode == 0, after.stdout + after.stderr
    assert sync.check(source, dest) == []


# ---------------------------------------------------------------------------
# AC2: empty-but-present contracts source works; a missing critical source dir
# fails before touching the old copies or the baseline.
# ---------------------------------------------------------------------------


def test_cli_sync_accepts_an_existing_empty_contracts_source(tmp_path):
    source = build_source(tmp_path / "repo")
    for path in (source / "contracts").iterdir():
        path.unlink()
    dest = tmp_path / "product"
    seed_unmanaged(dest)

    first = run_cli(source, dest, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (dest / "contracts").is_dir()
    assert tree_bytes(dest / "contracts") == {}
    baseline = json.loads((dest / "docs" / "spec" / sync.MANIFEST_NAME).read_text("utf-8"))
    assert baseline["contract_count"] == 0
    assert run_cli(source, dest, check=True).returncode == 0

    (source / "README.md").write_bytes(b"# spec v2\n")
    second = run_cli(source, dest, check=False)
    assert second.returncode == 0, second.stdout + second.stderr
    assert tree_bytes(dest / "contracts") == {}
    assert sync.check(source, dest) == []


def test_cli_sync_missing_contracts_source_fails_before_touching_targets(workspace):
    source, dest = workspace
    (source / "contracts").rename(source / "contracts-withheld")
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    unmanaged_before = unmanaged_bytes(dest)

    result = run_cli(source, dest, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "source_incomplete" in result.stdout

    assert managed_bytes(dest) == copies_before, "a missing critical source dir changed managed bytes"
    assert manifest_bytes(dest) == baseline_before, "the old baseline was rewritten"
    assert unmanaged_bytes(dest) == unmanaged_before
    assert not journal_path(dest).exists(), "a rejected source left an unfinished transaction"

    check = run_cli(source, dest, check=True)
    assert check.returncode == 1
    assert "source_incomplete" in check.stdout


# ---------------------------------------------------------------------------
# AC3: unsafe layouts and redirects fail before writing; normal nesting works.
# ---------------------------------------------------------------------------


def test_cli_rejects_destination_that_hides_essential_source_contracts(workspace):
    source, dest = workspace
    nested = source / "contracts" / "output"
    source_before = tree_bytes(source)

    result = run_cli(source, nested, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "invalid_layout" in result.stdout
    assert tree_bytes(source) == source_before
    assert not nested.exists(), "a rejected layout created a destination inside the source"


def test_cli_rejects_source_nested_inside_destination(workspace):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    unmanaged_before = unmanaged_bytes(dest)
    inner = dest / "source-copy"
    (inner / "contracts").mkdir(parents=True)
    (inner / "README.md").write_bytes(b"# nested source\n")

    result = run_cli(inner, dest, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "invalid_layout" in result.stdout
    assert managed_bytes(dest) == copies_before
    assert unmanaged_bytes(dest) == unmanaged_before


def test_cli_rejects_source_equal_to_destination(workspace):
    source, dest = workspace
    source_before = tree_bytes(source)

    result = run_cli(source, source, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "invalid_layout" in result.stdout
    assert tree_bytes(source) == source_before


def test_cli_rejects_redirected_source_and_managed_target(workspace, tmp_path):
    source, dest = workspace
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")

    redirected_source = tmp_path / "redirected-source"
    make_dir_link(redirected_source, outside)
    try:
        result = run_cli(redirected_source, dest, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "unsafe_path" in result.stdout
    finally:
        remove_dir_link(redirected_source)
    assert sentinel.read_bytes() == b"keep\n"

    alternate = tmp_path / "alternate-product"
    alternate.mkdir()
    managed_contracts = alternate / "contracts"
    make_dir_link(managed_contracts, outside)
    try:
        result = run_cli(source, alternate, check=False)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "unsafe_path" in result.stdout
    finally:
        remove_dir_link(managed_contracts)
    assert sentinel.read_bytes() == b"keep\n"
    assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]


def test_module_rejects_escape_overlap_and_private_state_overlap_before_writing(workspace, tmp_path):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    unmanaged_before = unmanaged_bytes(dest)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")

    unsafe = [
        ("../outside", "contracts"),      # spec copy escapes the destination
        ("docs/spec", "docs"),            # the two managed copies overlap
        (".scratch/spec-sync", "contracts"),  # spec copy overlaps private state
        ("docs/spec", ".scratch"),        # contract copy contains private state
    ]
    for spec_subdir, contracts_subdir in unsafe:
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest, spec_subdir=spec_subdir,
                      contracts_subdir=contracts_subdir)
        assert failure.value.finding["kind"] == "invalid_layout"
        assert sync.check(source, dest, spec_subdir=spec_subdir,
                          contracts_subdir=contracts_subdir), "unsafe check was reported clean"
        assert managed_bytes(dest) == copies_before
        assert unmanaged_bytes(dest) == unmanaged_before
    assert sentinel.read_bytes() == b"keep\n"


@pytest.mark.parametrize("dest_name", ["product", "workspace"])
def test_cli_sync_supports_normal_same_repository_nesting(tmp_path, dest_name):
    source = build_source(tmp_path / "repo")
    (source / "docs" / "notes").mkdir()
    (source / "docs" / "notes" / "extra.md").write_bytes(b"# nested notes\n")
    dest = source / dest_name

    first = run_cli(source, dest, check=False)
    assert first.returncode == 0, first.stdout + first.stderr
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# spec\n"
    assert (dest / "contracts" / "openapi.json").read_bytes() == b'{"openapi": "3.1.0"}\n'
    source.joinpath("README.md").write_bytes(b"# spec v2\n")

    # The existing nested destination inside the source must be pruned again.
    second = run_cli(source, dest, check=False)
    assert second.returncode == 0, second.stdout + second.stderr
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# spec v2\n"
    assert not (dest / "docs" / "spec" / dest_name).exists()
    assert sync.check(source, dest) == []


# ---------------------------------------------------------------------------
# AC4: prepare and verify, then switch; a change during preparation must not
# publish a mixed generation.
# ---------------------------------------------------------------------------


def test_staging_is_complete_and_verified_before_the_first_switch(workspace, monkeypatch):
    source, dest = workspace
    old_spec = tree_bytes(dest / "docs" / "spec")
    old_contracts = tree_bytes(dest / "contracts")
    (source / "README.md").write_bytes(b"# spec v2\n")
    (source / "docs" / "NEW.md").write_bytes(b"# new document\n")
    (source / "docs" / "HANDOFF.md").unlink()
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.1"}\n')
    expected_spec = {path.relative_to(source).as_posix(): path.read_bytes()
                     for path in source.rglob("*") if path.is_file()}
    expected_contracts = {path.relative_to(source / "contracts").as_posix(): path.read_bytes()
                          for path in (source / "contracts").rglob("*") if path.is_file()}

    observations: list[dict[str, object]] = []
    original_move = sync._move

    def observe(layout, from_path, to_path):
        if not observations:
            observations.append({
                "stage_spec": tree_bytes(layout.state / "stage-spec"),
                "stage_contracts": tree_bytes(layout.state / "stage-contracts"),
                "live_spec": tree_bytes(layout.spec),
                "live_contracts": tree_bytes(layout.contracts),
                "journal": layout.journal.exists(),
            })
        return original_move(layout, from_path, to_path)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", observe)
        result = sync.sync(source, dest)

    stage_spec = dict(observations[0]["stage_spec"])
    assert stage_spec.pop(sync.MANIFEST_NAME, None) is not None, \
        "the staged generation had no baseline before the first switch"
    assert stage_spec == expected_spec, "the first switch ran before the staged spec was complete"
    assert observations[0]["stage_contracts"] == expected_contracts
    assert observations[0]["live_spec"] == old_spec, "the old copy was switched before preparation"
    assert observations[0]["live_contracts"] == old_contracts
    assert observations[0]["journal"] is True, "no pending record existed before the first move"
    assert result["copied"] == len(expected_spec)
    assert sync.check(source, dest) == []


def test_source_change_during_preparation_never_publishes_a_mixed_generation(workspace, monkeypatch):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    (source / "README.md").write_bytes(b"# intended v2\n")
    (source / "docs" / "NEW.md").write_bytes(b"# new document\n")
    (source / "docs" / "HANDOFF.md").unlink()
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.1"}\n')
    changed = source / "README.md"
    original_copy = sync.shutil.copy2

    def changing_copy(src, dst, *args, **kwargs):
        result = original_copy(src, dst, *args, **kwargs)
        if Path(src) == changed:
            changed.write_bytes(b"# changed while preparing\n")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "copy2", changing_copy)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "source_changed"

    assert managed_bytes(dest) == copies_before, "a mixed generation replaced the copies"
    assert manifest_bytes(dest) == baseline_before, "the baseline advanced for a mixed generation"
    assert not journal_path(dest).exists()
    kinds = {item["kind"] for item in sync.check(source, dest)}
    assert kinds and kinds <= {"source_added", "source_removed", "source_modified"}, kinds

    # The same edit now completes; the published generation is the exact source.
    result = sync.sync(source, dest)
    assert result["warnings"] == []
    assert sync.check(source, dest) == []
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# changed while preparing\n"


def test_source_change_after_staging_verification_fails_before_switch(workspace, monkeypatch):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    (source / "README.md").write_bytes(b"# intended v2\n")
    changed = False
    original_write = sync._write_json

    def changing_write(path, value):
        nonlocal changed
        result = original_write(path, value)
        if not changed and Path(path).name == sync.MANIFEST_NAME:
            changed = True
            (source / "README.md").write_bytes(b"# changed after staging\n")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_write_json", changing_write)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "source_changed"

    assert changed, "the post-staging source recheck seam was never exercised"
    assert managed_bytes(dest) == copies_before
    assert manifest_bytes(dest) == baseline_before
    assert not journal_path(dest).exists()


# ---------------------------------------------------------------------------
# AC5: preparation/verification failure and each of the four switch positions
# report failure and restore the actual old copy and baseline bytes.
# ---------------------------------------------------------------------------


def test_preparation_failure_preserves_old_copies_and_baseline(workspace, monkeypatch):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")
    original_copy = sync.shutil.copy2
    blocked = source / "contracts" / "openapi.json"

    def failing_copy(src, dst, *args, **kwargs):
        if Path(src) == blocked:
            raise OSError("injected preparation failure")
        return original_copy(src, dst, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "copy2", failing_copy)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "sync_failed"

    assert managed_bytes(dest) == copies_before
    assert manifest_bytes(dest) == baseline_before
    assert not journal_path(dest).exists()


def test_post_install_verification_failure_rolls_back_the_old_generation(workspace, monkeypatch):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")
    original_audit = sync._audit

    def failing_audit(layout, files):
        return [{"path": "injected", "kind": "modified",
                 "detail": "injected post-install verification failure"}]

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_audit", failing_audit)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "sync_failed"
        assert "verification" in failure.value.finding["detail"]

    assert managed_bytes(dest) == copies_before
    assert manifest_bytes(dest) == baseline_before
    assert not journal_path(dest).exists()
    assert sync._audit is original_audit
    assert sync.sync(source, dest)["warnings"] == []
    assert sync.check(source, dest) == []


@pytest.mark.parametrize("move_number", [1, 2, 3, 4])
def test_each_capturable_switch_failure_restores_old_copies_and_baseline(workspace, monkeypatch, move_number):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    baseline_before = manifest_bytes(dest)
    unmanaged_before = unmanaged_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.1"}\n')
    original_move = sync._move
    moves = 0

    def fail_once(layout, from_path, to_path):
        nonlocal moves
        moves += 1
        if moves == move_number:
            raise OSError("injected switch failure")
        return original_move(layout, from_path, to_path)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_once)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "sync_failed", failure.value.finding

    assert moves >= move_number
    assert managed_bytes(dest) == copies_before, "old copies were not restored byte-for-byte"
    assert manifest_bytes(dest) == baseline_before, "old baseline was not restored"
    assert unmanaged_bytes(dest) == unmanaged_before
    assert not journal_path(dest).exists(), "a clean rollback left an unfinished transaction"

    observed = run_cli(source, dest, check=True)
    assert observed.returncode == 1
    assert cli_finding_kinds(observed) == {"source_modified"}, observed.stdout
    assert "incomplete_sync" not in observed.stdout

    retry = sync.sync(source, dest)
    assert retry["warnings"] == []
    assert sync.check(source, dest) == []
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# next generation\n"


SWITCH_FAILURE_CLI = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync

original = sync._move

def fail_final_switch(layout, from_path, to_path):
    if Path(from_path).name == "stage-contracts" and Path(to_path) == layout.contracts:
        raise OSError("injected switch failure")
    return original(layout, from_path, to_path)

sync._move = fail_final_switch
raise SystemExit(sync.main(sys.argv[2:]))
"""


def test_cli_reports_switch_failure_and_preserves_old_bytes(workspace):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")

    result = run_injected_cli(SWITCH_FAILURE_CLI, source, dest)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "sync_failed" in result.stdout
    assert "Traceback" not in result.stderr
    assert managed_bytes(dest) == copies_before
    assert not journal_path(dest).exists()


# ---------------------------------------------------------------------------
# AC6: rollback discards by rename before restoring the backup; partial
# cleanup is retryable and recursive cleanup never deletes the live copies.
# ---------------------------------------------------------------------------


def test_rollback_moves_disposable_tree_to_discard_before_restoring_backup(workspace, monkeypatch):
    source, dest = workspace
    old_spec = tree_bytes(dest / "docs" / "spec")
    old_contracts = tree_bytes(dest / "contracts")
    (source / "README.md").write_bytes(b"# next generation\n")
    (source / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.1"}\n')
    expected_spec = {path.relative_to(source).as_posix(): path.read_bytes()
                     for path in source.rglob("*") if path.is_file()}
    original_move = sync._move
    live_deletes: list[Path] = []

    def tracked_rmtree(path, *args, **kwargs):
        if Path(path) in (dest / "docs" / "spec", dest / "contracts"):
            live_deletes.append(Path(path))
        return original_rmtree(path, *args, **kwargs)

    def fail_switch_and_restore(layout, from_path, to_path):
        if Path(from_path).name == "stage-contracts" and Path(to_path) == layout.contracts:
            raise OSError("injected switch failure")
        if Path(from_path).name == "old-spec" and Path(to_path) == layout.spec:
            raise OSError("injected backup restore failure")
        return original_move(layout, from_path, to_path)

    original_rmtree = sync.shutil.rmtree
    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_switch_and_restore)
        patch.setattr(sync.shutil, "rmtree", tracked_rmtree)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "recovery_required"

    state = state_path(dest)
    assert live_deletes == [], "rollback recursively deleted a live managed copy"
    assert journal_path(dest).exists()
    assert not (dest / "docs" / "spec").exists(), "the disposable tree was not moved away first"
    assert not (dest / "contracts").exists()

    discarded_spec = tree_bytes(state / "discard-spec")
    discarded_baseline = discarded_spec.pop(sync.MANIFEST_NAME, None)
    assert discarded_baseline is not None
    assert json.loads(discarded_baseline)["schema"] == sync.MANIFEST_SCHEMA
    assert discarded_spec == expected_spec, "discard slot does not hold the disposed new tree"
    assert tree_bytes(state / "old-spec") == old_spec, "the complete backup was not preserved"
    assert tree_bytes(state / "old-contracts") == old_contracts

    assert sync.check(source, dest)[0]["kind"] == "incomplete_sync"
    result = sync.sync(source, dest)
    assert result["recovered"] is True
    assert sync.check(source, dest) == []
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# next generation\n"
    assert not any((state / name).exists() for name in SLOT_NAMES)


def test_partial_cleanup_failure_is_retryable_without_damaging_restored_copies(workspace, monkeypatch):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    unmanaged_before = unmanaged_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")
    original_move = sync._move

    def fail_final_switch(layout, from_path, to_path):
        if Path(from_path).name == "stage-contracts":
            raise OSError("injected switch failure")
        return original_move(layout, from_path, to_path)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_final_switch)
        with pytest.raises(sync.SyncError):
            sync.sync(source, dest)
    assert managed_bytes(dest) == copies_before, "clean rollback did not restore the old copies"
    state = state_path(dest)
    assert (state / "discard-spec").is_dir()

    original_rmtree = sync.shutil.rmtree

    def partial_cleanup(path, *args, **kwargs):
        if Path(path).name == "discard-spec":
            victim = next(item for item in Path(path).rglob("*") if item.is_file())
            victim.unlink()
            raise PermissionError("injected partial housekeeping failure")
        return original_rmtree(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "rmtree", partial_cleanup)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "sync_failed"

    assert managed_bytes(dest) == copies_before, "a cleanup failure damaged the restored live copies"
    assert unmanaged_bytes(dest) == unmanaged_before
    assert not journal_path(dest).exists()

    retry = run_cli(source, dest, check=False)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert not any((state / name).exists() for name in SLOT_NAMES)
    assert run_cli(source, dest, check=True).returncode == 0


# ---------------------------------------------------------------------------
# AC7: post-commit cleanup failure warns; the verified generation stays valid
# and can be cleaned later.
# ---------------------------------------------------------------------------


CLEANUP_FAILURE_CLI = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync

original = sync.shutil.rmtree

def blocked(path, *args, **kwargs):
    if Path(path).name.startswith("old-"):
        raise PermissionError("injected post-commit cleanup failure")
    return original(path, *args, **kwargs)

sync.shutil.rmtree = blocked
raise SystemExit(sync.main(sys.argv[2:]))
"""


def test_cli_reports_post_commit_cleanup_failure_as_a_warning(workspace):
    source, dest = workspace
    (source / "README.md").write_bytes(b"# next generation\n")
    unmanaged_before = unmanaged_bytes(dest)

    result = run_injected_cli(CLEANUP_FAILURE_CLI, source, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning:" in result.stdout
    assert "cleanup deferred" in result.stdout
    state = state_path(dest)
    assert (state / "old-spec").is_dir() or (state / "old-contracts").is_dir(), \
        "the injected cleanup failure never blocked the post-commit cleanup"

    # The verified new generation is complete and still passes the real check.
    checked = run_cli(source, dest, check=True)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert (dest / "docs" / "spec" / "README.md").read_bytes() == b"# next generation\n"
    assert unmanaged_bytes(dest) == unmanaged_before

    # A later sync runs the same cleanup to completion without a warning.
    retry = run_cli(source, dest, check=False)
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert "warning:" not in retry.stdout
    assert not any((state / name).exists() for name in SLOT_NAMES)
    assert sync.check(source, dest) == []


# ---------------------------------------------------------------------------
# AC8: outside the managed scope nothing changes and cleanup permission is
# not widened.
# ---------------------------------------------------------------------------


def test_unmanaged_code_decisions_and_report_sentinels_survive_sync_and_rollback(workspace, monkeypatch):
    source, dest = workspace
    decoys = {
        "old-spec/keep.txt": b"looks like a slot name but is not under private state\n",
        "spec-backup/keep.txt": b"another unmanaged lookalike\n",
        ".scratch/spec-sync/keep.txt": b"private state outside the fixed slots\n",
    }
    for name, data in decoys.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    expected = {**UNMANAGED_FILES, **decoys}

    (source / "README.md").write_bytes(b"# spec v2\n")
    assert run_cli(source, dest, check=False).returncode == 0
    assert unmanaged_bytes(dest, names=expected) == expected
    assert not (dest / "docs" / "spec" / "src").exists()

    (source / "README.md").write_bytes(b"# spec v3\n")
    original_move = sync._move

    def fail_final_switch(layout, from_path, to_path):
        if Path(from_path).name == "stage-contracts":
            raise OSError("injected switch failure")
        return original_move(layout, from_path, to_path)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_final_switch)
        with pytest.raises(sync.SyncError):
            sync.sync(source, dest)
    assert unmanaged_bytes(dest, names=expected) == expected, \
        "a failed sync or rollback touched files outside the managed scope"


def test_cleanup_refuses_a_redirected_discard_tree(workspace, monkeypatch, tmp_path):
    source, dest = workspace
    copies_before = managed_bytes(dest)
    (source / "README.md").write_bytes(b"# next generation\n")
    original_move = sync._move

    def fail_final_switch(layout, from_path, to_path):
        if Path(from_path).name == "stage-contracts":
            raise OSError("injected switch failure")
        return original_move(layout, from_path, to_path)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_final_switch)
        with pytest.raises(sync.SyncError):
            sync.sync(source, dest)

    discarded = state_path(dest) / "discard-spec"
    assert discarded.is_dir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_bytes(b"keep\n")
    redirect = discarded / "redirect"
    make_dir_link(redirect, outside)
    try:
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source, dest)
        assert failure.value.finding["kind"] == "unsafe_path"
        assert sentinel.read_bytes() == b"keep\n"
        assert sorted(path.name for path in outside.iterdir()) == ["keep.txt"]
        assert managed_bytes(dest) == copies_before
    finally:
        remove_dir_link(redirect)

    retry = sync.sync(source, dest)
    assert retry["warnings"] == []
    assert sync.check(source, dest) == []
    assert not any((state_path(dest) / name).exists() for name in SLOT_NAMES)
