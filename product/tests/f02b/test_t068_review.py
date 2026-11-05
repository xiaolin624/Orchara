"""T068 reviewPublication: one immutable version, one approval, recorded decisions.

The review fixtures use a guard-accepted sealed report over real evaluation/case
run rows (terminal states seeded, see tests/support/t068.py); the separate
end-to-end test drives the same API with a report the real runner produced. The
approval tests read the authoritative rows (version, active pointer, outbox,
audit) back from PostgreSQL.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from support import f00b, t025, t027, t052, t068, t070, t075, t078
from support import stack as st
from support.fixtures import (  # noqa: F401 - pytest fixtures
    api_server,
    fresh_database,
    go_binaries,
    migrated_database,
)


def _submitted_review(stack, *, login: str):
    report = t068.seeded_passed_report(stack, login=login)
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
    report, review_id = _submitted_review(t068_stack, login="t068-approve")
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]
    admin = t068.admin_session(t068_stack)
    review = t068.review_row(t068_stack.dbname, review_id)
    manifest_bytes_before = t068.sealed_bytes(t068_stack, review["manifest_artifact_id"])

    # A decision needs the admin role, and it is hash-bound to the frozen review.
    non_admin = t068.decided_review(
        session, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert non_admin.status == 403, non_admin.text
    mismatch = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256="1" * 64
    )
    assert mismatch.status == 409, mismatch.text
    assert mismatch.json["error"]["code"] == "manifest_mismatch", mismatch.text
    details = mismatch.json["error"]["details"]
    assert details["reviewed_manifest_sha256"] == manifest_sha, details
    assert details["provided_manifest_sha256"] == "1" * 64, details

    # A dependency revoked after submission fails the approval with evidence is
    # proved end-to-end in tests/f02b/test_t068_e2e.py (a genuine sales app has a
    # real tool dependency; the seeded fixture here has none).

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
    assert snapshot["tool_capabilities"] == [], snapshot
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


def test_rejection_returns_the_creator_to_editing_and_cannot_be_undone(
    t068_stack, tmp_path: Path
) -> None:
    report, review_id = _submitted_review(t068_stack, login="t068-reject")
    admin = t068.admin_session(t068_stack)
    session = report["session"]
    application_id = report["application_id"]
    manifest_sha = report["manifest_sha256"]

    # An application archived after submission cannot be published; restoring it
    # (test-only) leaves the review still submitted, so the same review can then
    # be rejected.
    t068.archive_application(t068_stack.dbname, application_id)
    archived = t068.decided_review(
        admin, review_id, decision="approve", manifest_sha256=manifest_sha
    )
    assert archived.status == 409, archived.text
    assert archived.json["error"]["code"] == "application_archived", archived.text
    assert t068.review_row(t068_stack.dbname, review_id)["state"] == "submitted"
    t052.st.sql(
        t068_stack.dbname,
        "UPDATE platform.applications SET archived_at = NULL "
        f"WHERE id = {t052.st.quote_literal(application_id)}::uuid",
    )

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
    saved = t027.save_draft(
        session, application_id, etag,
        definition=draft["definition"], layout=draft["layout"],
        capability_card=draft["capability_card"],
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


def test_concurrent_approvals_create_one_version_and_old_versions_stay_immutable(
    t068_stack, tmp_path: Path
) -> None:
    first, first_review = _submitted_review(t068_stack, login="t068-immutable")
    admin = t068.admin_session(t068_stack)
    session = first["session"]
    application_id = first["application_id"]
    manifest_sha = first["manifest_sha256"]

    # Two administrators approving at the same time: exactly one version.
    sessions = [t068.admin_session(t068_stack), t068.admin_session(t068_stack)]
    barrier = threading.Barrier(2)

    def decide(admin_session):
        barrier.wait(timeout=30)
        return t068.decided_review(
            admin_session, first_review, decision="approve", manifest_sha256=manifest_sha,
            reason="concurrent approval attempt",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(decide, sessions))
    statuses = sorted(response.status for response in responses)
    assert statuses == [200, 409], [(r.status, r.text) for r in responses]
    winner = next(response for response in responses if response.status == 200)
    loser = next(response for response in responses if response.status == 409)
    assert loser.json["error"]["code"] in {"review_conflict", "idempotency_conflict"}, loser.text

    versions = t068.version_rows(t068_stack.dbname, application_id)
    assert len(versions) == 1, versions
    first_version = versions[0]
    assert first_version["id"] == winner.json["version_id"], versions
    assert t068.active_version_id(t068_stack.dbname, application_id) == first_version["id"]
    assert t068.review_row(t068_stack.dbname, first_review)["state"] == "approved"
    assert len(t068.outbox_rows(t068_stack.dbname, first_version["id"])) == 1
    approved = t068.audit_actions(t068_stack.dbname, first_version["id"])
    assert approved.count("publication.approved") == 1, approved

    first_artifact = first_version["manifest_artifact_id"]
    bytes_before = t068.sealed_bytes(t068_stack, first_artifact)
    row_before = dict(first_version)

    # A later publication: edit executable content, evaluate the *same*
    # application again, submit and approve a second version.
    draft = session.client.get(f"/api/v1/applications/{application_id}/draft").json
    etag = session.client.get(f"/api/v1/applications/{application_id}/draft").header("ETag")
    changed = json.loads(json.dumps(draft["definition"]))
    changed["policy"]["max_active_seconds"] = 902
    saved = t027.save_draft(
        session, application_id, etag, definition=changed,
        layout=draft["layout"], capability_card=draft["capability_card"],
    )
    assert saved.status == 200, saved.text

    second = t068.seeded_passed_report(
        t068_stack, session=session, application_id=application_id
    )
    snapshot = t068.t027.snapshot_row(
        t068_stack.dbname, second["evaluation"]["snapshot_id"]
    )
    second_submit = t068.submit_publication(
        session, application_id,
        expected_manifest_sha256=snapshot["manifest_sha256"],
        evaluation_report_id=second["report"]["id"],
    )
    assert second_submit.status == 202, second_submit.text
    second_approved = t068.decided_review(
        admin, second_submit.json["id"],
        decision="approve", manifest_sha256=snapshot["manifest_sha256"],
    )
    assert second_approved.status == 200, second_approved.text

    versions = t068.version_rows(t068_stack.dbname, application_id)
    assert len(versions) == 2, versions
    assert [row["version_no"] for row in versions] == [1, 2], versions
    assert versions[1]["manifest_sha256"] != manifest_sha, versions
    assert t068.active_version_id(t068_stack.dbname, application_id) == versions[1]["id"]

    # The v1 artifact bytes and row are byte-identical after the v2 publish.
    assert t068.sealed_bytes(t068_stack, first_artifact) == bytes_before
    assert t068.version_rows(t068_stack.dbname, application_id)[0] == row_before
    fetched = session.client.get(f"/api/v1/versions/{first_version['id']}")
    assert fetched.status == 200, fetched.text
    assert fetched.json["manifest_sha256"] == manifest_sha, fetched.text


# The test-only advisory key the injected revoke gate waits on (this database is
# a throwaway and the trigger is dropped at the end of the test).
_REVOKE_GATE_KEY = 7300681


def _install_revoke_gate(dbname: str) -> None:
    """Hold the real revoke transaction inside its own audit write.

    The trigger blocks on an advisory lock that a test-held psql session owns, so
    the revoke keeps the child version row locked but cannot commit until the test
    releases the gate. That turns the approve-vs-revoke interleaving into an
    observed order instead of a sleep.
    """
    st.sql(dbname, f"""
        CREATE OR REPLACE FUNCTION platform.t068_revoke_gate() RETURNS trigger AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock({_REVOKE_GATE_KEY});
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER t068_revoke_gate BEFORE INSERT ON platform.audit_events
            FOR EACH ROW WHEN (NEW.action = 'version.revoked')
            EXECUTE FUNCTION platform.t068_revoke_gate();
    """)


def _drop_revoke_gate(dbname: str) -> None:
    st.sql(dbname, """
        DROP TRIGGER IF EXISTS t068_revoke_gate ON platform.audit_events;
        DROP FUNCTION IF EXISTS platform.t068_revoke_gate();
    """)


def test_a_concurrent_revoke_cannot_slip_past_the_approval_dependency_lock(
    t068_stack,
) -> None:
    """T068 AC5 (concurrent): the approval re-reads the pinned child under a lock.

    Linearization asserted here: **the revoke commits first and the approval
    fails**. The real revoke is held inside its transaction by an injected audit
    trigger; the real approval then blocks on the child version row the revoke
    locked FOR UPDATE (the recheck takes FOR KEY SHARE on that row). Releasing the
    gate lets the revoke commit before the approval can re-read the row, so the
    recheck sees ``dependency_revoked`` and no version is pinned. Without the lock
    the approval would have passed its plain recheck while the revoke was still
    uncommitted and then publish a parent pinned to a revoked child.
    """
    stack = t068_stack
    _admin, creator = t075.creator(stack)

    child_app = t025.create_application_or_fail(creator, "t068 race child")
    saved = t075.save_definition(creator, child_app["id"], t078.child_definition())
    assert saved.status == 200, saved.text
    child_version = t078.publish_version(stack, creator, child_app["id"])

    parent_app = t025.create_application_or_fail(creator, "t068 race parent")
    saved = t075.save_definition(
        creator, parent_app["id"], t078.parent_definition([child_version])
    )
    assert saved.status == 200, saved.text
    report = t068.seeded_passed_report(
        stack, session=creator, application_id=parent_app["id"]
    )
    snapshot = t027.snapshot_row(stack.dbname, report["evaluation"]["snapshot_id"])
    manifest_sha = snapshot["manifest_sha256"]
    submitted = t068.submit_publication(
        creator, parent_app["id"],
        expected_manifest_sha256=manifest_sha,
        evaluation_report_id=report["report"]["id"],
    )
    assert submitted.status == 202, submitted.text
    review_id = submitted.json["id"]

    revoker = t068.admin_session(stack)
    approver = t068.admin_session(stack)
    holder = "t068-revoke-gate-" + f00b.new_uuid()[:8]
    _install_revoke_gate(stack.dbname)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        with st.HeldLock(
            stack.dbname, f"SELECT pg_advisory_lock({_REVOKE_GATE_KEY});", name=holder
        ) as gate:
            revoked_future = pool.submit(
                t070.revoke_version, revoker, child_version,
                reason="concurrent revoke during an approval",
            )
            # The real revoke holds the child version row and is parked inside its
            # own audit write: it has not committed.
            st.wait_for_lock_waiters(stack.dbname)
            approved_future = pool.submit(
                t068.decided_review, approver, review_id,
                decision="approve", manifest_sha256=manifest_sha,
            )
            # The approval now waits on the pinned child version row itself; the
            # second waiter is the proof that it reached the recheck while the
            # revoke was still uncommitted.
            st.wait_for_lock_waiters(stack.dbname, count=2)
            gate.release()
        revoked = revoked_future.result(timeout=120)
        approved = approved_future.result(timeout=120)
    finally:
        pool.shutdown(wait=True)
        _drop_revoke_gate(stack.dbname)

    assert revoked.status == 200, revoked.text
    assert t070.version_row(stack.dbname, child_version)["revoked_at"] is not None
    assert approved.status == 409, approved.text
    assert approved.json["error"]["code"] == "dependency_unavailable", approved.text
    located = approved.json["error"]["details"]["errors"]
    assert any(error["code"] == "dependency_revoked" for error in located), located
    assert t068.version_count(stack.dbname, parent_app["id"]) == 0
    assert t068.review_row(stack.dbname, review_id)["state"] == "submitted"
