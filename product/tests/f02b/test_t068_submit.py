"""T068 submitApplication: freeze exactly the evaluated content.

Every test drives the real API over HTTP against real PostgreSQL and the real
sealed object store. The cited report is guard-accepted sealed evidence over real
evaluation/case-run rows (the review fixtures seed the runs' terminal states; the
separate end-to-end test uses a report the real runner produced).
"""

from __future__ import annotations

import json
import uuid

from support import t027, t068
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)

CATEGORIES = [
    "normal",
    "bad_input",
    "ambiguity_or_out_of_bounds",
    "tool_failure",
    "output_check",
]


def _report(stack, *, login: str | None = None) -> dict:
    return t068.seeded_passed_report(stack, login=login)


def test_submit_freezes_the_same_evaluated_content(t068_stack) -> None:
    report = _report(t068_stack, login="t068-submit-happy")
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]
    report_id = report["report"]["id"]

    # The sealed report really carries the five categories: two normal runs, a
    # located bad input, an over-limit input and a real tool failure code.
    sealed_cases = {case["id"]: case for case in report["sealed"]["cases"]}
    assert sealed_cases["author-normal-1"]["facts"]["run_state"] == "succeeded"
    assert sealed_cases["author-normal-2"]["facts"]["run_state"] == "succeeded"
    assert sealed_cases["author-bad-input"]["facts"]["run_error_code"] == "node_input_invalid"
    assert sealed_cases["author-out-of-bounds"]["facts"]["run_error_code"] == "row_limit_exceeded"
    assert sealed_cases["author-tool-failure"]["facts"]["run_error_code"] == "sandbox_unavailable"

    preview = t068.preview_publication(session, application_id)
    assert preview.status == 200, preview.text
    assert preview.json["manifest_sha256"] == manifest_sha, preview.json
    assert preview.json["purpose_card"]["purpose"], preview.json
    # This start->end fixture has no tool dependency; the genuine sales e2e test
    # proves a non-empty tool capability list.
    assert preview.json["tool_capabilities"] == [], preview.json

    key = "t068-submit-" + uuid.uuid4().hex
    response = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        idempotency_key=key,
    )
    assert response.status == 202, response.text
    view = response.json
    assert set(view) == {"id", "state"}, view
    assert view["state"] == "submitted", view
    assert t068.review_count(t068_stack.dbname, application_id) == 1

    row = t068.review_row(t068_stack.dbname, view["id"])
    assert row["state"] == "submitted", row
    assert row["manifest_sha256"] == manifest_sha, row
    assert row["report_id"] == report_id, row
    assert row["submitted_by"] == session.principal_id, row
    assert row["snapshot_id"] == report["snapshot"]["id"], row
    assert row["evaluation_id"] == report["evaluation"]["id"], row
    assert row["dataset_sha256"] == report["report"]["dataset_sha256"], row
    snapshot = json.loads(row["snapshot"])
    assert snapshot["review_version"] == 1, snapshot
    assert snapshot["manifest_sha256"] == manifest_sha, snapshot
    assert snapshot["purpose_card"]["purpose"], snapshot
    assert snapshot["model_destinations"] == [], snapshot
    assert snapshot["subapp_versions"] == [], snapshot
    assert snapshot["report_id"] == report_id, snapshot
    assert snapshot["report_status"] == "passed", snapshot
    assert snapshot["report_sha256"] == t068.artifact_row(
        t068_stack.dbname, report["report"]["result_artifact_id"]
    )["sha256"], snapshot
    assert snapshot["tool_capabilities"] == [], snapshot
    assert snapshot["author_cases"]["satisfied"] is True, snapshot
    assert snapshot["author_cases"]["categories"] == CATEGORIES, snapshot
    assert snapshot["author_cases"]["case_count"] == 5, snapshot
    # The shared definition only: no run body, user content or credential field.
    for forbidden in ("secret_ref", "object_key", "request", "conversation"):
        assert forbidden not in row["snapshot"], forbidden

    # Same key, same body: the stored 202 is replayed and nothing is created.
    replay = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        idempotency_key=key,
    )
    assert replay.status == 202, replay.text
    assert replay.json == view, replay.json
    assert t068.review_count(t068_stack.dbname, application_id) == 1

    # Same key, different body: conflict, never a second review.
    conflict = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        body={
            "expected_manifest_sha256": manifest_sha,
            "evaluation_report_id": str(uuid.uuid4()),
        },
        idempotency_key=key,
    )
    assert conflict.status == 409, conflict.text
    assert conflict.json["error"]["code"] == "idempotency_conflict", conflict.text

    # Identical content with a new key: the existing review is returned.
    identical = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
    )
    assert identical.status == 202, identical.text
    assert identical.json["id"] == view["id"], identical.json
    assert t068.review_count(t068_stack.dbname, application_id) == 1

    # Authorization and body rules on the same live review.
    missing_key = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        omit_key=True,
    )
    assert missing_key.status == 400, missing_key.text
    assert missing_key.json["error"]["code"] == "idempotency_key_required", missing_key.text

    no_csrf = session.client.post(
        t068.submit_path(application_id),
        body={"expected_manifest_sha256": manifest_sha, "evaluation_report_id": report_id},
        headers={"Idempotency-Key": "t068-no-csrf"},
    )
    assert no_csrf.status == 403, no_csrf.text
    assert no_csrf.json["error"]["code"] == "csrf_invalid", no_csrf.text

    foreign = t068.creator(t068_stack, login="t068-foreign-" + uuid.uuid4().hex[:8])
    hidden = t068.submit_publication(
        foreign, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
    )
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found", hidden.text

    invalid_hash = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256="not-a-hash",
        evaluation_report_id=report_id,
    )
    assert invalid_hash.status == 422, invalid_hash.text
    assert invalid_hash.json["error"]["details"]["field"] == "expected_manifest_sha256", invalid_hash.text

    unknown_field = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
        body={
            "expected_manifest_sha256": manifest_sha,
            "evaluation_report_id": report_id,
            "draft_revision": 2,
        },
    )
    assert unknown_field.status == 422, unknown_field.text
    assert unknown_field.json["error"]["details"]["field"] == "draft_revision", unknown_field.text

    unknown_app = t068.submit_publication(
        session, str(uuid.uuid4()),
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report_id,
    )
    assert unknown_app.status == 404, unknown_app.text
    assert t068.review_count(t068_stack.dbname) == 1


def test_a_draft_edit_after_evaluation_cannot_be_smuggled_into_the_review(
    t068_stack,
) -> None:
    report = _report(t068_stack, login="t068-draft-edit")
    session = report["session"]
    application_id = report["application_id"]
    report_id = report["report"]["id"]
    old_hash = report["manifest_sha256"]

    etag = t027.get_draft(session, application_id).header("ETag")
    current = json.loads(t027.get_draft(session, application_id).text)
    changed = json.loads(json.dumps(current["definition"]))
    changed["policy"]["max_active_seconds"] = 901
    saved = t027.save_draft(
        session, application_id, etag,
        definition=changed, capability_card=t027.full_card(),
    )
    assert saved.status == 200, saved.text

    stale = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=old_hash,
        evaluation_report_id=report_id,
    )
    assert stale.status == 409, stale.text
    assert stale.json["error"]["code"] == "manifest_mismatch", stale.text
    assert "current_manifest_sha256" in stale.json["error"]["details"], stale.text
    assert t068.review_count(t068_stack.dbname, application_id) == 0

    validated = session.client.post(
        f"/api/v1/applications/{application_id}/validate", csrf=session.csrf
    )
    assert validated.status == 200, validated.text
    new_hash = validated.header("X-Manifest-SHA256")
    assert new_hash and new_hash != old_hash, validated.text

    # Naming the new hash but citing the old report is refused: the report is not
    # a report for this content.
    wrong_report = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=new_hash,
        evaluation_report_id=report_id,
    )
    assert wrong_report.status == 422, wrong_report.text
    assert wrong_report.json["error"]["code"] == "report_binding_mismatch", wrong_report.text
    assert t068.review_count(t068_stack.dbname, application_id) == 0


def test_an_under_covered_report_is_refused_before_publication(t068_stack) -> None:
    report = t068.seeded_passed_report(t068_stack, login="t068-undercovered", cases=1)
    response = t068.submit_publication(
        report["session"], report["application_id"],
        expected_manifest_sha256=report["manifest_sha256"],
        evaluation_report_id=report["report"]["id"],
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "author_cases_incomplete", response.text
    missing = response.json["error"]["details"]["missing_categories"]
    assert "tool_failure" in missing and "bad_input" in missing, missing
    assert t068.review_count(t068_stack.dbname, report["application_id"]) == 0


def test_an_empty_or_not_run_report_and_a_forged_runner_row_are_refused(
    t068_stack,
) -> None:
    not_run = t068.not_run_report(t068_stack, login="t068-not-run")
    response = t068.submit_publication(
        not_run["session"], not_run["application_id"],
        expected_manifest_sha256=not_run["manifest_sha256"],
        evaluation_report_id=not_run["report"]["id"],
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "evaluation_not_passed", response.text
    assert t068.review_count(t068_stack.dbname, not_run["application_id"]) == 0

    # Defence in depth for a report row the current guard would not write: the
    # publication gate re-reads the sealed document instead of trusting the row.
    forged = t068.forged_runner_report(t068_stack, login="t068-forged")
    response = t068.submit_publication(
        forged["session"], forged["application_id"],
        expected_manifest_sha256=forged["manifest_sha256"],
        evaluation_report_id=forged["report_id"],
    )
    assert response.status == 422, response.text
    assert response.json["error"]["code"] == "report_not_trusted", response.text
    assert t068.review_count(t068_stack.dbname, forged["application_id"]) == 0

