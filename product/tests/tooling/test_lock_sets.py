"""T004: production, test and training dependencies are locked separately.

The ticket asks for the three sets to be locked independently, with the SBOM and
licence list written down. It does not ask for a training install on a host that
does not exist, so the training lock records resolution only and says so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]

PINS = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", re.MULTILINE)
HASH = re.compile(r"--hash=sha256:([0-9a-f]{64})")

LOCK_SETS = {
    "runtime": PRODUCT_ROOT / "probes" / "agent_min" / "requirements.lock.txt",
    "test": PRODUCT_ROOT / "tests" / "requirements-test.lock.txt",
    "training": PRODUCT_ROOT / "experiments" / "routing_sft" / "requirements-train.lock.txt",
}

SBOM = PRODUCT_ROOT / "reports" / "p01" / "sbom.json"


def test_each_dependency_set_has_its_own_lock_file() -> None:
    for name, path in LOCK_SETS.items():
        assert path.exists(), f"{name} lock is missing: {path}"
        text = path.read_text(encoding="utf-8")
        pins = PINS.findall(text)
        hashes = HASH.findall(text)
        assert pins, f"{name} lock has no pins"
        assert len(hashes) >= len(pins), (
            f"{name} lock has {len(hashes)} hashes for {len(pins)} pins; "
            "every pin needs at least one"
        )


def test_the_three_sets_are_distinct() -> None:
    """A single shared file would not be 'locked separately'."""
    resolved = {path.resolve() for path in LOCK_SETS.values()}
    assert len(resolved) == len(LOCK_SETS)

    runtime = (LOCK_SETS["runtime"].read_text(encoding="utf-8")).lower()
    test = (LOCK_SETS["test"].read_text(encoding="utf-8")).lower()
    assert "pytest==" in test and "pytest==" not in runtime
    assert "langgraph==" in runtime and "langgraph==" not in test


def test_training_lock_records_that_it_was_never_installed() -> None:
    report = json.loads(SBOM.read_text(encoding="utf-8"))
    sets = {entry["name"]: entry for entry in report["lock_sets"]}
    assert set(sets) == {"runtime", "test", "training"}

    assert sets["runtime"]["install_verified"] is True
    assert sets["test"]["install_verified"] is True
    assert sets["training"]["install_verified"] is False, (
        "the training lock was only resolved; claiming a verified install would be false"
    )
    assert "not_run" in sets["training"]["verification"]

    for name, entry in sets.items():
        assert entry["package_count"] > 0, f"{name} lock is empty"
        assert entry["packages_with_hashes"] == entry["package_count"], (
            f"{name} has packages without a hash"
        )


def test_sbom_still_does_not_fake_an_image_digest() -> None:
    report = json.loads(SBOM.read_text(encoding="utf-8"))
    assert report["image_digest"] is None
    assert "not_run" in report["image_digest_note"]
