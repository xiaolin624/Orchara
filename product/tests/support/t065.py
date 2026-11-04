"""F13A/T065 real-stack helpers: async evaluation progress and failure classes.

Everything here drives the real platform:

* the public ``POST /api/v1/applications/{id}/evaluations`` submission;
* the real two-pool ``cmd/maintenance run`` process (the ``application_evaluate``
  handler in ``work``, ``artifact_finalize`` in ``system_io``);
* the real ``cmd/supervisor exec --once`` executor where a case's run must
  actually execute;
* the non-contract progress read (ADR 0016) and the sealed report bytes on disk.

Nothing here mocks the pools, the guard or the progress bridge. The helpers only
add the T065 vocabulary on top of the T052/T062 harness.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from support import f00b, t026, t052, t062, t033
from support import stack as st

PRODUCT_ROOT = st.PRODUCT_ROOT

# Re-exports of the real-stack helpers this ticket builds on.
minimal_owner = t062.minimal_owner
other_creator = t052.other_creator
draft_revision = t062.draft_revision
upload_dataset = t062.upload_dataset
dataset_document = t062.dataset_document
evaluation_case = t062.evaluation_case
assertion_run_state = t062.assertion_run_state
assertion_effect_count = t062.assertion_effect_count
evaluation_row = t062.evaluation_row
report_row = t062.report_row
report_count = t062.report_count
case_run_rows = t062.case_run_rows
run_row = t062.run_row
artifact_row = t062.artifact_row
report_document = t062.report_document
start_pools = t062.start_pools
stop_process = t062.stop_process
wait_for_evaluation = t062.wait_for_evaluation
supervisor_exec_env = t062.supervisor_exec_env
run_supervisor_exec = t062.run_supervisor_exec
exec_report = t062.exec_report
get_evaluation = t062.get_evaluation
get_report = t062.get_report
submit_evaluation = t062.submit_evaluation
submit_evaluation_for = t062.submit_evaluation_for
claim_evaluation = t062.claim_evaluation
wait_until = f00b.wait_until
new_uuid = f00b.new_uuid
admin_session = f00b.admin_session
parse_json_lines = f00b.parse_json_lines

# The progress projection ADR 0016 pins. A changed field set is a contract
# decision, not an implementation detail, so the tests assert it exactly.
PROGRESS_FIELDS = {
    "evaluation_id",
    "state",
    "report_status",
    "report_id",
    "total_cases",
    "completed_cases",
    "failed_cases",
    "error_code",
    "failure_class",
    "created_at",
    "finished_at",
}


# ---------------------------------------------------------------------------
# the progress read (non-contract)
# ---------------------------------------------------------------------------
def progress_path(evaluation_id: str) -> str:
    return f"/api/v1/evaluations/{evaluation_id}/progress"


def get_progress(session, evaluation_id: str):
    return session.client.get(progress_path(evaluation_id))


def progress_row(dbname: str, evaluation_id: str) -> dict:
    rows = st.sql_rows(
        dbname,
        "SELECT id::text AS id, state, error_code, report_id::text AS report_id, "
        "progress_total, progress_done, progress_failed, "
        "created_at::text AS created_at, finished_at::text AS finished_at "
        "FROM platform.evaluation_jobs "
        f"WHERE id = {st.quote_literal(evaluation_id)}::uuid",
    )
    assert len(rows) == 1, rows
    return rows[0]


def case_progress_rows(dbname: str, evaluation_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT case_id, ordinal, state, verdict, reason_code, run_id::text AS run_id "
        "FROM platform.evaluation_case_runs "
        f"WHERE evaluation_id = {st.quote_literal(evaluation_id)}::uuid ORDER BY ordinal, case_id",
    )


def wait_for_progress(
    session,
    evaluation_id: str,
    predicate,
    *,
    timeout: float = 120.0,
    what: str = "the evaluation progress",
) -> dict:
    """Poll the real read until ``predicate(progress)`` is true."""
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = get_progress(session, evaluation_id)
        assert response.status == 200, response.text
        last = response.json
        if predicate(last):
            return last
        time.sleep(0.1)
    raise AssertionError(f"{what} never became true; last progress: {last}")


# ---------------------------------------------------------------------------
# cases that produce each failure class (real runner decisions)
# ---------------------------------------------------------------------------
def cancelled_case(case_id: str = "cancel-case") -> dict:
    """A case whose run is really cancelled through the owner transition.

    The run reaches ``cancelled`` in the platform's own state machine (no
    supervisor needed), the runner records ``failed/run_cancelled`` and the
    report status is ``failed`` while the job succeeds: the business failure.
    """
    case = evaluation_case(
        case_id=case_id,
        assertions=[assertion_run_state("cancelled")],
    )
    case["input"] = {"interactions": [{"kind": "cancel"}]}
    return case


def blocked_case(case_id: str = "unsupported-target") -> dict:
    """A case this build refuses (no publication path): verdict ``blocked``.

    It never creates a run, so only the runner's per-case progress report can
    make it observable; the sealed report is ``not_run`` while the job succeeds.
    """
    return evaluation_case(
        case_id=case_id,
        assertions=[assertion_run_state("succeeded")],
        target_kind="published_version",
        target_id="published-target",
    )


def quiet_case(case_id: str = "quiet-case") -> dict:
    """A case no executor will run, so its run never reaches a rest state."""
    return evaluation_case(
        case_id=case_id,
        assertions=[assertion_run_state("succeeded")],
    )


# ---------------------------------------------------------------------------
# sealed objects: tampering for the infrastructure boundary
# ---------------------------------------------------------------------------
def remove_sealed_object(stack, artifact_id: str) -> str:
    """Delete one artifact's real sealed bytes, leaving the ready row intact.

    The platform still believes the artifact is ready (metadata is sound) but a
    download really 404s. That is a genuine infrastructure failure, not a mock.
    """
    row = artifact_row(stack.dbname, artifact_id)
    path = Path(stack.object_dir) / row["object_key"]
    assert path.exists(), f"the sealed object is missing before the fault: {row['object_key']}"
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest == row["sha256"], "the sealed object does not match its row"
    path.unlink()
    assert not path.exists()
    return row["object_key"]


def sealed_report_bytes(stack, report: dict) -> bytes:
    return t062.report_raw_bytes(stack, report)


def sealed_sha256(stack, artifact_id: str) -> tuple[bytes, str]:
    """Read one sealed object's bytes and its sha256 (re-verified against the row)."""
    row = artifact_row(stack.dbname, artifact_id)
    path = Path(stack.object_dir) / row["object_key"]
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    assert digest == row["sha256"], "the sealed bytes changed after creation"
    return content, digest


# ---------------------------------------------------------------------------
# maintenance pools (real binary, real dual pools)
# ---------------------------------------------------------------------------
def enqueue_standalone_finalize(stack, owner_id: str) -> tuple[str, str]:
    """Seed a real scanning artifact and enqueue its real artifact_finalize job.

    This is an independent system_io job used to prove the finalize pool keeps
    making progress while the only work slot is leased by an evaluation.
    """
    artifact = f00b.seed_artifact(
        stack.dbname,
        stack.object_dir,
        owner_id,
        content=b"t065 finalize while the work pool is saturated\n",
    )
    job = f00b.enqueue_ok(
        stack,
        kind="artifact_finalize",
        target_id=artifact.id,
        owner_id=owner_id,
        dedup_key="t065-congestion:" + artifact.id,
        expected_revision=1,
        expected_sha256=artifact.expected_sha256,
    )
    return artifact.id, job["id"]


def job_state(dbname: str, job_id: str) -> str:
    return f00b.job_row(dbname, job_id)["state"]


def get_background_job(session, job_id: str):
    return session.client.get(f"/api/v1/background-jobs/{job_id}")


def service_client(stack, scopes: tuple[str, ...]):
    """A real service-account API key client with exactly the given scopes."""
    from support import t054

    admin = f00b.admin_session(stack)
    return t054.service_key_client(stack, admin, display_name="svc-t065", scopes=scopes)


__all__ = [name for name in dir() if not name.startswith("_")]
