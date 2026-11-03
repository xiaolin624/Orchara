"""F03/T038 real-stack helpers: conservative recovery.

T038 never takes a lost execution over automatically. A lease-expiry sweep parks a
Run in ``recovery_pending`` (job ``held``, allocation kept, token untouched); the
only way back to ``queued`` is the audited ``orchara-admin runs recover`` command
after the operator proved the old unit, its sandbox and its checkpoint database
sessions are gone.

Everything here is real: the T031/T036 stack (throwaway PostgreSQL migrated with
the compiled ``cmd/admin`` chain, the compiled API behind HTTP with the internal
mTLS listener, a real filesystem object store with HMAC-signed links) plus the
compiled ``cmd/admin`` binary for the two commands. The in-flight database write
test opens a real PostgreSQL session whose backend pid/backend_start is recorded
through the real heartbeat endpoint, then checks ``pg_stat_activity``.

Boundaries (documented in the T038 report):

* ``sweep-leases`` / ``recover`` are operator CLI surfaces, not public HTTP
  endpoints; the owner-facing page reads the resulting facts through the
  non-contract ``X-Run-Recovery-*`` headers.
* ``cmd/admin`` runs with ``ORCHARA_PROFILE`` + ``ORCHARA_DB_DSN`` +
  ``ORCHARA_OBJECT_DIR`` (the same environment convention as the API).
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from support import stack as st
from support import t029, t036

# Re-exports so a T038 test imports one module.
T038Stack = t036.T036Stack
Session = t036.Session
admin_session = t036.admin_session
session_for = t036.session_for
invite_and_activate = t036.invite_and_activate
new_uuid = t036.new_uuid
golden_owner = t036.golden_owner
minimal_owner = t036.minimal_owner
queued_run = t036.queued_run
claim_lease = t036.claim_lease
execution_client = t036.execution_client
exit_evidence = t036.exit_evidence
attempt_row = t036.attempt_row
attempts_for_run = t036.attempts_for_run
run_state_row = t036.run_state_row
job_row = t036.job_row
jobs_for_run = t036.jobs_for_run
quota_counter = t036.quota_counter
allocations_for_holder = t036.allocations_for_holder
allocation_for_job = t036.allocation_for_job
artifact_row = t036.artifact_row
seed_output_artifact = t036.seed_output_artifact
link_output = t036.link_output
wait_until = t036.wait_until
run_path = t036.run_path
run_events = t036.run_events
run_event_count = t036.run_event_count
latest_run_status = t036.latest_run_status
cancel_run = t036.cancel_run
snapshot_row = t029.snapshot_row

# Recovery vocabulary (mirrors internal/execution).
STATE_RECOVERY = "recovery_pending"
REASON_LEASE_EXPIRED = "lease_expired"
REASON_EXIT_WITHOUT_RESULT = "exit_without_result"
CODE_RUN_NOT_FOUND = "run_not_found"
CODE_NOT_RECOVERY_PENDING = "not_recovery_pending"
CODE_ALREADY_RECOVERED = "already_recovered"
CODE_OLD_EXECUTION_STILL_LIVE = "old_execution_still_live"
CODE_DATABASE_WRITES_IN_FLIGHT = "database_writes_in_flight"
CODE_ARTIFACT_UNAVAILABLE = "artifact_unavailable"
CODE_INPUT_DELETED = "input_deleted"
CODE_MANIFEST_HASH_MISMATCH = "manifest_hash_mismatch"
CODE_INPUT_HASH_MISMATCH = "input_hash_mismatch"
CODE_EVIDENCE_REQUIRED = "evidence_required"
CODE_EVIDENCE_INVALID = "evidence_invalid"
CODE_EVIDENCE_UNIT_MISMATCH = "evidence_unit_mismatch"
CODE_LIVE_ATTEMPT_CONFLICT = "live_attempt_conflict"
CODE_JOB_UNAVAILABLE = "job_unavailable"
CODE_NEEDS_RECONCILIATION = "needs_reconciliation"
CODE_ACTOR_NOT_FOUND = "actor_not_found"
CODE_CANCEL_REQUESTED = "cancel_requested"

# Non-contract Run-detail headers T038 adds.
RECOVERY_REASON_HEADER = "X-Run-Recovery-Reason"
RECOVERY_SINCE_HEADER = "X-Run-Recovery-Since"
RECOVERY_EVIDENCE_AT_HEADER = "X-Run-Recovery-Evidence-At"
RECOVERY_EVIDENCE_SHA_HEADER = "X-Run-Recovery-Evidence-SHA256"

# Audit actions.
AUDIT_PARKED = "run.recovery_parked"
AUDIT_RECOVERED = "run.recovery_recovered"

FIXED_OBSERVED_AT = "2026-11-03T02:15:04.123456Z"


# ---------------------------------------------------------------------------
# the real admin command (compiled binary, real DSN/object directory)
# ---------------------------------------------------------------------------
def admin_env(stack: T038Stack, *, object_dir: bool = True) -> dict[str, str]:
    env = st.admin_env(stack.dsn)
    if object_dir:
        env["ORCHARA_OBJECT_DIR"] = str(stack.object_dir)
    return env


def run_runs_command(
    stack: T038Stack, args: list[str], *, object_dir: bool = True, timeout: int = 240
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(stack.binaries["admin"]), "runs", *args],
        cwd=st.PRODUCT_ROOT,
        env=admin_env(stack, object_dir=object_dir),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def json_report(completed: subprocess.CompletedProcess[str]) -> dict:
    """The single JSON object the commands print on stdout."""
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert lines, f"no JSON output; stderr:\n{completed.stderr}"
    return json.loads(lines[-1])


def sweep(stack: T038Stack, *args: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    completed = run_runs_command(stack, ["sweep-leases", *args])
    assert completed.returncode == 0, (
        f"sweep-leases failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
    )
    return completed, json_report(completed)


def write_evidence_file(directory: Path, document: dict) -> Path:
    path = Path(directory) / ("t038-evidence-" + uuid.uuid4().hex[:10] + ".json")
    path.write_text(json.dumps(document, indent=2, sort_keys=False), encoding="utf-8")
    return path


def recover(
    stack: T038Stack,
    run_id: str,
    *,
    evidence: dict | None = None,
    directory: Path | str,
    extra: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict]:
    args = ["recover", "--run", run_id]
    if evidence is not None:
        args += ["--evidence", str(write_evidence_file(Path(directory), evidence))]
    args += list(extra or [])
    completed = run_runs_command(stack, args)
    return completed, json_report(completed)


def recover_ok(
    stack: T038Stack,
    run_id: str,
    *,
    evidence: dict | None = None,
    directory: Path | str,
    extra: list[str] | None = None,
) -> dict:
    completed, report = recover(
        stack, run_id, evidence=evidence, directory=directory, extra=extra
    )
    assert completed.returncode == 0, (
        f"recover failed ({completed.returncode}):\n{completed.stdout}\n{completed.stderr}"
    )
    assert report["status"] in {"recovered", "check_only"}, report
    return report


def assert_refused(report: dict, code: str) -> dict:
    assert report.get("status") == "refused", report
    assert report.get("code") == code, report
    return report


# ---------------------------------------------------------------------------
# the operator evidence document (closed shape, canonical hash)
# ---------------------------------------------------------------------------
def recovery_evidence(execution_unit_id: str, **overrides) -> dict:
    document = {
        "evidence_kind": "operator_recovery",
        "execution_unit_id": execution_unit_id,
        "observed_at": FIXED_OBSERVED_AT,
        "process": {
            "pid": 4123,
            "command": "ps -p 4123 -o pid=,stat=",
            "result": "absent",
            "observed": "no matching pid",
        },
        "sandbox": {
            "sandbox_id": "sandbox-" + execution_unit_id,
            "command": "runsc list",
            "result": "absent",
        },
        "db_drained": {
            "command": "select count(*) from pg_stat_activity where pid = 4123",
            "result": "no_active_backends",
        },
        "notes": "host rebooted; the unit and its sandbox are gone",
    }
    document.update(overrides)
    return document


def canonical_evidence(document: dict) -> bytes:
    """The exact RFC 8785 bytes the platform hashes (independent computation).

    Every value is a string/number/boolean and the object has no non-ASCII text,
    so Python's sorted compact JSON is byte-identical to JCS here.
    """
    canonical = {
        "evidence_kind": document["evidence_kind"],
        "execution_unit_id": document["execution_unit_id"],
        "observed_at": _normalised_timestamp(document["observed_at"]),
        "process": {
            "pid": int(document["process"]["pid"]),
            "command": document["process"]["command"],
            "result": document["process"]["result"],
        },
        "sandbox": {
            "sandbox_id": document["sandbox"]["sandbox_id"],
            "command": document["sandbox"]["command"],
            "result": document["sandbox"]["result"],
        },
    }
    if document["process"].get("observed"):
        canonical["process"]["observed"] = document["process"]["observed"]
    if document.get("db_drained"):
        canonical["db_drained"] = {
            "command": document["db_drained"]["command"],
            "result": document["db_drained"]["result"],
        }
    if document.get("notes"):
        canonical["notes"] = document["notes"]
    return json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def expected_evidence_sha256(document: dict) -> str:
    return hashlib.sha256(canonical_evidence(document)).hexdigest()


def _normalised_timestamp(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    parsed = parsed.astimezone(dt.timezone.utc)
    return parsed.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# driving real runs into and out of recovery_pending
# ---------------------------------------------------------------------------
def claim_next(stack: T038Stack, *, execution_unit_id: str = "unit-t038") -> dict:
    """Claim the one pending ready job; fail loudly when none came back."""
    response = execution_client(stack, "supervisor").claim_job(
        execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    return response.json


def claim_none(stack: T038Stack, *, execution_unit_id: str = "unit-t038") -> None:
    response = execution_client(stack, "supervisor").claim_job(
        execution_unit_id=execution_unit_id
    )
    assert response.status == 204, response.text


def set_lease_expired_seconds_ago(dbname: str, token: str, seconds: int) -> None:
    assert seconds >= 1
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET lease_deadline = now() - "
        f"make_interval(secs => {int(seconds)}) "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )


def age_attempt_heartbeat(dbname: str, token: str, seconds: int) -> None:
    """Move the last heartbeat into the past (deterministic grace check)."""
    assert seconds >= 1
    st.sql(
        dbname,
        "UPDATE platform.run_attempts SET heartbeat_at = heartbeat_at - "
        f"make_interval(secs => {int(seconds)}) "
        f"WHERE token = {st.quote_literal(token)}::uuid",
    )


def expire_lease(
    dbname: str, token: str, *, lease_seconds: int = 120, heartbeat_seconds: int = 120
) -> None:
    set_lease_expired_seconds_ago(dbname, token, lease_seconds)
    age_attempt_heartbeat(dbname, token, heartbeat_seconds)


def parked_run(
    stack: T038Stack,
    *,
    execution_unit_id: str = "unit-t038-parked",
    lease_seconds: int = 120,
    heartbeat_seconds: int = 120,
) -> tuple[object, dict, dict]:
    """One real claimed Run parked by the real sweep."""
    owner = golden_owner(stack)
    lease = claim_lease(stack, owner, execution_unit_id=execution_unit_id)
    expire_lease(
        stack.dbname,
        lease["attempt_token"],
        lease_seconds=lease_seconds,
        heartbeat_seconds=heartbeat_seconds,
    )
    _completed, report = sweep(stack, "--grace", "1s")
    assert report["parked"] == 1, report
    row = run_state_row(stack.dbname, lease["run_id"])
    assert row["state"] == STATE_RECOVERY, row
    return owner, lease, report


def recovery_columns(dbname: str, run_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT recovery_reason, recovery_since::text AS recovery_since "
        "FROM platform.runs "
        f"WHERE id = {st.quote_literal(run_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def recovery_evidence_rows(dbname: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, run_id::text AS run_id, attempt_token::text AS attempt_token, "
        "evidence::text AS evidence, evidence_sha256, recorded_by::text AS recorded_by, "
        "recorded_at::text AS recorded_at, resumed_job_id::text AS resumed_job_id "
        "FROM platform.run_recovery_evidence "
        f"WHERE run_id = {st.quote_literal(run_id)}::uuid ORDER BY recorded_at, id",
    )


def audit_events(dbname: str, action: str, run_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, action, actor_id::text AS actor_id, "
        "resource_type, resource_id::text AS resource_id, metadata::text AS metadata, "
        "created_at::text AS created_at "
        "FROM platform.audit_events "
        f"WHERE action = {st.quote_literal(action)} "
        f"AND resource_id = {st.quote_literal(run_id)}::uuid "
        "ORDER BY created_at, id",
    )


def evidence_count(dbname: str, where: str = "TRUE") -> int:
    return int(
        st.sql(dbname, "SELECT count(*) FROM platform.run_recovery_evidence WHERE " + where)
    )


def attempt_is_live(dbname: str, token: str) -> bool:
    row = attempt_row(dbname, token)
    return row["exited_at"] is None and row["revoked_at"] is None


def attempt_is_finalized(dbname: str, token: str) -> bool:
    row = attempt_row(dbname, token)
    return row["exited_at"] is not None and row["revoked_at"] is not None


def manifest_artifact_id(stack: T038Stack, run_id: str) -> str:
    run = run_state_row(stack.dbname, run_id)
    snapshot = snapshot_row(stack.dbname, run["execution_snapshot_id"])
    return snapshot["manifest_artifact_id"]


def input_artifact_id(stack: T038Stack, run_id: str) -> str:
    run = run_state_row(stack.dbname, run_id)
    assert run["input_artifact_id"], run
    return run["input_artifact_id"]


def delete_sealed_object(stack: T038Stack, artifact_id: str) -> None:
    key = artifact_row(stack.dbname, artifact_id)["object_key"]
    path = stack.object_dir / key
    assert path.exists(), path
    path.unlink()


def tamper_sealed_object(stack: T038Stack, artifact_id: str, payload: bytes) -> None:
    key = artifact_row(stack.dbname, artifact_id)["object_key"]
    path = stack.object_dir / key
    assert path.exists(), path
    path.write_bytes(payload)


def mark_artifact_deleted(dbname: str, artifact_id: str) -> None:
    st.sql(
        dbname,
        "UPDATE platform.artifacts SET deleted_at = now(), state = 'deleted' "
        f"WHERE id = {st.quote_literal(artifact_id)}::uuid",
    )


def run_status_states(dbname: str, run_id: str) -> list[str]:
    states = []
    for row in run_events(dbname, run_id):
        if row["type"] != "run.status":
            continue
        states.append(json.loads(row["payload"]).get("state"))
    return states


def recovery_headers(response) -> dict:
    def value(name: str) -> str | None:
        raw = response.header(name)
        return None if raw in (None, "") else raw

    return {
        "reason": value(RECOVERY_REASON_HEADER),
        "since": value(RECOVERY_SINCE_HEADER),
        "evidence_at": value(RECOVERY_EVIDENCE_AT_HEADER),
        "evidence_sha256": value(RECOVERY_EVIDENCE_SHA_HEADER),
    }


# ---------------------------------------------------------------------------
# a real in-flight checkpoint transaction (pg_stat_activity)
# ---------------------------------------------------------------------------
def _psql_binary() -> str:
    import pg_dev  # tools/ is on sys.path after support.stack is imported

    return pg_dev.psql_binary()


def _psql_env(database: str, marker: str) -> dict[str, str]:
    import pg_dev

    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    env["PGAPPNAME"] = marker
    return env


def _psql_args() -> list[str]:
    import pg_dev

    return [
        "-X",
        "-h",
        "127.0.0.1",
        "-p",
        str(pg_dev.DEFAULT_PORT),
        "-U",
        pg_dev.DEFAULT_USER,
        "-tA",
        "-v",
        "ON_ERROR_STOP=1",
    ]


def wait_for_open_session(dbname: str, marker: str, *, timeout: float = 20.0) -> dict:
    """The real backend pid/backend_start of an open psql transaction."""
    deadline = time.time() + timeout
    last: list[dict] = []
    while time.time() < deadline:
        last = st.sql_rows(
            dbname,
            "SELECT pid, "
            "to_char(backend_start AT TIME ZONE 'UTC', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS backend_start, "
            "application_name, state "
            "FROM pg_stat_activity "
            f"WHERE application_name = {st.quote_literal(marker)}",
        )
        if last:
            return last[0]
        time.sleep(0.05)
    raise AssertionError(f"no open session with application_name={marker!r}: {last}")


@contextlib.contextmanager
def open_checkpoint_session(dbname: str, *, seconds: int = 6, timeout: float = 20.0):
    """One real PostgreSQL session holding an open ``pg_sleep`` transaction.

    This is the "database writes may still be in flight" fact EXECUTION.md §3
    requires the supervisor/operator to disprove: the recorded connection is a
    real backend with a real pid and backend_start, visible in pg_stat_activity
    while the transaction is open.
    """
    marker = "t038-checkpoint-" + uuid.uuid4().hex[:10]
    process = subprocess.Popen(
        [
            _psql_binary(),
            *_psql_args(),
            "-d",
            dbname,
            "-c",
            f"BEGIN; SELECT pg_sleep({int(seconds)}); COMMIT;",
        ],
        env=_psql_env(dbname, marker),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        session = wait_for_open_session(dbname, marker, timeout=timeout)
        session["application_name"] = marker
        yield session
    finally:
        try:
            process.wait(timeout=120)
        except subprocess.TimeoutExpired:  # pragma: no cover - teardown
            process.kill()
            process.wait(timeout=5)
