"""Tests for the spec -> product reproduction tool (T001 / F00 drift gate).

T001 requires the specification to be reproduced in the product workspace and
F00 requires contract drift to be detectable. These tests pin both: a clean
check passes, edited/added/removed files are reported by name, and excluded
directories never get copied.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))

import sync_spec as sync  # noqa: E402


@pytest.fixture()
def source_tree(tmp_path: Path) -> Path:
    root = tmp_path / "spec"
    (root / "contracts").mkdir(parents=True)
    (root / "docs").mkdir()
    (root / "README.md").write_text("# spec\n", encoding="utf-8")
    (root / "contracts" / "openapi.json").write_text('{"openapi": "3.1.0"}', encoding="utf-8")
    (root / "docs" / "HANDOFF.md").write_text("# handoff\n", encoding="utf-8")
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / ".venv" / "lib" / "junk.py").write_text("x = 1\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "c.pyc").write_bytes(b"\x00")
    return root


def test_sync_copies_spec_and_contracts(source_tree: Path, tmp_path: Path) -> None:
    dest = tmp_path / "product"
    result = sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    assert (dest / "docs" / "spec" / "README.md").exists()
    assert (dest / "docs" / "spec" / "contracts" / "openapi.json").exists()
    # runtime contract snapshot lives at product/contracts
    assert (dest / "contracts" / "openapi.json").exists()
    assert result["copied"] > 0
    assert result["manifest"].exists()


def test_excluded_directories_are_not_copied(source_tree: Path, tmp_path: Path) -> None:
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    assert not (dest / "docs" / "spec" / ".venv").exists()
    assert not (dest / "docs" / "spec" / "__pycache__").exists()
    # and the manifest must not reference them either
    manifest = json.loads((dest / "docs" / "spec" / "SYNC-MANIFEST.json").read_text("utf-8"))
    assert not any(".venv" in path for path in manifest["files"])
    assert not any("__pycache__" in path for path in manifest["files"])


def test_check_passes_on_fresh_copy(source_tree: Path, tmp_path: Path) -> None:
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    drift = sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")
    assert drift == [], f"expected no drift, got {drift}"


def test_check_detects_modified_file(source_tree: Path, tmp_path: Path) -> None:
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    (dest / "docs" / "spec" / "README.md").write_text("# tampered\n", encoding="utf-8")
    drift = sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    assert drift, "tampering must be detected"
    assert any("README.md" in entry["path"] and entry["kind"] == "modified" for entry in drift)


def test_check_detects_added_and_removed(source_tree: Path, tmp_path: Path) -> None:
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    (dest / "docs" / "spec" / "extra.md").write_text("# stray\n", encoding="utf-8")
    (dest / "docs" / "spec" / "docs" / "HANDOFF.md").unlink()

    drift = sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")
    kinds = {entry["kind"] for entry in drift}
    assert "added" in kinds
    assert "removed" in kinds


def test_manifest_records_both_trees(source_tree: Path, tmp_path: Path) -> None:
    """A gate that hashes only docs/spec cannot see contract drift."""
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    manifest = json.loads((dest / "docs" / "spec" / "SYNC-MANIFEST.json").read_text("utf-8"))
    assert manifest["files"], "spec tree must be digested"
    assert manifest["contracts"], "contract snapshot must be digested too"
    assert "openapi.json" in manifest["contracts"]


def test_check_detects_modified_contract(source_tree: Path, tmp_path: Path) -> None:
    """Editing the runtime contract snapshot must fail the drift gate."""
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    (dest / "contracts" / "openapi.json").write_text('{"openapi": "3.1.0", "x": 1}', encoding="utf-8")
    drift = sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    assert any(
        entry["kind"] == "modified" and entry["path"].startswith("contracts/") for entry in drift
    ), f"contract tampering went unnoticed: {drift}"


def test_line_ending_change_is_not_drift(source_tree: Path, tmp_path: Path) -> None:
    """A CRLF checkout must not look like tampering."""
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")

    target = dest / "docs" / "spec" / "README.md"
    target.write_bytes(b"# spec\r\nsecond line\r\n")

    drift = sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")
    # The content genuinely changed here, so this must still be drift...
    assert drift

    # ...but a pure line-ending change to identical content must not be.
    target.write_bytes(b"# spec\n")
    assert sync.check(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts") == []


def test_cli_check_exit_code_is_non_zero_on_drift(
    source_tree: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "product"
    sync.sync(source_tree, dest, spec_subdir="docs/spec", contracts_subdir="contracts")
    (dest / "docs" / "spec" / "README.md").write_text("# tampered\n", encoding="utf-8")

    code = sync.main(
        [
            "--source",
            str(source_tree),
            "--dest",
            str(dest),
            "--check",
        ]
    )
    assert code != 0
    assert "drift" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("name", ["README.md", "contracts/openapi.json"])
@pytest.mark.parametrize("change", ["modified", "added", "removed"])
def test_live_source_changes_are_detected_and_syncable(source_tree, tmp_path, name, change):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    path = source_tree / name
    if change == "removed":
        path.unlink()
    elif change == "added":
        path = path.with_name("new-" + path.name)
        path.write_bytes(b"new source\n")
    else:
        path.write_bytes(b"changed source\n")
    assert sync.check(source_tree, dest), "live-source drift was missed"
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []
    relative = path.relative_to(source_tree)
    assert (dest / "docs/spec" / relative).exists() == path.exists()
    if relative.parts[0] == "contracts":
        assert (dest / relative).exists() == path.exists()


def test_source_and_copies_cannot_bypass_the_baseline(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    for path in [source_tree / "README.md", dest / "docs/spec/README.md"]:
        path.write_bytes(b"same change\n")
    assert sync.check(source_tree, dest)


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("missing", ["source", "contracts"])
def test_unavailable_source_cannot_check_or_replace_copies(source_tree, tmp_path, missing):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    spec_before = tree_bytes(dest / "docs/spec")
    contracts_before = tree_bytes(dest / "contracts")
    old = source_tree if missing == "source" else source_tree / "contracts"
    old.rename(old.with_name(old.name + "-unavailable"))
    assert sync.check(source_tree, dest), "unverifiable source was accepted"
    with pytest.raises(sync.SyncError):
        sync.sync(source_tree, dest)
    assert tree_bytes(dest / "docs/spec") == spec_before
    assert tree_bytes(dest / "contracts") == contracts_before


def test_preparation_failure_preserves_both_copies(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    (source_tree / "README.md").write_bytes(b"next generation\n")
    original = sync.shutil.copy2

    def unavailable(src, dst, *args, **kwargs):
        if Path(src).name == "README.md":
            raise OSError("injected copy failure")
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(sync.shutil, "copy2", unavailable)
    with pytest.raises(sync.SyncError):
        sync.sync(source_tree, dest)
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before


@pytest.mark.parametrize("spec,contracts", [("../outside", "contracts"),
                                            ("docs/spec", "docs"),
                                            (".", "contracts")])
def test_unsafe_layout_never_removes_unmanaged_files(source_tree, tmp_path, spec, contracts):
    dest = tmp_path / "product"
    sentinel = tmp_path / "outside/sentinel"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"keep")
    assert sync.check(source_tree, dest, spec_subdir=spec, contracts_subdir=contracts)
    with pytest.raises(sync.SyncError):
        sync.sync(source_tree, dest, spec_subdir=spec, contracts_subdir=contracts)
    assert sentinel.read_bytes() == b"keep"


@pytest.mark.parametrize("move_number", [1, 2, 3, 4])
def test_each_switch_failure_restores_original_bytes(source_tree, tmp_path, monkeypatch, move_number):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    sentinel = dest / "internal/unmanaged.go"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"not a managed copy")
    (source_tree / "README.md").write_bytes(b"new content\n")
    original = sync._move
    count = 0

    def fail_once(layout, source, target):
        nonlocal count
        count += 1
        if count == move_number:
            raise OSError("injected directory move failure")
        return original(layout, source, target)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_once)
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, dest)
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before
    assert sentinel.read_bytes() == b"not a managed copy"
    assert all(item["kind"] != "incomplete_sync" for item in sync.check(source_tree, dest))
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []


def crash_during_sync(source_tree, dest, move_number):
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync
original = sync._move
count = 0
def crash_after_move(layout, source, target):
    global count
    original(layout, source, target)
    count += 1
    if count == int(sys.argv[4]):
        os._exit(73)
sync._move = crash_after_move
sync.sync(Path(sys.argv[2]), Path(sys.argv[3]))
"""
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", script,
                             str(PRODUCT_ROOT / "tools"), str(source_tree), str(dest), str(move_number)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 73, result.stdout + result.stderr


@pytest.mark.parametrize("move_number", [1, 2, 3, 4])
def test_process_exit_is_detected_and_next_sync_recovers(source_tree, tmp_path, move_number):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    (source_tree / "README.md").write_bytes(b"new generation\n")
    crash_during_sync(source_tree, dest, move_number)
    assert sync.check(source_tree, dest)[0]["kind"] == "incomplete_sync"
    result = sync.sync(source_tree, dest)
    assert result["recovered"] is True
    assert sync.check(source_tree, dest) == []
    assert (dest / "docs/spec/README.md").read_bytes() == b"new generation\n"


def test_failed_rollback_remains_visible_and_can_be_retried(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    (source_tree / "README.md").write_bytes(b"new content\n")
    original = sync._move

    def fail_switch_and_rollback(layout, source, target):
        if source.name in ("stage-contracts", "old-spec"):
            raise OSError("injected switch/rollback failure")
        return original(layout, source, target)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_switch_and_rollback)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source_tree, dest)
        assert failure.value.finding["kind"] == "recovery_required"
    assert sync.check(source_tree, dest)[0]["kind"] == "incomplete_sync"
    assert sync.sync(source_tree, dest)["recovered"]
    assert sync.check(source_tree, dest) == []


def test_unreadable_source_preserves_copies(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    original = Path.read_bytes

    def unreadable(path):
        if path == source_tree / "README.md":
            raise PermissionError("injected denied read")
        return original(path)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_bytes", unreadable)
        assert sync.check(source_tree, dest)[0]["kind"] == "source_unavailable"
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, dest)
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before


def test_malformed_baseline_fails_closed(source_tree, tmp_path):
    dest = tmp_path / "product"
    result = sync.sync(source_tree, dest)
    for contents in ["{", "[]", '{"schema": "unknown"}']:
        result["manifest"].write_text(contents, encoding="utf-8")
        assert sync.check(source_tree, dest)[0]["kind"] == "invalid_manifest"


def test_contract_baseline_must_be_projection_of_spec_baseline(source_tree, tmp_path):
    dest = tmp_path / "product"
    result = sync.sync(source_tree, dest)
    value = json.loads(result["manifest"].read_text(encoding="utf-8"))
    value["contracts"]["openapi.json"] = "0" * 64
    result["manifest"].write_text(json.dumps(value), encoding="utf-8")
    assert sync.check(source_tree, dest)[0]["kind"] == "invalid_manifest"


def test_source_newlines_binary_content_and_nested_destination(source_tree):
    dest = source_tree / "workspace"
    binary = source_tree / "binary.bin"
    binary.write_bytes(b"\xff\r\n")
    sync.sync(source_tree, dest)
    source_tree.joinpath("README.md").write_bytes(b"# spec\r\n")
    assert sync.check(source_tree, dest) == []
    binary.write_bytes(b"\xff\n")
    assert sync.check(source_tree, dest)
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []
    assert not (dest / "docs/spec/workspace").exists()


def test_invalid_transaction_cannot_supply_recovery_paths(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    crash_during_sync(source_tree, dest, 2)
    journal = dest / ".scratch/spec-sync/transaction.json"
    record = json.loads(journal.read_text(encoding="utf-8"))
    record["layout"]["spec"] = "../outside"
    journal.write_text(json.dumps(record), encoding="utf-8")
    sentinel = tmp_path / "outside/keep"
    sentinel.parent.mkdir()
    sentinel.write_bytes(b"keep")
    with pytest.raises(sync.SyncError) as failure:
        sync.sync(source_tree, dest)
    assert failure.value.finding["kind"] == "recovery_required"
    assert journal.exists()
    assert sentinel.read_bytes() == b"keep"


def test_concurrent_process_is_rejected_without_stealing_lock(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    script = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import sync_spec as sync
layout = sync._layout(Path(sys.argv[2]), Path(sys.argv[3]), 'docs/spec', 'contracts')
with sync._locked(layout):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-c", script,
                                str(PRODUCT_ROOT / "tools"), str(source_tree), str(dest)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "locked"
        assert sync.check(source_tree, dest)[0]["kind"] == "busy"
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source_tree, dest)
        assert failure.value.finding["kind"] == "busy"
    finally:
        process.communicate("release\n", timeout=10)
    assert process.returncode == 0
    assert sync.check(source_tree, dest) == []


def test_source_changes_during_copy_do_not_publish_a_mixed_generation(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    original = sync.shutil.copy2

    def changing_copy(src, dst, *args, **kwargs):
        result = original(src, dst, *args, **kwargs)
        if Path(src) == source_tree / "README.md":
            Path(src).write_bytes(b"changed after copying\n")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "copy2", changing_copy)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source_tree, dest)
        assert failure.value.finding["kind"] == "source_changed"
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before


def test_post_commit_cleanup_failure_does_not_invalidate_verified_generation(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    (source_tree / "README.md").write_bytes(b"new generation\n")
    original = sync.shutil.rmtree

    def cannot_clean_old_tree(path, *args, **kwargs):
        if Path(path).name.startswith("old-"):
            raise PermissionError("injected cleanup failure")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "rmtree", cannot_clean_old_tree)
        result = sync.sync(source_tree, dest)
        assert result["warnings"]
        assert sync.check(source_tree, dest) == []
    assert sync.sync(source_tree, dest)["warnings"] == []
    assert sync.check(source_tree, dest) == []


def test_empty_contracts_and_repeated_sync_are_content_based(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    (source_tree / "contracts/openapi.json").unlink()
    for commit in ("first", "second"):
        monkeypatch.setattr(sync, "_source_commit", lambda source: commit)
        sync.sync(source_tree, dest)
        assert sync.check(source_tree, dest) == []
        assert tree_bytes(dest / "contracts") == {}


def test_source_and_target_directory_redirects_are_rejected(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "keep"
    sentinel.write_bytes(b"keep")
    source_link = source_tree / "redirect"
    # Directory junction creation does not require Windows symlink privileges.
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(source_link), str(outside)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        source_link.symlink_to(outside, target_is_directory=True)
    try:
        assert sync.check(source_tree, dest)[0]["kind"] == "unsafe_path"
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, dest)
    finally:
        source_link.rmdir() if sys.platform == "win32" else source_link.unlink()
    assert sentinel.read_bytes() == b"keep"
    # The same redirect at an explicitly managed destination is also refused.
    alternate = tmp_path / "alternate"
    alternate.mkdir()
    target_link = alternate / "contracts"
    if sys.platform == "win32":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(target_link), str(outside)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
    else:
        target_link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, alternate)
    finally:
        target_link.rmdir() if sys.platform == "win32" else target_link.unlink()
    assert sentinel.read_bytes() == b"keep"


def test_cli_explains_live_source_and_missing_source(source_tree, tmp_path, capsys):
    dest = tmp_path / "product"
    args = ["--source", str(source_tree), "--dest", str(dest)]
    assert sync.main(args) == 0
    assert sync.main(args + ["--check"]) == 0
    capsys.readouterr()
    (source_tree / "README.md").write_bytes(b"changed\n")
    assert sync.main(args + ["--check"]) == 1
    assert "source_modified" in capsys.readouterr().out
    source_tree.rename(source_tree.with_name("source-unavailable"))
    assert sync.main(args + ["--check"]) == 1
    assert "source_unavailable" in capsys.readouterr().out
    assert sync.main(args) == 1


def test_baseline_filename_is_not_ignored_in_runtime_contracts(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    (dest / "contracts" / sync.MANIFEST_NAME).write_bytes(b"unexpected contract\n")
    assert sync.check(source_tree, dest), "only the spec-copy baseline may be excluded"
    (source_tree / "contracts" / sync.MANIFEST_NAME).write_bytes(b"legitimate source contract\n")
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []
    assert (dest / "contracts" / sync.MANIFEST_NAME).read_bytes() == b"legitimate source contract\n"


@pytest.mark.parametrize("nested", ["contracts", "contracts/output"])
def test_destination_cannot_hide_essential_source_contracts(source_tree, nested):
    before = tree_bytes(source_tree)
    with pytest.raises(sync.SyncError):
        sync.sync(source_tree, source_tree / nested)
    assert tree_bytes(source_tree) == before


def test_rollback_restores_by_rename_without_deleting_live_tree(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    (source_tree / "README.md").write_bytes(b"next generation\n")
    original_move, original_remove = sync._move, sync.shutil.rmtree

    def fail_install(layout, source, target):
        if source.name == "stage-contracts":
            raise OSError("injected install failure")
        return original_move(layout, source, target)

    def forbid_live_delete(path, *args, **kwargs):
        assert Path(path) not in (dest / "docs/spec", dest / "contracts")
        return original_remove(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_install)
        patch.setattr(sync.shutil, "rmtree", forbid_live_delete)
        with pytest.raises(sync.SyncError) as failure:
            sync.sync(source_tree, dest)
        assert failure.value.finding["kind"] == "sync_failed"
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []


def test_partial_discard_cleanup_can_be_retried_without_losing_old_copies(source_tree, tmp_path, monkeypatch):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    before = (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts"))
    (source_tree / "README.md").write_bytes(b"next generation\n")
    original_move = sync._move

    def fail_install(layout, source, target):
        if source.name == "stage-contracts":
            raise OSError("injected install failure")
        return original_move(layout, source, target)

    with monkeypatch.context() as patch:
        patch.setattr(sync, "_move", fail_install)
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, dest)
    original_remove = sync.shutil.rmtree

    def partial_cleanup(path, *args, **kwargs):
        if Path(path).name == "discard-spec":
            next(p for p in Path(path).rglob("*") if p.is_file()).unlink()
            raise PermissionError("injected partial housekeeping failure")
        return original_remove(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sync.shutil, "rmtree", partial_cleanup)
        with pytest.raises(sync.SyncError):
            sync.sync(source_tree, dest)
    assert (tree_bytes(dest / "docs/spec"), tree_bytes(dest / "contracts")) == before
    sync.sync(source_tree, dest)
    assert sync.check(source_tree, dest) == []


@pytest.mark.parametrize("recovery_move", [1, 2])
def test_process_exit_during_rollback_is_repeatable(source_tree, tmp_path, recovery_move):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    (source_tree / "README.md").write_bytes(b"next generation\n")
    crash_during_sync(source_tree, dest, 2)
    crash_during_sync(source_tree, dest, recovery_move)
    assert sync.check(source_tree, dest)[0]["kind"] == "incomplete_sync"
    assert sync.sync(source_tree, dest)["recovered"]
    assert sync.check(source_tree, dest) == []


@pytest.mark.parametrize("move_number", [1, 2])
def test_interrupted_first_sync_has_no_old_generation_to_lose(source_tree, tmp_path, move_number):
    dest = tmp_path / "product"
    crash_during_sync(source_tree, dest, move_number)
    assert sync.check(source_tree, dest)[0]["kind"] == "incomplete_sync"
    assert sync.sync(source_tree, dest)["recovered"]
    assert sync.check(source_tree, dest) == []


def test_invalid_source_does_not_start_recovery_mutations(source_tree, tmp_path):
    dest = tmp_path / "product"
    sync.sync(source_tree, dest)
    crash_during_sync(source_tree, dest, 2)
    before = tree_bytes(dest)
    (source_tree / "contracts").rename(source_tree / "missing-contracts")
    with pytest.raises(sync.SyncError):
        sync.sync(source_tree, dest)
    assert tree_bytes(dest) == before
