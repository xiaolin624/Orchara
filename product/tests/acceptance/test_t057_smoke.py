"""T057 slice 2: the sales / failure / cancel / model-profile smoke, executed for real.

The smoke itself goes through the public HTTP API of the deployment started by
the bring-up entry. These tests read the recorded evidence *and* the real
PostgreSQL rows behind it (job state, attempt count, failure class, run/job
state, sealed report binding), so a product that only returned a plausible HTTP
body would fail here.

Boundaries asserted instead of mocked:

* a rejected sales input is a *business* failure and is never retried
  (``attempt_count == 1``, ``failure_class == 'business'``);
* a cancelled queued debug Run commits ``cancelled``, records the intent, has no
  attempt, and leaves no claimable job (the smoke claimed through real mTLS and
  got 204);
* the stub model profile's sealed compatibility report is bound to the model and
  evaluation rows, and says ``provider=stub, stub=true``;
* the page assets are executed under Node against the real smoke data.
"""

from __future__ import annotations

import json

from support import f00b, t027, t036, t039
from support import stack as st


def step(evidence: dict, name: str) -> dict:
    matches = [record for record in evidence["steps"] if record["name"] == name]
    assert len(matches) == 1, f"expected exactly one {name!r} step, got {len(matches)}"
    assert matches[0]["status"] == "passed", matches[0]
    return matches[0]


def test_the_environment_and_profile_are_recorded(smoke_evidence: dict) -> None:
    assert smoke_evidence["schema"] == "orchara.f15a.dev-smoke/1"
    assert smoke_evidence["status"] == "passed"
    assert smoke_evidence["profile"]["name"] == "dev-stub"
    assert smoke_evidence["profile"]["explicit"] is True
    assert smoke_evidence["profile"]["stub_results"] is True
    assert smoke_evidence["versions"]["go"].startswith("go version"), smoke_evidence["versions"]
    assert smoke_evidence["versions"]["node"].startswith("v"), smoke_evidence["versions"]
    assert smoke_evidence["versions"]["python"], smoke_evidence["versions"]
    assert smoke_evidence["versions"]["postgres"] not in ("", "unavailable"), smoke_evidence["versions"]

    environment = smoke_evidence["environment"]
    assert environment["profile"] == "dev-stub"
    assert environment["database"]["name"], environment
    assert environment["worker_image_digest"]["image_built"] is False, environment
    assert environment["worker_image_digest"]["kind"] == "deployer-placeholder", environment
    assert len(environment["runtime_lock"]["sha256"]) == 64, environment
    sequence = [command["step"] for command in environment["commands"]]
    for required in ("build", "migrate", "bootstrap", "api", "maintenance"):
        assert required in sequence, (required, sequence)

    blocked = {item["id"] for item in smoke_evidence["blocked"]}
    assert {"container_runtime", "linux_isolation", "model_credentials", "s3_object_store"} <= blocked
    not_run = {item["id"] for item in smoke_evidence["not_run"]}
    assert {"supervisor_sandbox_exec", "cmd_worker", "browser_dom"} <= not_run


def test_the_linear_sales_run_matches_the_independent_decimal_truth(
    dev_stack, smoke_evidence: dict
) -> None:
    sales = step(smoke_evidence, "sales")
    assert sales["state"] == "succeeded", sales
    assert sales["summary"] == {
        "total": "250.00",
        "regions": {"东区": "200.00", "西区": "50.00"},
        "order_count": 3,
    }, sales
    assert sales["summary_csv"] == {"东区": "200.00", "西区": "50.00"}, sales
    assert sales["independent_truth"] == sales["summary"], "the summary was not the independently computed truth"

    job = f00b.job_row(dev_stack.dbname, sales["tool_run_id"])
    assert job["kind"] == "tool_run", job
    assert job["state"] == "succeeded", job
    assert job["attempt_count"] == 1, job
    assert job["error_code"] is None, job

    recorded = smoke_evidence["artifacts"]["sales"]
    summary_artifact = f00b.artifact_row(dev_stack.dbname, recorded["summary_artifact_id"])
    assert summary_artifact["purpose"] == "tool_summary", summary_artifact
    assert summary_artifact["state"] == "ready", summary_artifact
    assert summary_artifact["sha256"] == recorded["summary_sha256"], summary_artifact
    csv_artifact = f00b.artifact_row(dev_stack.dbname, recorded["csv_artifact_id"])
    assert csv_artifact["purpose"] == "tool_artifact", csv_artifact
    assert csv_artifact["sha256"] == recorded["csv_sha256"], csv_artifact

    # The sales artifacts belong to the job (the page download path is the content
    # route, not an object key) and the uploaded input hash is the real bytes hash.
    links = t030_artifact_links(dev_stack.dbname, sales["tool_run_id"])
    assert {link["purpose"] for link in links} >= {"tool_summary", "tool_artifact"}, links
    input_artifact = st.sql_rows(
        dev_stack.dbname,
        "SELECT sha256, purpose, state FROM platform.artifacts WHERE sha256 = "
        f"{st.quote_literal(recorded['input_sha256'])}",
    )
    assert input_artifact and input_artifact[0]["purpose"] == "task_input", input_artifact
    assert input_artifact[0]["state"] == "ready", input_artifact


def test_the_failure_examples_are_explainable_business_failures(
    dev_stack, smoke_evidence: dict
) -> None:
    failure = step(smoke_evidence, "failure")
    cases = {case["case"]: case for case in failure["cases"]}
    assert set(cases) == {"missing_column", "duplicate_order_id"}, cases
    for name, case in cases.items():
        assert case["state"] == "failed", case
        assert case["error_code"] == name, case
        job = f00b.job_row(dev_stack.dbname, case["tool_run_id"])
        assert job["kind"] == "tool_run", job
        assert job["state"] == "failed", job
        assert job["error_code"] == name, job
        assert job["failure_class"] == "business", job
        assert job["attempt_count"] == 1, "a business rejection was retried"
        assert job["result_artifact_id"] is None, job

    recorded = smoke_evidence["artifacts"]["failure"]
    assert [case["case"] for case in recorded] == ["missing_column", "duplicate_order_id"], recorded
    for case in recorded:
        diagnostics = case["diagnostics"]
        assert diagnostics["status"] == "rejected", diagnostics
        assert diagnostics["retryable"] is False, diagnostics
        assert diagnostics["error_code"] == case["error_code"], diagnostics
        assert case["diagnostics_sha256"], case
        assert "sealed/" not in json.dumps(diagnostics) and "staging/" not in json.dumps(diagnostics)


def test_the_cancel_example_is_owner_only_and_leaves_no_claimable_job(
    dev_stack, smoke_evidence: dict
) -> None:
    cancel = step(smoke_evidence, "cancel")
    assert cancel["state"] == "cancelled", cancel
    assert cancel["phase"] == "idle", cancel
    assert cancel["cancel_requested_at"], cancel
    assert cancel["anonymous_read"] == "401 unauthenticated", cancel
    assert cancel["foreign_cancel"] == "404 resource_not_found", cancel
    assert cancel["claim_after_cancel"]["status"] == 204, cancel
    assert dev_stack.internal_url.startswith("https://"), dev_stack.internal_url

    run_id = cancel["run_id"]
    run = t036.run_cancel_row(dev_stack.dbname, run_id)
    assert run["state"] == "cancelled", run
    assert run["phase"] == "idle", run
    assert run["cancel_requested_at"] is not None, run
    assert run["finished_at"] is not None, run
    assert run["output_artifact_id"] is None, run
    assert run["error_code"] is None, run
    assert t036.attempts_for_run(dev_stack.dbname, run_id) == [], "a queued cancel must not create an attempt"
    jobs = t036.jobs_for_run(dev_stack.dbname, run_id)
    assert len(jobs) == 1, jobs
    assert jobs[0]["state"] == "void", "the cancelled Run's job must be voided, not requeued"
    assert jobs[0]["current_attempt_token"] is None, jobs


def test_the_stub_model_profile_runs_and_the_report_is_stub_marked(
    dev_stack, smoke_evidence: dict
) -> None:
    model_step = step(smoke_evidence, "model_profile")
    assert model_step["state"] == "succeeded", model_step
    assert model_step["report_status"] == "passed", model_step
    assert model_step["provider"] == "stub", model_step
    assert model_step["stub"] is True, model_step
    assert model_step["non_admin_registration"] == "403 forbidden", model_step

    recorded = smoke_evidence["artifacts"]["model_profile"]
    model = t039.model_row(dev_stack.dbname, recorded["model_profile_version_id"])
    assert model["provider"] == "stub", model
    assert model["validation_state"] == "passed", model
    assert model["evaluation_report_id"] == recorded["report_id"], model

    evaluation = st.sql_rows(
        dev_stack.dbname,
        "SELECT id::text AS id, kind, state, report_id::text AS report_id FROM platform.evaluation_jobs "
        f"WHERE id = {st.quote_literal(recorded['evaluation_job_id'])}::uuid",
    )
    assert len(evaluation) == 1, evaluation
    assert evaluation[0]["kind"] == "model", evaluation
    assert evaluation[0]["state"] == "succeeded", evaluation
    assert evaluation[0]["report_id"] == recorded["report_id"], evaluation

    report = t039.report_row(dev_stack.dbname, recorded["report_id"])
    assert report["status"] == "passed", report
    assert report["result_artifact_id"] == recorded["report_artifact_id"], report
    artifact = f00b.artifact_row(dev_stack.dbname, recorded["report_artifact_id"])
    assert artifact["purpose"] == "report", artifact
    assert artifact["state"] == "ready", artifact
    assert artifact["sha256"] == recorded["sealed_report_sha256"], artifact


def test_the_real_page_assets_execute_under_node(smoke_evidence: dict) -> None:
    page = step(smoke_evidence, "page_logic")
    assert "f15a page logic ok" in page["stdout"], page
    assert page["assets"] == [
        "assets/app.js",
        "assets/runstream.js",
        "assets/runstate-guard.js",
        "assets/attachments.js",
        "assets/runs.js",
        "assets/models.js",
    ], page


def test_the_evidence_contains_no_credential(smoke_evidence: dict, dev_stack) -> None:
    serialized = json.dumps(smoke_evidence, ensure_ascii=False)
    assert dev_stack.admin_password not in serialized
    assert "postgresql://" not in serialized
    assert "PGPASSWORD" not in serialized
    # The admin password is supplied out of band; the evidence records only the login.
    assert smoke_evidence["environment"]["admin_login"] == dev_stack.admin_login


def t030_artifact_links(dbname: str, job_id: str) -> list[dict]:
    return st.sql_rows(
        dbname,
        "SELECT l.purpose, a.id::text AS artifact_id, a.state, a.sha256 "
        "FROM platform.background_job_artifacts AS l "
        "JOIN platform.artifacts AS a ON a.id = l.artifact_id "
        f"WHERE l.background_job_id = {st.quote_literal(job_id)}::uuid",
    )
