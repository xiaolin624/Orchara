#!/usr/bin/env python3
"""P04/T016: only resume a run once the old process, sandbox and writes drained.

The gate answers one question: may this recovery attempt start now? It looks at
three independent evidence sources, because any one of them alone is misleading:

* **process** — ``/proc/<pid>/stat`` field 22 (start ticks) is recorded when the
  run starts. Comparing it later distinguishes "still running", "gone", and
  "the PID now belongs to something else". Treating a reused PID as "still
  running" would block recovery forever; killing it would kill an innocent
  process, so it is reported as *reused*, not as the old process.
* **sandbox** — a gVisor sandbox can outlive its client, so ``runsc list`` must
  be empty for that sandbox id.
* **database** — in-flight write markers and idle-in-transaction sessions must
  be gone; an exited process can leave writes in flight.

It never restarts anything: it only decides whether the *next* attempt is
allowed, and explains why not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PRODUCT_ROOT / "tools"))
import pg_dev  # noqa: E402

WSL_DISTRO = "Ubuntu-24.04"
ProcessVerdict = Literal["exited", "still_running", "pid_reused", "unknown"]


# --------------------------------------------------------------------------
# evidence
# --------------------------------------------------------------------------
def _run_wsl(command: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["wsl", "-d", WSL_DISTRO, "-u", "root", "-e", "bash", "-lc", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )


def read_start_ticks(pid: int) -> int | None:
    """Return /proc/<pid>/stat field 22, or None when the process is gone."""
    if pid <= 0:
        return None
    completed = _run_wsl(f"cat /proc/{pid}/stat 2>/dev/null || true")
    text = completed.stdout.strip()
    if not text:
        return None
    # The comm field may contain spaces and is wrapped in parentheses; the
    # numeric fields start after the last ')'.
    after_comm = text.rsplit(")", 1)[-1].split()
    # field 3 is the first token after comm, so field 22 is index 19
    try:
        return int(after_comm[19])
    except (IndexError, ValueError):
        return None


@dataclass(frozen=True)
class ProcessEvidence:
    pid: int
    recorded_start_ticks: int
    observed_start_ticks: int | None

    def verdict(self) -> ProcessVerdict:
        if self.observed_start_ticks is None:
            return "exited"
        if self.observed_start_ticks == self.recorded_start_ticks:
            return "still_running"
        # Same number, different process: the old one is gone and this one must
        # not be treated as it.
        return "pid_reused"


@dataclass(frozen=True)
class SandboxEvidence:
    sandbox_id: str
    present: bool


@dataclass(frozen=True)
class DbEvidence:
    run_id: str
    pending_writes: int
    idle_in_transaction: int

    def drained(self) -> bool:
        return self.pending_writes == 0 and self.idle_in_transaction == 0


@dataclass(frozen=True)
class Decision:
    decision: Literal["resume", "keep_pending"]
    reasons: list[str]
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": self.reasons,
            "evidence": self.evidence,
        }


def decide(process: ProcessEvidence, sandbox: SandboxEvidence, db: DbEvidence) -> Decision:
    """Pure gate: resume only when all three sources are clear."""
    reasons: list[str] = []
    verdict = process.verdict()

    if verdict == "still_running":
        reasons.append(f"process {process.pid} is still running")
    # 'pid_reused' is not a blocker: the old process is gone. It is surfaced in
    # the evidence so an operator can see what happened.
    if sandbox.present:
        reasons.append(f"sandbox {sandbox.sandbox_id} still exists")
    if db.pending_writes:
        reasons.append(f"{db.pending_writes} database write(s) still in flight")
    if db.idle_in_transaction:
        reasons.append(f"{db.idle_in_transaction} session(s) idle in transaction")

    evidence = {
        "process": {
            "pid": process.pid,
            "verdict": verdict,
            "recorded_start_ticks": process.recorded_start_ticks,
            "observed_start_ticks": process.observed_start_ticks,
        },
        "sandbox": {"id": sandbox.sandbox_id, "present": sandbox.present},
        "database": {
            "run_id": db.run_id,
            "pending_writes": db.pending_writes,
            "idle_in_transaction": db.idle_in_transaction,
        },
    }
    return Decision(
        decision="keep_pending" if reasons else "resume",
        reasons=reasons,
        evidence=evidence,
    )


# --------------------------------------------------------------------------
# live collectors
# --------------------------------------------------------------------------
def collect_sandbox(sandbox_id: str) -> SandboxEvidence:
    completed = _run_wsl("runsc --network=none list --quiet 2>/dev/null | sed '/^[[:space:]]*$/d'")
    present = sandbox_id in completed.stdout.split()
    return SandboxEvidence(sandbox_id=sandbox_id, present=present)


def collect_db(run_id: str) -> DbEvidence:
    """Read the write-drain evidence. SQL values are quoted, never interpolated raw."""

    def scalar(sql: str) -> int:
        return int(pg_dev.run_sql(sql).strip() or 0)

    pending = scalar(
        f"SELECT count(*) FROM app.pending_writes WHERE run_id = {pg_dev.quote_literal(run_id)}"
    )
    idle = scalar(
        "SELECT count(*) FROM pg_stat_activity "
        "WHERE state = 'idle in transaction' AND usename LIKE 'orchara%'"
    )
    return DbEvidence(run_id=run_id, pending_writes=pending, idle_in_transaction=idle)


def check(pid: int, recorded_start_ticks: int, sandbox_id: str, run_id: str) -> Decision:
    process = ProcessEvidence(
        pid=pid,
        recorded_start_ticks=recorded_start_ticks,
        observed_start_ticks=read_start_ticks(pid),
    )
    return decide(process, collect_sandbox(sandbox_id), collect_db(run_id))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P04 drain gate")
    sub = parser.add_subparsers(dest="command", required=True)

    check_cmd = sub.add_parser("check")
    check_cmd.add_argument("--pid", type=int, required=True)
    check_cmd.add_argument("--start-ticks", type=int, required=True)
    check_cmd.add_argument("--sandbox", required=True)
    check_cmd.add_argument("--run", required=True)

    start_cmd = sub.add_parser("start-process")
    start_cmd.add_argument("--seconds", type=int, default=120)

    args = parser.parse_args(argv)

    if args.command == "start-process":
        # `nohup setsid` matters: a plain background job dies with the shell that
        # launched it, which would make "still running" untestable.
        script = (
            f"nohup setsid sleep {args.seconds} >/dev/null 2>&1 & "
            "pid=$!; sleep 0.4; echo $pid; "
            "awk '{print $22}' /proc/$pid/stat 2>/dev/null || true"
        )
        completed = _run_wsl(script)
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        payload = {"pid": int(lines[0]), "start_ticks": int(lines[1]) if len(lines) > 1 else None}
        print(json.dumps(payload))
        return 0

    decision = check(args.pid, args.start_ticks, args.sandbox, args.run)
    print(json.dumps(decision.as_dict(), ensure_ascii=False, indent=2))
    return 0 if decision.decision == "resume" else 2


if __name__ == "__main__":
    raise SystemExit(main())
