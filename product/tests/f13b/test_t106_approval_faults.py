"""T106 / F13B: three real approval faults on the real product stack.

The ticket injects the EVALUATION.md §4 faults FAULT-01/02/03 against the real
stack: PostgreSQL migrated by the real ``cmd/admin`` chain, the compiled
``cmd/api`` with the real mTLS internal listener, the real object store with
HMAC-signed links, the real approval/operation/waiting tables, the real
supervisor binary (``cmd/supervisor exec --once``), real OS worker processes and
a real loopback connector endpoint (the endpoint's own request log is the
independent external ledger).

* **FAULT-01**: a real worker OS process parks the run at the approval, submits
  the T035 waiting result and confirms its exit; the process is then killed with
  ``taskkill /F /T`` while the approval is pending. A fresh
  ``cmd/supervisor exec --once`` process starts against the same database, the
  owner approves, and a fresh worker process claims the single resume job and
  replays the *same operation id / logical key / params*. The approval row and
  the operation row are the durable facts across the restart.
* **FAULT-02**: after the exact params are approved, a replay with changed
  params is refused with ``409 operation_conflict``; a decision carrying a wrong
  ``params_sha256`` is refused with ``409 approval_params_mismatch``; a stranger
  gets 404, the admin gets 403 ``approver_mismatch``, and a late decision after
  cancellation is a 409. All refused paths leave the endpoint's write count at
  zero.
* **FAULT-03**: the loopback endpoint really receives and applies the write and
  then closes the TCP connection without a response. The operation is
  ``unknown`` (never succeeded/failed), a duplicate invoke does not resend, the
  Run parks in ``needs_reconciliation``, and owner evidence plus
  ``POST /operations/{id}/reconcile`` settles it as succeeded with exactly one
  physical write.

Worker-role boundary (documented, not hidden): the product's graph executor has
no connector node (T101 J6b), so the connector capability call is made by the
worker mTLS role from a real OS worker process (``tests/f13b/t106_worker.py``)
over the real internal protocol, exactly the T048/T050/T051 seam. No egress
guard, approval, capability service, DB row or HTTP connection is mocked.

``not_run``: a real vendor connector/model (loopback + dev-stub stand in), a real
browser DOM, and a resident supervisor daemon (only the production one-shot
``exec --once`` process is used). The real vendor model/connector remains
``not_run``.
"""

from __future__ import annotations

import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from f13b import fault_support as fs
from support.runtime_stack import Stack
from support import f00b, t024, t029, t035, t041, t048, t050, t051
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr

PRODUCT_ROOT = st.PRODUCT_ROOT

#: The connector credential exists only in this process's environment and in the
#: loopback endpoint's `X-Connector-Secret` header (redacted from evidence).
T106_SECRET_VALUE = "t106-connector-secret-5b72e9"

FAULT_01_WORKER_A_APP = "t106-fault01-worker-a"
FAULT_01_UNIT_A = "t106-fault01-unit-a"
FAULT_01_UNIT_B = "t106-fault01-unit-b"


# ---------------------------------------------------------------------------
# stack + connector fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def t106_stack(tmp_path, fresh_database, go_binaries, api_server) -> Stack:
    """The T051-shaped real stack (objects, signed links, mTLS, approvals)."""
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)

    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    env = t029.api_extra_env(object_dir, certificates, signing_key=signing_key)
    env[t048.TEST_SECRET_ENV] = T106_SECRET_VALUE
    server = api_server(dsn, extra_env=env)

    stack = Stack(
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


@pytest.fixture
def success_server():
    """A real loopback endpoint that answers every write with 2xx JSON."""
    server = t048.LocalConnectorServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def lost_receipt_server():
    """A real loopback endpoint that can read the write and drop the response."""
    server = t051.LostReceiptServer()
    try:
        yield server
    finally:
        server.close()


@pytest.fixture
def worker_sessions():
    sessions: list[fs.WorkerProcess] = []
    try:
        yield sessions
    finally:
        for session in sessions:
            session.close()


# ---------------------------------------------------------------------------
# helpers: policy, worker spec, supervisor process
# ---------------------------------------------------------------------------
def prepare_write_policy(stack, server, owner):
    """Register one write-risk connector and pin it to the golden tool."""
    if isinstance(server, t048.LocalConnectorServer):
        server.respond_json({"ok": True, "write_id": "w-t106"})
    policy = t050.write_policy(stack, owner.admin, origin=server.url)
    t048.bind_tool_connectors(stack.dbname, [policy["id"]])
    return policy


def worker_spec(
    stack,
    *,
    mode: str,
    policy_id: str,
    params: dict,
    logical_key: str,
    operation_id: str,
    run_id: str,
    worker_id: str,
    execution_unit_id: str,
    owner_id: str | None = None,
) -> dict:
    certificates = stack.certificates
    supervisor = certificates.client("supervisor")
    worker = certificates.client("worker")
    return {
        "mode": mode,
        "internal_url": certificates.internal_url,
        "ca": str(certificates.ca.cert),
        "supervisor": {"cert": str(supervisor.cert), "key": str(supervisor.key)},
        "worker": {"cert": str(worker.cert), "key": str(worker.key)},
        "dsn": stack.dsn,
        "dbname": stack.dbname,
        "object_dir": str(stack.object_dir),
        "run_id": run_id,
        "policy_id": policy_id,
        "params": params,
        "logical_key": logical_key,
        "operation_id": operation_id,
        "worker_id": worker_id,
        "execution_unit_id": execution_unit_id,
        "owner_id": owner_id,
        "application_name": FAULT_01_WORKER_A_APP,
    }


def supervisor_env(stack) -> dict[str, str]:
    """The compile-time supervisor configuration; no sandbox run is requested."""
    certificates = stack.certificates
    supervisor = certificates.client("supervisor")
    worker = certificates.client("worker")
    env = st.admin_env(stack.dsn)
    env.pop("ORCHARA_DB_DSN", None)
    env.update(
        {
            "ORCHARA_INTERNAL_URL": certificates.internal_url,
            "ORCHARA_INTERNAL_CA": str(certificates.ca.cert),
            "ORCHARA_SUPERVISOR_CLIENT_CERT": str(supervisor.cert),
            "ORCHARA_SUPERVISOR_CLIENT_KEY": str(supervisor.key),
            "ORCHARA_WORKER_CLIENT_CERT": str(worker.cert),
            "ORCHARA_WORKER_CLIENT_KEY": str(worker.key),
            "ORCHARA_SUPERVISOR_DSN": stack.dsn,
            "ORCHARA_BACKGROUND_HEARTBEAT_INTERVAL": "100ms",
            "ORCHARA_EXEC_RESULT_WAIT": "10s",
            "ORCHARA_EXEC_POLL_INTERVAL": "10ms",
            "ORCHARA_PRODUCT_ROOT": str(PRODUCT_ROOT),
            # The supervisor is never asked to execute a sandbox node here; a
            # well-formed digest keeps a future accidental claim fail-closed on
            # the missing rootfs instead of on a parse error.
            "ORCHARA_SANDBOX_ROOTFS_DIGEST": "sha256:" + "0" * 64,
            "ORCHARA_SANDBOX_TIMEOUT": "5s",
        }
    )
    return env


def run_supervisor_once(stack, log_path: Path, *, timeout: float = 120.0) -> dict:
    """Start the real compiled supervisor as a one-shot OS process.

    ``cmd/supervisor exec --once`` is not a resident daemon (ENV.md); this is the
    production process shape started against the same database and mTLS API.
    """
    command = [str(stack.binaries["supervisor"]), "exec", "--once"]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = time.time()
    timed_out = False
    with log_path.open("w", encoding="utf-8") as handle:
        process = subprocess.Popen(
            command,
            cwd=str(PRODUCT_ROOT),
            env=supervisor_env(stack),
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:  # pragma: no cover - environment stall
            timed_out = True
            fs.kill_process_tree(process.pid)
            returncode = process.wait(timeout=30)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    report = None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            report = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        break
    return {
        "command": " ".join(command),
        "pid": process.pid,
        "started_at": started_at,
        "finished_at": time.time(),
        "returncode": returncode,
        "timed_out": timed_out,
        "report": report,
        "log": str(log_path),
        "log_tail": "\n".join(text.splitlines()[-8:]),
    }


def claim_resume_lease(stack, *, worker_id: str, execution_unit_id: str) -> dict:
    response = t029.execution_client(stack, "supervisor").claim_job(
        worker_id=worker_id, execution_unit_id=execution_unit_id
    )
    assert response.status == 200, response.text
    lease = response.json
    assert lease["job_kind"] == "resume", lease
    return lease


def stable_approval_fields(row: dict) -> dict:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "operation_id": row["operation_id"],
        "node_key": row["node_key"],
        "proposal_artifact_id": row["proposal_artifact_id"],
        "params_sha256": row["params_sha256"],
        "approver_id": row["approver_id"],
    }


def router_records(server) -> int:
    return len(server.records)


# ---------------------------------------------------------------------------
# FAULT-01: kill/restart a real worker while the approval waits
# ---------------------------------------------------------------------------
def test_fault_01_restart_keeps_the_same_operation_and_approval(
    t106_stack, success_server, worker_sessions, tmp_path
) -> None:
    stack = t106_stack
    owner = t041.owner(stack)
    policy = prepare_write_policy(stack, success_server, owner)
    params = t050.default_params()
    logical_key = "t106:fault01:write"
    operation_id = str(uuid.uuid4())
    run = t029.queued_run(stack, owner)

    fault = fs.start(
        "FAULT-01",
        "Approval waits while the real worker process is killed; the original "
        "approval resumes the unchanged operation",
        injection_point=(
            "OS process kill `taskkill /F /T /PID <worker-a>` of the real worker "
            "process tests/f13b/t106_worker.py (real mTLS capability + T035 "
            "waiting/exit protocol) while platform.approvals.state='pending'; "
            "restart = compiled `cmd/supervisor exec --once` process against the "
            "same PostgreSQL + mTLS API, then a fresh worker process claiming the "
            "single resume job. Durability seam: platform.approvals, "
            "platform.operations, platform.run_waitings, platform.jobs."
        ),
        expected=[
            "the killed process disappears and its PostgreSQL session leaves pg_stat_activity",
            "the approval row is not replaced (same id/proposal/params_sha256/approver)",
            "platform.operations keeps exactly the same operation id",
            "exactly one resume job is created, only after the human decision",
            "the loopback endpoint observes 0 requests before approval and exactly 1 after",
            "the resumed operation reaches succeeded with exactly one attempt",
        ],
    )
    with fault:
        fault.input.update(
            {
                "seed": "t106-fault01",
                "run_id": run["id"],
                "operation_id": operation_id,
                "logical_key": logical_key,
                "params": params,
                "policy_id": policy["id"],
                "policy_sha256": policy["policy_sha256"],
                "connector_origin": success_server.url,
            }
        )
        canonical, independent_sha256 = fs.canonical_params(params)
        fault.input["params_canonical"] = canonical.decode("utf-8")
        fault.input["params_sha256_independent"] = independent_sha256

        # -- 1. a real worker OS process parks the run at the approval ---------
        spec_a = worker_spec(
            stack,
            mode="prepare",
            policy_id=policy["id"],
            params=params,
            logical_key=logical_key,
            operation_id=operation_id,
            run_id=run["id"],
            worker_id="t106-worker-a",
            execution_unit_id=FAULT_01_UNIT_A,
        )
        worker_a = fs.WorkerProcess(
            "prepare",
            spec_a,
            facts_path=tmp_path / "worker-a-facts.json",
            log_path=tmp_path / "worker-a.log",
        )
        worker_sessions.append(worker_a)
        facts_a = worker_a.wait_facts(timeout=180, require=("parked", "holding"))
        fault.check(
            "worker_a_operation_unchanged",
            facts_a["operation_id"] == operation_id,
            facts_a["operation_id"],
        )

        approval = t050.approval_row(stack.dbname, facts_a["approval_id"])
        assert approval is not None
        operation = t050.operation(stack.dbname, run["id"], operation_id)
        waiting = t035.waiting_row(stack.dbname, run["id"])
        run_before = t050.run_row(stack.dbname, run["id"])
        fault.check(
            "parked_at_approval",
            run_before["state"] == "waiting_approval"
            and waiting is not None
            and waiting["exit_confirmed_at"] is not None
            and waiting["decision"] is None
            and approval["state"] == "pending"
            and operation["state"] == "awaiting_approval",
            {
                "run": run_before,
                "waiting": waiting,
                "approval": approval,
                "operation_state": operation["state"],
            },
        )
        fault.check(
            "product_hash_matches_the_independent_hash",
            operation["params_sha256"] == independent_sha256,
            {
                "product": operation["params_sha256"],
                "independent": independent_sha256,
            },
        )
        fault.check(
            "no_write_before_approval",
            router_records(success_server) == 0,
            success_server.records,
        )

        sessions_before = fs.wait_application_sessions(
            stack.dbname, FAULT_01_WORKER_A_APP, count=1, timeout=30
        )
        fault.check(
            "worker_db_session_present",
            len(sessions_before) == 1,
            sessions_before,
        )

        # -- 2. kill the real process, then start the real supervisor again ----
        kill = worker_a.kill()
        fault.check(
            "worker_process_killed",
            kill["taskkill"]["returncode"] == 0
            and kill["exit_code"] is not None
            and kill["present_after"]["present"] is False,
            kill,
        )
        sessions_after = fs.wait_application_sessions(
            stack.dbname, FAULT_01_WORKER_A_APP, count=0, timeout=30
        )
        fault.check(
            "worker_db_session_gone",
            sessions_after == [],
            sessions_after,
        )
        fault.ledger(
            "process_table",
            {
                "worker_a": {
                    "pid": worker_a.pid,
                    "started_at": worker_a.started_at,
                    "os_start_time": worker_a.start_time_os,
                    "kill": kill,
                    "db_sessions_before": sessions_before,
                    "db_sessions_after": sessions_after,
                }
            },
        )

        supervisor_run = run_supervisor_once(stack, tmp_path / "supervisor-exec.log")
        fault.check(
            "restarted_supervisor_is_idle",
            supervisor_run["returncode"] == 0
            and supervisor_run["report"] is not None
            and supervisor_run["report"].get("status") == "idle",
            supervisor_run,
        )
        fault.ledger("supervisor_restart", supervisor_run)
        fault.command("cmd/supervisor exec --once  # after the kill, before approval")

        # The approval is still pending and the restart must not auto-resume.
        after_restart = {
            "run": t050.run_row(stack.dbname, run["id"]),
            "approval": t050.approval_row(stack.dbname, approval["id"]),
            "operation": t050.operation(stack.dbname, run["id"], operation_id),
            "resume_jobs": t035.resume_jobs(stack.dbname, run["id"]),
        }
        fault.check(
            "restart_does_not_auto_resume",
            after_restart["run"]["state"] == "waiting_approval"
            and after_restart["approval"]["state"] == "pending"
            and after_restart["operation"]["state"] == "awaiting_approval"
            and after_restart["resume_jobs"] == []
            and router_records(success_server) == 0,
            after_restart,
        )

        # -- 3. the human approves the original approval -----------------------
        decided = t050.decide(
            owner.creator, approval["id"], params_sha256=approval["params_sha256"]
        )
        fault.check(
            "original_approval_decided",
            decided.status == 200 and decided.json.get("state") == "approved",
            decided.text,
        )
        resume_jobs = t035.resume_jobs(stack.dbname, run["id"])
        approval_after = t050.approval_row(stack.dbname, approval["id"])
        operation_after = t050.operation(stack.dbname, run["id"], operation_id)
        fault.check(
            "approval_row_not_replaced",
            stable_approval_fields(approval_after) == stable_approval_fields(approval)
            and approval_after["state"] == "approved"
            and approval_after["decided_by"] == owner.creator.principal_id,
            {"before": approval, "after": approval_after},
        )
        fault.check(
            "same_operation_after_decision",
            operation_after["id"] == operation_id
            and operation_after["state"] == "authorized",
            operation_after,
        )
        fault.check("exactly_one_resume_job", len(resume_jobs) == 1, resume_jobs)
        fault.check(
            "no_write_after_decision_before_resume",
            router_records(success_server) == 0,
            success_server.records,
        )

        # -- 4. a fresh worker process claims the resume and replays the call --
        spec_b = worker_spec(
            stack,
            mode="resume",
            policy_id=policy["id"],
            params=params,
            logical_key=logical_key,
            operation_id=operation_id,
            run_id=run["id"],
            worker_id="t106-worker-b",
            execution_unit_id=FAULT_01_UNIT_B,
            owner_id=owner.creator.principal_id,
        )
        worker_b = fs.WorkerProcess(
            "resume",
            spec_b,
            facts_path=tmp_path / "worker-b-facts.json",
            log_path=tmp_path / "worker-b.log",
        )
        worker_sessions.append(worker_b)
        facts_b = worker_b.wait_facts(timeout=180, require=("completed",))
        fault.check(
            "worker_b_exit_clean",
            worker_b.wait(timeout=30) == 0,
            {"facts": facts_b, "log": worker_b.log_tail()},
        )

        operation_final = t050.operation(stack.dbname, run["id"], operation_id)
        attempts = t041.attempt_rows(stack.dbname, operation_id)
        approval_final = t050.approval_row(stack.dbname, approval["id"])
        resume_jobs_final = t035.resume_jobs(stack.dbname, run["id"])
        run_final = t050.run_row(stack.dbname, run["id"])
        operations = t041.operation_rows(stack.dbname, run["id"])
        fault.check(
            "same_operation_succeeded_once",
            operation_final["id"] == operation_id
            and operation_final["state"] == "succeeded"
            and len(attempts) == 1
            and attempts[0]["state"] == "succeeded",
            {"operation": operation_final, "attempts": attempts},
        )
        fault.check(
            "exactly_one_physical_write",
            router_records(success_server) == 1
            and len(success_server.records) == 1
            and success_server.records[0]["method"] == "POST"
            and success_server.records[0]["path"] == "/v1/orders",
            success_server.records,
        )
        fault.check(
            "one_operation_row_and_one_resume_job",
            len(operations) == 1
            and operations[0]["id"] == operation_id
            and len(resume_jobs_final) == 1
            and resume_jobs_final[0]["state"] == "completed",
            {"operations": operations, "resume_jobs": resume_jobs_final},
        )
        fault.check(
            "approval_unchanged_after_resume",
            stable_approval_fields(approval_final) == stable_approval_fields(approval)
            and approval_final["state"] == "approved",
            {"before": approval, "after": approval_final},
        )
        fault.check(
            "run_succeeded",
            run_final["state"] == "succeeded" and run_final["finished_at"] is not None,
            run_final,
        )

        fault.ledger(
            "connector_endpoint",
            {
                "requests": success_server.records,
                "request_count": router_records(success_server),
            },
        )
        fault.ledger(
            "postgres",
            {
                "run": run_final,
                "waiting": t035.waiting_row(stack.dbname, run["id"]),
                "approval": approval_final,
                "approval_count": len(t050.approval_rows(stack.dbname, run_id=run["id"])),
                "operation": operation_final,
                "operation_rows": operations,
                "attempts": attempts,
                "resume_jobs": resume_jobs_final,
                "start_job": t035.job_row(stack.dbname, facts_a["job_id"]),
            },
        )
        fault.observed.update(
            {
                "worker_a_pid": worker_a.pid,
                "worker_b_pid": worker_b.pid,
                "supervisor_pid": supervisor_run["pid"],
                "approval_id": approval["id"],
                "operation_id": operation_final["id"],
                "resume_jobs": len(resume_jobs_final),
                "writes_before_approval": 0,
                "writes_after_resume": router_records(success_server),
                "run_state": run_final["state"],
            }
        )
        fault.command("POST /api/v1/approvals/{id}/decision (owner session, exact params_sha256)")
        fault.command(
            "POST /internal/v1/jobs/claim (mTLS supervisor) -> POST "
            "/internal/v1/runs/{id}/capabilities (mTLS worker, same operation id)"
        )
        fault.mark_passed()


# ---------------------------------------------------------------------------
# FAULT-02: changed params / wrong hash / stranger / admin / late decision
# ---------------------------------------------------------------------------
def test_fault_02_changed_params_cannot_reuse_the_approval(
    t106_stack, success_server
) -> None:
    stack = t106_stack
    owner = t041.owner(stack)
    policy = prepare_write_policy(stack, success_server, owner)
    params = t050.default_params()
    changed_params = {"path": "/v1/orders", "body": {"amount": 999999, "currency": "CNY"}}
    logical_key = "t106:fault02:write"
    operation_id = str(uuid.uuid4())

    fault = fs.start(
        "FAULT-02",
        "An approval is bound to the exact params: changed params and wrong hashes are refused with zero writes",
        injection_point=(
            "internal/capabilities/service.go:689-700 `prepareOperation` compares "
            "the stored operation's params_sha256 with the new canonical digest and "
            "returns 409 operation_conflict; internal/approvals/decision.go:148-151 "
            "compares the decision's params_sha256 with the approval and returns 409 "
            "approval_params_mismatch. exercised through the real mTLS capability "
            "and owner decision HTTP paths."
        ),
        expected=[
            "a replay with changed params is refused (409 operation_conflict)",
            "the old approval cannot authorise changed params or a different logical key",
            "a decision with a wrong params_sha256 is refused (409 approval_params_mismatch)",
            "a stranger (404) and the admin (403 approver_mismatch) cannot decide",
            "a late decision after cancellation is refused (409)",
            "every refused path leaves the loopback write count unchanged",
        ],
    )
    with fault:
        fault.input.update(
            {
                "seed": "t106-fault02",
                "operation_id": operation_id,
                "logical_key": logical_key,
                "params": params,
                "changed_params": changed_params,
                "policy_id": policy["id"],
                "connector_origin": success_server.url,
            }
        )
        canonical, independent_sha256 = fs.canonical_params(params)
        changed_canonical, changed_sha256 = fs.canonical_params(changed_params)
        fault.input["params_canonical"] = canonical.decode("utf-8")
        fault.input["params_sha256_independent"] = independent_sha256
        fault.input["changed_params_canonical"] = changed_canonical.decode("utf-8")
        fault.input["changed_params_sha256_independent"] = changed_sha256
        fault.check(
            "changed_params_are_a_different_input",
            independent_sha256 != changed_sha256,
            {"params": independent_sha256, "changed": changed_sha256},
        )

        _, run, lease = t041.claimed_run(stack, run_owner=owner)
        fault.input["run_id"] = run["id"]
        first = t050.invoke_write(
            stack,
            lease,
            policy_id=policy["id"],
            params=params,
            logical_key=logical_key,
            operation_id=operation_id,
        )
        fault.check(
            "write_parked_for_approval",
            first.status == 200 and first.json.get("state") == "awaiting_approval",
            first.text,
        )
        approval = t050.approval_row(stack.dbname, first.json["approval_id"])
        fault.check(
            "product_hash_matches_the_independent_hash",
            approval["params_sha256"] == independent_sha256,
            {"product": approval["params_sha256"], "independent": independent_sha256},
        )

        parked = t035.submit_waiting(
            t035.execution_client(stack, "worker"),
            lease,
            checkpoint_id="capability:" + operation_id,
        )
        assert parked.status == 200, parked.text
        approved = t050.decide(
            owner.creator, approval["id"], params_sha256=approval["params_sha256"]
        )
        assert approved.status == 200, approved.text
        confirmed = t035.confirm_waiting_exit(
            stack, lease, execution_unit_id="t041-unit"
        )
        assert confirmed.status == 200, confirmed.text
        fault.check(
            "one_resume_after_approval",
            len(t035.resume_jobs(stack.dbname, run["id"])) == 1,
            t035.resume_jobs(stack.dbname, run["id"]),
        )

        resume_lease = claim_resume_lease(
            stack, worker_id="t106-fault02", execution_unit_id="t106-fault02-unit"
        )
        operation_before = t050.operation(stack.dbname, run["id"], operation_id)
        approval_before = t050.approval_row(stack.dbname, approval["id"])
        assert router_records(success_server) == 0

        # -- the fault: exact-params approval replayed with changed params -----
        refused = t050.invoke_write(
            stack,
            resume_lease,
            policy_id=policy["id"],
            params=changed_params,
            logical_key=logical_key,
            operation_id=operation_id,
        )
        fault.check(
            "changed_params_refused_operation_conflict",
            refused.status == 409
            and refused.json["error"]["code"] == "operation_conflict",
            refused.text,
        )
        fault.check(
            "zero_writes_on_changed_params",
            router_records(success_server) == 0,
            success_server.records,
        )
        operation_after_refusal = t050.operation(stack.dbname, run["id"], operation_id)
        approval_after_refusal = t050.approval_row(stack.dbname, approval["id"])
        fault.check(
            "state_unchanged_by_refusal",
            operation_after_refusal == operation_before
            and approval_after_refusal == approval_before
            and t041.attempt_rows(stack.dbname, operation_id) == [],
            {
                "operation_before": operation_before,
                "operation_after": operation_after_refusal,
                "approval_before": approval_before,
                "approval_after": approval_after_refusal,
            },
        )

        # The old operation id cannot be reused for a different logical key.
        reused_id = t050.invoke_write(
            stack,
            resume_lease,
            policy_id=policy["id"],
            params=changed_params,
            logical_key=logical_key + ":other-call",
            operation_id=operation_id,
        )
        fault.check(
            "old_operation_id_not_reusable_for_new_call",
            reused_id.status == 409
            and reused_id.json["error"]["code"] == "operation_conflict"
            and router_records(success_server) == 0,
            reused_id.text,
        )

        # The exact params still dispatch once: the refusal spent nothing.
        exact = t050.invoke_write(
            stack,
            resume_lease,
            policy_id=policy["id"],
            params=params,
            logical_key=logical_key,
            operation_id=operation_id,
        )
        fault.check(
            "exact_params_still_succeed_once",
            exact.status == 200
            and exact.json.get("state") == "succeeded"
            and router_records(success_server) == 1,
            {"response": exact.text, "records": success_server.records},
        )
        fault.check(
            "one_attempt_for_the_sanctioned_write",
            len(t041.attempt_rows(stack.dbname, operation_id)) == 1,
            t041.attempt_rows(stack.dbname, operation_id),
        )

        # -- wrong params_sha256 decision -------------------------------------
        second_key = "t106:fault02:write:call-2"
        second = t050.invoke_write(
            stack,
            resume_lease,
            policy_id=policy["id"],
            params=params,
            logical_key=second_key,
        )
        fault.check(
            "second_write_parked",
            second.status == 200 and second.json.get("state") == "awaiting_approval",
            second.text,
        )
        approval_two = t050.approval_row(stack.dbname, second.json["approval_id"])
        wrong_hash = t050.decide(
            owner.creator,
            approval_two["id"],
            params_sha256="0" * 64,
        )
        fault.check(
            "wrong_params_hash_refused",
            wrong_hash.status == 409
            and wrong_hash.json["error"]["code"] == "approval_params_mismatch",
            wrong_hash.text,
        )
        fault.check(
            "wrong_hash_leaves_the_approval_pending",
            t050.approval_row(stack.dbname, approval_two["id"])["state"] == "pending"
            and t050.operation(
                stack.dbname, run["id"], second.json["operation_id"]
            )["state"]
            == "awaiting_approval"
            and router_records(success_server) == 1,
            t050.approval_row(stack.dbname, approval_two["id"]),
        )

        # -- stranger and admin cannot decide --------------------------------
        stranger = t041.secondary_owner(stack)
        stranger_read = t050.get_approval(stranger.creator, approval_two["id"])
        stranger_decide = t050.decide(
            stranger.creator, approval_two["id"], params_sha256=approval_two["params_sha256"]
        )
        admin_decide = t050.decide(
            owner.admin, approval_two["id"], params_sha256=approval_two["params_sha256"]
        )
        fault.check(
            "stranger_cannot_read_or_decide",
            stranger_read.status == 404 and stranger_decide.status == 404,
            {"read": stranger_read.status, "decide": stranger_decide.status},
        )
        fault.check(
            "admin_is_not_the_approver",
            admin_decide.status == 403
            and admin_decide.json["error"]["code"] == t050.APPROVER_MISMATCH,
            admin_decide.text,
        )
        fault.check(
            "refused_deciders_write_nothing",
            t050.approval_row(stack.dbname, approval_two["id"])["state"] == "pending"
            and router_records(success_server) == 1,
            success_server.records,
        )

        # -- cancel invalidates the pending approval; the late decision is 409
        cancelled = owner.creator.client.post(
            f"/api/v1/runs/{run['id']}/cancel", body={}, csrf=owner.creator.csrf
        )
        fault.check("run_cancel_accepted", cancelled.status == 202, cancelled.text)
        invalidated = t050.approval_row(stack.dbname, approval_two["id"])
        late = t050.decide(
            owner.creator, approval_two["id"], params_sha256=approval_two["params_sha256"]
        )
        fault.check(
            "late_decision_after_cancel_refused",
            invalidated["state"] == "invalidated"
            and late.status == 409
            and late.json["error"]["code"] in {t050.APPROVAL_NOT_PENDING, "run_state_conflict"}
            and router_records(success_server) == 1,
            {"approval": invalidated, "decision": late.text},
        )

        fault.ledger(
            "connector_endpoint",
            {
                "requests": success_server.records,
                "request_count": router_records(success_server),
                "requests_at_changed_params_refusal": 0,
            },
        )
        fault.ledger(
            "postgres",
            {
                "run": t050.run_row(stack.dbname, run["id"]),
                "approval_one": t050.approval_row(stack.dbname, approval["id"]),
                "approval_two": t050.approval_row(stack.dbname, approval_two["id"]),
                "approval_rows": t050.approval_rows(stack.dbname, run_id=run["id"]),
                "operation_one": t050.operation(stack.dbname, run["id"], operation_id),
                "operation_two": t050.operation(
                    stack.dbname, run["id"], second.json["operation_id"]
                ),
                "operation_rows": t041.operation_rows(stack.dbname, run["id"]),
                "attempts_one": t041.attempt_rows(stack.dbname, operation_id),
            },
        )
        fault.observed.update(
            {
                "changed_params_error": refused.json["error"]["code"],
                "wrong_hash_error": wrong_hash.json["error"]["code"],
                "stranger_status": stranger_decide.status,
                "admin_status": admin_decide.status,
                "late_decision_status": late.status,
                "writes": router_records(success_server),
            }
        )
        fault.command(
            "POST /internal/v1/runs/{id}/capabilities (changed params) -> 409 operation_conflict"
        )
        fault.command(
            "POST /api/v1/approvals/{id}/decision (wrong params_sha256) -> 409 approval_params_mismatch"
        )
        fault.mark_passed()


# ---------------------------------------------------------------------------
# FAULT-03: the write lands, the receipt is lost, unknown must be reconciled
# ---------------------------------------------------------------------------
def test_fault_03_lost_receipt_is_unknown_and_reconciles_without_resend(
    t106_stack, lost_receipt_server
) -> None:
    stack = t106_stack
    server = lost_receipt_server
    owner = t041.owner(stack)

    fault = fs.start(
        "FAULT-03",
        "The connector applies the write and the connection is dropped before the "
        "receipt; the operation is unknown, never auto-resent, and reconciles to one write",
        injection_point=(
            "tests/support/t051.py:39-70 `_DropHandler._handle` reads the complete "
            "request body (the write is really applied by the endpoint) and then "
            "calls socket.shutdown(SHUT_RDWR) with no response; the product's real "
            "HTTP client (internal/capabilities/connectors.go dispatchConnector) "
            "observes a transport error and markConnectorUnknown commits "
            "operations.state='unknown' + run_unknown_operations + "
            "runs.state='needs_reconciliation'."
        ),
        expected=[
            "the endpoint observes exactly one write request",
            "the operation is unknown (never succeeded/failed) and has no receipt",
            "a duplicate invoke does not send a second request",
            "the Run is not succeeded while the write is unknown (needs_reconciliation)",
            "owner evidence + POST /operations/{id}/reconcile settles it as succeeded",
            "the physical write count stays exactly 1",
        ],
    )
    with fault:
        prepared = t051.prepare_approved_write(stack, server, owner)
        fault.input.update(
            {
                "seed": "t106-fault03",
                "run_id": prepared["run"]["id"],
                "operation_id": prepared["operation_id"],
                "logical_key": prepared["logical_key"],
                "params": prepared["params"],
                "params_sha256_independent": fs.canonical_params(prepared["params"])[1],
                "policy_id": prepared["policy"]["id"],
                "policy_sha256": prepared["policy"]["policy_sha256"],
                "connector_origin": server.url,
            }
        )
        fault.check(
            "no_write_before_the_dispatch",
            len(server.records) == 0,
            server.records,
        )

        # -- the fault: the real endpoint applies the write, then drops --------
        server.respond_drop()
        response = t051.invoke_capability(stack, prepared["lease"], prepared)
        run_id = prepared["run"]["id"]
        operation_id = prepared["operation_id"]
        fault.check(
            "endpoint_received_exactly_one_write",
            len(server.records) == 1 and server.records[0]["method"] == "POST",
            server.records,
        )
        fault.check(
            "capability_answer_is_unknown_never_success",
            response.status == 200
            and response.json.get("state") == t051.STATE_UNKNOWN
            and response.json.get("state") not in {t051.STATE_SUCCEEDED, t051.STATE_FAILED},
            response.text,
        )
        operation = t051.operation_row(stack.dbname, run_id, operation_id)
        unknown = t051.unknown_row(stack.dbname, run_id, operation_id)
        attempts = t051.attempt_rows(stack.dbname, operation_id)
        run_at_unknown = t051.run_state(stack.dbname, run_id)
        fault.check(
            "durable_unknown_facts",
            operation["state"] == t051.STATE_UNKNOWN
            and operation["receipt_artifact_id"] is None
            and operation["last_error_code"] == "connector_transport_error"
            and operation["reconciliation_limitation"] == t051.LIMITATION_NO_IDEMPOTENCY
            and len(attempts) == 1
            and attempts[0]["state"] == "dispatching"
            and unknown is not None
            and unknown["resolved_at"] is None,
            {"operation": operation, "unknown": unknown, "attempts": attempts},
        )
        fault.check(
            "product_hash_matches_the_independent_hash",
            operation["params_sha256"] == fault.input["params_sha256_independent"],
            {
                "product": operation["params_sha256"],
                "independent": fault.input["params_sha256_independent"],
            },
        )
        fault.check(
            "run_is_not_succeeded_while_unknown",
            run_at_unknown["state"] != t051.STATE_SUCCEEDED,
            run_at_unknown,
        )
        data = response.json.get("data") or {}
        fault.check(
            "unknown_discloses_no_idempotency",
            data.get("requires_reconciliation") is True
            and data.get("limitation") == t051.LIMITATION_NO_IDEMPOTENCY,
            data,
        )

        # -- duplicate invoke / changed params never resend -------------------
        duplicate = t051.invoke_capability(stack, prepared["lease"], prepared)
        fault.check(
            "duplicate_invoke_never_resends",
            duplicate.status == 200
            and duplicate.json.get("state") == t051.STATE_UNKNOWN
            and len(server.records) == 1,
            {"response": duplicate.text, "records": len(server.records)},
        )
        changed = dict(prepared["params"])
        changed["body"] = {"amount": 9999, "currency": "CNY"}
        conflict = t048.invoke(
            stack,
            prepared["lease"],
            policy_id=prepared["policy"]["id"],
            params=changed,
            logical_key=prepared["logical_key"],
            operation_id=operation_id,
        )
        fault.check(
            "changed_params_after_unknown_refused",
            conflict.status == 409
            and conflict.json["error"]["code"] == "operation_conflict"
            and len(server.records) == 1,
            conflict.text,
        )

        # -- the attempt exits: the Run parks in needs_reconciliation ---------
        # The worker proposes success (a real output artifact) *after* the write
        # it cannot prove; the unknown guard must still park the Run instead of
        # letting the proposal become a success. Only reconciliation may settle
        # the unknown write and release the stored success.
        output_artifact = t051.seed_output_artifact(stack, owner.creator.principal_id)
        t051.link_output(
            stack, run_id, output_artifact, prepared["lease"]["attempt_token"]
        )
        t051.submit_succeeded_result(stack, prepared["lease"], output_artifact)
        t051.confirm_exit(stack, prepared["lease"])
        parked = t051.run_state(stack.dbname, run_id)
        fault.check(
            "run_parks_needs_reconciliation",
            parked["state"] == t051.STATE_NEEDS_RECONCILIATION
            and parked["error_code"] == "unknown_operations"
            and parked["finished_at"] is None,
            parked,
        )

        # -- owner evidence settles it without a second write ----------------
        evidence = t051.seed_evidence_artifact(
            stack,
            owner.creator.principal_id,
            operation_id=operation_id,
            run_id=run_id,
        )
        reconcile_key = "t106-f03-confirmed-success"
        settled = t051.reconcile(
            owner.creator,
            operation_id,
            conclusion=t051.CONCLUSION_SUCCESS,
            evidence_artifact_id=evidence,
            idempotency_key=reconcile_key,
        )
        fault.check(
            "reconcile_confirms_success",
            settled.status == 200
            and settled.json.get("state") == t051.STATE_SUCCEEDED,
            settled.text,
        )
        operation_final = t051.operation_row(stack.dbname, run_id, operation_id)
        unknown_final = t051.unknown_row(stack.dbname, run_id, operation_id)
        run_final = t051.run_state(stack.dbname, run_id)
        faults_after = t051.physical_write_count(stack.dbname, operation_id)
        fault.check(
            "physical_write_count_is_one",
            faults_after == 1 and len(server.records) == 1,
            {"attempts": faults_after, "endpoint_requests": len(server.records)},
        )
        fault.check(
            "evidence_is_the_receipt",
            operation_final["state"] == t051.STATE_SUCCEEDED
            and operation_final["receipt_artifact_id"] == evidence
            and operation_final["reconciliation_evidence_id"] == evidence
            and operation_final["reconciled_by"] == owner.creator.principal_id
            and unknown_final["resolved_at"] is not None,
            {"operation": operation_final, "unknown": unknown_final},
        )
        fault.check(
            "run_succeeded_after_reconciliation",
            run_final["state"] == t051.STATE_SUCCEEDED
            and run_final["finished_at"] is not None,
            run_final,
        )
        replay = t051.reconcile(
            owner.creator,
            operation_id,
            conclusion=t051.CONCLUSION_SUCCESS,
            evidence_artifact_id=evidence,
            idempotency_key=reconcile_key,
        )
        fault.check(
            "reconcile_replay_is_idempotent_and_never_resends",
            replay.status == 200
            and replay.json.get("state") == t051.STATE_SUCCEEDED
            and len(server.records) == 1
            and t051.physical_write_count(stack.dbname, operation_id) == 1,
            {"replay": replay.status, "records": len(server.records)},
        )

        item = t051.ledger_operation(owner.creator, run_id, operation_id)
        fault.ledger(
            "connector_endpoint",
            {"requests": server.records, "request_count": len(server.records)},
        )
        fault.ledger(
            "postgres",
            {
                "operation": operation_final,
                "attempts": t051.attempt_rows(stack.dbname, operation_id),
                "unknown": unknown_final,
                "run": run_final,
                "owner_ledger_item": item,
            },
        )
        fault.observed.update(
            {
                "initial_state": t051.STATE_UNKNOWN,
                "run_at_unknown": run_at_unknown["state"],
                "run_parked": parked["state"],
                "run_final": run_final["state"],
                "endpoint_requests": len(server.records),
                "physical_attempts": t051.physical_write_count(stack.dbname, operation_id),
                "evidence_artifact_id": evidence,
            }
        )
        fault.command(
            "POST /internal/v1/runs/{id}/capabilities -> 200 {state:'unknown'} (TCP reset after the write)"
        )
        fault.command(
            "POST /api/v1/operations/{id}/reconcile {conclusion:'confirmed_success', evidence_artifact_id}"
        )
        fault.mark_passed()
