"""C1.01 / #138 re-verification of the already-implemented spec sync tool.

Every assertion here goes through the existing command seam
(``tools/sync_spec.py`` run as a real subprocess with real temporary
directories and real exit codes) or through the same module's ``check`` entry
for identical diagnostics. The historical #137 suite
(``test_sync_spec.py``) stays untouched so that historical evidence and this
ticket's re-verification remain separately labelled.

This module adds only coverage that was not already observable on the command
seam: command exit status and diagnostics for each add/modify/delete drift
kind, check's non-mutating behaviour, missing/non-directory source failing
closed, baseline-name contracts not being skipped, and metadata/location not
deciding content identity.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOL = PRODUCT_ROOT / "tools" / "sync_spec.py"
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))

import sync_spec as sync  # noqa: E402

Mutation = Callable[[Path, Path], None]


def run_cli(source: Path, dest: Path, *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run the real command; the caller observes its exit status and stdout."""
    command = [sys.executable, "-X", "utf8", str(TOOL),
               "--source", str(source), "--dest", str(dest)]
    if check:
        command.append("--check")
    return subprocess.run(command, capture_output=True, text=True, timeout=120)


@pytest.fixture()
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    """A real source tree synchronized to a real destination by the real CLI."""
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
    """Bytes of the two managed copies; private lock state is out of scope."""
    return {path.relative_to(dest).as_posix(): path.read_bytes()
            for root in (dest / "docs" / "spec", dest / "contracts")
            for path in root.rglob("*") if path.is_file()}


def finding_lines(stdout: str) -> list[str]:
    return [line.strip() for line in stdout.splitlines()
            if line.startswith("  ") and not line.strip().startswith("...")]


def mutate_source_added(source: Path, dest: Path) -> None:
    (source / "NEW.md").write_bytes(b"# new\n")


def mutate_source_modified(source: Path, dest: Path) -> None:
    (source / "README.md").write_bytes(b"# spec changed\n")


def mutate_source_removed(source: Path, dest: Path) -> None:
    (source / "docs" / "HANDOFF.md").unlink()


def mutate_spec_copy_added(source: Path, dest: Path) -> None:
    (dest / "docs" / "spec" / "stray.md").write_bytes(b"# stray\n")


def mutate_spec_copy_modified(source: Path, dest: Path) -> None:
    (dest / "docs" / "spec" / "README.md").write_bytes(b"# changed copy\n")


def mutate_spec_copy_removed(source: Path, dest: Path) -> None:
    (dest / "docs" / "spec" / "docs" / "HANDOFF.md").unlink()


def mutate_contract_copy_added(source: Path, dest: Path) -> None:
    (dest / "contracts" / "extra.json").write_bytes(b"{}\n")


def mutate_contract_copy_modified(source: Path, dest: Path) -> None:
    (dest / "contracts" / "openapi.json").write_bytes(b'{"openapi": "3.1.0", "x": 1}\n')


def mutate_contract_copy_removed(source: Path, dest: Path) -> None:
    (dest / "contracts" / "openapi.json").unlink()


def mutate_source_and_copy_together(source: Path, dest: Path) -> None:
    """Two matching edits still fail against the unchanged baseline."""
    for path in (source / "README.md", dest / "docs" / "spec" / "README.md"):
        path.write_bytes(b"# matching edit\n")


def mutate_baseline_missing(source: Path, dest: Path) -> None:
    (dest / "docs" / "spec" / "SYNC-MANIFEST.json").unlink()


def mutate_baseline_corrupt(source: Path, dest: Path) -> None:
    (dest / "docs" / "spec" / "SYNC-MANIFEST.json").write_bytes(b"{")


def mutate_baseline_contradictory(source: Path, dest: Path) -> None:
    path = dest / "docs" / "spec" / "SYNC-MANIFEST.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["contracts"]["openapi.json"] = "0" * 64
    path.write_text(json.dumps(value), encoding="utf-8")


def mutate_runtime_contract_baseline_name(source: Path, dest: Path) -> None:
    (dest / "contracts" / sync.MANIFEST_NAME).write_bytes(b"unexpected contract\n")


def mutate_source_missing(source: Path, dest: Path) -> None:
    source.rename(source.with_name("repo-missing"))


def mutate_source_not_a_directory(source: Path, dest: Path) -> None:
    shutil.rmtree(source)
    source.write_bytes(b"this is a file, not a source directory\n")


def mutate_source_contracts_missing(source: Path, dest: Path) -> None:
    (source / "contracts").rename(source / "contracts-missing")


# Each case: mutation, then the (kind, path fragment) pairs the CLI must print.
DRIFT_CASES = [
    pytest.param(mutate_source_added,
                 [("source_added", "source/NEW.md")], id="source-added"),
    pytest.param(mutate_source_modified,
                 [("source_modified", "source/README.md")], id="source-modified"),
    pytest.param(mutate_source_removed,
                 [("source_removed", "source/docs/HANDOFF.md")], id="source-removed"),
    pytest.param(mutate_spec_copy_added,
                 [("added", "stray.md")], id="spec-copy-added"),
    pytest.param(mutate_spec_copy_modified,
                 [("modified", "README.md")], id="spec-copy-modified"),
    pytest.param(mutate_spec_copy_removed,
                 [("removed", "docs/HANDOFF.md")], id="spec-copy-removed"),
    pytest.param(mutate_contract_copy_added,
                 [("added", "contracts/extra.json")], id="contract-copy-added"),
    pytest.param(mutate_contract_copy_modified,
                 [("modified", "contracts/openapi.json")], id="contract-copy-modified"),
    pytest.param(mutate_contract_copy_removed,
                 [("removed", "contracts/openapi.json")], id="contract-copy-removed"),
    pytest.param(mutate_source_and_copy_together,
                 [("source_modified", "source/README.md"),
                  ("modified", "README.md")], id="source-and-copy-together"),
    pytest.param(mutate_baseline_missing,
                 [("missing_manifest", "SYNC-MANIFEST.json")], id="baseline-missing"),
    pytest.param(mutate_baseline_corrupt,
                 [("invalid_manifest", "SYNC-MANIFEST.json")], id="baseline-corrupt"),
    pytest.param(mutate_baseline_contradictory,
                 [("invalid_manifest", "SYNC-MANIFEST.json")], id="baseline-contradictory"),
    pytest.param(mutate_runtime_contract_baseline_name,
                 [("added", "contracts/SYNC-MANIFEST.json")], id="runtime-contract-baseline-name"),
    pytest.param(mutate_source_missing,
                 [("source_unavailable", "")], id="source-missing"),
    pytest.param(mutate_source_not_a_directory,
                 [("source_unavailable", "")], id="source-not-a-directory"),
    pytest.param(mutate_source_contracts_missing,
                 [("source_incomplete", "")], id="source-contracts-missing"),
]


def test_check_exits_zero_when_source_copies_and_baseline_agree(workspace):
    source, dest = workspace
    result = run_cli(source, dest)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "no drift" in result.stdout
    assert sync.check(source, dest) == []


def test_check_never_modifies_managed_copies(workspace):
    source, dest = workspace
    before = managed_bytes(dest)
    assert run_cli(source, dest).returncode == 0
    assert managed_bytes(dest) == before, "a passing check changed managed copies"

    (dest / "docs" / "spec" / "README.md").write_bytes(b"# tampered\n")
    (dest / "contracts" / "extra.json").write_bytes(b"{}\n")
    before = managed_bytes(dest)
    result = run_cli(source, dest)
    assert result.returncode == 1
    assert managed_bytes(dest) == before, "a failing check healed or replaced managed copies"

    (dest / "docs" / "spec" / "SYNC-MANIFEST.json").unlink()
    before = managed_bytes(dest)
    result = run_cli(source, dest)
    assert result.returncode == 1
    assert "missing_manifest" in result.stdout
    assert managed_bytes(dest) == before
    assert not (dest / "docs" / "spec" / "SYNC-MANIFEST.json").exists(), \
        "check recreated the baseline instead of reporting it missing"


@pytest.mark.parametrize("mutate, expected", DRIFT_CASES)
def test_cli_reports_drift_with_nonzero_exit(workspace, mutate, expected):
    source, dest = workspace
    mutate(source, dest)
    result = run_cli(source, dest)
    assert result.returncode == 1, result.stdout + result.stderr
    findings = finding_lines(result.stdout)
    assert findings, f"no diagnostic findings printed: {result.stdout}"
    for kind, fragment in expected:
        assert any(line.split(None, 1)[0] == kind and fragment in line
                   for line in findings), \
            f"expected {kind} for {fragment!r} in {findings}"


def test_module_check_matches_cli_diagnostics(workspace):
    source, dest = workspace
    (source / "README.md").write_bytes(b"# changed\n")
    result = run_cli(source, dest)
    assert result.returncode == 1
    assert "source_modified" in result.stdout
    assert sync.check(source, dest) == [{
        "path": "source/README.md",
        "kind": "source_modified",
        "detail": "content differs from the synchronized baseline",
    }]


def test_baseline_metadata_and_source_location_do_not_decide_content(workspace, tmp_path):
    source, dest = workspace
    baseline = dest / "docs" / "spec" / "SYNC-MANIFEST.json"
    manifest = json.loads(baseline.read_text(encoding="utf-8"))
    manifest["generated_at"] = "2000-01-01T00:00:00+00:00"
    manifest["source_root"] = "Z:\\unrelated-checkout"
    manifest["source_commit"] = "f" * 40
    manifest["generation"] = "0" * 32
    baseline.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    result = run_cli(source, dest)
    assert result.returncode == 0, result.stdout + result.stderr

    relocated = tmp_path / "relocated-checkout"
    shutil.copytree(source, relocated)
    result = run_cli(relocated, dest)
    assert result.returncode == 0, "identical content failed from another absolute location"
    assert sync.check(relocated, dest) == []


def test_normalization_and_binary_semantics_survive_the_cli(workspace):
    source, dest = workspace
    (source / "README.md").write_bytes(b"# spec\r\n")
    assert run_cli(source, dest).returncode == 0, "text CRLF change became drift"

    binary = source / "blob.bin"
    binary.write_bytes(b"\xff\r\n")
    assert run_cli(source, dest).returncode == 1
    assert run_cli(source, dest, check=False).returncode == 0
    binary.write_bytes(b"\xff\n")
    result = run_cli(source, dest)
    assert result.returncode == 1, "undecodable binary CRLF change was normalized away"
    assert any(line.split(None, 1)[0] == "source_modified" and "blob.bin" in line
               for line in finding_lines(result.stdout))
    assert run_cli(source, dest, check=False).returncode == 0
    assert run_cli(source, dest).returncode == 0


def test_runtime_contract_named_like_the_baseline_is_not_skipped(workspace):
    source, dest = workspace
    contract = source / "contracts" / sync.MANIFEST_NAME
    contract.write_bytes(b'{"legitimate": true}\n')
    assert run_cli(source, dest).returncode == 1
    assert run_cli(source, dest, check=False).returncode == 0
    assert (dest / "contracts" / sync.MANIFEST_NAME).read_bytes() == contract.read_bytes()
    assert run_cli(source, dest).returncode == 0

    (dest / "contracts" / sync.MANIFEST_NAME).write_bytes(b'{"legitimate": false}\n')
    result = run_cli(source, dest)
    assert result.returncode == 1
    assert any(line.split(None, 1)[0] == "modified" and "contracts/SYNC-MANIFEST.json" in line
               for line in finding_lines(result.stdout))
