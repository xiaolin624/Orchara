"""T012: profile handling, a separate stub ledger, and one-command CI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "probes" / "agent_min"))

from agent_min.__main__ import append_stub_ledger, build_report  # noqa: E402


def test_stub_runs_are_recorded_in_a_separate_ledger(tmp_path: Path) -> None:
    report = build_report()
    assert report["stub"] is True

    ledger = tmp_path / "stub-ledger.jsonl"
    append_stub_ledger(report, ledger)

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["stub"] is True
    assert entry["profile"] == report["profile"]
    assert len(entry["report_digest"]) == 64
    # The ledger is a separate artefact from the report itself.
    assert entry["probe"] == "p01-agent-min"


def test_ledger_is_append_only(tmp_path: Path) -> None:
    report = build_report()
    ledger = tmp_path / "stub-ledger.jsonl"
    append_stub_ledger(report, ledger)
    append_stub_ledger(report, ledger)

    lines = ledger.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "each run must add a line rather than overwrite the ledger"


def test_ci_checks_quick_reports_a_machine_readable_verdict() -> None:
    completed = subprocess.run(
        [sys.executable, "product/tools/ci_checks.py", "--quick", "--json"],
        cwd=PRODUCT_ROOT.parent,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    summary = json.loads(completed.stdout)

    assert summary["ok"] is True
    assert summary["verdict"] == "ok"
    names = {item["name"] for item in summary["checks"]}
    assert {"spec-drift", "python-tests-fast"} <= names
    assert summary["failed"] == []
    assert summary["passed"] + summary["skipped"] == len(summary["checks"])


def test_a_skipped_check_is_never_reported_as_passed() -> None:
    """The gate must not go green on a run that skipped work."""
    sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
    import ci_checks

    mixed = ci_checks.summarise(
        [
            {"name": "drift", "status": "passed", "detail": ""},
            {"name": "integration", "status": "skipped", "detail": "no database"},
        ],
        0.1,
    )
    assert mixed["verdict"] == "partial"
    assert mixed["ok"] is False
    assert mixed["skipped_names"] == ["integration"]

    clean = ci_checks.summarise([{"name": "drift", "status": "passed", "detail": ""}], 0.1)
    assert clean["verdict"] == "ok"
    assert clean["ok"] is True

    broken = ci_checks.summarise(
        [{"name": "drift", "status": "failed", "detail": "boom"}], 0.1
    )
    assert broken["verdict"] == "failed"
    assert broken["ok"] is False
