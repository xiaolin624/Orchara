"""P04/T018: a lost receipt must not become a second write.

Three properties are asserted separately, because each one alone is not enough:

* the client cannot tell success from failure, so it records ``unknown``;
* the independent ledger is the truth about how many writes happened;
* recovery only completes when the ledger proves the write landed, and only
  resends when there is proof it never left.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "probes" / "p04_unknown"))

import unknown_probe as probe  # noqa: E402


@pytest.fixture()
def ledger(tmp_path: Path) -> probe.Ledger:
    return probe.Ledger(tmp_path / "ledger.jsonl")


def _unknown(key: str = "op-1") -> probe.Operation:
    return probe.Operation(key=key, payload={"amount": "1.00"}, state="unknown", sends=1)


# --------------------------------------------------------------------------
# state semantics
# --------------------------------------------------------------------------
def test_unknown_is_its_own_state() -> None:
    operation = _unknown()
    assert operation.state == "unknown"
    assert operation.state not in ("completed", "failed")


def test_delivery_failure_is_unknown_not_failed() -> None:
    """A timeout means 'maybe applied', which is exactly why it is unknown."""
    outcome, _detail = probe.send_write("http://127.0.0.1:9/apply", "op-x", {}, timeout=1.0)
    assert outcome == "unknown"


# --------------------------------------------------------------------------
# reconciliation
# --------------------------------------------------------------------------
def test_ledger_record_completes_without_resending(ledger: probe.Ledger) -> None:
    ledger.record("op-1", {"amount": "1.00"})
    recovered = probe.recover(ledger, "op-1", _unknown())

    assert recovered.state == "completed"
    assert recovered.sends == 1, "recovery must not resend"
    assert ledger.count_for("op-1") == 1


def test_no_ledger_record_and_no_proof_stays_unknown(ledger: probe.Ledger) -> None:
    recovered = probe.recover(ledger, "op-1", _unknown())

    assert recovered.state == "unknown", "absence of a record is not proof of non-delivery"
    assert recovered.sends == 1
    assert "must not auto-resend" in recovered.detail


def test_proof_of_non_send_allows_a_retry(ledger: probe.Ledger) -> None:
    recovered = probe.recover(ledger, "op-1", _unknown(), proof_of_non_send=True)

    assert recovered.state == "failed", "with proof it never left, the operation is failed, not unknown"
    assert recovered.sends == 1, "this step decides the next attempt; it does not send"


def test_a_completed_operation_is_left_alone(ledger: probe.Ledger) -> None:
    done = probe.Operation(key="op-1", payload={}, state="completed", sends=1)
    assert probe.recover(ledger, "op-1", done) is done


# --------------------------------------------------------------------------
# end to end
# --------------------------------------------------------------------------
def test_lost_receipt_writes_exactly_once(tmp_path: Path) -> None:
    report = probe.run_scenario(tmp_path / "ledger.jsonl")

    assert report["initial"]["state"] == "unknown", report["initial"]
    assert report["ledger_entries"] == 1, "the service applied the write exactly once"
    assert report["final"]["state"] == "completed"
    assert report["final"]["sends"] == 1, "no second send may happen"
    assert all(report["assertions"].values()), report["assertions"]
