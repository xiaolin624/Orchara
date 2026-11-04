"""F13A/T062 real-stack helpers: report integrity and administrator attribution.

Everything here drives the real platform: the public evaluation submission, the
real background job claim/result protocol over mTLS, the real job-scoped report
upload + artifact finalize guard, the real two-pool maintenance process and the
real ``cmd/supervisor exec`` executor. No guard is mocked: the crafted report
bytes travel through the same sealed artifact path a trusted runner uses.

The helpers intentionally expose only what the ticket needs:

* :func:`crafted_document` builds a *sealed report document* from the database
  facts of one evaluation, with explicit overrides so a test can break exactly
  one binding at a time;
* :func:`upload_ready_report` runs create -> PUT -> complete -> finalize -> ready;
* :func:`create_case_run` asks the server (not SQL) to create the case's run,
  which is how a nonexistent/unfinished/cancelled run is produced with real rows;
* :func:`get_attribution` reads the T062 administrator read.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from support import f00b, t024, t026, t027, t033, t052
from support import stack as st

PRODUCT_ROOT = st.PRODUCT_ROOT
KIND_APPLICATION_EVALUATE = t026.KIND_APPLICATION_EVALUATE
RUNNER_EVALUATOR = "orchara-platform-runner/1"

# The documented report codes the guard must answer with (background/job.go).
REPORT_BINDING_MISMATCH = "report_binding_mismatch"
REPORT_INVALID = "report_invalid"
STALE_ATTEMPT = "stale_attempt"


# ---------------------------------------------------------------------------
# identities, submissions and evaluations (re-exports of the T052 harness)
# ---------------------------------------------------------------------------
minimal_owner = t052.minimal_owner
draft_revision = t052.draft_revision
upload_dataset = t052.upload_dataset
dataset_document = t052.dataset_document
evaluation_case = t052.evaluation_case
assertion_run_state = t052.assertion_run_state
assertion_effect_count = t052.assertion_effect_count
evaluation_row = t052.evaluation_row
evaluation_count = t052.evaluation_count
report_row = t052.report_row
report_count = t052.report_count
case_run_rows = t052.case_run_rows
run_row = t052.run_row
artifact_row = t052.artifact_row
report_document = t052.report_document
start_pools = t052.start_pools
stop_process = t052.stop_process
wait_for_evaluation = t052.wait_for_evaluation
supervisor_exec_env = t052.supervisor_exec_env
run_supervisor_exec = t052.run_supervisor_exec
exec_report = t052.exec_report
get_evaluation = t052.get_evaluation
get_report = t052.get_report
submit_evaluation = t052.submit_evaluation


def submit_evaluation_for(stack, owner, cases: list[dict], *, case_timeout: str = "30s") -> str:
    """Submit one real evaluation for ``owner`` and return its id (202 checked)."""
    artifact_id = upload_dataset(stack, owner.creator, dataset_document(cases))
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    assert response.status == 202, response.text
    assert t052.evaluation_row(stack.dbname, response.json["id"])["state"] == "queued"
    return response.json["id"]


# ---------------------------------------------------------------------------
# the background job: claim, result, and the report artifact pipeline
# ---------------------------------------------------------------------------
def claim_evaluation(stack) -> tuple[object, dict]:
    """Claim the one ready ``application_evaluate`` job over real mTLS."""
    client = stack.internal_client()
    response = client.claim(kinds=[KIND_APPLICATION_EVALUATE])
    assert response.status == 200, response.text
    lease = response.json
    assert lease["kind"] == KIND_APPLICATION_EVALUATE, lease
    return client, lease


def submit_result(
    client,
    lease: dict,
    *,
    state: str = "succeeded",
    result_artifact_id: str | None = None,
    error_code: str | None = None,
    event_id: str | None = None,
):
    return client.result(
        lease["job_id"],
        attempt_token=lease["attempt_token"],
        state=state,
        result_artifact_id=result_artifact_id,
        error_code=error_code,
        event_id=event_id,
    )


def upload_ready_report(
    stack, client, lease: dict, content: bytes, *, purpose: str = "report"
) -> str:
    """Upload report bytes through the job-scoped path and drive the real finalize."""
    ticket = client.upload(
        lease["job_id"],
        lease["attempt_token"],
        size_bytes=len(content),
        purpose=purpose,
    )
    assert ticket.status == 201, ticket.text
    artifact_id = ticket.json["artifact_id"]
    pushed = t024.put_link(ticket.json["upload_url"], content)
    assert pushed.status in (200, 204), pushed.raw
    digest = hashlib.sha256(content).hexdigest()
    completed = client.complete(lease["job_id"], artifact_id, lease["attempt_token"], digest)
    assert completed.status == 202, completed.text
    _done, reports = f00b.maintenance_once(stack, "--kinds", "artifact_finalize")
    assert f00b.job_reports(reports), reports
    status = client.artifact_status(lease["job_id"], lease["attempt_token"], artifact_id)
    assert status.status == 200 and status.json["state"] == "ready", status.text
    return artifact_id


def create_case_run(client, lease: dict, *, case_id: str, input_value: dict) -> dict:
    """Create a real case run through the server-side bridge (never a test SQL run).

    This is the same job-scoped endpoint the trusted runner calls. It is how a
    test produces a real, incomplete run for the completion guard to read.
    """
    created = client.post(
        f"/internal/v1/background/jobs/{lease['job_id']}/evaluation-runs",
        {
            "attempt_token": lease["attempt_token"],
            "case_id": case_id,
            "input": input_value,
        },
    )
    assert created.status == 200, created.text
    return created.json


def cancel_run(session, run_id: str):
    """The owner's real cancel transition (the same endpoint a user calls)."""
    return session.client.post(f"/api/v1/runs/{run_id}/cancel", csrf=session.csrf)


# ---------------------------------------------------------------------------
# crafted sealed report documents
# ---------------------------------------------------------------------------
def snapshot_identity(dbname: str, snapshot_id: str) -> dict:
    return t027.snapshot_row(dbname, snapshot_id)


def _dataset_hash(dbname: str, artifact_id: str) -> str:
    row = artifact_row(dbname, artifact_id)
    assert row["sha256"], row
    return row["sha256"]


def passed_case(
    case_id: str,
    *,
    run_id: str | None = None,
    run_state: str = "succeeded",
    actual: str | None = None,
    expected: str = "succeeded",
) -> dict:
    """A internally-consistent passed case with one passing assertion outcome.

    The guard cannot re-run the checker from a sealed document; what it *can*
    prove is that the claimed pass is backed by this evaluation's real run. The
    ``facts.run_id`` is therefore the load-bearing member and is omitted only by
    tests that want to prove the guard refuses an unbacked pass.
    """
    facts: dict = {"effect_counts": {}, "citation_sources": []}
    if run_id is not None:
        facts["run_id"] = run_id
    if run_state is not None:
        facts["run_state"] = run_state
    return {
        "id": case_id,
        "status": "passed",
        "verdict": "passed",
        "assertions": [
            {
                "kind": "run_state",
                "expected": expected,
                "actual": actual if actual is not None else run_state,
                "passed": True,
            }
        ],
        "facts": facts,
    }


def crafted_document(
    stack,
    evaluation: dict,
    *,
    status: str = "passed",
    cases: list[dict] | None = None,
    checks: list[str] | None = None,
    evaluator: str = RUNNER_EVALUATOR,
    provenance: dict | None = None,
    **overrides,
) -> dict:
    """The sealed report document a trusted runner would write for ``evaluation``.

    Every field is taken from the database/target evaluation; ``overrides`` breaks
    exactly the binding a test wants to prove is load-bearing.
    """
    snapshot = (
        snapshot_identity(stack.dbname, evaluation["snapshot_id"])
        if evaluation.get("snapshot_id")
        else None
    )
    document: dict = {
        "report_version": 1,
        "kind": "evaluation",
        "evaluation_kind": evaluation["kind"],
        "evaluation_id": evaluation["id"],
        "dataset_artifact_id": evaluation["dataset_artifact_id"],
        "dataset_sha256": _dataset_hash(stack.dbname, evaluation["dataset_artifact_id"]),
        "manifest_sha256": (snapshot or {}).get("manifest_sha256", ""),
        "status": status,
        "checks": checks if checks is not None else ["run_state"],
        "case_count": len(cases or []),
        "cases": cases or [],
        "synthetic": False,
        "evaluator": evaluator,
    }
    if provenance is not None:
        document["provenance"] = provenance
    elif snapshot is not None:
        document["provenance"] = {
            "snapshot_id": snapshot["id"],
            "application_id": snapshot["application_id"],
            "draft_revision": snapshot["draft_revision"],
            "compiler_version": snapshot["compiler_version"],
            "worker_image_digest": snapshot["worker_image_digest"],
            "state_schema_version": snapshot["state_schema_version"],
            "dataset_artifact_id": evaluation["dataset_artifact_id"],
        }
    document.update(overrides)
    return document


def report_bytes(document: dict) -> bytes:
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")


# ---------------------------------------------------------------------------
# administrator attribution read (T062)
# ---------------------------------------------------------------------------
def attribution_path(evaluation_id: str) -> str:
    return f"/api/v1/admin/evaluations/{evaluation_id}/attribution"


def get_attribution(session, evaluation_id: str):
    return session.client.get(attribution_path(evaluation_id))


def admin_session(stack):
    return f00b.admin_session(stack)


# ---------------------------------------------------------------------------
# bounded waits and the executor
# ---------------------------------------------------------------------------
wait_until = f00b.wait_until
wait_for_retry_available = t026.wait_for_retry_available
new_uuid = f00b.new_uuid


def wait_job_state(dbname: str, job_id: str, states: set[str], *, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = f00b.job_row(dbname, job_id)
        if last["state"] in states:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {states}: {last}")


def drive_supervisor_until(stack, evaluation_id: str, *, deadline_s: float = 120.0) -> list[dict]:
    """Run the real executor until the evaluation is terminal (T052 pattern)."""
    env = supervisor_exec_env(stack)
    reports: list[dict] = []
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        row = evaluation_row(stack.dbname, evaluation_id)
        if row["state"] in {"succeeded", "failed", "cancelled"}:
            return reports
        completed = run_supervisor_exec(stack, env, timeout=180)
        report = exec_report(completed)
        reports.append(report)
        if report.get("status") == "idle":
            time.sleep(0.05)
    raise AssertionError(
        f"the evaluation never became terminal: {evaluation_row(stack.dbname, evaluation_id)}"
    )


def return_sealed_path(stack, artifact_id: str) -> Path:
    """Locate a sealed object path without reading or changing it."""
    row = artifact_row(stack.dbname, artifact_id)
    return Path(stack.object_dir) / row["object_key"]


def sealed_bytes(stack, artifact_id: str) -> bytes:
    """Read one sealed object's real bytes and re-verify the recorded hash."""
    row = artifact_row(stack.dbname, artifact_id)
    content = return_sealed_path(stack, artifact_id).read_bytes()
    assert hashlib.sha256(content).hexdigest() == row["sha256"], "the sealed bytes changed"
    return content


def report_raw_bytes(stack, report: dict) -> bytes:
    return sealed_bytes(stack, report["result_artifact_id"])


__all__ = [name for name in dir() if not name.startswith("_")]
