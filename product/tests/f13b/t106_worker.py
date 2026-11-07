"""T106 FAULT-01: the real worker OS process for the approval-restart fault.

Started by the test with ``subprocess.Popen`` (``python t106_worker.py <mode>
<spec.json>``) so the fault injects into a genuine operating-system process, not
a mocked "restart" flag.

``prepare``
    claims the queued start job over the real mTLS internal protocol, invokes one
    write-risk connector capability with the pre-registered operation id, submits
    the real T035 ``waiting_approval`` result and the supervisor exit fact, then
    holds a real PostgreSQL session open (``pg_stat_activity`` evidence) until the
    test kills the process tree.

``resume``
    claims the single resume job, replays the *same* operation id / logical key /
    params with the fresh attempt token, seals a real output artifact, submits the
    succeeded result and confirms the exit.

The worker role is the documented T048/T050/T051 seam: the graph executor has no
connector node (T101 J6b), so the connector capability call is made by the worker
mTLS role from this real process. Nothing else is simulated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

TESTS_ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ROOT = TESTS_ROOT.parent
for _entry in (str(TESTS_ROOT), str(PRODUCT_ROOT / "tools")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

import pg_dev  # noqa: E402
from support import t029, t031, t035  # noqa: E402
from support.certs import ClientCertificate  # noqa: E402


class _Certificates:
    def __init__(self, spec: dict) -> None:
        self.internal_url = spec["internal_url"]
        self.ca = SimpleNamespace(cert=Path(spec["ca"]))
        self._clients = {
            role: ClientCertificate(
                role, Path(spec[role]["cert"]), Path(spec[role]["key"])
            )
            for role in ("supervisor", "worker")
        }

    def client(self, role: str) -> ClientCertificate:
        return self._clients[role]


class _Stack:
    """The slice of the test stack a worker needs; all paths are real."""

    def __init__(self, spec: dict) -> None:
        self.certificates = _Certificates(spec)
        self.dsn = spec["dsn"]
        self.dbname = spec["dbname"]
        self.object_dir = Path(spec["object_dir"])
        self.binaries: dict = {}


def _write_facts(path: Path, document: dict) -> None:
    """Write the facts file in place.

    The parent polls the same file; on Windows an atomic ``os.replace`` fails
    while a reader has the destination open. A plain truncate+write is safe here
    because the reader tolerates a transiently partial JSON document and simply
    retries; every write is small and fully buffered.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def _invoke_connector(worker, spec: dict, attempt_token: str):
    return worker.post(
        f"/internal/v1/runs/{spec['run_id']}/capabilities",
        {
            "attempt_token": attempt_token,
            "operation_id": spec["operation_id"],
            "logical_key": spec["logical_key"],
            "capability_version": spec["policy_id"],
            "capability_kind": "connector",
            "params": spec["params"],
        },
    )


def _hold_db_session(spec: dict, facts_path: Path, facts: dict) -> None:
    """Hold a real PostgreSQL session open so the kill has database evidence.

    The session is a real ``psql`` child in this worker's process tree. It is
    kept *idle* (stdin stays open and no statement runs): a PostgreSQL backend
    executing ``pg_sleep`` does not poll the client socket on Windows, while an
    idle backend notices the closed connection immediately. The whole process
    tree is what the test kills with ``taskkill /F /T``.
    """
    env = dict(os.environ)
    env["PGPASSWORD"] = pg_dev.load_secret()
    env["PGAPPNAME"] = spec["application_name"]
    command = [
        pg_dev._tool("psql"),
        "-h", "127.0.0.1",
        "-p", str(pg_dev.DEFAULT_PORT),
        "-U", pg_dev.DEFAULT_USER,
        "-d", spec["dbname"],
    ]
    holder = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.PIPE,
    )
    facts.update(
        {
            "holding": True,
            "hold_application_name": spec["application_name"],
            "hold_pid": holder.pid,
            "hold_started_at": time.time(),
        }
    )
    _write_facts(facts_path, facts)
    while True:  # killed by the test's taskkill /F /T
        time.sleep(1)


def prepare(spec: dict, facts_path: Path) -> None:
    stack = _Stack(spec)
    supervisor = t029.execution_client(stack, "supervisor")
    worker = t029.execution_client(stack, "worker")
    facts: dict = {
        "mode": "prepare",
        "pid": os.getpid(),
        "started_at": time.time(),
        "run_id": spec["run_id"],
        "operation_id": spec["operation_id"],
        "logical_key": spec["logical_key"],
    }
    _write_facts(facts_path, facts)

    claim = supervisor.claim_job(
        worker_id=spec["worker_id"], execution_unit_id=spec["execution_unit_id"]
    )
    assert claim.status == 200, claim.text
    lease = claim.json
    assert lease["run_id"] == spec["run_id"], lease
    assert lease["job_kind"] == "start", lease
    facts.update({"job_id": lease["job_id"], "claimed_at": time.time(), "lease": lease})
    _write_facts(facts_path, facts)

    invoked = _invoke_connector(worker, spec, lease["attempt_token"])
    assert invoked.status == 200, invoked.text
    body = invoked.json
    assert body.get("state") == "awaiting_approval", body
    assert body.get("operation_id") == spec["operation_id"], body
    facts.update(
        {
            "invoke_state": body["state"],
            "approval_id": body["approval_id"],
            "invoked_at": time.time(),
        }
    )
    _write_facts(facts_path, facts)

    parked = t035.submit_waiting(
        worker, lease, checkpoint_id="capability:" + spec["operation_id"]
    )
    assert parked.status == 200, parked.text
    exited = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id=spec["execution_unit_id"],
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert exited.status == 200, exited.text
    facts.update(
        {
            "parked": True,
            "parked_at": time.time(),
            "exit_confirmed_at": time.time(),
            "attempt_token": lease["attempt_token"],
        }
    )
    _write_facts(facts_path, facts)

    _hold_db_session(spec, facts_path, facts)


def resume(spec: dict, facts_path: Path) -> None:
    stack = _Stack(spec)
    supervisor = t029.execution_client(stack, "supervisor")
    worker = t029.execution_client(stack, "worker")
    facts: dict = {
        "mode": "resume",
        "pid": os.getpid(),
        "started_at": time.time(),
        "run_id": spec["run_id"],
        "operation_id": spec["operation_id"],
        "logical_key": spec["logical_key"],
    }
    _write_facts(facts_path, facts)

    claim = supervisor.claim_job(
        worker_id=spec["worker_id"], execution_unit_id=spec["execution_unit_id"]
    )
    assert claim.status == 200, claim.text
    lease = claim.json
    assert lease["run_id"] == spec["run_id"], lease
    assert lease["job_kind"] == "resume", lease
    facts.update({"job_id": lease["job_id"], "claimed_at": time.time(), "lease": lease})
    _write_facts(facts_path, facts)

    invoked = _invoke_connector(worker, spec, lease["attempt_token"])
    assert invoked.status == 200, invoked.text
    body = invoked.json
    assert body.get("state") == "succeeded", body
    assert body.get("operation_id") == spec["operation_id"], body
    facts.update({"resume_state": body["state"], "invoked_at": time.time()})
    _write_facts(facts_path, facts)

    output_artifact = t031.seed_output_artifact(stack, spec["owner_id"])
    t031.link_output(
        spec["dbname"],
        spec["run_id"],
        output_artifact,
        attempt_token=lease["attempt_token"],
    )
    proposed = worker.submit_result(
        spec["run_id"],
        attempt_token=lease["attempt_token"],
        suggested_state="succeeded",
        output_artifact_id=output_artifact,
    )
    assert proposed.status == 200, proposed.text
    exited = supervisor.confirm_exit(
        lease["attempt_token"],
        execution_unit_id=spec["execution_unit_id"],
        evidence=t029.exit_evidence(lease, started=True, exit_code=0),
    )
    assert exited.status == 200, exited.text
    facts.update(
        {
            "completed": True,
            "output_artifact_id": output_artifact,
            "attempt_token": lease["attempt_token"],
            "finished_at": time.time(),
        }
    )
    _write_facts(facts_path, facts)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"prepare", "resume"}:
        print("usage: t106_worker.py prepare|resume <spec.json>", file=sys.stderr)
        return 2
    mode = argv[1]
    spec = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    facts_path = Path(spec.get("facts_path") or (argv[2] + ".facts.json"))
    if mode == "prepare":
        prepare(spec, facts_path)
    else:
        resume(spec, facts_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
