"""T052 acceptance: the boundaries a report may never cross.

Each test drives the real submission + real pools. A dataset the platform refuses,
a target this build cannot execute, a run the owner can no longer access, a failed
hard check next to a perfect informational quality slot, and a *forged* passed
report submitted through the job-scoped upload path must all fail closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from support import f00b, t024, t026, t052
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submit(stack, owner, cases, *, case_timeout: str = "30s") -> str:
    artifact_id = t052.upload_dataset(
        stack, owner.creator, t052.dataset_document(cases)
    )
    response = t052.submit_evaluation(
        owner.creator,
        owner.application_id,
        draft_revision=t052.draft_revision(owner.creator, owner.application_id),
        dataset_artifact_id=artifact_id,
    )
    assert response.status == 202, response.text
    return response.json["id"]


def _run_pools(stack, evaluation_id: str, tmp_path: Path, *, case_timeout: str = "30s") -> None:
    log = tmp_path / "pools.log"
    process, handle = t052.start_pools(stack, log_path=log, case_timeout=case_timeout)
    try:
        t052.wait_for_evaluation(stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=90)
    finally:
        t052.stop_process(process, handle, log)


def test_a_failed_hard_check_wins_over_the_quality_slot(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    cancelled = t052.evaluation_case(
        case_id="claims-success-but-was-cancelled",
        # The hard check demands success; the run's real terminal state is
        # cancelled, so this must fail no matter what a quality score says.
        assertions=[t052.assertion_run_state("succeeded")],
    )
    cancelled["input"] = {"interactions": [{"kind": "cancel"}]}
    evaluation_id = _submit(t052_stack, owner, [cancelled])
    _run_pools(t052_stack, evaluation_id, tmp_path)

    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "failed", report
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    case = document["cases"][0]
    assert case["status"] == "failed", case
    assert case["assertions"][0]["expected"] == "succeeded", case["assertions"]
    assert case["assertions"][0]["actual"] == "cancelled", case["assertions"]
    # The quality slot exists, is explicitly informational, and has no score.
    quality = document["quality"]
    assert quality["informational"] is True, quality
    assert quality["adapter"] == "not_installed", quality
    assert quality["score"] is None, quality
    assert document["status"] == "failed", document
    # A failed report is a *successful* job with no passed verdict anywhere.
    assert report["status"] != "passed"


def test_a_published_version_target_is_blocked_and_never_passed(t052_stack, tmp_path: Path) -> None:
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit(
            owner,
        [
            t052.evaluation_case(
                case_id="published-target",
                assertions=[t052.assertion_run_state("succeeded")],
                target_kind="published_version",
                target_id="fixture-sales-v1",
            )
        ],
    )
    _run_pools(t052_stack, evaluation_id, tmp_path)
    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    case = document["cases"][0]
    assert case["status"] == "blocked" and case["verdict"] == "blocked", case
    assert case["reason_code"] == "unsupported_target", case
    assert "published_version" in case["detail"], case
    # No run was created for a target this build cannot execute.
    assert t052.case_run_rows(t052_stack.dbname, evaluation_id) == []
    assert "published_version targets are not executable" in " ".join(document["limits"])


def test_the_collector_refuses_an_application_withdrawn_after_submit(
    t052_stack, tmp_path: Path
) -> None:
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit(
            owner,
        [
            t052.evaluation_case(
                case_id="withdrawn-app",
                assertions=[t052.assertion_run_state("succeeded")],
            )
        ],
    )
    # The owner loses access between submission and collection: the collector
    # must refuse, not collect from a cached authorization.
    t052.archive_application(t052_stack.dbname, owner.application_id)
    _run_pools(t052_stack, evaluation_id, tmp_path)

    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    assert evaluation["state"] == "succeeded", evaluation
    report = t052.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] != "passed", report
    document = t052.report_document(t052_stack, report["result_artifact_id"])
    case = document["cases"][0]
    assert case["status"] == "blocked", case
    assert case["reason_code"] == "collection_refused", case
    assert "evaluation_not_visible" in case["detail"], case
    assert t052.case_run_rows(t052_stack.dbname, evaluation_id) == []


def test_a_forged_passed_report_is_refused_and_records_no_report(t052_stack) -> None:
    """The guard is the only report writer, and an empty checker is not a pass.

    This claims the real ``application_evaluate`` job over mTLS, uploads a
    crafted ``passed`` report whose passed case carries no assertion evidence,
    drives the real artifact finalize, and submits succeeded. The guard must
    refuse it: the job fails with ``report_invalid``, the evaluation fails and no
    report row exists.
    """
    owner = t052.minimal_owner(t052_stack)
    evaluation_id = _submit(
            owner,
        [
            t052.evaluation_case(
                case_id="forged",
                assertions=[t052.assertion_run_state("succeeded")],
            )
        ],
    )
    evaluation = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    client = t052_stack.internal_client()
    claimed = client.claim(kinds=[t026.KIND_APPLICATION_EVALUATE])
    assert claimed.status == 200, claimed.text
    lease = claimed.json

    dataset_row = t052.artifact_row(t052_stack.dbname, evaluation["dataset_artifact_id"])
    snapshot_manifest = st.sql(
        t052_stack.dbname,
        "SELECT manifest_sha256 FROM platform.execution_snapshots "
        f"WHERE id = {st.quote_literal(evaluation['snapshot_id'])}::uuid",
    )
    document = {
        "report_version": 1,
        "kind": "evaluation",
        "evaluation_kind": "application",
        "evaluation_id": evaluation_id,
        "dataset_artifact_id": evaluation["dataset_artifact_id"],
        "dataset_sha256": dataset_row["sha256"],
        "manifest_sha256": snapshot_manifest,
        "status": "passed",
        "checks": ["run_state"],
        "case_count": 1,
        "cases": [{"id": "forged", "status": "passed", "verdict": "passed"}],
        "synthetic": False,
        "evaluator": "orchara-platform-runner/1",
        "provenance": {"snapshot_id": evaluation["snapshot_id"]},
    }
    content = json.dumps(document).encode("utf-8")
    ticket = client.upload(
        lease["job_id"], lease["attempt_token"], size_bytes=len(content), purpose="report"
    )
    assert ticket.status == 201, ticket.text
    artifact_id = ticket.json["artifact_id"]
    pushed = t024.put_link(ticket.json["upload_url"], content)
    assert pushed.status in (200, 204), pushed.raw
    digest = hashlib.sha256(content).hexdigest()
    completed = client.complete(lease["job_id"], artifact_id, lease["attempt_token"], digest)
    assert completed.status == 202, completed.text
    f00b.maintenance_once(t052_stack, "--kinds", "artifact_finalize")
    status = client.artifact_status(lease["job_id"], lease["attempt_token"], artifact_id)
    assert status.status == 200 and status.json["state"] == "ready", status.text

    response = client.result(
        lease["job_id"],
        attempt_token=lease["attempt_token"],
        state="succeeded",
        result_artifact_id=artifact_id,
    )
    assert response.status == 200, response.text
    assert response.json["state"] == "failed", response.json
    assert response.json["error_code"] == "report_invalid", response.json

    failed = t052.evaluation_row(t052_stack.dbname, evaluation_id)
    assert failed["state"] == "failed", failed
    assert failed["report_id"] is None, failed
    assert t052.report_count(t052_stack.dbname) == 0
