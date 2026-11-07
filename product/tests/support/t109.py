"""F13B/T109 fault-injection helpers: evidence recorder and grant/job probes.

T109 injects four real data/permission faults (EVALUATION.md §4 FAULT-09/11/12/14)
against the real stack. This module only supplies what the test file needs to make
those facts auditable:

* a small recorder that writes one JSON record per fault to
  ``product/reports/f13b/T109-faults.json`` only when ``F13B_WRITE_EVIDENCE=1``
  (the T101/T106/T107 convention);
* the F01 grant surface (``POST/DELETE /api/v1/admin/grants``) plus the
  authoritative ``platform.resource_grants`` / ``platform.policy_epochs`` reads;
* the background-job read model and the job-scoped upload path used to create a
  real ``artifact_finalize`` job through the API (so the dropped wakeup is the
  product's own notifier, not a test switch);
* the real ``cmd/maintenance`` process launcher used for the lost-wakeup and
  maintenance-restart half of FAULT-12.

Nothing here mocks a product path, and no product SQL/migration/contract is
touched: every helper drives an HTTP route or the compiled maintenance binary and
reads PostgreSQL back as the independent truth.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from support import f00b, t024
from support import stack as st

PRODUCT_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = PRODUCT_ROOT / "reports" / "f13b" / "T109-faults.json"

KIND_ARTIFACT_FINALIZE = "artifact_finalize"
KIND_DOCUMENT_INGEST = "document_ingest"

ENVIRONMENT = {
    "profile": "dev-stub (ORCHARA_PROFILE=dev-stub; no vendor credentials or endpoint)",
    "database": "isolated PostgreSQL 16.4 cluster at 127.0.0.1:55432; one throwaway database per stack",
    "api": "compiled cmd/api behind real HTTP, real filesystem object store, real mTLS internal listener",
    "maintenance": "compiled cmd/maintenance as a real OS process (append-only poller or `run --once`)",
    "supervisor": "compiled cmd/supervisor claim/exit protocol over real mTLS (no node executes here)",
    "sandbox": "WSL2/runsc not used: every graph in this ticket is start->...->end/call_app (no python node)",
    "notifier": "the API installs background.InProcessNotifier with no consumer, so every wakeup is dropped; "
    "a maintenance process claims from PostgreSQL polling. There is no Redis adapter in this build.",
    "not_run": [
        "a real vendor model / vendor 429/5xx endpoint (the dev stub's registered fault model ids are used and labelled)",
        "a Redis server or Redis wakeup adapter (this build has none; T022/T024 report it not_run)",
        "a real browser DOM (no Playwright/Chromium on this host)",
    ],
}


# ---------------------------------------------------------------------------
# clock / git
# ---------------------------------------------------------------------------
def utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def git_branch_and_commit() -> tuple[str | None, str | None]:
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


# ---------------------------------------------------------------------------
# F01 grant surface (application grants only in this build)
# ---------------------------------------------------------------------------
def create_grant(
    admin,
    *,
    principal_id: str,
    resource_id: str,
    resource_type: str = "application",
    action: str = "read",
):
    """One real admin grant (session cookie + CSRF), exactly the contract body."""
    return admin.client.post(
        "/api/v1/admin/grants",
        body={
            "principal_id": principal_id,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "action": action,
        },
        csrf=admin.csrf,
    )


def revoke_grant(admin, grant_id: str):
    """One real admin revoke: `DELETE /api/v1/admin/grants/{id}`, 204/404."""
    return admin.client.request(
        "DELETE", f"/api/v1/admin/grants/{grant_id}", csrf=admin.csrf
    )


def grant_rows(
    dbname: str,
    *,
    principal_id: str | None = None,
    resource_id: str | None = None,
) -> list[dict]:
    clauses = ["TRUE"]
    if principal_id is not None:
        clauses.append(f"principal_id = {st.quote_literal(principal_id)}::uuid")
    if resource_id is not None:
        clauses.append(f"resource_id = {st.quote_literal(resource_id)}::uuid")
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, principal_id::text AS principal_id, resource_type, "
        "resource_id::text AS resource_id, action, expires_at::text AS expires_at "
        "FROM platform.resource_grants WHERE "
        + " AND ".join(clauses)
        + " ORDER BY id",
    )


def policy_epoch(dbname: str) -> int:
    return int(
        st.sql_scalar(
            dbname,
            "SELECT revision FROM platform.policy_epochs WHERE scope = 'acl'",
        )
    )


# ---------------------------------------------------------------------------
# background jobs: the job-scoped upload path and PostgreSQL read model
# ---------------------------------------------------------------------------
def upload_artifact(stack, lease: dict, content: bytes, *, purpose: str = "report"):
    """Reserve one job-scoped artifact over real mTLS (201 with a signed PUT link)."""
    return stack.internal_client().upload(
        lease["job_id"],
        lease["attempt_token"],
        media_type="application/json",
        size_bytes=len(content),
        purpose=purpose,
    )


def complete_artifact(stack, lease: dict, content: bytes):
    """Fill the staging object over the real signed link, then complete it."""
    ticket = upload_artifact(stack, lease, content).json
    put = t024.put_link(ticket["upload_url"], content)
    assert put.status == 204, put.status
    return stack.internal_client().complete(
        lease["job_id"],
        ticket["artifact_id"],
        lease["attempt_token"],
        t024.sha256_hex(content),
    )


def background_jobs(
    dbname: str, *, kind: str | None = None, target_id: str | None = None
) -> list[dict]:
    clauses = ["TRUE"]
    if kind is not None:
        clauses.append("kind = " + st.quote_literal(kind))
    if target_id is not None:
        clauses.append(f"target_id = {st.quote_literal(target_id)}::uuid")
    return st.sql_rows(
        dbname,
        "SELECT id::text AS id, kind, target_id::text AS target_id, "
        "owner_id::text AS owner_id, state, error_code, failure_class, attempt_count, "
        "lease_token::text AS lease_token, lease_deadline::text AS lease_deadline, "
        "result_artifact_id::text AS result_artifact_id, "
        "finished_at::text AS finished_at FROM platform.background_jobs WHERE "
        + " AND ".join(clauses)
        + " ORDER BY created_at, id",
    )


def job_by_target(dbname: str, *, kind: str, target_id: str) -> dict:
    rows = background_jobs(dbname, kind=kind, target_id=target_id)
    assert len(rows) == 1, (kind, target_id, rows)
    return rows[0]


def start_maintenance(
    stack,
    *,
    kinds: str,
    log_path: Path,
    worker_id: str,
    poll_interval: str = "20ms",
) -> tuple[subprocess.Popen, object]:
    """Start one real compiled ``cmd/maintenance run`` process (long polling)."""
    log_handle = Path(log_path).open("w", encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        [
            str(stack.binaries["maintenance"]),
            "run",
            "--kinds",
            kinds,
            "--poll-interval",
            poll_interval,
            "--worker-id",
            worker_id,
        ],
        cwd=PRODUCT_ROOT,
        env=f00b.maintenance_env(stack),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return process, log_handle


def stop_process(process: subprocess.Popen, log_handle, *, timeout: float = 15.0) -> dict:
    """Terminate one real process and return the exit fact (for the evidence JSON)."""
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:  # pragma: no cover - stubborn process
        process.kill()
        process.wait(timeout=5)
    if log_handle is not None and not getattr(log_handle, "closed", True):
        log_handle.close()
    return {"pid": process.pid, "returncode": process.returncode}


def wait_job(
    dbname: str,
    job_id: str,
    states: set[str],
    *,
    timeout: float = 60.0,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        rows = st.sql_rows(
            dbname,
            "SELECT id::text AS id, kind, state, error_code, failure_class, "
            "attempt_count, lease_token::text AS lease_token, "
            "result_artifact_id::text AS result_artifact_id "
            "FROM platform.background_jobs WHERE "
            f"id = {st.quote_literal(job_id)}::uuid",
        )
        last = rows[0] if rows else {}
        if last.get("state") in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"background job {job_id} never reached {states}: {last}")


# ---------------------------------------------------------------------------
# small read models used by more than one fault
# ---------------------------------------------------------------------------
def count(dbname: str, table: str, where: str = "TRUE") -> int:
    return int(st.sql(dbname, f"SELECT count(*) FROM {table} WHERE {where}"))


def run_count(dbname: str) -> int:
    return count(dbname, "platform.runs")


def run_children_count(dbname: str) -> int:
    return count(dbname, "platform.run_children")


def new_uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# evidence recorder
# ---------------------------------------------------------------------------
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
        self.blocked: list[dict] = []

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
        not_run: list[str] | None = None,
        blocked: dict | None = None,
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
            "not_run": list(not_run or []),
            "blocked": blocked,
        }
        self.records.append(record)
        if blocked:
            self.blocked.append({"id": fault_id, **blocked})
        return record

    def fail(self, fault_id: str, *, title: str, error: str) -> None:
        self.failures.append(
            {"id": fault_id, "title": title, "error": error, "recorded_at": utc_now()}
        )

    def document(self) -> dict:
        produced = {record["id"] for record in self.records}
        passed = (
            len(self.records) == 4
            and not self.failures
            and all(record["verdict"] == "passed" for record in self.records)
        )
        return {
            "ticket": "T109",
            "issue": 110,
            "title": "fault injection: delete, rate limit, Redis wakeup and permission revoke",
            "status": "passed" if passed else "partial",
            "branch": self.branch,
            "commit": self.commit,
            "generated_at": utc_now(),
            "environment": ENVIRONMENT,
            "faults": sorted(self.records, key=lambda record: record["id"]),
            "blocked": self.blocked,
            "failures": self.failures,
            "produced_faults": sorted(produced),
            "not_covered": [
                "a real vendor model / a real vendor 429/5xx/truncated endpoint "
                "(the dev-stub fault model ids are used instead and labelled)",
                "the Redis-restart half of FAULT-12: this build has no Redis adapter, so only "
                "the PostgreSQL-notifier / SSE-replay half is executed; the Redis restart is blocked",
                "a real browser DOM for the pages (no Playwright/Chromium on this host)",
            ],
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
