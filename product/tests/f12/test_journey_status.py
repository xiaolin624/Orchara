"""Regression tests for truthful journey statuses (T100/T101).

The review found both recorders promoted a body that simply returned into
``passed`` without any assertion. That violates AGENTS.md hard rule 1
(a journey that did not run is ``not_run``) and TESTING-AND-DATASETS.md §7
(skips/empty verifications never count as passed).

These tests are pure recorder tests: no real stack, no evidence write.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:  # pragma: no cover - pytest already prepends it
    sys.path.insert(0, str(HERE))

import t100_support as t100  # noqa: E402
import t101_support as t101  # noqa: E402


def test_a_t101_journey_that_records_nothing_stays_not_run() -> None:
    with t101.journey("T-empty", "empty body") as record:
        pass
    assert record.status == "not_run"
    assert record.record()["status"] == "not_run"
    assert t101.records()["T-empty"]["status"] == "not_run"


def test_t101_mark_passed_requires_an_assertion() -> None:
    record = t101.Journey(journey_id="T-no-assertions", title="no assertions")
    with pytest.raises(AssertionError, match="at least one recorded assertion"):
        record.mark_passed()


def test_t101_a_forced_passed_status_without_assertions_is_rejected() -> None:
    record = t101.Journey(journey_id="T-forced", title="forced")
    record.status = "passed"
    with pytest.raises(AssertionError, match="requires at least one recorded assertion"):
        record.record()
    with pytest.raises(AssertionError, match="requires at least one recorded assertion"):
        record.persist()


def test_t101_explicit_pass_after_an_assertion_is_kept_and_counted() -> None:
    with t101.journey("T-one", "one assertion") as record:
        record.check("one real check", True, {"ok": True})
        record.mark_passed()
    payload = record.record()
    assert payload["status"] == "passed"
    assert payload["assertion_count"] == 1


def test_t101_a_blocked_journey_is_not_rewritten_as_passed() -> None:
    with t101.journey("T-blocked", "blocked") as record:
        record.check("a real check ran", True)
        record.block("documented product gap")
        record.mark_passed()
    assert record.status == "blocked"


def test_a_t100_journey_that_records_nothing_stays_not_run() -> None:
    log = t100.JourneyLog()
    with log.journey(
        "T100-empty",
        title="empty body",
        entry_commands=[],
        entry_http=[],
        truth_sources=[],
    ) as handle:
        pass
    assert handle.record.status == "not_run"
    assert log.records()[0]["status"] == "not_run"


def test_t100_mark_passed_requires_an_assertion() -> None:
    log = t100.JourneyLog()
    handle = log.journey(
        "T100-no-assertions",
        title="no assertions",
        entry_commands=[],
        entry_http=[],
        truth_sources=[],
    )
    with pytest.raises(AssertionError, match="at least one recorded assertion"):
        handle.mark_passed()


def test_t100_explicit_pass_after_an_assertion_is_kept() -> None:
    log = t100.JourneyLog()
    with log.journey(
        "T100-one",
        title="one assertion",
        entry_commands=["real command"],
        entry_http=["GET /api/v1/auth/me"],
        truth_sources=["platform.sessions"],
    ) as handle:
        handle.assertion("one real check")
        handle.mark_passed()
    assert handle.record.status == "passed"
    assert log.records()[0]["status"] == "passed"


def test_a_not_run_journey_is_written_to_the_evidence_report(monkeypatch, tmp_path) -> None:
    """A journey that recorded nothing must appear as ``not_run`` in the report."""
    monkeypatch.setenv("F12_WRITE_EVIDENCE", "1")
    monkeypatch.setattr(t100, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(t100, "EVIDENCE_JSON", tmp_path / "T100-journeys.json")
    log = t100.JourneyLog()
    with log.journey(
        "T100-not-run",
        title="nothing ran",
        entry_commands=[],
        entry_http=[],
        truth_sources=[],
    ):
        pass
    payload = json.loads((tmp_path / "T100-journeys.json").read_text(encoding="utf-8"))
    assert payload["journeys"][0]["journey_id"] == "T100-not-run"
    assert payload["journeys"][0]["status"] == "not_run"


def test_evidence_writes_are_redacted_at_the_recorder(monkeypatch, tmp_path) -> None:
    """A secret handed to a recorder must never land in the evidence file."""
    monkeypatch.setenv("T101_WRITE_EVIDENCE", "0")
    record = t101.Journey(journey_id="T-redacted", title="redaction")
    record.check(
        "a response detail",
        True,
        {
            "secret": "orchara_" + "A" * 43,
            "headers": {"x-connector-secret": "connector-value"},
        },
    )
    payload = record.record()
    assert "A" * 43 not in str(payload)
    assert "connector-value" not in str(payload)
    assert payload["assertions"][0]["detail"].count("<redacted>") == 2

    monkeypatch.setenv("F12_WRITE_EVIDENCE", "1")
    monkeypatch.setattr(t100, "EVIDENCE_DIR", tmp_path)
    monkeypatch.setattr(t100, "EVIDENCE_JSON", tmp_path / "T100-journeys.json")
    log = t100.JourneyLog()
    with log.journey(
        "T100-redacted",
        title="redaction",
        entry_commands=["orchara_" + "B" * 43],
        entry_http=[],
        truth_sources=[],
    ) as handle:
        handle.fact("connector", {"x-connector-secret": "connector-value"})
        handle.assertion("one real check")
        handle.mark_passed()
    written = (tmp_path / "T100-journeys.json").read_text(encoding="utf-8")
    assert "B" * 43 not in written
    assert "connector-value" not in written
    assert "<redacted>" in written
    assert os.environ["F12_WRITE_EVIDENCE"] == "1"
