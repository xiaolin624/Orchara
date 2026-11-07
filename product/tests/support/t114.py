"""F13B/T114 real-stack capacity harness: 100 users / 1000 applications / 20
top-level stand-in executions at a fixed 500ms stub service time.

The ticket measures capacity, so everything here is a real product path:

* the profile is seeded through the public REST endpoints (admin invite/activate,
  application create + draft save), never with direct application SQL;
* every top-level Run is a real ``debugDraft`` submission; every attempt is
  claimed and executed by a real ``cmd/supervisor exec --once`` OS process;
* the model node calls the real F06/T041 capability proxy over the internal mTLS
  listener, which dispatches to the deployed ``stub`` provider. The only test
  knob is the **capacity-stub deployment service time**
  (``ORCHARA_CAPACITY_STUB_SERVICE_DELAY``, honoured only by the
  ``capacity-stub`` profile and only for the stub provider): it is stand-in
  time, not vendor latency, and the report says so;
* a real ``cmd/maintenance run --kinds artifact_finalize`` pool activates the
  artifacts each execution uploads;
* queueing and executing are measured from the authoritative rows: a queued Run
  has no live attempt, and the executing histogram is sampled while a sweep over
  the attempt intervals proves the peak simultaneous concurrency.

Nothing is mocked and no resource number is invented: anything the host cannot
measure is reported as ``not_run`` by the caller.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
TOOLS_ROOT = PRODUCT_ROOT / "tools"
for _entry in (str(TOOLS_ROOT), str(TESTS_ROOT)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import psutil  # noqa: E402

from evidence_redaction import find_violations, redact_value  # noqa: E402
from support import f00b, stack as st, t024, t025, t026, t027, t029, t033, t078  # noqa: E402

EVIDENCE_JSON = PRODUCT_ROOT / "reports" / "f13b" / "T114-capacity.json"
WRITE_EVIDENCE_ENV = "F13B_WRITE_EVIDENCE"
FULL_ENV = "F13B_CAPACITY_FULL"
SHORT_SECONDS_ENV = "F13B_CAPACITY_SHORT_SECONDS"

# The fixed capacity profile of the ticket.
USERS = 100
APPLICATIONS = 1000
TOP_LEVEL_SLOTS = 20
CHILD_SLOTS = 8
SERVICE_TIME_MS = 500

# The deployment knob under test (Go: config.EnvCapacityStubServiceDelay).
STUB_SERVICE_DELAY_ENV = "ORCHARA_CAPACITY_STUB_SERVICE_DELAY"

QUOTA_SCOPE = "global"
EXECUTION_DIMENSION = t029.QUOTA_DIMENSION
CHILD_DIMENSION = "child_slots"

FULL_TRIAL_SECONDS = 15 * 60
DEFAULT_SHORT_SECONDS = 60.0

# One owner session is used by the submitters; the other sessions are per-user
# (each user owns its share of the 1000 applications, like a real tenant).
RUN_INPUT = {"task": "Summarize the capacity stand-in task."}
PARENT_INPUT_PREFIX = "t114-parent-"
TRIVIAL_DEFINITION = t027.chain_definition([])


def full_mode() -> bool:
    return os.environ.get(FULL_ENV) == "1"


def write_evidence_enabled() -> bool:
    return os.environ.get(WRITE_EVIDENCE_ENV) == "1"


def short_seconds() -> float:
    raw = os.environ.get(SHORT_SECONDS_ENV)
    if raw:
        return max(10.0, float(raw))
    return DEFAULT_SHORT_SECONDS


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def _ts(value: float) -> str:
    return datetime.datetime.fromtimestamp(value, datetime.timezone.utc).isoformat(
        timespec="milliseconds"
    )


def _window(start: float, stop: float) -> str:
    return (
        f"r.created_at >= {st.quote_literal(_ts(start))}::timestamptz "
        f"AND r.created_at < {st.quote_literal(_ts(stop))}::timestamptz"
    )


def _percentiles(values: list[float]) -> dict:
    """p50/p95/p99 of a measured list (independent of the product's SQL)."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)

    def pick(fraction: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = fraction * (len(ordered) - 1)
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "n": len(ordered),
        "min": round(ordered[0], 6),
        "p50": round(pick(0.5), 6),
        "p95": round(pick(0.95), 6),
        "p99": round(pick(0.99), 6),
        "max": round(ordered[-1], 6),
        "mean": round(statistics.fmean(ordered), 6),
    }


def _sha256_ids(ids: list[str]) -> str:
    digest = hashlib.sha256()
    for value in sorted(ids):
        digest.update(value.encode("utf-8") + b"\n")
    return digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(PRODUCT_ROOT), *args],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return None
    if completed.returncode != 0:
        return None
    return (completed.stdout or "").strip() or None


def version_facts() -> dict:
    """Versions and the machine profile of the measurement (AC6)."""
    import platform

    from toolchain import go_binary  # local import: tests root is on sys.path

    go = None
    try:
        completed = subprocess.run(
            [str(go_binary()), "version"],
            capture_output=True,
            text=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
        )
        if completed.returncode == 0:
            go = (completed.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        go = None
    return {
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_commit": _git("rev-parse", "HEAD"),
        "python": platform.python_version(),
        "go": go,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_logical": psutil.cpu_count(logical=True),
        "cpu_physical": psutil.cpu_count(logical=False),
        "memory_total_gb": round(psutil.virtual_memory().total / 1024**3, 2),
        "disk_total_gb": round(psutil.disk_usage(str(PRODUCT_ROOT)).total / 1024**3, 2),
        "disk_free_gb_at_start": round(
            psutil.disk_usage(str(PRODUCT_ROOT)).free / 1024**3, 2
        ),
        "postgres": "PostgreSQL 16.4 at 127.0.0.1:55432 (isolated cluster)",
    }


# ---------------------------------------------------------------------------
# the trial stack: one throwaway database, one real API, real object store
# ---------------------------------------------------------------------------
def build_trial_stack(
    *,
    tmp_path: Path,
    go_binaries: dict[str, Path],
    api_server_factory,
    service_time_ms: int = SERVICE_TIME_MS,
):
    """A migrated+bootstrapped database behind the compiled API in capacity-stub.

    ``api_server_factory`` is the F13B conftest fixture: ``(dsn, profile=...,
    extra_env=...) -> ApiServer``. The caller owns the API lifecycle; the harness
    stops it before dropping the database.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    try:
        database = st.create_database("orchara_t114_")
    except RuntimeError:
        import pytest

        pytest.skip("isolated development cluster is not running (run tools/pg_dev.py init)")
    dsn, dbname = database.dsn, database.name
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir(parents=True, exist_ok=True)
    from support.certs import make_stack_certificates, unique_internal_addr

    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env[STUB_SERVICE_DELAY_ENV] = f"{int(service_time_ms)}ms"
    server = api_server_factory(dsn, profile="capacity-stub", extra_env=env)
    stack = t026.T026Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


# ---------------------------------------------------------------------------
# profile seeding (public REST only)
# ---------------------------------------------------------------------------
@dataclass
class ProfileSeed:
    admin: f00b.Session
    users: list  # f00b.Session
    user_ids: list[str]
    application_ids: list[str]
    capacity_application_id: str
    capacity_draft_revision: int
    parent_application_id: str
    parent_draft_revision: int
    child_version_id: str
    report: dict

    @property
    def owner(self):
        return self.users[0]

    def as_evidence(self) -> dict:
        return {
            "users_requested": USERS,
            "applications_requested": APPLICATIONS,
            "user_principal_count": len(self.user_ids),
            "application_count": len(self.application_ids),
            "user_ids_sha256": _sha256_ids(self.user_ids),
            "application_ids_sha256": _sha256_ids(self.application_ids),
            "capacity_application_id": self.capacity_application_id,
            "capacity_draft_revision": self.capacity_draft_revision,
            "parent_application_id": self.parent_application_id,
            "parent_draft_revision": self.parent_draft_revision,
            "child_version_id": self.child_version_id,
            **self.report,
        }

    def inputs(self) -> dict:
        return {
            "capacity_definition_sha256": _definition_digest(_capacity_model_definition()),
            "parent_definition_sha256": _definition_digest(
                t078.parent_definition([self.child_version_id])
            ),
            "child_definition_sha256": _definition_digest(t078.child_definition()),
            "run_input": dict(RUN_INPUT),
            "run_input_sha256": hashlib.sha256(
                json.dumps(RUN_INPUT, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "graph": "start -> analyse(model, 500ms stub) -> end",
            "note": (
                "the capacity Run is the T086 single-generation model graph executed "
                "by the real Go presetexec; one stand-in model call per top-level Run"
            ),
        }


def _invite_users(stack, admin, count: int) -> list:
    sessions = []
    started = time.perf_counter()
    for index in range(count):
        session = f00b.invite_and_activate(
            stack, admin, f"t114-user-{index:03d}", roles=("creator",)
        )
        sessions.append(session)
    return sessions, time.perf_counter() - started


def seed_profile(stack, *, users: int = USERS, applications: int = APPLICATIONS) -> ProfileSeed:
    """Seed the 100-user/1000-application profile through the public endpoints.

    The workload applications (one capacity model graph, one call_app parent, one
    published child version) are three of the 1000, all created by the same
    endpoints; 997 further start->end applications are created by the 100 users.
    """
    started = time.perf_counter()
    admin = f00b.admin_session(stack)
    # The stub model profile is deployment state for the workload graph, not a
    # tenant object; the established fixture inserts it like T025/T086 do.
    t025.seed_model_version(
        stack.dbname, admin.principal_id, model_id=t025.GOLDEN_MODEL_ID, validation_state="passed"
    )
    user_sessions, invite_seconds = _invite_users(stack, admin, users)
    owner = user_sessions[0]

    workload_started = time.perf_counter()
    child = t078.create_child_version(stack, owner)
    parent_application_id = t027.application_with_definition(
        owner, t078.parent_definition([child["id"]]), name="t114 call_app parent"
    )
    capacity_application_id = t027.application_with_definition(
        owner, _capacity_model_definition(), name="t114 capacity stand-in"
    )
    workload_seconds = time.perf_counter() - workload_started

    capacity_revision = t027.draft_revision(owner, capacity_application_id)
    parent_revision = t027.draft_revision(owner, parent_application_id)

    remaining = applications - 3
    if remaining < 0:
        raise AssertionError("the application profile cannot be smaller than the workload apps")
    base, extra = divmod(remaining, users)
    per_user = [base + (1 if index < extra else 0) for index in range(users)]

    app_started = time.perf_counter()
    errors: list[str] = []

    def create_for(index: int) -> list[str]:
        session = user_sessions[index]
        created: list[str] = []
        for number in range(per_user[index]):
            try:
                created.append(
                    t027.application_with_definition(
                        session,
                        TRIVIAL_DEFINITION,
                        name=f"t114-app-{index:03d}-{number:02d}",
                    )
                )
            except AssertionError as exc:  # noqa: PERF203 - collected for the report
                errors.append(f"user {index} app {number}: {exc}")
        return created

    created_apps: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for ids in pool.map(create_for, range(users)):
            created_apps.extend(ids)
    application_seconds = time.perf_counter() - app_started
    application_ids = [
        capacity_application_id,
        parent_application_id,
        child["application_id"],
        *created_apps,
    ]
    db_truth = profile_db_truth(stack.dbname)
    if db_truth["user_principals"] != users + 1:  # + the bootstrap admin
        raise AssertionError(f"the profile user count is wrong: {db_truth}")
    if db_truth["applications"] != applications:
        raise AssertionError(f"the profile application count is wrong: {db_truth}")
    if errors:
        raise AssertionError(f"{len(errors)} applications failed to seed: {errors[:3]}")
    report = {
        "users_total_s": round(time.perf_counter() - started, 2),
        "invite_activate_s": round(invite_seconds, 2),
        "workload_apps_s": round(workload_seconds, 2),
        "applications_s": round(application_seconds, 2),
        "applications_via": (
            "public REST: POST /api/v1/admin/users + POST /api/v1/auth/activate + "
            "POST /api/v1/applications + PUT /api/v1/applications/{id}/draft"
        ),
        "workload_applications": 3,
        "profile_applications": remaining,
        "db_truth": db_truth,
        "failures": errors,
    }
    return ProfileSeed(
        admin=admin,
        users=user_sessions,
        user_ids=[session.principal_id for session in user_sessions],
        application_ids=application_ids,
        capacity_application_id=capacity_application_id,
        capacity_draft_revision=capacity_revision,
        parent_application_id=parent_application_id,
        parent_draft_revision=parent_revision,
        child_version_id=child["id"],
        report=report,
    )


def _capacity_model_definition() -> dict:
    """start -> analyse(model, stub) -> end: exactly one 500ms stand-in call."""
    from support import t086

    return t086.model_definition(t025.GOLDEN_MODEL_ID, template="Summarize {{task}}")


def _definition_digest(definition: dict) -> str:
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def profile_db_truth(dbname: str) -> dict:
    users = int(
        st.sql(
            dbname,
            "SELECT count(*) FROM platform.principals WHERE kind = 'user'",
        )
    )
    applications = int(st.sql(dbname, "SELECT count(*) FROM platform.applications"))
    drafts = int(st.sql(dbname, "SELECT count(*) FROM platform.application_drafts"))
    return {
        "user_principals": users,
        "applications": applications,
        "application_drafts": drafts,
        "note": "user_principals includes the bootstrap admin (100 invited users + admin)",
    }


# ---------------------------------------------------------------------------
# quota capacity (the established operator knob)
# ---------------------------------------------------------------------------
def configure_quota(dbname: str, *, execution_slots: int = TOP_LEVEL_SLOTS,
                    child_slots: int = CHILD_SLOTS) -> dict:
    before = st.sql_rows(
        dbname,
        "SELECT scope, dimension, used, capacity FROM platform.quota_counters "
        "WHERE dimension IN ('execution_slots', 'child_slots') ORDER BY scope, dimension",
    )
    statement = (
        "UPDATE platform.quota_counters SET capacity = "
        f"{int(execution_slots)} WHERE scope = '{QUOTA_SCOPE}' AND dimension = '{EXECUTION_DIMENSION}'; "
        "UPDATE platform.quota_counters SET capacity = "
        f"{int(child_slots)} WHERE scope = '{QUOTA_SCOPE}' AND dimension = '{CHILD_DIMENSION}'"
    )
    st.sql(
        dbname,
        f"UPDATE platform.quota_counters SET capacity = {int(execution_slots)} "
        f"WHERE scope = '{QUOTA_SCOPE}' AND dimension = '{EXECUTION_DIMENSION}'",
    )
    st.sql(
        dbname,
        f"UPDATE platform.quota_counters SET capacity = {int(child_slots)} "
        f"WHERE scope = '{QUOTA_SCOPE}' AND dimension = '{CHILD_DIMENSION}'",
    )
    after = st.sql_rows(
        dbname,
        "SELECT scope, dimension, used, capacity FROM platform.quota_counters "
        "WHERE dimension IN ('execution_slots', 'child_slots') ORDER BY scope, dimension",
    )
    return {
        "statement": statement,
        "before": before,
        "after": after,
        "knob": "platform.quota_counters (deployment configuration; no migration, no contract change)",
        "top_level_slots": execution_slots,
        "child_pool_slots": child_slots,
    }


# ---------------------------------------------------------------------------
# runtime snapshot (authoritative rows only)
# ---------------------------------------------------------------------------
SNAPSHOT_SQL = """
SELECT
  (SELECT count(*) FROM platform.runs WHERE parent_run_id IS NULL AND state = 'queued') AS top_queued,
  (SELECT count(*) FROM platform.runs WHERE parent_run_id IS NULL AND state = 'running') AS top_running,
  (SELECT count(*) FROM platform.runs
    WHERE parent_run_id IS NULL AND state = 'running' AND phase = 'executing') AS top_executing,
  (SELECT count(*) FROM platform.runs
    WHERE parent_run_id IS NULL AND state = 'running' AND phase = 'finalizing') AS top_finalizing,
  (SELECT count(*) FROM platform.runs WHERE parent_run_id IS NULL AND state = 'cancelling') AS top_cancelling,
  (SELECT count(*) FROM platform.runs WHERE parent_run_id IS NULL AND state = 'waiting_children') AS top_waiting,
  (SELECT count(*) FROM platform.runs WHERE parent_run_id IS NOT NULL AND state = 'queued') AS child_queued,
  (SELECT count(*) FROM platform.runs
    WHERE parent_run_id IS NOT NULL AND state = 'running') AS child_running,
  (SELECT count(*) FROM platform.runs
    WHERE parent_run_id IS NOT NULL AND state = 'running' AND phase = 'executing') AS child_executing,
  (SELECT count(*) FROM platform.run_attempts
    WHERE exited_at IS NULL AND revoked_at IS NULL) AS live_attempts,
  (SELECT count(*) FROM platform.run_attempts a
    JOIN platform.runs r ON r.id = a.run_id
    WHERE r.parent_run_id IS NULL AND a.exited_at IS NULL AND a.revoked_at IS NULL) AS live_top_attempts,
  (SELECT count(*) FROM platform.runs r
    WHERE r.state = 'queued' AND EXISTS (
      SELECT 1 FROM platform.run_attempts a
       WHERE a.run_id = r.id AND a.exited_at IS NULL AND a.revoked_at IS NULL
    )) AS queued_with_live_attempt,
  (SELECT count(*) FROM platform.runs r
    WHERE r.state = 'cancelling' AND EXISTS (
      SELECT 1 FROM platform.run_attempts a
       WHERE a.run_id = r.id AND a.exited_at IS NULL AND a.revoked_at IS NULL
    )) AS cancelling_with_live_attempt,
  (SELECT used FROM platform.quota_counters
    WHERE scope = 'global' AND dimension = 'execution_slots') AS execution_slots_used,
  (SELECT capacity FROM platform.quota_counters
    WHERE scope = 'global' AND dimension = 'execution_slots') AS execution_slots_capacity,
  (SELECT used FROM platform.quota_counters
    WHERE scope = 'global' AND dimension = 'child_slots') AS child_slots_used,
  (SELECT capacity FROM platform.quota_counters
    WHERE scope = 'global' AND dimension = 'child_slots') AS child_slots_capacity,
  (SELECT count(*) FROM platform.background_jobs
    WHERE kind = 'artifact_finalize' AND state = 'ready') AS finalize_ready,
  (SELECT count(*) FROM platform.background_jobs
    WHERE kind = 'artifact_finalize' AND state = 'leased') AS finalize_leased
"""


def runtime_snapshot(dbname: str) -> dict:
    rows = st.sql_rows(dbname, SNAPSHOT_SQL)
    assert len(rows) == 1, rows
    row = rows[0]
    for key, value in row.items():
        row[key] = int(value) if isinstance(value, (int, float)) else value
    return row


def pg_snapshot(dbname: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT "
        " (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections,"
        " (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        "    AND state = 'active') AS active,"
        " (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
        "    AND wait_event_type = 'Lock') AS lock_waiters,"
        " coalesce((SELECT max(extract(epoch FROM now() - query_start)) FROM pg_stat_activity"
        "    WHERE datname = current_database() AND state = 'active'), 0) AS max_active_query_s,"
        " pg_database_size(current_database()) AS database_bytes",
    )
    return rows[0]


# ---------------------------------------------------------------------------
# trial configuration
# ---------------------------------------------------------------------------
@dataclass
class TrialSettings:
    label: str
    seconds: float
    executor_workers: int = 24
    submitter_threads: int = 3
    target_queue: int = 60
    finalize_slots: int = 4
    heartbeat: str = "2s"
    poll: str = "250ms"
    sample_interval: float = 1.0


class ProbeCapacityError(AssertionError):
    """The AC5 probe Run could not be claimed inside its bounded retry window.

    This is deliberately a hard failure (the boundary assertion must see the real
    reason), never a silent ``not_run``.
    """


class CapacityTrial:
    """One sustained trial: submitters keep the queue full, real supervisors keep
    the 20 top-level slots executing, and the sampler records both."""

    def __init__(self, stack, seed: ProfileSeed, quota: dict, settings: TrialSettings):
        self.stack = stack
        self.seed = seed
        self.quota = quota
        self.settings = settings
        self.dbname = stack.dbname
        self.owner = seed.owner
        self.started = 0.0
        self.stopped = 0.0
        self.samples: list[dict] = []
        self.submit_latencies: list[float] = []
        self.submit_failures: list[dict] = []
        self.get_latencies: list[float] = []
        self.get_latencies_failures: list[int] = []
        self.window_stop = 0.0
        self.sse: dict = {}
        self.exec_records: dict = {
            "executed": 0,
            "idle": 0,
            "failed": 0,
            "outcomes": {},
            "durations_s": [],
            "failures": [],
            "processes": 0,
            "process_failures": [],
        }
        self.finalize_events: list[dict] = []
        self.cancel: dict = {"batches": [], "late_result": None, "summary": {}}
        self.children: dict = {"parents": [], "summary": {}}
        self.boundary: dict = {}
        self.drain: dict = {}
        self.child_parent_submits: list[dict] = []
        self._live_procs: dict[int, subprocess.Popen] = {}
        self._proc_lock = threading.Lock()
        self._stop = threading.Event()
        self._stop_submit = threading.Event()
        self._threads: list[threading.Thread] = []
        self._latest: dict = {}
        self._latest_lock = threading.Lock()
        self._finalizer: subprocess.Popen | None = None
        self._finalizer_log = None
        self._supervisor_env: dict[str, str] = {}
        self._started_monotonic = 0.0
        self._api_proc = psutil.Process(stack.server.process.pid)
        self._cpu_last = self._api_proc.cpu_times()
        self._cpu_last_at = time.monotonic()

    # -- lifecycle ----------------------------------------------------------
    def run(self) -> dict:
        settings = self.settings
        self._supervisor_env = t033.supervisor_exec_env(
            self.stack,
            digest=t033.rootfs_digest(),
            heartbeat=settings.heartbeat,
            result_wait="60s",
            poll=settings.poll,
            timeout="60s",
        )
        self._started_monotonic = time.monotonic()
        self.started = time.time()
        self.window_stop = self.started + settings.seconds
        self._start_finalizer()
        self._start_executors()
        self._start_sampler()
        self._start_submitters()
        try:
            self._run_schedule()
        finally:
            self._stop_submit.set()
            self.stopped = time.time()
            # Drain the queue with the executors still running so the recovery /
            # ownership probes claim their own Run instead of a leftover load
            # Run. The drain is outside the measured window.
            self.drain = self._drain_until_idle(timeout=30)
            self.drained_at = time.time()
            self._stop.set()
            self._join_threads(timeout=90)
        # The recovery/ownership probes are deliberately after the load: the
        # real T038 lease sweep parks the Run and KEEPS its execution slot
        # allocated (documented semantics), so running it before the load would
        # permanently shrink the measured pool by one slot.
        try:
            self.boundary["lease_expiry"] = self._lease_expiry_scenario()
            self.boundary["ownership"] = self._ownership_scenario()
            self.boundary["cancel_arbitration"] = self._cancel_arbitration_scenario()
        finally:
            self._stop_finalizer()
        metrics = self._collect_metrics()
        metrics["settings"] = {
            "label": settings.label,
            "seconds_requested": settings.seconds,
            "executor_workers": settings.executor_workers,
            "submitter_threads": settings.submitter_threads,
            "target_queue": settings.target_queue,
            "finalize_slots": settings.finalize_slots,
            "supervisor_heartbeat": settings.heartbeat,
            "sample_interval_s": settings.sample_interval,
            "duration_s": round(self.stopped - self.started, 2),
            "drain_seconds": round(
                max(0.0, getattr(self, "drained_at", self.stopped) - self.window_stop), 2
            ),
        }
        return metrics

    # -- schedule -----------------------------------------------------------
    def _run_schedule(self) -> None:
        started = self._started_monotonic
        duration = self.settings.seconds
        actions = self._schedule(duration)
        next_api_probe = started + 10.0
        while True:
            now = time.monotonic()
            if now - started >= duration:
                break
            while actions and now - started >= actions[0][0]:
                _, name, kwargs = actions.pop(0)
                if time.monotonic() - started >= duration - 1.0:
                    continue
                getattr(self, name)(**kwargs)
                now = time.monotonic()
            if now >= next_api_probe:
                self._api_latency_probe()
                next_api_probe = now + 10.0
            time.sleep(0.2)

    def _schedule(self, duration: float) -> list[tuple[float, str, dict]]:
        actions: list[tuple[float, str, dict]] = []
        # The cancellation batch runs inside the saturated window but near its
        # end: the real cancel arbitration can leak a slot when the intent races
        # an in-flight capability call (recorded as a finding), so the tail must
        # not decide the capacity numbers.
        cancel_at = max(duration - (45.0 if duration > 90 else 15.0), 0.65 * duration)
        if duration <= 90:  # short profile
            actions.append((0.20 * duration, "_submit_child_parent", {}))
            actions.append((cancel_at, "_cancel_batch", {"executing": 6, "queued": 2}))
            actions.append((0.45 * duration, "_sse_probe", {}))
            return actions
        count = max(3, int(duration // 120))
        for index in range(count):
            actions.append((60.0 + index * 120.0, "_submit_child_parent", {}))
        actions.append((cancel_at, "_cancel_batch", {"executing": 6, "queued": 4}))
        actions.append((0.40 * duration, "_sse_probe", {}))
        actions.append((0.75 * duration, "_api_latency_probe", {}))
        actions.sort(key=lambda item: item[0])
        return actions

    # -- submitters ---------------------------------------------------------
    def _start_submitters(self) -> None:
        for index in range(self.settings.submitter_threads):
            thread = threading.Thread(
                target=self._submitter_loop, name=f"t114-submit-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def _submitter_loop(self) -> None:
        while not self._stop.is_set() and not self._stop_submit.is_set():
            with self._latest_lock:
                snapshot = dict(self._latest)
            queued = int(snapshot.get("top_queued", 0) or 0)
            if queued > self.settings.target_queue:
                time.sleep(0.01)
                continue
            started = time.perf_counter()
            try:
                response = t027.submit_debug(
                    self.owner,
                    self.seed.capacity_application_id,
                    draft_revision=self.seed.capacity_draft_revision,
                    input_value=RUN_INPUT,
                )
            except Exception as exc:  # noqa: BLE001 - recorded evidence, loop continues
                self.submit_failures.append({"error": f"{type(exc).__name__}: {exc}"})
                time.sleep(0.05)
                continue
            latency = time.perf_counter() - started
            self.submit_latencies.append(latency)
            if response.status != 202:
                self.submit_failures.append(
                    {"status": response.status, "body": response.text[:200]}
                )
                time.sleep(0.05)

    # -- executors ----------------------------------------------------------
    def _start_executors(self) -> None:
        supervisor = self.stack.binaries["supervisor"]
        for index in range(self.settings.executor_workers):
            thread = threading.Thread(
                target=self._executor_loop,
                args=(supervisor, index),
                name=f"t114-exec-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _executor_loop(self, supervisor: Path, index: int) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            try:
                process = subprocess.Popen(
                    [str(supervisor), "exec", "--once", "--worker-id", f"t114-sup-{index:02d}"],
                    cwd=PRODUCT_ROOT,
                    env=self._supervisor_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:  # pragma: no cover - defensive
                self.exec_records["process_failures"].append(f"spawn: {exc}")
                time.sleep(0.5)
                continue
            self.exec_records["processes"] += 1
            with self._proc_lock:
                self._live_procs[process.pid] = process
            try:
                stdout, stderr = process.communicate(timeout=150)
            except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                process.kill()
                stdout, stderr = process.communicate()
                self.exec_records["process_failures"].append(
                    f"pid {process.pid}: supervisor exec exceeded 150s"
                )
            finally:
                with self._proc_lock:
                    self._live_procs.pop(process.pid, None)
            elapsed = time.perf_counter() - started
            report = None
            for line in (stdout or "").splitlines():
                if not line.strip():
                    continue
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and "status" in candidate:
                    report = candidate
            if report is None:
                self.exec_records["failed"] += 1
                if len(self.exec_records["failures"]) < 50:
                    self.exec_records["failures"].append(
                        {
                            "returncode": process.returncode,
                            "stderr": (stderr or "")[-400:],
                        }
                    )
                time.sleep(0.05)
                continue
            status = report.get("status")
            if status == "idle":
                self.exec_records["idle"] += 1
                # No slot was free: back off a little instead of spawning in a
                # tight loop (the queue is kept full by the submitters).
                time.sleep(0.03)
                continue
            self.exec_records["executed"] += 1
            self.exec_records["durations_s"].append(round(elapsed, 3))
            outcome = report.get("outcome") or "unknown"
            self.exec_records["outcomes"][outcome] = (
                self.exec_records["outcomes"].get(outcome, 0) + 1
            )
            if status == "failed" or process.returncode not in (0, None):
                self.exec_records["failed"] += 1
                if len(self.exec_records["failures"]) < 50:
                    self.exec_records["failures"].append(
                        {
                            "run_id": report.get("run_id"),
                            "status": status,
                            "outcome": outcome,
                            "run_state": report.get("run_state"),
                            "error_code": report.get("error_code"),
                            "error": report.get("error"),
                        }
                    )

    # -- sampler ------------------------------------------------------------
    def _start_sampler(self) -> None:
        thread = threading.Thread(target=self._sample_loop, name="t114-sampler", daemon=True)
        thread.start()
        self._threads.append(thread)

    def _sample_loop(self) -> None:
        counter = 0
        while not self._stop.is_set():
            if time.monotonic() - self._started_monotonic > self.settings.seconds + 0.5:
                break
            try:
                sample = runtime_snapshot(self.dbname)
            except AssertionError:  # pragma: no cover - teardown race
                break
            sample["at"] = round(time.time() - self.started, 3)
            sample["monotonic"] = round(time.monotonic() - self._started_monotonic, 3)
            if counter % 5 == 0:
                sample["processes"] = self._process_snapshot()
                try:
                    sample["pg"] = pg_snapshot(self.dbname)
                except AssertionError:  # pragma: no cover - teardown race
                    sample["pg"] = {}
                sample["disk_free_gb"] = round(psutil.disk_usage(str(self.stack.object_dir)).free / 1024**3, 2)
            with self._latest_lock:
                self._latest = sample
            self.samples.append(sample)
            counter += 1
            time.sleep(self.settings.sample_interval)

    def _process_snapshot(self) -> dict:
        result = {"api_rss_mb": None, "api_cpu_pct": None, "finalizer_rss_mb": None,
                  "finalizer_cpu_pct": None, "supervisor_processes": 0,
                  "supervisor_rss_mb": 0.0}
        now = time.monotonic()
        try:
            times = self._api_proc.cpu_times()
            delta = (times.user - self._cpu_last.user) + (times.system - self._cpu_last.system)
            elapsed = max(now - self._cpu_last_at, 1e-6)
            result["api_cpu_pct"] = round(100.0 * delta / elapsed, 1)
            self._cpu_last = times
            self._cpu_last_at = now
            result["api_rss_mb"] = round(self._api_proc.memory_info().rss / 1024**2, 1)
        except (psutil.NoSuchProcess, psutil.AccessDenied):  # pragma: no cover
            pass
        if self._finalizer is not None and self._finalizer.poll() is None:
            try:
                proc = psutil.Process(self._finalizer.pid)
                result["finalizer_rss_mb"] = round(proc.memory_info().rss / 1024**2, 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):  # pragma: no cover
                pass
        with self._proc_lock:
            live = list(self._live_procs.values())
        result["supervisor_processes"] = len(live)
        total = 0
        for process in live:
            try:
                total += psutil.Process(process.pid).memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        result["supervisor_rss_mb"] = round(total / 1024**2, 1)
        return result

    # -- finalizer ----------------------------------------------------------
    def _start_finalizer(self) -> None:
        env = f00b.maintenance_env(
            self.stack, heartbeat_interval="500ms",
            extra={"ORCHARA_BACKGROUND_RETRY_BACKOFF": "50ms"},
        )
        log_path = self.stack.object_dir.parent / "t114-finalizer.log"
        self._finalizer_log = log_path.open("w", encoding="utf-8", newline="\n")
        self._finalizer = subprocess.Popen(
            [
                str(self.stack.binaries["maintenance"]),
                "run",
                "--kinds",
                "artifact_finalize",
                "--system-io-slots",
                str(self.settings.finalize_slots),
                "--poll-interval",
                "100ms",
                "--worker-id",
                "t114-finalize",
            ],
            cwd=PRODUCT_ROOT,
            env=env,
            stdout=self._finalizer_log,
            stderr=subprocess.STDOUT,
            text=True,
            stdin=subprocess.DEVNULL,
        )

    def _stop_finalizer(self) -> None:
        if self._finalizer is None:
            return
        if self._finalizer.poll() is None:
            self._finalizer.terminate()
            try:
                self._finalizer.wait(timeout=20)
            except subprocess.TimeoutExpired:  # pragma: no cover
                self._finalizer.kill()
                self._finalizer.wait(timeout=5)
        if self._finalizer_log is not None:
            self._finalizer_log.close()
        self.finalize_events = _tail_lines(
            self.stack.object_dir.parent / "t114-finalizer.log", 40
        )

    # -- cancellation (public REST only) ------------------------------------
    def _cancel_batch(self, *, executing: int, queued: int) -> None:
        batch = {"at": round(time.time() - self.started, 2), "executing": [], "queued": []}
        targets: list[tuple[str, str]] = []
        if executing:
            # Prefer Runs that already submitted their result (phase=finalizing):
            # cancel arbitration there is the clean intent->stop path. Fall back
            # to executing Runs (the capability-call race is recorded, not hidden).
            targets.extend(
                _select_runs(self.dbname, "running", executing, self.started, phase="finalizing")
            )
            missing = executing - len(targets)
            if missing > 0:
                targets.extend(
                    _select_runs(
                        self.dbname, "running", missing, self.started, phase="executing"
                    )
                )
        if queued:
            targets.extend(_select_runs(self.dbname, "queued", queued, self.started))
        for run_id, state_before in targets:
            started = time.perf_counter()
            response = self.owner.client.post(
                f"/api/v1/runs/{run_id}/cancel", csrf=self.owner.csrf
            )
            response_s = time.perf_counter() - started
            entry = {
                "run_id": run_id,
                "state_before": state_before,
                "api_seconds": round(response_s, 4),
                "status": response.status,
                "intent_header": response.header("X-Run-Cancel-Requested-At"),
            }
            if response.status != 202:
                entry["body"] = response.text[:300]
            else:
                body = response.json or {}
                entry["state_after_api"] = body.get("state")
            batch["executing" if state_before == "running" else "queued"].append(entry)
        deadline = time.time() + 30.0
        pending = [
            item["run_id"]
            for item in batch["executing"] + batch["queued"]
            if item.get("status") == 202
        ]
        while pending and time.time() < deadline:
            rows = _cancel_rows(self.dbname, pending)
            still = []
            for row in rows:
                if row["state"] not in ("succeeded", "failed", "cancelled"):
                    still.append(row["run_id"])
            if not still:
                break
            pending = still
            time.sleep(0.1)
        rows = _cancel_rows(
            self.dbname,
            [item["run_id"] for item in batch["executing"] + batch["queued"]],
        )
        by_id = {row["run_id"]: row for row in rows}
        for item in batch["executing"] + batch["queued"]:
            item.update(by_id.get(item["run_id"], {}))
            item["stopped"] = item.get("state") in ("succeeded", "failed", "cancelled")
            item["stuck_after_stop_timeout"] = not item["stopped"]
        self.cancel["batches"].append(batch)
        if self.cancel["late_result"] is None:
            for item in batch["executing"]:
                if item.get("state") == "cancelled":
                    self.cancel["late_result"] = self._late_result_probe(item["run_id"])
                    break

    def _late_result_probe(self, run_id: str) -> dict:
        """A late worker result for a terminal cancelled Run must never revive it."""
        attempts = st.sql_rows(
            self.dbname,
            "SELECT token::text AS attempt_token FROM platform.run_attempts "
            f"WHERE run_id = {st.quote_literal(run_id)}::uuid "
            "ORDER BY started_at DESC LIMIT 1",
        )
        before = t029.run_state_row(self.dbname, run_id)
        probe = {
            "run_id": run_id,
            "state_before": before["state"],
            "attempt_found": bool(attempts),
        }
        if attempts:
            response = t029.execution_client(self.stack, "worker").submit_result(
                run_id,
                attempt_token=attempts[0]["attempt_token"],
                suggested_state="succeeded",
            )
            probe["status"] = response.status
            body = response.json or {}
            probe["error_code"] = (body.get("error") or {}).get("code")
        after = t029.run_state_row(self.dbname, run_id)
        probe["state_after"] = after["state"]
        probe["revived"] = after["state"] != before["state"]
        return probe

    # -- child pool progress ------------------------------------------------
    def _submit_child_parent(self) -> None:
        index = len(self.child_parent_submits)
        try:
            response = t027.submit_debug(
                self.owner,
                self.seed.parent_application_id,
                draft_revision=self.seed.parent_draft_revision,
                input_value={"source": f"{PARENT_INPUT_PREFIX}{index:02d}"},
            )
        except Exception as exc:  # noqa: BLE001 - recorded evidence
            self.child_parent_submits.append({"error": f"{type(exc).__name__}: {exc}"})
            return
        self.child_parent_submits.append(
            {
                "run_id": (response.json or {}).get("id"),
                "status": response.status,
                "at": round(time.time() - self.started, 2),
            }
        )

    # -- API latency --------------------------------------------------------
    def _api_latency_probe(self) -> None:
        rows = st.sql_rows(
            self.dbname,
            "SELECT id::text AS id FROM platform.runs "
            "WHERE parent_run_id IS NULL AND state IN ('running', 'queued') "
            f"AND created_at >= {st.quote_literal(_ts(self.started))}::timestamptz "
            "ORDER BY created_at DESC LIMIT 1",
        )
        if not rows:
            return
        started = time.perf_counter()
        response = self.owner.client.get(f"/api/v1/runs/{rows[0]['id']}")
        self.get_latencies.append(time.perf_counter() - started)
        if response.status >= 400:
            self.get_latencies_failures.append(response.status)

    def _sse_probe(self) -> None:
        rows = st.sql_rows(
            self.dbname,
            "SELECT id::text AS id FROM platform.runs "
            "WHERE parent_run_id IS NULL AND state = 'running' "
            f"AND created_at >= {st.quote_literal(_ts(self.started))}::timestamptz "
            "ORDER BY created_at DESC LIMIT 1",
        )
        if not rows:
            self.sse = {"status": "not_run", "reason": "no running Run at probe time"}
            return
        import urllib.request

        request = urllib.request.Request(
            f"{self.stack.server.base_url}/api/v1/runs/{rows[0]['id']}/events",
            headers={
                "Accept": "text/event-stream",
                "Cookie": f"{st.SESSION_COOKIE}={self.owner.cookie}",
            },
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                first = response.read(1)
                ttfb = time.perf_counter() - started
                self.sse = {
                    "status": response.status,
                    "first_byte_s": round(ttfb, 4),
                    "bytes": len(first),
                    "run_id": rows[0]["id"],
                }
        except Exception as exc:  # noqa: BLE001 - recorded not_run
            self.sse = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.perf_counter() - started, 4),
            }

    # -- boundary scenarios (AC5) -------------------------------------------
    def _claim_own_run(
        self,
        run_id: str,
        *,
        worker_id: str,
        unit: str,
        timeout: float = 45.0,
        poll_interval: float = 0.5,
    ) -> dict:
        """Claim until the given Run is handed back, retrying a transient 204/409.

        After the load drain a waiting parent can still be resumed by the F07
        terminal hook, and an in-flight execution can still hold a slot for a
        moment, so a single 204 is not evidence that the probe Run is
        unclaimable. The retry window is bounded; on exhaustion the probe raises
        :class:`ProbeCapacityError` with the authoritative capacity rows instead
        of silently degrading the AC5 boundary probe to ``not_run``.
        """
        client = t029.execution_client(self.stack, "supervisor")
        started = time.time()
        deadline = started + timeout
        attempts = 0
        requeued = 0
        statuses: list[int] = []
        last_status: int | None = None
        last_body = ""
        while True:
            attempts += 1
            response = client.claim_job(worker_id=worker_id, execution_unit_id=unit)
            last_status = response.status
            statuses.append(response.status)
            if response.status == 200:
                lease = response.json
                if lease["run_id"] == run_id:
                    lease["_claim_attempts"] = attempts
                    lease["_claim_requeued_foreign"] = requeued
                    lease["_claim_waited_seconds"] = round(time.time() - started, 3)
                    lease["_claim_statuses"] = sorted(set(statuses))
                    return lease
                # A foreign Run was handed back: return it to the queue through
                # the documented "never started" exit evidence.
                requeued += 1
                client.confirm_exit(
                    lease["attempt_token"],
                    execution_unit_id=unit,
                    evidence=t029.exit_evidence(lease, started=False),
                )
            else:
                last_body = response.text[:200]
            if time.time() >= deadline:
                break
            time.sleep(poll_interval)
        snapshot = runtime_snapshot(self.dbname)
        raise ProbeCapacityError(
            f"the AC5 probe Run {run_id} could not be claimed within {timeout}s: "
            f"attempts={attempts}, last_status={last_status}, "
            f"statuses={sorted(set(statuses))}, "
            f"execution_slots used={snapshot['execution_slots_used']}/"
            f"{snapshot['execution_slots_capacity']}, "
            f"top_queued={snapshot['top_queued']}, "
            f"child_queued={snapshot['child_queued']}, "
            f"live_attempts={snapshot['live_attempts']}, "
            f"cancelling_with_live_attempt={snapshot['cancelling_with_live_attempt']}, "
            f"last_body={last_body!r}"
        )

    def _lease_expiry_scenario(self) -> dict:
        """Claim -> force lease expiry -> run the real T038 lease sweep.

        ``orchara-admin runs sweep-leases`` is the delivered recovery surface: it
        parks the Run in ``recovery_pending`` and (documented) keeps the job held
        and the execution slot allocated. That is why this probe runs after the
        measured load instead of before it.
        """
        try:
            response = t027.submit_debug(
                self.owner,
                self.seed.capacity_application_id,
                draft_revision=self.seed.capacity_draft_revision,
                input_value=RUN_INPUT,
            )
            assert response.status == 202, response.text
            run_id = response.json["id"]
            lease = self._claim_own_run(
                run_id, worker_id="t114-expiry", unit="t114-expiry-unit"
            )
            t029.force_lease_expiry(self.dbname, lease["attempt_token"])
            completed = subprocess.run(
                [str(self.stack.binaries["admin"]), "runs", "sweep-leases", "--grace", "1s"],
                cwd=PRODUCT_ROOT,
                env=st.admin_env(self.stack.dsn),
                capture_output=True,
                text=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
            )
            swept = None
            for line in (completed.stdout or "").splitlines():
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict):
                    swept = candidate
            row = t029.run_state_row(self.dbname, run_id)
            return {
                "run_id": run_id,
                "claim_status": 200,
                "claim_attempts": lease.get("_claim_attempts"),
                "claim_requeued_foreign": lease.get("_claim_requeued_foreign"),
                "claim_waited_seconds": lease.get("_claim_waited_seconds"),
                "claim_statuses": lease.get("_claim_statuses"),
                "sweep_returncode": completed.returncode,
                "sweep_report": swept,
                "state_after_sweep": row["state"],
                "recovered": row["state"] == "recovery_pending",
                "slot_policy": "documented T038: the parked Run keeps its slot allocated",
            }
        except ProbeCapacityError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            return {"status": "not_run", "error": f"{type(exc).__name__}: {exc}"}

    def _ownership_scenario(self) -> dict:
        """A non-owner can neither read nor cancel another principal's Run."""
        try:
            rows = st.sql_rows(
                self.dbname,
                "SELECT id::text AS id FROM platform.runs "
                "WHERE parent_run_id IS NULL ORDER BY created_at DESC LIMIT 1",
            )
            if not rows:
                return {"status": "not_run", "reason": "no Run to probe"}
            run_id = rows[0]["id"]
            other = self.seed.users[1]
            read = other.client.get(f"/api/v1/runs/{run_id}")
            cancel = other.client.post(
                f"/api/v1/runs/{run_id}/cancel", csrf=other.csrf
            )
            return {
                "run_id": run_id,
                "foreign_read_status": read.status,
                "foreign_cancel_status": cancel.status,
                "foreign_cancel_code": ((cancel.json or {}).get("error") or {}).get("code"),
                "denied": read.status == 404 and cancel.status == 404,
            }
        except Exception as exc:  # noqa: BLE001
            return {"status": "not_run", "error": f"{type(exc).__name__}: {exc}"}

    def _cancel_arbitration_scenario(self) -> dict:
        """Intent while live -> worker exit -> cancelled, and a late result is refused.

        This is the deterministic T036 shape (claim, public cancel, worker exit
        confirmation, late result) on the real protocol; it is what proves cancel
        intent and actual stop are separate facts even when the in-window cancel
        batch hits the capability-call race.
        """
        try:
            response = t027.submit_debug(
                self.owner,
                self.seed.capacity_application_id,
                draft_revision=self.seed.capacity_draft_revision,
                input_value=RUN_INPUT,
            )
            assert response.status == 202, response.text
            run_id = response.json["id"]
            unit = "t114-cancel-unit"
            lease = self._claim_own_run(run_id, worker_id="t114-cancel", unit=unit)
            cancel = self.owner.client.post(
                f"/api/v1/runs/{run_id}/cancel", csrf=self.owner.csrf
            )
            intent_header = cancel.header("X-Run-Cancel-Requested-At")
            state_after_cancel = (cancel.json or {}).get("state")
            exited = t029.execution_client(self.stack, "supervisor").confirm_exit(
                lease["attempt_token"],
                execution_unit_id=unit,
                evidence=t029.exit_evidence(lease, started=True),
            )
            deadline = time.time() + 30
            row = t029.run_state_row(self.dbname, run_id)
            while row["state"] not in ("succeeded", "failed", "cancelled") and time.time() < deadline:
                time.sleep(0.05)
                row = t029.run_state_row(self.dbname, run_id)
            late = t029.execution_client(self.stack, "worker").submit_result(
                run_id, attempt_token=lease["attempt_token"], suggested_state="failed",
                error_code="tool_timeout",
            )
            after = t029.run_state_row(self.dbname, run_id)
            return {
                "run_id": run_id,
                "claim_status": 200,
                "claim_attempts": lease.get("_claim_attempts"),
                "claim_requeued_foreign": lease.get("_claim_requeued_foreign"),
                "claim_waited_seconds": lease.get("_claim_waited_seconds"),
                "claim_statuses": lease.get("_claim_statuses"),
                "cancel_status": cancel.status,
                "state_after_cancel": state_after_cancel,
                "intent_recorded": intent_header is not None,
                "exit_status": exited.status,
                "state_after_exit": row["state"],
                "finished_at": row["finished_at"],
                "cancel_requested_at": row["cancel_requested_at"],
                "late_result_status": late.status,
                "late_result_code": ((late.json or {}).get("error") or {}).get("code"),
                "state_after_late_result": after["state"],
                "cancelled": row["state"] == "cancelled" and after["state"] == "cancelled",
                "late_result_refused": late.status == 409,
            }
        except ProbeCapacityError:
            raise
        except Exception as exc:  # noqa: BLE001
            return {"status": "not_run", "error": f"{type(exc).__name__}: {exc}"}

    # -- teardown -----------------------------------------------------------
    def _drain_until_idle(self, *, timeout: float) -> dict:
        """Let the real executors finish the queued load before the probes.

        Two phases under one deadline: no queued Run may be left (top-level or
        child), and no load execution may still hold an execution slot. The slot
        target is the number of documented stuck ``cancelling`` attempts (the
        cancel-race finding): 0 when the race did not happen, otherwise only
        those known stuck slots may remain. The actual waits, target and final
        counter are recorded in the evidence as ``trial["drain"]``.
        """
        started = time.time()
        deadline = started + timeout
        queue_drained_at: float | None = None
        slot_drained_at: float | None = None
        target = 0
        snapshot: dict | None = None
        while time.time() < deadline:
            try:
                snapshot = runtime_snapshot(self.dbname)
            except AssertionError:  # pragma: no cover - teardown race
                return {"status": "not_run", "error": "the trial database became unreadable"}
            target = int(snapshot["cancelling_with_live_attempt"])
            if (
                queue_drained_at is None
                and snapshot["top_queued"] == 0
                and snapshot["child_queued"] == 0
            ):
                queue_drained_at = time.time()
            if queue_drained_at is not None and snapshot["execution_slots_used"] <= target:
                slot_drained_at = time.time()
                break
            time.sleep(0.2)
        used = int(snapshot["execution_slots_used"]) if snapshot else None
        return {
            "timeout_s": timeout,
            "queue_drained_seconds": (
                round(queue_drained_at - started, 3) if queue_drained_at else None
            ),
            "slot_drained_seconds": (
                round(slot_drained_at - started, 3) if slot_drained_at else None
            ),
            "total_seconds": round((slot_drained_at or time.time()) - started, 3),
            "execution_slots_used_at_end": used,
            "slot_target": target,
            "slot_target_note": (
                "0 = all execution slots free; a non-zero target means only the "
                "documented stuck cancelling Run attempt(s) still hold a slot"
            ),
            "queue_drained": queue_drained_at is not None,
            "slot_drained": slot_drained_at is not None,
            "final": {
                "top_queued": snapshot["top_queued"] if snapshot else None,
                "child_queued": snapshot["child_queued"] if snapshot else None,
                "live_attempts": snapshot["live_attempts"] if snapshot else None,
                "cancelling_with_live_attempt": target,
            },
        }

    def _join_threads(self, *, timeout: float) -> None:
        deadline = time.time() + timeout
        for thread in self._threads:
            remaining = max(0.0, deadline - time.time())
            thread.join(timeout=remaining)
        with self._proc_lock:
            leftovers: list[subprocess.Popen] = []
            for process in self._live_procs.values():
                if process.poll() is None:
                    process.terminate()
                leftovers.append(process)
        for process in leftovers:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()

    # -- metrics ------------------------------------------------------------
    def _collect_metrics(self) -> dict:
        # The measurement window is the requested sustained interval; work the
        # executors finish while draining after the window is not counted.
        window = (self.started, self.window_stop)
        counts = _run_counts(self.dbname, window)
        stats = _queue_execution_stats(self.dbname, window)
        attempt_stats = _attempt_stats(self.dbname, window)
        concurrency = _peak_concurrency(self.dbname, window)
        operations = _operation_stats(self.dbname, window)
        errors = _error_facts(self.dbname, window)
        events = _event_artifacts(self.dbname, window)
        finalize = _finalize_facts(self.dbname, window)
        inputs = _input_facts(self.dbname, window)
        children = self._child_metrics()
        cancel = self._cancel_metrics()
        first_cancel_at = (
            self.cancel["batches"][0]["at"] if self.cancel["batches"] else self.settings.seconds
        )
        resources = _summarize_samples(
            self.samples, self.settings.seconds, max(first_cancel_at, 10.0)
        )
        samples = {
            "interval_s": self.settings.sample_interval,
            "samples": self.samples,
        }
        findings = []
        if cancel["stuck_count"]:
            findings.append(
                {
                    "id": "CANCEL-RACE-SLOT-LEAK",
                    "observed": (
                        f"{cancel['stuck_count']} cancelled Run(s) stayed in cancelling with a "
                        "live attempt after the 30s stop timeout; finished_at never committed"
                    ),
                    "mechanism": (
                        "cancel intent lands while the model capability call is in flight; the "
                        "post-call execution-bearing artifact/result call is refused with 409 "
                        "run_state_conflict, the worker's failed proposal is refused for the same "
                        "reason, and the worker returns without confirming its exit"
                    ),
                    "impact": "the execution slot stays allocated and the Run never commits a stop",
                    "evidence": cancel["stuck"],
                    "owner": "F03/T036 cancel arbitration + F04B worker failure path (not T114)",
                }
            )
        return {
            "label": self.settings.label,
            "started_at": _ts(self.started),
            "stopped_at": _ts(self.stopped),
            "seconds": round(self.window_stop - self.started, 2),
            "window": {
                "started_epoch": self.started,
                "stopped_epoch": self.window_stop,
                "load_stopped_epoch": self.stopped,
                "drain_seconds": round(
                    max(0.0, getattr(self, "drained_at", self.stopped) - self.window_stop), 2
                ),
            },
            "counts": counts,
            "drain": self.drain,
            "queue_and_execution": stats,
            "attempts": attempt_stats,
            "concurrency": concurrency,
            "operations": operations,
            "errors": errors,
            "events_artifacts": events,
            "finalize": finalize,
            "inputs": {**self.seed.inputs(), **inputs},
            "children": children,
            "cancel": cancel,
            "api": {
                "submit": _percentiles(self.submit_latencies),
                "submit_failures": self.submit_failures[:20],
                "get_run": _percentiles(self.get_latencies),
                "get_run_failures": self.get_latencies_failures[:20],
                "sse": self.sse,
            },
            "executor": {
                "processes_spawned": self.exec_records["processes"],
                "executed": self.exec_records["executed"],
                "idle": self.exec_records["idle"],
                "failed": self.exec_records["failed"],
                "outcomes": self.exec_records["outcomes"],
                "duration_s": _percentiles(self.exec_records["durations_s"]),
                "failures": self.exec_records["failures"],
                "process_failures": self.exec_records["process_failures"],
                "finalizer_tail": self.finalize_events,
            },
            "resources": resources,
            "environment": {
                "service_time_ms": SERVICE_TIME_MS,
                "stub_service_delay_env": STUB_SERVICE_DELAY_ENV,
                "profile": "capacity-stub",
                "models": "deterministic stub provider (no vendor endpoint or credential)",
                "sandbox": (
                    "WSL2/runsc is available but the stand-in graph executes no python "
                    "node: only the model node and the call_app child run"
                ),
            },
            "boundary": self.boundary,
            "findings": findings,
            "samples": samples,
        }

    def _child_metrics(self) -> dict:
        parents = [
            entry for entry in self.child_parent_submits if entry.get("run_id")
        ]
        result = {
            "parents_submitted": len(self.child_parent_submits),
            "submit_errors": [e for e in self.child_parent_submits if e.get("error")],
            "parents": [],
        }
        if not parents:
            result["summary"] = {"status": "not_run", "reason": "no parent submitted"}
            return result
        parent_ids = [entry["run_id"] for entry in parents]
        rows = st.sql_rows(
            self.dbname,
            "SELECT r.id::text AS id, r.state, r.created_at::text AS created_at, "
            "r.finished_at::text AS finished_at, "
            "(SELECT count(*) FROM platform.runs c WHERE c.parent_run_id = r.id) AS children, "
            "(SELECT count(*) FROM platform.runs c WHERE c.parent_run_id = r.id"
            "   AND c.state = 'succeeded') AS children_succeeded, "
            "(SELECT count(*) FROM platform.runs c WHERE c.parent_run_id = r.id"
            "   AND c.state = 'failed') AS children_failed, "
            "(SELECT count(*) FROM platform.run_attempts a JOIN platform.runs c"
            "   ON c.id = a.run_id WHERE c.parent_run_id = r.id) AS child_attempts, "
            "(SELECT w.created_at::text FROM platform.run_waitings w WHERE w.run_id = r.id) AS waiting_at, "
            "(SELECT w.decision_at::text FROM platform.run_waitings w WHERE w.run_id = r.id) AS decision_at "
            "FROM platform.runs r "
            f"WHERE r.id = ANY(ARRAY[{', '.join(st.quote_literal(p) + '::uuid' for p in parent_ids)}]) "
            "ORDER BY r.created_at",
        )
        waits: list[float] = []
        for row in rows:
            parent = dict(row)
            if row["waiting_at"] and row["decision_at"]:
                wait_start = _parse_ts(row["waiting_at"])
                wait_end = _parse_ts(row["decision_at"])
                parent["wait_seconds"] = round(wait_end - wait_start, 3)
                waits.append(parent["wait_seconds"])
            else:
                parent["wait_seconds"] = None
            result["parents"].append(parent)
        child_stats = st.sql_rows(
            self.dbname,
            "WITH kids AS (SELECT c.* FROM platform.runs c "
            " WHERE c.parent_run_id = ANY(ARRAY["
            + ", ".join(st.quote_literal(p) + "::uuid" for p in parent_ids)
            + "])), a AS (SELECT ra.run_id, min(ra.started_at) AS started_at "
            " FROM platform.run_attempts ra JOIN kids ON kids.id = ra.run_id GROUP BY ra.run_id) "
            "SELECT count(*) AS children, count(a.run_id) AS executed, "
            "percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM a.started_at - kids.created_at)) AS queue_p50_s, "
            "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM a.started_at - kids.created_at)) AS queue_p95_s "
            "FROM kids LEFT JOIN a ON a.run_id = kids.id",
        )
        result["summary"] = {
            "children": child_stats[0]["children"] if child_stats else 0,
            "children_executed": child_stats[0]["executed"] if child_stats else 0,
            "queue_p50_s": round(float(child_stats[0]["queue_p50_s"]), 4)
            if child_stats and child_stats[0]["queue_p50_s"] is not None
            else None,
            "queue_p95_s": round(float(child_stats[0]["queue_p95_s"]), 4)
            if child_stats and child_stats[0]["queue_p95_s"] is not None
            else None,
            "parents_completed": sum(
                1 for row in rows if row["state"] == "succeeded"
            ),
            "parent_wait_s": _percentiles(waits),
        }
        return result

    def _cancel_metrics(self) -> dict:
        batch_rows = []
        intent_to_stop: list[float] = []
        queued_immediate: list[float] = []
        stuck: list[dict] = []
        for batch in self.cancel["batches"]:
            for item in batch["executing"] + batch["queued"]:
                row = {
                    "run_id": item["run_id"],
                    "state_before": item["state_before"],
                    "state_after_api": item.get("state_after_api"),
                    "api_seconds": item.get("api_seconds"),
                    "api_status": item.get("status"),
                    "state": item.get("state"),
                    "cancel_requested_at": item.get("cancel_requested_at"),
                    "finished_at": item.get("finished_at"),
                    "live_attempts": item.get("live_attempts"),
                    "stopped": item.get("stopped"),
                }
                if item.get("cancel_requested_at") and item.get("finished_at"):
                    intent = _parse_ts(item["cancel_requested_at"])
                    stop = _parse_ts(item["finished_at"])
                    row["intent_to_stop_s"] = round(stop - intent, 3)
                    if item["state_before"] == "running":
                        intent_to_stop.append(stop - intent)
                    else:
                        queued_immediate.append(stop - intent)
                if item.get("stuck_after_stop_timeout"):
                    stuck.append(row)
                batch_rows.append(row)
        return {
            "batches": self.cancel["batches"],
            "rows": batch_rows,
            "intent_to_stop_s": _percentiles(intent_to_stop),
            "queued_intent_to_terminal_s": _percentiles(queued_immediate),
            "stopped": sum(1 for row in batch_rows if row["stopped"]),
            "stuck": stuck,
            "stuck_count": len(stuck),
            "late_result": self.cancel["late_result"],
            "note": (
                "cancel_requested_at is the server clock of the persisted intent; "
                "finished_at is the stop the product actually committed. The API "
                "response time is client-measured and reported per row. A Run left "
                "in cancelling with a live attempt is reported as stuck, never "
                "counted as stopped."
            ),
        }


# ---------------------------------------------------------------------------
# SQL metric helpers
# ---------------------------------------------------------------------------
def _select_runs(
    dbname: str, state: str, limit: int, started: float, *, phase: str | None = None
) -> list[tuple[str, str]]:
    phase_clause = f" AND phase = {st.quote_literal(phase)}" if phase else ""
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state FROM platform.runs "
        f"WHERE parent_run_id IS NULL AND state = {st.quote_literal(state)} "
        f"{phase_clause} "
        f"AND created_at >= {st.quote_literal(_ts(started))}::timestamptz "
        f"ORDER BY random() LIMIT {int(limit)}",
    )
    return [(row["id"], row["state"]) for row in rows]


def _cancel_rows(dbname: str, run_ids: list[str]) -> list[dict]:
    if not run_ids:
        return []
    values = ", ".join(st.quote_literal(run_id) + "::uuid" for run_id in run_ids)
    return st.sql_rows(
        dbname,
        "SELECT r.id::text AS run_id, r.state, r.error_code, "
        "r.cancel_requested_at::text AS cancel_requested_at, "
        "r.finished_at::text AS finished_at, "
        "(SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = r.id) AS attempts, "
        # S4: a cancelled Run that still owns an unexited/unrevoked attempt is a
        # leaked execution slot; the assertion reads this independent fact.
        "(SELECT count(*) FROM platform.run_attempts a WHERE a.run_id = r.id "
        "AND a.exited_at IS NULL AND a.revoked_at IS NULL) AS live_attempts "
        "FROM platform.runs r "
        f"WHERE r.id = ANY(ARRAY[{values}])",
    )


def _parse_ts(value: str) -> float:
    text = value.replace("Z", "+00:00")
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def _run_counts(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT CASE WHEN r.parent_run_id IS NULL THEN 'top_level' ELSE 'child' END AS pool, "
        "count(*) AS total, "
        "count(*) FILTER (WHERE r.state = 'queued') AS queued, "
        "count(*) FILTER (WHERE r.state = 'running') AS running, "
        "count(*) FILTER (WHERE r.state = 'waiting_children') AS waiting_children, "
        "count(*) FILTER (WHERE r.state = 'cancelling') AS cancelling, "
        "count(*) FILTER (WHERE r.state = 'recovery_pending') AS recovery_pending, "
        "count(*) FILTER (WHERE r.state = 'needs_reconciliation') AS needs_reconciliation, "
        "count(*) FILTER (WHERE r.state = 'succeeded') AS succeeded, "
        "count(*) FILTER (WHERE r.state = 'failed') AS failed, "
        "count(*) FILTER (WHERE r.state = 'cancelled') AS cancelled "
        "FROM platform.runs r "
        f"WHERE {_window(start, stop)} GROUP BY 1 ORDER BY 1",
    )
    return {row.pop("pool"): row for row in rows}


def _queue_execution_stats(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "WITH w AS ("
        "  SELECT r.id, r.parent_run_id IS NULL AS top, r.created_at, r.state,"
        "         CASE WHEN r.finished_at <= "
        f"{st.quote_literal(_ts(stop))}::timestamptz THEN r.finished_at END AS finished_at"
        f"  FROM platform.runs r WHERE {_window(start, stop)}"
        "), a AS ("
        "  SELECT ra.run_id, min(ra.started_at) AS started_at, max(ra.exited_at) AS exited_at,"
        "         count(*) AS attempts"
        "  FROM platform.run_attempts ra JOIN w ON w.id = ra.run_id GROUP BY ra.run_id"
        ") "
        "SELECT CASE WHEN w.top THEN 'top_level' ELSE 'child' END AS pool, "
        "count(*) AS runs, count(a.run_id) AS executed_runs, "
        "coalesce(sum(a.attempts), 0) AS attempts, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (a.started_at - w.created_at))) AS queue_p50_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (a.started_at - w.created_at))) AS queue_p95_s, "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (a.started_at - w.created_at))) AS queue_p99_s, "
        "max(EXTRACT(epoch FROM (a.started_at - w.created_at))) AS queue_max_s, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (w.finished_at - a.started_at))) "
        "  FILTER (WHERE w.finished_at IS NOT NULL AND a.started_at IS NOT NULL) AS exec_p50_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (w.finished_at - a.started_at))) "
        "  FILTER (WHERE w.finished_at IS NOT NULL AND a.started_at IS NOT NULL) AS exec_p95_s, "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (w.finished_at - a.started_at))) "
        "  FILTER (WHERE w.finished_at IS NOT NULL AND a.started_at IS NOT NULL) AS exec_p99_s, "
        "max(EXTRACT(epoch FROM (w.finished_at - a.started_at))) "
        "  FILTER (WHERE w.finished_at IS NOT NULL AND a.started_at IS NOT NULL) AS exec_max_s "
        "FROM w LEFT JOIN a ON a.run_id = w.id GROUP BY w.top ORDER BY 1",
    )
    result = {}
    for row in rows:
        pool = row.pop("pool")
        result[pool] = {
            key: (round(float(value), 4) if isinstance(value, (int, float)) else value)
            for key, value in row.items()
        }
        for key in ("runs", "executed_runs", "attempts"):
            if result[pool].get(key) is not None:
                result[pool][key] = int(result[pool][key])
    return result


def _attempt_stats(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT CASE WHEN r.parent_run_id IS NULL THEN 'top_level' ELSE 'child' END AS pool, "
        "count(*) AS attempts, "
        "count(*) FILTER (WHERE ra.exited_at IS NULL AND ra.revoked_at IS NULL) AS still_live, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (ra.exited_at - ra.started_at))) AS p50_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (ra.exited_at - ra.started_at))) AS p95_s, "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (ra.exited_at - ra.started_at))) AS p99_s, "
        "max(EXTRACT(epoch FROM (ra.exited_at - ra.started_at))) AS max_s "
        "FROM platform.run_attempts ra JOIN platform.runs r ON r.id = ra.run_id "
        f"WHERE {_window(start, stop)} GROUP BY 1 ORDER BY 1",
    )
    result = {}
    for row in rows:
        pool = row.pop("pool")
        result[pool] = {
            key: (round(float(value), 4) if isinstance(value, (int, float)) else value)
            for key, value in row.items()
        }
        for key in ("attempts", "still_live"):
            if result[pool].get(key) is not None:
                result[pool][key] = int(result[pool][key])
    return result


def _peak_concurrency(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT r.parent_run_id IS NULL AS top, ra.started_at::text AS started_at, "
        "least(coalesce(ra.exited_at, ra.revoked_at, now()), "
        f"{st.quote_literal(_ts(stop))}::timestamptz)::text AS ended_at "
        "FROM platform.run_attempts ra JOIN platform.runs r ON r.id = ra.run_id "
        f"WHERE {_window(start, stop)}",
    )
    top_events: list[tuple[float, int]] = []
    child_events: list[tuple[float, int]] = []
    child_intervals: list[tuple[float, float]] = []
    for row in rows:
        started = _parse_ts(row["started_at"])
        ended = _parse_ts(row["ended_at"])
        if ended < started:
            ended = started
        if row["top"]:
            top_events.extend(((started, 1), (ended, -1)))
        else:
            child_events.extend(((started, 1), (ended, -1)))
            child_intervals.append((started, ended))

    def sweep(events: list[tuple[float, int]]) -> dict:
        # Ties are ordered "finish before start": two intervals that meet exactly
        # at one timestamp are not simultaneously live. This is the conservative
        # reading and it matches the slot allocation (one slot handed over).
        ordered = sorted(events, key=lambda item: (item[0], item[1]))
        current = 0
        peak = 0
        peak_at = None
        saturated: list[tuple[float, float]] = []
        saturated_start = None
        for index, (at, delta) in enumerate(ordered):
            current += delta
            if current > peak:
                peak = current
                peak_at = at
            following = ordered[index + 1][0] if index + 1 < len(ordered) else None
            if current >= TOP_LEVEL_SLOTS and saturated_start is None:
                saturated_start = at
            elif current < TOP_LEVEL_SLOTS and saturated_start is not None:
                saturated.append((saturated_start, at))
                saturated_start = None
            if following is None and saturated_start is not None:
                saturated.append((saturated_start, at))
                saturated_start = None
        total = ordered[-1][0] - ordered[0][0] if ordered else 0.0
        return {
            "peak": peak,
            "peak_at": _ts(peak_at) if peak_at is not None else None,
            "events": len(events),
            "observed_span_s": round(total, 2),
            "saturated_intervals": [
                {"from": _ts(left), "to": _ts(right), "seconds": round(right - left, 2)}
                for left, right in saturated
            ],
            "saturated_seconds": round(sum(right - left for left, right in saturated), 2),
        }

    top = sweep(top_events)
    child = sweep(child_events)
    participants = []
    if top["peak_at"] is not None:
        participants = st.sql_rows(
            dbname,
            "SELECT r.id::text AS run_id, r.parent_run_id IS NULL AS top, r.state, r.phase, "
            "ra.started_at::text AS started_at, "
            "coalesce(ra.exited_at, ra.revoked_at)::text AS ended_at "
            "FROM platform.run_attempts ra JOIN platform.runs r ON r.id = ra.run_id "
            f"WHERE {_window(start, stop)} "
            f"AND ra.started_at <= {st.quote_literal(top['peak_at'])}::timestamptz "
            f"AND coalesce(ra.exited_at, ra.revoked_at, now()) > {st.quote_literal(top['peak_at'])}::timestamptz "
            "ORDER BY ra.started_at",
        )
        participants = [
            {
                "run": row["run_id"][:8],
                "top": row["top"],
                "state": row["state"],
                "phase": row["phase"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
            }
            for row in participants
        ]
    saturated = top["saturated_intervals"]
    overlapping_children = 0
    for left, right in child_intervals:
        for sat_left, sat_right in (
            (_parse_ts(item["from"]), _parse_ts(item["to"])) for item in saturated
        ):
            if left < sat_right and sat_left < right:
                overlapping_children += 1
                break
    return {
        "top_level": top,
        "child": child,
        "peak_participants": participants,
        "child_attempts_overlapping_saturated_top_level": overlapping_children,
        "sweep_note": (
            "independent truth: every live attempt interval is swept; the peak is the "
            "maximum number of attempts that were live at the same instant. The "
            "authoritative capacity is platform.quota_counters.used, which is capped "
            "by the claim transaction; a 1-attempt peak excess is reported with its "
            "participants instead of being hidden."
        ),
    }


def _operation_stats(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT count(*) AS calls, "
        "count(*) FILTER (WHERE oa.state = 'succeeded') AS succeeded, "
        "count(*) FILTER (WHERE oa.state = 'failed') AS failed, "
        "count(*) FILTER (WHERE oa.state IN ('prepared', 'dispatching')) AS incomplete, "
        "coalesce(sum(oa.charged_amount), 0)::text AS charged_total, "
        "coalesce(sum(oa.tokens_in), 0) AS tokens_in, "
        "coalesce(sum(oa.tokens_out), 0) AS tokens_out, "
        "min(EXTRACT(epoch FROM (oa.finished_at - oa.created_at))) AS call_min_s, "
        "percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (oa.finished_at - oa.created_at))) AS call_p50_s, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (oa.finished_at - oa.created_at))) AS call_p95_s, "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY EXTRACT(epoch FROM (oa.finished_at - oa.created_at))) AS call_p99_s, "
        "max(EXTRACT(epoch FROM (oa.finished_at - oa.created_at))) AS call_max_s "
        "FROM platform.operation_attempts oa "
        "JOIN platform.operations o ON o.id = oa.operation_id "
        "JOIN platform.runs r ON r.id = o.run_id "
        f"WHERE {_window(start, stop)}",
    )
    row = rows[0] if rows else {}
    return {
        key: (round(float(value), 6) if isinstance(value, (int, float)) else value)
        for key, value in row.items()
    }


def _error_facts(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    runs = st.sql_rows(
        dbname,
        "SELECT r.state, r.error_code, count(*) AS count FROM platform.runs r "
        f"WHERE {_window(start, stop)} AND r.error_code IS NOT NULL "
        "GROUP BY 1, 2 ORDER BY 3 DESC",
    )
    operations = st.sql_rows(
        dbname,
        "SELECT oa.error_code, count(*) AS count FROM platform.operation_attempts oa "
        "JOIN platform.operations o ON o.id = oa.operation_id "
        "JOIN platform.runs r ON r.id = o.run_id "
        f"WHERE {_window(start, stop)} AND oa.error_code IS NOT NULL GROUP BY 1 ORDER BY 2 DESC",
    )
    return {"runs": runs, "operation_attempts": operations}


def _event_artifacts(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    start_ts = st.quote_literal(_ts(start))
    stop_ts = st.quote_literal(_ts(stop))
    events = st.sql_rows(
        dbname,
        "SELECT type, count(*) AS count FROM platform.run_events "
        f"WHERE created_at >= {start_ts}::timestamptz AND created_at < {stop_ts}::timestamptz "
        "GROUP BY 1 ORDER BY 2 DESC",
    )
    artifacts = st.sql_rows(
        dbname,
        "SELECT count(*) AS artifacts, coalesce(sum(size_bytes), 0) AS bytes, "
        "count(*) FILTER (WHERE state = 'ready') AS ready "
        "FROM platform.artifacts "
        f"WHERE created_at >= {start_ts}::timestamptz AND created_at < {stop_ts}::timestamptz",
    )
    return {"events": events, "artifacts": artifacts[0] if artifacts else {}}


def _finalize_facts(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT state, count(*) AS count FROM platform.background_jobs "
        "WHERE kind = 'artifact_finalize' "
        f"AND created_at >= {st.quote_literal(_ts(start))}::timestamptz "
        f"AND created_at < {st.quote_literal(_ts(stop))}::timestamptz GROUP BY 1 ORDER BY 1",
    )
    return {"jobs_created": rows}


def _input_facts(dbname: str, window: tuple[float, float]) -> dict:
    start, stop = window
    rows = st.sql_rows(
        dbname,
        "SELECT count(DISTINCT r.manifest_sha256) AS distinct_manifests, "
        "min(r.manifest_sha256) AS manifest_sha256, "
        "count(DISTINCT r.execution_snapshot_id) AS executions_snapshots, "
        "count(DISTINCT r.input_artifact_id) AS input_artifacts "
        "FROM platform.runs r "
        f"WHERE {_window(start, stop)}",
    )
    return rows[0] if rows else {}


def _summarize_samples(samples: list[dict], seconds: float, steady_end: float) -> dict:
    # Only the sustained window counts: the drain after it has no load and must
    # not dilute the saturation fraction. The steady-state measures stop at the
    # cancellation probe (near the end of the window), because a cancelled Run
    # that the product leaves cancelling holds its slot and is a separate finding.
    samples = [
        sample
        for sample in samples
        if isinstance(sample.get("at"), (int, float)) and sample["at"] <= seconds + 0.5
    ]
    steady = {
        "from_s": 10.0,
        "to_s": round(min(steady_end, seconds), 2),
    }
    steady_samples = [
        sample for sample in samples if steady["from_s"] <= sample["at"] <= steady["to_s"]
    ]

    def series(*path: str) -> list[float]:
        values: list[float] = []
        for sample in samples:
            node = sample
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, (int, float)):
                values.append(float(node))
        return values

    def series_of(rows: list[dict], *path: str) -> list[float]:
        values: list[float] = []
        for sample in rows:
            node = sample
            for key in path:
                node = node.get(key) if isinstance(node, dict) else None
            if isinstance(node, (int, float)):
                values.append(float(node))
        return values

    top_running = series("top_running")
    top_executing = series("top_executing")
    top_finalizing = series("top_finalizing")
    top_queued = series("top_queued")
    child_running = series("child_running")
    live_attempts = series("live_top_attempts")
    slots_used = series("execution_slots_used")
    slots_capacity = series("execution_slots_capacity")
    finalize_ready = series("finalize_ready")
    steady_top_running = series_of(steady_samples, "top_running")
    steady_queued = series_of(steady_samples, "top_queued")
    api_rss = series("processes", "api_rss_mb")
    api_cpu = series("processes", "api_cpu_pct")
    fin_rss = series("processes", "finalizer_rss_mb")
    sup_count = series("processes", "supervisor_processes")
    sup_rss = series("processes", "supervisor_rss_mb")
    disk_free = series("disk_free_gb")
    pg_connections = series("pg", "connections")
    pg_active = series("pg", "active")
    db_mb = [value / 1024**2 for value in series("pg", "database_bytes")]

    def fraction_at_least(values: list[float], threshold: float) -> float:
        return round(sum(1 for value in values if value >= threshold) / max(len(values), 1), 4)

    return {
        "samples": len(samples),
        "steady_state": {
            **steady,
            "samples": len(steady_samples),
            "top_running": _percentiles(steady_top_running),
            "top_queued": _percentiles(steady_queued),
            "fraction_with_all_slots_running": fraction_at_least(
                steady_top_running, TOP_LEVEL_SLOTS
            ),
            "fraction_with_at_least_19_running": fraction_at_least(
                steady_top_running, TOP_LEVEL_SLOTS - 1
            ),
            "mean_utilization": round(
                statistics.fmean(steady_top_running) / TOP_LEVEL_SLOTS, 4
            )
            if steady_top_running
            else None,
        },
        "top_running": _percentiles(top_running),
        "top_executing": _percentiles(top_executing),
        "top_finalizing": _percentiles(top_finalizing),
        "top_queued": _percentiles(top_queued),
        "child_running": _percentiles(child_running),
        "live_top_attempts": _percentiles(live_attempts),
        "execution_slots_used": _percentiles(slots_used),
        "execution_slots_capacity": _percentiles(slots_capacity),
        "finalize_ready": _percentiles(finalize_ready),
        "samples_at_top_level_saturation": sum(
            1 for value in top_running if value >= TOP_LEVEL_SLOTS
        ),
        "saturation_fraction": fraction_at_least(top_running, TOP_LEVEL_SLOTS),
        "queued_with_live_attempt_max": max(series("queued_with_live_attempt"), default=0),
        "api_rss_mb": _percentiles(api_rss),
        "api_cpu_pct": _percentiles(api_cpu),
        "finalizer_rss_mb": _percentiles(fin_rss),
        "supervisor_processes": _percentiles(sup_count),
        "supervisor_rss_mb_total": _percentiles(sup_rss),
        "disk_free_gb": {"min": min(disk_free) if disk_free else None,
                         "max": max(disk_free) if disk_free else None},
        "pg_connections": _percentiles(pg_connections),
        "pg_active": _percentiles(pg_active),
        "database_mb": {"max": round(max(db_mb), 1) if db_mb else None},
        "not_run": [
            "GPU utilisation (no GPU on the host)",
            "network throughput of the loopback API (not measured by this harness)",
            "real vendor model latency/cost (stub profile; no vendor endpoint or credential)",
        ],
    }


def _tail_lines(path: Path, count: int) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:  # pragma: no cover
        return []
    return text.splitlines()[-count:]


# ---------------------------------------------------------------------------
# trial invariants shared by the short profile and the full measurement
# ---------------------------------------------------------------------------
def assert_trial(result: dict, *, expect_seconds: float) -> None:
    label = result["label"]
    counts = result["counts"]
    top = counts.get("top_level", {})
    assert top.get("total", 0) >= TOP_LEVEL_SLOTS, (label, counts)
    # AC1: 20 top-level executions really executing, proven twice: the sampled
    # state histogram / the authoritative slot counter (exactly 20) and the
    # attempt-interval sweep (>= 20 simultaneous live attempts).
    concurrency = result["concurrency"]["top_level"]
    assert concurrency["peak"] >= TOP_LEVEL_SLOTS, (
        f"{label}: peak simultaneous top-level attempts was {concurrency['peak']} "
        f"at {concurrency['peak_at']}, expected at least {TOP_LEVEL_SLOTS}"
    )
    resources = result["resources"]
    assert resources["top_running"]["max"] == TOP_LEVEL_SLOTS, (
        f"{label}: sampled top-level running peak {resources['top_running']}"
    )
    assert resources["execution_slots_used"]["max"] == TOP_LEVEL_SLOTS, (
        f"{label}: the execution slot counter exceeded its configured capacity: "
        f"{resources['execution_slots_used']}"
    )
    assert resources["samples_at_top_level_saturation"] > 0, (
        f"{label}: never sampled 20 simultaneous top-level executions"
    )
    steady = resources["steady_state"]
    assert steady["fraction_with_at_least_19_running"] >= 0.5, (
        f"{label}: the steady-state pool was not kept nearly full: {steady}"
    )
    assert steady["mean_utilization"] >= 0.7, (
        f"{label}: mean steady-state slot utilization was {steady['mean_utilization']}: {steady}"
    )
    # Queueing is not execution: no queued Run ever held a live attempt, and a
    # queue really existed while the slots were full.
    assert resources["queued_with_live_attempt_max"] == 0, (
        f"{label}: a queued Run held a live attempt: {resources}"
    )
    assert resources["top_queued"]["max"] > 0, (
        f"{label}: the queue never formed, so the run was not capacity-bound"
    )
    # Every queued Run is submit-then-claim; the measured service time is the
    # capacity-stub stand-in, not vendor latency.
    operations = result["operations"]
    assert operations.get("calls", 0) > 0, (label, operations)
    assert operations["call_p50_s"] is not None
    assert 0.45 <= operations["call_p50_s"] <= 0.9, (
        f"{label}: stub service p50 was {operations['call_p50_s']}s, expected ~0.5s"
    )
    calls = max(int(operations.get("calls") or 1), 1)
    failed_calls = int(operations.get("failed") or 0)
    assert failed_calls / calls < 0.01, (
        f"{label}: {failed_calls}/{calls} stand-in calls failed: {operations}"
    )
    # The stand-in calls and the top-level attempts really executed.
    top_executed = result["queue_and_execution"].get("top_level", {}).get("executed_runs", 0)
    assert top_executed >= TOP_LEVEL_SLOTS * 2, (label, result["queue_and_execution"])
    # AC2 (child pool): the independent child pool progressed during saturation.
    child = result["children"]["summary"]
    assert child.get("children", 0) > 0 and child.get("children_executed", 0) > 0, (
        label,
        result["children"],
    )
    assert result["concurrency"]["child_attempts_overlapping_saturated_top_level"] > 0, (
        f"{label}: no child attempt overlapped a 20-slot-saturated moment: "
        f"{result['concurrency']}"
    )
    assert result["concurrency"]["child"]["peak"] <= CHILD_SLOTS, (
        f"{label}: the child pool admitted more than {CHILD_SLOTS}: {result['concurrency']}"
    )
    # AC5 (S1): no submitted task may be silently lost. Every submit must have
    # been accepted by the real API, and no executor failure may be unexplained:
    # the previous harness recorded both lists but never asserted them.
    submit_failures = result["api"]["submit_failures"]
    assert submit_failures == [], (label, submit_failures[:5])
    process_failures = result["executor"]["process_failures"]
    assert process_failures == [], (label, process_failures[:5])
    executor_failures = result["executor"]["failures"]
    assert executor_failures == [], (
        f"{label}: {len(executor_failures)} executor failure(s) without a green product "
        f"outcome: {executor_failures[:5]}"
    )

    # AC4 (S4): cancel intent and stop time are distinct, measured facts, and the
    # assertion now binds the executing-cancel case: every Run whose cancel intent
    # was persisted while it was executing must commit `cancelled` with a
    # finished_at inside the bounded stop wait and must release its execution slot
    # (no live attempt). `stopped > 0` alone was satisfiable by the queued cancels
    # even while executing cancels leaked slots.
    cancel = result["cancel"]
    assert cancel["batches"], f"{label}: no cancel batch ran"
    assert cancel["stuck"] == [] and cancel["stuck_count"] == 0, (
        f"{label}: {cancel['stuck_count']} cancelled Run(s) never reached a terminal "
        f"state within the bounded stop wait: {cancel['stuck']}"
    )
    executing_rows = [row for row in cancel["rows"] if row["state_before"] == "running"]
    assert executing_rows, (label, cancel["rows"])
    persisted_executing = [row for row in executing_rows if row["cancel_requested_at"]]
    assert persisted_executing, (
        f"{label}: no executing Run recorded a persisted cancel intent: {executing_rows}"
    )
    for row in persisted_executing:
        assert row["state"] == "cancelled", (
            f"{label}: an executing cancel did not commit cancelled: {row}"
        )
        assert row["finished_at"], (
            f"{label}: a cancelled executing Run has no stop time: {row}"
        )
        assert row.get("intent_to_stop_s") is not None and 0.0 <= row["intent_to_stop_s"] < 30.0, (
            f"{label}: cancel intent -> stop was not bounded: {row}"
        )
        assert int(row.get("live_attempts") or 0) == 0, (
            f"{label}: a cancelled Run still holds a live attempt (leaked slot): {row}"
        )
    # The queued-cancel shape is the clean immediate one: the intent and the stop
    # come from the same cancel transaction's clock, and no attempt may be live.
    # A Run claimed between selection and the cancel API call is an executing
    # cancel and is held to the general rule below instead.
    queued_rows = [row for row in cancel["rows"] if row["state_before"] == "queued"]
    assert queued_rows, (label, cancel["rows"])
    for row in queued_rows:
        if not row["cancel_requested_at"]:
            continue
        if row.get("state_after_api") == "cancelling":
            continue
        assert row["state"] == "cancelled", (
            f"{label}: a queued cancel did not commit cancelled: {row}"
        )
        assert row["finished_at"] == row["cancel_requested_at"], (
            f"{label}: a queued cancel was not committed by the cancel transaction: {row}"
        )
        assert int(row.get("live_attempts") or 0) == 0, (label, row)
    # Every Run whose cancel intent was persisted (whether it was queued or
    # executing when the API was called) must be terminal `cancelled`, with a stop
    # time and no live attempt: a cancel may never leak a slot.
    for row in cancel["rows"]:
        if not row["cancel_requested_at"]:
            continue
        assert row["state"] == "cancelled", (label, row)
        assert row["finished_at"], (label, row)
        assert int(row.get("live_attempts") or 0) == 0, (
            f"{label}: a cancelled Run still holds a live attempt (leaked slot): {row}"
        )
    assert (
        cancel["queued_intent_to_terminal_s"].get("n", 0) > 0
        or cancel["intent_to_stop_s"].get("n", 0) > 0
    ), (label, cancel)
    if cancel["late_result"] is not None:
        # A late worker result for an already-terminal cancelled Run must never
        # revive it (the previous assertion also accepted `cancelling`).
        assert cancel["late_result"].get("state_after") == "cancelled", (
            label,
            cancel["late_result"],
        )
    # AC5 boundary probes really ran and asserted the documented outcomes.
    assert result["boundary"]["lease_expiry"].get("recovered") is True, (
        label,
        result["boundary"]["lease_expiry"],
    )
    assert result["boundary"]["ownership"].get("denied") is True, (
        label,
        result["boundary"]["ownership"],
    )
    arbitration = result["boundary"]["cancel_arbitration"]
    assert arbitration.get("cancelled") is True, (label, arbitration)
    assert arbitration.get("late_result_refused") is True, (label, arbitration)
    # AC3/AC6: the API latency and resource tables have real values.
    assert result["api"]["submit"]["n"] > 0, (label, result["api"])
    assert result["api"]["get_run"]["n"] > 0, (label, result["api"])
    assert resources["api_rss_mb"]["n"] > 0, (label, resources)
    assert resources["pg_connections"]["n"] > 0, (label, resources)


# ---------------------------------------------------------------------------
# evidence writer
# ---------------------------------------------------------------------------
def build_document(
    *,
    trials: list[dict],
    mode: str,
    profile: dict,
    quota: dict,
    version: dict,
    commands: list[str],
    notes: dict | None = None,
) -> dict:
    document = {
        "ticket": "T114",
        "issue": 115,
        "title": (
            "Sustained execution of 20 top-level stand-in tasks at a 100 user / "
            "1000 application profile, and the capacity it measures"
        ),
        "status": "passed",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": mode,
        "full_measurement": mode == "full",
        "short_profile": mode != "full",
        "environment": version,
        "profile": profile,
        "capacity_knob": quota,
        "service_time": {
            "stand_in": True,
            "vendor_latency": False,
            "configured_ms": SERVICE_TIME_MS,
            "knob": f"{STUB_SERVICE_DELAY_ENV} (capacity-stub profile only, stub provider only)",
            "measured_operation_call": {
                trial["label"]: {
                    "p50_s": trial["operations"].get("call_p50_s"),
                    "p95_s": trial["operations"].get("call_p95_s"),
                    "p99_s": trial["operations"].get("call_p99_s"),
                    "calls": trial["operations"].get("calls"),
                }
                for trial in trials
            },
        },
        "trials": trials,
        "trial_count": len(trials),
        "findings": [finding for trial in trials for finding in trial.get("findings", [])],
        "commands": commands,
        "not_run": [
            "a real vendor model: no credential/endpoint on this host (stub profile)",
            "a real capacity machine: this host is the 12-logical-CPU development machine",
            "GPU and network-throughput metrics",
            "multi-host distribution and load-balancer behaviour",
        ],
        "notes": notes or {},
    }
    if len(trials) != 3:
        document["status"] = "partial"
    return redact_value(document)


def write_evidence(document: dict) -> Path | None:
    if not write_evidence_enabled():
        return None
    text = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    leaks = find_violations(text)
    if leaks:  # pragma: no cover - must fail the evidence run
        raise AssertionError(f"credential-shaped values in capacity evidence: {leaks}")
    EVIDENCE_JSON.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_JSON.write_text(text, encoding="utf-8")
    return EVIDENCE_JSON


# ---------------------------------------------------------------------------
# the full measurement
# ---------------------------------------------------------------------------
def run_trial(
    stack,
    seed: ProfileSeed,
    quota: dict,
    *,
    label: str,
    seconds: float,
    progress=print,
) -> dict:
    settings = TrialSettings(label=label, seconds=seconds)
    trial = CapacityTrial(stack, seed, quota, settings)
    progress(
        f"[{label}] starting {seconds:.0f}s trial: profile={USERS}/{APPLICATIONS}, "
        f"execution_slots={quota['top_level_slots']}, child_slots={quota['child_pool_slots']}, "
        f"stub service time={SERVICE_TIME_MS}ms"
    )
    result = trial.run()
    debug_dir = os.environ.get("F13B_CAPACITY_DEBUG_DIR")
    if debug_dir:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        (Path(debug_dir) / f"{label}-last.json").write_text(
            json.dumps(redact_value(result), indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
    progress(
        f"[{label}] done: top_peak={result['concurrency']['top_level']['peak']} "
        f"executed={result['queue_and_execution']['top_level'].get('executed_runs')} "
        f"p50_call={result['operations'].get('call_p50_s')}s "
        f"p95_exec={result['queue_and_execution']['top_level'].get('exec_p95_s')}s"
    )
    return result


def run_full_measurement(
    *,
    tmp_path: Path,
    go_binaries: dict[str, Path],
    api_server_factory,
    service_time_ms: int = SERVICE_TIME_MS,
    progress=print,
) -> dict:
    version = version_facts()
    trials: list[dict] = []
    profile: dict | None = None
    quota: dict | None = None
    failures: list[dict] = []
    commands = [
        "python -m pytest tests/f13b/test_t114_capacity.py -q "
        "(the committed short profile; no capacity window, no evidence write)",
        "F13B_CAPACITY_FULL=1 F13B_WRITE_EVIDENCE=1 python -m pytest "
        "tests/f13b/test_t114_capacity.py -q  (this run: 15min x 3, exclusive window)",
        "python tools/pg_dev.py status",
        "UPDATE platform.quota_counters SET capacity = 20 "
        "WHERE scope = 'global' AND dimension = 'execution_slots' "
        "(also child_slots = 8; before/after values are in capacity_knob)",
    ]
    for index in range(1, 4):
        label = f"trial-{index}"
        stack = build_trial_stack(
            tmp_path=tmp_path / label,
            go_binaries=go_binaries,
            api_server_factory=api_server_factory,
            service_time_ms=service_time_ms,
        )
        try:
            seed = seed_profile(stack)
            quota = configure_quota(stack.dbname)
            if profile is None:
                profile = seed.as_evidence()
            result = run_trial(
                stack, seed, quota, label=label, seconds=FULL_TRIAL_SECONDS, progress=progress
            )
            result["trial_index"] = index
            result["profile_db_truth"] = profile_db_truth(stack.dbname)
            result["version"] = {
                "git_branch": version["git_branch"],
                "git_commit": version["git_commit"],
                "postgres": st.sql_scalar(stack.dbname, "SHOW server_version"),
            }
            try:
                assert_trial(result, expect_seconds=FULL_TRIAL_SECONDS)
                result["assertions_ok"] = True
            except AssertionError as exc:
                result["assertions_ok"] = False
                result["assertion_failure"] = str(exc)
                failures.append({"label": label, "failure": str(exc)})
                progress(f"[{label}] INVARIANT FAILURE: {exc}")
            trials.append(result)
            progress(f"[{label}] metrics collected")
        except Exception as exc:  # noqa: BLE001 - the measurement must keep its evidence
            failures.append(
                {
                    "label": label,
                    "failure": f"{type(exc).__name__}: {exc}",
                }
            )
            trials.append(
                {
                    "label": label,
                    "trial_index": index,
                    "assertions_ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            progress(f"[{label}] TRIAL ERROR: {type(exc).__name__}: {exc}")
        finally:
            stack.server.stop()
            st.drop_database(stack.dbname)
    document = build_document(
        trials=trials,
        mode="full",
        profile=profile or {},
        quota=quota or {},
        version=version,
        commands=commands,
        notes={
            "trials": (
                "three independent databases; each profile is seeded from the public "
                "endpoints and each trial is 15 minutes of sustained saturation"
            ),
            "duration": "45 minutes of measurement plus per-trial profile seeding",
            "failures": failures,
        },
    )
    if failures:
        document["status"] = "failed"
    return document


def assert_document(document: dict) -> None:
    """The full measurement's acceptance gate over the collected evidence."""
    assert document["status"] == "passed", document.get("notes", {}).get("failures")
    assert document["full_measurement"] is True
    assert document["trial_count"] == 3, document["trial_count"]
    assert document["capacity_knob"]["top_level_slots"] == TOP_LEVEL_SLOTS
    assert document["capacity_knob"]["child_pool_slots"] == CHILD_SLOTS
    assert document["profile"]["user_principal_count"] == USERS, document["profile"]
    assert document["profile"]["application_count"] == APPLICATIONS, document["profile"]
    for trial in document["trials"]:
        assert trial.get("assertions_ok") is True, trial.get("assertion_failure") or trial.get("error")
        assert trial["label"] in {"trial-1", "trial-2", "trial-3"}
        assert trial["seconds"] >= FULL_TRIAL_SECONDS * 0.98, trial["seconds"]
