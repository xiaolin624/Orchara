"""T106 / F13B fault evidence support: records, processes and database truth.

This module is deliberately small and explicit. It exists so the three
EVALUATION.md §4 faults (FAULT-01 approval restart, FAULT-02 changed params,
FAULT-03 lost receipt) can be recorded as machine-readable evidence with the four
things the brief requires for each fault:

* the **injection point** (which process / product line / connection was hit),
* the **input** (params, seed and an independently recomputed hash),
* the **expected** pre-registered outcome versus the **observed** one,
* the **independent ledger** (the loopback endpoint's own request log, PostgreSQL
  rows, and the OS process table) plus the commands that produced it.

The evidence JSON is written only when ``F13B_WRITE_EVIDENCE=1`` (the T101
convention), so an ordinary pytest run never rewrites a committed evidence file.
Every string is passed through ``tools/evidence_redaction``; the writer refuses
to persist a document that still contains a credential-shaped value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
for _entry in (str(TOOLS_ROOT), str(TESTS_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from evidence_redaction import find_violations, redact_value  # noqa: E402
from support import stack as st  # noqa: E402

#: Mirrors the T101 evidence convention: only an explicit evidence run rewrites.
WRITE_EVIDENCE_ENV = "F13B_WRITE_EVIDENCE"
EVIDENCE_JSON = PRODUCT_ROOT / "reports" / "f13b" / "T106-faults.json"

WORKER_SCRIPT = Path(__file__).with_name("t106_worker.py")

ENVIRONMENT = {
    "profile": "dev-stub (ORCHARA_PROFILE=dev-stub; no vendor credentials or endpoint)",
    "postgres": "PostgreSQL 16.4 at 127.0.0.1:55432; one throwaway database per stack",
    "api": "compiled cmd/api: real mTLS internal listener, real object store, HMAC signed links",
    "supervisor": "compiled cmd/supervisor exec --once as a real one-shot OS process",
    "worker": "real OS worker processes (tests/f13b/t106_worker.py) over the real mTLS protocol",
    "connector": "real loopback HTTP endpoint owned by the test; its request log is the independent ledger",
    "sandbox": "WSL2/runsc not used by this ticket (no graph node executes)",
    "not_run": [
        "a real vendor connector endpoint (loopback stands in for the deployment-defined endpoint)",
        "a real vendor model (dev-stub profile; no credentials/endpoint on this host)",
        "a real browser DOM",
        "a resident supervisor daemon (the production one-shot `exec --once` shape is used)",
    ],
}


# ---------------------------------------------------------------------------
# independent input hashing
# ---------------------------------------------------------------------------
def canonical_params(params: dict) -> tuple[bytes, str]:
    """The canonical params bytes and their sha256, recomputed in Python.

    The product hashes RFC-8785 JCS bytes (``internal/capabilities/params.go``).
    For this ticket's params every scalar is a string or an integer, where JCS and
    ``json.dumps(sort_keys=True, separators=(",", ":"))`` agree byte for byte, so
    the test can assert the product's ``params_sha256`` against an independent
    value instead of echoing it back.
    """
    canonical = json.dumps(
        params, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return canonical, hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# OS process truth
# ---------------------------------------------------------------------------
def process_present(pid: int) -> dict:
    """Ask the OS process table whether ``pid`` is still running."""
    command = ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        stdin=subprocess.DEVNULL,
    )
    output = (completed.stdout or "").strip()
    rows: list[list[str]] = []
    if output and "INFO:" not in output.upper():
        rows = [row for row in csv.reader(io.StringIO(output)) if row]
    present = any(len(row) > 1 and row[1].strip() == str(int(pid)) for row in rows)
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "present": present,
        "rows": rows,
    }


def os_start_time(pid: int) -> str | None:
    """The OS creation time of ``pid`` (best effort, never a test dependency)."""
    script = (
        f"(Get-Process -Id {int(pid)} -ErrorAction Stop).StartTime"
        ".ToUniversalTime().ToString('o')"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - host quirk
        return None
    value = (completed.stdout or "").strip()
    return value or None


def kill_process_tree(pid: int) -> dict:
    """Terminate one real process and its whole tree (the psql hold session)."""
    command = ["taskkill", "/F", "/T", "/PID", str(int(pid))]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        stdin=subprocess.DEVNULL,
    )
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def application_sessions(dbname: str, application_name: str) -> list[dict]:
    """Real PostgreSQL backends carrying this test's ``application_name``."""
    return st.sql_rows(
        dbname,
        "SELECT pid, application_name, state, backend_start::text AS backend_start, "
        "query FROM pg_stat_activity WHERE application_name = "
        + st.quote_literal(application_name)
        + " ORDER BY pid",
    )


def wait_application_sessions(
    dbname: str, application_name: str, *, count: int, timeout: float = 30.0
) -> list[dict]:
    """Wait until exactly ``count`` sessions of that name exist (or time out)."""
    deadline = time.time() + timeout
    latest: list[dict] = []
    while time.time() < deadline:
        latest = application_sessions(dbname, application_name)
        if len(latest) == count:
            return latest
        time.sleep(0.1)
    return latest


# ---------------------------------------------------------------------------
# the worker OS process
# ---------------------------------------------------------------------------
class WorkerProcess:
    """One real worker OS process executing ``tests/f13b/t106_worker.py``.

    The worker performs the real mTLS claim / capability / waiting / exit calls
    and writes its facts to a JSON file, so the injected kill is an OS-level event
    on a genuine process, never a mocked "restart" flag.
    """

    def __init__(
        self,
        mode: str,
        spec: dict,
        *,
        facts_path: Path,
        log_path: Path,
    ) -> None:
        assert WORKER_SCRIPT.exists(), WORKER_SCRIPT
        self.mode = mode
        self.spec = dict(spec)
        self.spec["facts_path"] = str(facts_path)
        self.facts_path = Path(facts_path)
        self.log_path = Path(log_path)
        self.spec_path = self.facts_path.with_name(self.facts_path.stem + ".spec.json")
        self.spec_path.parent.mkdir(parents=True, exist_ok=True)
        self.spec_path.write_text(
            json.dumps(self.spec, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self._log_handle = self.log_path.open("w", encoding="utf-8")
        self.started_at = time.time()
        self._process = subprocess.Popen(
            [sys.executable, str(WORKER_SCRIPT), mode, str(self.spec_path)],
            cwd=str(PRODUCT_ROOT),
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        self.start_time_os = os_start_time(self._process.pid)

    @property
    def pid(self) -> int:
        return self._process.pid

    def poll(self):
        return self._process.poll()

    def wait(self, *, timeout: float = 60.0) -> int:
        return self._process.wait(timeout=timeout)

    def log_tail(self, lines: int = 20) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover - log vanished
            return ""
        return "\n".join(text.splitlines()[-lines:])

    def facts(self) -> dict:
        try:
            return json.loads(self.facts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def wait_facts(
        self, *, timeout: float = 180.0, require: tuple[str, ...] = ()
    ) -> dict:
        """Wait for the worker's durable facts; fail early if the process dies."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.facts_path.exists():
                try:
                    document = json.loads(self.facts_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    document = {}
                if all(document.get(key) for key in require):
                    return document
            if self._process.poll() is not None:
                raise AssertionError(
                    f"worker {self.mode} exited before writing facts "
                    f"(exit={self._process.returncode}); log tail:\n{self.log_tail()}"
                )
            time.sleep(0.05)
        raise AssertionError(
            f"worker {self.mode} wrote no facts within {timeout}s; "
            f"facts={self.facts()}; log tail:\n{self.log_tail()}"
        )

    def kill(self) -> dict:
        """Kill the real process tree and return the process-table evidence."""
        taskkill = kill_process_tree(self._process.pid)
        exit_code: int | None = None
        try:
            exit_code = self._process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - stubborn process
            self._process.kill()
            exit_code = self._process.wait(timeout=30)
        return {
            "taskkill": taskkill,
            "exit_code": exit_code,
            "exited_at": time.time(),
            "present_after": process_present(self._process.pid),
        }

    def close(self) -> None:
        if self._process.poll() is None:
            try:
                kill_process_tree(self._process.pid)
                self._process.wait(timeout=30)
            except (OSError, subprocess.SubprocessError):  # pragma: no cover
                self._process.kill()
        if not self._log_handle.closed:
            self._log_handle.close()


# ---------------------------------------------------------------------------
# fault records
# ---------------------------------------------------------------------------
def _short(value, limit: int = 600) -> str:
    redacted = redact_value(value)
    text = (
        redacted
        if isinstance(redacted, str)
        else json.dumps(redacted, ensure_ascii=False, default=str)
    )
    return text if len(text) <= limit else text[: limit - 3] + "..."


@dataclass
class Fault:
    """One injected fault and its evidence (expected vs. observed + ledger)."""

    fault_id: str
    title: str
    injection_point: str
    expected: list[str] = field(default_factory=list)
    status: str = "not_run"
    input: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    independent_ledger: dict = field(default_factory=dict)
    assertions: list[dict] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    not_run: list[str] = field(default_factory=list)
    failure: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    # -- recording ----------------------------------------------------------
    def check(self, name: str, condition, detail=None) -> None:
        ok = bool(condition)
        self.assertions.append(
            {"name": name, "ok": ok, "detail": None if detail is None else _short(detail)}
        )
        assert ok, f"{self.fault_id}: {name} failed: {detail}"

    def observe(self, key: str, value) -> None:
        self.observed[key] = value

    def ledger(self, key: str, value) -> None:
        self.independent_ledger[key] = value

    def command(self, text: str) -> None:
        self.commands.append(text)

    def block(self, reason: str) -> None:
        self.status = "blocked"
        self.failure = reason

    def mark_passed(self) -> None:
        if not self.assertions:
            raise AssertionError(
                f"{self.fault_id}: cannot be passed without at least one assertion"
            )
        if self.status in {"blocked", "failed"}:
            return
        self.status = "passed"

    # -- evidence -----------------------------------------------------------
    def record(self) -> dict:
        return redact_value(
            {
                "fault_id": self.fault_id,
                "title": self.title,
                "status": self.status,
                "verdict": self.status,
                "injection_point": self.injection_point,
                "input": self.input,
                "expected": self.expected,
                "observed": self.observed,
                "independent_ledger": self.independent_ledger,
                "assertions": self.assertions,
                "assertion_count": len(self.assertions),
                "commands": self.commands,
                "not_run": self.not_run,
                "duration_s": self.duration_s,
                "failure": self.failure,
            }
        )

    def persist(self) -> None:
        self.duration_s = round(time.time() - self.started_at, 2)
        _FAULTS[self.fault_id] = self
        if os.environ.get(WRITE_EVIDENCE_ENV) != "1":
            return
        existing: dict[str, dict] = {}
        if EVIDENCE_JSON.exists():
            try:
                payload = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
                for item in payload.get("faults", []):
                    existing[item["fault_id"]] = item
            except (json.JSONDecodeError, KeyError):  # pragma: no cover - corrupt file
                existing = {}
        existing[self.fault_id] = self.record()
        ordered = [existing[key] for key in sorted(existing)]
        document = {
            "ticket": "T106",
            "issue": 107,
            "title": "Fault injection: approval restart, changed params and lost receipt",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "product_commit": _git_commit(),
            "product_commit_note": (
                "HEAD checked out when this evidence run started; the ticket's test "
                "sources are committed on the same branch"
            ),
            "environment": ENVIRONMENT,
            "faults": ordered,
        }
        text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
        leaks = find_violations(text)
        if leaks:  # pragma: no cover - a credential leak must fail the evidence run
            raise AssertionError(f"credential-shaped values in evidence: {leaks}")
        EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_JSON.write_text(text, encoding="utf-8")

    def __enter__(self) -> "Fault":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc is not None and self.status not in {"blocked", "failed"}:
            self.status = "failed"
            if self.failure is None:
                self.failure = f"{exc_type.__name__}: {exc}"
            if os.environ.get("F13B_VERBOSE_FAILURES") == "1":
                self.observed["traceback"] = traceback.format_exc(limit=8)
        self.persist()
        return False


_FAULTS: dict[str, Fault] = {}


def start(
    fault_id: str,
    title: str,
    *,
    injection_point: str,
    expected: list[str] | None = None,
) -> Fault:
    """Create and register one fault record (pre-registering its expectations)."""
    fault = Fault(
        fault_id=fault_id,
        title=title,
        injection_point=injection_point,
        expected=list(expected or []),
        not_run=list(ENVIRONMENT["not_run"]),
    )
    _FAULTS[fault_id] = fault
    return fault


def records() -> dict[str, dict]:
    return {key: value.record() for key, value in _FAULTS.items()}


def _git_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(PRODUCT_ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None
