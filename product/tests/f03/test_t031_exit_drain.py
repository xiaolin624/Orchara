"""T031 exit guard: the old execution's database writes must drain first.

STATE-MACHINES 搂2 allows a terminal Run only after the execution process, its
sandbox and its in-flight database writes are proven gone. The supervisor's
``db_writes_drained`` claim is not evidence: the API re-checks every checkpoint
backend the worker reported on heartbeat against ``pg_stat_activity``. An
uncommitted transaction keeps the Run non-terminal and answers 409
``database_writes_in_flight``; after it ends the same exit call finalizes once.
"""

from __future__ import annotations

import os
import subprocess
import time
import uuid

import pytest

from support import stack as st, t029, t031
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t029_stack,
)


def _open_idle_transaction(dbname: str) -> tuple[subprocess.Popen, dict]:
    """A real psql session holding an uncommitted write (state idle-in-transaction)."""
    application_name = "orchara-t031-drain-" + uuid.uuid4().hex[:8]
    env = dict(os.environ)
    env["PGPASSWORD"] = st.pg_dev.load_secret()
    env["PGAPPNAME"] = application_name
    process = subprocess.Popen(
        [
            st.pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(st.pg_dev.DEFAULT_PORT),
            "-U",
            st.pg_dev.DEFAULT_USER,
            "-d",
            dbname,
            "-tA",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    process.stdin.write(
        "BEGIN;\n"
        "CREATE TEMP TABLE t031_drain_probe (value int);\n"
        "INSERT INTO t031_drain_probe VALUES (1);\n"
    )
    process.stdin.flush()
    deadline = time.time() + 15
    last: list[dict] = []
    while time.time() < deadline:
        last = st.sql_rows(
            dbname,
            "SELECT pid::text AS pid, "
            "to_char(backend_start AT TIME ZONE 'UTC', "
            '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS backend_start, state, '
            "application_name "
            "FROM pg_stat_activity "
            f"WHERE application_name = {st.quote_literal(application_name)}",
        )
        if last and last[0]["state"] == "idle in transaction":
            return process, last[0]
        if process.poll() is not None:
            break
        time.sleep(0.05)
    process.kill()
    raise AssertionError(f"the probe transaction never became idle-in-transaction: {last}")


def _close_transaction(process: subprocess.Popen) -> None:
    assert process.stdin is not None
    process.stdin.write("ROLLBACK;\n\\q\n")
    process.stdin.flush()
    try:
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            process.kill()


def test_an_open_checkpoint_transaction_blocks_the_terminal_state(t029_stack) -> None:
    owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-drain"
    )
    del owner
    supervisor = t031.execution_client(t029_stack, "supervisor")
    worker = t031.execution_client(t029_stack, "worker")

    probe, connection = _open_idle_transaction(t029_stack.dbname)
    try:
        heartbeat = supervisor.heartbeat_run(
            lease["run_id"],
            attempt_token=lease["attempt_token"],
            execution_unit_id="unit-drain",
            checkpoint_connections=[
                {
                    "pid": int(connection["pid"]),
                    "backend_start": connection["backend_start"],
                    "application_name": connection["application_name"],
                }
            ],
        )
        assert heartbeat.status == 200, heartbeat.text

        proposal = t031.result_body(
            lease,
            suggested_state="succeeded",
            output_artifact_id=artifact,
            checkpoint_id="ckpt-drain",
        )
        accepted = worker.submit_result(lease["run_id"], body=proposal)
        assert accepted.status == 200, accepted.text
        assert accepted.json["phase"] == "finalizing"

        blocked = supervisor.confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-drain",
            evidence=t029.exit_evidence(lease, started=True, exit_code=0),
        )
        assert blocked.status == 409, blocked.text
        assert blocked.json["error"]["code"] == "database_writes_in_flight", blocked.text

        row = t029.run_state_row(t029_stack.dbname, lease["run_id"])
        assert row["state"] == "running" and row["phase"] == "finalizing", row
        assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "leased"
        assert t029.attempt_row(t029_stack.dbname, lease["attempt_token"])["exited_at"] is None
    finally:
        _close_transaction(probe)

    # The same call now succeeds because the backend is gone; it finalizes once.
    accepted_exit = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id="unit-drain",
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert accepted_exit.status == 200, accepted_exit.text
    final = t029.run_state_row(t029_stack.dbname, lease["run_id"])
    assert final["state"] == "succeeded", final
    assert final["output_artifact_id"] == artifact, final
    assert t029.job_row(t029_stack.dbname, lease["job_id"])["state"] == "completed"


def test_an_idle_checkpoint_connection_does_not_block_the_terminal_state(t029_stack) -> None:
    """An idle session has no write in flight; only active transactions block."""
    owner, lease, artifact = t031.claimed_with_output(
        t029_stack, execution_unit_id="unit-idle"
    )
    del owner
    supervisor = t031.execution_client(t029_stack, "supervisor")
    worker = t031.execution_client(t029_stack, "worker")

    application_name = "orchara-t031-idle-" + uuid.uuid4().hex[:8]
    env = dict(os.environ)
    env["PGPASSWORD"] = st.pg_dev.load_secret()
    env["PGAPPNAME"] = application_name
    idle = subprocess.Popen(
        [
            st.pg_dev.psql_binary(),
            "-X",
            "-h",
            "127.0.0.1",
            "-p",
            str(st.pg_dev.DEFAULT_PORT),
            "-U",
            st.pg_dev.DEFAULT_USER,
            "-d",
            t029_stack.dbname,
            "-tA",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.time() + 15
        last: list[dict] = []
        while time.time() < deadline:
            last = st.sql_rows(
                t029_stack.dbname,
                "SELECT pid::text AS pid, "
                "to_char(backend_start AT TIME ZONE 'UTC', "
                '\'YYYY-MM-DD"T"HH24:MI:SS.US"Z"\') AS backend_start, state, '
                "application_name "
                "FROM pg_stat_activity "
                f"WHERE application_name = {st.quote_literal(application_name)}",
            )
            if last and last[0]["state"] == "idle":
                break
            time.sleep(0.05)
        else:  # pragma: no cover - defensive
            pytest.fail(f"the idle probe never appeared: {last}")

        heartbeat = supervisor.heartbeat_run(
            lease["run_id"],
            attempt_token=lease["attempt_token"],
            execution_unit_id="unit-idle",
            checkpoint_connections=[
                {
                    "pid": int(last[0]["pid"]),
                    "backend_start": last[0]["backend_start"],
                    "application_name": last[0]["application_name"],
                }
            ],
        )
        assert heartbeat.status == 200, heartbeat.text

        proposal = t031.result_body(
            lease,
            suggested_state="succeeded",
            output_artifact_id=artifact,
            checkpoint_id="ckpt-idle",
        )
        assert worker.submit_result(lease["run_id"], body=proposal).status == 200
        accepted = supervisor.confirm_exit(
            lease["attempt_token"],
            execution_unit_id="unit-idle",
            evidence=t029.exit_evidence(lease, started=True, exit_code=0),
        )
        assert accepted.status == 200, accepted.text
        assert t029.run_state_row(t029_stack.dbname, lease["run_id"])["state"] == "succeeded"
    finally:
        assert idle.stdin is not None
        idle.stdin.write("\\q\n")
        idle.stdin.flush()
        try:
            idle.wait(timeout=10)
        finally:
            if idle.poll() is None:
                idle.kill()

