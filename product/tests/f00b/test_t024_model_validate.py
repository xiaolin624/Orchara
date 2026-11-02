"""T024 acceptance: the developer's end-to-end synthetic ``model_validate`` job.

The maintenance process claims a real job, reads the candidate's fixture through
a signed job-scoped link, calls the platform model proxy, uploads a compatibility
report through the job-scoped upload/complete flow, waits for the finalize guard
to activate it, and submits ``succeeded`` with the report artifact. A second real
process services ``artifact_finalize`` (the spec's system_io pool) because T022's
maintenance loop is single-threaded; T026 owns the runtime pool split.

A failed compatibility verdict is a successful job with a failed report; only an
unconfigured provider fails the job, as a business failure with no retry.
"""

from __future__ import annotations

import json

from support import f00b, t024
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    t024_stack,
)


def wait_for_retry_available(dbname: str, job_id: str) -> None:
    """Wait on the database clock until the retry's backoff has elapsed."""
    f00b.wait_until(
        lambda: st.sql(
            dbname,
            "SELECT available_at <= now() FROM platform.background_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == "t",
        timeout=5.0,
        interval=0.01,
        what=f"the retry of job {job_id} to become available",
    )


def enqueue_model_validate(stack, candidate: dict, fixture: f00b.SeededArtifact, owner: str) -> dict:
    return f00b.enqueue_ok(
        stack,
        kind=t024.KIND_MODEL_VALIDATE,
        target_id=candidate["id"],
        owner_id=owner,
        dedup_key="t024-e2e:" + fixture.id,
        input_artifact_id=fixture.id,
    )


def test_the_handler_produces_a_validated_candidate_report(t024_stack) -> None:
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    fixture = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = enqueue_model_validate(t024_stack, candidate, fixture, owner)

    completed, reports, finalizer_log = t024.run_model_validate_once(t024_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["kind"] == t024.KIND_MODEL_VALIDATE
    assert job_reports[0]["result"] == "succeeded", job_reports[0]

    job = f00b.job_row(t024_stack.dbname, payload["id"])
    assert job["state"] == "succeeded", job
    assert job["error_code"] is None
    assert t024.job_result_artifact_id(t024_stack.dbname, payload["id"]), job

    report_artifact = f00b.artifact_row(t024_stack.dbname, t024.job_result_artifact_id(t024_stack.dbname, payload["id"]))
    assert report_artifact["state"] == "ready", report_artifact
    assert report_artifact["media_type"] == "application/json"
    assert report_artifact["purpose"] == "report"
    sealed = t024_stack.object_dir / report_artifact["object_key"]
    report_bytes = sealed.read_bytes()
    assert t024.sha256_hex(report_bytes) == report_artifact["sha256"]

    report = json.loads(report_bytes)
    assert report["kind"] == "model_compatibility"
    assert report["compatible"] is True, report
    assert report["check"]["name"] == "structured_output"
    assert report["fixture_sha256"] == t024.sha256_hex(fixture.content)
    assert report["fixture_artifact_id"] == fixture.id
    assert report["provider"] == "stub" and report["stub"] is True

    calls = t024.model_calls(t024_stack.dbname, payload["id"])
    assert len(calls) == 1, calls
    assert calls[0]["state"] == "succeeded"
    assert calls[0]["result_artifact_id"] == report["model_result_artifact_id"]
    assert int(calls[0]["tokens_in"]) > 0 and int(calls[0]["tokens_out"]) > 0
    result_artifact = f00b.artifact_row(t024_stack.dbname, calls[0]["result_artifact_id"])
    assert result_artifact["state"] == "ready"
    assert result_artifact["purpose"] == "model_result"


def test_a_failed_compatibility_verdict_is_a_report_not_a_job_failure(t024_stack) -> None:
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    fixture = t024.seed_fixture(
        t024_stack.dbname,
        t024_stack.object_dir,
        owner,
        content=t024.fixture_bytes("structured_output_invalid"),
    )
    payload = enqueue_model_validate(t024_stack, candidate, fixture, owner)

    completed, reports, finalizer_log = t024.run_model_validate_once(t024_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    assert f00b.job_reports(reports)[0]["result"] == "succeeded"

    job = f00b.job_row(t024_stack.dbname, payload["id"])
    assert job["state"] == "succeeded", job
    assert job["failure_class"] is None
    report_artifact = f00b.artifact_row(t024_stack.dbname, t024.job_result_artifact_id(t024_stack.dbname, payload["id"]))
    report = json.loads((t024_stack.object_dir / report_artifact["object_key"]).read_bytes())
    assert report["compatible"] is False, report
    assert report["check"]["reason"] == "structured_output", report

    calls = t024.model_calls(t024_stack.dbname, payload["id"])
    assert len(calls) == 1 and calls[0]["state"] == "succeeded", calls


def test_a_retry_replays_the_original_call_and_keeps_the_report_passed(t024_stack) -> None:
    """A retried job must reproduce the first call's answer and verdict.

    The first attempt performs the real model call and then fails later as an
    infrastructure failure (for example a storage error after the call), which
    returns the job to ready. The retry runs the real handler in a new process; it
    makes the same logical call, which is a replay. The compatibility report must
    stay ``compatible: true`` with the stub/provider markers and the original call
    and result ids, not be re-derived from a placeholder.
    """
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(t024_stack.dbname, t024_stack.object_dir, owner)
    fixture = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = enqueue_model_validate(t024_stack, candidate, fixture, owner)

    job = t024.claim_model_validate(t024_stack, payload["id"], candidate, fixture, owner)
    fixture_sha = t024.sha256_hex(fixture.content)
    call = t024_stack.internal_client().model_call(
        job.job_id,
        job.attempt_token,
        logical_key="compatibility:" + fixture_sha,
        model_profile_version_id=candidate["id"],
        params={
            "task": "model_compatibility",
            "fixture_sha256": fixture_sha,
            "check": "structured_output",
        },
    )
    assert call.status == 200, call.text
    assert call.json["data"]["check"]["passed"] is True, call.json
    assert call.json["data"]["provider"] == "stub"
    assert call.json["data"]["stub"] is True

    failed = t024_stack.internal_client().result(
        job.job_id, attempt_token=job.attempt_token, state="failed",
        error_code="transient_storage_io",
    )
    assert failed.status == 200, failed.text
    assert failed.json["state"] == "ready", failed.json

    wait_for_retry_available(t024_stack.dbname, payload["id"])
    completed, reports, finalizer_log = t024.run_model_validate_once(t024_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["result"] == "succeeded", job_reports[0]

    assert f00b.job_row(t024_stack.dbname, payload["id"])["state"] == "succeeded"
    report_artifact = f00b.artifact_row(
        t024_stack.dbname, t024.job_result_artifact_id(t024_stack.dbname, payload["id"])
    )
    report = json.loads((t024_stack.object_dir / report_artifact["object_key"]).read_bytes())
    # The replay returned the original answer: the report is the same passed
    # compatibility verdict, with the same call identity.
    assert report["compatible"] is True, report
    assert report["check"] == call.json["data"]["check"], report
    assert report["provider"] == "stub" and report["stub"] is True
    assert report["call_id"] == call.json["call_id"]
    assert report["model_result_artifact_id"] == call.json["result_artifact_id"]

    # No second physical call was billed: the replay reused the recorded row.
    calls = t024.model_calls(t024_stack.dbname, payload["id"])
    assert len(calls) == 1, calls
    assert calls[0]["state"] == "succeeded"
    assert calls[0]["id"] == call.json["call_id"]


def test_an_unconfigured_provider_fails_the_job_without_retrying(t024_stack) -> None:
    owner = t024.owner_id(t024_stack.dbname)
    candidate = t024.seed_candidate(
        t024_stack.dbname,
        t024_stack.object_dir,
        owner,
        provider="openai",
        model_id="gpt-test",
        endpoint_ref="env:ORCHARA_MODEL_ENDPOINT",
        secret_ref="env:ORCHARA_MODEL_SECRET",
    )
    fixture = t024.seed_fixture(t024_stack.dbname, t024_stack.object_dir, owner)
    payload = enqueue_model_validate(t024_stack, candidate, fixture, owner)

    completed, reports, finalizer_log = t024.run_model_validate_once(t024_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_report = f00b.job_reports(reports)[0]
    assert job_report["result"] == "failed", job_report
    assert job_report["error_code"] == "provider_not_configured", job_report

    job = f00b.job_row(t024_stack.dbname, payload["id"])
    assert job["state"] == "failed"
    assert job["failure_class"] == "business"
    assert job["attempt_count"] == 1
    assert t024.job_result_artifact_id(t024_stack.dbname, payload["id"]) is None
    assert t024.model_calls(t024_stack.dbname, payload["id"]) == []
