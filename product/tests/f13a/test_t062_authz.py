"""T062 acceptance: accounts, voided attempt tokens and the administrator read.

Three boundaries, all through real interfaces:

* A and B are different real accounts. B cannot read A's evaluation or report
  (404, the same answer as a missing id) and cannot submit an evaluation for an
  application B does not own; a report B creates can never be surfaced as A's.
* A stale/voided attempt token cannot submit a final success, including after a
  retry created a newer attempt (409 ``stale_attempt``, nothing changes).
* The administrator attribution read exposes the account, cancellation outcome
  and the snapshot/manifest/dataset/compiler/worker identity of a report without
  exposing sealed object keys or any user content. Non-admins never see it.
"""

from __future__ import annotations

from pathlib import Path

from support import t062
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

ATTRIBUTION_KEYS = {
    "evaluation_id",
    "kind",
    "state",
    "error_code",
    "background_job_id",
    "owner",
    "application_id",
    "snapshot_id",
    "draft_revision",
    "manifest_sha256",
    "dataset_artifact_id",
    "dataset_sha256",
    "compiler_version",
    "worker_image_digest",
    "state_schema_version",
    "cancel_requested_at",
    "report",
    "cases",
    "created_at",
    "finished_at",
}
OWNER_KEYS = {"principal_id", "login"}
REPORT_KEYS = {"id", "status", "manifest_sha256", "dataset_sha256", "result_artifact_id"}
CASE_KEYS = {
    "case_id",
    "ordinal",
    "case_state",
    "run_id",
    "run_state",
    "run_owner_id",
    "run_snapshot_id",
    "run_error_code",
    "terminal",
    "completed",
    "reason_code",
    "updated_at",
}


def _complete_with_failed_report(stack, evaluation_id: str) -> dict:
    """Accept a real (empty-case, not_run) runner report so a report row exists.

    No case ran, so the report is a legitimate ``not_run``: the guard accepts it
    when every binding field is this evaluation's own. It gives the account tests
    a real report row without starting the pools.
    """
    evaluation = t062.evaluation_row(stack.dbname, evaluation_id)
    client, lease = t062.claim_evaluation(stack)
    document = t062.crafted_document(
        stack,
        evaluation,
        status="not_run",
        cases=[],
        checks=["run_facts"],
    )
    artifact_id = t062.upload_ready_report(stack, client, lease, t062.report_bytes(document))
    response = t062.submit_result(client, lease, result_artifact_id=artifact_id)
    assert response.status == 200, response.text
    assert response.json["state"] == "succeeded", response.json
    completed = t062.evaluation_row(stack.dbname, evaluation_id)
    assert completed["report_id"] is not None, completed
    return completed


def test_accounts_are_isolated_on_reads_and_submission(t052_stack) -> None:
    owner_a = t062.minimal_owner(t052_stack, login="t062-account-a")
    owner_b = t062.minimal_owner(t052_stack, login="t062-account-b")
    admin = t062.admin_session(t052_stack)

    evaluation_a = t062.submit_evaluation_for(
        t052_stack,
        owner_a,
        [
            t062.evaluation_case(
                case_id="a-case", assertions=[t062.assertion_run_state("succeeded")]
            )
        ],
    )
    completed_a = _complete_with_failed_report(t052_stack, evaluation_a)
    report_a = t062.report_row(t052_stack.dbname, completed_a["report_id"])

    # A reads its own evaluation/report.
    own_evaluation = t062.get_evaluation(owner_a.creator, evaluation_a)
    assert own_evaluation.status == 200, own_evaluation.text
    own_report = t062.get_report(owner_a.creator, report_a["id"])
    assert own_report.status == 200, own_report.text

    # B gets exactly the missing-resource answer for both ids.
    for path in (
        f"/api/v1/evaluations/{evaluation_a}",
        f"/api/v1/evaluation-reports/{report_a['id']}",
    ):
        response = owner_b.creator.client.get(path)
        assert response.status == 404, (path, response.text)
        assert response.json["error"]["code"] == "resource_not_found", response.text
        missing = owner_b.creator.client.get(path.rsplit("/", 1)[0] + "/" + t062.new_uuid())
        assert missing.status == 404, missing.text
        # Same status, code and message for "exists but not yours" and "missing";
        # only the per-request id differs.
        assert missing.json["error"]["code"] == response.json["error"]["code"], missing.text
        assert missing.json["error"]["message"] == response.json["error"]["message"], missing.text

    # The administrator may read both (it is the same contract projection).
    assert t062.get_evaluation(admin, evaluation_a).status == 200
    assert t062.get_report(admin, report_a["id"]).status == 200

    # The per-case result bodies are owner-only. An administrator is a non-owner
    # here: the role alone does not grant result-body viewing (DATA-POLICY.md),
    # and the administrator attribution read was built without bodies. The answer
    # is identical to a missing report, so the route cannot enumerate report ids.
    cases_path = f"/api/v1/evaluation-reports/{report_a['id']}/cases"
    owner_cases = owner_a.creator.client.get(cases_path)
    assert owner_cases.status == 200, owner_cases.text
    assert owner_cases.json["cases"] == [], owner_cases.json
    admin_cases = admin.client.get(cases_path)
    missing_cases = admin.client.get(
        f"/api/v1/evaluation-reports/{t062.new_uuid()}/cases"
    )
    assert admin_cases.status == 404, admin_cases.text
    # Same status, error code and message as "missing"; only the per-request id
    # differs (that is true of the missing case itself, not of this route).
    assert admin_cases.json["error"]["code"] == missing_cases.json["error"]["code"]
    assert admin_cases.json["error"]["message"] == missing_cases.json["error"]["message"]

    # B cannot start an evaluation for A's application: the submission route
    # answers 404 before it can compile or freeze anything.
    b_dataset = t062.upload_dataset(
        t052_stack,
        owner_b.creator,
        t062.dataset_document(
            [
                t062.evaluation_case(
                    case_id="b-case", assertions=[t062.assertion_run_state("succeeded")]
                )
            ]
        ),
    )
    refused = t062.submit_evaluation(
        owner_b.creator,
        owner_a.application_id,
        draft_revision=t062.draft_revision(owner_b.creator, owner_b.application_id),
        dataset_artifact_id=b_dataset,
    )
    assert refused.status == 404, refused.text
    assert refused.json["error"]["code"] == "resource_not_found", refused.text

    # A cannot borrow B's dataset for A's own application.
    borrowed = t062.submit_evaluation(
        owner_a.creator,
        owner_a.application_id,
        draft_revision=t062.draft_revision(owner_a.creator, owner_a.application_id),
        dataset_artifact_id=b_dataset,
    )
    assert borrowed.status == 422, borrowed.text
    assert borrowed.json["error"]["code"] == "validation_failed", borrowed.text
    assert borrowed.json["error"]["details"]["field"] == "dataset_artifact_id", borrowed.text


def test_the_administrator_attribution_read_exposes_account_and_cancellation_evidence(
    t052_stack, tmp_path: Path
) -> None:
    owner = t062.minimal_owner(t052_stack, login="t062-attribution-owner")
    other = t062.minimal_owner(t052_stack, login="t062-attribution-other")
    admin = t062.admin_session(t052_stack)

    cancelled_case = t062.evaluation_case(
        case_id="cancelled-case",
        assertions=[t062.assertion_run_state("cancelled")],
    )
    cancelled_case["input"] = {
        "interactions": [{"kind": "cancel"}],
        "text": "t062-content-marker-do-not-leak",
    }
    incomplete_case = t062.evaluation_case(
        case_id="incomplete-case",
        assertions=[t062.assertion_run_state("succeeded")],
    )
    incomplete_case["input"] = {"text": "t062-content-marker-do-not-leak"}
    evaluation_id = t062.submit_evaluation_for(
        t052_stack, owner, [cancelled_case, incomplete_case]
    )

    log = tmp_path / "pools.log"
    process, handle = t062.start_pools(t052_stack, log_path=log, case_timeout="5s")
    try:
        evaluation = t062.wait_for_evaluation(
            t052_stack.dbname, evaluation_id, {"succeeded", "failed"}, timeout=120
        )
    finally:
        t062.stop_process(process, handle, log)
    assert evaluation["state"] == "succeeded", evaluation
    report = t062.report_row(t052_stack.dbname, evaluation["report_id"])
    assert report["status"] == "failed", report

    response = t062.get_attribution(admin, evaluation_id)
    assert response.status == 200, response.text
    body = response.json
    assert set(body) == ATTRIBUTION_KEYS, body

    snapshot = t062.snapshot_identity(t052_stack.dbname, evaluation["snapshot_id"])
    dataset = t062.artifact_row(t052_stack.dbname, evaluation["dataset_artifact_id"])
    assert body["evaluation_id"] == evaluation_id
    assert body["kind"] == "application"
    assert body["state"] == "succeeded"
    assert body["error_code"] is None
    assert body["background_job_id"] == evaluation["background_job_id"]

    # The account: owner principal + login, never another user's content.
    assert set(body["owner"]) == OWNER_KEYS, body["owner"]
    assert body["owner"]["principal_id"] == owner.creator.principal_id, body["owner"]
    assert body["owner"]["login"] == "t062-attribution-owner", body["owner"]
    assert body["application_id"] == owner.application_id

    # The attribution identity: snapshot + manifest + dataset + compiler/worker.
    assert body["snapshot_id"] == evaluation["snapshot_id"]
    assert body["draft_revision"] == snapshot["draft_revision"]
    assert body["manifest_sha256"] == snapshot["manifest_sha256"]
    assert body["dataset_artifact_id"] == evaluation["dataset_artifact_id"]
    assert body["dataset_sha256"] == dataset["sha256"]
    assert body["compiler_version"] == snapshot["compiler_version"]
    assert body["worker_image_digest"] == snapshot["worker_image_digest"]
    assert body["state_schema_version"] == snapshot["state_schema_version"]
    assert body["cancel_requested_at"] is None

    # The report binding, with the business verdict (never passed).
    assert set(body["report"]) == REPORT_KEYS, body["report"]
    assert body["report"]["id"] == evaluation["report_id"]
    assert body["report"]["status"] == "failed"
    assert body["report"]["manifest_sha256"] == snapshot["manifest_sha256"]
    assert body["report"]["dataset_sha256"] == dataset["sha256"]
    assert body["report"]["result_artifact_id"] == report["result_artifact_id"]

    # Per-case run states, including the cancelled one and the unfinished one.
    assert len(body["cases"]) == 2, body["cases"]
    cases = {item["case_id"]: item for item in body["cases"]}
    assert set(cases) == {"cancelled-case", "incomplete-case"}, cases
    for item in body["cases"]:
        assert set(item) == CASE_KEYS, item
        assert item["run_id"], item
        assert item["run_owner_id"] == owner.creator.principal_id, item
        assert item["run_snapshot_id"] == evaluation["snapshot_id"], item
    cancelled = cases["cancelled-case"]
    assert cancelled["run_state"] == "cancelled", cancelled
    assert cancelled["terminal"] is True, cancelled
    # Cancelled is terminal but not *completed*: it may never back a pass.
    assert cancelled["completed"] is False, cancelled
    incomplete = cases["incomplete-case"]
    assert incomplete["run_state"] == "queued", incomplete
    assert incomplete["terminal"] is False, incomplete
    assert incomplete["completed"] is False, incomplete
    assert [item["ordinal"] for item in body["cases"]] == sorted(
        item["ordinal"] for item in body["cases"]
    )

    # Nothing sealed or user-authored leaks: no object keys, no case input text,
    # no report body and no dataset bytes.
    assert "object_key" not in response.text
    assert "sealed/" not in response.text
    assert "t062-content-marker-do-not-leak" not in response.text
    assert "run_facts" not in response.text, "the sealed report body must not leak"
    assert "assertions" not in response.text

    # Only an administrator may read it, and the refusal never reveals existence.
    owner_refused = t062.get_attribution(owner.creator, evaluation_id)
    assert owner_refused.status == 403, owner_refused.text
    assert owner_refused.json["error"]["code"] == "forbidden", owner_refused.text
    other_refused = t062.get_attribution(other.creator, evaluation_id)
    assert other_refused.status == 403, other_refused.text
    unauthenticated = t052_stack.client().get(t062.attribution_path(evaluation_id))
    assert unauthenticated.status == 401, unauthenticated.text

    # An unknown or malformed id is the same 404, never a 500 and never a hint.
    missing = t062.get_attribution(admin, t062.new_uuid())
    assert missing.status == 404, missing.text
    assert missing.json["error"]["code"] == "resource_not_found", missing.text
    malformed = t062.get_attribution(admin, "not-a-uuid")
    assert malformed.status == 404, malformed.text
    assert malformed.json["error"]["code"] == "resource_not_found", malformed.text


def test_a_voided_attempt_token_cannot_submit_a_final_success(t052_stack) -> None:
    owner = t062.minimal_owner(t052_stack, login="t062-stale-owner")
    evaluation_id = t062.submit_evaluation_for(
        t052_stack,
        owner,
        [
            t062.evaluation_case(
                case_id="stale-case", assertions=[t062.assertion_run_state("succeeded")]
            )
        ],
    )
    evaluation = t062.evaluation_row(t052_stack.dbname, evaluation_id)
    client, first = t062.claim_evaluation(t052_stack)

    # A retryable infrastructure failure returns the job to ready; the evaluation
    # stays running because an attempt is still owed.
    retryable = t062.submit_result(
        client, first, state="failed", error_code="transient_storage_io"
    )
    assert retryable.status == 200, retryable.text
    assert retryable.json["state"] == "ready", retryable.json
    t062.wait_for_retry_available(t052_stack.dbname, first["job_id"], timeout=10)
    assert t062.evaluation_row(t052_stack.dbname, evaluation_id)["state"] == "running"

    # A newer attempt really exists and owns the job.
    client, second = t062.claim_evaluation(t052_stack)
    assert second["job_id"] == first["job_id"], (second, first)
    assert second["attempt_token"] != first["attempt_token"], (second, first)

    # The voided token now tries to submit a *forged passed* result.
    forged = t062.crafted_document(
        t052_stack,
        evaluation,
        status="passed",
        cases=[t062.passed_case("stale-case", run_id=None, run_state="succeeded")],
    )
    artifact_id = t062.upload_ready_report(t052_stack, client, second, t062.report_bytes(forged))
    before_reports = t062.report_count(t052_stack.dbname)
    response = t062.submit_result(client, first, result_artifact_id=artifact_id)
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == t062.STALE_ATTEMPT, response.text

    # Nothing changed: the job is still the second attempt's lease, the evaluation
    # is still running and no report row exists.
    job = st.sql_rows(
        t052_stack.dbname,
        "SELECT state, lease_token::text AS lease_token, attempt_count "
        f"FROM platform.background_jobs WHERE id = {st.quote_literal(first['job_id'])}::uuid",
    )[0]
    assert job["state"] == "leased", job
    assert job["lease_token"] == second["attempt_token"], job
    assert int(job["attempt_count"]) == 2, job
    assert t062.evaluation_row(t052_stack.dbname, evaluation_id)["state"] == "running"
    assert t062.report_count(t052_stack.dbname) == before_reports

    # The current attempt can still be completed (it does not inherit the voided
    # token's claim): the guard refuses the crafted pass for lack of a run.
    refused = t062.submit_result(client, second, result_artifact_id=artifact_id)
    assert refused.status == 200, refused.text
    assert refused.json["state"] == "failed", refused.json
    assert refused.json["error_code"] == t062.REPORT_BINDING_MISMATCH, refused.json
