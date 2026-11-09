"""T100 journey 3: draft -> validate -> real evaluation -> review -> immutable version.

The whole chain is the product's real publication path:

* real invite + real ``POST /applications`` + ``PUT /draft`` (If-Match) for the
  executable content;
* real ``POST /applications/{id}/validate`` (the compiler) with the location of
  the resulting sealed manifest checked against the object bytes;
* a **real five-category evaluation**: the real ``cmd/maintenance`` pools create
  and collect the cases and a real ``cmd/supervisor exec --once`` runs each case
  in WSL2/runsc (T068's own runner helper is reused, including its bounded retry
  for shared-sandbox contention);
* real ``POST /applications/{id}/submit`` -> admin ``POST /reviews/{id}/decision``
  -> an immutable ``application_versions`` row.

Independent truth: application/version/review rows, the sealed manifest bytes,
the sealed evaluation report, the outbox and audit rows in PostgreSQL, plus the
contract version read. The only test-side SQL is the two deployment seeds
(preset tool version and execution-slot capacity) that a dev deployment carries
before any creator exists — no journey state is fabricated.

Two failure boundaries are proved here: validation refuses a draft depending on a
pending model (located error, no manifest), and the publish path refuses a stale
manifest hash / non-admin decision / second decision. The last section records
the **known F08<->F04B product gap**: a published Run whose input is a chat
message fails with ``node_input_invalid`` before any sandbox starts.
"""

from __future__ import annotations

import json
import secrets

import pytest

from support import execution_support as execution
from support import f00b, f08, stack as st, t025, t027, t029, t033, t039, t042, t056, t068
from t100_support import journey_log, sha256_hex

CATEGORIES = [
    "normal",
    "bad_input",
    "ambiguity_or_out_of_bounds",
    "tool_failure",
    "output_check",
]


def _deployment_seeds(stack, owner_id: str) -> None:
    """The two deployment inputs a dev deployment already has.

    The preset ``sales_stats`` tool version and the execution-slot capacity are
    deployment values, not user actions: T025/T056/T068's own suites seed them
    the same way. Everything the ticket calls a journey step below is HTTP.
    """
    t025.seed_tool_version(stack.dbname, owner_id, tool_id=t042.PRESET_TOOL_ID)
    t056.seed_evaluation_capacity(stack)


def _create_sales_application(stack, creator) -> str:
    """A start -> python(sales_stats) -> end draft saved through the public API."""
    return t027.application_with_definition(
        creator,
        t042.linear_definition(input_schema=t068.TOOL_INPUT_SCHEMA),
        name="t100 publication app",
    )


def _validate(stack, journey, creator, application_id: str) -> tuple[str, dict, dict]:
    response = t025.validate(creator, application_id)
    assert response.status == 200, response.text
    assert response.json == {"valid": True, "errors": []}, response.text
    manifest_sha = response.header("X-Manifest-SHA256")
    assert manifest_sha and len(manifest_sha) == 64, manifest_sha
    rows = t025.manifest_artifacts(stack.dbname, application_id)
    assert rows, "the real compiler sealed no manifest artifact"
    manifest_row = rows[-1]
    assert manifest_row["manifest_sha256"] == manifest_sha, manifest_row
    manifest_bytes = t025.manifest_artifact_bytes(stack.object_dir, manifest_row)
    assert sha256_hex(manifest_bytes) == manifest_sha, "the sealed manifest bytes do not hash"
    manifest = json.loads(manifest_bytes)
    assert manifest["definition"]["nodes"], manifest
    journey.assertion(
        "POST /applications/{id}/validate returns {valid:true, errors:[]} and a 64-hex "
        "X-Manifest-SHA256; the compiler's sealed manifest artifact has the same hash and its "
        "object bytes hash back to it"
    )
    return manifest_sha, manifest_row, manifest


def _validate_pending_model_boundary(stack, journey, admin, creator) -> None:
    pending = t039.register_or_fail(admin, name="t100 pending dependency")
    invalid_application = t027.application_with_definition(
        creator, t039.model_definition(pending["id"]), name="t100 pending model app"
    )
    refused = t025.validate(creator, invalid_application)
    assert refused.status == 200, refused.text
    body = refused.json
    assert body["valid"] is False and body["errors"], body
    error = body["errors"][0]
    assert error["code"] == "dependency_not_ready", body
    assert error["pointer"] == "/nodes/1/config/model_profile_version", error
    assert error["node_id"] == "analysis", error
    assert refused.header("X-Manifest-SHA256") is None, refused.header("X-Manifest-SHA256")
    assert t068.version_count(stack.dbname, invalid_application) == 0
    assert t025.manifest_artifacts(stack.dbname, invalid_application) == []
    journey.assertion(
        "validate of a draft depending on a pending model returns valid:false with the located "
        "dependency_not_ready error, no X-Manifest-SHA256 header, no manifest artifact and no version"
    )


def _run_evaluation(stack, journey, owner, tmp_path) -> dict:
    try:
        outcome = t068._genuine_outcome(  # noqa: SLF001 - T068's own bounded retry wrapper
            stack,
            owner,
            t068.five_category_cases(),
            log_path=tmp_path / "t100-evaluation-pools.log",
        )
    except t068.SandboxEnvironmentUnavailable as unavailable:
        pytest.skip(f"the genuine five-case evaluation needs WSL2/runsc: {unavailable}")
    assert outcome["report"]["status"] == "passed", outcome["sealed"]
    journey.assertion(
        "the real five-category evaluation (maintenance pools + one real `cmd/supervisor exec "
        "--once` per case in WSL2/runsc) ends in a passed sealed report"
    )
    return outcome


def _read_evaluation_contracts(journey, creator, outcome: dict) -> tuple[str, dict]:
    evaluation = outcome["evaluation"]
    evaluation_read = creator.client.get(f"/api/v1/evaluations/{evaluation['id']}")
    assert evaluation_read.status == 200, evaluation_read.text
    assert set(evaluation_read.json) == {
        "id", "kind", "state", "background_job_id", "report_id", "error_code",
    }, evaluation_read.json
    assert evaluation_read.json["kind"] == "application", evaluation_read.json
    assert evaluation_read.json["state"] == "succeeded", evaluation_read.json
    report_id = evaluation_read.json["report_id"]
    assert report_id == outcome["report"]["id"], evaluation_read.json

    report_read = creator.client.get(f"/api/v1/evaluation-reports/{report_id}")
    assert report_read.status == 200, report_read.text
    assert set(report_read.json) == {
        "id", "status", "manifest_sha256", "result_artifact_id",
    }, report_read.json
    assert report_read.json["status"] == "passed", report_read.json
    journey.assertion(
        "GET /evaluations/{id} is 200 state=succeeded with the report id and "
        "GET /evaluation-reports/{id} is 200 status=passed (both owner-readable contract reads)"
    )
    return report_id, report_read.json


def _submit_and_review(stack, journey, admin, creator, owner, application_id, report_id, manifest_sha):
    # The real five categories the report must cover are asserted from the sealed
    # report, not from the test's case list.
    preview = t068.preview_publication(creator, application_id)
    assert preview.status == 200, preview.text
    assert preview.json["manifest_sha256"] == manifest_sha, preview.json
    journey.note(
        "POST /applications/{id}/publication-preview is a real product route but is NOT one of "
        "the 106 OpenAPI operations (non-contract route); it is only a pre-submit read here"
    )

    submitted = t068.submit_publication(
        creator,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
    )
    assert submitted.status == 202, submitted.text
    assert set(submitted.json) == {"id", "state"}, submitted.json
    assert submitted.json["state"] == "submitted", submitted.json
    review_id = submitted.json["id"]
    review = t068.review_row(stack.dbname, review_id)
    assert review["state"] == "submitted", review
    assert review["manifest_sha256"] == manifest_sha, review
    assert review["report_id"] == report_id, review
    assert review["submitted_by"] == creator.principal_id, review
    snapshot = json.loads(review["snapshot"])
    assert snapshot["author_cases"]["satisfied"] is True, snapshot["author_cases"]
    assert snapshot["author_cases"]["categories"] == CATEGORIES, snapshot["author_cases"]
    assert snapshot["report_status"] == "passed", snapshot
    journey.assertion(
        "POST /applications/{id}/submit returns 202 {id, state:submitted}; the review row freezes "
        "the same manifest hash and a snapshot whose author_cases cover all five categories"
    )

    # -- submission boundaries -------------------------------------------------
    foreign = f00b.invite_and_activate(
        stack, admin, "t100.foreign-" + secrets.token_hex(3), roles=("creator",)
    )
    hidden = t068.submit_publication(
        foreign,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
    )
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found", hidden.text
    assert t068.review_count(stack.dbname, application_id) == 1

    wrong_hash = t068.submit_publication(
        creator,
        application_id,
        expected_manifest_sha256="1" * 64,
        evaluation_report_id=report_id,
    )
    assert wrong_hash.status == 409, wrong_hash.text
    assert wrong_hash.json["error"]["code"] == "manifest_mismatch", wrong_hash.text

    missing_key = t068.submit_publication(
        creator,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        omit_key=True,
    )
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text
    journey.assertion(
        "submission boundaries: another creator gets 404 resource_not_found (no review created), "
        "a wrong expected manifest hash is 409 manifest_mismatch, a missing Idempotency-Key is "
        "400 idempotency_key_required"
    )

    # -- decision boundaries + real approval ----------------------------------
    non_admin = t068.decided_review(
        creator, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert non_admin.status == 403, non_admin.text
    assert non_admin.json["error"]["code"] == "forbidden", non_admin.text
    mismatch = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256="1" * 64
    )
    assert mismatch.status == 409, mismatch.text
    assert mismatch.json["error"]["code"] == "manifest_mismatch", mismatch.text
    assert mismatch.json["error"]["details"]["reviewed_manifest_sha256"] == manifest_sha, mismatch.text
    assert t068.review_row(stack.dbname, review_id)["state"] == "submitted"

    approved = t068.decided_review(
        admin,
        review_id,
        decision="approve",
        manifest_sha256=manifest_sha,
        reason="T100 reviewed the frozen five-category evidence",
    )
    assert approved.status == 200, approved.text
    assert set(approved.json) == {"id", "state", "version_id"}, approved.json
    assert approved.json["state"] == "approved", approved.json
    version_id = approved.json["version_id"]
    assert version_id, approved.json
    journey.assertion(
        "review decision boundaries: a non-admin creator is 403 forbidden, a mismatched manifest "
        "hash is 409 manifest_mismatch with the reviewed hash in details; the admin approval is "
        "200 {state:approved, version_id} and creates exactly one version"
    )
    return review_id, version_id


def _assert_immutable_version(
    stack, journey, admin, creator, application_id, review_id, version_id, manifest_sha, manifest_row
) -> None:
    db = stack.dbname
    versions = t068.version_rows(db, application_id)
    assert len(versions) == 1, versions
    version = versions[0]
    assert version["id"] == version_id, version
    assert version["version_no"] == 1, version
    assert version["review_id"] == review_id, version
    assert version["manifest_sha256"] == manifest_sha, version
    assert version["manifest_artifact_id"] == manifest_row["artifact_id"], version
    manifest_bytes = t068.sealed_bytes(stack, version["manifest_artifact_id"])
    assert sha256_hex(manifest_bytes) == manifest_sha
    assert t068.active_version_id(db, application_id) == version_id
    assert t068.review_row(db, review_id)["state"] == "approved"
    assert t068.review_row(db, review_id)["version_id"] == version_id
    assert len(t068.outbox_rows(db, version_id)) == 1, t068.outbox_rows(db, version_id)
    outbox_payload = json.loads(t068.outbox_rows(db, version_id)[0]["payload"])
    assert outbox_payload["op"] == "publish", outbox_payload
    assert outbox_payload["manifest_sha256"] == manifest_sha, outbox_payload
    assert "publication.approved" in t068.audit_actions(db, version_id)

    version_read = creator.client.get(f"/api/v1/versions/{version_id}")
    assert version_read.status == 200, version_read.text
    assert version_read.json["manifest_sha256"] == manifest_sha, version_read.json
    application_read = creator.client.get(f"/api/v1/applications/{application_id}")
    assert application_read.status == 200, application_read.text
    assert application_read.json["active_version_id"] == version_id, application_read.json
    journey.fact("published_version", {
        "application_id": application_id,
        "version_id": version_id,
        "version_no": version["version_no"],
        "manifest_sha256": manifest_sha,
        "manifest_artifact_id": version["manifest_artifact_id"],
        "review_id": review_id,
        "outbox_publish_rows": 1,
        "audit_actions": t068.audit_actions(db, version_id),
    })
    journey.assertion(
        "independent truth: exactly one application_versions row (version_no 1) bound to the "
        "approved review and the validated manifest artifact; the sealed manifest bytes hash to "
        "the evaluated manifest; the active pointer, one catalog_index outbox row and the "
        "publication.approved audit event are real rows; GET /versions and GET /applications agree"
    )

    # A decided review cannot be decided again; identical content re-submission
    # returns the approved review instead of creating a second one.
    again = t068.decided_review(admin, review_id, decision="approve", manifest_sha256=manifest_sha)
    assert again.status == 409, again.text
    assert again.json["error"]["code"] == "review_conflict", again.text
    assert again.json["error"]["details"]["state"] == "approved", again.text
    resubmit = t068.submit_publication(
        creator,
        application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=t068.review_row(db, review_id)["report_id"],
    )
    assert resubmit.status == 202, resubmit.text
    assert resubmit.json["id"] == review_id, resubmit.json
    assert resubmit.json["state"] == "approved", resubmit.json
    assert t068.review_count(db, application_id) == 1
    assert t068.version_count(db, application_id) == 1

    # Editing the draft after publication changes the next manifest hash but not
    # the published version: the row and its sealed bytes are byte-identical.
    before_row = dict(version)
    before_bytes = t068.sealed_bytes(stack, version["manifest_artifact_id"])
    draft_read = creator.client.get(f"/api/v1/applications/{application_id}/draft")
    assert draft_read.status == 200, draft_read.text
    etag = draft_read.header("ETag")
    draft = draft_read.json
    changed = json.loads(json.dumps(draft["definition"]))
    changed["policy"]["max_active_seconds"] = 901
    saved = t027.save_draft(
        creator,
        application_id,
        etag,
        definition=changed,
        layout=draft["layout"],
        capability_card=draft["capability_card"],
    )
    assert saved.status == 200, saved.text
    revalidated = t025.validate(creator, application_id)
    assert revalidated.status == 200, revalidated.text
    new_hash = revalidated.header("X-Manifest-SHA256")
    assert new_hash and new_hash != manifest_sha, (new_hash, manifest_sha)
    assert t068.version_rows(db, application_id)[0] == before_row, "the published version row changed"
    assert t068.sealed_bytes(stack, version["manifest_artifact_id"]) == before_bytes
    still = creator.client.get(f"/api/v1/versions/{version_id}")
    assert still.status == 200, still.text
    assert still.json["manifest_sha256"] == manifest_sha, still.json
    assert t068.active_version_id(db, application_id) == version_id
    journey.fact("immutability", {
        "draft_revalidated_manifest_sha256": new_hash,
        "published_manifest_sha256": manifest_sha,
        "published_row_unchanged": True,
        "published_sealed_bytes_unchanged": True,
    })
    journey.assertion(
        "immutability: a second decision is 409 review_conflict(state=approved), identical "
        "re-submission returns the same approved review (no second review/version), and a real "
        "draft edit + revalidate yields a different manifest hash while the published version row "
        "and sealed bytes stay byte-identical and the active pointer does not move"
    )


def _record_chat_input_gap(stack, journey, creator, version_id: str) -> None:
    """The known F08<->F04B gap: chat text as a published Run input.

    This is a real product entry point (``POST /requests`` with an explicit
    ``application_version_id`` — IMPLEMENTATION-DETAILS §5) and a real supervisor
    execution. It is recorded as a **blocked** capability, not as a passed
    journey: the executor requires the Run input to be a JSON object, the Request
    path seals the chat message verbatim, so the first execution fails with
    ``node_input_invalid`` before any sandbox starts.
    """
    log = journey_log()
    if not t033.sandbox_available():
        log.known_gap(
            "GAP-chat-input-as-run-input",
            title="聊天正文作为已发布 Run 输入",
            status="not_run",
            entry_http=[
                "POST /api/v1/conversations",
                "POST /api/v1/requests {message: <chat text>, application_version_id}",
                "GET /api/v1/requests/{id}",
                "GET /api/v1/runs/{id}",
                "GET /api/v1/runs/{id}/node-results",
            ],
            entry_commands=["cmd/supervisor exec --once"],
            truth_sources=["platform.runs (state, error_code)", "platform.run_artifacts"],
            observed={},
            reason="not_run: WSL2/runsc is not installed on this host",
        )
        return

    conversation_id = f08.conversation(creator)
    message = "把这份销售明细汇总成报告"
    submitted = f08.submit_request(
        creator,
        conversation_id,
        message,
        version_id=version_id,
        key="t100-chat-gap-" + secrets.token_hex(6),
    )
    request = f08.request_of(submitted)
    assert request["state"] == "dispatched", request
    assert submitted.header("X-Request-Application-Version-Id") == version_id
    request_row = f08.request_row(stack.dbname, request["id"])
    input_row = f08.artifact_row(stack.dbname, request_row["input_artifact_id"])
    assert input_row["purpose"] == "request_input", input_row
    assert input_row["sha256"] == sha256_hex(message.encode()), input_row
    assert f08.artifact_bytes(stack, request_row["input_artifact_id"]) == message.encode()

    completed, report = execution.execute_once(stack)
    run_id = request["run_id"]
    run_row = t029.run_state_row(stack.dbname, run_id)
    run_read = creator.client.get(f"/api/v1/runs/{run_id}")
    node_results = t042.node_results(creator, run_id)
    node_items = node_results.json["items"] if node_results.status == 200 else None
    observed = {
        "exit_code": completed.returncode,
        "executor_report": {
            key: report.get(key) for key in ("status", "outcome", "run_state", "error_code")
        },
        "run_row": {
            "state": run_row["state"],
            "error_code": run_row["error_code"],
            "output_artifact_id": run_row["output_artifact_id"],
        },
        "run_read_status": run_read.status,
        "run_read": run_read.json if run_read.status == 200 else run_read.text,
        "node_results_status": node_results.status,
        "node_results_items": len(node_items) if node_items is not None else None,
        "request_input_artifact": {
            "purpose": input_row["purpose"],
            "sha256": input_row["sha256"],
            "message_bytes_equal": True,
        },
    }
    # The product's real behaviour: the executor refuses non-JSON task input before
    # any sandbox starts. The supervisor calls a pre-execution refusal "refused";
    # the committed Run state is failed/node_input_invalid either way.
    assert report["outcome"] in {"failed", "refused"}, (report, completed.stderr)
    assert report["status"] == "failed", report
    assert report["error_code"] == "node_input_invalid", report
    assert run_row["state"] == "failed", run_row
    assert run_row["error_code"] == "node_input_invalid", run_row
    assert run_row["output_artifact_id"] is None, run_row
    assert run_read.status == 200 and run_read.json["state"] == "failed", run_read.text
    assert run_read.json["error_code"] == "node_input_invalid", run_read.json
    if node_items is not None:
        assert node_items == [], node_items
    log.known_gap(
        "GAP-chat-input-as-run-input",
        title="聊天正文作为已发布 Run 输入（F08↔F04B 产品缺口）",
        status="blocked",
        entry_http=[
            "POST /api/v1/conversations (201)",
            "POST /api/v1/requests {message: '把这份销售明细汇总成报告', "
            "application_version_id: <published version>} (202 dispatched)",
            "GET /api/v1/requests/{id}",
            "GET /api/v1/runs/{id}",
            "GET /api/v1/runs/{id}/node-results",
        ],
        entry_commands=["cmd/supervisor exec --once (real compiled supervisor, WSL2/runsc)"],
        truth_sources=[
            "platform.runs.state / error_code / output_artifact_id",
            "platform.requests + artifacts (the sealed request_input bytes equal the message)",
            "object directory: the Run's input artifact bytes",
            "platform.run_node_outputs (no node output rows)",
        ],
        observed=observed,
        reason=(
            "The Request path seals the chat message verbatim as the Run input "
            "(internal/requests/submit.go:131,646-690) while the executor requires a JSON object "
            "(internal/presetexec/linear.go:177-187 / graphrun.go:929-937), so the real execution "
            "fails with node_input_invalid before any sandbox starts. This is the known F08<->F04B "
            "integration gap, not a test-authoring shortcut; the ticket's structured entry "
            "(explicit application_version_id + JSON task document) is the in-spec alternative and "
            "was not used to claim this chat-text capability."
        ),
    )
    journey.note(
        "known product gap recorded separately (status blocked): chat text as a published Run "
        "input fails node_input_invalid; see reports/f12/T100-journeys.md §已知产品缺口"
    )


def run_publication_journey(stack, journey, tmp_path) -> None:
    db = stack.dbname
    admin = f00b.admin_session(stack)
    creator = f00b.invite_and_activate(
        stack, admin, "t100.publisher-" + secrets.token_hex(3), roles=("creator",)
    )
    _deployment_seeds(stack, creator.principal_id)
    application_id = _create_sales_application(stack, creator)
    owner = t029.Owner(admin=admin, creator=creator, application_id=application_id)

    # -- 1. validate: the real compiler + its sealed manifest ------------------
    manifest_sha, manifest_row, _manifest = _validate(stack, journey, creator, application_id)
    _validate_pending_model_boundary(stack, journey, admin, creator)

    # -- 2. the real five-category evaluation ----------------------------------
    outcome = _run_evaluation(stack, journey, owner, tmp_path)
    report_id, report_view = _read_evaluation_contracts(journey, creator, outcome)
    assert report_view["manifest_sha256"] == manifest_sha, (report_view, manifest_sha)
    snapshot = t027.snapshot_row(db, outcome["evaluation"]["snapshot_id"])
    assert snapshot["manifest_sha256"] == manifest_sha, snapshot
    sealed_cases = {case["id"]: case for case in outcome["sealed"]["cases"]}
    facts = {case_id: case["facts"] for case_id, case in sealed_cases.items()}
    assert facts["sales-happy-csv"]["run_state"] == "succeeded", facts
    assert facts["sales-bad-schema-1"]["run_error_code"] == "node_input_invalid", facts
    assert facts["sales-over-limit"]["run_error_code"] == "row_limit_exceeded", facts
    assert facts["sales-tool-failure"]["run_error_code"] == "sandbox_unavailable", facts
    assert facts["sales-bad-schema-2"]["run_error_code"] == "node_input_invalid", facts
    journey.fact("evaluation", {
        "evaluation_id": outcome["evaluation"]["id"],
        "report_id": report_id,
        "report_status": report_view["status"],
        "manifest_sha256": manifest_sha,
        "snapshot_manifest_sha256": snapshot["manifest_sha256"],
        "cases": {
            case_id: {
                "run_state": case_facts.get("run_state"),
                "run_error_code": case_facts.get("run_error_code"),
            }
            for case_id, case_facts in facts.items()
        },
    })
    journey.assertion(
        "the evaluated snapshot's manifest hash equals the validated manifest hash; the sealed "
        "report covers five genuine categories (succeeded, node_input_invalid x2, "
        "row_limit_exceeded, sandbox_unavailable)"
    )

    # -- 3. submit + decision boundaries + approval ----------------------------
    review_id, version_id = _submit_and_review(
        stack, journey, admin, creator, owner, application_id, report_id, manifest_sha
    )

    # -- 4. immutable version + truth -----------------------------------------
    _assert_immutable_version(
        stack, journey, admin, creator, application_id, review_id, version_id, manifest_sha,
        manifest_row,
    )

    # -- 5. known F08<->F04B gap: chat text as a published Run input -----------
    _record_chat_input_gap(stack, journey, creator, version_id)
