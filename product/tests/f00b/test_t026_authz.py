"""T026 acceptance: evaluation/report reads are strictly owner-or-admin.

The owner is a regular invited user (not the administrator), so the test can
distinguish "owner" from "admin" from "another user". Everyone else gets 404 —
the same answer as a missing row — and the projections carry only contract
fields: no dataset/candidate id, object key, report body or draft text.
"""

from __future__ import annotations

from support import f00b, t026
from support import f02a as f02a_support
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
    t026_stack,
)

EVALUATION_KEYS = {
    "id",
    "kind",
    "state",
    "background_job_id",
    "report_id",
    "error_code",
}
REPORT_KEYS = {"id", "status", "manifest_sha256", "result_artifact_id"}
BACKGROUND_JOB_KEYS = {"id", "kind", "state", "error_code"}


def test_owner_admin_and_everyone_else(t026_stack) -> None:
    admin = f00b.admin_session(t026_stack)
    owner = f00b.invite_and_activate(t026_stack, admin, "t026-owner")
    other = f00b.invite_and_activate(t026_stack, admin, "t026-other")

    prepared = t026.prepare_model_evaluation(
        t026_stack, owner_login="t026-owner", cases=[t026.dataset_case(case_id="c1", input_value={"q": 1})]
    )
    completed, reports = t026.run_pools_once(t026_stack, work_slots=1)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)

    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=10
    )
    report_id = evaluation["report_id"]
    assert report_id is not None

    owner_body = t026.get_evaluation(owner, prepared["evaluation_id"])
    assert owner_body.status == 200, owner_body.text
    assert set(owner_body.json) == EVALUATION_KEYS, owner_body.json
    assert owner_body.json["state"] == "succeeded"
    assert owner_body.json["report_id"] == report_id

    admin_body = t026.get_evaluation(admin, prepared["evaluation_id"])
    assert admin_body.status == 200, admin_body.text
    assert admin_body.json == owner_body.json

    other_body = t026.get_evaluation(other, prepared["evaluation_id"])
    assert other_body.status == 404, other_body.text
    assert other_body.json["error"]["code"] == "resource_not_found"

    owner_report = t026.get_report(owner, report_id)
    assert owner_report.status == 200, owner_report.text
    assert set(owner_report.json) == REPORT_KEYS, owner_report.json
    assert set(owner_report.json) == {"id", "status", "manifest_sha256", "result_artifact_id"}
    admin_report = t026.get_report(admin, report_id)
    assert admin_report.status == 200
    assert admin_report.json == owner_report.json
    other_report = t026.get_report(other, report_id)
    assert other_report.status == 404, other_report.text

    # The background-job diagnostic keeps its T022 rule as well.
    job_id = prepared["background_job_id"]
    owner_job = t026.get_background_job(owner, job_id)
    assert owner_job.status == 200, owner_job.text
    assert set(owner_job.json) == BACKGROUND_JOB_KEYS, owner_job.json
    assert t026.get_background_job(admin, job_id).status == 200
    other_job = t026.get_background_job(other, job_id)
    assert other_job.status == 404, other_job.text

    # A malformed or unknown id is the same 404, not a 500.
    assert t026.get_evaluation(owner, "not-a-uuid").status == 404
    assert t026.get_report(owner, t026.new_uuid()).status == 404

    # No body/artifact/draft leakage anywhere: the report projection has no
    # dataset hash or artifact path, and the evaluation has no dataset id.
    assert "dataset" not in owner_report.text.lower()
    assert "object_key" not in owner_report.text
    assert "dataset_artifact_id" not in owner_body.text
    assert "manifest" not in owner_body.text


def test_api_keys_need_the_read_scope_for_evaluations_reports_and_jobs(t026_stack) -> None:
    """The three public reads enforce a scope for bearer credentials.

    A key that authenticates but carries only application:write is refused before
    the owner/admin decision (403 insufficient_scope). A read-scoped key still
    only sees what it owns (404 otherwise), and once the evaluation/job really is
    owned by the service principal the same key gets the 200: the scope is
    necessary, never sufficient. Session callers are unaffected (the rest of this
    file).
    """
    admin = f00b.admin_session(t026_stack)
    service_id = f02a_support.create_service_account(
        admin, "t026-eval-reader", admin.principal_id
    )
    _write_id, write_secret = f02a_support.create_api_key(
        admin, service_id, ["application:write"]
    )
    write_key = f02a_support.bearer_client(t026_stack, write_secret)

    prepared = t026.prepare_model_evaluation(
        t026_stack, cases=[t026.dataset_case(case_id="c1", input_value={"q": 1})]
    )
    completed, _reports = t026.run_pools_once(t026_stack, work_slots=1)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    evaluation = t026.wait_for_evaluation(
        t026_stack.dbname, prepared["evaluation_id"], {"succeeded"}, timeout=15
    )
    report_id = evaluation["report_id"]
    assert report_id is not None
    job_id = prepared["background_job_id"]

    for path in (
        f"/api/v1/evaluations/{prepared['evaluation_id']}",
        f"/api/v1/evaluation-reports/{report_id}",
        f"/api/v1/background-jobs/{job_id}",
    ):
        st.assert_error(write_key.get(path), 403, "insufficient_scope")

    _read_id, read_secret = f02a_support.create_api_key(
        admin, service_id, ["application:read"]
    )
    read_key = f02a_support.bearer_client(t026_stack, read_secret)
    for path in (
        f"/api/v1/evaluations/{prepared['evaluation_id']}",
        f"/api/v1/evaluation-reports/{report_id}",
        f"/api/v1/background-jobs/{job_id}",
    ):
        st.assert_error(read_key.get(path), 404, "resource_not_found")

    # Make the service principal the real owner of these rows. The same scoped
    # key now reads them: the scope check passed before, and the ACL now allows.
    st.sql(
        t026_stack.dbname,
        "UPDATE platform.evaluation_jobs SET owner_id = "
        f"{st.quote_literal(service_id)}::uuid "
        f"WHERE id = {st.quote_literal(prepared['evaluation_id'])}::uuid",
    )
    st.sql(
        t026_stack.dbname,
        "UPDATE platform.background_jobs SET owner_id = "
        f"{st.quote_literal(service_id)}::uuid "
        f"WHERE id = {st.quote_literal(job_id)}::uuid",
    )
    owner_evaluation = read_key.get(f"/api/v1/evaluations/{prepared['evaluation_id']}")
    assert owner_evaluation.status == 200, owner_evaluation.text
    assert set(owner_evaluation.json) == EVALUATION_KEYS
    owner_report = read_key.get(f"/api/v1/evaluation-reports/{report_id}")
    assert owner_report.status == 200, owner_report.text
    assert set(owner_report.json) == REPORT_KEYS
    owner_job = read_key.get(f"/api/v1/background-jobs/{job_id}")
    assert owner_job.status == 200, owner_job.text
    assert set(owner_job.json) == BACKGROUND_JOB_KEYS


def test_unauthenticated_reads_are_401(t026_stack) -> None:
    prepared = t026.prepare_model_evaluation(
        t026_stack, cases=[t026.dataset_case(case_id="c1", input_value={"q": 1})]
    )
    client = t026_stack.client()
    assert client.get(f"/api/v1/evaluations/{prepared['evaluation_id']}").status == 401
    assert client.get(f"/api/v1/evaluation-reports/{t026.new_uuid()}").status == 401
