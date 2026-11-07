"""F13B/T114 acceptance defect CANCEL-RACE-SLOT-LEAK (GitHub #115).

T114's sustained trial discovered that a public cancel which lands while an
executing Run is *inside* a capability call could leave the Run in ``cancelling``
forever:

1. ``POST /api/v1/runs/{id}/cancel`` -> 202, ``state=cancelling`` (intent stored);
2. the in-flight model call returns, and the worker's post-call artifact upload /
   result proposal is refused with 409 ``run_state_conflict``;
3. the worker's ``failed`` proposal is refused for the same reason, and the worker
   returned **without confirming its attempt exit**;
4. the Run kept a live attempt and its execution-slot allocation until the lease
   expired (swept to ``recovery_pending``).

The fix is in the worker failure path (``presetexec``): a refused proposal still
confirms the attempt exit when the token is live, so the existing finalizer
priority (cancel > proposal) commits ``cancelled`` and releases the slot; a token
the platform already considers dead answers ``stale_attempt`` and the worker
returns safely.

This is the real-stack regression: a migrated PostgreSQL database, the compiled
API in the ``capacity-stub`` profile with the T114 stand-in service-time knob, a
real ``cmd/supervisor exec --once`` process, the real capability proxy, the real
cancel endpoint and the real DB rows. The model-only definition starts no sandbox
unit (no WSL/runsc child is created); the supervisor still runs as a real product
process, so this test belongs to the exclusive real-stack window.

Red evidence on the pre-fix worker: the Run stayed ``cancelling`` with
``finished_at`` NULL, one live attempt and ``execution_slots.used == 1``. Green:
``cancelled`` + ``finished_at`` + exited attempt + ``execution_slots.used == 0``.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import time
from pathlib import Path

from support import f00b, t025, t027, t029, t033, t041, t086, t114
from support import stack as st

PRODUCT_ROOT = Path(__file__).resolve().parents[2]

# The T114 deployment knob (capacity-stub profile only): stand-in service time of
# a stub model call. 5s makes the in-flight window deterministic; it is stand-in
# time, never vendor latency.
INFLIGHT_SERVICE_DELAY_MS = 5000
# The in-flight call cannot be interrupted, so the worker discovers the cancel
# only when the stand-in call returns. The terminal poll bounds that wait.
ACTIVE_TIMEOUT = 60.0
TERMINAL_TIMEOUT = 45.0
SUPERVISOR_TIMEOUT = 120.0


def _supervisor_env(stack) -> dict[str, str]:
    """The real ``cmd/supervisor exec`` environment for the model-only graph.

    A model-only definition starts no sandbox unit, so the sandbox digest is a
    placeholder: any accidental sandbox execution would fail closed instead of
    claiming a guest image that was never read. This test therefore starts no
    WSL/runsc child, but it still starts real product processes and therefore
    belongs to the exclusive real-stack window (ENV-B2 §2).
    """
    return t033.supervisor_exec_env(
        stack,
        digest="sha256:" + "0" * 64,
        heartbeat="1s",
        result_wait="60s",
        poll="100ms",
        timeout="60s",
    )


def _seed_model_application(stack) -> tuple[f00b.Session, str, int]:
    """One owner, one model application: the T114 capacity model graph."""
    admin = f00b.admin_session(stack)
    t025.seed_model_version(
        stack.dbname, admin.principal_id, model_id=t025.GOLDEN_MODEL_ID, validation_state="passed"
    )
    owner = f00b.invite_and_activate(stack, admin, "t114-cancel-owner", roles=("creator",))
    definition = t086.model_definition(
        t025.GOLDEN_MODEL_ID, template="Summarize {{task}}"
    )
    application_id = t027.application_with_definition(
        owner, definition, name="t114 cancel race"
    )
    return owner, application_id, t027.draft_revision(owner, application_id)


def _submit(owner, application_id: str, revision: int) -> str:
    response = t027.submit_debug(
        owner,
        application_id,
        draft_revision=revision,
        input_value={"task": "cancel while the stand-in model call is in flight"},
    )
    assert response.status == 202, response.text
    run = response.json
    assert run["state"] == "queued", run
    return run["id"]


def _run_row(dbname: str, run_id: str) -> dict:
    return t029.run_state_row(dbname, run_id)


def _wait_state(dbname: str, run_id: str, states: set[str], *, timeout: float) -> dict:
    deadline = time.time() + timeout
    row = _run_row(dbname, run_id)
    while row["state"] not in states and time.time() < deadline:
        time.sleep(0.1)
        row = _run_row(dbname, run_id)
    return row


def _wait_operation_in_flight(dbname: str, run_id: str, *, timeout: float) -> dict | None:
    """Wait until the model capability call is dispatching (committed before the
    provider call, so its presence proves the worker is inside the call)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = t086.operations(dbname, run_id)
        if rows and rows[0]["state"] == "dispatching":
            return rows[0]
        time.sleep(0.05)
    return None


def _executor_report(stdout: str) -> dict | None:
    report = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "status" in candidate:
            report = candidate
    return report


def _quota_used(dbname: str) -> int:
    return int(t029.quota_counter(dbname)["used"])


def _worker_artifacts_after(dbname: str, run_id: str, cancel_at: str) -> int:
    """Worker-registered artifacts created after the cancel intent (must be 0).

    The capability proxy's own params/receipt sealing is not a worker
    registration; the worker's post-call upload would be final_output /
    node_output / log.
    """
    return int(
        st.sql_scalar(
            dbname,
            "SELECT count(*) FROM platform.run_artifacts AS ra "
            "JOIN platform.artifacts AS a ON a.id = ra.artifact_id "
            f"WHERE ra.run_id = {st.quote_literal(run_id)}::uuid "
            "AND a.purpose IN ('final_output', 'node_output', 'log') "
            f"AND a.created_at > {st.quote_literal(cancel_at)}::timestamptz",
        )
    )


def _epoch(value: str) -> float:
    text = value.replace("Z", "+00:00")
    parsed = datetime.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def test_cancel_during_inflight_capability_commits_cancelled_and_releases_slot(
    tmp_path: Path, go_binaries, api_server
) -> None:
    stack = t114.build_trial_stack(
        tmp_path=tmp_path / "cancel-exit",
        go_binaries=go_binaries,
        api_server_factory=api_server,
        service_time_ms=INFLIGHT_SERVICE_DELAY_MS,
    )
    try:
        owner, application_id, revision = _seed_model_application(stack)

        # Control (T036): a queued Run has no live unit, so the cancel transaction
        # commits cancelled immediately; intent time == stop time.
        queued_id = _submit(owner, application_id, revision)
        queued_cancel = owner.client.post(
            f"/api/v1/runs/{queued_id}/cancel", csrf=owner.csrf
        )
        assert queued_cancel.status == 202, queued_cancel.text
        queued_row = _run_row(stack.dbname, queued_id)
        assert queued_row["state"] == "cancelled", queued_row
        assert queued_row["cancel_requested_at"] == queued_row["finished_at"], queued_row
        assert t029.attempts_for_run(stack.dbname, queued_id) == [], queued_row

        # The race: a real supervisor claims and executes the Run; the cancel
        # lands while its model capability call is in flight.
        run_id = _submit(owner, application_id, revision)
        process = subprocess.Popen(
            [
                str(stack.binaries["supervisor"]),
                "exec",
                "--once",
                "--worker-id",
                "t114-cancel-race",
            ],
            cwd=PRODUCT_ROOT,
            env=_supervisor_env(stack),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        try:
            row = _wait_state(stack.dbname, run_id, {"running"}, timeout=ACTIVE_TIMEOUT)
            assert row["state"] == "running", row
            operation = _wait_operation_in_flight(
                stack.dbname, run_id, timeout=ACTIVE_TIMEOUT
            )
            assert operation is not None, (
                "the worker never reached a dispatching model capability call; "
                f"run={_run_row(stack.dbname, run_id)}"
            )
            cancel = owner.client.post(f"/api/v1/runs/{run_id}/cancel", csrf=owner.csrf)
            assert cancel.status == 202, cancel.text
            assert (cancel.json or {}).get("state") == "cancelling", cancel.json
            intent_header = cancel.header("X-Run-Cancel-Requested-At")
            assert intent_header, "the cancel response carries no intent timestamp"
            stdout, stderr = process.communicate(timeout=SUPERVISOR_TIMEOUT)
        finally:
            if process.poll() is None:  # pragma: no cover - defensive
                process.kill()
                process.communicate()

        report = _executor_report(stdout)
        row = _wait_state(
            stack.dbname, run_id, {"cancelled", "failed", "succeeded"},
            timeout=TERMINAL_TIMEOUT,
        )
        attempts = t029.attempts_for_run(stack.dbname, run_id)
        operations_after = t086.operations(stack.dbname, run_id)
        quota_used = _quota_used(stack.dbname)
        evidence = {
            "run_id": run_id,
            "operation_at_cancel": operation,
            "operation_after_worker": operations_after,
            "cancel_status": cancel.status,
            "state_after_cancel": (cancel.json or {}).get("state"),
            "cancel_requested_at_header": intent_header,
            "state_after_worker": row["state"],
            "finished_at": row["finished_at"],
            "cancel_requested_at_db": row["cancel_requested_at"],
            "attempts": attempts,
            "execution_slots_used": quota_used,
            "executor_returncode": process.returncode,
            "executor_report": report,
            "executor_stderr_tail": (stderr or "")[-800:],
        }
        if row["cancel_requested_at"] and row["finished_at"]:
            evidence["intent_to_stop_s"] = round(
                _epoch(row["finished_at"]) - _epoch(row["cancel_requested_at"]), 3
            )
        print("cancel-exit evidence: " + json.dumps(evidence, default=str), flush=True)

        # The cancellation reached its terminal state, with a distinct stop time.
        assert row["state"] == "cancelled", evidence
        assert row["finished_at"], evidence
        assert row["cancel_requested_at"], evidence
        assert 0 <= evidence["intent_to_stop_s"] < TERMINAL_TIMEOUT, evidence
        # The attempt is over and the execution slot is released: no leaked unit.
        assert len(attempts) == 1, evidence
        assert attempts[0]["exited_at"], evidence
        assert quota_used == 0, evidence
        # The in-flight call really returned (the cancel hit inside it), and the
        # worker reported a cancellation rather than a stranded error.
        assert len(operations_after) == 1, evidence
        assert operations_after[0]["state"] == "succeeded", evidence
        # H2 regression guard: a cancel observed around an in-flight model call
        # must never be recorded as a provider failure in the ledger.
        operation_attempts = t041.attempt_rows(stack.dbname, operation["id"])
        assert len(operation_attempts) == 1, evidence | {"operation_attempts": operation_attempts}
        assert operation_attempts[0]["state"] == "succeeded", operation_attempts
        assert operation_attempts[0]["error_code"] != "provider_error", operation_attempts
        assert operations_after[0]["last_error_code"] != "provider_error", operations_after
        assert report is not None, evidence
        assert report.get("outcome") == "cancelled", evidence
        assert report.get("run_state") == "cancelled", evidence
        # No worker artifact may be registered after the cancel intent.
        assert (
            _worker_artifacts_after(
                stack.dbname, run_id, row["cancel_requested_at"]
            )
            == 0
        ), evidence

        # A late worker result is 409 run_terminal and never revives the Run.
        late = t029.execution_client(stack, "worker").submit_result(
            run_id,
            attempt_token=attempts[0]["token"],
            suggested_state="failed",
            error_code="tool_timeout",
        )
        assert late.status == 409, late.text
        assert ((late.json or {}).get("error") or {}).get("code") == "run_terminal", late.text
        after = _run_row(stack.dbname, run_id)
        assert after["state"] == "cancelled", after
        assert after["finished_at"] == row["finished_at"], after
    finally:
        stack.server.stop()
        st.drop_database(stack.dbname)
