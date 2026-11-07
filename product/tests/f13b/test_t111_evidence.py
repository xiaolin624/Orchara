"""T111 evidence aggregation: a partial/failed matrix must never read ``passed``.

These tests are offline (no WSL2/runsc): they drive the recorder directly and
pin the rule the review found missing — the committed acceptance JSON must cover
the complete attack set and every case must pass, otherwise the run is
``partial``/``failed`` and nothing is written over the complete record.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from support import t111


def _fake_host(**_: object) -> dict:
    return {
        "host": "wsl2",
        "kernel": "6.6.87.2-microsoft-standard-WSL2",
        "runsc_version": "runsc version release-20241118.0",
        "rootfs_digest": "sha256:" + "0" * 64,
    }


def _recorder(monkeypatch, tmp_path: Path) -> t111.Evidence:
    monkeypatch.setattr(t111, "host_facts", _fake_host)
    monkeypatch.setattr(t111, "EVIDENCE_PATH", tmp_path / "T111-isolation.json")
    return t111.Evidence(branch="t/test", commit="deadbeef")


def _record_all(recorder: t111.Evidence, *, verdict: str = "passed") -> None:
    for case_id in t111.EXPECTED_CASE_IDS:
        recorder.record(
            case_id,
            stage="run",
            attack=case_id,
            inputs={},
            expected={},
            observed={},
            verdict=verdict,
        )


def test_a_complete_all_passed_run_is_passed(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    document = recorder.document()
    assert document["status"] == "passed"
    assert document["complete"] is True
    assert document["missing_cases"] == []
    assert document["failed_cases"] == []


def test_one_failed_case_makes_the_whole_run_failed(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    recorder.cases[0]["verdict"] = "failed"
    document = recorder.document()
    assert document["status"] == "failed"
    assert document["failed_cases"] == [recorder.cases[0]["id"]]
    assert document["complete"] is True


def test_a_subset_with_one_case_is_partial(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    recorder.record(
        "run-timeout-reclaim",
        stage="run",
        attack="only one attack ran",
        inputs={},
        expected={},
        observed={},
    )
    document = recorder.document()
    assert document["status"] == "partial"
    assert document["complete"] is False
    assert len(document["missing_cases"]) == len(t111.EXPECTED_CASE_IDS) - 1


def test_an_unexpected_or_duplicate_case_is_partial(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    recorder.record(
        "not-an-accepted-case",
        stage="run",
        attack="extra",
        inputs={},
        expected={},
        observed={},
    )
    assert recorder.document()["status"] == "partial"
    assert recorder.document()["unexpected_cases"] == ["not-an-accepted-case"]

    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    recorder.cases.append(dict(recorder.cases[0]))
    assert recorder.document()["status"] == "partial"


def test_a_blocked_component_is_partial(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    recorder.block("x", stage="build", attack="blocked attack", missing="no runtime")
    assert recorder.document()["status"] == "partial"


def test_an_incomplete_run_never_overwrites_the_committed_record(
    monkeypatch, tmp_path: Path
) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    monkeypatch.setenv("F13B_WRITE_EVIDENCE", "1")

    recorder.record(
        "run-timeout-reclaim",
        stage="run",
        attack="partial run",
        inputs={},
        expected={},
        observed={},
    )
    assert recorder.write_if_enabled() is None
    assert not (tmp_path / "T111-isolation.json").exists()

    # A fresh complete run is the only thing allowed to replace the record.
    recorder = _recorder(monkeypatch, tmp_path)
    _record_all(recorder)
    written = recorder.write_if_enabled()
    assert written is not None
    document = json.loads(written.read_text(encoding="utf-8"))
    assert document["status"] == "passed"
    assert document["complete"] is True
    assert document["expected_cases"] == list(t111.EXPECTED_CASE_IDS)


def test_a_failed_run_never_overwrites_the_committed_record(
    monkeypatch, tmp_path: Path
) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    monkeypatch.setenv("F13B_WRITE_EVIDENCE", "1")
    _record_all(recorder)
    recorder.cases[-1]["verdict"] = "failed"
    assert recorder.write_if_enabled() is None
    assert not (tmp_path / "T111-isolation.json").exists()
