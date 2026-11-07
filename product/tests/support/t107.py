"""F13B/T107 fault-injection helpers: evidence recorder and real process probes.

The four FAULT records (04/05/06/10) are *real-stack* facts. This module only
provides the machinery the test file needs to make those facts auditable:

* a small recorder that accumulates one JSON record per fault and writes
  ``product/reports/f13b/T107-faults.json`` only when ``F13B_WRITE_EVIDENCE=1``;
* Windows process probes (alive / creation time / suspend / resume) so the
  "old worker is really still alive" claim is backed by the OS, not by a flag;
* ``pg_stat_activity`` read models keyed by ``application_name`` so a real
  supervisor's database session can be pointed at independently;
* a DSN helper that labels that session without changing any product rule.

Nothing here is product code; no product path, migration or contract is touched.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import json
import os
import uuid
from ctypes import wintypes
from pathlib import Path

from support import stack as st

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = PRODUCT_ROOT / "reports" / "f13b" / "T107-faults.json"

# Windows constants used by the process probes.
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_SUSPEND_RESUME = 0x0800
_STILL_ACTIVE = 259
_FILETIME_EPOCH_OFFSET = 11644473600  # seconds between 1601-01-01 and 1970-01-01


# ---------------------------------------------------------------------------
# process truth (Windows host, real PIDs, no fake liveness)
# ---------------------------------------------------------------------------
def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _ntdll():
    return ctypes.WinDLL("ntdll")


def open_process(pid: int) -> int:
    handle = _kernel32().OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_SUSPEND_RESUME, False, int(pid)
    )
    if not handle:
        raise OSError(ctypes.get_last_error(), f"OpenProcess({pid}) failed")
    return handle


def process_alive(pid: int) -> bool:
    """True only when the OS says the process exists and has not exited.

    Opening the process and reading its exit code is the same evidence
    ``tasklist`` uses; a suspended process still reports ``STILL_ACTIVE``.
    """
    try:
        handle = open_process(pid)
    except OSError:
        return False
    try:
        code = wintypes.DWORD()
        if not _kernel32().GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == _STILL_ACTIVE
    finally:
        _kernel32().CloseHandle(handle)


def process_creation_epoch(pid: int) -> float:
    """The real process creation time (guards against PID reuse)."""
    handle = open_process(pid)
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not _kernel32().GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return ticks / 10_000_000 - _FILETIME_EPOCH_OFFSET
    finally:
        _kernel32().CloseHandle(handle)


def process_identity(pid: int) -> dict:
    """A small, auditable identity record for one live process."""
    return {
        "pid": int(pid),
        "alive": process_alive(pid),
        "creation_time_epoch": round(process_creation_epoch(pid), 6),
    }


def suspend_process(pid: int) -> dict:
    """Stop the process at the OS level (the real fault: heartbeat stalls)."""
    handle = open_process(pid)
    try:
        status = _ntdll().NtSuspendProcess(handle)
        if status != 0:
            raise OSError(f"NtSuspendProcess({pid}) returned NTSTATUS {status:#x}")
        return {"pid": int(pid), "operation": "NtSuspendProcess", "status": status}
    finally:
        _kernel32().CloseHandle(handle)


def resume_process(pid: int) -> dict:
    handle = open_process(pid)
    try:
        status = _ntdll().NtResumeProcess(handle)
        if status != 0:
            raise OSError(f"NtResumeProcess({pid}) returned NTSTATUS {status:#x}")
        return {"pid": int(pid), "operation": "NtResumeProcess", "status": status}
    finally:
        _kernel32().CloseHandle(handle)


def terminate_process(process, *, timeout: float = 30.0) -> dict:
    """Best-effort teardown that never hangs a real-stack test."""
    if process.poll() is None:
        process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except Exception:  # noqa: BLE001 - teardown must not mask the assertion
        process.kill()
        stdout, stderr = process.communicate(timeout=10)
    return {
        "returncode": process.returncode,
        "stdout_tail": (stdout or "")[-4000:],
        "stderr_tail": (stderr or "")[-4000:],
    }


# ---------------------------------------------------------------------------
# real PostgreSQL sessions of one labelled process
# ---------------------------------------------------------------------------
def dsn_with_application_name(dsn: str, name: str) -> str:
    """Label the supervisor's real database sessions (a libpq DSN parameter).

    A connection label is observability, not a product rule: no SQL, migration or
    contract changes, and the product's own DSN is still the only credential.
    """
    separator = "&" if "?" in dsn else "?"
    return f"{dsn}{separator}application_name={name}"


def database_sessions(dbname: str, application_name: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT pid, application_name, state, "
        "to_char(backend_start AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS backend_start, "
        "to_char(state_change AT TIME ZONE 'UTC', "
        "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS state_change "
        "FROM pg_stat_activity "
        f"WHERE application_name = {st.quote_literal(application_name)} "
        "ORDER BY pid",
    )


# ---------------------------------------------------------------------------
# evidence recorder
# ---------------------------------------------------------------------------
def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_run_token() -> str:
    return uuid.uuid4().hex[:10]


class Faults:
    """Accumulate per-fault evidence and write the JSON report on request.

    ``F13B_WRITE_EVIDENCE=1`` is the only trigger: a normal gate run must not
    rewrite the checked-in evidence file, exactly like the other F13B reports.
    """

    def __init__(self, *, branch: str | None = None, commit: str | None = None) -> None:
        self.records: list[dict] = []
        self.branch = branch
        self.commit = commit
        self.failures: list[dict] = []

    def record(
        self,
        fault_id: str,
        *,
        title: str,
        injection_point: str,
        inputs: dict,
        independent_truth: dict,
        expected: dict,
        observed: dict,
        verdict: str = "passed",
        commands: list[str] | None = None,
    ) -> dict:
        record = {
            "id": fault_id,
            "title": title,
            "verdict": verdict,
            "recorded_at": utc_now(),
            "injection_point": injection_point,
            "inputs": inputs,
            "independent_truth": independent_truth,
            "expected": expected,
            "observed": observed,
            "commands": list(commands or []),
        }
        self.records.append(record)
        return record

    def fail(self, fault_id: str, *, title: str, error: str) -> None:
        self.failures.append(
            {"id": fault_id, "title": title, "error": error, "recorded_at": utc_now()}
        )

    def document(self) -> dict:
        produced = {record["id"] for record in self.records}
        return {
            "ticket": "T107",
            "issue": 108,
            "title": "fault injection: old graph, live old worker, cancel race and event races",
            "status": "passed" if len(self.records) == 4 and not self.failures else "partial",
            "branch": self.branch,
            "commit": self.commit,
            "generated_at": utc_now(),
            "environment": {
                "profile": "dev-stub",
                "model": "stub only; no real vendor credential or endpoint",
                "database": "isolated PostgreSQL 16.4 cluster at 127.0.0.1:55432",
                "object_store": "real filesystem object root behind the product API",
                "sandbox": "WSL2 Ubuntu-24.04 + runsc (real supervisor exec where used)",
            },
            "faults": sorted(self.records, key=lambda record: record["id"]),
            "failures": self.failures,
            "not_covered": [
                "real vendor model quality/latency/cost",
                "FAULT-04 recovery against a *running* production deployment (this is a throwaway stack)",
                "FAULT-05 host/network partition with an unreachable host (this suspends the real process instead)",
            ],
            "produced_faults": sorted(produced),
        }

    def write_if_enabled(self) -> Path | None:
        if os.environ.get("F13B_WRITE_EVIDENCE") != "1":
            return None
        EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_PATH.write_text(
            json.dumps(self.document(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return EVIDENCE_PATH


def git_branch_and_commit() -> tuple[str | None, str | None]:
    import subprocess

    try:
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PRODUCT_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        return branch or None, commit or None
    except Exception:  # noqa: BLE001 - evidence only
        return None, None
