"""P04/T013: losing the lease must not start a second executor.

The unit tests pin the decision rules (process liveness, token status, event
ordering) without a database or a guest. The integration tests then drive the
same rules against the real isolated PostgreSQL cluster and a controllable
process in WSL, because "the lease expired" alone is not evidence that the old
process stopped.
"""

from __future__ import annotations

import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = PRODUCT_ROOT / "probes" / "p04_lease"
sys.path.insert(0, str(PROBE_ROOT))
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))

import lease_probe as probe  # noqa: E402
import pg_dev  # noqa: E402


# --------------------------------------------------------------------------
# pure decision logic
# --------------------------------------------------------------------------
def _process(observed: int | None, recorded: int = 100, pid: int = 42) -> probe.ProcessEvidence:
    return probe.ProcessEvidence(pid=pid, recorded_start_ticks=recorded, observed_start_ticks=observed)


def test_new_attempt_is_blocked_while_the_old_process_is_alive() -> None:
    decision = probe.attempt_decision(_process(observed=100), "recovery_pending")

    assert decision.decision == "blocked"
    assert "still running" in decision.reason
    assert decision.evidence["process"]["verdict"] == "still_running"


def test_new_attempt_is_allowed_once_the_old_process_is_gone() -> None:
    decision = probe.attempt_decision(_process(observed=None), "recovery_pending")

    assert decision.decision == "start"
    assert "exited" in decision.reason
    assert decision.evidence["process"]["verdict"] == "exited"


def test_pid_reuse_is_not_mistaken_for_still_running() -> None:
    """A reused pid is a different process: it must not block recovery."""
    process = _process(observed=999)
    assert process.verdict() == "pid_reused"

    decision = probe.attempt_decision(process, "recovery_pending")
    assert decision.decision == "start", decision.reason
    assert "different process" in decision.reason
    assert decision.evidence["process"]["verdict"] == "pid_reused"


def test_attempt_requires_a_recovery_pending_run() -> None:
    decision = probe.attempt_decision(_process(observed=None), "running")

    assert decision.decision == "blocked"
    assert "recovery_pending" in decision.reason


def test_missing_process_identity_does_not_prove_an_exit() -> None:
    """Absence of a recorded pid is not death evidence; fail closed."""
    decision = probe.attempt_decision(None, "recovery_pending")

    assert decision.decision == "blocked"
    assert "no process identity" in decision.reason


def test_parse_start_ticks_uses_field_22_after_the_comm_field() -> None:
    # The comm field may contain spaces; only the numeric fields after the last
    # ')' are positional. Tokens 1..20 are stat fields 3..22, so field 22 == 20.
    line = "123 (a tricky name) " + " ".join(str(value) for value in range(1, 21))
    assert probe.parse_start_ticks(line) == 20


# --------------------------------------------------------------------------
# token state
# --------------------------------------------------------------------------
def test_capability_status_rejects_superseded_and_unknown_tokens() -> None:
    active = probe.TokenRecord(token="token-1", worker_id="w", run_id="r", attempt=1)
    superseded = probe.TokenRecord(
        token="token-1",
        worker_id="w",
        run_id="r",
        attempt=1,
        superseded_at="2026-11-01T00:00:00+00:00",
        superseded_by="token-2",
    )

    assert probe.capability_verdict(active) == "ok"
    assert probe.capability_verdict(superseded) == "token_superseded"
    assert probe.capability_verdict(None) == "token_unknown"


def test_reports_never_contain_the_raw_token() -> None:
    token = str(uuid.uuid4())
    fingerprint = probe.token_fingerprint(token)

    assert fingerprint is not None
    assert fingerprint.startswith("tok_")
    assert token not in fingerprint
    assert probe.token_fingerprint(None) is None


# --------------------------------------------------------------------------
# event log
# --------------------------------------------------------------------------
def test_event_log_is_append_only_and_time_ordered(tmp_path: Path) -> None:
    path = tmp_path / "lease-events.jsonl"
    log = probe.EventLog(path, truncate=True)
    for name in ("heartbeat_lost", "worker_declared_stale", "attempt_requested"):
        log.append(name, run_id="run-1", detail={"why": name})

    entries = log.entries()
    assert [entry["sequence"] for entry in entries] == [1, 2, 3]
    assert all(
        entries[index]["monotonic_ns"] < entries[index + 1]["monotonic_ns"]
        for index in range(len(entries) - 1)
    )
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3

    # Reopening continues the sequence instead of overwriting the record.
    reopened = probe.EventLog(path)
    reopened.append("attempt_blocked", run_id="run-1", detail={"reason": "still running"})
    assert reopened.entries()[-1]["sequence"] == 4
    assert len(path.read_text(encoding="utf-8").splitlines()) == 4

    verification = probe.verify_event_log(
        reopened.entries(),
        required_order=("heartbeat_lost", "worker_declared_stale", "attempt_requested", "attempt_blocked"),
    )
    assert verification["ok"], verification


def test_event_order_check_catches_a_reordered_record() -> None:
    base = {
        "run_id": "run-1",
        "worker_id": None,
        "token_fingerprint": None,
        "detail": {},
    }
    entries = [
        {**base, "sequence": 2, "monotonic_ns": 2, "at": "2026-11-01T00:00:02+00:00", "event": "attempt_blocked"},
        {**base, "sequence": 1, "monotonic_ns": 1, "at": "2026-11-01T00:00:01+00:00", "event": "heartbeat_lost"},
    ]
    verification = probe.verify_event_log(entries, required_order=("heartbeat_lost", "attempt_blocked"))

    assert verification["ok"] is False
    assert any("sequence" in problem for problem in verification["problems"])


# --------------------------------------------------------------------------
# integration: real PostgreSQL, real controllable process
# --------------------------------------------------------------------------
DB_READY = pg_dev.is_running()


def _guest_ready() -> bool:
    import shutil

    if shutil.which("wsl") is None:
        return False
    try:
        completed = probe._run_wsl("echo ok", timeout=60)
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False
    return completed.returncode == 0


GUEST_READY = DB_READY and _guest_ready()

requires_db = pytest.mark.skipif(not DB_READY, reason="development PostgreSQL cluster unavailable")
requires_guest = pytest.mark.skipif(
    not GUEST_READY, reason="guest or development PostgreSQL cluster unavailable"
)


@pytest.fixture(scope="module")
def schema() -> None:
    if not DB_READY:
        pytest.skip("development PostgreSQL cluster unavailable")
    applied = probe.apply_migrations()
    assert "0004_fence_tokens.sql" in applied


@pytest.fixture()
def run_ids() -> tuple[str, list[str]]:
    run_id = f"lease-test-{uuid.uuid4().hex[:10]}"
    worker_ids: list[str] = []
    yield run_id, worker_ids
    probe.cleanup_run(run_id, worker_ids)


def _seed(run_id: str, worker_id: str, state: str = "running") -> None:
    probe.register_worker(worker_id)
    probe.seed_run(run_id, worker_id, state=state)


@requires_guest
def test_live_old_process_blocks_the_new_attempt(
    schema: None, run_ids: tuple[str, list[str]], tmp_path: Path
) -> None:
    run_id, worker_ids = run_ids
    first_worker = f"{run_id}-w1"
    worker_ids.append(first_worker)
    _seed(run_id, first_worker)

    process = probe.start_guest_process(60)
    try:
        probe.issue_token(first_worker, run_id, process["pid"], process["start_ticks"])
        probe.declare_stale(first_worker, run_id)

        events = probe.EventLog(tmp_path / "blocked.jsonl", truncate=True)
        decision = probe.request_attempt(
            run_id,
            worker_id=f"{run_id}-w2",
            start=True,
            events=events,
        )

        assert decision["decision"] == "blocked", decision
        assert "still running" in decision["reason"]
        assert decision["evidence"]["process"]["verdict"] == "still_running"
        assert probe.token_counts(run_id) == {"total": 1, "active": 1, "superseded": 0}, (
            "a blocked attempt must not issue or revoke a token"
        )
        assert [entry["event"] for entry in events.entries()][-1] == "attempt_blocked"
    finally:
        probe.kill_guest_process(process["pid"])


@requires_guest
def test_new_attempt_allowed_once_the_old_process_is_gone(
    schema: None, run_ids: tuple[str, list[str]]
) -> None:
    run_id, worker_ids = run_ids
    first_worker = f"{run_id}-w1"
    second_worker = f"{run_id}-w2"
    worker_ids.extend([first_worker, second_worker])
    _seed(run_id, first_worker)

    process = probe.start_guest_process(60)
    try:
        probe.issue_token(first_worker, run_id, process["pid"], process["start_ticks"])
        probe.declare_stale(first_worker, run_id)
        probe.kill_guest_process(process["pid"])
        exit_evidence = probe.wait_for_process_exit(process["pid"], process["start_ticks"])
        assert exit_evidence.verdict() != "still_running"

        decision = probe.request_attempt(run_id, worker_id=second_worker, start=True)

        assert decision["decision"] == "started", decision
        old_token, new_token = probe.tokens_for_run(run_id)
        assert old_token.superseded_at is not None
        assert new_token.active
        assert probe.capability_status(old_token.token) == "token_superseded"
        assert probe.capability_status(new_token.token) == "ok"
        assert probe.token_counts(run_id) == {"total": 2, "active": 1, "superseded": 1}
        assert probe.run_state(run_id) == "running"
    finally:
        probe.kill_guest_process(process["pid"])


@requires_db
def test_racing_supersessions_produce_exactly_one_winner(
    schema: None, run_ids: tuple[str, list[str]]
) -> None:
    """Two attempts presenting the same old token: the UPDATE ... RETURNING CAS
    may only hand the lease to one of them."""
    run_id, worker_ids = run_ids
    first_worker = f"{run_id}-w1"
    workers = [f"{run_id}-w2", f"{run_id}-w3"]
    worker_ids.extend([first_worker, *workers])
    _seed(run_id, first_worker)

    first = probe.issue_token(first_worker, run_id)
    assert first["won"] is True
    old_token = str(first["new_token"])

    barrier = threading.Barrier(2)

    def racer(worker_id: str) -> dict:
        barrier.wait(timeout=10)
        return probe.supersede_token(old_token, worker_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(racer, workers))

    winners = [result for result in results if result["won"]]
    losers = [result for result in results if not result["won"]]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert losers[0]["new_token"] is None
    assert probe.token_counts(run_id) == {"total": 2, "active": 1, "superseded": 1}
    assert probe.active_token(run_id) is not None
    assert probe.capability_status(old_token) == "token_superseded"
    assert probe.capability_status(str(winners[0]["new_token"])) == "ok"


@requires_guest
def test_full_outage_and_recovery_are_recorded_in_time_order(
    schema: None, run_ids: tuple[str, list[str]], tmp_path: Path
) -> None:
    run_id, worker_ids = run_ids
    worker_base = f"{run_id}-sim"
    worker_ids.extend([f"{worker_base}-1", f"{worker_base}-2"])
    events_path = tmp_path / "lease-events.jsonl"

    report = probe.run_simulation(
        events_path=events_path,
        run_id=run_id,
        worker_id=worker_base,
        process_seconds=60,
        migrate=False,
    )

    assert report["status"] == "passed", report["assertions"]
    assert all(report["assertions"].values()), report["assertions"]
    assert report["event_order"]["ok"], report["event_order"]
    assert [entry["sequence"] for entry in report["timeline"]] == list(
        range(1, len(report["timeline"]) + 1)
    )

    events = [entry["event"] for entry in report["timeline"]]
    required = (
        "heartbeat_lost",
        "worker_declared_stale",
        "attempt_requested",
        "attempt_blocked",
        "old_process_exited",
        "attempt_allowed",
        "token_superseded",
        "capability_rejected",
        "recovery_completed",
    )
    for name in required:
        assert name in events, (name, events)

    blocked = [entry for entry in report["timeline"] if entry["event"] == "attempt_blocked"]
    assert blocked, events
    assert "still running" in blocked[0]["detail"]["reason"], blocked[0]

    # The report is an artefact: it must carry fingerprints, never the tokens.
    assert "new_token" not in report
    assert all(str(token["token_fingerprint"]).startswith("tok_") for token in report["tokens"])
    assert report["process"]["verdict_after_kill"] in ("exited", "pid_reused")
    assert report["event_log"].endswith("lease-events.jsonl")
