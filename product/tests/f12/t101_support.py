"""T101 (GitHub #102) support: one real stack and machine-readable journey records.

Every journey in this ticket runs against the same shape the rest of the F-series
uses: a throwaway PostgreSQL 16 database migrated by the real ``cmd/admin`` chain,
the compiled ``cmd/api`` on a loopback socket, the real internal mTLS listener
with per-role certificates, the real object store, and the real ``cmd/supervisor``
/ ``cmd/maintenance`` binaries for execution and background work.

The journey record is the ticket's evidence contract:
``journey_id / status / 鐪熷疄鍏ュ彛 / 鐢ㄦ椂 / 鐙珛鐪熷€兼潵婧?/ 鏂█鐐?/ 澶辫触鍘熷洜``.
It is persisted only when ``T101_WRITE_EVIDENCE=1`` so ordinary pytest runs do not
rewrite the committed report; the evidence run is the one that does.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from support import f00b, t024, t029, t033, t048
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

EVIDENCE_JSON = st.PRODUCT_ROOT / "reports" / "f12" / "T101-journeys.json"

# The deployment-defined connector secret reference resolved by the API process.
# It is a local test value, never a credential: the connector endpoint is the
# loopback server the approval journey starts.
CONNECTOR_SECRET_VALUE = "t101-connector-secret-5c2e8a"

# Journey ids are stable; the report references them.
J1_REQUEST = "J1-request-to-run-result"
J1_NATURAL = "J1b-natural-language-request"
J2_APPROVAL = "J2-write-approval"
J3_MEMORY = "J3-personal-memory"
J4_KNOWLEDGE = "J4-knowledge-ingestion"
J5_SERVICE_ACCOUNT = "J5-service-account"

_RECORDS: dict[str, "Journey"] = {}


def build_stack(
    tmp_path: Path,
    go_binaries: dict[str, Path],
    *,
    label: str,
    extra_env: dict[str, str] | None = None,
):
    """Build one migrated + bootstrapped database behind one real API process.

    Returns ``(stack, server, dbname)`` so the module fixture can tear both down.
    The approval sweep is kept at the production 5m cadence (the decision-path
    expiry in this ticket is observed through the real decision call, not a
    background sweeper) and the execution lease gets the same 300s margin T068 /
    T084 use under a shared runsc host.
    """
    try:
        dsn, dbname = st.create_database("orchara_t101_")
    except RuntimeError as exc:  # pragma: no cover - environment missing
        pytest.skip(f"isolated development cluster is not running: {exc}")
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env["ORCHARA_APPROVAL_SWEEP_INTERVAL"] = "5m"
    env["ORCHARA_EXECUTION_LEASE_TTL"] = "300s"
    env[t048.TEST_SECRET_ENV] = CONNECTOR_SECRET_VALUE
    if extra_env:
        env.update(extra_env)
    server = st.start_api(
        go_binaries["api"],
        dsn,
        web_dir=st.WEB_DIR,
        profile="dev-stub",
        extra_env=env,
        log_path=tmp_path / f"t101-{label}-api.log",
    )
    stack = t033.T033Stack(
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
    except BaseException:
        server.stop()
        st.drop_database(dbname)
        raise
    return stack, server, dbname


def drop_stack(server, dbname: str) -> None:
    server.stop()
    st.drop_database(dbname)


# The golden draft's fixed application id can exist once per database, so every
# journey on the shared module stack reuses one real creator.
_GOLDEN_OWNERS: dict[tuple, object] = {}


def golden_owner(stack):
    """A real creator who owns the golden draft, shared by the module's journeys."""
    key = (id(stack), stack.dbname)
    if key not in _GOLDEN_OWNERS:
        from support import t041

        _GOLDEN_OWNERS[key] = t041.owner(stack, login="t101.golden")
    return _GOLDEN_OWNERS[key]


# ---------------------------------------------------------------------------
# journey records
# ---------------------------------------------------------------------------
@dataclass
class Journey:
    """One journey's evidence: real entry points, truth sources, assertions."""

    journey_id: str
    title: str
    status: str = "not_run"
    entry: list[str] = field(default_factory=list)
    truth: list[str] = field(default_factory=list)
    assertions: list[dict] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    failure: str | None = None
    started_at: float = field(default_factory=time.time)
    duration_s: float = 0.0

    # -- recording helpers --------------------------------------------------
    def http(self, method: str, path: str, response=None) -> None:
        status = getattr(response, "status", None)
        suffix = f" -> {status}" if status is not None else ""
        self.entry.append(f"{method} {path}{suffix}")

    def command(self, text: str) -> None:
        self.entry.append(text)

    def truth_source(self, text: str) -> None:
        if text not in self.truth:
            self.truth.append(text)

    def check(self, name: str, condition, detail=None) -> None:
        ok = bool(condition)
        self.assertions.append(
            {
                "name": name,
                "ok": ok,
                "detail": None if detail is None else _short(detail),
            }
        )
        assert ok, f"{self.journey_id}: {name} failed: {detail}"

    def note(self, key: str, value) -> None:
        self.facts[key] = value

    def block(self, reason: str) -> None:
        self.status = "blocked"
        self.failure = reason

    # -- evidence -----------------------------------------------------------
    def record(self) -> dict:
        return {
            "journey_id": self.journey_id,
            "title": self.title,
            "status": self.status,
            "entry": self.entry,
            "duration_s": self.duration_s,
            "truth_sources": self.truth,
            "assertions": self.assertions,
            "assertion_count": len(self.assertions),
            "facts": self.facts,
            "failure": self.failure,
            "environment": {
                "profile": "dev-stub",
                "postgres": "127.0.0.1:55432 (throwaway database per stack)",
                "execution": "cmd/supervisor exec --once (WSL2/runsc) / real mTLS internal protocol",
                "maintenance": "cmd/maintenance run --once",
            },
        }

    def persist(self) -> None:
        self.duration_s = round(time.time() - self.started_at, 2)
        _RECORDS[self.journey_id] = self
        if os.environ.get("T101_WRITE_EVIDENCE") != "1":
            return
        existing: dict[str, dict] = {}
        if EVIDENCE_JSON.exists():
            try:
                payload = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
                for item in payload.get("journeys", []):
                    existing[item["journey_id"]] = item
            except (json.JSONDecodeError, KeyError):  # pragma: no cover - corrupt file
                existing = {}
        existing[self.journey_id] = self.record()
        ordered = [existing[key] for key in sorted(existing)]
        EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "ticket": "T101",
            "issue": 102,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "journeys": ordered,
        }
        EVIDENCE_JSON.write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )


def _short(value, limit: int = 400) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[: limit - 3] + "..."


@contextmanager
def journey(journey_id: str, title: str):
    """Run one journey, always persisting a truthful status (passed/failed/blocked)."""
    record = Journey(journey_id=journey_id, title=title)
    try:
        yield record
    except BaseException as exc:
        if record.status != "blocked":
            record.status = "failed"
        if record.failure is None:
            record.failure = f"{type(exc).__name__}: {exc}"
        if os.environ.get("T101_VERBOSE_FAILURES") == "1":
            record.facts.setdefault("traceback", traceback.format_exc(limit=8))
        raise
    else:
        if record.status == "not_run":
            record.status = "passed"
    finally:
        record.persist()


def records() -> dict[str, dict]:
    return {key: value.record() for key, value in _RECORDS.items()}
