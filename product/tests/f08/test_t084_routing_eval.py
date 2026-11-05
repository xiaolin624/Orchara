"""T084 (#85, F08): replay the frozen routing corpus against a real published catalog.

One evaluator run, everything real:

1. **Publication (AC1)** — a creator publishes the sales application through the
   real T068 path: the real five-category runner produces the evaluation report in
   the WSL2/runsc sandbox, the creator submits the publication, an administrator
   approves it, and the immutable version becomes active. If the host has no
   WSL2/runsc runtime at all, the test records a documented ``stub_publish``
   fallback and marks AC1 partial instead of pretending the real path ran.
2. **Second-user journey (AC1)** — another principal receives a real read grant,
   routes the published sales app from the REST entry, the dispatched Run is
   executed by the real supervisor, and ``summary.csv`` is downloaded and checked
   against the independent Decimal truth (``tests/support/t049.py``). The
   publisher cannot read the second user's Run or artifact (same 404).
3. **Corpus (AC3)** — every frozen/dev case is submitted by the second user and
   routed by the real ``route_request`` maintenance handler. False auto
   execution, clarification completion and candidate recall are computed from
   those runs into ``reports/f04b/T084-routing-eval.json`` (the path is
   ``T084_EVAL_REPORT`` during the evidence run; pytest otherwise writes its own
   tmp file).

The control model is the dev stub (``stub: true``); there is no vendor
credential on this host, so no real-model baseline is claimed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from support import f00b, f08, t024, t025, t026, t033, t042, t049, t068, t079, t082
from support import stack as st
from support.certs import make_stack_certificates, unique_internal_addr
from f08.routing import corpus, runner


@pytest.fixture
def t084_stack(tmp_path: Path, fresh_database, go_binaries, api_server) -> t026.T026Stack:
    """The real stack for the long evaluator run.

    It is the F02B/T068 shape (objects, signed links, mTLS, compiler identities)
    with one deployment knob: a 300s execution lease, the same margin T068 uses so
    a sandbox start under shared-host load cannot expire the lease mid-heartbeat.
    No product rule changes; the sandbox timeout still bounds the work.
    """
    dsn, dbname = fresh_database
    st.migrate_or_fail(go_binaries["admin"], dsn)
    st.bootstrap_or_fail(go_binaries["admin"], dsn, f00b.ADMIN_PASSWORD)
    object_dir = tmp_path / "objects"
    object_dir.mkdir()
    internal_addr, _port = unique_internal_addr()
    certificates = make_stack_certificates(tmp_path / "certs", internal_addr)
    signing_key = t024.new_signing_key()
    compiler_inputs = t025.api_extra_env(None)
    compiler_inputs["ORCHARA_EXECUTION_LEASE_TTL"] = "300s"
    server = api_server(
        dsn,
        extra_env=t024.api_extra_env(
            object_dir, certificates, signing_key=signing_key, extra=compiler_inputs
        ),
    )
    stack = t026.T026Stack(
        server=server,
        dsn=dsn,
        dbname=dbname,
        object_dir=object_dir,
        certificates=certificates,
        binaries=go_binaries,
        signing_key=signing_key,
    )
    f00b.wait_for_internal(stack)
    return stack


def _git_head() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(st.PRODUCT_ROOT.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def _publish_sales_application(stack, tmp_path: Path) -> dict:
    """The real T068 submit -> approve publication, with an honest fallback record."""
    publication: dict = {"mode": "t068_real", "fallback_reason": None}
    try:
        report = t068.genuine_sales_report(
            stack, login="t084-publisher", log_path=tmp_path / "t084-publication-pools.log"
        )
    except t068.SandboxEnvironmentUnavailable as unavailable:
        # Only "no WSL2/runsc at all" takes this branch, and the report says so.
        # The version is still a real compiler manifest + real tool row; only the
        # five-category evidence and the approval endpoint are skipped.
        publication["mode"] = "stub_publish"
        publication["fallback_reason"] = str(unavailable)
        publisher = f08.account(stack, login="t084-stub-publisher")
        t025.seed_tool_version(
            stack.dbname, publisher.principal_id, tool_id=t042.PRESET_TOOL_ID
        )
        published = f08.publish_version(
            stack,
            publisher.session,
            name="T084 sales application (stub publish)",
            definition=t042.linear_definition(input_schema=t068.TOOL_INPUT_SCHEMA),
        )
        sales = runner.PublishedApp(
            key="sales",
            application_id=published.application_id,
            version_id=published.version_id,
            version_no=published.version_no,
            card=dict(published.manifest["capability_card"]),
            publication="stub_publish",
        )
        publication.update(
            {
                "application_id": sales.application_id,
                "version_id": sales.version_id,
                "manifest_sha256": published.manifest_sha256,
                "review_id": None,
                "evaluation_report_id": None,
            }
        )
        return {"sales": sales, "session": publisher.session, "publication": publication}

    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]
    preview = t068.preview_publication(session, application_id)
    assert preview.status == 200, preview.text
    assert preview.json["manifest_sha256"] == manifest_sha, preview.json

    submitted = t068.submit_publication(
        session,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["report"]["id"],
    )
    assert submitted.status == 202, submitted.text
    review_id = submitted.json["id"]
    review = t068.review_row(stack.dbname, review_id)
    assert review["state"] == "submitted", review
    assert review["report_id"] == report["report"]["id"], review

    admin = f00b.admin_session(stack)
    approved = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha,
        reason="T084: frozen routing corpus + second-user sales journey",
    )
    assert approved.status == 200, approved.text
    assert approved.json["state"] == "approved", approved.json
    version_id = approved.json["version_id"]
    versions = t068.version_rows(stack.dbname, application_id)
    assert len(versions) == 1 and versions[0]["id"] == version_id, versions
    assert t068.active_version_id(stack.dbname, application_id) == version_id
    manifest = json.loads(versions[0]["manifest"])
    publication.update(
        {
            "application_id": application_id,
            "version_id": version_id,
            "manifest_sha256": manifest_sha,
            "review_id": review_id,
            "evaluation_report_id": report["report"]["id"],
            "evaluation_status": report["report"]["status"],
            "five_category_cases": len(report["sealed"].get("cases", [])),
        }
    )
    sales = runner.PublishedApp(
        key="sales",
        application_id=application_id,
        version_id=version_id,
        version_no=versions[0]["version_no"],
        card=dict(manifest["capability_card"]),
        publication="t068_real",
    )
    return {"sales": sales, "session": session, "publication": publication}


TRANSIENT_SANDBOX = "the attempt lease was lost while the sandbox ran"


def _journey(stack, second, publisher_session, sales: runner.PublishedApp) -> dict:
    """The second user routes, executes, downloads and verifies the sales CSV."""
    attachment_id, csv_bytes = runner.upload_sales_csv(stack, second.session)
    message = json.dumps(
        runner.make_request_input(message="把这份销售明细汇总成报告"), ensure_ascii=False
    )
    # The WSL2/runsc host has one known jitter: a heartbeat tick can race the tool
    # exit ("the attempt lease was lost while the sandbox ran", reported in earlier
    # F04A/F06 tickets). It is not a product-rule failure, so a bounded retry with a
    # fresh request is recorded rather than hidden; any other failure fails at once.
    history: list[dict] = []
    for attempt in range(1, 4):
        conversation = f08.conversation(second.session)
        submitted = f08.submit_request(
            second.session, conversation, message, artifact_ids=[attachment_id],
            key=f00b.new_uuid(),
        )
        request = f08.request_of(submitted)
        assert request["state"] == "routing", request
        route = t079.route_once(stack)
        assert route.completed.returncode == 0, route.stdout

        row = t079.request_state(stack.dbname, request["id"])
        assert row["state"] == "dispatched", row
        run_id = row["run_id"]
        assert run_id, row
        supervisor = runner.supervisor_report(stack, heartbeat="1s")
        history.append({
            "attempt": attempt,
            "request_id": request["id"],
            "run_id": run_id,
            "status": supervisor.get("status"),
            "outcome": supervisor.get("outcome"),
            "error_code": supervisor.get("error_code"),
            "duration_ms": supervisor.get("duration_ms"),
        })
        if supervisor.get("status") == "executed" and supervisor.get("outcome") == "succeeded":
            break
        transient = (
            supervisor.get("error_code") == "sandbox_failed"
            and TRANSIENT_SANDBOX in str(supervisor.get("error"))
        )
        print(
            f"T084: journey attempt {attempt} hit the known sandbox heartbeat jitter; "
            f"retrying with a fresh request: {supervisor.get('error')}"
        )
        assert transient, supervisor
    else:  # pragma: no cover - only a repeatedly stalled host reaches this
        raise AssertionError(f"the journey Run never executed successfully: {history}")

    run = t082.run_row(stack.dbname, run_id)
    assert run["owner_id"] == second.principal_id, run
    assert run["application_version_id"] == sales.version_id, run
    assert run["kind"] == "published", run
    read = f08.get_request(second.session, request["id"])
    assert read.status == 200, read.text
    assert read.header("X-Request-Application-Version-Id") == sales.version_id
    assert read.header("X-Request-Application-Version-No") == str(sales.version_no)

    state = t033.run_state_row(stack.dbname, run_id)
    assert state["state"] == "succeeded", state
    assert state["error_code"] is None, state

    # The owner read exposes labels; the CSV is a real run artifact, not a fabricated
    # test file. The bytes are checked against the independent Decimal truth and the
    # known-good literal summary.
    results = t042.wait_node_results(second.session, run_id, expected=4)
    by_label = {item["label"]: item for item in results["items"] if item["label"]}
    assert "summary.csv" in by_label, results["items"]
    csv_artifact = by_label["summary.csv"]["artifact_id"]
    artifact_fact = f08.artifact_row(stack.dbname, csv_artifact)
    assert artifact_fact["owner_id"] == second.principal_id, artifact_fact
    assert artifact_fact["media_type"] == "text/csv", artifact_fact
    download = t033.artifact_content(second.session, csv_artifact)
    assert download.status == 200, download.text
    # The artifact's recorded hash is the exact bytes the second user downloads.
    assert artifact_fact["sha256"] == st.sha256_hex(download.raw), artifact_fact
    t049.check_summary_csv(download.raw)
    assert download.raw == t033.EXPECTED_SUMMARY_CSV, download.raw

    summary_json = by_label.get("summary.json")
    assert summary_json is not None, results["items"]
    summary_read = t033.artifact_content(second.session, summary_json["artifact_id"])
    assert summary_read.status == 200, summary_read.text
    summary = json.loads(summary_read.raw)
    t049.check_summary(summary)

    output_id = t033.output_artifact_id(stack.dbname, run_id)
    assert output_id, state
    output_read = t033.artifact_content(second.session, output_id)
    assert output_read.status == 200, output_read.text
    output = json.loads(output_read.raw)
    t049.check_summary(output["summary"])
    assert output["summary"] == summary, output
    assert output["report"], output

    # AC1's second half: the publisher cannot read the second user's Run or its
    # artifact; both answer the same not-found shape, and an anonymous caller 401s.
    acl = {}
    foreign_run = publisher_session.client.get("/api/v1/runs/" + run_id)
    st.assert_error(foreign_run, 404, "resource_not_found")
    foreign_artifact = publisher_session.client.get(
        "/api/v1/artifacts/" + csv_artifact + "/content"
    )
    st.assert_error(foreign_artifact, 404, "resource_not_found")
    anonymous = stack.client()
    st.assert_error(anonymous.get("/api/v1/runs/" + run_id), 401, "unauthenticated")
    st.assert_error(
        anonymous.get("/api/v1/artifacts/" + csv_artifact + "/content"),
        401,
        "unauthenticated",
    )
    acl["publisher_run_status"] = foreign_run.status
    acl["publisher_artifact_status"] = foreign_artifact.status
    acl["anonymous_run_status"] = 401
    acl["anonymous_artifact_status"] = 401

    return {
        "request_id": request["id"],
        "conversation_id": conversation,
        "run_id": run_id,
        "run_owner_matches_second_user": run["owner_id"] == second.principal_id,
        "application_version_id": run["application_version_id"],
        "input_artifact_id": row["input_artifact_id"],
        "attachment_artifact_id": attachment_id,
        "summary_csv_artifact_id": csv_artifact,
        "summary_csv_sha256": artifact_fact["sha256"],
        "input_sha256": st.sha256_hex(csv_bytes),
        "independent_truth": "tests/support/t049.py:check_summary_csv (Decimal, never the tool)",
        "final_file_ok": True,
        "execution_attempts": history,
        "execution_attempt_count": len(history),
        "supervisor": {
            "status": supervisor["status"],
            "outcome": supervisor["outcome"],
            "sandbox_started": supervisor.get("sandbox", {}).get("started"),
            "error_code": supervisor.get("error_code"),
        },
        "acl": acl,
    }


def test_the_frozen_corpus_is_replayed_against_a_real_published_catalog(
    t084_stack, tmp_path: Path
) -> None:
    stack = t084_stack
    started_at = time.time()
    published = _publish_sales_application(stack, tmp_path)
    sales = published["sales"]
    publication = published["publication"]
    admin = f00b.admin_session(stack)
    publisher_session = published["session"]

    second = f08.account(stack, login="t084-second-user")
    catalog = runner.build_catalog(
        stack, admin, second.principal_id, publisher_session, sales
    )
    route_model = runner.seed_route_control(stack, publisher_session.principal_id)
    assert route_model["provider"] == "stub", route_model

    journey = _journey(stack, second, publisher_session, sales)

    registry = corpus.verify_registry()
    cases = corpus.load_cases()
    corpus.validate_corpus(cases, registry)
    attachment_id, _ = runner.upload_sales_csv(stack, second.session)
    results = {split: [] for split in corpus.SPLITS}
    for split in corpus.SPLITS:
        for case in cases:
            if case.split != split:
                continue
            results[split].append(
                runner.run_case(stack, second, catalog, case, attachment_id)
            )

    records = [record for split in corpus.SPLITS for record in results[split]]
    payroll_version = catalog[runner.HIDDEN_KEY].version_id
    summary = runner.aggregate(records)
    for record in records:
        print(
            "T084CASE "
            + json.dumps(
                {
                    "case_id": record["case_id"],
                    "split": record["split"],
                    "expected": record["expected"]["decision"],
                    "actual": record["actual"]["decision"],
                    "error_code": record["actual"]["error_code"],
                    "candidates": record["actual"]["candidates"],
                    "decision_matches": record["decision_matches"],
                    "candidate_set_matches": record["candidate_set_matches"],
                    "false_auto_execution": record["false_auto_execution"],
                    "clarification_required": record["clarification_required"],
                    "clarification_completed": record["clarification_completed"],
                    "run_id": record["actual"]["run_id"],
                },
                ensure_ascii=False,
            )
        )

    environment = {
        "profile": "dev-stub",
        "stack": (
            "real PostgreSQL 16 cluster (tests/support/stack.py) + compiled cmd/api, "
            "cmd/maintenance and cmd/supervisor; WSL2/runsc for the publication and "
            "journey execution"
        ),
        "route_control_model": (
            "dev-stub route control model registered as a real passed model profile "
            "(provider=stub, stub=true); the server owns ACL, required inputs and the "
            "decision policy"
        ),
        "database": "throwaway PostgreSQL 16 cluster on 127.0.0.1:55432",
        "python": sys.version.split()[0],
        "git_head": _git_head(),
        "run_started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started_at)),
        "browser_dom": "not_run (no browser driver); chat.js executed under Node with a minimal DOM",
        "real_model_baseline": "not_run (no vendor model credentials; dev-stub only)",
        "corpus_input": "docs/spec/examples/sales/input.csv (copied into the frozen corpus)",
    }
    document = runner.report_document(
        stack=stack,
        results=results,
        catalog=catalog,
        publication=publication,
        environment=environment,
        journey=journey,
    )
    assert document["summary"] == summary, "the report summary must be the computed one"
    assert document["corpus"]["registry_sha256"] == corpus.sha256_file(corpus.REGISTRY)
    # Write the machine-readable result before the assertions so a regression leaves
    # its actual recorded outcome on disk instead of only in the traceback.
    written = runner.write_report(document, default_path=tmp_path / "T084-routing-eval.json")
    stored = json.loads(written.read_text(encoding="utf-8"))
    assert stored["summary"]["total_cases"] == 34

    for record in records:
        assert record["decision_matches"], record
        assert record["candidate_set_matches"], record
        assert record["false_auto_execution"] is False, record
        assert payroll_version not in record["actual"]["candidate_version_ids"], record
        if record["actual"]["decision"] == "failed":
            assert record["actual"]["run_id"] is None, record
            assert record["clarification_required"] is False, record
        if record["expected"]["decision"] == "auto_dispatch":
            assert record["actual"]["run_owner_matches_second_user"] is True, record
        if record["clarification_required"]:
            assert record["clarification_completed"] is True, record

    for split in corpus.SPLITS:
        stats = summary["splits"][split]
        assert stats["decision_accuracy"] == 1.0, stats
        assert stats["false_auto_executions"] == 0, stats
        assert stats["false_auto_execution_rate"] == 0.0, stats
        assert stats["no_candidate_leaks"] == [], stats
        assert stats["candidate_recall"] == 1.0, stats
        assert stats["clarification_required"] > 0, stats
        assert stats["clarification_completed"] == stats["clarification_required"], stats
        assert stats["clarification_completion_rate"] == 1.0, stats
    assert summary["total_cases"] == 34, summary
    assert sum(item["cases"] for item in summary["splits"]["frozen_test"]["by_cell"].values()) == 25
    assert sum(item["cases"] for item in summary["splits"]["dev"]["by_cell"].values()) == 9

    print(f"T084: routing evaluation report written to {written}")
    print("T084: summary=" + json.dumps(summary, ensure_ascii=False)[:1200])
