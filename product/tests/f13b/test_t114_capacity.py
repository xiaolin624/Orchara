"""F13B/T114: sustained 20 top-level stand-in executions at a 100/1000 profile.

Two run modes share one harness (``tests/support/t114.py``):

* **short (default gate)** — ``python -m pytest tests/f13b/test_t114_capacity.py``
  seeds the full 100-user/1000-application profile and runs one 60-second
  saturated trial. It asserts the harness invariants (20 real simultaneous
  top-level executions, queueing separated from execution, the child pool
  progressing, cancel intent separated from stop time, metrics collected) but
  does **not** write the committed evidence file.
* **full (exclusive capacity window)** — ``F13B_CAPACITY_FULL=1
  F13B_WRITE_EVIDENCE=1`` runs three independent 15-minute trials, each on a
  fresh database seeded through the public endpoints, and writes
  ``reports/f13b/T114-capacity.json``.

The 500ms service time is the **capacity-stub deployment knob**
(``ORCHARA_CAPACITY_STUB_SERVICE_DELAY``): stand-in time for a stub model call,
not vendor latency. Stub and real boundaries are reported separately.

The current seeder creates drafts, not the 1000 published applications required
by EVALUATION §5. Short mode is a narrower harness regression. Full mode now
refuses overall acceptance without independently counted published applications;
the original committed evidence is explicitly partial and remains historical.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from support import t114
from support import stack as st

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_ROOT = PRODUCT_ROOT / "tools"
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from evidence_redaction import find_violations  # noqa: E402


def _close(stack) -> None:
    stack.server.stop()
    st.drop_database(stack.dbname)


def test_draft_only_profile_cannot_be_reported_as_full_acceptance() -> None:
    trials = [{"label": f"trial-{index}", "operations": {}} for index in range(1, 4)]
    document = t114.build_document(
        trials=trials, mode="full",
        profile={"user_principal_count": 100, "application_count": 1000,
                 "db_truth": {"applications": 1000, "application_drafts": 1000}},
        quota={}, version={}, commands=[],
    )
    assert document["status"] == "partial"
    assert document["scope_assessment"]["published_application_profile"] == "not_run"
    with pytest.raises(AssertionError, match="published"):
        t114.assert_published_profile(document["profile"])


def test_each_capacity_trial_must_verify_the_published_profile() -> None:
    profile = {"db_truth": {"published_applications": t114.APPLICATIONS}}
    trials = [
        {"label": f"trial-{index}", "operations": {},
         "profile_db_truth": {"published_applications": t114.APPLICATIONS}}
        for index in range(1, 4)
    ]
    for observed, expected in [(None, "partial"), (999, "partial"), (1000, "passed")]:
        trials[-1]["profile_db_truth"]["published_applications"] = observed
        document = t114.build_document(
            trials=trials, mode="full", profile=profile,
            quota={}, version={}, commands=[],
        )
        assert document["status"] == expected


def test_capacity_harness_short_profile(tmp_path: Path, go_binaries, api_server) -> None:
    """The default gate: the whole harness, one short saturated trial."""
    if t114.full_mode():
        pytest.skip("F13B_CAPACITY_FULL=1 selects the full 15min x 3 measurement")
    stack = t114.build_trial_stack(
        tmp_path=tmp_path / "short",
        go_binaries=go_binaries,
        api_server_factory=api_server,
    )
    try:
        seed = t114.seed_profile(stack)
        quota = t114.configure_quota(stack.dbname)
        seconds = t114.short_seconds()
        result = t114.run_trial(
            stack, seed, quota, label="short", seconds=seconds
        )
        t114.assert_trial(result, expect_seconds=seconds)
        print(
            "short profile: "
            f"{t114.USERS} users / {t114.APPLICATIONS} apps / "
            f"top_peak={result['concurrency']['top_level']['peak']} / "
            f"executed={result['queue_and_execution']['top_level'].get('executed_runs')} / "
            f"stub_p50={result['operations'].get('call_p50_s')}s",
            flush=True,
        )
    finally:
        _close(stack)


def test_capacity_full_three_trials(tmp_path: Path, go_binaries, api_server) -> None:
    """The measurement: three 15-minute sustained trials in one capacity window."""
    if not t114.full_mode():
        pytest.skip(
            "the full 15min x 3 measurement requires F13B_CAPACITY_FULL=1 and the "
            "exclusive capacity window"
        )
    document = t114.run_full_measurement(
        tmp_path=tmp_path / "full",
        go_binaries=go_binaries,
        api_server_factory=api_server,
    )
    try:
        t114.assert_document(document)
    finally:
        written = t114.write_evidence(document)
        if written is not None:
            print(f"evidence written: {written}", flush=True)


def test_committed_capacity_evidence_is_clean() -> None:
    """The committed evidence is redaction-clean and carries the three trials."""
    if not t114.EVIDENCE_JSON.exists():
        pytest.skip("written by the F13B_CAPACITY_FULL measurement")
    text = t114.EVIDENCE_JSON.read_text(encoding="utf-8")
    assert find_violations(text) == []
    document = json.loads(text)
    assert document["ticket"] == "T114"
    assert document["issue"] == 115
    assert document["trial_count"] == 3
    assert document["full_measurement"] is True
    # The original trials measured a draft-only profile. Preserve their raw
    # samples, but never turn that historical run into published-profile proof.
    assert document["status"] == "partial"
    assert document["scope_assessment"]["published_application_profile"] == "not_run"
    with pytest.raises(AssertionError, match="published"):
        t114.assert_document(document)
    assert document["service_time"]["stand_in"] is True
    assert document["service_time"]["vendor_latency"] is False
    assert document["capacity_knob"]["top_level_slots"] == t114.TOP_LEVEL_SLOTS
    assert document["capacity_knob"]["child_pool_slots"] == t114.CHILD_SLOTS
    for trial in document["trials"]:
        # The authoritative capacity is the quota counter; the interval sweep can
        # report one extra overlap at a claim/exit hand-over (documented in §4).
        assert trial["concurrency"]["top_level"]["peak"] >= t114.TOP_LEVEL_SLOTS, trial["label"]
        assert trial["resources"]["execution_slots_used"]["max"] == t114.TOP_LEVEL_SLOTS, trial["label"]
        assert trial["resources"]["top_running"]["max"] == t114.TOP_LEVEL_SLOTS, trial["label"]
        assert trial["resources"]["queued_with_live_attempt_max"] == 0, trial["label"]
        assert trial["operations"]["call_p50_s"] >= 0.45, trial["label"]
