"""T100 journey 2: model registration -> validate -> evaluation read -> passed report.

The journey uses the product's real model-registry entry points:

* ``POST /models`` (admin + CSRF) registers the candidate;
* ``POST /models/{id}/validate`` (admin + CSRF + ``Idempotency-Key``) creates the
  real evaluation job;
* the real ``cmd/maintenance`` process (with a real ``artifact_finalize`` worker
  beside it, because the handler waits for its own report to be sealed) runs
  ``model_validate``;
* ``GET /evaluations/{id}``, ``GET /evaluation-reports/{id}`` and ``GET /models/{id}``
  are the contract reads.

Independent truth is the model/evaluation/report rows in PostgreSQL, the sealed
report and dataset bytes in the object directory, and the model-call ledger row.
The profile is ``dev-stub``: this proves the registry + validation machinery, not
a real vendor model's quality (recorded as blocked/not asserted).
"""

from __future__ import annotations

import secrets

from support import f00b, stack as st, t024, t039
from t100_support import sha256_hex


def _count(stack, table: str) -> int:
    return int(st.sql(stack.dbname, f"SELECT count(*) FROM platform.{table}"))


def run_models_journey(stack, journey) -> None:
    db = stack.dbname
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, "t100.model-reader-" + secrets.token_hex(3), roles=("creator",)
    )

    # -- 1. register: 201 pending, real row ------------------------------------
    model = t039.register_or_fail(admin)
    assert model["state"] == "pending", model
    candidate = t039.model_row(db, model["id"])
    assert candidate["provider"] == "stub" and candidate["model_id"] == "stub-model-1", candidate
    assert candidate["endpoint_ref"] == t039.DEFAULT_ENDPOINT, candidate
    assert candidate["data_destination"] == t039.DEFAULT_DESTINATION, candidate
    assert candidate["validation_state"] == "pending", candidate
    assert len(candidate["manifest_sha256"]) == 64, candidate
    journey.fact("model_registration", {
        "model_profile_version_id": model["id"],
        "provider": candidate["provider"],
        "model_id": candidate["model_id"],
        "endpoint_ref": candidate["endpoint_ref"],
        "data_destination": candidate["data_destination"],
        "manifest_sha256": candidate["manifest_sha256"],
    })
    journey.assertion(
        "POST /models by the admin returns 201 {id, state:pending}; PostgreSQL holds a real "
        "pending model profile version with a 64-hex manifest and the stub destination"
    )

    # Authorization boundaries: registration and validation are admin-only.
    non_admin_register = t039.register(creator)
    assert non_admin_register.status == 403, non_admin_register.text
    assert non_admin_register.json["error"]["code"] == "forbidden", non_admin_register.text
    non_admin_validate = t039.validate(creator, model["id"], key=str(secrets.token_hex(8)))
    assert non_admin_validate.status == 403, non_admin_validate.text
    assert non_admin_validate.json["error"]["code"] == "forbidden", non_admin_validate.text
    assert _count(stack, "background_jobs") == 0, "a refused validate created a job"
    journey.assertion(
        "a non-admin creator gets 403 forbidden for POST /models and POST /models/{id}/validate, "
        "and no background job is created"
    )

    missing_key = t039.validate(admin, model["id"])
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text
    assert missing_key.json["error"]["details"] == {"header": "Idempotency-Key"}, missing_key.text
    assert _count(stack, "background_jobs") == 0
    journey.assertion(
        "validate without Idempotency-Key is 400 idempotency_key_required (located on the "
        "header) and creates no background job"
    )

    # -- 2. validate: 202 evaluation job, idempotent replay --------------------
    key = "t100-model-validate-" + secrets.token_hex(6)
    accepted = t039.validate(admin, model["id"], key=key)
    assert accepted.status == 202, accepted.text
    evaluation = accepted.json
    assert set(evaluation) == {
        "id", "kind", "state", "background_job_id", "report_id", "error_code",
    }, evaluation
    assert evaluation["kind"] == "model", evaluation
    assert evaluation["state"] == "queued", evaluation
    assert evaluation["report_id"] is None and evaluation["error_code"] is None, evaluation
    replay = t039.validate(admin, model["id"], key=key)
    assert replay.status == 202, replay.text
    assert replay.json == evaluation, replay.text
    assert _count(stack, "evaluation_jobs") == 1, "the same key created a second evaluation"
    assert _count(stack, "background_jobs") == 1, "the same key created a second job"
    job_row = f00b.job_row(db, evaluation["background_job_id"])
    assert job_row["kind"] == "model_validate" and job_row["state"] == "ready", job_row
    journey.fact("evaluation_submission", {
        "evaluation_id": evaluation["id"],
        "background_job_id": evaluation["background_job_id"],
        "idempotency_key": key,
        "replay_created_second_job": False,
    })
    journey.assertion(
        "POST /models/{id}/validate returns 202 EvaluationJob(kind=model, state=queued); the "
        "same Idempotency-Key replays the identical body and creates exactly one evaluation + "
        "one model_validate background job"
    )

    # -- 3. the real maintenance process runs the validation ------------------
    completed, reports, finalizer_log = t039.run_validation(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr, finalizer_log[-2000:])
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["kind"] == "model_validate", job_reports
    assert job_reports[0]["result"] == "succeeded", job_reports
    journey.assertion(
        "the real `cmd/maintenance run --once --kinds model_validate` (beside a real "
        "artifact_finalize worker) claims and completes the job with result=succeeded"
    )

    # -- 4. contract reads: evaluation, report, model --------------------------
    passed = t039.get_model(admin, model["id"])
    assert passed.status == 200, passed.text
    assert set(passed.json) == t039.PROFILE_KEYS, passed.json
    assert passed.json["validation_state"] == "passed", passed.json
    report_id = passed.json["evaluation_report_id"]
    assert report_id, passed.json

    evaluation_read = admin.client.get(f"/api/v1/evaluations/{evaluation['id']}")
    assert evaluation_read.status == 200, evaluation_read.text
    assert set(evaluation_read.json) == {
        "id", "kind", "state", "background_job_id", "report_id", "error_code",
    }, evaluation_read.json
    assert evaluation_read.json["state"] == "succeeded", evaluation_read.json
    assert evaluation_read.json["report_id"] == report_id, evaluation_read.json

    report_read = admin.client.get(f"/api/v1/evaluation-reports/{report_id}")
    assert report_read.status == 200, report_read.text
    assert set(report_read.json) == {
        "id", "status", "manifest_sha256", "result_artifact_id",
    }, report_read.json
    assert report_read.json["status"] == "passed", report_read.json
    assert report_read.json["manifest_sha256"] == candidate["manifest_sha256"], report_read.json
    journey.assertion(
        "GET /evaluations/{id} is 200 state=succeeded with the report id; "
        "GET /evaluation-reports/{id} is 200 status=passed; GET /models/{id} reports "
        "validation_state=passed and the same report id"
    )

    # -- 5. independent truth: rows, sealed report, sealed dataset, ledger -----
    evaluation_row = t039.evaluation_for_model(db, model["id"])
    assert evaluation_row["state"] == "succeeded", evaluation_row
    assert evaluation_row["report_id"] == report_id, evaluation_row
    assert evaluation_row["background_job_id"] == evaluation["background_job_id"], evaluation_row
    assert evaluation_row["error_code"] is None, evaluation_row

    model_after = t039.model_row(db, model["id"])
    assert model_after["validation_state"] == "passed", model_after
    assert model_after["evaluation_report_id"] == report_id, model_after
    report_row = t039.report_row(db, report_id)
    assert report_row["status"] == "passed", report_row
    assert report_row["manifest_sha256"] == model_after["manifest_sha256"], report_row
    dataset_bytes = t039.sealed_content(
        stack.object_dir, t039.artifact_row(db, evaluation_row["dataset_artifact_id"])
    )
    assert report_row["dataset_sha256"] == sha256_hex(dataset_bytes), report_row

    document = t039.report_document(stack, report_id)
    assert document["kind"] == "model_compatibility", document
    assert document["model_profile_version_id"] == model["id"], document
    assert document["provider"] == "stub" and document["stub"] is True, document
    assert document["compatible"] is True and document["check"]["passed"] is True, document
    assert document["check"]["name"] == "structured_output", document
    assert document["fixture_artifact_id"] == evaluation_row["dataset_artifact_id"], document
    assert document["fixture_sha256"] == report_row["dataset_sha256"], document

    ledger = t024.model_calls(db, evaluation["background_job_id"])
    assert len(ledger) == 1, ledger
    assert ledger[0]["state"] == "succeeded", ledger
    assert ledger[0]["background_job_id"] == evaluation["background_job_id"], ledger
    assert ledger[0]["model_profile_version_id"] == model["id"], ledger
    # The ledger's result artifact is the adapter call's own completion artifact,
    # distinct from the sealed compatibility report the handler registered.
    call_artifact = t039.artifact_row(db, ledger[0]["result_artifact_id"])
    assert call_artifact["state"] == "ready", call_artifact
    assert call_artifact["owner_id"] == ledger[0]["owner_id"], (call_artifact, ledger)
    assert t039.sealed_content(stack.object_dir, call_artifact), "the call artifact has no bytes"
    journey.fact("independent_truth", {
        "model_validation_state": model_after["validation_state"],
        "evaluation_state": evaluation_row["state"],
        "report_status": report_row["status"],
        "report_manifest_sha256": report_row["manifest_sha256"],
        "dataset_sha256_matches_sealed_bytes": True,
        "report_provider": document["provider"],
        "report_stub": document["stub"],
        "model_calls_ledger_rows": len(ledger),
    })
    journey.assertion(
        "independent truth agrees: model row passed + report bound; evaluation row succeeded; "
        "report row passed; the sealed dataset's sha256 equals the report's dataset_sha256; the "
        "sealed compatibility document says provider=stub, stub=true, compatible=true; the "
        "model-call ledger holds exactly one succeeded row bound to the report artifact"
    )

    visible = t039.list_models(creator)
    assert visible.status == 200, visible.text
    assert [item["id"] for item in visible.json["items"]] == [model["id"]], visible.json
    journey.assertion(
        "a non-admin creator sees the passed model in GET /models (validation controls visibility)"
    )

    # -- 6. failure boundaries: incompatibility and an unconfigured provider ---
    incompatible = t039.register_or_fail(admin, model_id="stub-model-incompatible")
    accepted = t039.validate(admin, incompatible["id"], key="t100-incompatible-" + secrets.token_hex(6))
    assert accepted.status == 202, accepted.text
    completed, reports, _log = t039.run_validation(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "succeeded", reports
    failed_model = t039.get_model(admin, incompatible["id"])
    assert failed_model.status == 200, failed_model.text
    assert failed_model.json["validation_state"] == "failed", failed_model.json
    failed_report_id = failed_model.json["evaluation_report_id"]
    assert failed_report_id, failed_model.json
    failed_report = t039.report_row(db, failed_report_id)
    assert failed_report["status"] == "failed", failed_report
    failed_document = t039.report_document(stack, failed_report_id)
    assert failed_document["compatible"] is False, failed_document
    assert failed_document["check"]["passed"] is False, failed_document
    assert failed_document["check"]["reason"] == "structured_output", failed_document
    hidden = t039.list_models(creator)
    assert hidden.status == 200, hidden.text
    assert [item["id"] for item in hidden.json["items"]] == [model["id"]], hidden.json
    assert incompatible["id"] not in [item["id"] for item in hidden.json["items"]], hidden.json
    journey.assertion(
        "an incompatible stub model completes the job but records a failed report and a failed "
        "model; the failed model is invisible to non-admin listModels (only the passed one stays)"
    )

    reports_before = _count(stack, "evaluation_reports")
    unconfigured = t039.register_or_fail(admin, provider="openai", model_id="gpt-real-1")
    accepted = t039.validate(admin, unconfigured["id"], key="t100-unconfigured-" + secrets.token_hex(6))
    assert accepted.status == 202, accepted.text
    completed, reports, _log = t039.run_validation(stack)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    report = f00b.job_reports(reports)[0]
    assert report["result"] == "failed", reports
    assert report["error_code"] == "provider_not_configured", reports
    unconfigured_model = t039.get_model(admin, unconfigured["id"])
    assert unconfigured_model.json["validation_state"] == "failed", unconfigured_model.json
    assert unconfigured_model.json["evaluation_report_id"] is None, unconfigured_model.json
    unconfigured_eval = t039.evaluation_for_model(db, unconfigured["id"])
    assert unconfigured_eval["state"] == "failed", unconfigured_eval
    assert unconfigured_eval["error_code"] == "provider_not_configured", unconfigured_eval
    assert _count(stack, "evaluation_reports") == reports_before, "a failed job wrote a report"
    journey.assertion(
        "a model naming an undeployed real provider fails validation with the located terminal "
        "code provider_not_configured, the evaluation row fails, and no report row is written"
    )

    journey.variant(
        "real-vendor-model",
        "blocked",
        "this environment has ORCHARA_PROFILE=dev-stub only: no vendor endpoint, credential or "
        "GPU exists, so no real model's compatibility/quality was exercised or claimed",
    )
    journey.note(
        "stub/real split: the registry, validation job, maintenance handler, sealed report and "
        "contract reads are real product code; the compatibility verdict is produced by the "
        "dev-stub adapter (document.provider=stub, document.stub=true) and is reported as stub"
    )
