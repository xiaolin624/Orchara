"""T084 (#85): the frozen routing corpus is pinned and family-isolated.

The corpus is a test asset, not a contract (the contract's evaluation-case schema
has no routing outcome vocabulary). These checks are the drift guard: every byte
of every dataset file is pinned by sha256, the six cells exist, and no task family
is split across ``dev`` and ``frozen_test``.
"""

from __future__ import annotations

import json
import shutil

import pytest

from f08.routing import corpus


def test_the_registry_pins_every_corpus_byte() -> None:
    registry = corpus.verify_registry()
    assert registry["corpus_version"] == "1.0.0", registry["corpus_version"]
    assert registry["frozen_at"], registry["frozen_at"]
    listed = {entry["path"] for entry in registry["files"]}
    for cell in corpus.CELLS:
        assert f"{cell}.json" in listed, cell
    assert "inputs/sales_sample.csv" in listed
    for entry in registry["files"]:
        assert len(entry["sha256"]) == 64 and entry["size"] > 0, entry
    # The public sales sample is byte-identical to the repository sample.
    sample = corpus.DATASETS / "inputs" / "sales_sample.csv"
    repository_sample = (
        __import__("support.stack", fromlist=["PRODUCT_ROOT"]).PRODUCT_ROOT
        / "docs" / "spec" / "examples" / "sales" / "input.csv"
    )
    assert sample.read_bytes() == repository_sample.read_bytes()


def test_the_corpus_covers_six_cells_with_isolated_families() -> None:
    registry = corpus.verify_registry()
    cases = corpus.load_cases()
    corpus.validate_corpus(cases, registry)
    assert len(cases) == 34, len(cases)
    counts = corpus.split_counts(cases)
    assert set(counts) == set(corpus.SPLITS)
    for cell in corpus.CELLS:
        frozen = counts["frozen_test"].get(cell, {"cases": 0, "families": 0})
        dev = counts["dev"].get(cell, {"cases": 0, "families": 0})
        assert frozen["families"] >= 3, (cell, frozen)
        assert frozen["cases"] >= 3, (cell, frozen)
        assert dev["families"] >= 1, (cell, dev)
        assert dev["cases"] >= 1, (cell, dev)
    # Three automatic-dispatch-family outputs for the unique cell are real
    # attachments, never empty auto executions.
    for case in cases:
        if case.decision == "auto_dispatch":
            assert [item["kind"] for item in case.attachments] == ["sales_csv"], case.case_id
        if case.decision in ("awaiting_input",):
            assert case.attachments == (), case.case_id
    # The hidden application is never named as an expected candidate.
    for case in cases:
        assert "payroll" not in case.expected["candidates"], case.case_id
    assert any("{{payroll_version_id}}" in case.message for case in cases)


def test_a_changed_dataset_byte_is_detected(tmp_path) -> None:
    copy = tmp_path / "datasets"
    shutil.copytree(corpus.DATASETS, copy)
    target = copy / "unique.json"
    document = json.loads(target.read_text(encoding="utf-8"))
    document["cases"][0]["message"] = "a silently edited case"
    target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="drifted"):
        corpus.verify_registry(copy / "registry.json")


def test_a_family_that_crosses_splits_is_rejected() -> None:
    registry = corpus.load_registry()
    cases = corpus.load_cases()
    mutated = []
    for case in cases:
        if case.case_id == "rt-unique-frozen-02":
            case = corpus.Case(
                case_id=case.case_id,
                category=case.category,
                family_id=case.family_id,
                split="dev",
                message=case.message,
                attachments=case.attachments,
                expected=case.expected,
                provenance=case.provenance,
                cell_file=case.cell_file,
            )
        mutated.append(case)
    with pytest.raises(AssertionError, match="crosses splits"):
        corpus.validate_corpus(mutated, registry)
