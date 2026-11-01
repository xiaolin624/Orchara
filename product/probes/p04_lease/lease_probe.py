#!/usr/bin/env python3
"""P04/T013: lose the lease without starting a second executor.

The scenario this probe reproduces is the one that produces double execution:

1. an attempt holds a lease token and is heartbeating;
2. the heartbeat disappears (network partition, killed supervisor, ...);
3. the control plane declares the worker stale and parks the run in
   ``recovery_pending``;
4. a recovery attempt is requested *while the old process is still alive*.

The probe refuses step 4 and records why. It refuses on process liveness, not
on lease expiry: ``/proc/<pid>/stat`` field 22 (start ticks) is recorded when the
attempt starts, and a live process, an exited process and a reused PID are
distinguished. A reused PID is *not* the old process, so it must not block
recovery forever and must not be killed.

Once the old process is gone, the next attempt is allowed and takes the lease
over with a fencing token: ``app.supersede_token`` performs one conditional
``UPDATE ... WHERE superseded_at IS NULL RETURNING`` (a compare-and-swap), so of
two racing attempts exactly one can win and become active. From then on the old
token is rejected by capability calls with ``token_superseded`` while the new
token is accepted; an unknown token is rejected with ``token_unknown``.

Every step is appended to a JSONL event log with a monotonic sequence, a
monotonic clock reading and a wall-clock timestamp, so the outage and the
recovery decision are auditable in time order::

    heartbeat_lost -> worker_declared_stale -> attempt_requested
    -> attempt_blocked ("still running") -> old_process_exited
    -> attempt_requested -> attempt_allowed -> token_superseded
    -> capability_rejected (old token) -> capability_allowed (new token)
    -> recovery_completed

What this does **not** prove: a revoked token cannot roll back an external side
effect the old process already sent, and the token check is not yet an atomic
fence around arbitrary database writes (that needs the checkpoint-write fencing
described in docs/spec/research/execution-fencing-options.md). The probe is the
control-plane half of the argument; the drain gate (T016) is the other half.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402

WSL_DISTRO = "Ubuntu-24.04"
MIGRATIONS = (
    PRODUCT_ROOT / "db" / "migrations" / "0001_worker_lease.sql",
    PRODUCT_ROOT / "db" / "migrations" / "0004_fence_tokens.sql",
)
DEFAULT_EVENTS = PRODUCT_ROOT / "reports" / "p04" / "lease-events.jsonl"
GONE_SENTINEL = "__orchara_process_gone__"

ProcessVerdict = Literal["exited", "still_running", "pid_reused"]
AttemptVerdict = Literal["start", "blocked"]
CapabilityVerdict = Literal["ok", "token_superseded", "token_unknown"]

REQUIRED_EVENT_ORDER: tuple[str, ...] = (
    "heartbeat_lost",
    "worker_declared_stale",
    "attempt_requested",
    "attempt_blocked",
    "old_process_exited",
    "attempt_allowed",
    "token_superseded",
    "new_attempt_started",
    "capability_rejected",
    "recovery_completed",
)


class ProbeError(RuntimeError):
    """A probe step could not obtain real evidence. Never report this as a pass."""


# --------------------------------------------------------------------------
# process evidence: a pid alone is not an identity
# --------------------------------------------------------------------------
def _run_wsl(command: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "-u", "root", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def parse_start_ticks(stat_line: str) -> int | None:
    """Return ``/proc/<pid>/stat`` field 22 (start ticks) from one stat line.

    The comm field may contain spaces and is wrapped in parentheses, so the
    numeric fields start after the last ``)``; field 22 is then index 19.
    """
    after_comm = stat_line.rsplit(")", 1)[-1].split()
    try:
        return int(after_comm[19])
    except (IndexError, ValueError):
        return None


def read_start_ticks(pid: int | None) -> int | None:
    """Read start ticks for ``pid``; ``None`` means the process no longer exists.

    A read failure raises: "cannot tell" is not evidence that the process died.
    """
    if not pid or pid <= 0:
        return None
    completed = _run_wsl(
        f"if [ -e /proc/{int(pid)}/stat ]; then cat /proc/{int(pid)}/stat; "
        f"else echo {GONE_SENTINEL}; fi"
    )
    if completed.returncode != 0:
        raise ProbeError(
            f"cannot read /proc/{pid}/stat in {WSL_DISTRO}: {completed.stderr.strip()}"
        )
    text = completed.stdout.strip()
    if not text:
        raise ProbeError(f"empty /proc/{pid}/stat output; liveness is unknown")
    if text == GONE_SENTINEL:
        return None
    ticks = parse_start_ticks(text)
    if ticks is None:
        raise ProbeError(f"cannot parse start ticks from /proc/{pid}/stat")
    return ticks


@dataclass(frozen=True)
class ProcessEvidence:
    """Recorded identity of the old attempt's process, plus what we observe now."""

    pid: int
    recorded_start_ticks: int
    observed_start_ticks: int | None

    def verdict(self) -> ProcessVerdict:
        if self.observed_start_ticks is None:
            return "exited"
        if self.observed_start_ticks == self.recorded_start_ticks:
            return "still_running"
        # Same number, different process: the old one is gone. It must not be
        # treated as the old process (which would block forever) and must not be
        # killed (that would kill an innocent process).
        return "pid_reused"

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "recorded_start_ticks": self.recorded_start_ticks,
            "observed_start_ticks": self.observed_start_ticks,
            "verdict": self.verdict(),
        }


def collect_process_evidence(pid: int | None, recorded_start_ticks: int | None) -> ProcessEvidence | None:
    """Return evidence, or ``None`` when the attempt recorded no process identity."""
    if not pid or recorded_start_ticks is None:
        return None
    return ProcessEvidence(
        pid=int(pid),
        recorded_start_ticks=int(recorded_start_ticks),
        observed_start_ticks=read_start_ticks(int(pid)),
    )


def start_guest_process(seconds: int = 120) -> dict[str, int]:
    """Start a controllable process in the guest and capture its identity.

    ``nohup setsid`` matters: a plain background job dies with the shell that
    launched it, which would make "still running" untestable.
    """
    script = (
        f"nohup setsid sleep {int(seconds)} >/dev/null 2>&1 & "
        "pid=$!; sleep 0.4; echo \"$pid\"; "
        "cat /proc/$pid/stat 2>/dev/null || true"
    )
    completed = _run_wsl(script)
    if completed.returncode != 0:
        raise ProbeError(f"cannot start a guest process: {completed.stderr.strip()}")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ProbeError(f"guest process did not report its identity: {completed.stdout!r}")
    pid = int(lines[0])
    ticks = parse_start_ticks(lines[1])
    if ticks is None:
        raise ProbeError(f"cannot parse start ticks for guest pid {pid}")
    return {"pid": pid, "start_ticks": ticks}


def kill_guest_process(pid: int) -> None:
    _run_wsl(f"kill -9 {int(pid)} 2>/dev/null || true", timeout=60)


def wait_for_process_exit(pid: int, recorded_start_ticks: int, timeout: float = 20.0) -> ProcessEvidence:
    """Poll until the recorded process is gone (or its PID was reused)."""
    deadline = time.monotonic() + timeout
    evidence = ProcessEvidence(pid=pid, recorded_start_ticks=recorded_start_ticks, observed_start_ticks=None)
    while time.monotonic() < deadline:
        evidence = ProcessEvidence(
            pid=pid,
            recorded_start_ticks=recorded_start_ticks,
            observed_start_ticks=read_start_ticks(pid),
        )
        if evidence.verdict() != "still_running":
            return evidence
        time.sleep(0.25)
    return evidence


# --------------------------------------------------------------------------
# attempt gate: only process evidence can authorise a new attempt
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class AttemptDecision:
    decision: AttemptVerdict
    reason: str
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.decision == "blocked"

    def as_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "reason": self.reason, "evidence": self.evidence}


def attempt_decision(process: ProcessEvidence | None, run_state: str | None) -> AttemptDecision:
    """Decide whether the next attempt may start.

    Order matters: a run that is not pending recovery is not eligible at all,
    and a missing process identity is *not* treated as proof of death.
    """
    if run_state is None:
        return AttemptDecision(
            decision="blocked",
            reason="run is unknown to the control plane; cannot start an attempt",
            evidence={"run_state": None},
        )
    if run_state != "recovery_pending":
        return AttemptDecision(
            decision="blocked",
            reason=f"run state is {run_state!r}, not 'recovery_pending'; a new attempt may not start",
            evidence={"run_state": run_state},
        )
    if process is None:
        return AttemptDecision(
            decision="blocked",
            reason="the old attempt recorded no process identity; cannot prove it exited",
            evidence={"process": None},
        )

    verdict = process.verdict()
    evidence = {"run_state": run_state, "process": process.as_dict()}
    if verdict == "still_running":
        return AttemptDecision(
            decision="blocked",
            reason=f"old worker process {process.pid} is still running (pid + start ticks match)",
            evidence=evidence,
        )
    if verdict == "pid_reused":
        return AttemptDecision(
            decision="start",
            reason=f"old process is gone; pid {process.pid} now belongs to a different process (start ticks differ)",
            evidence=evidence,
        )
    return AttemptDecision(
        decision="start",
        reason="old process exited; pid + start ticks are no longer present",
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# tokens and capability calls (pure half; the SQL is in migration 0004)
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TokenRecord:
    token: str
    worker_id: str
    run_id: str
    attempt: int
    issued_at: str | None = None
    superseded_at: str | None = None
    superseded_by: str | None = None
    process_pid: int | None = None
    process_start_ticks: int | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "TokenRecord":
        return cls(
            token=row["token"],
            worker_id=row["worker_id"],
            run_id=row["run_id"],
            attempt=int(row["attempt"]),
            issued_at=row.get("issued_at"),
            superseded_at=row.get("superseded_at"),
            superseded_by=row.get("superseded_by"),
            process_pid=row.get("process_pid"),
            process_start_ticks=row.get("process_start_ticks"),
        )

    @property
    def active(self) -> bool:
        return self.superseded_at is None

    def status(self) -> CapabilityVerdict:
        return "ok" if self.active else "token_superseded"

    def as_dict(self) -> dict[str, Any]:
        return {
            "token_fingerprint": token_fingerprint(self.token),
            "worker_id": self.worker_id,
            "run_id": self.run_id,
            "attempt": self.attempt,
            "issued_at": self.issued_at,
            "superseded_at": self.superseded_at,
            "superseded_by": token_fingerprint(self.superseded_by),
            "process_pid": self.process_pid,
            "process_start_ticks": self.process_start_ticks,
        }


def token_fingerprint(token: str | None) -> str | None:
    """Short, non-reversible handle for a token: safe to put in logs and reports.

    The raw token is a capability; it is printed by ``issue`` for the operator
    that needs it and never written to the event log or the report.
    """
    if not token:
        return None
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
    return f"tok_{digest}"


def capability_verdict(record: TokenRecord | None) -> CapabilityVerdict:
    if record is None:
        return "token_unknown"
    return record.status()


# --------------------------------------------------------------------------
# append-only, time-ordered event log
# --------------------------------------------------------------------------
class EventLog:
    """Append-only JSONL record with a monotonic sequence and clock reading.

    ``at`` is the wall-clock projection used by humans; ``sequence`` and
    ``monotonic_ns`` are the ordering keys, so a clock step cannot reorder the
    record. Reopening an existing file continues its sequence.
    """

    def __init__(self, path: Path, *, truncate: bool = False) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if truncate:
            self.path.write_text("", encoding="utf-8", newline="\n")
        self._entries = self._read()
        self._sequence = self._entries[-1]["sequence"] if self._entries else 0
        self._monotonic_ns = self._entries[-1]["monotonic_ns"] if self._entries else 0
        self._at = (
            datetime.fromisoformat(self._entries[-1]["at"]) if self._entries else None
        )

    def _read(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProbeError(f"corrupt event log line in {self.path}: {exc}") from exc
            entries.append(entry)
        return entries

    def append(
        self,
        event: str,
        *,
        run_id: str,
        worker_id: str | None = None,
        token: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        if self._at is not None and now < self._at:
            # Keep the wall-clock projection non-decreasing even if the host
            # clock steps backwards; the monotonic fields remain authoritative.
            now = self._at
        monotonic_ns = time.monotonic_ns()
        if monotonic_ns <= self._monotonic_ns:
            monotonic_ns = self._monotonic_ns + 1
        entry = {
            "sequence": self._sequence + 1,
            "at": now.isoformat(timespec="microseconds"),
            "monotonic_ns": monotonic_ns,
            "event": event,
            "run_id": run_id,
            "worker_id": worker_id,
            "token_fingerprint": token_fingerprint(token),
            "detail": detail or {},
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self._sequence = entry["sequence"]
        self._monotonic_ns = entry["monotonic_ns"]
        self._at = now
        self._entries.append(entry)
        return entry

    def entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._entries]


def verify_event_log(
    entries: list[dict[str, Any]],
    required_order: tuple[str, ...] = REQUIRED_EVENT_ORDER,
) -> dict[str, Any]:
    """Check that the record is append-only, time-ordered and shows the full path."""
    problems: list[str] = []
    last_sequence = 0
    last_monotonic = -1
    last_at: datetime | None = None

    for index, entry in enumerate(entries):
        sequence = entry.get("sequence")
        monotonic_ns = entry.get("monotonic_ns")
        at = entry.get("at")
        if not isinstance(sequence, int) or sequence <= last_sequence:
            problems.append(f"entry {index}: sequence is not strictly increasing ({sequence!r})")
        if not isinstance(monotonic_ns, int) or monotonic_ns <= last_monotonic:
            problems.append(f"entry {index}: monotonic_ns is not strictly increasing")
        try:
            at_dt = datetime.fromisoformat(str(at))
        except (TypeError, ValueError):
            problems.append(f"entry {index}: timestamp {at!r} is not ISO-8601")
            at_dt = None
        if at_dt is not None and last_at is not None and at_dt < last_at:
            problems.append(f"entry {index}: timestamp went backwards")
        if isinstance(sequence, int):
            last_sequence = sequence
        if isinstance(monotonic_ns, int):
            last_monotonic = monotonic_ns
        if at_dt is not None:
            last_at = at_dt

    first_positions: dict[str, int] = {}
    for index, entry in enumerate(entries):
        first_positions.setdefault(str(entry.get("event")), index)
    missing = [name for name in required_order if name not in first_positions]
    if missing:
        problems.append(f"missing required events: {missing}")
    positions = [first_positions[name] for name in required_order if name in first_positions]
    if positions != sorted(positions):
        problems.append("required events appear out of order")

    return {
        "ok": not problems,
        "problems": problems,
        "count": len(entries),
        "events": [entry.get("event") for entry in entries],
    }


# --------------------------------------------------------------------------
# PostgreSQL through pg_dev + psql (same style as the drain gate)
# --------------------------------------------------------------------------
def _psql_command() -> list[str]:
    return [
        pg_dev._tool("psql"),
        "-X",
        "-h",
        "127.0.0.1",
        "-p",
        str(pg_dev.DEFAULT_PORT),
        "-U",
        pg_dev.DEFAULT_USER,
        "-d",
        pg_dev.DEFAULT_DB,
        "-tA",
        "-v",
        "ON_ERROR_STOP=1",
    ]


def _psql_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    return env


def _run_psql(extra_args: list[str], *, timeout: int = 120) -> str:
    completed = subprocess.run(
        _psql_command() + extra_args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_psql_env(),
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise ProbeError(f"psql failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def psql(sql: str, *, timeout: int = 120) -> str:
    return _run_psql(["-c", sql], timeout=timeout)


def apply_migrations() -> list[str]:
    """Apply the migrations this probe depends on. Idempotent by construction."""
    applied: list[str] = []
    for path in MIGRATIONS:
        if not path.exists():
            raise ProbeError(f"migration is missing: {path}")
        _run_psql(["-f", str(path)])
        applied.append(path.name)
    return applied


def _sql_text(value: str | None) -> str:
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _sql_int(value: int | None) -> str:
    return "NULL" if value is None else str(int(value))


def _query_json(sql: str) -> dict[str, Any] | None:
    out = psql(sql)
    if not out:
        return None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"expected one JSON row from psql, got {out[:200]!r}") from exc
    if not isinstance(parsed, dict):
        raise ProbeError(f"expected a JSON object from psql, got {type(parsed).__name__}")
    return parsed


def issue_token(
    worker_id: str,
    run_id: str,
    process_pid: int | None = None,
    process_start_ticks: int | None = None,
) -> dict[str, Any]:
    """Issue the first token for a run through ``app.issue_token``."""
    sql = (
        "SELECT row_to_json(x) FROM app.issue_token("
        f"{_sql_text(worker_id)}, {_sql_text(run_id)}, "
        f"{_sql_int(process_pid)}, {_sql_int(process_start_ticks)}) AS x"
    )
    row = _query_json(sql)
    if row is None:
        raise ProbeError("app.issue_token returned no row")
    return row


def supersede_token(
    old_token: str,
    worker_id: str,
    process_pid: int | None = None,
    process_start_ticks: int | None = None,
) -> dict[str, Any]:
    """Take the lease over through ``app.supersede_token`` (atomic CAS)."""
    sql = (
        "SELECT row_to_json(x) FROM app.supersede_token("
        f"{_sql_text(old_token)}, {_sql_text(worker_id)}, "
        f"{_sql_int(process_pid)}, {_sql_int(process_start_ticks)}) AS x"
    )
    row = _query_json(sql)
    if row is None:
        raise ProbeError("app.supersede_token returned no row")
    return row


def capability_status(token: str) -> CapabilityVerdict:
    """Ask the database whether a capability call may proceed."""
    result = psql(f"SELECT app.capability_status({_sql_text(token)})")
    if result not in ("ok", "token_superseded", "token_unknown"):
        raise ProbeError(f"unexpected capability_status result: {result!r}")
    return result  # type: ignore[return-value]


def active_token(run_id: str) -> TokenRecord | None:
    row = _query_json(
        "SELECT row_to_json(x) FROM ("
        "SELECT token, worker_id, run_id, attempt, issued_at, superseded_at, "
        "superseded_by, process_pid, process_start_ticks "
        f"FROM app.lease_tokens WHERE run_id = {_sql_text(run_id)} AND superseded_at IS NULL"
        ") x"
    )
    return TokenRecord.from_row(row) if row is not None else None


def tokens_for_run(run_id: str) -> list[TokenRecord]:
    rows = psql(
        "SELECT row_to_json(x) FROM ("
        "SELECT token, worker_id, run_id, attempt, issued_at, superseded_at, "
        "superseded_by, process_pid, process_start_ticks "
        f"FROM app.lease_tokens WHERE run_id = {_sql_text(run_id)} ORDER BY attempt"
        ") x"
    )
    return [TokenRecord.from_row(json.loads(line)) for line in rows.splitlines() if line.strip()]


def token_counts(run_id: str) -> dict[str, int]:
    records = tokens_for_run(run_id)
    return {
        "total": len(records),
        "active": sum(1 for record in records if record.active),
        "superseded": sum(1 for record in records if not record.active),
    }


def run_state(run_id: str) -> str | None:
    state = psql(f"SELECT state FROM app.runs WHERE run_id = {_sql_text(run_id)}")
    return state or None


def worker_heartbeat(worker_id: str) -> str | None:
    heartbeat = psql(
        f"SELECT heartbeat_at::text FROM app.workers WHERE worker_id = {_sql_text(worker_id)}"
    )
    return heartbeat or None


def register_worker(worker_id: str) -> None:
    psql(
        f"INSERT INTO app.workers (worker_id) VALUES ({_sql_text(worker_id)}) "
        "ON CONFLICT (worker_id) DO UPDATE "
        "SET heartbeat_at = now(), started_at = now(), stopped_at = NULL"
    )


def seed_run(run_id: str, worker_id: str, state: str = "running") -> None:
    psql(
        f"INSERT INTO app.runs (run_id, state, worker_id) "
        f"VALUES ({_sql_text(run_id)}, {_sql_text(state)}, {_sql_text(worker_id)}) "
        "ON CONFLICT (run_id) DO UPDATE "
        "SET state = EXCLUDED.state, worker_id = EXCLUDED.worker_id, updated_at = clock_timestamp()"
    )


def set_run_running(run_id: str, worker_id: str) -> None:
    psql(
        "UPDATE app.runs SET state = 'running', "
        f"worker_id = {_sql_text(worker_id)}, updated_at = clock_timestamp() "
        f"WHERE run_id = {_sql_text(run_id)}"
    )


def declare_stale(worker_id: str, run_id: str, stale_seconds: int = 600) -> dict[str, Any]:
    """Stop the heartbeat and park the run, mirroring the supervisor's decision.

    ``internal/supervisor`` (proven by tests/f00) never restarts a run: it only
    moves a stale worker's running runs to ``recovery_pending``. This probe
    reproduces that transition with the same SQL so the lease scenario can run
    without a second long-lived process; it does not claim to be the supervisor.
    """
    psql(
        f"UPDATE app.workers SET heartbeat_at = now() - make_interval(secs => {int(stale_seconds)}) "
        f"WHERE worker_id = {_sql_text(worker_id)}"
    )
    # psql appends the "UPDATE n" command tag to RETURNING output even with -tA,
    # so count through a CTE to get one unambiguous scalar.
    parked = psql(
        "WITH parked AS ("
        "UPDATE app.runs SET state = 'recovery_pending', updated_at = clock_timestamp() "
        f"WHERE run_id = {_sql_text(run_id)} AND state = 'running' RETURNING run_id"
        ") SELECT count(*) FROM parked"
    )
    return {
        "stale_workers": [worker_id],
        "runs_parked": int(parked or 0),
        "run_state": run_state(run_id),
        "stale_after_seconds": int(stale_seconds),
    }


def cleanup_run(run_id: str, worker_ids: list[str]) -> None:
    """Remove a probe run's rows so the scenario can be replayed deterministically."""
    psql(f"DELETE FROM app.lease_tokens WHERE run_id = {_sql_text(run_id)}")
    psql(f"DELETE FROM app.runs WHERE run_id = {_sql_text(run_id)}")
    for worker_id in worker_ids:
        # Never delete a worker another run still references (foreign key), and
        # never assume this probe owns a worker id it only reused.
        psql(
            "DELETE FROM app.workers AS w "
            f"WHERE w.worker_id = {_sql_text(worker_id)} "
            "AND NOT EXISTS (SELECT 1 FROM app.runs AS r WHERE r.worker_id = w.worker_id)"
        )


# --------------------------------------------------------------------------
# attempt requests
# --------------------------------------------------------------------------
def request_attempt(
    run_id: str,
    *,
    worker_id: str = "probe-recovery-worker",
    pid: int | None = None,
    recorded_start_ticks: int | None = None,
    start: bool = False,
    events: EventLog | None = None,
) -> dict[str, Any]:
    """Request a new attempt and, when authorised, take the lease over.

    Process identity defaults to what the old token recorded, so the caller
    cannot "prove" an exit by simply omitting the pid.
    """
    state = run_state(run_id)
    active = active_token(run_id)
    if pid is None and active is not None:
        pid = active.process_pid
    if recorded_start_ticks is None and active is not None:
        recorded_start_ticks = active.process_start_ticks

    evidence = collect_process_evidence(pid, recorded_start_ticks)
    decision = attempt_decision(evidence, state)
    old_fingerprint = token_fingerprint(active.token) if active is not None else None
    base_detail = {
        "run_state": state,
        "old_token": old_fingerprint,
        "requested_pid": pid,
        "recorded_start_ticks": recorded_start_ticks,
        "process": evidence.as_dict() if evidence is not None else None,
    }

    if events is not None:
        events.append(
            "attempt_requested",
            run_id=run_id,
            worker_id=worker_id,
            token=active.token if active is not None else None,
            detail=base_detail,
        )

    if decision.blocked:
        if events is not None:
            events.append(
                "attempt_blocked",
                run_id=run_id,
                worker_id=worker_id,
                token=active.token if active is not None else None,
                detail={"reason": decision.reason, "decision": decision.as_dict()},
            )
        return {
            "decision": "blocked",
            "reason": decision.reason,
            "evidence": decision.evidence,
            "old_token": old_fingerprint,
            "new_token": None,
            "token_fingerprint": None,
            "attempt": active.attempt if active is not None else None,
        }

    if events is not None:
        events.append(
            "attempt_allowed",
            run_id=run_id,
            worker_id=worker_id,
            token=active.token if active is not None else None,
            detail={"reason": decision.reason, "decision": decision.as_dict()},
        )

    if not start:
        return {
            "decision": "allowed",
            "reason": decision.reason,
            "evidence": decision.evidence,
            "old_token": old_fingerprint,
            "new_token": None,
            "token_fingerprint": None,
            "attempt": None,
        }

    register_worker(worker_id)
    if active is None:
        outcome = issue_token(worker_id, run_id, pid, recorded_start_ticks)
        issued_event = "token_issued"
    else:
        outcome = supersede_token(active.token, worker_id, pid, recorded_start_ticks)
        issued_event = "token_superseded"

    if not outcome.get("won"):
        reason = "another attempt already superseded the lease; this attempt must not start"
        if events is not None:
            events.append(
                "attempt_blocked",
                run_id=run_id,
                worker_id=worker_id,
                token=active.token if active is not None else None,
                detail={"reason": reason, "race_lost": True},
            )
        return {
            "decision": "blocked",
            "reason": reason,
            "evidence": decision.evidence,
            "old_token": old_fingerprint,
            "new_token": None,
            "token_fingerprint": None,
            "attempt": None,
        }

    new_token = str(outcome["new_token"])
    set_run_running(run_id, worker_id)
    issue_detail = {
        "attempt": outcome["attempt"],
        "old_token": old_fingerprint,
        "new_token": token_fingerprint(new_token),
        "pid": pid,
        "start_ticks": recorded_start_ticks,
        "reason": decision.reason,
    }
    if events is not None:
        events.append(
            issued_event,
            run_id=run_id,
            worker_id=worker_id,
            token=new_token,
            detail=issue_detail,
        )
        events.append(
            "new_attempt_started",
            run_id=run_id,
            worker_id=worker_id,
            token=new_token,
            detail={"attempt": outcome["attempt"], "worker_id": worker_id},
        )

    return {
        "decision": "started",
        "reason": decision.reason,
        "evidence": decision.evidence,
        "old_token": old_fingerprint,
        "new_token": new_token,
        "token_fingerprint": token_fingerprint(new_token),
        "attempt": outcome["attempt"],
        "worker_id": worker_id,
    }


def capability_call(
    token: str,
    *,
    events: EventLog | None = None,
    run_id: str | None = None,
    worker_id: str | None = None,
    phase: str = "",
) -> dict[str, Any]:
    """Perform one capability call check: allowed, or rejected with the reason."""
    result = capability_status(token)
    if events is not None:
        if run_id is None:
            raise ProbeError("run_id is required to record capability events")
        event = "capability_allowed" if result == "ok" else "capability_rejected"
        events.append(
            event,
            run_id=run_id,
            worker_id=worker_id,
            token=token,
            detail={"result": result, "phase": phase},
        )
    return {
        "result": result,
        "allowed": result == "ok",
        "token_fingerprint": token_fingerprint(token),
        "phase": phase,
    }


# --------------------------------------------------------------------------
# full scenario
# --------------------------------------------------------------------------
def _display_path(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(PRODUCT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _server_version() -> str:
    return psql("SHOW server_version")


def run_simulation(
    *,
    events_path: Path = DEFAULT_EVENTS,
    run_id: str | None = None,
    worker_id: str = "probe-worker",
    process_seconds: int = 120,
    out_path: Path | None = None,
    migrate: bool = True,
) -> dict[str, Any]:
    """Drive the lease-loss scenario end to end and return the evidence report."""
    if migrate:
        apply_migrations()

    run = run_id or f"run-{uuid.uuid4().hex[:10]}"
    first_worker = f"{worker_id}-1"
    second_worker = f"{worker_id}-2"
    events = EventLog(events_path, truncate=True)
    cleanup_run(run, [first_worker, second_worker])
    register_worker(first_worker)
    seed_run(run, first_worker, state="running")

    pid: int | None = None
    process: dict[str, int] | None = None
    exit_evidence: ProcessEvidence | None = None
    try:
        process = start_guest_process(process_seconds)
        pid = process["pid"]
        first = issue_token(first_worker, run, pid, process["start_ticks"])
        if not first.get("won"):
            raise ProbeError(f"cannot issue the first token for {run}: {first}")
        token1 = str(first["new_token"])
        events.append(
            "attempt_started",
            run_id=run,
            worker_id=first_worker,
            token=token1,
            detail={
                "attempt": first["attempt"],
                "pid": pid,
                "start_ticks": process["start_ticks"],
            },
        )

        before = capability_call(
            token1, events=events, run_id=run, worker_id=first_worker, phase="before_outage"
        )
        if not before["allowed"]:
            raise ProbeError(f"the live attempt's token was rejected: {before}")

        last_heartbeat = worker_heartbeat(first_worker)
        events.append(
            "heartbeat_lost",
            run_id=run,
            worker_id=first_worker,
            token=token1,
            detail={
                "last_heartbeat_at": last_heartbeat,
                "cause": "simulated network partition: the supervised process stopped heartbeating",
            },
        )
        stale = declare_stale(first_worker, run)
        events.append(
            "worker_declared_stale",
            run_id=run,
            worker_id=first_worker,
            token=token1,
            detail={
                **stale,
                "source": "probe SQL mirroring internal/supervisor (park only, never restart)",
            },
        )

        blocked = request_attempt(run, worker_id=second_worker, start=True, events=events)
        blocked_counts = token_counts(run)
        if blocked["decision"] != "blocked" or "still running" not in blocked["reason"]:
            raise ProbeError(f"the guard did not block a live old process: {blocked}")
        if blocked_counts != {"total": 1, "active": 1, "superseded": 0}:
            raise ProbeError(f"a blocked attempt changed the token ledger: {blocked_counts}")
        if capability_status(token1) != "ok":
            raise ProbeError("a blocked attempt must not revoke the still-live attempt's token")

        kill_guest_process(pid)
        exit_evidence = wait_for_process_exit(pid, process["start_ticks"])
        if exit_evidence.verdict() == "still_running":
            raise ProbeError(f"guest process {pid} did not exit; refusing to call recovery safe")
        events.append(
            "old_process_exited",
            run_id=run,
            worker_id=first_worker,
            token=token1,
            detail=exit_evidence.as_dict(),
        )

        started = request_attempt(run, worker_id=second_worker, start=True, events=events)
        if started["decision"] != "started":
            raise ProbeError(f"recovery was not allowed after the old process exited: {started}")
        token2 = str(started["new_token"])

        old_call = capability_call(
            token1, events=events, run_id=run, worker_id=second_worker, phase="old_token_after_supersession"
        )
        new_call = capability_call(
            token2, events=events, run_id=run, worker_id=second_worker, phase="new_token"
        )
        unknown_token = f"unknown-{uuid.uuid4().hex}"
        unknown_call = capability_call(
            unknown_token, events=events, run_id=run, worker_id=second_worker, phase="unknown_token"
        )

        second_start = request_attempt(run, worker_id=second_worker, start=True, events=events)

        recorded = events.entries()
        events.append(
            "recovery_completed",
            run_id=run,
            worker_id=second_worker,
            token=token2,
            detail={
                "attempt_requests": sum(1 for entry in recorded if entry["event"] == "attempt_requested"),
                "blocked_attempts": sum(1 for entry in recorded if entry["event"] == "attempt_blocked"),
                "old_token": token_fingerprint(token1),
                "new_token": token_fingerprint(token2),
                "final_attempt": started["attempt"],
                "why_allowed": started["reason"],
                "second_start_refused": second_start["reason"],
            },
        )

        after_counts = token_counts(run)
        record = tokens_for_run(run)
        ordered = verify_event_log(events.entries())
        blocking_reason_recorded = any(
            entry["event"] == "attempt_blocked"
            and "still running" in str(entry.get("detail", {}).get("reason", ""))
            for entry in events.entries()
        )

        assertions = {
            "new_attempt_blocked_while_old_process_alive": blocked["decision"] == "blocked"
            and "still running" in blocked["reason"],
            "no_token_issued_while_blocked": blocked_counts == {"total": 1, "active": 1, "superseded": 0},
            "new_attempt_allowed_once_process_exited": started["decision"] == "started"
            and started["evidence"]["process"]["verdict"] in ("exited", "pid_reused"),
            "supersession_reported_a_single_winner": started["decision"] == "started",
            "old_token_rejected_after_supersession": old_call["result"] == "token_superseded",
            "new_token_accepted": new_call["result"] == "ok",
            "unknown_token_rejected": unknown_call["result"] == "token_unknown",
            "only_one_active_token_after_recovery": after_counts == {
                "total": 2,
                "active": 1,
                "superseded": 1,
            },
            "second_start_refused_after_new_attempt_began": second_start["decision"] == "blocked"
            and second_start["evidence"].get("run_state") == "running",
            "blocking_reason_recorded": blocking_reason_recorded,
            "events_time_ordered": ordered["ok"],
            "process_was_alive_when_first_attempt_was_requested": blocked["evidence"]["process"][
                "verdict"
            ]
            == "still_running",
        }

        report = {
            "probe": "p04-lease-loss",
            "task": "P04 / T013",
            "status": "passed" if all(assertions.values()) else "failed",
            "environment": {
                "python": sys.version.split()[0],
                "postgres_server": _server_version(),
                "guest": WSL_DISTRO,
            },
            "run_id": run,
            "worker_ids": [first_worker, second_worker],
            "process": {
                "pid": pid,
                "recorded_start_ticks": process["start_ticks"],
                "verdict_after_kill": exit_evidence.verdict() if exit_evidence else None,
            },
            "tokens": [token.as_dict() for token in record],
            "timeline": events.entries(),
            "event_log": _display_path(events_path),
            "event_order": ordered,
            "assertions": assertions,
        }
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report
    finally:
        if pid is not None and (exit_evidence is None or exit_evidence.verdict() == "still_running"):
            # Leave no stray process behind when a step raised; the scenario's
            # exit is re-checked by the caller's assertions.
            kill_guest_process(pid)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P04/T013 lease-loss probe")
    sub = parser.add_subparsers(dest="command", required=True)

    issue_cmd = sub.add_parser("issue", help="issue the first token for a run")
    issue_cmd.add_argument("--worker", required=True)
    issue_cmd.add_argument("--run", required=True)
    issue_cmd.add_argument("--pid", type=int, default=None)
    issue_cmd.add_argument("--start-ticks", type=int, default=None)

    call_cmd = sub.add_parser("call", help="make one capability call with a token")
    call_cmd.add_argument("--token", required=True)
    call_cmd.add_argument("--run", default=None, help="record the event against this run")
    call_cmd.add_argument("--worker", default=None)
    call_cmd.add_argument("--events", type=Path, default=None)

    attempt_cmd = sub.add_parser("attempt", help="request a new attempt")
    attempt_cmd.add_argument("--run", required=True)
    attempt_cmd.add_argument("--worker", default="probe-recovery-worker")
    attempt_cmd.add_argument("--pid", type=int, default=None)
    attempt_cmd.add_argument("--start-ticks", type=int, default=None)
    attempt_cmd.add_argument("--start", action="store_true", help="take the lease over if allowed")
    attempt_cmd.add_argument("--events", type=Path, default=None)

    simulate_cmd = sub.add_parser("simulate", help="drive outage + recovery end to end")
    simulate_cmd.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    simulate_cmd.add_argument("--out", type=Path, default=None)
    simulate_cmd.add_argument("--run", default=None)
    simulate_cmd.add_argument("--worker", default="probe-worker")
    simulate_cmd.add_argument("--seconds", type=int, default=120)
    simulate_cmd.add_argument("--no-migrate", action="store_true")

    events_cmd = sub.add_parser("events", help="inspect and order-check an event log")
    events_cmd.add_argument("--events", type=Path, default=DEFAULT_EVENTS)

    migrate_cmd = sub.add_parser("migrate", help="apply the migrations this probe needs")
    migrate_cmd.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "migrate":
        applied = apply_migrations()
        if not args.quiet:
            _emit({"applied": applied})
        return 0

    if args.command == "issue":
        outcome = issue_token(args.worker, args.run, args.pid, args.start_ticks)
        payload = dict(outcome)
        payload["token_fingerprint"] = token_fingerprint(outcome.get("new_token"))
        _emit(payload)
        return 0 if outcome.get("won") else 2

    if args.command == "call":
        event_log = None
        if args.events is not None:
            if not args.run:
                parser.error("--run is required with --events so the call can be recorded")
            event_log = EventLog(args.events)
        result = capability_call(
            args.token, events=event_log, run_id=args.run, worker_id=args.worker
        )
        _emit(result)
        return 0 if result["allowed"] else 2

    if args.command == "attempt":
        event_log = EventLog(args.events) if args.events is not None else None
        result = request_attempt(
            args.run,
            worker_id=args.worker,
            pid=args.pid,
            recorded_start_ticks=args.start_ticks,
            start=args.start,
            events=event_log,
        )
        _emit(result)
        return 0 if result["decision"] in ("allowed", "started") else 2

    if args.command == "simulate":
        report = run_simulation(
            events_path=args.events,
            run_id=args.run,
            worker_id=args.worker,
            process_seconds=args.seconds,
            out_path=args.out,
            migrate=not args.no_migrate,
        )
        _emit(report)
        return 0 if all(report["assertions"].values()) else 1

    if args.command == "events":
        log = EventLog(args.events)
        verification = verify_event_log(log.entries())
        _emit({"events": log.entries(), "verification": verification})
        return 0 if verification["ok"] else 1

    parser.error(f"unknown command {args.command!r}")
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProbeError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc
