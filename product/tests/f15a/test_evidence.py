"""T053/F15A: the checked-in evidence itself must be honest and credential-free.

The report is a deliverable, so its JSON evidence files are tested too: the
candidate run must be `blocked` (this machine has no candidate S3), the fixture
runs must show the discrimination, and no fixture credential literal may appear
in any committed evidence file.

These tests do not replace the live runs; they stop a future edit from quietly
turning `blocked` into `passed` or pasting a secret into the report.
"""

from __future__ import annotations

import json
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = PRODUCT_ROOT / "reports" / "f15a"
SECRET_LITERALS = (
    "fixture-runtime-secret-not-a-credential",
    "fixture-provision-secret-not-a-credential",
    "probe-runtime-credential",
    "probe-provision-credential",
)


def _load(name: str) -> dict:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def test_candidate_evidence_is_blocked_and_empty() -> None:
    report = _load("candidate-absent.json")
    assert report["status"] == "blocked"
    assert report["exit_code"] == 2
    assert report["blocked_reason"]["code"] == "endpoint_unreachable"
    assert report["assertions"] == []
    assert report["config"]["profile"] == "candidate"

    unconfigured = _load("candidate-not-configured.json")
    assert unconfigured["status"] == "blocked"
    assert unconfigured["exit_code"] == 2
    assert unconfigured["blocked_reason"]["code"] == "config_missing"
    assert set(unconfigured["blocked_reason"]["missing"]) >= {
        "endpoint",
        "bucket",
        "access_key",
        "secret_key",
    }


def test_fixture_evidence_shows_the_discrimination() -> None:
    compliant = _load("fixture-compliant.json")
    assert compliant["status"] == "passed"
    assert compliant["exit_code"] == 0
    assert compliant["summary"] == {"passed": 9, "failed": 0, "blocked": 0, "not_run": 1}

    violating = _load("fixture-violating-all.json")
    assert violating["status"] == "failed"
    assert violating["exit_code"] == 1
    assert violating["summary"]["failed"] == 9

    for violation in (
        "anonymous_read_denied",
        "anonymous_put_denied",
        "anonymous_list_denied",
        "out_of_range_key_denied",
        "sealed_put_denied",
        "staging_overwrite_preserves_sealed",
        "short_lived_signature_expiry",
        "proxy_download_boundary",
        "delete_semantics",
    ):
        report = _load(f"fixture-violation-{violation}.json")
        assert report["status"] == "failed", violation
        statuses = {item["id"]: item["status"] for item in report["assertions"]}
        assert statuses[violation] == "failed", violation
        assert sum(1 for status in statuses.values() if status == "failed") == 1, violation


def test_local_fs_evidence_is_labelled_not_s3() -> None:
    report = _load("local-fs-analogue.json")
    assert report["not_s3_evidence"] is True
    assert report["scope"] == "local-filesystem"
    assert report["status"] == "passed"
    statuses = {item["id"]: item["status"] for item in report["assertions"]}
    assert statuses["anonymous_network_boundary"] == "not_run"
    assert statuses["object_versioning"] == "not_run"


def test_evidence_contains_no_credential_literals() -> None:
    files = sorted(EVIDENCE.glob("*.json"))
    assert files, "no evidence files found"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for literal in SECRET_LITERALS:
            assert literal not in text, f"{literal!r} leaked into {path.name}"


def test_runs_index_records_real_exit_codes() -> None:
    index = json.loads((EVIDENCE / "runs.json").read_text(encoding="utf-8"))
    by_name = {run["name"]: run for run in index["runs"]}
    assert by_name["candidate-absent"]["exit_code"] == 2
    assert by_name["candidate-absent"]["status"] == "blocked"
    assert by_name["fixture-compliant"]["exit_code"] == 0
    assert by_name["fixture-violating-all"]["exit_code"] == 1
    assert by_name["local-fs-analogue"]["status"] == "passed"
    # No candidate run may be recorded as passed while the endpoint is absent.
    for name, run in by_name.items():
        if name.startswith("candidate-"):
            assert run["status"] == "blocked", name
