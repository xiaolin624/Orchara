"""T026 acceptance: the server-side completion guard binds and validates reports.

These tests do not run the handler. They claim the real application_evaluate job
over the real internal mTLS API, upload a *crafted* report through the real
job-scoped upload/complete/finalize path, and submit the result directly. The
guard must refuse every report it cannot prove and create no report row; a valid
not_run report must never be turned into a passed one.

Flow per case: claim (marks evaluation running) -> upload -> complete -> run the
system_io-only finalize -> submit succeeded with the report artifact id.
"""

from __future__ import annotations

import json

import pytest

from support import f00b, t024, t026
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)

KIND_APPLICATION_EVALUATE = t026.KIND_APPLICATION_EVALUATE


def _report_document(
    prepared: dict,
    *,
    status: str,
    checks: list[str],
    cases: list[dict],
    **overrides,
) -> dict:
    document = {
        "report_version": 1,
        "kind": "evaluation",
        "evaluation_kind": "model",
        "evaluation_id": prepared["evaluation_id"],
        "dataset_artifact_id": prepared["dataset"].id,
        "dataset_sha256": t026.sha256_hex(prepared["dataset"].content),
        "manifest_sha256": prepared["candidate"]["manifest_sha256"],
        "status": status,
        "checks": checks,
        "case_count": len(cases),
        "cases": cases,
        "synthetic": True,
        "evaluator": "test-crafted/1",
    }
    document.update(overrides)
    return document


def _claim(stack):
    client = stack.internal_client()
    response = client.claim(kinds=[KIND_APPLICATION_EVALUATE])
    assert response.status == 200, response.text
    lease = response.json
    return client, lease


def _upload_ready_report(stack, client, lease, content: bytes) -> str:
    """Upload a report and drive the real finalize of that artifact."""
    ticket = client.upload(
        lease["job_id"],
        lease["attempt_token"],
        size_bytes=len(content),
        purpose="report",
    )
    assert ticket.status == 201, ticket.text
    artifact_id = ticket.json["artifact_id"]
    pushed = t024.put_link(ticket.json["upload_url"], content)
    assert pushed.status in (200, 204), pushed.raw

    digest = t026.sha256_hex(content)
    completed = client.complete(lease["job_id"], artifact_id, lease["attempt_token"], digest)
    assert completed.status == 202, completed.text

    _done, reports = f00b.maintenance_once(stack, "--kinds", "artifact_finalize")
    assert f00b.job_reports(reports), reports
    status = client.artifact_status(lease["job_id"], lease["attempt_token"], artifact_id)
    assert status.status == 200, status.text
    assert status.json["state"] == "ready", status.json
    return artifact_id


def _submit_success(stack, client, lease, artifact_id: str):
    return client.result(
        lease["job_id"],
        attempt_token=lease["attempt_token"],
        state="succeeded",
        result_artifact_id=artifact_id,
    )


def test_a_wrong_manifest_hash_is_refused(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack)
    client, lease = _claim(t026_stack)
    document = _report_document(
        prepared,
        status="passed",
        checks=["dataset_schema", "case_sha256"],
        cases=[{"id": "c1", "status": "passed"}],
        manifest_sha256="1" * 64,
    )
    artifact_id = _upload_ready_report(
        t026_stack, client, lease, json.dumps(document).encode("utf-8")
    )
    response = _submit_success(t026_stack, client, lease, artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "failed", response.json
    assert response.json["error_code"] == "report_binding_mismatch"

    evaluation = t026.evaluation_row(t026_stack.dbname, prepared["evaluation_id"])
    assert evaluation["state"] == "failed"
    assert evaluation["error_code"] == "report_binding_mismatch"
    assert evaluation["report_id"] is None
    assert t026.report_count(t026_stack.dbname) == 0


def test_a_wrong_dataset_hash_is_refused(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack)
    client, lease = _claim(t026_stack)
    document = _report_document(
        prepared,
        status="passed",
        checks=["dataset_schema", "case_sha256"],
        cases=[{"id": "c1", "status": "passed"}],
        dataset_sha256="2" * 64,
    )
    artifact_id = _upload_ready_report(
        t026_stack, client, lease, json.dumps(document).encode("utf-8")
    )
    response = _submit_success(t026_stack, client, lease, artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "report_binding_mismatch"
    assert t026.report_count(t026_stack.dbname) == 0


def test_a_malformed_report_is_refused(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack)
    client, lease = _claim(t026_stack)
    artifact_id = _upload_ready_report(t026_stack, client, lease, b'{"not":"a report"')
    response = _submit_success(t026_stack, client, lease, artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "failed"
    assert response.json["error_code"] == "report_invalid"
    assert t026.evaluation_row(t026_stack.dbname, prepared["evaluation_id"])["state"] == "failed"
    assert t026.report_count(t026_stack.dbname) == 0


@pytest.mark.parametrize(
    ("label", "document_kwargs"),
    [
        ("empty cases claimed passed", {"status": "passed", "cases": [], "checks": ["dataset_schema"]}),
        (
            "failed with no failed case",
            {"status": "failed", "cases": [{"id": "c1", "status": "passed"}],
             "checks": ["dataset_schema", "case_sha256"]},
        ),
        ("no checks", {"status": "passed", "cases": [{"id": "c1", "status": "passed"}], "checks": []}),
        (
            "case count disagrees",
            {"status": "passed", "cases": [{"id": "c1", "status": "passed"}],
             "checks": ["dataset_schema"], "case_count": 2},
        ),
    ],
)
def test_an_illegal_verdict_is_report_invalid(t026_stack, label, document_kwargs) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack)
    client, lease = _claim(t026_stack)
    cases = document_kwargs.pop("cases")
    checks = document_kwargs.pop("checks")
    status = document_kwargs.pop("status")
    document = _report_document(
        prepared,
        status=status,
        checks=checks,
        cases=cases,
        **document_kwargs,
    )
    artifact_id = _upload_ready_report(
        t026_stack, client, lease, json.dumps(document).encode("utf-8")
    )
    response = _submit_success(t026_stack, client, lease, artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "failed", (label, response.json)
    assert response.json["error_code"] == "report_invalid", (label, response.json)
    assert t026.report_count(t026_stack.dbname) == 0


def test_a_valid_not_run_report_is_recorded_but_is_never_passed(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(t026_stack, cases=[])
    client, lease = _claim(t026_stack)
    document = _report_document(
        prepared,
        status="not_run",
        checks=["dataset_schema"],
        cases=[],
    )
    artifact_id = _upload_ready_report(
        t026_stack, client, lease, json.dumps(document).encode("utf-8")
    )
    response = _submit_success(t026_stack, client, lease, artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.json

    evaluation = t026.evaluation_row(t026_stack.dbname, prepared["evaluation_id"])
    assert evaluation["state"] == "succeeded"
    assert evaluation["report_id"] is not None
    report = t026.report_row(t026_stack.dbname, evaluation["report_id"])
    assert report["status"] == "not_run", report
