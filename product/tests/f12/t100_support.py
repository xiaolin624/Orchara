"""T100 (#101) journey support: one real stack per module + machine-readable evidence.

Every journey driven from this package uses the product's real entry points: the
compiled ``cmd/api`` over a real HTTP socket, the real internal mTLS listener
(worker / supervisor / maintenance client certificates), the compiled
``cmd/maintenance`` and ``cmd/supervisor`` binaries, real PostgreSQL and the real
filesystem object store. The database and the object directory are read **only to
assert independent truth**; the only SQL writes are:

* the two disclosed, clock-only expiry boundaries (activation token deadline and
  background-job lease deadline), which move a server-side deadline into the past
  instead of waiting for it, and
* the documented deployment seeds (preset tool version + execution-slot
  capacity) that a dev deployment already carries before any user journey.

This module deliberately does not reuse the T093 package fixture ``f12_stack``:
that fixture runs the conformance gate, which creates a Run and claims a live
lease, so every journey would read a database the gate has already mutated. It
also does not touch ``tests/f12/conftest.py`` (T093's session fixtures), per the
T100/T101 file split.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent

for entry in (TESTS_ROOT, PRODUCT_ROOT / "worker", PRODUCT_ROOT / "tools"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from support.runtime_stack import Stack
from support import f00b, stack as st, t024, t029, t033  # noqa: E402
from support.certs import make_stack_certificates, unique_internal_addr  # noqa: E402
from evidence_redaction import redact_value  # noqa: E402

EVIDENCE_DIR = PRODUCT_ROOT / "reports" / "f12"
EVIDENCE_JSON = EVIDENCE_DIR / "T100-journeys.json"
WRITE_EVIDENCE_ENV = "F12_WRITE_EVIDENCE"
SANDBOX_DISTRO = t033.SANDBOX_DISTRO
SANDBOX_ROOTFS = t033.SANDBOX_ROOTFS


# ---------------------------------------------------------------------------
# one real stack, one migration chain, one bootstrap
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def t100_stack(
    tmp_path_factory: pytest.TempPathFactory, go_binaries: dict[str, Path]
) -> Stack:
    """A migrated + bootstrapped database behind one real compiled API process.

    The build is identical in shape to ``tests/support/fixtures.py:t033_stack``
    (real ``cmd/admin migrate``, real ``cmd/admin bootstrap``, real mTLS
    certificates, real signing key, real object directory, compiled binaries), but
    it is module-scoped because each T100 journey is one expensive scenario.
    """
    tmp_path = tmp_path_factory.mktemp("t100-stack")
    try:
        database = st.create_database("orchara_t100_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    dsn, dbname = database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    # Keep the node-approval deadline sweep away from journeys that park nothing
    # and would only see nondeterminism from it; the execution lease is generous
    # because the sandbox lives in the shared WSL2 host (same reasoning as
    # tests/f13a/conftest.py:87 and tests/f12/test_t096_stack.py).
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    env["ORCHARA_EXECUTION_LEASE_TTL"] = "300s"
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=st.WEB_DIR,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / "api.log",
    )
    stack = Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    try:
        f00b.wait_for_internal(stack)
    except Exception:
        server.stop()
        st.drop_database(dbname)
        raise
    try:
        yield stack
    finally:
        server.stop()
        st.drop_database(dbname)


# ---------------------------------------------------------------------------
# environment profile (recorded once in the evidence file)
# ---------------------------------------------------------------------------
def _run(command: list[str], *, timeout: float = 60.0) -> str:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        return f"unavailable: {exc}"
    text = (completed.stdout or completed.stderr or "").strip()
    return text.splitlines()[0] if text else f"exit {completed.returncode}"


def sandbox_profile() -> dict:
    version = _run(["wsl", "-d", SANDBOX_DISTRO, "-u", "root", "-e", "runsc", "--version"])
    digest = _run(
        ["wsl", "-d", SANDBOX_DISTRO, "-u", "root", "-e", "bash", "-lc",
         f"cat {SANDBOX_ROOTFS}.digest"]
    )
    return {
        "available": "runsc version" in version,
        "runsc": version,
        "distro": SANDBOX_DISTRO,
        "rootfs": SANDBOX_ROOTFS,
        "rootfs_digest": digest,
    }


def environment_facts(stack: Stack) -> dict:
    """The environment profile every journey evidence record is stamped with."""
    migrations = st.migration_chain(stack.dbname)
    head = migrations[max(migrations)] if migrations else ""
    return {
        "profile": "ORCHARA_PROFILE=dev-stub (no vendor credentials, endpoints or GPU)",
        "python": sys.version.split()[0],
        "pytest": pytest.__version__,
        "go": _run([str(st.GO), "version"]),
        "node": _run(["node", "--version"]),
        "postgres": st.sql(stack.dbname, "SHOW server_version"),
        "database": stack.dbname,
        "migrations": {"count": len(migrations), "head": head},
        "git_head": _run(["git", "-C", str(st.REPO_ROOT), "rev-parse", "HEAD"]),
        "contract_sha256": hashlib.sha256(
            (PRODUCT_ROOT / "contracts" / "openapi.json").read_bytes()
        ).hexdigest(),
        "platform": platform.platform(),
        "sandbox": sandbox_profile(),
        "network": "loopback only; real HTTP (external) + real mTLS (internal)",
    }


# ---------------------------------------------------------------------------
# evidence recorder
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class JourneyRecord:
    journey_id: str
    title: str
    status: str = "not_run"
    entry_commands: list[str] = field(default_factory=list)
    entry_http: list[str] = field(default_factory=list)
    truth_sources: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    variants: list[dict] = field(default_factory=list)
    failure_reason: str | None = None
    started_at: str = field(default_factory=_utcnow)
    duration_seconds: float = 0.0


class JourneyHandle:
    """Context manager for one journey: a normal exit never invents ``passed``."""

    def __init__(self, log: "JourneyLog", record: JourneyRecord) -> None:
        self._log = log
        self.record = record
        self._started = time.monotonic()

    def __enter__(self) -> "JourneyHandle":
        return self

    def __exit__(self, exc_type, exc, _traceback) -> bool:
        self.record.duration_seconds = round(time.monotonic() - self._started, 3)
        if exc is None:
            # Explicitly marked by the journey body (mark_passed) or left as
            # not_run. There is no implicit promotion here: a body that records
            # no assertion must not become a passed journey.
            pass
        elif exc_type is not None and exc_type.__name__ in {"Skipped", "XFailed"}:
            self.record.status = "blocked"
            self.record.failure_reason = f"environment/declared boundary: {exc}"
        else:
            self.record.status = "failed"
            self.record.failure_reason = f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"
        self._log.write()
        return False  # never swallow the failure

    def mark_passed(self) -> None:
        """Set ``passed`` explicitly, only after the journey body asserted something."""
        if not self.record.assertions:
            raise AssertionError(
                f"{self.record.journey_id}: cannot mark passed without at least "
                "one recorded assertion"
            )
        if self.record.status in {"blocked", "failed"}:
            return
        self.record.status = "passed"

    def validate(self) -> None:
        if self.record.status == "passed" and not self.record.assertions:
            raise AssertionError(
                f"{self.record.journey_id}: status='passed' requires at least one "
                f"recorded assertion (assertions={len(self.record.assertions)})"
            )

    def assertion(self, text: str) -> None:
        """One independently checked assertion, in the language of the journey."""
        self.record.assertions.append(text)

    def note(self, text: str) -> None:
        self.record.notes.append(text)

    def fact(self, key: str, value) -> None:
        self.record.facts[key] = value

    def variant(self, variant_id: str, status: str, evidence: str) -> None:
        self.record.variants.append(
            {"variant_id": variant_id, "status": status, "evidence": evidence}
        )


class JourneyLog:
    def __init__(self) -> None:
        self._records: dict[str, JourneyRecord] = {}
        self._gaps: list[dict] = []
        self._environment: dict | None = None
        self._created_at = _utcnow()

    def set_environment(self, facts: dict) -> None:
        if self._environment is None:
            self._environment = facts

    def journey(
        self,
        journey_id: str,
        *,
        title: str,
        entry_commands: list[str],
        entry_http: list[str],
        truth_sources: list[str],
    ) -> JourneyHandle:
        record = JourneyRecord(
            journey_id=journey_id,
            title=title,
            entry_commands=list(entry_commands),
            entry_http=list(entry_http),
            truth_sources=list(truth_sources),
        )
        self._records[journey_id] = record
        return JourneyHandle(self, record)

    def known_gap(
        self,
        gap_id: str,
        *,
        title: str,
        status: str,
        entry_http: list[str],
        entry_commands: list[str],
        truth_sources: list[str],
        observed: dict,
        reason: str,
    ) -> None:
        """Record a known product gap (blocked capability), with real evidence."""
        self._gaps.append(
            {
                "gap_id": gap_id,
                "title": title,
                "status": status,
                "entry_http": list(entry_http),
                "entry_commands": list(entry_commands),
                "truth_sources": list(truth_sources),
                "observed": observed,
                "reason": reason,
                "recorded_at": _utcnow(),
            }
        )
        self.write()

    def records(self) -> list[dict]:
        """The redacted, validated machine-readable records (used by tests too)."""
        records = []
        for record in sorted(self._records.values(), key=lambda item: item.journey_id):
            if record.status == "passed" and not record.assertions:
                raise AssertionError(
                    f"{record.journey_id}: status='passed' requires at least one "
                    f"recorded assertion (assertions={len(record.assertions)})"
                )
            records.append(redact_value(asdict(record)))
        return records

    def write(self) -> None:
        if os.environ.get(WRITE_EVIDENCE_ENV) != "1":
            return
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        payload = redact_value(
            {
                "ticket": "T100",
                "issue": 101,
                "title": "复现账号到发布和后台执行的关键旅程",
                "created_at": self._created_at,
                "generated_at": _utcnow(),
                "environment": self._environment or {},
                "journeys": self.records(),
                "known_gaps": self._gaps,
            }
        )
        temporary = EVIDENCE_JSON.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        temporary.replace(EVIDENCE_JSON)


_LOG: JourneyLog | None = None


def journey_log() -> JourneyLog:
    global _LOG
    if _LOG is None:
        _LOG = JourneyLog()
    return _LOG


# ---------------------------------------------------------------------------
# independent truth: sealed objects read straight from the object directory
# ---------------------------------------------------------------------------
def sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def sealed_bytes(stack: Stack, artifact_id: str) -> bytes:
    """Read one artifact's exact bytes from the object directory (not via HTTP)."""
    row = t029.artifact_row(stack.dbname, artifact_id)
    content = (stack.object_dir / row["object_key"]).read_bytes()
    assert sha256_hex(content) == row["sha256"], "the sealed bytes do not match the row hash"
    return content


def utc_epoch(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
