"""Tests for the spec -> product reproduction tool (T001 / F00 drift gate).

T001 requires the specification to be reproduced in the product workspace and
F00 requires contract drift to be detectable. These tests pin both: a clean
check passes, edited/added/removed files are reported by name, and excluded
directories never get copied.
"""

from __future__ import annotations

import json
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
