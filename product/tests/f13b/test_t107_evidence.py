"""T107 evidence redaction: a real attempt token must never reach a report.

The review found the recorder serialized ``platform.run_attempts`` facts as-is,
so the committed ``T107-faults.json`` carried a full (revoked) attempt token.
These tests are offline: they pin that the writer redacts at the source, that a
credential-shaped document is refused, and that the committed evidence is clean.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from support import t107

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PRODUCT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))
from evidence_redaction import find_violations  # noqa: E402

COMMITTED = PRODUCT_ROOT / "reports" / "f13b" / "T107-faults.json"
TOKEN = "4d9dd2c6-0241-442c-af0a-5cd1852ecf89"


def _recorder(monkeypatch, tmp_path: Path) -> t107.Faults:
    monkeypatch.setattr(t107, "EVIDENCE_PATH", tmp_path / "T107-faults.json")
    return t107.Faults(branch="t/test", commit="deadbeef")


def _record_one(recorder: t107.Faults, token: str = TOKEN) -> None:
    recorder.record(
        "FAULT-04",
        title="v1 recovery",
        injection_point="test",
        inputs={"run": {"id": "r1"}, "current_attempt_token": token},
        independent_truth={
            "attempts": [{"id": "a1", "token": token}],
            # The waitings row's JSONB body embeds the token inside another
            # string; the writer must scrub that copy too.
            "waitings": {"body": '{"id": "' + token + '", "state": "exited"}'},
        },
        expected={"state": "recovered"},
        observed={"state": "recovered"},
    )


def test_the_writer_redacts_attempt_tokens(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    monkeypatch.setenv("F13B_WRITE_EVIDENCE", "1")
    _record_one(recorder)
    written = recorder.write_if_enabled()
    assert written is not None
    text = written.read_text(encoding="utf-8")
    assert find_violations(text) == []
    assert TOKEN not in text
    document = json.loads(text)
    assert document["faults"][0]["inputs"]["current_attempt_token"] == "<redacted>"
    assert document["faults"][0]["independent_truth"]["attempts"][0]["token"] == "<redacted>"
    embedded = document["faults"][0]["independent_truth"]["waitings"]["body"]
    assert TOKEN not in embedded
    assert "<redacted>" in embedded


def test_the_writer_refuses_a_document_that_still_leaks(monkeypatch, tmp_path: Path) -> None:
    recorder = _recorder(monkeypatch, tmp_path)
    monkeypatch.setenv("F13B_WRITE_EVIDENCE", "1")
    # ``redact_value`` is bypassed so the writer's own scanner is exercised as
    # the last line of defence, not just the field-name rewriter.
    monkeypatch.setattr(t107, "redact_value", lambda value: value)
    monkeypatch.setattr(
        recorder,
        "document",
        lambda: {
            "faults": [
                {
                    "id": "FAULT-04",
                    # Not under a credential-named member: only the scanner's
                    # own Bearer rule can catch it.
                    "observed": {"detail": "sent Bearer " + "a" * 32}
                }
            ]
        },
    )
    with pytest.raises(AssertionError, match="credential-shaped"):
        recorder.write_if_enabled()
    assert not (tmp_path / "T107-faults.json").exists()


def test_the_committed_evidence_is_clean() -> None:
    text = COMMITTED.read_text(encoding="utf-8")
    assert find_violations(text) == []
    assert TOKEN not in text
