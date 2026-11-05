"""Frozen routing corpus loader and freeze checker (F08/T084 #85).

The corpus is a non-contract test asset: six cell files plus ``registry.json``
with a sha256 per file. This module is the only reader; it validates structure,
split/family isolation and the byte-level freeze before any evaluator run starts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

DATASETS = Path(__file__).resolve().parent / "datasets"
REGISTRY = DATASETS / "registry.json"

SCHEMA = "orchara.routing-corpus/1"
REGISTRY_SCHEMA = "orchara.routing-corpus-registry/1"
CELLS = (
    "unique",
    "ambiguous",
    "missing_input",
    "no_match",
    "unseen_application",
    "unauthorized_candidate",
)
SPLITS = ("dev", "frozen_test")
DECISIONS = ("auto_dispatch", "awaiting_selection", "awaiting_input", "failed")
NO_RUN_REASONS = ("no_matching_application", "route_candidate_invalid")
ATTACHMENT_KINDS = ("sales_csv",)
EXPECTED_FIELDS = {"decision", "application", "candidates", "interaction", "no_run_reason"}


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    family_id: str
    split: str
    message: str
    attachments: tuple[dict, ...]
    expected: dict
    provenance: dict
    cell_file: str

    @property
    def decision(self) -> str:
        return self.expected["decision"]

    @property
    def expected_candidates(self) -> list[str]:
        return list(self.expected["candidates"])


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_registry(path: Path = REGISTRY) -> dict:
    registry = json.loads(path.read_text(encoding="utf-8"))
    assert registry["schema"] == REGISTRY_SCHEMA, registry["schema"]
    return registry


def verify_registry(path: Path = REGISTRY) -> dict:
    """Recompute every frozen hash/size; any drift is a hard failure."""
    registry = load_registry(path)
    base = path.parent
    listed = {entry["path"]: entry for entry in registry["files"]}
    on_disk = {
        child.relative_to(base).as_posix()
        for child in base.rglob("*")
        if child.is_file() and child.name != "registry.json"
    }
    assert set(listed) == on_disk, (
        "the frozen routing corpus file set changed: "
        f"listed-only={sorted(set(listed) - on_disk)} disk-only={sorted(on_disk - set(listed))}"
    )
    for relative, entry in sorted(listed.items()):
        data = (base / relative).read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        assert digest == entry["sha256"], f"{relative} drifted: {digest} != {entry['sha256']}"
        assert len(data) == entry["size"], f"{relative} size drifted: {len(data)} != {entry['size']}"
    return registry


def load_cases(datasets: Path = DATASETS) -> list[Case]:
    cases: list[Case] = []
    for cell in CELLS:
        path = datasets / f"{cell}.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["schema"] == SCHEMA, (path.name, document["schema"])
        assert document["cell"] == cell, (path.name, document["cell"])
        for raw in document["cases"]:
            cases.append(
                Case(
                    case_id=raw["case_id"],
                    category=raw["category"],
                    family_id=raw["family_id"],
                    split=raw["split"],
                    message=raw["message"],
                    attachments=tuple(raw["attachments"]),
                    expected=dict(raw["expected"]),
                    provenance=dict(raw["provenance"]),
                    cell_file=path.name,
                )
            )
    return cases


def validate_corpus(cases: list[Case], registry: dict) -> None:
    """Structural, split and family-isolation rules the corpus promises."""
    assert cases, "the routing corpus is empty"
    seen_ids: set[str] = set()
    family_split: dict[str, str] = {}
    family_cell: dict[str, str] = {}
    messages_by_split: dict[str, set[str]] = {split: set() for split in SPLITS}
    frozen_families: dict[str, set[str]] = {cell: set() for cell in CELLS}
    dev_families: dict[str, set[str]] = {cell: set() for cell in CELLS}

    registry_families = {entry["family_id"]: entry for entry in registry["families"]}

    for case in cases:
        assert case.case_id not in seen_ids, f"duplicate case_id {case.case_id}"
        seen_ids.add(case.case_id)
        assert case.category in CELLS, case.case_id
        assert case.category == case.cell_file.removesuffix(".json"), case.case_id
        assert case.split in SPLITS, case.case_id
        assert case.message.strip(), case.case_id
        assert len(case.message) <= 32000, case.case_id
        assert set(case.expected) == EXPECTED_FIELDS, (case.case_id, sorted(case.expected))
        assert case.decision in DECISIONS, (case.case_id, case.decision)
        assert isinstance(case.expected["candidates"], list), case.case_id
        for kind in case.expected["candidates"]:
            assert isinstance(kind, str) and kind, case.case_id
        for attachment in case.attachments:
            assert set(attachment) == {"kind"}, (case.case_id, attachment)
            assert attachment["kind"] in ATTACHMENT_KINDS, (case.case_id, attachment)

        expected = case.expected
        if case.decision == "failed":
            assert expected["no_run_reason"] in NO_RUN_REASONS, (case.case_id, expected)
            assert expected["interaction"] is None, case.case_id
            assert expected["application"] is None, case.case_id
        else:
            assert expected["no_run_reason"] is None, (case.case_id, expected)
        if case.decision == "auto_dispatch":
            assert expected["application"], case.case_id
            assert expected["candidates"] == [expected["application"]], case.case_id
            assert expected["interaction"] is None, case.case_id
            assert case.attachments, f"{case.case_id}: auto dispatch needs an input-complete case"
        if case.decision == "awaiting_input":
            assert expected["interaction"] == "input", case.case_id
            assert expected["application"], case.case_id
            assert expected["candidates"] == [expected["application"]], case.case_id
            assert not case.attachments, f"{case.case_id}: the input must be missing"
        if case.decision == "awaiting_selection":
            assert expected["interaction"] == "selection", case.case_id
            assert expected["application"] is None, case.case_id
            assert len(expected["candidates"]) >= 2, case.case_id
        if case.decision == "auto_dispatch":
            assert not expected["candidates"] or expected["application"] in expected["candidates"]

        assert case.provenance.get("origin") == "self_created", case.case_id
        assert case.provenance.get("license_review") == "not_applicable", case.case_id
        assert case.provenance.get("reference"), case.case_id

        previous = family_split.setdefault(case.family_id, case.split)
        assert previous == case.split, f"family {case.family_id} crosses splits"
        previous_cell = family_cell.setdefault(case.family_id, case.category)
        assert previous_cell == case.category, f"family {case.family_id} crosses cells"
        assert case.family_id in registry_families, case.family_id
        entry = registry_families[case.family_id]
        assert entry["split"] == case.split and entry["cell"] == case.category, entry
        messages_by_split[case.split].add(case.message)
        if case.split == "frozen_test":
            frozen_families[case.category].add(case.family_id)
        else:
            dev_families[case.category].add(case.family_id)

    assert not (messages_by_split["dev"] & messages_by_split["frozen_test"]), (
        "the same message string appears in both splits"
    )
    for cell in CELLS:
        assert len(frozen_families[cell]) >= 3, (
            f"frozen_test needs >=3 independent families for {cell}: {sorted(frozen_families[cell])}"
        )
        assert len(dev_families[cell]) >= 1, f"dev does not cover {cell}"
    assert set(family_split) == set(registry_families), "registry families do not match the case files"


def split_counts(cases: list[Case]) -> dict:
    """Cases per (split, cell) and families per (split, cell), for the report."""
    counts: dict[str, dict[str, dict[str, int]]] = {split: {} for split in SPLITS}
    for case in cases:
        cell = counts[case.split].setdefault(
            case.category, {"cases": 0, "families": 0, "_families": set()}
        )
        cell["cases"] += 1
        cell["_families"].add(case.family_id)
    for split in SPLITS:
        for cell, entry in counts[split].items():
            entry["families"] = len(entry.pop("_families"))
    return counts


def dataset_digest(cases: list[Case]) -> str:
    """One stable digest of the canonical case content (independent of file layout)."""
    payload = [
        {
            "case_id": case.case_id,
            "category": case.category,
            "family_id": case.family_id,
            "split": case.split,
            "message": case.message,
            "attachments": list(case.attachments),
            "expected": case.expected,
        }
        for case in sorted(cases, key=lambda item: item.case_id)
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
