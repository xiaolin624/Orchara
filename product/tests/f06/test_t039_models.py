"""F06/T039 acceptance: model registration, compatibility validation and ACLs.

Every test drives the real HTTP surface against real PostgreSQL, real sealed
objects and the real maintenance binary. The compatibility job is the existing
T024 ``model_validate`` handler; T039 adds the server-side registration entry
point, the fixture it seals, and the completion binding that turns the report
into a passed/failed model version.

``not_run``: the real-vendor adapter path (F06 has no vendor protocol adapter in
this build; a real provider fails closed as ``provider_not_configured`` and is
asserted here as the terminal infrastructure-failure path), and a browser DOM
driving the page (the page behaviour is executed under Node instead).
"""

from __future__ import annotations

import json
import secrets

from support import f00b, f02a, t024, t025
from support import stack as st
from support import t039


def creator(stack) -> f00b.Session:
    """A real non-admin session; administrators are not creators by default."""
    admin = f00b.admin_session(stack)
    login = "t039-creator-" + secrets.token_hex(4)
    return f00b.invite_and_activate(stack, admin, login, roles=("creator",))


def assert_evaluation_shape(body: dict) -> None:
    assert set(body) == t039.EVALUATION_KEYS, body
    assert body["kind"] == "model", body
    assert body["report_id"] is None and body["error_code"] is None, body


def test_registration_is_pending_and_never_echoes_the_secret(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    body = t039.registration_body()
    response = admin.client.post("/api/v1/models", body=body, csrf=admin.csrf)
    assert response.status == 201, response.text
    accepted = response.json
    assert set(accepted) == {"id", "state"}, accepted
    assert accepted["state"] == "pending", accepted
    assert "secret" not in response.text.lower()
    assert t039.DEFAULT_CREDENTIAL not in response.text

    model = t039.get_model(admin, accepted["id"])
    assert model.status == 200, model.text
    assert set(model.json) == t039.PROFILE_KEYS, model.json
    assert model.json["validation_state"] == "pending"
    assert model.json["data_destination"] == t039.DEFAULT_DESTINATION
    assert model.json["evaluation_report_id"] is None
    assert t039.DEFAULT_CREDENTIAL not in model.text

    row = t039.model_row(f06_stack.dbname, accepted["id"])
    assert row["provider"] == "stub"
    assert row["secret_ref"] == t039.DEFAULT_CREDENTIAL
    assert row["endpoint_ref"] == t039.DEFAULT_ENDPOINT
    assert len(row["manifest_sha256"]) == 64
    manifest = t039.artifact_row(f06_stack.dbname, row["manifest_artifact_id"])
    assert manifest["purpose"] == "model_manifest", manifest
    assert manifest["state"] == "ready", manifest
    assert manifest["sha256"] == row["manifest_sha256"], manifest
    sealed = t039.sealed_content(f06_stack.object_dir, manifest)
    assert t024.sha256_hex(sealed) == row["manifest_sha256"]
    document = json.loads(sealed)
    assert document["format_version"] == 1
    assert document["name"] == body["name"]
    assert document["config"] == body["config"]
    for forbidden in ("created_at", "timestamp", "run_id"):
        assert forbidden not in document, document


def test_duplicate_registration_conflicts_without_a_second_row(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    body = t039.registration_body()
    first = admin.client.post("/api/v1/models", body=body, csrf=admin.csrf)
    assert first.status == 201, first.text
    second = admin.client.post("/api/v1/models", body=body, csrf=admin.csrf)
    assert second.status == 409, second.text
    assert second.json["error"]["code"] == "model_already_registered", second.json
    assert st.sql(
        f06_stack.dbname, "SELECT count(*) FROM platform.model_profile_versions"
    ) == "1"


def test_non_admin_cannot_register_or_validate(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    model = t039.register_or_fail(admin)
    other = creator(f06_stack)

    register = t039.register(other)
    assert register.status == 403, register.text
    assert register.json["error"]["code"] == "forbidden", register.json

    validate = t039.validate(other, model["id"], key="t039-non-admin-" + secrets.token_hex(4))
    assert validate.status == 403, validate.text
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.evaluation_jobs") == "0"


def test_registration_rejects_unregistered_references(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    for field, overrides in (
        ("endpoint_ref", {"endpoint_ref": "https://evil.example/v1"}),
        ("secret_ref", {"secret_ref": "not-a-registered-credential"}),
        ("data_destination", {"data_destination": "eu-cloud"}),
        ("config.timeout_ms", {"config": t039.config(timeout_ms=0)}),
    ):
        response = t039.register(admin, **overrides)
        assert response.status == 422, (field, response.text)
        assert response.json["error"]["code"] == "validation_failed", response.json
        assert response.json["error"]["details"]["field"] == field, response.json


def test_catalog_reads_are_admin_only_and_return_contract_items(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    endpoints = admin.client.get("/api/v1/admin/model-endpoints")
    assert endpoints.status == 200, endpoints.text
    assert set(endpoints.json) == {"items"}, endpoints.json
    assert endpoints.json["items"] == [
        {"id": t039.DEFAULT_ENDPOINT, "data_destination": t039.DEFAULT_DESTINATION}
    ], endpoints.json
    assert "stub-credential" not in endpoints.text

    credentials = admin.client.get("/api/v1/admin/credential-refs")
    assert credentials.status == 200, credentials.text
    assert credentials.json["items"] == [
        {"id": t039.DEFAULT_CREDENTIAL, "description": "dev stub (no real secret)"}
    ], credentials.json
    assert "env" not in json.dumps(credentials.json).lower()

    other = creator(f06_stack)
    assert other.client.get("/api/v1/admin/model-endpoints").status == 403
    assert other.client.get("/api/v1/admin/credential-refs").status == 403


def test_validate_requires_one_idempotency_key(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    model = t039.register_or_fail(admin)
    missing = t039.validate(admin, model["id"])
    assert missing.status == 400, missing.text
    assert missing.json["error"]["code"] == "idempotency_key_required", missing.json
    assert missing.json["error"]["details"] == {"header": "Idempotency-Key"}
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.background_jobs") == "0"


def test_validate_happy_path_passes_the_model_and_records_the_report(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    model = t039.register_or_fail(admin)
    key = "t039-validate-" + secrets.token_hex(8)

    accepted = t039.validate(admin, model["id"], key=key)
    assert accepted.status == 202, accepted.text
    evaluation = accepted.json
    assert_evaluation_shape(evaluation)
    assert evaluation["state"] == "queued"

    # The same key and the same request replay the recorded 202 and create no
    # second evaluation or job.
    replay = t039.validate(admin, model["id"], key=key)
    assert replay.status == 202, replay.text
    assert replay.json == evaluation, replay.json
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.evaluation_jobs") == "1"
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.background_jobs") == "1"

    completed, reports, finalizer_log = t039.run_validation(f06_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["kind"] == t024.KIND_MODEL_VALIDATE, job_reports
    assert job_reports[0]["result"] == "succeeded", job_reports

    passed = t039.get_model(admin, model["id"])
    assert passed.status == 200, passed.text
    assert set(passed.json) == t039.PROFILE_KEYS, passed.json
    assert passed.json["validation_state"] == "passed", passed.json
    report_id = passed.json["evaluation_report_id"]
    assert report_id, passed.json

    row = t039.evaluation_for_model(f06_stack.dbname, model["id"])
    assert row["state"] == "succeeded", row
    assert row["report_id"] == report_id, row
    assert len(row["background_job_id"]) == 36, row

    report = t039.report_row(f06_stack.dbname, report_id)
    assert report["status"] == "passed", report
    assert report["manifest_sha256"] == t039.model_row(f06_stack.dbname, model["id"])["manifest_sha256"]
    assert report["dataset_sha256"] == t024.sha256_hex(
        t039.sealed_content(
            f06_stack.object_dir, t039.artifact_row(f06_stack.dbname, row["dataset_artifact_id"])
        )
    )

    document = t039.report_document(f06_stack, report_id)
    assert document["kind"] == "model_compatibility", document
    assert document["model_profile_version_id"] == model["id"]
    assert document["compatible"] is True and document["check"]["passed"] is True
    assert document["check"]["name"] == "structured_output"
    assert document["provider"] == "stub" and document["stub"] is True
    assert document["fixture_artifact_id"] == row["dataset_artifact_id"]
    assert document["fixture_sha256"] == report["dataset_sha256"]

    # The report read is the contract projection, not the sealed document.
    read = admin.client.get("/api/v1/evaluation-reports/" + report_id)
    assert read.status == 200, read.text
    assert set(read.json) == {"id", "status", "manifest_sha256", "result_artifact_id"}, read.json
    assert read.json["status"] == "passed"

    # Visibility follows validation: the creator sees a passed model.
    other = creator(f06_stack)
    visible = t039.list_models(other)
    assert visible.status == 200, visible.text
    assert [item["id"] for item in visible.json["items"]] == [model["id"]], visible.json
    assert visible.json["next_cursor"] is None


def test_retryable_failure_keeps_model_running_and_the_retry_passes(f06_stack) -> None:
    """AC5: a retried attempt must not fail the model or the evaluation.

    The first attempt is claimed by hand and fails after the real model call as a
    transient storage error — exactly the T024 infrastructure-failure shape. The
    guard must treat it as retryable: the job returns to ready, the model and the
    evaluation stay running, and no report exists. The real maintenance process
    then runs the retry in a new process; the model ends passed, the evaluation
    succeeded, and exactly one immutable report exists.
    """
    admin = f00b.admin_session(f06_stack)
    model = t039.register_or_fail(admin)
    accepted = t039.validate(admin, model["id"], key="t039-retry-" + secrets.token_hex(8))
    assert accepted.status == 202, accepted.text
    job_id = accepted.json["background_job_id"]

    lease = f06_stack.internal_client().claim(kinds=[t024.KIND_MODEL_VALIDATE])
    assert lease.status == 200, lease.text
    assert lease.json["job_id"] == job_id, lease.json

    failed = f06_stack.internal_client().result(
        job_id,
        attempt_token=lease.json["attempt_token"],
        state="failed",
        error_code="transient_storage_io",
    )
    assert failed.status == 200, failed.text
    assert failed.json["state"] == "ready", failed.json

    running = t039.get_model(admin, model["id"])
    assert running.status == 200, running.text
    assert running.json["validation_state"] == "running", running.json
    assert running.json["evaluation_report_id"] is None, running.json
    row = t039.evaluation_for_model(f06_stack.dbname, model["id"])
    assert row["state"] == "running", row
    assert row["report_id"] is None, row
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.evaluation_reports") == "0"
    assert st.sql(
        f06_stack.dbname,
        "SELECT attempt_count FROM platform.background_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    ) == "1"

    f00b.wait_until(
        lambda: st.sql(
            f06_stack.dbname,
            "SELECT available_at <= now() FROM platform.background_jobs "
            f"WHERE id = {st.quote_literal(job_id)}::uuid",
        )
        == "t",
        timeout=5.0,
        interval=0.01,
        what="the retry to become claimable",
    )

    completed, reports, finalizer_log = t039.run_validation(f06_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, reports
    assert job_reports[0]["result"] == "succeeded", job_reports

    passed = t039.get_model(admin, model["id"])
    assert passed.status == 200, passed.text
    assert passed.json["validation_state"] == "passed", passed.json
    report_id = passed.json["evaluation_report_id"]
    assert report_id, passed.json

    row = t039.evaluation_for_model(f06_stack.dbname, model["id"])
    assert row["state"] == "succeeded", row
    assert row["report_id"] == report_id, row
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.evaluation_reports") == "1"
    assert st.sql(
        f06_stack.dbname,
        "SELECT attempt_count FROM platform.background_jobs "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    ) == "2"
    document = t039.report_document(f06_stack, report_id)
    assert document["compatible"] is True, document


def test_failed_stub_case_is_a_failed_report_and_a_failed_model(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    model = t039.register_or_fail(admin, model_id="stub-model-incompatible")
    accepted = t039.validate(admin, model["id"], key="t039-failed-" + secrets.token_hex(8))
    assert accepted.status == 202, accepted.text

    completed, reports, finalizer_log = t039.run_validation(f06_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    assert f00b.job_reports(reports)[0]["result"] == "succeeded", reports

    failed = t039.get_model(admin, model["id"])
    assert failed.status == 200, failed.text
    assert failed.json["validation_state"] == "failed", failed.json
    report_id = failed.json["evaluation_report_id"]
    assert report_id, failed.json

    row = t039.evaluation_for_model(f06_stack.dbname, model["id"])
    assert row["state"] == "succeeded", row
    report = t039.report_row(f06_stack.dbname, report_id)
    assert report["status"] == "failed", report
    document = t039.report_document(f06_stack, report_id)
    assert document["compatible"] is False, document
    assert document["check"]["passed"] is False, document
    assert document["check"]["reason"] == "structured_output", document

    # A failed model is invisible to every non-admin principal.
    other = creator(f06_stack)
    hidden = t039.list_models(other)
    assert hidden.status == 200 and hidden.json["items"] == [], hidden.json


def test_terminal_infrastructure_failure_fails_model_without_a_report(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    # A real provider with no deployed endpoint: the provider is unconfigured,
    # which is a deployment fact, not a retryable outage.
    model = t039.register_or_fail(admin, provider="openai", model_id="gpt-real-1")
    accepted = t039.validate(admin, model["id"], key="t039-terminal-" + secrets.token_hex(8))
    assert accepted.status == 202, accepted.text

    completed, reports, finalizer_log = t039.run_validation(f06_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    job_reports = f00b.job_reports(reports)
    assert len(job_reports) == 1, job_reports
    assert job_reports[0]["result"] == "failed", job_reports
    assert job_reports[0]["error_code"] == "provider_not_configured", job_reports

    failed = t039.get_model(admin, model["id"])
    assert failed.status == 200, failed.text
    assert failed.json["validation_state"] == "failed", failed.json
    assert failed.json["evaluation_report_id"] is None, failed.json

    row = t039.evaluation_for_model(f06_stack.dbname, model["id"])
    assert row["state"] == "failed", row
    assert row["report_id"] is None, row
    assert row["error_code"] == "provider_not_configured", row
    assert st.sql(f06_stack.dbname, "SELECT count(*) FROM platform.evaluation_reports") == "0"


def test_pending_model_blocks_draft_validation_until_it_passes(f06_stack) -> None:
    admin = f00b.admin_session(f06_stack)
    owner = creator(f06_stack)
    model = t039.register_or_fail(admin, name="dependency model " + secrets.token_hex(4))

    application = f02a.create_application_or_fail(owner, "T039 dependency case")
    revision = f02a.get_draft(owner, application["id"]).header("ETag")
    saved = f02a.save_draft(
        owner,
        application["id"],
        revision,
        definition=t039.model_definition(model["id"]),
        capability_card=f02a.full_card(),
    )
    assert saved.status == 200, saved.text

    refused = t025.validate(owner, application["id"])
    errors = t025.validation_errors(refused)
    t025.assert_location(
        errors[0],
        code="dependency_not_ready",
        pointer="/nodes/1/config/model_profile_version",
        node_id="analysis",
    )

    accepted = t039.validate(admin, model["id"], key="t039-dep-" + secrets.token_hex(8))
    assert accepted.status == 202, accepted.text
    completed, _reports, finalizer_log = t039.run_validation(f06_stack)
    assert completed.returncode == 0, f"{completed.stdout}\n{completed.stderr}\n{finalizer_log}"
    assert t039.get_model(admin, model["id"]).json["validation_state"] == "passed"

    allowed = t025.validate(owner, application["id"])
    assert allowed.status == 200, allowed.text
    assert allowed.json == {"valid": True, "errors": []}, allowed.json
