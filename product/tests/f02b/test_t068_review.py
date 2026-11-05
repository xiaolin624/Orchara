"""T068 reviewPublication: one immutable version, one approval, recorded decisions.

The cited evidence is produced by the real runner (tests/support/t068.py). The
approval tests then drive the real API over HTTP and read the authoritative rows
(version, active pointer, outbox, audit) back from PostgreSQL.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from support import t042, t068, t052
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submitted_review(stack, tmp_path: Path, *, login: str):
    report = t068.genuine_sales_report(
        stack, login=login, log_path=tmp_path / "pools.log"
    )
    response = t068.submit_publication(
        report["session"], report["application_id"],
        expected_manifest_sha256=report["manifest_sha256"],
        evaluation_report_id=report["report"]["id"],
    )
    assert response.status == 202, response.text
    return report, response.json["id"]


def test_approval_creates_one_version_and_moves_the_active_pointer(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-approve")
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]
    admin = t068.admin_session(t068_stack)
    review = t068.review_row(t068_stack.dbname, review_id)
    manifest_artifact = t068.artifact_row(t068_stack.dbname, review["manifest_artifact_id"])
    manifest_bytes_before = t068.sealed_bytes(t068_stack, review["manifest_artifact_id"])

    # A decision needs the admin role.
    non_admin = t068.decided_review(
        session, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert non_admin.status == 403, non_admin.text

    response = t068.decided_review(
        admin, review_id,
        decision="approve", manifest_sha256=manifest_sha,
        reason="content reviewed against the frozen evidence",
    )
    assert response.status == 200, response.text
    decision = response.json
    assert set(decision) == {"id", "state", "version_id"}, decision
    assert decision["state"] == "approved", decision
    version_id = decision["version_id"]
    assert version_id, decision

    versions = t068.version_rows(t068_stack.dbname, application_id)
    assert len(versions) == 1, versions
    version = versions[0]
    assert version["id"] == version_id, version
    assert version["version_no"] == 1, version
    assert version["review_id"] == review_id, version
    assert version["manifest_sha256"] == manifest_sha, version
    assert version["manifest_artifact_id"] == review["manifest_artifact_id"], version
    assert json.loads(version["manifest"]) == json.loads(
        manifest_bytes_before.decode("utf-8")
    ), version
    # The version JSON artifact is the frozen canonical manifest bytes, unchanged.
    assert t068.sealed_bytes(t068_stack, version["manifest_artifact_id"]) == manifest_bytes_before
    assert manifest_artifact["sha256"] == manifest_sha, manifest_artifact

    assert t068.active_version_id(t068_stack.dbname, application_id) == version_id
    application = session.client.get(f"/api/v1/applications/{application_id}")
    assert application.status == 200, application.text
    assert application.json["active_version_id"] == version_id, application.text
    listed = session.client.get(f"/api/v1/applications/{application_id}/versions")
    assert listed.status == 200, listed.text
    assert [item["id"] for item in listed.json["items"]] == [version_id], listed.text
    fetched = session.client.get(f"/api/v1/versions/{version_id}")
    assert fetched.status == 200, fetched.text
    assert fetched.json["manifest_sha256"] == manifest_sha, fetched.text

    row = t068.review_row(t068_stack.dbname, review_id)
    assert row["state"] == "approved", row
    assert row["reviewer_id"] == admin.principal_id, row
    assert row["version_id"] == version_id, row
    assert row["reason"] == "content reviewed against the frozen evidence", row
    assert row["decided_at"], row

    outbox = t068.outbox_rows(t068_stack.dbname, version_id)
    assert len(outbox) == 1, outbox
    payload = json.loads(outbox[0]["payload"])
    assert outbox[0]["kind"] == "catalog_index", outbox
    assert payload["op"] == "publish", payload
    assert payload["application_id"] == application_id, payload
    assert payload["version_no"] == 1, payload
    assert payload["manifest_sha256"] == manifest_sha, payload
    assert payload["active_version_id"] == version_id, payload
    assert payload["previous_active_version_id"] is None, payload
    assert outbox[0]["delivered_at"] is None, outbox

    assert "publication.approved" in t068.audit_actions(t068_stack.dbname, version_id)
    assert "publication.submitted" in t068.audit_actions(t068_stack.dbname, review_id)

    # The creator reads the decided review and its frozen snapshot.
    detail = t068.get_review(session, review_id)
    assert detail.status == 200, detail.text
    body = detail.json
    assert set(body) == {"id", "state", "manifest_sha256", "report_id", "snapshot"}, body
    assert body["state"] == "approved", body
    snapshot = body["snapshot"]
    assert snapshot["author_cases"]["satisfied"] is True, snapshot
    assert snapshot["tool_capabilities"], snapshot
    assert snapshot["model_destinations"] == [], snapshot
    assert snapshot["subapp_versions"] == [], snapshot

    # Review reads are ACL-filtered: an administrator sees the queue; a foreign
    # creator sees nothing and cannot enumerate the review id.
    admin_list = t068.list_reviews(admin)
    assert admin_list.status == 200, admin_list.text
    assert any(item["id"] == review_id for item in admin_list.json["items"]), admin_list.text
    foreign = t068.creator(t068_stack, login="t068-foreign-" + uuid.uuid4().hex[:8])
    foreign_list = t068.list_reviews(foreign)
    assert foreign_list.status == 200, foreign_list.text
    assert foreign_list.json == {"items": [], "next_cursor": None}, foreign_list.text
    hidden = t068.get_review(foreign, review_id)
    assert hidden.status == 404, hidden.text
    assert hidden.json["error"]["code"] == "resource_not_found", hidden.text


def test_a_revoked_dependency_fails_the_approval_with_evidence(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-dep")
    admin = t068.admin_session(t068_stack)
    manifest_sha = report["manifest_sha256"]

    t052.st.sql(
        t068_stack.dbname,
        "UPDATE platform.tool_versions SET state = 'revoked', revoked_at = now(), "
        f"revoke_reason = 't068 test revocation' "
        f"WHERE id = {t052.st.quote_literal(t042.PRESET_TOOL_ID)}::uuid",
    )
    blocked = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert blocked.status == 409, blocked.text
    assert blocked.json["error"]["code"] == "dependency_unavailable", blocked.text
    errors = blocked.json["error"]["details"]["errors"]
    assert any(error["code"] == "dependency_revoked" for error in errors), errors
    assert t068.version_count(t068_stack.dbname, report["application_id"]) == 0
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "submitted"

    # Restoring the dependency in this test-only scenario lets the same review be
    # approved; the gate re-reads current rows on every attempt.
    t052.st.sql(
        t068_stack.dbname,
        "UPDATE platform.tool_versions SET state = 'ready', revoked_at = NULL, "
        f"revoke_reason = NULL "
        f"WHERE id = {t052.st.quote_literal(t042.PRESET_TOOL_ID)}::uuid",
    )
    approved = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert approved.status == 200, approved.text
    assert approved.json["state"] == "approved", approved.json


def test_rejection_returns_the_creator_to_editing_and_cannot_be_undone(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-reject")
    admin = t068.admin_session(t068_stack)
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]

    missing_reason = admin.client.post(
        t068.decision_path(review_id),
        body={"decision": "reject", "manifest_sha256": manifest_sha, "reason": "   "},
        csrf=admin.csrf,
        headers={"Idempotency-Key": "t068-reject-no-reason"},
    )
    assert missing_reason.status == 422, missing_reason.text
    assert missing_reason.json["error"]["details"]["field"] == "reason", missing_reason.text
    response = t068.decided_review(
        admin, review_id, decision="reject", manifest_sha256=manifest_sha,
        reason="the purpose card does not describe the shipped behaviour",
    )
    assert response.status == 200, response.text
    assert response.json == {
        "id": review_id, "state": "rejected", "version_id": None,
    }, response.json
    row = t068.review_row(t068_stack.dbname, review_id)
    assert row["state"] == "rejected", row
    assert row["reason"] == "the purpose card does not describe the shipped behaviour", row
    assert row["version_id"] is None, row
    assert row["decided_at"], row
    assert t068.version_count(t068_stack.dbname, application_id) == 0
    assert t068.active_version_id(t068_stack.dbname, application_id) is None

    # The draft is still editable after a rejection.
    etag = session.client.get(f"/api/v1/applications/{application_id}/draft").header("ETag")
    draft = session.client.get(f"/api/v1/applications/{application_id}/draft").json
    saved = session.client.put(
        f"/api/v1/applications/{application_id}/draft",
        body=draft,
        csrf=session.csrf,
        headers={"If-Match": etag},
    )
    assert saved.status == 200, saved.text

    # A rejected review can never be approved later.
    late = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert late.status == 409, late.text
    assert late.json["error"]["code"] == "review_conflict", late.text
    assert late.json["error"]["details"]["state"] == "rejected", late.text
    assert t068.version_count(t068_stack.dbname, application_id) == 0

    # Identical content re-submission returns the rejected review, not a new one.
    again = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["report"]["id"],
    )
    assert again.status == 202, again.text
    assert again.json == {"id": review_id, "state": "rejected"}, again.json
    assert t068.review_count(t068_stack.dbname, application_id) == 1

    # The decision body is validated before the review is touched.
    invalid = admin.client.post(
        t068.decision_path(review_id),
        body={"decision": "approve", "manifest_sha256": "nope", "reason": "x"},
        csrf=admin.csrf,
        headers={"Idempotency-Key": "t068-bad-hash"},
    )
    assert invalid.status == 422, invalid.text


def test_a_decision_hash_mismatch_is_refused(t068_stack, tmp_path: Path) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-hash")
    admin = t068.admin_session(t068_stack)
    response = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256="1" * 64
    )
    assert response.status == 409, response.text
    assert response.json["error"]["code"] == "manifest_mismatch", response.text
    details = response.json["error"]["details"]
    assert details["reviewed_manifest_sha256"] == report["manifest_sha256"], details
    assert details["provided_manifest_sha256"] == "1" * 64, details
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "submitted"
    assert t068.version_count(t068_stack.dbname, report["application_id"]) == 0


def test_concurrent_approvals_create_exactly_one_version(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-race")
    manifest_sha = report["manifest_sha256"]
    sessions = [t068.admin_session(t068_stack), t068.admin_session(t068_stack)]
    barrier = threading.Barrier(2)

    def decide(session):
        barrier.wait(timeout=30)
        return t068.decided_review(
            session, review_id, decision="approve", manifest_sha256=manifest_sha,
            reason="concurrent approval attempt",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(decide, sessions))
    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 409], [(r.status, r.text) for r in responses]
    winner = next(response for response in responses if response.status == 200)
    loser = next(response for response in responses if response.status == 409)
    assert loser.json["error"]["code"] in {"review_conflict", "idempotency_conflict"}, loser.text

    versions = t068.version_rows(t068_stack.dbname, report["application_id"])
    assert len(versions) == 1, versions
    assert versions[0]["id"] == winner.json["version_id"], versions
    assert t068.active_version_id(t068_stack.dbname, report["application_id"]) == versions[0]["id"]
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "approved"
    assert len(t068.outbox_rows(t068_stack.dbname, versions[0]["id"])) == 1
    approved = t068.audit_actions(t068_stack.dbname, versions[0]["id"])
    assert approved.count("publication.approved") == 1, approved


def test_old_version_bytes_and_rows_are_never_rewritten(
    t068_stack, tmp_path: Path
) -> None:
    first, first_review = _submitted_review(t068_stack, tmp_path, login="t068-immutable")
    admin = t068.admin_session(t068_stack)
    session = first["session"]
    application_id = first["application_id"]
    approved = t068.decided_review(
        admin, first_review, decision="approve", manifest_sha256=first["manifest_sha256"]
    )
    assert approved.status == 200, approved.text
    first_version = t068.version_rows(t068_stack.dbname, application_id)[0]
    first_artifact = first_version["manifest_artifact_id"]
    bytes_before = t068.sealed_bytes(t068_stack, first_artifact)
    row_before = dict(first_version)

    # A later publication: edit executable content, evaluate the *same*
    # application again, submit and approve a second version.
    draft = session.client.get(f"/api/v1/applications/{application_id}/draft").json
    etag = session.client.get(f"/api/v1/applications/{application_id}/draft").header("ETag")
    changed = json.loads(json.dumps(draft["definition"]))
    changed["policy"]["max_active_seconds"] = 902
    saved = session.client.put(
        f"/api/v1/applications/{application_id}/draft",
        body={"definition": changed, "layout": draft["layout"], "capability_card": draft["capability_card"]},
        csrf=session.csrf,
        headers={"If-Match": etag},
    )
    assert saved.status == 200, saved.text
    second_review = _second_review_on_application(t068_stack, session, application_id, tmp_path)
    approved_second = t068.decided_review(
        admin, second_review["review_id"],
        decision="approve", manifest_sha256=second_review["manifest_sha256"],
    )
    assert approved_second.status == 200, approved_second.text
    versions = t068.version_rows(t068_stack.dbname, application_id)
    assert len(versions) == 2, versions
    assert [row["version_no"] for row in versions] == [1, 2], versions
    assert versions[1]["manifest_sha256"] != first["manifest_sha256"], versions

    # The v1 artifact bytes and row are byte-identical after the v2 publish.
    assert t068.sealed_bytes(t068_stack, first_artifact) == bytes_before
    assert t068.version_rows(t068_stack.dbname, application_id)[0] == row_before
    fetched = session.client.get(f"/api/v1/versions/{first_version['id']}")
    assert fetched.status == 200, fetched.text
    assert fetched.json["manifest_sha256"] == first["manifest_sha256"], fetched.text


def _second_review_on_application(stack, session, application_id: str, tmp_path: Path) -> dict:
    """Run a second genuine evaluation of the *same* application and submit it."""
    owner = t068.owner_for(session, application_id)
    outcome = t068.run_adaptive_evaluation(
        stack, owner, t068.five_category_cases(), log_path=tmp_path / "pools2.log"
    )
    assert outcome["report"]["status"] == "passed", outcome["sealed"]
    snapshot = t068.t027.snapshot_row(stack.dbname, outcome["evaluation"]["snapshot_id"])
    response = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=snapshot["manifest_sha256"],
        evaluation_report_id=outcome["report"]["id"],
    )
    assert response.status == 202, response.text
    return {
        "review_id": response.json["id"],
        "manifest_sha256": snapshot["manifest_sha256"],
    }


def test_application_archived_after_submission_cannot_be_published(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, tmp_path, login="t068-archived")
    admin = t068.admin_session(t068_stack)
    t068.archive_application(t068_stack.dbname, report["application_id"])
    blocked = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=report["manifest_sha256"]
    )
    assert blocked.status == 409, blocked.text
    assert blocked.json["error"]["code"] == "application_archived", blocked.text
    assert t068.version_count(t068_stack.dbname, report["application_id"]) == 0
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "submitted"
